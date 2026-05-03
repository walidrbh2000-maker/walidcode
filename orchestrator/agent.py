"""
agent.py — AsyncAgentNode

One agent = one Playwright browser session + one LocalExecutor.
The agent runs its own asyncio task:
  1. Polls the web chat UI for new AI messages.
  2. Parses tool calls and executes them locally.
  3. Injects tool results back into the chat.
  4. Publishes AGENT_RESPONSE messages onto the MessageBus.

Fault-tolerance: automatic reconnection with exponential back-off.
"""

import asyncio
import hashlib
import logging
import os
import socket
import subprocess
import time
from enum import Enum
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

from config import AgentConfig, SwarmConfig
from orchestrator.message_bus import MessageBus, Message, MsgType
from orchestrator.roles import get_role, Role
from executor.local_executor import LocalExecutor
from executor.tool_parser import ToolParser

logger = logging.getLogger("agent")


# ── Platform selector catalogue (same as before, kept here for async bridge) ──

PLATFORM_SELECTORS = {
    "deepseek": {
        "last_ai_message": "div[class*='message'][class*='assistant']:last-of-type",
        "input_box":       "textarea#chat-input, textarea[placeholder]",
        "send_button":     "button[type='submit'], button[aria-label*='send' i]",
    },
    "chatgpt": {
        "last_ai_message": "div[data-message-author-role='assistant']:last-of-type",
        "input_box":       "div#prompt-textarea",
        "send_button":     "button[data-testid='send-button']",
    },
    "claude": {
        "last_ai_message": "div[data-testid='assistant-message']:last-of-type",
        "input_box":       "div[contenteditable='true']",
        "send_button":     "button[aria-label='Send message']",
    },
    "gemini": {
        "last_ai_message": "model-response:last-of-type .markdown",
        "input_box":       "div[contenteditable='true']",
        "send_button":     "button.send-button, button[aria-label*='send' i]",
    },
    "generic": {
        "last_ai_message": "div.assistant, div.ai-message, div[class*='response']:last-of-type",
        "input_box":       "textarea, div[contenteditable='true']",
        "send_button":     "button[type='submit']",
    },
}


def _detect_platform(url: str) -> str:
    url = url.lower()
    if "deepseek"    in url: return "deepseek"
    if "chat.openai" in url or "chatgpt" in url: return "chatgpt"
    if "claude.ai"   in url: return "claude"
    if "gemini"      in url: return "gemini"
    return "generic"


class AgentStatus(str, Enum):
    STARTING     = "starting"
    READY        = "ready"
    BUSY         = "busy"
    RECONNECTING = "reconnecting"
    STOPPED      = "stopped"
    FAILED       = "failed"


