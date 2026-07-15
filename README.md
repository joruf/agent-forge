# AgentForge

**Multi-agent AI desktop platform** — coding assistance, collaborative task processing, and human-in-the-loop workflows with local Ollama and optional cloud LLMs. Built with **Python (FastAPI)** and **React**, so the core app is cross-platform.

| | |
|---|---|
| **License** | MIT |
| **Platform** | Cross-platform (Linux, Windows, macOS) — Linux best tested |
| **Stack** | Python 3.12 · FastAPI · React · TypeScript · LiteLLM · SQLite |

---

## Quick install (copy & paste)

Use the built-in project installer (`install.py`).

### Linux (Debian/Ubuntu/Linux Mint)

```bash
git clone https://github.com/joruf/agent-forge.git
cd agent-forge
python3 install.py --system
python3 install.py
cp -n .env.example backend/.env
```

### macOS

Prerequisites: install `git`, Python 3.12+, Node.js 20+ (with npm).

```bash
git clone https://github.com/joruf/agent-forge.git
cd agent-forge
python3 install.py
cp -n .env.example backend/.env
```

### Windows (PowerShell)

Prerequisites: install Git, Python 3.12+, Node.js 20+ (with npm).

```powershell
git clone https://github.com/joruf/agent-forge.git
cd agent-forge
py -3 install.py
copy .env.example backend\.env
```

---

## What is AgentForge?

AgentForge is a local-first AI assistant that can read and write files in your workspace, run shell commands (with approval), and coordinate multiple specialized agent roles on complex tasks. It connects to **Ollama** on your machine or network (e.g. Synology NAS) and optionally to **OpenAI, Anthropic, Google Gemini, Groq, and Mistral** cloud APIs.

### Key features

- **Single-agent coding mode** — one developer agent with file and shell tools
- **Multi-agent mode** — six built-in roles collaborate; live agent history panel
- **Human-in-the-loop** — shell command whitelist + approval dialog for everything else
- **Expandable agent history** — truncated messages expand on click
- **Configurable memory** — per-chat token budget (100 – 128 000 tokens), configured in Properties
- **LLM auto-routing** — task-type based model selection (coding, SQL, research, …)
- **Multi-provider support** — Ollama (local/remote) + OpenAI, Claude, Gemini, Groq, Mistral via LiteLLM
- **Setup wizard** — guided first-run checks for Ollama, models, workspace, API keys
- **Persistent chats** — SQLite storage with auto-generated titles
- **Bilingual UI** — English and German
- **Desktop integration** — browser or Chromium app window; optional Tauri build; automated menu shortcut on Linux

### Built-in agent roles

| Role | Purpose |
|------|---------|
| Developer | Writes and edits code in the workspace |
| Reviewer | Code review, bugs, best practices |
| Architect | System design and module structure |
| Researcher | Research and technical summaries |
| Documentation | README, API docs, user guides |
| Project Manager | Coordinates agents, involves the user |

Custom roles can be added as YAML files in `assets/roles/`.

---

## User interface layout

This section describes how AgentForge is **visually structured**, which **UI areas** exist, and what they are called in the interface and in the frontend code (CSS class names in `frontend/src/styles/app.css`).

### Main screen (default view)

