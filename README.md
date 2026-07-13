# AgentForge

**Multi-agent AI desktop platform for Linux** — coding assistance, collaborative task processing, and human-in-the-loop workflows with local Ollama and optional cloud LLMs.

| | |
|---|---|
| **Version** | 0.1.0 |
| **License** | MIT |
| **Platform** | Linux (desktop) |
| **Stack** | Python 3.12 · FastAPI · React · TypeScript · LiteLLM · SQLite |

---

## What is AgentForge?

AgentForge is a local-first AI assistant that can read and write files in your workspace, run shell commands (with approval), and coordinate multiple specialized agent roles on complex tasks. It connects to **Ollama** on your machine or network (e.g. Synology NAS) and optionally to **OpenAI, Anthropic, Google Gemini, Groq, and Mistral** cloud APIs.

### Key features

- **Single-agent coding mode** — one developer agent with file and shell tools
- **Multi-agent mode** — six built-in roles collaborate; live agent history panel
- **Human-in-the-loop** — shell command whitelist + approval dialog for everything else
- **Expandable agent history** — truncated messages expand on click
- **Configurable memory** — per-chat token budget (100 – 128 000 tokens), chat or global scope
- **LLM auto-routing** — task-type based model selection (coding, SQL, research, …)
- **Multi-provider support** — Ollama (local/remote) + OpenAI, Claude, Gemini, Groq, Mistral via LiteLLM
- **Setup wizard** — guided first-run checks for Ollama, models, workspace, API keys
- **Persistent chats** — SQLite storage with auto-generated titles
- **Bilingual UI** — English and German
- **Desktop integration** — Chromium app window, optional native Tauri build, Linux desktop shortcut

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

## Requirements

| Component | Minimum |
|-----------|---------|
| **OS** | Linux desktop (Ubuntu, Mint, Debian, …) |
| **Python** | 3.12+ |
| **Node.js** | 20+ (with npm) |
| **Ollama** | HTTP-accessible instance (local or remote) |
| **RAM** | 8 GB+ (16 GB+ recommended; depends on models) |
| **Disk** | ~500 MB for app + dependencies (models stored separately in Ollama) |

**Optional:**

- Chromium or Firefox (desktop app window)
- Rust toolchain (native Tauri desktop build)
- Cloud API keys (OpenAI, Anthropic, Gemini, Groq, Mistral)

---

## Installation from GitHub

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/AgentForge.git
cd AgentForge
```

> Replace `YOUR_USERNAME` with your GitHub account or organization name once the repository is published.

### 2. Install system packages (recommended)

On Debian/Ubuntu/Linux Mint:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm chromium-browser curl
```

For the native Tauri desktop build, also install Tauri Linux dependencies:

```bash
python3 install.py --system
```

### 3. Run the installer

```bash
python3 install.py
```

This will:

1. Create a Python virtual environment in `backend/.venv`
2. Install Python dependencies from `backend/requirements.txt`
3. Install frontend npm packages in `frontend/`
4. Create a Linux desktop shortcut (application menu entry)

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
| `python3 run.py` | Default: Chromium/Firefox app window |
| `AGENTFORGE_MODE=browser python3 run.py` | Open in default browser tab |
| `AGENTFORGE_MODE=window python3 run.py` | Force standalone browser window |
| `AGENTFORGE_MODE=tauri python3 run.py` | Native Tauri app (requires Rust + system deps) |

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
| [docs/TECHNICAL_DOCUMENTATION.html](docs/TECHNICAL_DOCUMENTATION.html) | Developers | Architecture, API, internals |

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
| *Desktop window not opening* | Install Chromium: `sudo apt install chromium-browser` |
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
