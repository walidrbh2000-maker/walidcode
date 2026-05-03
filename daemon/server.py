"""
daemon/server.py — walidcode Swarm Daemon

FastAPI server that:
  • Hosts the SwarmOrchestrator in the same event loop.
  • Exposes REST endpoints for status, agents, history.
  • Exposes a WebSocket /ws endpoint for real-time UI clients.
  • Serves the built-in Web UI from ui/static/.

Run directly:
  python -m daemon.server --config ... --agent "id|url|role" ...
Or via:
  walidcode start ...
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AgentConfig, SwarmConfig, make_agent_configs_from_specs
from orchestrator.swarm import SwarmOrchestrator
from orchestrator.message_bus import Message, MsgType

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers = [logging.StreamHandler()],
)
logger = logging.getLogger("daemon")

# ── Global state ───────────────────────────────────────────────────────────────

_orchestrator: Optional[SwarmOrchestrator] = None
_ws_clients:   Set[WebSocket]              = set()
_start_time    = time.time()


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    config: SwarmConfig = app.state.swarm_config

    config.ensure_home()

    # Write PID file
    with open(config.pid_file, "w") as f:
        f.write(str(os.getpid()))

    logger.info("walidcode daemon starting — PID %d", os.getpid())

    _orchestrator = SwarmOrchestrator(config)
    await _orchestrator.start()

    # Start the bus-→-websocket bridge
    asyncio.create_task(_broadcast_loop(), name="ws-broadcast")

    yield

    # Shutdown
    logger.info("walidcode daemon shutting down …")
    await _orchestrator.stop()
    try:
        os.unlink(config.pid_file)
    except OSError:
        pass


app = FastAPI(title="walidcode Swarm Daemon", version="2.0.0", lifespan=lifespan)


# ── WebSocket broadcast loop ───────────────────────────────────────────────────

async def _broadcast_loop():
    """Subscribe to the bus and forward every message to all WS clients."""
    if _orchestrator is None:
        return
    q = _orchestrator.bus.subscribe("_ws_broadcaster")
    while True:
        try:
            msg: Message = await asyncio.wait_for(q.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue
        payload = json.dumps(msg.to_dict())
        dead = set()
        for ws in list(_ws_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        _ws_clients.difference_update(dead)


# ── HTTP Endpoints ─────────────────────────────────────────────────────────────

class MessageRequest(BaseModel):
    content: str
    target:  Optional[str] = None  # agent_id or None for auto-routing


class AgentSpec(BaseModel):
    agent_id: str
    chat_url: str
    role:     str = "general"
    port:     int = 9300


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the Web UI."""
    ui_path = Path(__file__).parent.parent / "ui" / "static" / "index.html"
    if ui_path.exists():
        return HTMLResponse(ui_path.read_text())
    return HTMLResponse("<h1>walidcode daemon running</h1><p>UI not found.</p>")


@app.get("/api/status")
async def status():
    return {
        "status":    "running",
        "uptime_s":  round(time.time() - _start_time, 1),
        "pid":       os.getpid(),
        "agents":    _orchestrator.agent_count if _orchestrator else 0,
        "ws_clients": len(_ws_clients),
    }


@app.get("/api/agents")
async def list_agents():
    if not _orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    return {"agents": _orchestrator.agent_list()}


@app.get("/api/history")
async def history(limit: int = 100):
    if not _orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    return {"messages": _orchestrator.get_history(limit)}


@app.post("/api/message")
async def send_message(req: MessageRequest):
    if not _orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    await _orchestrator.route_user_message(req.content, target_agent=req.target)
    return {"ok": True, "routed_to": req.target or "auto"}


@app.post("/api/broadcast")
async def broadcast_message(req: MessageRequest):
    if not _orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    await _orchestrator.broadcast_to_agents(req.content)
    return {"ok": True}


@app.post("/api/agents/add")
async def add_agent(spec: AgentSpec):
    if not _orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    cfg = AgentConfig(
        agent_id  = spec.agent_id,
        chat_url  = spec.chat_url,
        role      = spec.role,
        debug_port= spec.port,
    )
    await _orchestrator.spawn_agent(cfg)
    return {"ok": True, "agent_id": spec.agent_id}


@app.delete("/api/agents/{agent_id}")
async def remove_agent(agent_id: str):
    if not _orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    await _orchestrator.remove_agent(agent_id)
    return {"ok": True}


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    ws_id = f"ws_{id(websocket)}"
    logger.info("WS client connected: %s  (total: %d)", ws_id, len(_ws_clients))

    # Send history to newly connected client
    if _orchestrator:
        for msg in _orchestrator.get_history(50):
            try:
                await websocket.send_text(json.dumps(msg))
            except Exception:
                break

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # Send heartbeat ping
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue

            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            msg_type = payload.get("type", "user_input")

            if msg_type == "user_input" and _orchestrator:
                content = payload.get("content", "").strip()
                target  = payload.get("target")
                if content:
                    # Publish to bus so all WS clients see the user message too
                    await _orchestrator.bus.publish(Message(
                        type    = MsgType.USER_INPUT,
                        content = content,
                        source  = "user",
                        target  = target or "broadcast",
                    ))
                    await _orchestrator.route_user_message(content, target_agent=target)

            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)
        logger.info("WS client disconnected: %s  (remaining: %d)", ws_id, len(_ws_clients))


# ── Entry point (when run as module) ──────────────────────────────────────────

def run_daemon(swarm_config: SwarmConfig):
    """Start uvicorn with the swarm config attached to app.state."""
    app.state.swarm_config = swarm_config

    # Add file logging if walidcode_home exists
    swarm_config.ensure_home()
    file_handler = logging.FileHandler(swarm_config.log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
    logging.getLogger().addHandler(file_handler)

    uvicorn.run(
        app,
        host       = swarm_config.daemon_host,
        port       = swarm_config.daemon_port,
        log_level  = "warning",   # uvicorn itself stays quiet; our logger handles output
        access_log = False,
    )


if __name__ == "__main__":
    import click

    @click.command()
    @click.option("--agent",    multiple=True, required=True,
                  help='Agent spec: "id|url|role"  (repeat for multiple agents)')
    @click.option("--port",     default=7771,  show_default=True)
    @click.option("--host",     default="127.0.0.1", show_default=True)
    @click.option("--skills",   default="skills", show_default=True)
    @click.option("--dry-run",  is_flag=True)
    def main(agent, port, host, skills, dry_run):
        agent_configs = make_agent_configs_from_specs(list(agent))
        for cfg in agent_configs:
            cfg.dry_run = dry_run

        swarm_config = SwarmConfig(
            agents      = agent_configs,
            daemon_host = host,
            daemon_port = port,
            skills_dir  = skills,
        )
        run_daemon(swarm_config)

    main()
