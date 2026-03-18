# cli.py
# Copyright RomanAILabs - Daniel Harding
# Version: 1.9.1 — auto-git commits disabled by default (never runs unless config["auto_git"] = true)

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn, MofNCompleteColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.markdown import Markdown
from rich.markup import escape
import os
import json
import time
import signal
import sys
import uuid
import re
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import ollama

console = Console(highlight=False, soft_wrap=True)

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".romatask" / "config.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

def load_global_config() -> Dict:
    default = {
        "default_model": "qwen2.5",
        "projects_root": str(Path.home() / "Desktop" / "RomaProjects"),
        "auto_git": False,                 # ← explicitly disabled — will never commit automatically
        "ask_before_major_write": False,
        "verbose_stream": False,
        "temperature_phase1": 0.25,
        "temperature_later": 0.08,
    }
    config = default.copy()
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config.update(loaded)
        except:
            pass
    # Always enforce auto_git = False unless user manually edits config.json
    config["auto_git"] = False
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config

CONFIG = load_global_config()

current_task = None

# ────────────────────────────────────────────────
# TASK STATE
# ────────────────────────────────────────────────

class Task:
    def __init__(self, directory: str | Path, model: str):
        self.id = str(uuid.uuid4())[:8]
        self.model = model
        self.directory = Path(directory).expanduser().resolve()
        self.prompt = ""
        self.duration = 60
        self.progress = {"completed": [], "history": [], "last_critic": {}}
        self.start_time = time.time()
        self.queue = []
        self.structure = []
        self.git_initialized = False
        self.total_phases = 0

    def get_internal_dir(self) -> Path:
        d = self.directory / ".romatask"
        d.mkdir(exist_ok=True)
        return d

    def save(self, status: str = "Active"):
        internal = self.get_internal_dir()
        log_md = f"# RomanAILabs Project\n\n## Status: {status}\n\n## Objective\n{self.prompt}\n\n## History\n"
        log_md += "\n".join(f"• {h}" for h in self.progress["history"])
        (internal / "PROMPT_CONTEXT.md").write_text(log_md, encoding="utf-8")

        (internal / "state.json").write_text(
            json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in vars(self).items()},
                       indent=2),
            encoding="utf-8"
        )

    @staticmethod
    def load(directory: str) -> 'Task':
        state_path = Path(directory).expanduser() / ".romatask" / "state.json"
        if not state_path.exists():
            raise FileNotFoundError(f"No project at {directory}")
        data = json.loads(state_path.read_text(encoding="utf-8"))
        task = Task(data["directory"], data["model"])
        for k, v in data.items():
            if k not in {"directory", "model"} and hasattr(task, k):
                setattr(task, k, v)
        return task

# ────────────────────────────────────────────────
# SIGNAL HANDLER
# ────────────────────────────────────────────────

def signal_handler(sig, frame):
    console.print("\n[bold yellow]Swarm paused. State saved.[/]")
    if current_task:
        current_task.save()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ────────────────────────────────────────────────
# DASHBOARD
# ────────────────────────────────────────────────

def render_dashboard(tokens: int, elapsed: float, task: Task):
    if elapsed <= 0:
        return
    tps = tokens / elapsed
    proj_elap = time.time() - task.start_time
    proj_rem = max(0, task.duration * 60 - proj_elap)
    rem_m, rem_s = divmod(int(proj_rem), 60)
    ela_m, ela_s = divmod(int(proj_elap), 60)
    completed = len(task.progress["completed"])
    total = task.total_phases or (completed + len(task.queue) or 1)
    pct = (completed / total) * 100 if total > 0 else 0.0

    panel = Panel(
        f"[bold white]Progress[/]   [cyan]{completed}/{total}[/] • [bold green]{pct:5.1f}%[/]\n"
        f"[bold white]Throughput[/] [cyan]{tps:6.1f} t/s[/] • {tokens} tokens\n"
        f"[bold white]Session[/]    [cyan]{ela_m}m {ela_s:02d}s[/] elapsed • [yellow]{rem_m}m {rem_s:02d}s remaining[/]",
        title="[bold cyan]RomaTask Status[/]",
        border_style="bright_blue",
        expand=False,
        padding=(1, 2),
    )
    console.print(panel, justify="center")

# ────────────────────────────────────────────────
# AGENTS (unchanged core, but git block removed from executor)
# ────────────────────────────────────────────────

