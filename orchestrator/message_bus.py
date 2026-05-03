"""
message_bus.py — Async pub/sub message bus for inter-agent communication.

Topology
────────
  user  ──► orchestrator ──► agent_A
                         ──► agent_B
  agent_A ──► orchestrator ──► agent_B  (inter-agent routing)
  agent_A ──► orchestrator ──► user     (result delivery)

All messages flow through the bus so the daemon can log / replay / broadcast
to any connected WebSocket client with zero coupling.
"""

import asyncio
import time
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, AsyncIterator

logger = logging.getLogger("message_bus")


class MsgType(str, Enum):
    USER_INPUT      = "user_input"      # user → orchestrator
    AGENT_TASK      = "agent_task"      # orchestrator → agent
    AGENT_RESPONSE  = "agent_response"  # agent → orchestrator
    INTER_AGENT     = "inter_agent"     # agent → agent (via orchestrator)
    TOOL_RESULT     = "tool_result"     # executor → agent
    SYSTEM          = "system"          # daemon status / notifications
    BROADCAST       = "broadcast"       # orchestrator → ALL agents


@dataclass
class Message:
    type:       MsgType
    content:    str
    source:     str                  # sender id ("user", "orchestrator", or agent_id)
    target:     str   = "broadcast"  # receiver id or "broadcast" or "user"
    id:         str   = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp:  float = field(default_factory=time.time)
    metadata:   dict  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id":        self.id,
            "type":      self.type.value,
            "content":   self.content,
            "source":    self.source,
            "target":    self.target,
            "timestamp": self.timestamp,
            "metadata":  self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            type      = MsgType(d["type"]),
            content   = d["content"],
            source    = d["source"],
            target    = d.get("target", "broadcast"),
            id        = d.get("id", str(uuid.uuid4())[:8]),
            timestamp = d.get("timestamp", time.time()),
            metadata  = d.get("metadata", {}),
        )


class MessageBus:
    """
    Central async pub/sub bus.

    Subscribers register with a subscriber_id and receive a Queue.
    Publishing to "broadcast" delivers to every subscriber.
    Publishing to a specific id delivers only to that subscriber.
    History keeps the last `history_limit` messages for late joiners.
    """

    def __init__(self, history_limit: int = 500):
        self._queues:       Dict[str, asyncio.Queue] = {}
        self._history:      List[Message]            = []
        self._history_limit = history_limit
        self._lock          = asyncio.Lock()

    # ── Subscription management ────────────────────────────────────────────────

    def subscribe(self, subscriber_id: str, maxsize: int = 200) -> asyncio.Queue:
        """Register a subscriber; return its private Queue."""
        if subscriber_id in self._queues:
            logger.debug("Bus: %s already subscribed, returning existing queue.", subscriber_id)
            return self._queues[subscriber_id]
        q = asyncio.Queue(maxsize=maxsize)
        self._queues[subscriber_id] = q
        logger.debug("Bus: %s subscribed.", subscriber_id)
        return q

    def unsubscribe(self, subscriber_id: str):
        self._queues.pop(subscriber_id, None)
        logger.debug("Bus: %s unsubscribed.", subscriber_id)

    # ── Publishing ─────────────────────────────────────────────────────────────

    async def publish(self, message: Message):
        """Deliver message to target subscriber(s) and append to history."""
        async with self._lock:
            self._history.append(message)
            if len(self._history) > self._history_limit:
                self._history.pop(0)

        target = message.target
        if target == "broadcast":
            recipients = list(self._queues.keys())
        else:
            recipients = [target] if target in self._queues else []
            # Always deliver to "monitor" subscribers (daemon WS clients)
            for sid in self._queues:
                if sid.startswith("ws_") and sid not in recipients:
                    recipients.append(sid)

        for sid in recipients:
            q = self._queues.get(sid)
            if q:
                try:
                    q.put_nowait(message)
                except asyncio.QueueFull:
                    logger.warning("Bus: queue full for %s — dropping message %s", sid, message.id)

    async def publish_system(self, content: str, source: str = "system"):
        await self.publish(Message(
            type    = MsgType.SYSTEM,
            content = content,
            source  = source,
            target  = "broadcast",
        ))

    # ── History ────────────────────────────────────────────────────────────────

    def get_history(self, limit: Optional[int] = None) -> List[Message]:
        h = self._history
        return h[-limit:] if limit else list(h)

    @property
    def subscriber_ids(self) -> List[str]:
        return list(self._queues.keys())

    # ── Async iteration helper ─────────────────────────────────────────────────

    async def iter_queue(self, subscriber_id: str) -> AsyncIterator[Message]:
        """Yield messages from a subscriber's queue indefinitely."""
        q = self._queues.get(subscriber_id)
        if q is None:
            q = self.subscribe(subscriber_id)
        while True:
            msg = await q.get()
            yield msg
