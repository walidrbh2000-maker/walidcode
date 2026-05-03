"""
config.py — Central configuration for walidcode Swarm Framework.
AgentConfig  : per-agent settings (URL, role, browser).
SwarmConfig  : swarm-level settings (daemon, skills, ingestion).
"""

import os
import shutil
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ── Binary detection (unchanged) ───────────────────────────────────────────────

def _detect_chromium_binary() -> str:
    env_override = os.environ.get("WALIDCODE_CHROMIUM_BIN")
    if env_override:
        return env_override
    candidates = [
        "/data/data/com.termux/files/usr/bin/chromium-browser",
        "/data/data/com.termux/files/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    for name in ("chromium-browser", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return "chromium-browser"


def _is_termux() -> bool:
    return os.path.isdir("/data/data/com.termux")


def _walidcode_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".walidcode")


# ── Per-agent configuration ────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Configuration for a single AI agent / Playwright session."""
    agent_id:             str                      # unique id e.g. "coder"
    chat_url:             str                      # e.g. "https://chat.deepseek.com"
    role:                 str           = "general"
    debug_port:           int           = 9222     # each agent needs a unique port
    poll_interval:        float         = 2.5
    headless:             bool          = True
    shell_timeout:        int           = 30
    max_file_read_bytes:  int           = 1_000_000
    allowed_root_dirs:    List[str]     = field(default_factory=list)
    mcp_servers:          Dict[str,str] = field(default_factory=dict)
    chromium_binary:      str           = field(default_factory=_detect_chromium_binary)
    is_termux:            bool          = field(default_factory=_is_termux)
    dry_run:              bool          = False
    # How many times to retry before marking agent as failed
    max_reconnect_tries:  int           = 5
    reconnect_delay:      float         = 10.0     # seconds between retries


# ── Swarm-level configuration ──────────────────────────────────────────────────

@dataclass
class SwarmConfig:
    """Top-level configuration for the entire swarm + daemon."""
    agents:             List[AgentConfig]
    skills_dir:         str           = "skills"
    daemon_host:        str           = "127.0.0.1"
    daemon_port:        int           = 7771
    result_prefix:      str           = "[TOOL_RESULT]"
    # Parallel skill execution limit (per agent)
    max_parallel_skills: int          = 5
    # Ingestion
    max_ingest_tokens:  int           = 900_000    # leave headroom
    # Paths
    walidcode_home:     str           = field(default_factory=_walidcode_home)

    @property
    def pid_file(self) -> str:
        return os.path.join(self.walidcode_home, "daemon.pid")

    @property
    def log_file(self) -> str:
        return os.path.join(self.walidcode_home, "daemon.log")

    @property
    def session_file(self) -> str:
        return os.path.join(self.walidcode_home, "session.json")

    def ensure_home(self):
        os.makedirs(self.walidcode_home, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_agent_configs_from_specs(specs: List[str], base_port: int = 9222) -> List[AgentConfig]:
    """
    Parse agent spec strings like: "id|url|role"
    and assign sequential debug ports.
    """
    configs = []
    for i, spec in enumerate(specs):
        parts = spec.split("|", 2)
        if len(parts) < 2:
            raise ValueError(f"Agent spec must be 'id|url' or 'id|url|role', got: {spec!r}")
        agent_id  = parts[0].strip()
        chat_url  = parts[1].strip()
        role      = parts[2].strip() if len(parts) == 3 else "general"
        configs.append(AgentConfig(
            agent_id=agent_id,
            chat_url=chat_url,
            role=role,
            debug_port=base_port + i,
        ))
    return configs
