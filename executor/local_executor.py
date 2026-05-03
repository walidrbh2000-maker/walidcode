"""
LocalExecutor — implements every built-in tool, now accepts both
AgentConfig and SwarmConfig for backwards compatibility.
Sandbox rules (allowed_root_dirs, shell blocklist, file-size cap) enforced.
"""

import json
import logging
import os
import shlex
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("executor")

BLOCKED_SHELL_PATTERNS = [
    "rm -rf /", "rm -rf ~", ":(){:|:&};:", "mkfs",
    "dd if=/dev/zero", "> /dev/sda", "chmod -R 777 /",
    "chown -R", "shutdown", "reboot", "halt",
]


class LocalExecutor:
    def __init__(self, config, swarm_config=None):
        """
        config       : AgentConfig (per-agent settings)
        swarm_config : SwarmConfig (optional, for skills_dir etc.)
        """
        self.config        = config
        self.swarm_config  = swarm_config
        skills_dir = (
            swarm_config.skills_dir if swarm_config
            else getattr(config, "skills_dir", "skills")
        )
        from executor.skill_registry import SkillRegistry
        self.skills = SkillRegistry(skills_dir)

    # ── Public dispatch ────────────────────────────────────────────────────────

    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        # Skill plugins have priority
        skill_result = self.skills.run(tool_name, parameters)
        if skill_result is not None:
            return skill_result

        dispatch = {
            "read_file":   self._read_file,
            "write_file":  self._write_file,
            "list_dir":    self._list_dir,
            "delete_file": self._delete_file,
            "shell":       self._shell,
            "http_get":    self._http_get,
            "mcp_call":    self._mcp_call,
            "mcp_github":  self._mcp_github,
            "search_web":  self._search_web,
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return (
                f"[ERROR] Unknown tool: '{tool_name}'. "
                f"Available: {', '.join(dispatch)} + skills: {self.skills.list_skills()}"
            )
        try:
            return handler(parameters)
        except Exception as exc:
            logger.exception("Tool '%s' raised an exception.", tool_name)
            return f"[ERROR] {tool_name}: {exc}"

    # ── Sandbox ────────────────────────────────────────────────────────────────

    def _resolve_path(self, raw: str) -> Path:
        expanded = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
        allowed  = self.config.allowed_root_dirs
        if allowed:
            roots = [Path(d).resolve() for d in allowed]
            if not any(str(expanded).startswith(str(r)) for r in roots):
                raise PermissionError(
                    f"Path '{expanded}' outside allowed dirs: "
                    + ", ".join(str(r) for r in roots)
                )
        return expanded

    def _blocked(self, cmd: str) -> bool:
        lower = cmd.lower().replace("  ", " ")
        return any(p.lower() in lower for p in BLOCKED_SHELL_PATTERNS)

    # ── File ops ───────────────────────────────────────────────────────────────

    def _read_file(self, p: dict) -> str:
        path = self._resolve_path(p.get("path", ""))
        if not path.exists():
            return f"[ERROR] File not found: {path}"
        if not path.is_file():
            return f"[ERROR] Not a file: {path}"
        cap  = self.config.max_file_read_bytes
        size = path.stat().st_size
        with open(path, "r", errors="replace") as fh:
            content = fh.read(cap)
        suffix = f"\n[TRUNCATED — {size} bytes, read {cap}]" if size > cap else ""
        return content + suffix

    def _write_file(self, p: dict) -> str:
        path    = self._resolve_path(p.get("path", ""))
        content = p.get("content", "")
        mode    = p.get("mode", "overwrite").lower()
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "overwrite":
            path.write_text(content)
        elif mode == "append":
            with open(path, "a") as fh:
                fh.write(content)
        elif mode == "patch":
            old = p.get("old", "")
            new = p.get("new", "")
            original = path.read_text(errors="replace")
            if old not in original:
                return f"[ERROR] patch: old string not found in {path}"
            path.write_text(original.replace(old, new, 1))
        else:
            return f"[ERROR] Unknown mode: '{mode}'"
        return f"[OK] {mode} → {path}"

    def _list_dir(self, p: dict) -> str:
        path  = self._resolve_path(p.get("path", "."))
        depth = int(p.get("depth", 3))
        if not path.exists():
            return f"[ERROR] Not found: {path}"
        lines = []
        def _walk(cur: Path, level: int):
            if level > depth:
                return
            try:
                entries = sorted(cur.iterdir(), key=lambda e: (e.is_file(), e.name))
            except PermissionError:
                lines.append("  " * level + "[permission denied]")
                return
            for entry in entries:
                lines.append("  " * level + ("📄 " if entry.is_file() else "📁 ") + entry.name)
                if entry.is_dir():
                    _walk(entry, level + 1)
        _walk(path, 0)
        return "\n".join(lines) if lines else "(empty)"

    def _delete_file(self, p: dict) -> str:
        path = self._resolve_path(p.get("path", ""))
        if not path.exists():
            return f"[ERROR] Not found: {path}"
        path.unlink()
        return f"[OK] Deleted: {path}"

    # ── Shell ──────────────────────────────────────────────────────────────────

    def _shell(self, p: dict) -> str:
        command = p.get("command", "").strip()
        if not command:
            return "[ERROR] No command."
        if self._blocked(command):
            return f"[BLOCKED] Dangerous command refused: {command}"
        cwd     = p.get("cwd") or None
        timeout = int(p.get("timeout", self.config.shell_timeout))
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, timeout=timeout, cwd=cwd,
            )
            out = (proc.stdout + proc.stderr).strip()
            return f"{out}\nEXIT {proc.returncode}" if out else f"EXIT {proc.returncode}"
        except subprocess.TimeoutExpired:
            return f"[ERROR] Timed out after {timeout}s"

    # ── HTTP ───────────────────────────────────────────────────────────────────

    def _http_get(self, p: dict) -> str:
        url     = p.get("url", "")
        headers = p.get("headers", {})
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except Exception:
                headers = {}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read(self.config.max_file_read_bytes).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return f"[ERROR] HTTP {e.code}: {e.reason}"
        except Exception as exc:
            return f"[ERROR] http_get: {exc}"

    # ── MCP ────────────────────────────────────────────────────────────────────

    def _mcp_call(self, p: dict) -> str:
        endpoint = p.get("endpoint", "")
        method   = p.get("method", "tools/call")
        args_raw = p.get("arguments", "{}")
        endpoint = getattr(self.config, "mcp_servers", {}).get(endpoint, endpoint)
        if isinstance(args_raw, str):
            try:
                arguments = json.loads(args_raw)
            except Exception:
                arguments = {}
        else:
            arguments = args_raw
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": arguments})
        req = urllib.request.Request(
            endpoint, data=payload.encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read(self.config.max_file_read_bytes).decode()
        except Exception as exc:
            return f"[ERROR] mcp_call: {exc}"

    def _mcp_github(self, p: dict) -> str:
        action = p.get("action", "")
        token  = p.get("token") or os.environ.get("GITHUB_TOKEN", "")
        owner  = p.get("owner", "")
        repo   = p.get("repo", "")
        hdrs   = {"Accept": "application/vnd.github+json"}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        base = "https://api.github.com"
        if action == "get_file":
            path = p.get("path", "")
            ref  = p.get("ref", "")
            url  = f"{base}/repos/{owner}/{repo}/contents/{path}"
            if ref:
                url += f"?ref={ref}"
        elif action == "list_repos":
            url = f"{base}/users/{owner}/repos?per_page=30"
        elif action == "create_issue":
            url = f"{base}/repos/{owner}/{repo}/issues"
            payload = json.dumps({"title": p.get("title",""), "body": p.get("body","")}).encode()
            req = urllib.request.Request(url, data=payload,
                                         headers={**hdrs, "Content-Type":"application/json"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read(self.config.max_file_read_bytes).decode()
            except Exception as exc:
                return f"[ERROR] mcp_github create_issue: {exc}"
        elif action == "search_code":
            q   = urllib.parse.quote(f"{p.get('query','')} repo:{owner}/{repo}")
            url = f"{base}/search/code?q={q}"
        else:
            return f"[ERROR] Unknown mcp_github action: '{action}'"
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read(self.config.max_file_read_bytes).decode()
        except Exception as exc:
            return f"[ERROR] mcp_github: {exc}"

    # ── Web search ─────────────────────────────────────────────────────────────

    def _search_web(self, p: dict) -> str:
        query = p.get("query", "")
        if not query:
            return "[ERROR] No query."
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
            "q": query, "format": "json", "no_html": "1", "skip_disambig": "1"
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "walidcode/2.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read(self.config.max_file_read_bytes))
        except Exception as exc:
            return f"[ERROR] search_web: {exc}"
        parts = []
        if data.get("AbstractText"):
            parts.append(f"Summary: {data['AbstractText']}")
            if data.get("AbstractURL"):
                parts.append(f"Source: {data['AbstractURL']}")
        for item in data.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                parts.append(f"• {item['Text']}")
        return "\n".join(parts) if parts else f"No results for: {query}"