class Agents:
    @staticmethod
    def planner(model: str, objective: str, ctx) -> Dict:
        ctx.console.print("[bold yellow]Architecting blueprint...[/]")
        prompt = f"""You are senior architect at RomanAILabs.
Objective: {objective}
Rules:
- Keep structure pragmatic and minimal
- Define 3–6 clear, sequential implementation phases
- Focus on files that will contain meaningful logic
Return ONLY valid JSON:
{{
  "structure": ["index.html", "css/style.css", "js/main.js"],
  "tasks": ["Create semantic HTML structure", "Implement responsive styling", "Add core interactivity"]
}}
"""
        try:
            res = ollama.generate(model=model, prompt=prompt, format="json")
            return json.loads(res["response"])
        except Exception as e:
            ctx.console.print(f"[red]Planner failed: {e}[/]")
            return {"structure": [], "tasks": [objective]}

    @staticmethod
    def critic(model: str, task: str, draft: str, ctx) -> Dict:
        ctx.console.print("[bold magenta]Performing code review...[/]")
        prompt = f"""
Task: {task}
Code excerpt:\n{draft[-2800:] or "<empty draft>"}

Must pass ALL:
1. Correct use of ollama API (if applicable)
2. No invented or fake modules
3. Implements real logic for this task
4. Uses valid relative paths
5. Syntactically correct
6. No repetitive or identical output from previous passes

Reject if looping, hallucinated, or no progress.

Return ONLY JSON:
{{
  "is_complete": true|false,
  "feedback": "brief reason"
}}
"""
        try:
            res = ollama.generate(model=model, prompt=prompt, format="json")
            return json.loads(res["response"])
        except:
            return {"is_complete": False, "feedback": "Critic failed"}

    @staticmethod
    def reflection(model: str, task: str, draft: str, feedback: str, ctx) -> str:
        ctx.console.print("[bold yellow]Reflection & correction pass...[/]")
        prompt = f"""Previous attempt failed review.
Task: {task}
Feedback: {feedback}
Draft excerpt: {draft[:1000]}...

Analyze what went wrong. Produce ONE corrected version.
Output ONLY <file path="...">code here</file> tags — nothing else.
"""
        try:
            res = ollama.chat(model=model, messages=[{"role": "system", "content": prompt}])
            return res["message"]["content"]
        except:
            return draft

    @staticmethod
    def executor(model: str, task_obj: Task, current_task: str, ctx) -> Tuple[str, bool]:
        snapshot = get_live_snapshot(task_obj.directory)
        full_draft = ""
        attempts = 0
        approved = False
        max_attempts = 4
        temp = CONFIG["temperature_later"] if attempts > 1 else CONFIG["temperature_phase1"]

        while attempts < max_attempts and not approved:
            attempts += 1
            ctx.console.print(f"\n[bold cyan]Pass {attempts}/{max_attempts} • {current_task}[/]")

            thought_prompt = f"Given project goal '{task_obj.prompt}' and sub-task '{current_task}', provide a 1-sentence engineering thought on your execution strategy."
            try:
                thought_res = ollama.generate(model=model, prompt=thought_prompt, options={"temperature": 0.4})
                ctx.console.print(f"🧠 [italic white]'{escape(thought_res['response'].strip())}'[/]\n")
            except:
                pass

            system = f"""Lead Builder at RomanAILabs
Project objective: {task_obj.prompt}
Current phase: {current_task}

Strict rules:
- Output ONLY inside <file path="relative/path.ext"> ...code... </file>
- Use paths exactly as shown in snapshot
- Use real ollama API: ollama.chat(..., stream=True) or ollama.generate(...)
- Implement functional code — fill stubs
- STOP immediately after code — no prose, no repetition, no JSON summaries unless requested

Current disk state:\n{snapshot}
"""
            stream = ollama.chat(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": "Implement this phase now."}],
                stream=True,
                options={"temperature": temp, "num_predict": 4096, "repeat_penalty": 1.15}
            )

            pass_content = ""
            current_line = ""
            tokens = 0
            t0 = time.time()

            for chunk in stream:
                token = chunk["message"]["content"]
                pass_content += token
                current_line += token
                tokens += 1

                if '\n' in current_line:
                    ctx.console.print(f"[dim white]{escape(current_line.rstrip())}[/]")
                    current_line = ""

            if current_line:
                ctx.console.print(f"[dim white]{escape(current_line)}[/]")

            elapsed = time.time() - t0
            render_dashboard(tokens, elapsed, task_obj)

            if attempts > 1 and not task_obj.progress.get("last_critic", {}).get("is_complete", True):
                pass_content = Agents.reflection(model, current_task, pass_content,
                                                 task_obj.progress["last_critic"]["feedback"], ctx)

            process_output_to_files(pass_content, task_obj.directory, ctx)
            full_draft += "\n" + pass_content

            eval_result = Agents.critic(model, current_task, full_draft, ctx)
            task_obj.progress["last_critic"] = eval_result

            if eval_result["is_complete"]:
                ctx.console.print("[bold green]Phase approved ✓[/]")
                approved = True
                # ─── NO AUTO GIT COMMIT ───
                # The block below is intentionally commented out and will not run
                # if CONFIG["auto_git"]:
                #     try:
                #         ...
                #     except:
                #         ...
            else:
                ctx.console.print(f"[yellow]Review notes: {escape(eval_result['feedback'][:120])}...[/]")

        return full_draft, approved

    @staticmethod
    def final_summarizer(model: str, task: Task, ctx):
        ctx.console.print("\n[bold yellow]Generating execution guide...[/]")
        snapshot = get_live_snapshot(task.directory)
        prompt = f"""Project completed: {task.prompt}

Final file state:\n{snapshot}

Create professional Markdown guide:
- Exact commands to run the project
- Purpose of each main file
- Setup instructions
- Known limitations and suggested improvements

Output pure Markdown only.
"""
        try:
            res = ollama.generate(model=model, prompt=prompt)
            content = res["response"].strip()
            path = task.directory / "README-RomaTask.md"
            path.write_text(content, encoding="utf-8")
            ctx.console.print(Panel(
                Markdown(content),
                title="[bold green]Project Ready – Execution Guide[/]",
                border_style="green",
                padding=(1, 2),
            ))
            ctx.console.print(f"[dim]Guide saved to: {path}[/]")
        except Exception as e:
            ctx.console.print(f"[red]Failed to generate guide: {e}[/]")

