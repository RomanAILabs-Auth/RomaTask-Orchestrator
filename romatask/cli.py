# cli.py
# Copyright RomanAILabs - Daniel Harding
import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.prompt import Prompt
import os
import json
import time
import signal
import sys
import uuid
import re
from pathlib import Path
from typing import Optional, List, Dict
import ollama
from romatask.utils import setup_logging, get_safe_filename

console = Console()

# Global dirs
STATE_DIR = os.path.expanduser('~/.romatask/tasks')
LOG_DIR = os.path.expanduser('~/.romatask/logs')
CONFIG_PATH = os.path.expanduser('~/.romatask/config.json')

logger = setup_logging(LOG_DIR, False)

def load_config() -> Dict[str, any]:
    default = {"default_model": "qwen2.5", "max_recursion_depth": 10, "chunk_minutes": 5, "max_queue": 5}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data: return data
        except Exception:
            pass 
            
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(default, f, indent=4)
    return default

CONFIG = load_config()

class Task:
    def __init__(self, task_id: Optional[str] = None):
        self.id: str = task_id or str(uuid.uuid4())[:8]
        self.model: str = CONFIG['default_model']
        self.directory: str = os.path.expanduser('~/Desktop')
        self.prompt: str = ''
        self.duration: int = 60  
        self.progress: Dict[str, any] = {'completed': [], 'memory': ''} 
        self.current_depth: int = 0
        self.paused: bool = False
        self.start_time: float = time.time()
        self.checkpoints: List[Dict[str, any]] = []
        self.queue: List[str] = []
        self.structure: List[str] = [] 

def save_state(task: Task) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    state_path = os.path.join(STATE_DIR, f"{task.id}.json")
    with open(state_path, 'w', encoding='utf-8') as f:
        json.dump(vars(task), f, default=str, indent=4)
    logger.info(f"Task saved: {task.id}")

def load_state(task_id: str) -> Task:
    state_path = os.path.join(STATE_DIR, f"{task_id}.json")
    if os.path.exists(state_path):
        with open(state_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        task = Task(task_id)
        for k, v in data.items():
            setattr(task, k, v)
        return task
    raise FileNotFoundError(f"Task {task_id} not found")

def signal_handler(sig, frame):
    console.print("\n[bold yellow]Pausing task. Saving state...[/]")
    if 'current_task' in globals():
        save_state(globals()['current_task'])
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ==========================================
# THE AGENT SWARM
# ==========================================
class Agents:
    @staticmethod
    def planner(model: str, objective: str, progress_ctx) -> Dict[str, List[str]]:
        progress_ctx.console.print(f"[bold yellow]⚙️ Planner Agent analyzing project blueprint...[/]")
        meta_prompt = f"""
        You are the Master Architect for RomanAILabs. 
        Objective: {objective}

        1. Map out the exact file structure (folders and files) required.
        2. Create a step-by-step implementation roadmap focusing on code content.

        Respond ONLY with a valid JSON object:
        {{
            "structure": ["folder/", "folder/file.ext", "root_file.ext"],
            "tasks": ["Write logic for folder/file.ext", "Build UI in root_file.ext"]
        }}
        """
        try:
            res = ollama.generate(model=model, prompt=meta_prompt, format="json")
            data = json.loads(res['response'])
            return {
                "tasks": data.get('tasks', [objective]),
                "structure": data.get('structure', [])
            }
        except Exception as e:
            return {"tasks": [objective], "structure": []}

    @staticmethod
    def critic(model: str, task: str, draft: str, progress_ctx) -> dict:
        progress_ctx.console.print(f"\n[bold magenta]🔍 Critic Agent reviewing output...[/]")
        prompt = f"""
        You are the QA Critic Agent. 
        Current Sub-Task: "{task}"
        
        Review the following generated output:
        ---
        {draft[-3000:]}
        ---

        Check if it contains valid <file path="...">...</file> blocks.
        Verify that the requested code (e.g. 5 lines) is actually present and not just described.
        
        Respond ONLY in JSON format:
        {{
            "is_complete": true or false,
            "feedback": "If false, explain exactly what is missing or wrong."
        }}
        """
        try:
            res = ollama.generate(model=model, prompt=prompt, format="json")
            return json.loads(res['response'])
        except Exception:
            return {"is_complete": False, "feedback": "Continue building the requested code."}

    @staticmethod
    def executor(model: str, objective: str, current_task: str, task_obj: 'Task', progress_ctx) -> str:
        full_draft = ""
        attempts = 0
        
        while attempts < 5:
            # Always get fresh disk state at the start of every pass
            snapshot = get_live_snapshot(task_obj.directory)
            
            system_instruction = (
                "You are the Lead Executor Agent at RomanAILabs. "
                f"Overall Project: {objective}. "
                "IMPORTANT: The directory structure exists. Provide code content ONLY. "
                "Output code inside <file path=\"relative/path.ext\"> blocks. "
                "DO NOT use markdown code blocks inside the file tags. "
                f"\n[LIVE DISK SNAPSHOT - CURRENT CONTENT]\n{snapshot}"
            )
            
            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Current Task: {current_task}. Provide the full file content now."}
            ]
            
            if full_draft:
                messages.append({"role": "assistant", "content": full_draft})
                messages.append({"role": "user", "content": "The previous output was saved but might be incomplete. Please continue or fix it based on the requirements."})

            stream = ollama.chat(model=model, messages=messages, stream=True)
            current_pass_output = ""
            current_line = ""
            
            progress_ctx.console.print(f"\n[bold cyan]--- Pass {attempts + 1} Generating... ---[/]")
            
            for chunk in stream:
                token = chunk['message']['content']
                current_pass_output += token
                current_line += token
                
                if '\n' in current_line:
                    parts = current_line.split('\n')
                    for p in parts[:-1]:
                        progress_ctx.console.print(f"[dim white]{p}[/]")
                    current_line = parts[-1]
            
            if current_line:
                progress_ctx.console.print(f"[dim white]{current_line}[/]")
            
            # CRITICAL: Save files IMMEDIATELY after each pass regardless of Critic happiness
            process_output_to_files(current_pass_output, task_obj.directory, progress_ctx)
            
            full_draft += "\n" + current_pass_output
            
            evaluation = Agents.critic(model, current_task, full_draft, progress_ctx)
            if evaluation.get("is_complete", False):
                progress_ctx.console.print("[bold green]✅ Logic Validated & Committed.[/]")
                break
            else:
                feedback = evaluation.get('feedback', 'Continue.')
                progress_ctx.console.print(f"[bold yellow]⚠️ Iteration Required: {feedback}[/]")
                attempts += 1
                
        return full_draft

