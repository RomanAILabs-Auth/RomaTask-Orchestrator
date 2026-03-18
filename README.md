# RomaTask Orchestrator v1.6.1
### Enterprise AI Project Architect by RomanAILabs

**RomaTask** is a local-first, autonomous agentic swarm designed to build complex software projects from the ground up. Powered by **Ollama (qwen2.5)**, it utilizes a sophisticated **Planner/Executor/Critic** loop to manifest full directory structures and populate them with production-ready code.

## 🚀 Core Innovations

### 🏛️ Architecture First
RomaTask does not "guess" where files go. The **Planner Agent** first maps out a complete project skeleton (folders and stubs). The system manifests this structure on your disk before a single line of code is written, ensuring structural integrity.

### 💾 Real-Time Disk Commits
Unlike other agents that wait for a "final" output, RomaTask commits code to your disk after every pass. This allows the system to take a **Live Disk Snapshot**, feeding the actual file content back into the AI's prompt for perfect contextual continuity.

### 🛡️ Protected Implementation
Code is delivered via a specialized XML engine (`<file path="...">`). This "boxes" the AI, preventing terminal hallucinations and ensuring it only interacts with the project structure.

## 🛠️ Features
- **Swarm Logic:** Dedicated agents for Planning, Execution, and QA Critique.
- **Persistence:** Pause and resume any task with unique session IDs.
- **Live Streaming:** Line-buffered token streaming for a clean, professional CLI experience.
- **Local Sovereignty:** 100% local execution via Ollama.

## 🏁 Installation
```bash
# Clone the repository
git clone [https://github.com/RomanAILabs-Auth/RomaTask.git](https://github.com/RomanAILabs-Auth/RomaTask.git)
cd RomaTask

# Install in editable mode
pip install -e .
```

## ⚖️ License
Copyright © 2026 RomanAILabs - Daniel Harding. All rights reserved.