When the backend is running, the app uses a **two-column layout**: a fixed **sidebar** on the left and the **chat area** on the right. A thin **sidebar resizer** sits between them and can be dragged to change the sidebar width.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  .app                                                                       │
│ ┌──────────────────┬─┬──────────────────────────────────────────────────┐ │
│ │ .sidebar-shell   │ │ .chat-shell                                      │ │
│ │ ┌──────────────┐ │R│ ┌──────────────────────────────────────────────┐ │ │
│ │ │ .sidebar     │ │E│ │ .model-readiness-banner (if models fail)     │ │ │
│ │ │              │ │S│ ├──────────────────────────────────────────────┤ │ │
│ │ │              │ │I│ │ .chat-panel                                  │ │ │
│ │ │              │ │Z│ │  or .chat-panel.empty (no chat selected)     │ │ │
│ │ │              │ │E│ │                                              │ │ │
│ │ │              │ │R│ │                                              │ │ │
│ │ └──────────────┘ │ │ └──────────────────────────────────────────────┘ │ │
│ └──────────────────┴─┴──────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Area | UI name (EN) | UI name (DE) | Root CSS class |
|------|----------------|--------------|----------------|
| Application root | — | — | `.app` |
| Sidebar container | Sidebar | Sidebar | `.sidebar-shell` → `.sidebar` |
| Width handle | Sidebar resize | Menübreite anpassen | `.sidebar-resizer` |
| Main content | Chat area | Chat-Bereich | `.chat-shell` |
| Active chat view | Chat panel | Chat-Panel | `.chat-panel` |
| No chat selected | Empty state | Leerer Zustand | `.chat-panel.empty` |

**Default on start:** **Multi-Agent** is pre-selected in the sidebar (`.mode-btn.active` on **+ Multi-Agent**).

---

### Sidebar (`.sidebar`)

The sidebar is the primary navigation. It contains the app title, a gear menu, chat-mode buttons, and the list of saved chats.

```
┌─────────────────────────┐
│ .sidebar-header         │
│  AgentForge    [⚙ menu] │  ← .sidebar-menu-trigger
├─────────────────────────┤
│ .sidebar-actions        │
│ [+ Quick Chat]          │  ← .mode-btn
│ [+ Single Agent]        │
│ [+ Multi-Agent] ★       │  ← default active
├─────────────────────────┤
│ .chat-list              │
│ ┌─ .chat-item ────────┐ │
│ │ .chat-item-btn      │ │
│ │  .chat-title        │ │
│ │  .chat-mode         │ │
│ │  .chat-item-status  │ │  ← running clock / completed check
│ │              [×]    │ │  ← .chat-delete
│ └─────────────────────┘ │
│  … more chats …         │
├─────────────────────────┤
│ .sidebar-footer         │  ← optional: Resume setup
└─────────────────────────┘
```

| Element | Description | CSS class |
|---------|-------------|-----------|
| **App title** | “AgentForge” heading | `.sidebar-header` `h1` |
| **Menu button** | Gear icon (⚙), opens dropdown | `.sidebar-menu-trigger` |
| **Menu popup** | Dropdown with links to settings, manual, models, about | `.sidebar-menu-popup` |
| **Properties** | Opens settings dialog (DE: *Eigenschaften*) | menu item → **Settings modal** |
| **Manage models** | Opens model manager | menu item → **Models modal** |
| **User manual** | Opens `docs/USER_MANUAL.html` | menu item |
| **About** | Opens about dialog | menu item → **About modal** |
| **New chat buttons** | Quick Chat / Single Agent / Multi-Agent | `.sidebar-actions` `.mode-btn` |
| **Chat list** | All persisted chats, newest on top | `.chat-list` |
| **Chat item** | One row per chat; click to open | `.chat-item` / `.chat-item-btn` |
| **Chat title** | Double-click to rename | `.chat-title` / `.chat-title-input` |
| **Chat mode label** | Shows Quick / Single / Multi | `.chat-mode` |
| **Run status icon** | Clock while agents work, check when done | `.chat-item-status` |
| **Delete chat** | × button on each row | `.chat-delete` |
| **Resume setup** | Shown if first-run wizard was dismissed | `.sidebar-footer` `.setup-resume-link` |

---

### Chat panel (`.chat-panel`)

Shown in the chat area when a chat is selected or a **new chat draft** exists (after clicking a mode button). If neither applies, the **empty state** shows: *“Select a chat or create a new one.”*

