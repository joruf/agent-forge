# AgentForge User Manual

**Version 0.1.0**  
**Author:** Joachim Ruf · Loresoft

This manual describes how to install, configure, and use AgentForge on Linux. AgentForge is a multi-agent AI desktop platform for coding tasks and collaborative workflows, with support for local Ollama models and optional OpenAI cloud models.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [First Run — Setup Wizard](#4-first-run--setup-wizard)
5. [Starting and Stopping](#5-starting-and-stopping)
6. [User Interface](#6-user-interface)
7. [Working Modes](#7-working-modes)
8. [Chats and Messaging](#8-chats-and-messaging)
9. [Memory Settings](#9-memory-settings)
10. [Settings](#10-settings)
11. [Model Management and Routing](#11-model-management-and-routing)
12. [Shell Commands and Approvals](#12-shell-commands-and-approvals)
13. [Remote Ollama (NAS / Docker)](#13-remote-ollama-nas--docker)
14. [OpenAI (Optional)](#14-openai-optional)
15. [Language (English / German)](#15-language-english--german)
16. [Troubleshooting](#16-troubleshooting)
17. [Data and Privacy](#17-data-and-privacy)

---

## 1. Overview

AgentForge combines:

- **Single-agent coding mode** — one AI assistant reads, writes, and refactors files in your workspace.
- **Multi-agent mode** — several specialized roles (Developer, Reviewer, Architect, etc.) collaborate on a task. You see their internal discussion in the agent history panel.
- **Human-in-the-loop** — shell commands outside a safe whitelist require your explicit approval.
- **Configurable memory** — conversation context is stored per chat or globally, with adjustable token limits.
- **Model routing** — different models can be assigned to different task types (coding, SQL, research, etc.).

The application consists of:

| Component | Role |
|-----------|------|
| Python backend (FastAPI) | API, agents, tools, storage |
| React frontend | Chat UI, settings, setup wizard |
| Ollama (local or remote) | LLM inference |
| SQLite database | Chats, messages, memory |

---

## 2. Requirements

- **OS:** Linux (desktop)
- **Python:** 3.12+
- **Node.js:** 18+ (for frontend build/dev)
- **Ollama:** reachable via HTTP (local or network)
- **RAM:** depends on models; 16 GB+ recommended for medium models; 64 GB allows larger models
- **Workspace:** a directory AgentForge can read and write (default: your Documents folder)

Optional:

- OpenAI API key for GPT models
- Chromium (opened automatically in app mode if Tauri is not used)

---

## 3. Installation

From the AgentForge project directory:

```bash
python3 install.py
```

This script:

1. Creates a Python virtual environment in `backend/.venv`
2. Installs Python dependencies
3. Installs frontend npm packages
4. Copies `.env.example` to `backend/.env` if missing

### Configure environment

Edit `backend/.env`:

```env
AGENTFORGE_OLLAMA_BASE_URL=http://192.168.1.10:11434
AGENTFORGE_DEFAULT_MODEL=ollama/llama3.1:8b
AGENTFORGE_WORKSPACE_ROOT=/home/youruser/Documents
AGENTFORGE_LLM_AUTO_ROUTING=true
AGENTFORGE_UI_LANGUAGE=en
```

| Variable | Description |
|----------|-------------|
| `AGENTFORGE_OLLAMA_BASE_URL` | URL of your Ollama server |
| `AGENTFORGE_DEFAULT_MODEL` | Fallback LiteLLM model string |
| `AGENTFORGE_WORKSPACE_ROOT` | Root directory for file tools |
| `AGENTFORGE_LLM_AUTO_ROUTING` | Auto-select model by task type |
| `AGENTFORGE_UI_LANGUAGE` | UI language: `en` or `de` (default: `en`) |

---

## 4. First Run — Setup Wizard

On first start, AgentForge shows a **setup wizard** that verifies your environment.

### Steps

| Step | Purpose |
|------|---------|
| Welcome | Overview of checks |
| Ollama | Enter server URL and test connectivity |
| Models | Import installed Ollama models into the registry |
| OpenAI | Optional API key test |
| Workspace | Verify directory exists and is writable |
| Verification | Run all tests including a short inference call |
| Complete | Finish setup |

### Skip and resume

- Click **Skip** at any time to use the app immediately.
- Later, choose **Resume setup** in the sidebar footer to continue where you left off.
- Progress is stored in `~/.local/share/agentforge/setup_state.json`.

### Required vs optional tests

**Required:** backend, Ollama reachability, workspace write access.

**Optional:** OpenAI key, model registry entries, inference test (warnings if models are missing).

---

## 5. Starting and Stopping

### Start

```bash
python3 run.py
```

This starts the backend (port 8765) and the frontend (port 5173), then opens a desktop window.

### Stop

```bash
python3 stop.py
```

Or close the application window and press `Ctrl+C` in the terminal if you started it in the foreground.

### Backend not reachable

If the UI shows *Backend is not reachable*:

1. Ensure `python3 run.py` is running
2. Check that port 8765 is free
3. Click **Reconnect**

---

## 6. User Interface

```
┌─────────────────┬──────────────────────────────────────────┐
│ Sidebar         │ Chat panel                               │
│                 │  ┌─────────────────────────────────────┐ │
│ + Coding        │  │ Messages                            │ │
│ + Multi-Agent   │  │                                     │ │
│ Chat list       │  └─────────────────────────────────────┘ │
│                 │  [Input area]              [Send]        │
│ Settings ⚙      │                                          │
│ Footer links    │  (Multi-agent: Agent history on right)   │
└─────────────────┴──────────────────────────────────────────┘
```

### Sidebar

- **+ Coding** — new single-agent chat
- **+ Multi-Agent** — new multi-role chat
- Chat list with delete (×) button
- **Settings** (gear icon)
- **Manage models**, **User manual**, **About AgentForge**
- **Resume setup** (if setup was skipped)

### Resizing

Drag the vertical bar between sidebar and chat to adjust sidebar width (220–520 px). Width is saved automatically.

### Theme

In **Settings → Appearance**, choose Dark mode or Light mode.

---

## 7. Working Modes

### Coding (single-agent)

Best for:

- Writing or editing code
- Reading and explaining files
- Running whitelisted shell commands
- Focused tasks with one assistant

The agent uses tools: `read_file`, `write_file`, `list_directory`, `run_shell`, etc.

### Multi-Agent

Best for:

- Larger tasks needing design, implementation, and review
- Structured collaboration between roles

Default roles in a new multi-agent chat:

- Project Manager
- Developer
- Reviewer

Enable/disable roles via checkboxes in the chat header before sending a message.

**Agent history** (right panel) shows inter-agent messages in real time during processing.

---

## 8. Chats and Messaging

### Sending messages

- Type in the input area at the bottom
- **Enter** — send
- **Shift+Enter** — new line

While the agent works, a *Agent is working…* indicator is shown.

### Chat title

Titles are generated automatically from the first exchange when possible.

### Deleting chats

Click **×** next to a chat in the sidebar. Deletion is permanent.

---

## 9. Memory Settings

Each chat has memory controls in the header:

| Setting | Description |
|---------|-------------|
| **Memory (tokens)** | How much context the agent retains (8K–128K) |
| **Per chat / Global** | Scope of stored memory |

- **Per chat** — memory applies only to this conversation
- **Global** — shared context across chats (use carefully)

Default memory for new chats is set in **Settings → Memory (tokens)**.

Higher token values improve long-context tasks but increase RAM use and latency.

---

## 10. Settings

Open via the gear icon in the sidebar.

| Field | Description |
|-------|-------------|
| Language | English or Deutsch |
| Workspace directory | Root path for all file operations |
| Ollama URL | Base URL of Ollama server |
| Default model | Fallback when routing finds no match |
| Auto routing | Pick model by detected task type |
| OpenAI API key | Optional cloud models |
| Memory (tokens) | Default for new chats |
| Appearance | Dark / Light theme |

Click **Save** to apply. Most settings apply immediately to the running backend (restart required only for `.env` values loaded at startup if not changed via UI).

**Manage models & routing** opens the model manager (see next section).

---

## 11. Model Management and Routing

Open via **Manage models** in the sidebar or settings.

### Add models

1. Enter an Ollama tag (e.g. `deepseek-coder:6.7b-instruct-q4_K_M`)
2. Click **Suggest** for catalog-based recommendations
3. Click **Add**
4. Or click **Import from Ollama** to import all installed models

### Assign tasks

Each model card has task checkboxes:

| Task | Typical use |
|------|-------------|
| Coding | Code generation, refactoring |
| Code review | Quality checks |
| Architecture | System design |
| Research | Information gathering |
| Documentation | Docs and README |
| Coordination | Multi-agent leadership |
| SQL | Database queries |
| Vision/OCR | Image analysis |
| Finance | Financial analysis |
| General | Fallback tasks |
| Chat title | Short title generation |

### Routing per task

The **Routing per task** table lets you override which model handles each task:

- **Automatic** — uses routing rules and assigned tasks
- **Specific model** — always use that model for the task

Enable **Automatic model routing** in Settings for task detection from message content.

### Recommended models (CPU-focused)

For CPU inference with ~64 GB RAM, consider:

- `deepseek-coder:6.7b` — coding
- `codellama:13b` — stronger coding
- `llama3.1:8b` — general
- `qwen2.5:7b` — general / research
- `sqlcoder` — SQL tasks

Pull models on your Ollama host:

```bash
ollama pull deepseek-coder:6.7b-instruct-q4_K_M
```

---

## 12. Shell Commands and Approvals

Agents can run shell commands in your workspace via `run_shell`.

### Whitelist (auto-approved)

Examples: `ls`, `cat`, `grep`, `git`, `python3`, `npm`, `mkdir`, `echo`

### Blacklist (blocked)

Examples: `rm`, `sudo`, `curl`, `wget`, `ssh`, `dd`

### Approval required

Any command not on the whitelist triggers an **Approvals required** panel:

- **Allow** — run the command
- **Deny** — reject; agent receives an error message

Review the full command string before approving.

---

## 13. Remote Ollama (NAS / Docker)

AgentForge works with Ollama on another machine.

### Docker example

On your NAS/server:

```bash
docker run -d -v ollama:/root/.ollama -p 11434:11434 --name ollama ollama/ollama
docker exec -it ollama ollama pull llama3.1:8b
```

### AgentForge configuration

Set in `.env` or Settings:

```
AGENTFORGE_OLLAMA_BASE_URL=http://192.168.1.10:11434
```

Use the LAN IP of the Ollama host, not `localhost`, when AgentForge runs on a different machine.

### Verify

In the setup wizard or terminal:

```bash
curl http://192.168.1.10:11434/api/tags
```

You should see a JSON list of installed models.

---

## 14. OpenAI (Optional)

To use OpenAI models alongside Ollama:

1. Obtain an API key from [OpenAI](https://platform.openai.com/)
2. Enter it in **Settings → OpenAI API key** or the setup wizard
3. Set default/routing models to OpenAI format if needed (e.g. `gpt-4o-mini`)

Without a key, OpenAI-related setup tests are skipped.

---

## 15. Language (English / German)

AgentForge supports **English** (default) and **German**.

### Change language

1. Open **Settings**
2. Select **Language** → English or Deutsch
3. The UI updates immediately; backend messages (setup tests, roles, task labels) follow the selected language

Language is stored in:

- Browser `localStorage` (instant UI)
- Backend runtime setting `ui_language`
- Optional: `AGENTFORGE_UI_LANGUAGE` in `.env` at startup

Translation files:

- Frontend: `frontend/src/i18n/locales/en.json`, `de.json`
- Backend: `backend/agentforge/i18n/en.json`, `de.json`

---

## 16. Troubleshooting

### Ollama not reachable

| Symptom | Action |
|---------|--------|
| Connection refused | Start Ollama; check URL and firewall |
| Wrong URL | Fix `AGENTFORGE_OLLAMA_BASE_URL` |
| No models | Run `ollama pull <model>` on the server |

### LLM error in chat

Messages like `LLM error: ...` mean the model call failed. Check Ollama logs, model name, and available RAM.

### Workspace errors

Ensure the workspace path exists and your user has read/write permissions.

### Port conflicts

`run.py` attempts to free ports 8765 and 5173. If problems persist:

```bash
python3 stop.py
fuser -k 8765/tcp 5173/tcp
python3 run.py
```

### Setup wizard keeps appearing

Complete all steps and click **Launch AgentForge**, or finish via **Resume setup**. State is in `~/.local/share/agentforge/setup_state.json`.

To reset setup:

```bash
rm ~/.local/share/agentforge/setup_state.json
```

### Tests pass but chat fails

Routing may point to a model not loaded in Ollama. Open **Manage models**, verify tags match `ollama list` output, and run a test in the setup wizard.

---

## 17. Data and Privacy

AgentForge stores data locally:

| Path | Content |
|------|---------|
| `~/.local/share/agentforge/agentforge.db` | Chats, messages, memory |
| `~/.local/share/agentforge/model_config.json` | Models and routing |
| `~/.local/share/agentforge/setup_state.json` | Setup wizard progress |

File operations are restricted to **workspace_root**. Paths outside the workspace are rejected.

LLM requests go to your configured Ollama server and/or OpenAI — no third-party analytics are built into AgentForge.

---

## Quick Reference

| Action | How |
|--------|-----|
| Start app | `python3 run.py` |
| Stop app | `python3 stop.py` |
| New coding chat | Sidebar → + Coding |
| New multi-agent chat | Sidebar → + Multi-Agent |
| Settings | Sidebar → ⚙ |
| Models | Sidebar → Manage models |
| User manual | Sidebar → User manual |
| Change language | Settings → Language |
| Approve shell cmd | Allow in approval panel |
| Resume setup | Sidebar → Resume setup |

---

*AgentForge · Loresoft · Joachim Ruf*