# ────────────────────────────────────────────────
# UTILS (unchanged from your last version)
# ────────────────────────────────────────────────

def get_live_snapshot(base):
    base = Path(base)
    lines = []
    for p in sorted(base.rglob("*")):
        if ".romatask" in p.name or "__pycache__" in p.name or ".git" in str(p) or p.is_dir():
            continue
        if p.suffix.lower() not in {".py", ".html", ".css", ".js", ".json", ".md", ".txt"}:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            preview = "EMPTY" if not content.strip() else (content[:500] + "..." if len(content) > 500 else content)
            lines.append(f"FILE: {p.relative_to(base)}\n{preview}\n{'─'*60}")
        except:
            pass
    return "\n".join(lines) or "No code files yet."

def manifest_skeleton(structure: List[str], base: Path, ctx):
    ctx.console.print("[bold green]Creating project structure...[/]")
    for item in structure:
        item = item.strip("/\\")
        full = base / item
        if item.endswith("/") or "." not in Path(item).name:
            full.mkdir(parents=True, exist_ok=True)
            ctx.console.print(f"[dim cyan]DIR [/]{item}/")
        else:
            full.parent.mkdir(parents=True, exist_ok=True)
            if not full.exists():
                full.write_text("", encoding="utf-8")
            ctx.console.print(f"[dim white]FILE[/] {item}")

def process_output_to_files(text: str, base: Path, ctx):
    pattern = r'<file\s+path=["\'](.*?)["\'][^>]*>(.*?)</file>'
    matches = re.findall(pattern, text, re.DOTALL | re.I)
   
    if not matches:
        matches = re.findall(r'#\s*file\s*path=["\'](.*?)["\']\n(.*?)(?=\n#\s*file\s*path=|\n```|\Z)', text, re.DOTALL | re.IGNORECASE)
    if not matches:
        matches = re.findall(r'###\s*(.*?)\n```\w*\n(.*?)\n```', text, re.DOTALL | re.IGNORECASE)
   
    for path_str, content in matches:
        path_str = path_str.strip().replace("\\", "/")
        if any(bad in path_str.lower() for bad in ["relative", "[path", "actual", ".."]):
            continue
           
        proj_name = os.path.basename(base)
        if path_str.startswith(proj_name + "/"):
            path_str = path_str[len(proj_name)+1:]
           
        content = re.sub(r'^```[\w]*\n?', '', content.strip())
        content = re.sub(r'\n?```$', '', content).strip()
        content = re.sub(r'^\[CONTENT\]\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'\s*\[/CONTENT\]$', '', content, flags=re.IGNORECASE)
       
        target = base / path_str
        if not target.is_relative_to(base):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content + "\n", encoding="utf-8")
            ctx.console.print(f"[bold green]→[/] {path_str}")
        except Exception as e:
            ctx.console.print(f"[red]Write failed {path_str}: {e}[/]")

# ────────────────────────────────────────────────
# SWARM LOOP
# ────────────────────────────────────────────────