```
┌──────────────────────────────────────────────────────────────────────────┐
│ .chat-header                                                             │
│  .chat-header-title: [Chat title]  (.badge: mode)                       │
│  .role-selector: role chips (Single / Multi only, not Quick Chat)        │
│  .memory-controls: command history btn, execution strategy (Multi)       │
├──────────────────────────────────────────────────────────────────────────┤
│ .approval-panel (if shell/command needs approval)                        │
├──────────────────────────────────────────────────────────────────────────┤
│ .chat-body                                                               │
│ ┌───────────────────────────────┬────────────────────────────────────┐ │
│ │ .messages                     │ .agent-history (Multi-Agent only)  │ │
│ │  message bubbles…             │  Agent-Verlauf / agent discussions │ │
│ └───────────────────────────────┴────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────┤
│ .chat-input                                                              │
│  .chat-blocked-notice (if models not ready)                              │
│  textarea + Send / Stop                                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

| Element | Description | CSS class |
|---------|-------------|-----------|
| **Chat header** | Title + mode badge + controls | `.chat-header` |
| **Mode badge** | Quick Chat / Single Agent / Multi-Agent | `.badge` |
| **Role selector** | Pick agent role(s); Single = radio, Multi = checkboxes | `.role-selector` `.role-chip` |
| **Auto role** | Automatic role selection (Single Agent) | `.role-chip-auto` |
| **Execution strategy** | Auto / Serial / Parallel / Hybrid (Multi-Agent) | `.memory-controls` `select` |
| **Command history** | Shell commands run in this chat | `.command-history-btn` → `CommandHistoryModal` |
| **Approval panel** | Approve or deny risky shell commands | `.approval-panel` |
| **Message list** | Scrollable chat transcript | `.messages` |
| **Agent history** | Side panel with inter-agent messages (Multi-Agent only) | `.agent-history` |
| **Chat input** | Message textarea, Send, Stop while running | `.chat-input` |

---

### Message bubbles (inside `.messages`)

Each line in the transcript is a **message bubble** (`.message`). Styling differs by role:

| Type | Label in header | CSS classes | Border colour |
|------|-----------------|-------------|---------------|
| **User message** | “You” / *Du* | `.message.message-user` | Accent (blue) |
| **Assistant reply** | Agent name or “Assistant” | `.message.message-assistant` | Green |
| **Agent discussion** | Agent name | `.message.message-agent` | Green |
| **Context plugin result** | “Plugin {name}” (e.g. Plugin Weather) | `.message.message-agent` | Green |
| **Streaming reply** | Agent name while tokens arrive | `.message-assistant.message-streaming` | Green |
| **Loading placeholder** | Agent name + clock | `.message-assistant.message-loading` | Green |
| **Error** | — | `.message.message-error` | Red |

Common parts of every bubble:

| Part | CSS class |
|------|-----------|
| Header row (name, model, time) | `.message-header` |
| Model name (if routed) | `.message-model` |
| Timestamp + copy (+ resend on last user msg) | `.message-meta` `.message-time` `.message-copy-btn` |
| Body text (expandable) | `ExpandableText` → `.expandable-text` |
| Plugin trigger reason | `.message-plugin-reason` |

**Context plugins** appear as normal green message bubbles in the chat flow (not in a separate box). They load on demand when the user message or AI process requires them.

---

### Model readiness banner (`.model-readiness-banner`)

Optional bar at the top of `.chat-shell`, shown when configured models are **not ready**. It blocks chat input until resolved.

| Control | Action |
|---------|--------|
| **Recheck** | Run readiness test again |
| **Settings** | Open Properties dialog |
| **Show / hide details** | Expand test result list |

---

### Dialogs and overlays

These appear on top of the main UI (`.modal-overlay` or dedicated overlay classes).

| Dialog | How to open | CSS / component |
|--------|-------------|-----------------|
| **Language picker** | First launch (no saved language) | `.language-picker-overlay` → `LanguagePicker` |
| **Setup wizard** | First run or *Resume setup* in sidebar | `SetupWizard` (multi-step modal) |
| **Properties / Settings** | Sidebar menu → Properties | `.modal.settings-modal` → `SettingsModal` |
| **Manage models & routing** | Sidebar menu or Settings → Models tab | `.modal.models-modal` → `ModelsManagerModal` |
| **About AgentForge** | Sidebar menu → About | `.modal.about-modal` → `AboutModal` |

#### Properties dialog — tabs (`.settings-tabs`)

The settings form uses **horizontal tabs** that wrap to the next line when space is tight. The dialog keeps a **fixed size**; tab content scrolls inside.

| Tab (EN) | Tab (DE) | Contents |
|----------|----------|----------|
| **General** | Allgemein | Language, appearance (dark/light), workspace directory |
| **Models** | Modelle | Ollama URL, default model, auto-routing, model access test, link to manage models |
| **Cloud** | Cloud | API keys (OpenAI, Anthropic, Gemini, Groq, Mistral) |
| **Memory** | Gedächtnis | Default memory token budget |
| **Context** | Context | Context plugins catalog (on-demand weather, date/time, etc.) |
| **Agents** | Agenten | List of available agent roles |

Footer on all modals: **Cancel** / **Save** (`.modal-actions`).

#### Manage models dialog (sections)

| Section | Purpose |
|---------|---------|
| **Add model** | Register Ollama tag or cloud model ID |
| **Model list** | Enable/disable, edit, delete, assign task types (`.model-card`) |
| **Routing table** | Default model per task type (coding, research, SQL, …) |

---

### Offline screen (`.offline-screen`)

If the backend is not reachable, the full window is replaced by a minimal screen: title, hint to run `python3 run.py`, and **Reconnect**.

---

### Frontend component map

For developers, UI areas map to React components under `frontend/src/components/`:

| UI area | Component file |
|---------|----------------|
| Root layout | `App.tsx` |
| Sidebar | `Sidebar.tsx` |
| Chat panel | `ChatPanel.tsx` |
| Messages / input | `ChatPanel.tsx` |
| Context plugin bubbles | `ContextPluginLog.tsx` |
| Agent history | `AgentHistory.tsx` |
| Approvals | `ApprovalPanel.tsx` |
| Model readiness | `ModelReadinessBanner.tsx` |
| Settings (Properties) | `SettingsModal.tsx` |
| Context plugins list (settings) | `ContextPluginsList.tsx` |
| Models manager | `ModelsManagerModal.tsx` |
| About | `AboutModal.tsx` |
| Setup wizard | `SetupWizard.tsx` |
| Language picker | `LanguagePicker.tsx` |

---

## Requirements

| Component | Minimum |
|-----------|---------|
| **OS** | Linux, Windows, or macOS (desktop) |
| **Python** | 3.12+ |
| **Node.js** | 20+ (with npm) |
| **Ollama** | HTTP-accessible instance (local or remote) |
| **RAM** | 8 GB+ (16 GB+ recommended; depends on models) |
| **Disk** | ~500 MB for app + dependencies (models stored separately in Ollama) |

**Optional:**

- Chromium, Firefox, Edge, or Safari (UI in browser or app window)
- Rust toolchain (native Tauri desktop build)
- Cloud API keys (OpenAI, Anthropic, Gemini, Groq, Mistral)

### Platform support

AgentForge is **not Linux-only**. The backend and frontend use standard Python and Node.js tooling and should run on all major desktop operating systems.

| OS | Status | Notes |
|----|--------|-------|
| **Linux** | Primary / best tested | Full installer (`install.py`), desktop shortcut, shell-tool defaults tuned for Unix |
| **Windows** | Supported | Use `py -3 install.py` after installing Git + Python + Node.js; browser mode is usually the simplest start |
| **macOS** | Supported | Use `python3 install.py` after installing Git + Python + Node.js; Ollama is typically installed locally |

What differs by platform is mostly **integration**, not the core product:

- **Linux** — one-command install, `.desktop` launcher, documented package list
- **Windows / macOS** — same runtime stack; you install Python/Node yourself; browser mode (`AGENTFORGE_MODE=browser`) is the simplest start
- **All platforms** — SQLite data under the user profile, FastAPI on port 8765, Vite dev UI on port 5173

If something fails on Windows or macOS, it is usually a small path, shell, or launcher detail — not a different architecture.

---

## Installation from GitHub

### 1. Clone the repository

```bash
git clone https://github.com/joruf/agent-forge.git
cd agent-forge
```

### 2. Install prerequisites

- **Linux (Debian/Ubuntu/Linux Mint):** prerequisites can be installed by the project installer (`python3 install.py --system` in step 3).
- **Windows/macOS:** install **Git**, **Python 3.12+**, and **Node.js 20+ (npm included)** first.

### 3. Run the built-in installer

Linux:

```bash
python3 install.py --system
python3 install.py
```

macOS:

```bash
python3 install.py
```

Windows (PowerShell):

```powershell
py -3 install.py
```

The installer will:

1. Create a Python virtual environment in `backend/.venv`
2. Install Python dependencies from `backend/requirements.txt`
3. Install frontend npm packages in `frontend/`
4. Create a desktop shortcut on Linux (application menu entry)

### 4. Configure environment

Copy the example configuration and edit it:

```bash
cp .env.example backend/.env
```

Edit `backend/.env`:

```env
AGENTFORGE_OLLAMA_BASE_URL=http://192.168.1.10:11434
AGENTFORGE_DEFAULT_MODEL=ollama/llama3.1:8b
AGENTFORGE_WORKSPACE_ROOT=/home/youruser/Documents
AGENTFORGE_LLM_AUTO_ROUTING=true
AGENTFORGE_LLM_REQUEST_TIMEOUT=300
AGENTFORGE_UI_LANGUAGE=en