class AsyncAgentNode:
    """
    Async Playwright agent with auto-reconnect and MessageBus integration.
    """

    def __init__(
        self,
        config:       AgentConfig,
        swarm_config: SwarmConfig,
        bus:          MessageBus,
    ):
        self.config       = config
        self.swarm_config = swarm_config
        self.bus          = bus
        self.role: Role   = get_role(config.role)

        self.platform     = _detect_platform(config.chat_url)
        self.selectors    = PLATFORM_SELECTORS.get(self.platform, PLATFORM_SELECTORS["generic"])

        self.executor     = LocalExecutor(config, swarm_config)
        self.parser       = ToolParser()

        self._pw          = None
        self._browser: Optional[Browser]        = None
        self._context: Optional[BrowserContext] = None
        self._page:    Optional[Page]           = None
        self._chromium_proc: Optional[subprocess.Popen] = None

        self._last_hash:    Optional[str] = None
        self._status:       AgentStatus   = AgentStatus.STARTING
        self._stop_event    = asyncio.Event()
        self._reconnects    = 0
        self._task_queue    = self.bus.subscribe(config.agent_id)

        logger.info("[%s] Agent created — role=%s platform=%s port=%d",
                    config.agent_id, config.role, self.platform, config.debug_port)

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    @property
    def status(self) -> AgentStatus:
        return self._status

    def info_dict(self) -> dict:
        return {
            "id":       self.agent_id,
            "role":     self.role.name,
            "emoji":    self.role.emoji,
            "url":      self.config.chat_url,
            "platform": self.platform,
            "status":   self._status.value,
            "port":     self.config.debug_port,
        }

    # ── Chromium lifecycle ─────────────────────────────────────────────────────

    def _cdp_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.config.debug_port), timeout=2):
                return True
        except OSError:
            return False

    def _chromium_args(self) -> list:
        args = [
            f"--remote-debugging-port={self.config.debug_port}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-default-apps", "--disable-extensions",
        ]
        if self.config.is_termux:
            args += [
                "--headless", "--disable-gpu", "--disable-software-rasterizer",
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox",
                "--single-process", "--no-zygote",
                f"--user-data-dir=/data/data/com.termux/files/tmp/walidcode-{self.agent_id}",
            ]
        else:
            if self.config.headless:
                args.append("--headless=new")
            args += [
                "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                f"--user-data-dir=/tmp/walidcode-{self.agent_id}",
            ]
        return args

    async def _ensure_chromium(self) -> bool:
        if self._cdp_open():
            logger.info("[%s] CDP port %d already open — reusing.", self.agent_id, self.config.debug_port)
            return True

        binary = self.config.chromium_binary
        args   = [binary] + self._chromium_args()
        logger.info("[%s] Launching Chromium on port %d …", self.agent_id, self.config.debug_port)
        try:
            self._chromium_proc = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            logger.error("[%s] Chromium binary not found: %s", self.agent_id, binary)
            return False

        deadline = asyncio.get_event_loop().time() + 15
        while asyncio.get_event_loop().time() < deadline:
            if self._cdp_open():
                logger.info("[%s] Chromium ready on port %d.", self.agent_id, self.config.debug_port)
                return True
            await asyncio.sleep(0.5)

        logger.error("[%s] Chromium did not open CDP port within 15 s.", self.agent_id)
        return False

    # ── Playwright session ─────────────────────────────────────────────────────

    async def _start_session(self) -> bool:
        try:
            if not await self._ensure_chromium():
                return False

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(
                f"http://localhost:{self.config.debug_port}"
            )
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                pages = self._context.pages
                self._page = pages[0] if pages else await self._context.new_page()
            else:
                self._context = await self._browser.new_context()
                self._page    = await self._context.new_page()

            await self._page.goto(self.config.chat_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            logger.info("[%s] Session started — navigated to %s", self.agent_id, self.config.chat_url)
            return True
        except Exception as e:
            logger.error("[%s] Session start failed: %s", self.agent_id, e)
            return False

    async def _close_session(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        if self._chromium_proc and self._chromium_proc.poll() is None:
            self._chromium_proc.terminate()
        self._browser = self._context = self._page = self._pw = None

    # ── Chat I/O ───────────────────────────────────────────────────────────────

    async def _poll(self) -> Optional[str]:
        if not self._page:
            return None
        try:
            sel  = self.selectors["last_ai_message"]
            el   = await self._page.query_selector(sel)
            if not el:
                return None
            text = (await el.inner_text() or "").strip()
            if not text:
                return None
            h = hashlib.md5(text.encode()).hexdigest()
            if h == self._last_hash:
                return None
            self._last_hash = h
            return text
        except Exception as e:
            logger.debug("[%s] Poll error: %s", self.agent_id, e)
            return None

    async def _send(self, text: str) -> bool:
        if not self._page:
            return False
        try:
            input_sel = self.selectors["input_box"]
            send_sel  = self.selectors["send_button"]

            input_el = await self._page.wait_for_selector(input_sel, timeout=5_000)
            if not input_el:
                return False
            await input_el.click()
            tag = ((await input_el.evaluate("el => el.tagName")) or "").lower()
            if tag == "textarea":
                await input_el.fill(text)
            else:
                await self._page.evaluate(
                    """(args) => {
                        const el = document.querySelector(args.sel);
                        el.focus();
                        document.execCommand('insertText', false, args.txt);
                    }""",
                    {"sel": input_sel, "txt": text},
                )
            await asyncio.sleep(0.3)
            btn = await self._page.query_selector(send_sel)
            if btn and await btn.is_enabled():
                await btn.click()
            else:
                await input_el.press("Enter")
            return True
        except Exception as e:
            logger.error("[%s] send error: %s", self.agent_id, e)
            return False

    async def send_system_prompt(self, system_prompt_text: str):
        """Inject the walidcode system prompt at the start of a session."""
        await self._send(system_prompt_text)
        logger.info("[%s] System prompt injected.", self.agent_id)

    # ── Tool execution ─────────────────────────────────────────────────────────

    async def _handle_tool_calls(self, message_text: str) -> Optional[str]:
        calls = self.parser.extract_tool_calls(message_text)
        if not calls:
            return None

        self._status = AgentStatus.BUSY
        logger.info("[%s] %d tool call(s) detected.", self.agent_id, len(calls))

        # Execute up to max_parallel_skills concurrently
        limit = self.swarm_config.max_parallel_skills
        results = []
        for batch_start in range(0, len(calls), limit):
            batch = calls[batch_start : batch_start + limit]
            tasks = []
            for call in batch:
                if self.config.dry_run:
                    tasks.append(asyncio.coroutine(
                        lambda c=call: f"[DRY-RUN] {c['tool']}({c['parameters']})"
                    )())
                else:
                    tasks.append(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            self.executor.execute_tool,
                            call["tool"],
                            call["parameters"],
                        )
                    )
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for call, res in zip(batch, batch_results):
                if isinstance(res, Exception):
                    res = f"[ERROR] {res}"
                results.append(f'<tool_result name="{call["tool"]}">\n{res}\n</tool_result>')

        self._status = AgentStatus.READY
        return f"{self.swarm_config.result_prefix}\n" + "\n\n".join(results)

    # ── Inter-agent routing ────────────────────────────────────────────────────

    def _extract_inter_agent_message(self, text: str) -> Optional[tuple]:
        """Detect <send_to agent="id">msg</send_to> patterns."""
        import re
        m = re.search(r'<send_to\s+agent=["\'](\w+)["\']>(.*?)</send_to>', text, re.DOTALL)
        if m:
            return m.group(1), m.group(2).strip()
        return None

    # ── Main agent loop ────────────────────────────────────────────────────────

    async def run(self):
        """Main agent lifecycle: connect → poll + handle inbound tasks → reconnect on failure."""
        while not self._stop_event.is_set():
            self._status = AgentStatus.STARTING
            await self.bus.publish_system(
                f"{self.role.emoji} Agent [{self.agent_id}] connecting …",
                source=self.agent_id,
            )

            if not await self._start_session():
                self._reconnects += 1
                if self._reconnects > self.config.max_reconnect_tries:
                    self._status = AgentStatus.FAILED
                    await self.bus.publish_system(
                        f"❌ Agent [{self.agent_id}] failed after {self._reconnects} attempts.",
                        source=self.agent_id,
                    )
                    return
                delay = min(self.config.reconnect_delay * (2 ** (self._reconnects - 1)), 120)
                self._status = AgentStatus.RECONNECTING
                await self.bus.publish_system(
                    f"🔄 Agent [{self.agent_id}] reconnecting in {delay:.0f}s (attempt {self._reconnects}) …",
                    source=self.agent_id,
                )
                await asyncio.sleep(delay)
                continue

            self._status  = AgentStatus.READY
            self._reconnects = 0
            await self.bus.publish_system(
                f"{self.role.emoji} Agent [{self.agent_id}] READY ({self.role.display_name} @ {self.config.chat_url})",
                source=self.agent_id,
            )

            try:
                await self._agent_loop()
            except Exception as e:
                logger.error("[%s] Agent loop crashed: %s", self.agent_id, e, exc_info=True)
            finally:
                await self._close_session()

        self._status = AgentStatus.STOPPED
        logger.info("[%s] Agent stopped.", self.agent_id)

    async def _agent_loop(self):
        """Inner loop: poll chat + drain inbound task queue concurrently."""
        poll_task  = asyncio.create_task(self._poll_loop())
        inbox_task = asyncio.create_task(self._inbox_loop())

        done, pending = await asyncio.wait(
            [poll_task, inbox_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for t in pending:
            t.cancel()
        for t in done:
            if t.exception():
                raise t.exception()

    async def _poll_loop(self):
        """Continuously poll the web chat for new AI messages."""
        while not self._stop_event.is_set():
            text = await self._poll()
            if text:
                # Check for inter-agent routing
                ia = self._extract_inter_agent_message(text)
                if ia:
                    target_id, content = ia
                    await self.bus.publish(Message(
                        type    = MsgType.INTER_AGENT,
                        content = content,
                        source  = self.agent_id,
                        target  = target_id,
                        metadata={"original": text},
                    ))
                    logger.info("[%s] → inter-agent message routed to [%s].", self.agent_id, target_id)
                else:
                    # Publish response to bus (daemon will forward to UI)
                    await self.bus.publish(Message(
                        type    = MsgType.AGENT_RESPONSE,
                        content = text,
                        source  = self.agent_id,
                        target  = "broadcast",
                        metadata= {"role": self.role.name},
                    ))

                # Handle any tool calls in the message
                result_payload = await self._handle_tool_calls(text)
                if result_payload:
                    await self._send(result_payload)

            await asyncio.sleep(self.config.poll_interval)

    async def _inbox_loop(self):
        """Drain tasks arriving on our message-bus queue."""
        while not self._stop_event.is_set():
            try:
                msg: Message = await asyncio.wait_for(
                    self._task_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            if msg.type in (MsgType.AGENT_TASK, MsgType.INTER_AGENT, MsgType.BROADCAST):
                logger.info("[%s] Inbox: %s from [%s]", self.agent_id, msg.type.value, msg.source)
                # Format and send to the AI in the browser
                prefix = f"[From {msg.source}]: " if msg.type == MsgType.INTER_AGENT else ""
                await self._send(prefix + msg.content)

    async def stop(self):
        self._stop_event.set()
        await self._close_session()