def run_swarm(task: Task):
    global current_task
    current_task = task

    with Progress(
        SpinnerColumn(style="bold cyan"),
        TextColumn("[progress.description]{task.description}", justify="right"),
        BarColumn(bar_width=None, finished_style="green", pulse_style="cyan"),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        if not task.queue:
            bp = Agents.planner(task.model, task.prompt, progress)
            task.queue = bp.get("tasks", [])
            task.structure = bp.get("structure", [])
            task.total_phases = len(task.queue)
            manifest_skeleton(task.structure, task.directory, progress)
            task.save()

        total_phases = task.total_phases or (len(task.progress["completed"]) + len(task.queue))
        main_task = progress.add_task("[bold green]Project Progress", total=total_phases or 1)

        deadline = task.start_time + task.duration * 60

        while time.time() < deadline and task.queue:
            phase = task.queue.pop(0)
            if phase in task.progress["completed"]:
                continue

            console.print(f"\n[bold cyan]Executing phase[/] [white]{phase}[/]")
            _, approved = Agents.executor(task.model, task, phase, progress)

            task.progress["completed"].append(phase)
            task.progress["history"].append(f"{phase} — approved={approved}")
            progress.update(main_task, advance=1)
            task.save()

            if not task.queue:
                console.print("\n[bold green]Mission complete.[/]")
                Agents.final_summarizer(task.model, task, progress)
                task.save(status="Completed")
                return

        if time.time() >= deadline:
            console.print("\n[bold red]Time allocation exceeded — swarm paused[/]")

# ────────────────────────────────────────────────
# CLI ENTRY
# ────────────────────────────────────────────────

def display_header():
    console.clear()
    banner = (
        "[bold magenta]RomaTask[/bold magenta] [dim]•[/dim] [bold cyan]RomanAILabs[/bold cyan]\n"
        "[white]Autonomous Agentic Code Orchestrator[/white]\n"
        "[dim]Streaming Execution • Reflection Loop • Enterprise Ready[/dim]"
    )
    console.print(Panel(
        banner,
        border_style="bright_blue",
        padding=(1, 4),
        expand=False,
        subtitle="[dim italic]v1.9.1[/dim italic]",
    ))

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        main_flow()

def main_flow():
    display_header()
    model = Prompt.ask("[bold]Model[/bold]", default=CONFIG["default_model"])

    console.print("\n[bold magenta]MODE SELECTION[/bold magenta]")
    mode_table = Table(show_header=False, box=None, padding=(0, 2))
    mode_table.add_row("[bold cyan]1[/bold cyan]", "Start [bold green]New[/bold green] Project")
    mode_table.add_row("[bold cyan]2[/bold cyan]", "[bold yellow]Resume[/bold yellow] or [bold red]Delete[/bold red] Existing")
    console.print(mode_table)

    choice = Prompt.ask("Select", choices=["1", "2"], default="1")
    projects_root = Path(CONFIG["projects_root"]).expanduser().resolve()
    projects_root.mkdir(parents=True, exist_ok=True)

    if choice == "2":
        projects = [d for d in projects_root.iterdir() if d.is_dir() and (d / ".romatask").exists()]
        if not projects:
            console.print("[yellow]No existing projects found.[/]")
            time.sleep(1.5)
            return main_flow()

        table = Table(title="Existing Projects", show_header=True, header_style="bold cyan")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Project Name", style="white")
        for i, p in enumerate(projects, 1):
            table.add_row(str(i), p.name)
        console.print(table)

        sel = Prompt.ask("Enter number (or [red]del N[/red] to delete)")
        if sel.lower().startswith("del "):
            try:
                idx = int(sel.split()[1]) - 1
                target = projects[idx]
                if Confirm.ask(f"Delete project [bold]{target.name}[/bold]?"):
                    shutil.rmtree(target)
                    console.print(f"[bold red]Project {target.name} deleted.[/]")
            except:
                console.print("[red]Invalid delete command.[/]")
            return main_flow()

        try:
            idx = int(sel) - 1
            proj_path = projects[idx]
        except:
            proj_path = projects[0]

        task = Task.load(str(proj_path))
        task.model = model
        task.duration = int(Prompt.ask("Session duration (minutes)", default=str(task.duration or 45)))
        task.start_time = time.time()
        console.print(f"[bold green]Project resumed:[/] {proj_path.name}")
        run_swarm(task)

    else:
        root_input = Prompt.ask("Root directory", default=str(projects_root))
        name = Prompt.ask("Project name")
        proj_path = Path(root_input).expanduser() / name
        proj_path.mkdir(parents=True, exist_ok=True)

        if (proj_path / ".romatask").exists():
            console.print("[yellow]Existing project detected — switching to resume mode[/]")
            task = Task.load(str(proj_path))
            task.model = model
        else:
            task = Task(str(proj_path), model)
            task.prompt = Prompt.ask("Project objective")
            task.duration = int(Prompt.ask("Max duration (minutes)", default="45"))

        task.start_time = time.time()
        run_swarm(task)

if __name__ == "__main__":
    cli()