# Optional: force one Ollama model for all tasks (Quick Chat, agents, titles)
# AGENTFORGE_OVERRIDE_MODEL=ollama/llama3.2:1b-instruct-q4_K_M
```

| Variable | Description |
|----------|-------------|
| `AGENTFORGE_HOST` | Backend bind address (default: `127.0.0.1`) |
| `AGENTFORGE_PORT` | Backend port (default: `8765`) |
| `AGENTFORGE_OLLAMA_BASE_URL` | URL of your Ollama server |
| `AGENTFORGE_DEFAULT_MODEL` | Fallback LiteLLM model string |
| `AGENTFORGE_OVERRIDE_MODEL` | Force one model for **all** tasks (Quick Chat, single/multi-agent, titles). Leave empty for normal task-based routing |
| `AGENTFORGE_WORKSPACE_ROOT` | Root directory for file/shell tools |
| `AGENTFORGE_LLM_AUTO_ROUTING` | Auto-select model by task type (`true`/`false`) |
| `AGENTFORGE_LLM_REQUEST_TIMEOUT` | LLM request timeout in seconds (default: `300`; increase for remote or CPU-only Ollama) |
| `AGENTFORGE_LLM_TITLE_TIMEOUT` | Timeout for AI chat title generation in seconds (default: `45`) |
| `AGENTFORGE_MULTI_AGENT_MAX_ROUNDS` | Discussion rounds in multi-agent mode with cloud models (default: `4`) |
| `AGENTFORGE_MULTI_AGENT_MAX_ROUNDS_OLLAMA` | Discussion rounds when using Ollama (default: `2`; reduces timeouts on slow/local models) |
| `AGENTFORGE_DEFAULT_MEMORY_TOKENS` | Default context budget (default: `32000`) |
| `AGENTFORGE_UI_LANGUAGE` | UI language: `en` or `de` |
| `AGENTFORGE_WEB_SEARCH_ENABLED` | Enable web search tool (`true`/`false`, default: `true`) |
| `AGENTFORGE_WEB_SEARCH_MAX_RESULTS` | Max web search hits per query (default: `5`) |
| `AGENTFORGE_OPENAI_API_KEY` | Optional OpenAI key |
| `AGENTFORGE_OPENAI_API_BASE` | Optional custom OpenAI-compatible API base URL |
| `AGENTFORGE_ANTHROPIC_API_KEY` | Optional Anthropic (Claude) key |
| `AGENTFORGE_GEMINI_API_KEY` | Optional Google Gemini key |
| `AGENTFORGE_GROQ_API_KEY` | Optional Groq key |
| `AGENTFORGE_MISTRAL_API_KEY` | Optional Mistral key |

Cloud keys can also be entered in the UI under **Settings → Cloud providers** (stored in the local database).

### 5. Start AgentForge

```bash
python3 run.py
```

On first start, the **setup wizard** guides you through Ollama connectivity, model import, and workspace checks.

### 6. Stop AgentForge

```bash
python3 stop.py
```

---

## Start modes

| Command | Behaviour |
|---------|-----------|
| `python3 run.py` | Default on Linux: Chromium/Firefox app window when available |
| `AGENTFORGE_MODE=browser python run.py` | Open in default browser tab (simplest on Windows/macOS) |
| `AGENTFORGE_MODE=window python run.py` | Force standalone browser window |
| `AGENTFORGE_MODE=tauri python run.py` | Native Tauri app (requires Rust + platform-specific deps) |

**Ports:**

| Service | URL |
|---------|-----|
| Backend API | `http://127.0.0.1:8765/api` |
| Frontend UI | `http://127.0.0.1:5173` |

