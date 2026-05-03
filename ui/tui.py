"""
ui/tui.py — Textual TUI client for the walidcode daemon.

Layout
──────
┌─ walidcode swarm ──────────────────────────────────────┐
│ ┌── Chat ───────────────────────────────┐ ┌─ Agents ─┐ │
│ │                                       │ │ 🧑💻 cod  │ │
│ │  [user] Hello, write me a parser     │ │  READY   │ │
│ │  [coder] Sure! <tool_call>…          │ │          │ │
│ │  [system] Tool result injected       │ │ 🔍 rev   │ │
│ │                                       │ │  BUSY    │ │
│ └───────────────────────────────────────┘ └──────────┘ │
│ > _                                                      │
└──────────────────────────────────────────────────────────┘
Slash commands: /assign <id> <msg>  /broadcast <msg>
                /agents  /status  /attach <path>  /ingest <path>
                /add_agent <id|url|role>  /quit
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# ── Optional Textual import (graceful fallback to plain readline client) ───────
try:
    from textual              import on
    from textual.app          import App, ComposeResult
    from textual.binding      import Binding
    from textual.containers   import Horizontal, Vertical, ScrollableContainer
    from textual.widgets      import Header, Footer, Input, RichLog, Static, Label
    from textual.reactive     import reactive
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


DAEMON_URL = "http://127.0.0.1:7771"


# ── Colour / role helpers ──────────────────────────────────────────────────────

ROLE_COLOURS = {
    "coder":      "cyan",
    "reviewer":   "green",
    "tester":     "yellow",
    "architect":  "blue",
    "researcher": "magenta",
    "general":    "white",
    "user":       "bright_white",
    "system":     "dim",
    "orchestrator": "bright_cyan",
}

def _colour(source: str) -> str:
    return ROLE_COLOURS.get(source.lower(), "white")

def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ══════════════════════════════════════════════════════════════════════════════
# Textual TUI (full featured)
# ══════════════════════════════════════════════════════════════════════════════

if HAS_TEXTUAL:
    import websockets as _ws_lib

    class AgentPanel(Static):
        agents: reactive[list] = reactive([], layout=True)

        def render(self) -> str:
            if not self.agents:
                return "[dim]No agents[/dim]"
            lines = []
            for a in self.agents:
                emoji  = a.get("emoji", "🤖")
                aid    = a["id"]
                status = a["status"].upper()
                role   = a["role"]
                colour = {"ready": "green", "busy": "yellow",
                          "reconnecting": "orange3", "failed": "red",
                          "stopped": "dim", "starting": "blue"}.get(a["status"], "white")
                lines.append(f"{emoji} [{colour}]{aid}[/{colour}]")
                lines.append(f"  [dim]{role}[/dim]")
                lines.append(f"  [{colour}]{status}[/{colour}]\n")
            return "\n".join(lines)

    class WalidcodeTUI(App):
        CSS = """
        Screen { layout: horizontal; }
        #chat-panel { width: 3fr; height: 100%; }
        #right-panel { width: 1fr; height: 100%; border-left: solid $primary; }
        #chat-log { height: 1fr; overflow-y: scroll; padding: 0 1; }
        #agent-panel { height: 1fr; padding: 1; overflow-y: scroll; }
        #input-bar { height: 3; dock: bottom; }
        Input { width: 100%; }
        #status-bar { height: 1; dock: bottom; background: $boost; }
        Label { padding: 0 1; }
        """

        BINDINGS = [
            Binding("ctrl+c", "quit",       "Quit"),
            Binding("ctrl+l", "clear_chat", "Clear"),
            Binding("f1",     "show_help",  "Help"),
        ]

        def __init__(self, daemon_url: str = DAEMON_URL):
            super().__init__()
            self.daemon_url = daemon_url
            self.ws_url     = daemon_url.replace("http://", "ws://") + "/ws"
            self._ws_conn   = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal():
                with Vertical(id="chat-panel"):
                    yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
                    yield Input(placeholder="  Message… (/help for commands)", id="input-bar")
                with Vertical(id="right-panel"):
                    yield Label("⚡ Agents", id="agent-label")
                    yield AgentPanel(id="agent-panel")
            yield Footer()

        async def on_mount(self):
            self.set_interval(5, self._refresh_agents)
            asyncio.create_task(self._ws_loop())
            self._log_system("walidcode TUI — type /help for commands")

        # ── WebSocket loop ─────────────────────────────────────────────────────

        async def _ws_loop(self):
            log = self.query_one("#chat-log", RichLog)
            while True:
                try:
                    async with _ws_lib.connect(self.ws_url) as ws:
                        self._ws_conn = ws
                        self._log_system(f"✅ Connected to daemon at {self.daemon_url}")
                        async for raw in ws:
                            if raw.startswith("{"):
                                try:
                                    msg = json.loads(raw)
                                    if msg.get("type") in ("ping", "pong"):
                                        continue
                                    self._render_message(msg)
                                except Exception:
                                    pass
                except Exception as e:
                    self._log_system(f"⚠️  WS disconnected: {e}  — retrying in 5 s …")
                    self._ws_conn = None
                    await asyncio.sleep(5)

        # ── Message rendering ──────────────────────────────────────────────────

        def _render_message(self, msg: dict):
            log    = self.query_one("#chat-log", RichLog)
            source = msg.get("source", "?")
            mtype  = msg.get("type", "")
            ts     = _fmt_time(msg.get("timestamp", 0))
            colour = _colour(source)
            text   = msg.get("content", "")

            if mtype == "system":
                log.write(f"[dim]{ts}  ℹ  {text}[/dim]")
            elif mtype == "user_input":
                log.write(f"[{ts}] [bright_white bold]YOU[/bright_white bold]: {text}")
            elif mtype == "agent_response":
                # Truncate very long tool call blobs
                display = text[:800] + (" …[truncated]" if len(text) > 800 else "")
                log.write(f"[{ts}] [{colour} bold]{source.upper()}[/{colour} bold]: {display}")
            elif mtype == "agent_task":
                log.write(f"[{ts}] [dim]→ [{source}] → [{msg.get('target','?')}]: {text[:120]}[/dim]")
            elif mtype == "inter_agent":
                log.write(f"[{ts}] [magenta]↔ [{source}]→[{msg.get('target','?')}]: {text[:120]}[/magenta]")

        def _log_system(self, text: str):
            log = self.query_one("#chat-log", RichLog)
            log.write(f"[dim]{text}[/dim]")

        # ── Agent refresh ──────────────────────────────────────────────────────

        async def _refresh_agents(self):
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(f"{self.daemon_url}/api/agents", timeout=3)
                    data = r.json()
                panel = self.query_one("#agent-panel", AgentPanel)
                panel.agents = data.get("agents", [])
            except Exception:
                pass

        # ── Input handling & slash commands ───────────────────────────────────

        @on(Input.Submitted, "#input-bar")
        async def handle_input(self, event: Input.Submitted):
            text = event.value.strip()
            event.input.clear()
            if not text:
                return
            await self._process_input(text)

        async def _process_input(self, text: str):
            if text.startswith("/"):
                await self._handle_slash(text)
            else:
                await self._send_user_message(text)

        async def _handle_slash(self, cmd: str):
            parts  = cmd[1:].split(None, 2)
            verb   = parts[0].lower() if parts else ""

            if verb == "help":
                self._log_system(
                    "Commands: /assign <agent_id> <msg>  /broadcast <msg>  "
                    "/agents  /status  /attach <path>  /ingest <path>  "
                    "/add_agent <id|url|role>  /clear  /quit"
                )
            elif verb == "quit":
                self.exit()
            elif verb == "clear":
                self.query_one("#chat-log", RichLog).clear()
            elif verb == "agents":
                await self._refresh_agents()
                self._log_system("Agent panel refreshed.")
            elif verb == "status":
                try:
                    async with httpx.AsyncClient() as c:
                        r = await c.get(f"{self.daemon_url}/api/status", timeout=3)
                        self._log_system(str(r.json()))
                except Exception as e:
                    self._log_system(f"Status error: {e}")
            elif verb == "assign" and len(parts) >= 3:
                agent_id, msg_text = parts[1], parts[2]
                await self._send_user_message(msg_text, target=agent_id)
            elif verb == "broadcast" and len(parts) >= 2:
                msg_text = " ".join(parts[1:])
                async with httpx.AsyncClient() as c:
                    await c.post(f"{self.daemon_url}/api/broadcast",
                                 json={"content": msg_text}, timeout=5)
            elif verb == "attach" and len(parts) >= 2:
                await self._attach_file(parts[1])
            elif verb == "ingest" and len(parts) >= 2:
                await self._ingest_project(parts[1])
            elif verb == "add_agent" and len(parts) >= 2:
                spec = " ".join(parts[1:])
                agent_parts = spec.split("|")
                if len(agent_parts) >= 2:
                    async with httpx.AsyncClient() as c:
                        await c.post(f"{self.daemon_url}/api/agents/add", json={
                            "agent_id": agent_parts[0].strip(),
                            "chat_url": agent_parts[1].strip(),
                            "role":     agent_parts[2].strip() if len(agent_parts) > 2 else "general",
                        }, timeout=5)
                    self._log_system(f"Agent '{agent_parts[0]}' spawn requested.")
                else:
                    self._log_system("Usage: /add_agent id|url|role")
            elif verb == "skills" and len(parts) >= 2:
                self._log_system(f"Skills context: {', '.join(parts[1:])} (send in next message manually for now)")
            else:
                self._log_system(f"Unknown command: /{verb}  — try /help")

        async def _send_user_message(self, text: str, target: Optional[str] = None):
            if self._ws_conn:
                try:
                    await self._ws_conn.send(json.dumps({
                        "type":    "user_input",
                        "content": text,
                        "target":  target,
                    }))
                except Exception as e:
                    self._log_system(f"Send error: {e}")
            else:
                try:
                    async with httpx.AsyncClient() as c:
                        await c.post(f"{self.daemon_url}/api/message",
                                     json={"content": text, "target": target}, timeout=5)
                except Exception as e:
                    self._log_system(f"HTTP send error: {e}")

        async def _attach_file(self, path_str: str):
            path = Path(path_str).expanduser()
            if not path.exists():
                self._log_system(f"Path not found: {path}")
                return
            if path.is_file():
                content = path.read_text(errors="replace")[:50_000]
                msg = f"[Attached file: {path.name}]\n```\n{content}\n```"
                await self._send_user_message(msg)
                self._log_system(f"Attached {path.name} ({len(content)} chars)")
            else:
                self._log_system(f"For directories use /ingest {path}")

        async def _ingest_project(self, path_str: str):
            from ingestion.project_reader import ProjectReader
            self._log_system(f"🔍 Ingesting {path_str} …")
            reader  = ProjectReader(path_str)
            context = reader.build_context()
            stats   = reader.stats()
            self._log_system(f"📦 Ingested {stats['files']} files / ~{stats['chars']:,} chars")
            await self._send_user_message(context)

        def action_clear_chat(self):
            self.query_one("#chat-log", RichLog).clear()

        def action_show_help(self):
            asyncio.create_task(self._handle_slash("/help"))


# ══════════════════════════════════════════════════════════════════════════════
# Fallback: plain readline client (no Textual)
# ══════════════════════════════════════════════════════════════════════════════

class PlainClient:
    """Minimal async readline client — works anywhere."""

    def __init__(self, daemon_url: str = DAEMON_URL):
        self.daemon_url = daemon_url

    async def run(self):
        print(f"\n🤖 walidcode plain client — connected to {self.daemon_url}")
        print("   Type messages and press Enter.  /quit to exit.\n")

        async with httpx.AsyncClient() as client:
            # Quick status check
            try:
                r = await client.get(f"{self.daemon_url}/api/status", timeout=3)
                print(f"   Daemon: {r.json()}\n")
            except Exception as e:
                print(f"   ⚠️  Cannot reach daemon: {e}")
                return

            loop = asyncio.get_event_loop()
            while True:
                try:
                    line = await loop.run_in_executor(None, input, "you> ")
                except (KeyboardInterrupt, EOFError):
                    break
                line = line.strip()
                if not line:
                    continue
                if line in ("/quit", "/exit"):
                    break
                if line.startswith("/agents"):
                    r = await client.get(f"{self.daemon_url}/api/agents", timeout=3)
                    print(json.dumps(r.json(), indent=2))
                elif line.startswith("/assign "):
                    parts = line.split(None, 2)
                    if len(parts) == 3:
                        await client.post(f"{self.daemon_url}/api/message",
                                          json={"content": parts[2], "target": parts[1]}, timeout=5)
                else:
                    await client.post(f"{self.daemon_url}/api/message",
                                      json={"content": line}, timeout=5)
                    print("   [sent]")

        print("Goodbye.")


# ── Public entry point ─────────────────────────────────────────────────────────

def launch_tui(daemon_url: str = DAEMON_URL):
    if HAS_TEXTUAL:
        WalidcodeTUI(daemon_url=daemon_url).run()
    else:
        print("Textual not installed — falling back to plain client.")
        print("Install with:  pip install textual\n")
        asyncio.run(PlainClient(daemon_url=daemon_url).run())
