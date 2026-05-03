#!/usr/bin/env python3
"""
walidcode v2 — Local Multi-Agent Swarm Framework
Main CLI entry point.

Commands
────────
  walidcode start     — launch the daemon (swarm + API server)
  walidcode chat      — open the TUI client (connects to running daemon)
  walidcode web       — print the Web UI URL and open it in a browser
  walidcode status    — quick status check of the daemon
  walidcode stop      — gracefully stop the daemon
  walidcode agents    — list all agents and their status
  walidcode ingest    — read a project directory and print context stats
  walidcode add-agent — hot-add a new agent to a running swarm
"""

import asyncio
import json
import os
import signal
import sys
import time
import webbrowser
from pathlib import Path

import click
import httpx

# ── Make project root importable regardless of CWD ────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config import AgentConfig, SwarmConfig, make_agent_configs_from_specs

BANNER = r"""
 ██╗    ██╗ █████╗ ██╗     ██╗██████╗  ██████╗ ██████╗ ██████╗ ███████╗
 ██║    ██║██╔══██╗██║     ██║██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ██║ █╗ ██║███████║██║     ██║██║  ██║██║     ██║   ██║██║  ██║█████╗
 ██║███╗██║██╔══██║██║     ██║██║  ██║██║     ██║   ██║██║  ██║██╔══╝
 ╚███╔███╔╝██║  ██║███████╗██║██████╔╝╚██████╗╚██████╔╝██████╔╝███████╗
  ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚═╝╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
         Local Multi-Agent Swarm Framework  •  Termux Edition  v2.0
"""

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7771