**Logs:** `.run/logs/` (launcher, backend, frontend)

---

## LLM providers and models

### Ollama (primary)

Point `AGENTFORGE_OLLAMA_BASE_URL` to any reachable Ollama instance — local PC, Docker container, or NAS.

Recommended fast models for CPU-only setups:

- `qwen2.5:0.5b-instruct` — very fast responses
- `llama3.2:1b-instruct-q4_K_M` — good balance of speed and quality

For local CPU testing, you can pin a single fast model for every task:

```env
AGENTFORGE_OVERRIDE_MODEL=ollama/llama3.2:1b-instruct-q4_K_M
AGENTFORGE_LLM_REQUEST_TIMEOUT=300
AGENTFORGE_MULTI_AGENT_MAX_ROUNDS_OLLAMA=2
```

When `AGENTFORGE_OVERRIDE_MODEL` is set, auto-routing is bypassed and that model is used for Quick Chat, single-agent, multi-agent, and chat titles. On Ollama, multi-agent mode automatically uses fewer discussion rounds (`AGENTFORGE_MULTI_AGENT_MAX_ROUNDS_OLLAMA`, default `2`) to reduce timeouts.

Use **Manage models** in the UI to import installed Ollama tags and assign them to task types.

Default routing preferences are defined in `assets/models.yaml`.