def get_live_snapshot(base_dir: str) -> str:
    snapshot = ""
    b = Path(base_dir).expanduser().resolve()
    if not b.exists(): return "Empty"
    
    for file in sorted(b.rglob("*")):
        if file.is_file() and file.suffix in ['.py', '.html', '.css', '.js', '.md']:
            try:
                content = file.read_text(encoding="utf-8", errors="ignore")
                # Provide enough context (last 500 chars) for the model to see its previous work
                preview = content if len(content) < 500 else "..." + content[-500:]
                snapshot += f"FILE: {file.relative_to(b)} ({len(content)} chars)\nCONTENT:\n{preview}\n{'-'*20}\n"
            except:
                pass
    return snapshot if snapshot else "Project skeleton manifested (all files currently empty)."

def manifest_skeleton(structure: List[str], base_dir: str, progress_ctx):
    progress_ctx.console.print(f"[bold green]🏗️  Manifesting Project Skeleton...[/]")
    for item in structure:
        clean_item = item.strip().replace("\\", "/")
        full_path = os.path.normpath(os.path.join(base_dir, clean_item))
        if not full_path.startswith(os.path.abspath(base_dir)): continue

        if clean_item.endswith('/') or "." not in os.path.basename(clean_item):
            os.makedirs(full_path, exist_ok=True)
            progress_ctx.console.print(f"[dim cyan]  DIR  [/] {clean_item}")
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if not os.path.exists(full_path):
                with open(full_path, 'w', encoding='utf-8') as f: f.write("")
            progress_ctx.console.print(f"[dim white]  FILE [/] {clean_item}")

def process_output_to_files(output: str, base_dir: str, progress_ctx):
    # Regex designed to catch XML tags even if model adds markdown wrappers accidentally
    matches = re.findall(r'<file\s+path=["\'](.*?)["\'][^>]*>(.*?)</file>', output, re.DOTALL | re.IGNORECASE)
    for path_str, content in matches:
        clean_path = path_str.strip().replace("\\", "/")
        # Strip potential markdown code blocks if the model was "helpful" inside the tag
        clean_content = re.sub(r'^```\w*\n', '', content.strip())
        clean_content = re.sub(r'\n```$', '', clean_content)
        
        full_path = os.path.normpath(os.path.join(base_dir, clean_path))
        if not full_path.startswith(os.path.normpath(base_dir)): continue
        
        # Committing to disk
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(clean_content + "\n")
        progress_ctx.console.print(f"[bold green]💾 Disk Commit:[/] [white]{clean_path}[/]")

def run_task_in_chunks(task: Task, verbose: bool = False) -> None:
    global current_task
    current_task = task
    end_time = task.start_time + (task.duration * 60)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TimeRemainingColumn()) as progress:
        main_task = progress.add_task("[green]Orchestrating Swarm...[/]", total=task.duration * 60)

        if not task.queue and not task.progress.get('completed'):
            blueprint = Agents.planner(task.model, task.prompt, progress)
            task.queue = blueprint['tasks']
            task.structure = blueprint['structure']
            manifest_skeleton(task.structure, task.directory, progress)
            save_state(task)

        while time.time() < end_time and task.queue:
            current_sub_task = task.queue.pop(0)
            if current_sub_task in task.progress.get('completed', []): continue
                
            progress.console.print(f"\n[bold cyan]🚀 Implementation Phase:[/] {current_sub_task}")
            
            # Pass the whole task object so executor can access directory and state
            Agents.executor(task.model, task.prompt, current_sub_task, task, progress)

            task.progress.setdefault('completed', []).append(current_sub_task)
            progress.update(main_task, completed=time.time() - task.start_time)
            save_state(task)

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        display_welcome()
        run_new_task()

def display_welcome() -> None:
    console.print(Panel("[bold magenta]RomaTask v1.6.1[/]\n[cyan]Agentic Swarm Engine Powered by Ollama[/]\n[dim]Real-Time Disk Commitment • Live Context Enabled[/]", title="RomanAILabs", expand=False, border_style="bright_blue"))

def run_new_task() -> None:
    task = Task()
    task.model = Prompt.ask("Model", default="qwen2.5")
    task.directory = os.path.abspath(os.path.expanduser(Prompt.ask("Directory", default="~/Desktop/RomaProject")))
    task.prompt = Prompt.ask("Task prompt")
    task.duration = int(Prompt.ask("Duration (min)", default="60"))
    os.makedirs(task.directory, exist_ok=True)
    save_state(task)
    run_task_in_chunks(task)

if __name__ == '__main__':
    cli()
