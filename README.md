<div align="center">

```
 ██╗    ██╗ █████╗ ██╗     ██╗██████╗  ██████╗ ██████╗ ██████╗ ███████╗
 ██║    ██║██╔══██╗██║     ██║██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ██║ █╗ ██║███████║██║     ██║██║  ██║██║     ██║   ██║██║  ██║█████╗  
 ██║███╗██║██╔══██║██║     ██║██║  ██║██║     ██║   ██║██║  ██║██╔══╝  
 ╚███╔███╔╝██║  ██║███████╗██║██████╔╝╚██████╗╚██████╔╝██████╔╝███████╗
  ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚═╝╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
```

**Local Multi-Agent Swarm Framework — Termux Edition**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Termux%20%7C%20Linux-green?style=flat-square&logo=android)](https://termux.dev)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-35%20passing-brightgreen?style=flat-square)](#running-tests)

*Run multiple AI agents (DeepSeek, Claude, ChatGPT, Gemini) simultaneously on your Android device — collaborating, debating, and executing tasks locally.*

</div>

---

## ⚡ One-Line Install (Termux / Linux)

```bash
bash <(curl -sL https://raw.githubusercontent.com/walidrbh2000-maker/walidcode/main/setup.sh)
```

> **Requirements:** [Termux](https://termux.dev) on Android, or any Linux machine with Python 3.10+.  
> The installer handles everything: system packages, Chromium, Playwright, and the `walidcode` command.

---

## What Is This?

**walidcode** is a bridge that connects any web-based AI chat UI (DeepSeek, Claude, ChatGPT, Gemini…) to your local Android/Linux machine via Playwright. In v2, it evolves into a full **multi-agent swarm** — you spawn multiple AI agents with different roles, they talk to each other through an orchestrator, execute real shell commands and file operations on your device, and you control everything from a single TUI or web dashboard.

```
  ┌─────────────┐     ┌─────────────────────┐     ┌──────────────────┐
  │  DeepSeek   │◄───►│                     │◄───►│  LocalExecutor   │
  │  (Coder)    │ CDP │  SwarmOrchestrator  │     │  shell / files   │
  ├─────────────┤     │   + MessageBus      │     │  git / http      │
  │  Claude.ai  │◄───►│                     │◄───►│  Skills/Plugins  │
  │  (Reviewer) │     └──────────┬──────────┘     └──────────────────┘
  ├─────────────┤                │
  │  Gemini     │◄───►    FastAPI Daemon
  │  (Tester)   │         WebSocket API
  └─────────────┘              │
                         ┌─────┴──────┐
                         │  TUI / Web │  ← you type here
                         │  Dashboard │
                         └────────────┘
```

---

## Features

| Feature | Detail |
|---|---|
| 🤖 **Multi-Agent Swarm** | Spawn N agents on different AI platforms simultaneously |
| 🎭 **Roles** | Coder · Reviewer · Tester · Architect · Researcher · General |
| 💬 **Inter-Agent Messaging** | Agents route tasks to each other via `<send_to agent="id">` |
| 🔄 **Auto-Reconnect** | Exponential back-off reconnection — swarm survives crashes |
| 🖥️ **TUI Dashboard** | Rich Textual interface with live agent status panel |
| 🌐 **Web Dashboard** | Zero-dependency HTML dashboard served by the daemon |
| 📦 **Mass Ingestion** | Read entire projects (500+ files), respects `.gitignore` |
| ⚡ **Parallel Skills** | Up to 5 tool calls executed concurrently per agent |
| 📝 **MD Skill Prompts** | Drop `.md` files into `skills/prompts/` as system-prompt injections |
| 🔒 **Sandbox** | Path allowlist · shell blocklist · file size cap · dry-run mode |
| 🤖 **Termux-Native** | Designed for Android Termux; works on desktop Linux too |

---

## Quick Start

### 1. Install

```bash
bash <(curl -sL https://raw.githubusercontent.com/walidrbh2000-maker/walidcode/main/setup.sh)
```

### 2. Launch the Swarm

```bash
# Interactive setup (prompts for agent URLs)
walidcode start

# Or specify agents directly
walidcode start \
  -a "coder|https://chat.deepseek.com|coder" \
  -a "reviewer|https://claude.ai|reviewer" \
  -a "tester|https://gemini.google.com|tester"
```

### 3. Connect

```bash
# Open TUI client (in a second terminal / tmux pane)
walidcode chat

# Or open the web dashboard in a browser
walidcode web
```

> **Termux tip:** Use tmux to run the daemon and TUI side-by-side:
> ```bash
> pkg install tmux
> tmux new -s walidcode
> # Ctrl+B then % to split panes
> ```

---

## CLI Reference

```
walidcode start     Launch the daemon (swarm + API server)
walidcode chat      Open the TUI chat client
walidcode web       Print/open the Web Dashboard URL
walidcode status    Show daemon status and agent list
walidcode stop      Gracefully stop the daemon
walidcode agents    List all agents and their current status
walidcode ingest    Read a project directory into LLM context
walidcode send      Send a one-shot message (useful in scripts)
walidcode add-agent Hot-add an agent to a running swarm
```

### Start options

```bash
walidcode start \
  -a "id|url|role"          # Agent spec (repeat for each agent)
  --port 7771               # Daemon API port (default: 7771)
  --skills ./skills         # Skills/plugins directory
  --dry-run                 # Parse tool calls without executing
  --foreground              # Don't daemonise (useful for Termux)
  --base-port 9222          # First Chromium CDP port
```

---

## Agent Roles

| Role | Emoji | Behaviour |
|---|---|---|
| `coder` | 🧑‍💻 | Implements code, saves files, runs build commands |
| `reviewer` | 🔍 | Reviews code quality, security, and logic |
| `tester` | 🧪 | Writes and runs test suites via shell |
| `architect` | 🏛️ | Produces design docs, API contracts, directory structures |
| `researcher` | 📚 | Web searches and synthesises information |
| `general` | 🤖 | All-purpose — handles any task |

Each role receives a tailored system-prompt suffix that shapes its behaviour within the swarm.

---

## Tool Reference

| Tool | Parameters | Description |
|---|---|---|
| `read_file` | `path` | Read a local file |
| `write_file` | `path`, `content`, `mode` | Write / append / patch a file |
| `list_dir` | `path`, `depth` | List directory tree |
| `delete_file` | `path` | Delete a file |
| `shell` | `command`, `cwd`, `timeout` | Run a shell command |
| `http_get` | `url`, `headers` | HTTP GET request |
| `mcp_call` | `endpoint`, `method`, `arguments` | Call an MCP server |
| `mcp_github` | `action`, `owner`, `repo`, … | GitHub API actions |
| `search_web` | `query` | DuckDuckGo instant answers |
| `git_status` | `path` | Git status (built-in skill) |
| `summarize_file` | `path` | First 40 lines of a file (built-in skill) |

### Tool call syntax (any of these work)

```xml
<!-- Full XML -->
<tool_call>
  <name>write_file</name>
  <path>~/app.py</path>
  <content>print("hello")</content>
</tool_call>

<!-- Compact JSON -->
<tool_call>{"tool": "shell", "command": "pytest tests/ -v"}</tool_call>

<!-- Shorthand tags -->
<shell>git log --oneline -10</shell>
<read_file>~/project/main.py</read_file>
<search_web>Python asyncio best practices</search_web>
```

---

## Slash Commands (TUI & Web UI)

```
/assign <agent_id> <message>   Send task to a specific agent
/broadcast <message>           Send to all agents simultaneously
/agents                        Refresh agent status panel
/status                        Show daemon info
/attach <path>                 Attach a file's content to the next message
/ingest <path>                 Ingest entire project directory
/add_agent <id|url|role>       Hot-add a new agent
/clear                         Clear chat history
/help                          Show all commands
```

---

## Mass Project Ingestion

Feed an entire codebase into the AI context window with one command:

```bash
# Print context to stdout (pipe to walidcode send)
walidcode ingest ~/my_project

# Save to file
walidcode ingest ~/my_project -o /tmp/context.txt

# Then send to a specific agent
walidcode send "$(cat /tmp/context.txt)" --target architect
```

The ingester:
- Respects `.gitignore` (requires `pip install pathspec`)
- Skips binary files, `node_modules`, `__pycache__`, `.git`, build artifacts
- Caps at 3M characters by default (~750k tokens)
- Produces a structured `<project_context>` block with a directory tree

---

## Adding Skills (Plugins)

Drop a `.py` file in `skills/`:

```python
SKILL_NAME        = "run_tests"
SKILL_DESCRIPTION = "Runs pytest and returns the output."

def run(parameters: dict) -> str:
    import subprocess
    path = parameters.get("path", ".")
    result = subprocess.run(
        ["python", "-m", "pytest", path, "-v", "--tb=short"],
        capture_output=True, text=True, timeout=60,
    )
    return (result.stdout + result.stderr).strip()
```

Skills are **hot-loaded** — no restart needed after adding a file.

### Markdown Skill Prompts

Drop `.md` files in `skills/prompts/` to inject them as additional system context for all agents:

```markdown
<!-- skills/prompts/coding_style.md -->
## Coding Standards
- Python 3.11+ type hints on all functions
- Maximum line length: 100 characters
- Use dataclasses for structured data
```

---

## Project Structure

```
walidcode/
├── main.py                  ← Unified CLI entry point
├── config.py                ← AgentConfig + SwarmConfig
├── setup.sh                 ← One-click Termux installer
├── requirements.txt
├── tests.py                 ← 35 unit tests (no browser needed)
│
├── orchestrator/
│   ├── message_bus.py       ← Async pub/sub bus
│   ├── roles.py             ← Role definitions & system prompts
│   ├── agent.py             ← AsyncAgentNode (Playwright + executor)
│   └── swarm.py             ← SwarmOrchestrator
│
├── daemon/
│   └── server.py            ← FastAPI daemon + WebSocket API
│
├── ui/
│   ├── tui.py               ← Textual TUI client
│   └── static/index.html    ← Web Dashboard (single-file, no framework)
│
├── ingestion/
│   └── project_reader.py    ← Gitignore-aware project ingestion
│
├── executor/
│   ├── local_executor.py    ← Built-in tools + parallel execution
│   ├── tool_parser.py       ← XML / JSON / shorthand parser
│   └── skill_registry.py    ← Hot-loading plugin system
│
└── skills/
    ├── git_status.py
    ├── summarize_file.py
    └── prompts/             ← Drop .md files here
```

---

## Supported Platforms

| AI Platform | URL |
|---|---|
| DeepSeek | `chat.deepseek.com` |
| Claude.ai | `claude.ai` |
| ChatGPT | `chat.openai.com` |
| Gemini | `gemini.google.com` |
| Any other | generic fallback |

---

## Sandbox & Safety

| Guard | Detail |
|---|---|
| `allowed_root_dirs` | Confine file ops to specific paths |
| Shell blocklist | `rm -rf /`, fork-bombs, `mkfs`, `shutdown`, etc. — always blocked |
| `shell_timeout` | Commands killed after 30 s by default |
| `max_file_read_bytes` | Files capped at 1 MB per read |
| `--dry-run` | Parse and log tool calls without executing |

---

## Running Tests

```bash
cd ~/walidcode
python -m pytest tests.py -v
```

No browser or running daemon required — 35 tests covering the parser, executor, message bus, roles, config, ingestion, and skill registry.

---

## API Reference

The daemon exposes a REST + WebSocket API at `http://localhost:7771`:

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web Dashboard |
| `/api/status` | GET | Daemon status |
| `/api/agents` | GET | List all agents |
| `/api/history` | GET | Message history |
| `/api/message` | POST | Send to one agent |
| `/api/broadcast` | POST | Send to all agents |
| `/api/agents/add` | POST | Spawn a new agent |
| `/api/agents/{id}` | DELETE | Remove an agent |
| `/ws` | WS | Real-time message stream |

---

## License

MIT — do whatever you want with it.

---

<div align="center">
Made for Termux · Built with Playwright + FastAPI + Textual
</div>