### Cloud providers (optional)

| Provider | Example model string | Env variable |
|----------|---------------------|--------------|
| OpenAI | `gpt-4o`, `gpt-4o-mini` | `AGENTFORGE_OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-3-5-haiku-20241022` | `AGENTFORGE_ANTHROPIC_API_KEY` |
| Google Gemini | `gemini/gemini-2.0-flash` | `AGENTFORGE_GEMINI_API_KEY` |
| Groq | `groq/llama-3.1-8b-instant` | `AGENTFORGE_GROQ_API_KEY` |
| Mistral | `mistral/mistral-small-latest` | `AGENTFORGE_MISTRAL_API_KEY` |

All providers are routed through [LiteLLM](https://github.com/BerriAI/litellm).

---

## Project structure

```
AgentForge/
├── backend/
│   ├── agentforge/          # Python application package
│   │   ├── agents/          # Orchestrator, roles, approvals
│   │   ├── api/             # FastAPI routes + WebSocket
│   │   ├── llm/             # LiteLLM provider, routing, catalog
│   │   ├── memory/          # Context memory store
│   │   ├── storage/         # SQLite conversations, models, setup
│   │   ├── tools/           # read_file, write_file, shell, …
│   │   └── services/        # Setup wizard tests
│   ├── tests/               # pytest unit tests
│   ├── requirements.txt
│   └── .env                 # Local config (not committed)
├── frontend/
│   └── src/                 # React + TypeScript UI
├── assets/
│   ├── roles/               # Custom agent role YAML files
│   ├── models.yaml          # Default LLM routing preferences
│   └── models_catalog.json  # Known model metadata
├── docs/
│   ├── USER_MANUAL.md
│   ├── USER_MANUAL.html       # End-user guide
│   ├── TECHNICAL_DOCUMENTATION.md
│   └── TECHNICAL_DOCUMENTATION.html
├── install.py               # Dependency installer
├── run.py                   # Launcher (backend + frontend + window)
├── stop.py                  # Stop all processes
├── run_tests.py             # Run backend unit tests
└── .env.example             # Configuration template
```

---

## Custom roles

Add YAML files to `assets/roles/`:

```yaml
id: security_expert
name: Security Expert
description: Audits code for security vulnerabilities
system_prompt: |
  You are a security specialist. Review code for OWASP risks,
  injection flaws, and insecure defaults. Provide concrete fixes.
is_builtin: false
```

Restart the backend or reload roles via the API after adding files.

---

## Command security

| Category | Examples | Behaviour |
|----------|----------|-----------|
| **Whitelist** | `git`, `npm`, `python`, `php`, `ls`, `grep`, `find` | Executed automatically |
| **Blacklist** | `rm`, `sudo`, `shutdown`, `curl`, `wget`, `ssh` | Blocked entirely |
| **Other** | Any command not in either list | Requires user approval in UI |

Lists are configurable in **Settings → Security**.

All file operations are restricted to `AGENTFORGE_WORKSPACE_ROOT`.

---

## Testing

```bash
# Unit tests (no live Ollama required)
python3 run_tests.py

# Include live Ollama integration tests
python3 run_tests.py --live
```

---

## Documentation

| Document | Audience | Format |
|----------|----------|--------|
| [README.md](README.md) | Everyone | Overview + install |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | End users | Full usage guide (Markdown) |
| [docs/USER_MANUAL.html](docs/USER_MANUAL.html) | End users | Full usage guide (HTML) |
| [docs/TECHNICAL_DOCUMENTATION.md](docs/TECHNICAL_DOCUMENTATION.md) | Developers | Architecture, internals (Markdown, DE) |
| [docs/TECHNICAL_DOCUMENTATION.html](docs/TECHNICAL_DOCUMENTATION.html) | Developers | Architecture, API, internals (HTML) |

Open the HTML documentation in any browser:

```bash
xdg-open docs/USER_MANUAL.html
xdg-open docs/TECHNICAL_DOCUMENTATION.html
```

---

## Data and privacy

| Path | Contents |
|------|----------|
| `~/.local/share/agentforge/agentforge.db` | Chats, messages, memory |
| `~/.local/share/agentforge/model_config.json` | Model registry and routing |
| `~/.local/share/agentforge/setup_state.json` | Setup wizard progress |
| `backend/.env` | Local secrets (never commit) |
| `.run/logs/` | Runtime logs |

---

## Development

```bash
# Backend only
cd backend && source .venv/bin/activate && python -m agentforge

# Frontend only
cd frontend && npm run dev

# Production frontend build
cd frontend && npm run build

# Native Tauri build (optional)
cd frontend && npm run tauri:build
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| *Backend is not reachable* | Run `python3 run.py`; check port 8765 |
| *Ollama connection failed* | Verify `AGENTFORGE_OLLAMA_BASE_URL`; test with `curl http://host:11434/api/tags` |
| *LLM timeout / Connection timed out* | Increase `AGENTFORGE_LLM_REQUEST_TIMEOUT` (e.g. `600`); use Single Agent instead of Multi-Agent; set `AGENTFORGE_MULTI_AGENT_MAX_ROUNDS_OLLAMA=2` |
| *No models available* | Pull models in Ollama; run setup wizard or **Manage models → Sync** |
| *Desktop window not opening* | Linux: install Chromium (`sudo apt install chromium-browser`). Windows/macOS: use `AGENTFORGE_MODE=browser python run.py` |
| *Frontend not installed* | Run `python3 install.py` |

---

## Roadmap (Phase 2+)

- PDF/Word document read/write with auto-install of dependencies
- Web search and REST API integrations
- Custom role editor in the UI
- Plugin system for external tools

---

## License

MIT — see [LICENSE](LICENSE).