def _daemon_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _check_daemon(host: str, port: int) -> bool:
    try:
        r = httpx.get(f"{_daemon_url(host, port)}/api/status", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _read_pid(swarm_config: SwarmConfig) -> int | None:
    try:
        return int(Path(swarm_config.pid_file).read_text().strip())
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CLI group
# ══════════════════════════════════════════════════════════════════════════════

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """walidcode — Local Multi-Agent Swarm Framework"""
    if ctx.invoked_subcommand is None:
        print(BANNER)
        print(ctx.get_help())


# ══════════════════════════════════════════════════════════════════════════════
# walidcode start
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("start")
@click.option("--agent",    "-a", multiple=True,
              help='Agent spec: "id|url|role"  (repeat for each agent)\n'
                   'Example: -a "coder|https://chat.deepseek.com|coder" '
                   '-a "reviewer|https://claude.ai|reviewer"')
@click.option("--port",     "-p", default=DEFAULT_PORT, show_default=True,
              help="Daemon API port")
@click.option("--host",           default=DEFAULT_HOST, show_default=True)
@click.option("--skills",   "-s", default="skills",    show_default=True,
              help="Skills / plugins directory")
@click.option("--dry-run",        is_flag=True,
              help="Parse tool calls but do not execute them")
@click.option("--no-inject",      is_flag=True,
              help="Skip auto-injection of system prompts")
@click.option("--base-port",      default=9222, show_default=True,
              help="First Chromium CDP port (each agent gets base+N)")
@click.option("--foreground", "-f", is_flag=True,
              help="Run in foreground (do not daemonise)")
def start_cmd(agent, port, host, skills, dry_run, no_inject, base_port, foreground):
    """Launch the walidcode daemon (Swarm + API server)."""
    print(BANNER)

    if _check_daemon(host, port):
        click.echo(f"  ⚠️  Daemon already running on {host}:{port}")
        click.echo("  Use  walidcode status  or  walidcode chat  to connect.")
        sys.exit(0)

    # ── Interactive agent setup if none provided ───────────────────────────────
    if not agent:
        click.echo("  No agents specified. Let's set up at least one.\n")
        specs = []
        idx   = 0
        while True:
            click.echo(f"  Agent {idx + 1} setup  (leave URL blank to finish)")
            agent_id = click.prompt("    Agent ID", default=f"agent{idx}")
            url      = click.prompt("    Chat URL", default="")
            if not url.strip():
                if not specs:
                    click.echo("  At least one agent is required.")
                    continue
                break
            role = click.prompt(
                "    Role",
                type=click.Choice(["coder","reviewer","tester","architect","researcher","general"]),
                default="general",
            )
            specs.append(f"{agent_id}|{url.strip()}|{role}")
            idx += 1
        agent = tuple(specs)

    agent_configs = make_agent_configs_from_specs(list(agent), base_port=base_port)
    for cfg in agent_configs:
        cfg.dry_run = dry_run

    swarm_config = SwarmConfig(
        agents      = agent_configs,
        daemon_host = host,
        daemon_port = port,
        skills_dir  = skills,
    )
    swarm_config.ensure_home()

    # ── Print launch summary ───────────────────────────────────────────────────
    click.echo(f"\n  🚀 Launching walidcode swarm with {len(agent_configs)} agent(s):\n")
    for cfg in agent_configs:
        from orchestrator.roles import get_role
        role = get_role(cfg.role)
        click.echo(f"     {role.emoji}  {cfg.agent_id:15s} | {cfg.role:12s} | {cfg.chat_url}")
    click.echo(f"\n  Daemon API : http://{host}:{port}")
    click.echo(f"  Web UI     : http://{host}:{port}/")
    click.echo(f"  TUI client : walidcode chat")
    click.echo(f"  Logs       : {swarm_config.log_file}\n")

    # ── Daemonise (Termux-compatible, no os.fork() — use nohup subprocess) ────
    if not foreground and not _is_termux():
        _daemonise_unix(swarm_config)
    else:
        # Termux: just run in foreground (user handles background with &)
        if not foreground:
            click.echo("  Termux detected — running in foreground.")
            click.echo("  Tip: press Ctrl+Z then 'bg' to background, or run in a tmux/screen pane.\n")
        _run_server_inprocess(swarm_config, no_inject)


def _run_server_inprocess(swarm_config: SwarmConfig, no_inject: bool):
    """Import and start the FastAPI server in this process."""
    from daemon.server import run_daemon

    if not no_inject:
        # Patch lifespan to inject system prompts after agents connect
        import daemon.server as _ds
        _orig_lifespan = _ds.lifespan

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _patched_lifespan(app):
            async with _orig_lifespan(app):
                asyncio.create_task(
                    _ds._orchestrator.inject_system_prompts()
                    if _ds._orchestrator else asyncio.sleep(0),
                    name="sys-prompt-injector",
                )
                yield

        _ds.app.router.lifespan_context = _patched_lifespan

    run_daemon(swarm_config)


def _daemonise_unix(swarm_config: SwarmConfig):
    """Fork a background process (POSIX only, not Termux)."""
    import subprocess
    cmd = [
        sys.executable, "-m", "daemon.server",
        "--host", swarm_config.daemon_host,
        "--port", str(swarm_config.daemon_port),
        "--skills", swarm_config.skills_dir,
    ]
    for cfg in swarm_config.agents:
        cmd += ["--agent", f"{cfg.agent_id}|{cfg.chat_url}|{cfg.role}"]

    log_file = open(swarm_config.log_file, "a")
    proc = subprocess.Popen(
        cmd,
        stdout    = log_file,
        stderr    = log_file,
        start_new_session=True,
        cwd       = str(Path(__file__).parent),
    )
    swarm_config.ensure_home()
    Path(swarm_config.pid_file).write_text(str(proc.pid))
    click.echo(f"  ✅ Daemon started (PID {proc.pid}).")
    click.echo(f"  Connect with:  walidcode chat")


def _is_termux() -> bool:
    return os.path.isdir("/data/data/com.termux")


# ══════════════════════════════════════════════════════════════════════════════
# walidcode chat  (TUI)
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("chat")
@click.option("--host", default=DEFAULT_HOST)
@click.option("--port", default=DEFAULT_PORT)
@click.option("--plain", is_flag=True, help="Force plain readline client (no Textual)")
def chat_cmd(host, port, plain):
    """Open the interactive TUI chat client."""
    url = _daemon_url(host, port)
    if not _check_daemon(host, port):
        click.echo(f"  ❌ Daemon not reachable at {url}")
        click.echo("  Start it first with:  walidcode start ...")
        sys.exit(1)

    click.echo(f"  Connecting to daemon at {url} …\n")
    from ui.tui import launch_tui, PlainClient
    if plain:
        asyncio.run(PlainClient(daemon_url=url).run())
    else:
        launch_tui(daemon_url=url)


# ══════════════════════════════════════════════════════════════════════════════
# walidcode web
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("web")
@click.option("--host", default=DEFAULT_HOST)
@click.option("--port", default=DEFAULT_PORT)
@click.option("--no-open", is_flag=True, help="Print URL but do not open browser")
def web_cmd(host, port, no_open):
    """Print the Web Dashboard URL and open it in a browser."""
    url = f"http://{host}:{port}/"
    click.echo(f"\n  🌐 Web Dashboard: {url}\n")
    if not no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# walidcode status
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("status")
@click.option("--host", default=DEFAULT_HOST)
@click.option("--port", default=DEFAULT_PORT)
def status_cmd(host, port):
    """Show daemon status and agent list."""
    url = _daemon_url(host, port)
    try:
        r = httpx.get(f"{url}/api/status", timeout=3)
        info = r.json()
        click.echo(f"\n  ✅ Daemon RUNNING")
        click.echo(f"     PID:       {info.get('pid')}")
        click.echo(f"     Uptime:    {info.get('uptime_s')}s")
        click.echo(f"     Agents:    {info.get('agents')}")
        click.echo(f"     WS clients:{info.get('ws_clients')}")

        agents_r = httpx.get(f"{url}/api/agents", timeout=3)
        agents   = agents_r.json().get("agents", [])
        if agents:
            click.echo(f"\n  Agents:")
            for a in agents:
                status_sym = {"ready":"🟢","busy":"🟡","reconnecting":"🟠",
                              "failed":"🔴","stopped":"⚫","starting":"🔵"}.get(a["status"],"⚪")
                click.echo(f"     {status_sym} {a['emoji']} {a['id']:15s} | {a['role']:12s} | {a['status']:12s} | {a['url']}")
        click.echo()
    except Exception as e:
        click.echo(f"\n  ❌ Daemon not reachable at {url}: {e}\n")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# walidcode stop
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("stop")
@click.option("--host", default=DEFAULT_HOST)
@click.option("--port", default=DEFAULT_PORT)
def stop_cmd(host, port):
    """Gracefully stop the running daemon."""
    from config import SwarmConfig, AgentConfig
    swarm_config = SwarmConfig(agents=[], daemon_host=host, daemon_port=port)

    pid = _read_pid(swarm_config)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            click.echo(f"  ✅ Sent SIGTERM to PID {pid}")
        except ProcessLookupError:
            click.echo(f"  ⚠️  PID {pid} not found (already stopped?).")
        try:
            Path(swarm_config.pid_file).unlink(missing_ok=True)
        except Exception:
            pass
    else:
        click.echo("  No PID file found. Daemon may not be running or was started in foreground.")


# ══════════════════════════════════════════════════════════════════════════════
# walidcode agents
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("agents")
@click.option("--host", default=DEFAULT_HOST)
@click.option("--port", default=DEFAULT_PORT)
def agents_cmd(host, port):
    """List all agents in the running swarm."""
    url = _daemon_url(host, port)
    try:
        r = httpx.get(f"{url}/api/agents", timeout=3)
        agents = r.json().get("agents", [])
        if not agents:
            click.echo("  No agents registered.")
            return
        click.echo(f"\n  {'ID':15s} {'ROLE':12s} {'STATUS':12s} {'PLATFORM':10s} URL")
        click.echo("  " + "─" * 80)
        for a in agents:
            click.echo(f"  {a['emoji']} {a['id']:13s} {a['role']:12s} {a['status']:12s} {a['platform']:10s} {a['url']}")
        click.echo()
    except Exception as e:
        click.echo(f"  ❌ {e}")


# ══════════════════════════════════════════════════════════════════════════════
# walidcode add-agent
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("add-agent")
@click.argument("spec", required=False)
@click.option("--host", default=DEFAULT_HOST)
@click.option("--port", default=DEFAULT_PORT)
def add_agent_cmd(spec, host, port):
    """Hot-add an agent to the running swarm.

    SPEC format: "id|url|role"   e.g.  "tester|https://gemini.google.com|tester"
    """
    url = _daemon_url(host, port)
    if not spec:
        agent_id = click.prompt("  Agent ID")
        chat_url = click.prompt("  Chat URL")
        role     = click.prompt("  Role", default="general")
        spec = f"{agent_id}|{chat_url}|{role}"

    parts = spec.split("|")
    if len(parts) < 2:
        click.echo("  ❌ Spec must be 'id|url' or 'id|url|role'")
        sys.exit(1)

    payload = {
        "agent_id": parts[0].strip(),
        "chat_url": parts[1].strip(),
        "role":     parts[2].strip() if len(parts) > 2 else "general",
        "port":     9300,
    }
    try:
        r = httpx.post(f"{url}/api/agents/add", json=payload, timeout=5)
        if r.status_code == 200:
            click.echo(f"  ✅ Agent '{payload['agent_id']}' spawn requested.")
        else:
            click.echo(f"  ❌ Error: {r.text}")
    except Exception as e:
        click.echo(f"  ❌ {e}")


# ══════════════════════════════════════════════════════════════════════════════
# walidcode ingest
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("ingest")
@click.argument("path", default=".")
@click.option("--max-chars", default=3_000_000, show_default=True,
              help="Maximum characters to ingest")
@click.option("--output",  "-o", default=None,
              help="Write context to file instead of stdout")
@click.option("--stats-only", is_flag=True,
              help="Print statistics only, do not output context")
def ingest_cmd(path, max_chars, output, stats_only):
    """Read an entire project directory into LLM context.

    Respects .gitignore. Outputs a structured XML-ish context block.
    """
    from ingestion.project_reader import ProjectReader

    click.echo(f"  🔍 Ingesting {path} …", err=True)
    reader  = ProjectReader(path, max_chars=max_chars)

    if stats_only:
        # Quick walk without building full string
        for _ in reader._walk():
            pass
        click.echo(f"  Files found: (run without --stats-only to ingest)", err=True)
        return

    context = reader.build_context()
    stats   = reader.stats()

    click.echo(
        f"  ✅ Ingested {stats['files']} files | "
        f"~{stats['chars']:,} chars | "
        f"{stats['skipped']} skipped",
        err=True,
    )

    if output:
        Path(output).write_text(context)
        click.echo(f"  📄 Written to {output}", err=True)
    else:
        click.echo(context)


# ══════════════════════════════════════════════════════════════════════════════
# walidcode send  (one-shot message, useful in scripts)
# ══════════════════════════════════════════════════════════════════════════════

@cli.command("send")
@click.argument("message")
@click.option("--target", "-t", default=None, help="Agent ID (default: auto-route)")
@click.option("--host",         default=DEFAULT_HOST)
@click.option("--port",         default=DEFAULT_PORT)
def send_cmd(message, target, host, port):
    """Send a one-shot message to the swarm (from scripts or shell)."""
    url = _daemon_url(host, port)
    try:
        r = httpx.post(
            f"{url}/api/message",
            json={"content": message, "target": target},
            timeout=5,
        )
        data = r.json()
        click.echo(f"  ✅ Sent → {data.get('routed_to', 'auto')}")
    except Exception as e:
        click.echo(f"  ❌ {e}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cli()
