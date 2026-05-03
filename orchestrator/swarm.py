"""
swarm.py — SwarmOrchestrator

Manages the lifecycle of all AsyncAgentNodes, routes messages between
them, and exposes a simple async API consumed by the daemon server.

Routing logic
─────────────
  User message → route_user_message()
    • If message targets a specific agent (/assign coder <task>): direct.
    • If message is a broadcast or ambiguous: send to all or to the first
      available READY agent (round-robin among agents).

Inter-agent messages (already handled inside each AgentNode via bus).

The orchestrator also serves as the authoritative registry for agent state.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from config import AgentConfig, SwarmConfig
from orchestrator.agent import AsyncAgentNode, AgentStatus
from orchestrator.message_bus import MessageBus, Message, MsgType
from orchestrator.roles import get_role

logger = logging.getLogger("swarm")


def _load_system_prompt(skills_dir: str, agent_config: AgentConfig) -> str:
    """
    Build the full system prompt:
      base SYSTEM_PROMPT.md  +  role-specific suffix  +  any .md skill prompts
    """
    # 1. Base prompt
    base_path = Path(__file__).parent.parent / "SYSTEM_PROMPT.md"
    if base_path.exists():
        base = base_path.read_text(errors="replace")
        # Strip the comment header lines
        lines = base.splitlines()
        base = "\n".join(l for l in lines if not l.startswith("#"))
    else:
        base = "You are an AI assistant connected to walidcode."

    # 2. Role suffix
    role   = get_role(agent_config.role)
    suffix = role.system_suffix

    # 3. Markdown skill prompts from skills/prompts/
    md_prompts = []
    prompts_dir = Path(skills_dir) / "prompts"
    if prompts_dir.is_dir():
        for md_file in sorted(prompts_dir.glob("*.md")):
            try:
                md_prompts.append(f"\n--- Skill Prompt: {md_file.stem} ---\n" + md_file.read_text())
            except Exception:
                pass

    return base + suffix + "\n".join(md_prompts)


class SwarmOrchestrator:
    """
    Spawns and supervises all agent tasks.
    Provides send_to_agent / broadcast / add_agent / remove_agent.
    """

    def __init__(self, swarm_config: SwarmConfig):
        self.config  = swarm_config
        self.bus     = MessageBus()
        self._agents: Dict[str, AsyncAgentNode] = {}
        self._tasks:  Dict[str, asyncio.Task]   = {}
        self._rr_idx  = 0   # round-robin cursor
        self._started = False

    # ── Agent registry ─────────────────────────────────────────────────────────

    def add_agent(self, agent_config: AgentConfig) -> AsyncAgentNode:
        if agent_config.agent_id in self._agents:
            raise ValueError(f"Agent '{agent_config.agent_id}' already exists.")
        node = AsyncAgentNode(agent_config, self.config, self.bus)
        self._agents[agent_config.agent_id] = node
        logger.info("[swarm] Registered agent: %s (%s)", agent_config.agent_id, agent_config.role)
        return node

    async def spawn_agent(self, agent_config: AgentConfig) -> AsyncAgentNode:
        """Add and immediately start an agent (can be called after swarm is running)."""
        node = self.add_agent(agent_config)
        task = asyncio.create_task(node.run(), name=f"agent-{agent_config.agent_id}")
        self._tasks[agent_config.agent_id] = task
        return node

    async def remove_agent(self, agent_id: str):
        node = self._agents.pop(agent_id, None)
        if node:
            await node.stop()
        task = self._tasks.pop(agent_id, None)
        if task:
            task.cancel()
        logger.info("[swarm] Removed agent: %s", agent_id)

    # ── Startup / shutdown ─────────────────────────────────────────────────────

    async def start(self):
        """Initialise the bus subscriber for the orchestrator itself, spawn all agents."""
        if self._started:
            return
        self._started = True

        # Orchestrator subscribes to the bus to route inter-agent messages
        self._orch_queue = self.bus.subscribe("orchestrator")

        # Spawn all pre-configured agents
        for agent_config in self.config.agents:
            node = self.add_agent(agent_config)
            task = asyncio.create_task(node.run(), name=f"agent-{agent_config.agent_id}")
            self._tasks[agent_config.agent_id] = task

        # Start orchestrator routing loop
        asyncio.create_task(self._routing_loop(), name="orchestrator-router")

        await self.bus.publish_system(
            f"🚀 Swarm started with {len(self._agents)} agent(s): "
            + ", ".join(f"{a.role.emoji}{a.agent_id}" for a in self._agents.values())
        )
        logger.info("[swarm] Started with %d agents.", len(self._agents))

    async def stop(self):
        for node in list(self._agents.values()):
            await node.stop()
        for task in list(self._tasks.values()):
            task.cancel()
        self._agents.clear()
        self._tasks.clear()
        self._started = False
        logger.info("[swarm] Stopped.")

    # ── Message routing ────────────────────────────────────────────────────────

    async def _routing_loop(self):
        """
        Intercept INTER_AGENT messages and forward to the target agent.
        Also clean up crashed agent tasks and attempt restart.
        """
        while True:
            try:
                msg: Message = await asyncio.wait_for(
                    self._orch_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                await self._health_check()
                continue

            if msg.type == MsgType.INTER_AGENT:
                target = msg.target
                if target in self._agents:
                    await self.bus.publish(msg)
                    logger.info("[swarm] Routed inter-agent msg %s → %s", msg.source, target)
                else:
                    await self.bus.publish_system(
                        f"⚠️  Unknown agent target '{target}' from [{msg.source}]"
                    )

    async def _health_check(self):
        """Restart failed agent tasks."""
        for agent_id, task in list(self._tasks.items()):
            if task.done() and not self._agents[agent_id].status == AgentStatus.STOPPED:
                exc = task.exception() if not task.cancelled() else None
                logger.warning("[swarm] Agent task '%s' died (exc=%s). Restarting.", agent_id, exc)
                node = self._agents[agent_id]
                new_task = asyncio.create_task(node.run(), name=f"agent-{agent_id}")
                self._tasks[agent_id] = new_task

    # ── Public API consumed by daemon ──────────────────────────────────────────

    async def send_to_agent(self, agent_id: str, content: str, source: str = "user"):
        """Send a task directly to a named agent."""
        if agent_id not in self._agents:
            raise ValueError(f"Unknown agent: {agent_id}")
        await self.bus.publish(Message(
            type    = MsgType.AGENT_TASK,
            content = content,
            source  = source,
            target  = agent_id,
        ))

    async def broadcast_to_agents(self, content: str, source: str = "user"):
        """Broadcast a message to all ready agents."""
        for agent_id, node in self._agents.items():
            if node.status in (AgentStatus.READY, AgentStatus.BUSY):
                await self.bus.publish(Message(
                    type    = MsgType.AGENT_TASK,
                    content = content,
                    source  = source,
                    target  = agent_id,
                ))

    async def route_user_message(self, content: str, target_agent: Optional[str] = None):
        """
        Smart routing for user messages.
        target_agent=None → round-robin to next READY agent.
        """
        if target_agent:
            await self.send_to_agent(target_agent, content)
            return

        # Round-robin among READY agents
        ready = [aid for aid, n in self._agents.items()
                 if n.status in (AgentStatus.READY, AgentStatus.BUSY)]
        if not ready:
            await self.bus.publish_system("⚠️  No agents currently available.")
            return

        self._rr_idx = self._rr_idx % len(ready)
        target = ready[self._rr_idx]
        self._rr_idx = (self._rr_idx + 1) % len(ready)

        await self.bus.publish(Message(
            type    = MsgType.AGENT_TASK,
            content = content,
            source  = "user",
            target  = target,
        ))

    async def inject_system_prompts(self):
        """
        Wait until agents are READY, then inject their system prompts.
        Call this once after start() if you want auto-injection.
        """
        for agent_id, node in self._agents.items():
            prompt = _load_system_prompt(self.config.skills_dir, node.config)
            await asyncio.sleep(3)  # let the page settle
            await node.send_system_prompt(prompt)

    # ── Status / info ──────────────────────────────────────────────────────────

    def agent_list(self) -> List[dict]:
        return [n.info_dict() for n in self._agents.values()]

    def get_history(self, limit: int = 100) -> List[dict]:
        return [m.to_dict() for m in self.bus.get_history(limit)]

    @property
    def agent_count(self) -> int:
        return len(self._agents)
