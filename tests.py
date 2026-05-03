"""
Tests for walidcode v2 — Swarm Framework.
No browser or daemon required.

Run:  python -m pytest tests.py -v
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════════════════════════
# ToolParser
# ══════════════════════════════════════════════════════════════════════════════

class TestToolParser(unittest.TestCase):
    def setUp(self):
        from executor.tool_parser import ToolParser
        self.parser = ToolParser()

    def _parse(self, text):
        return self.parser.extract_tool_calls(text)

    def test_xml_full_form(self):
        text = "<tool_call><name>read_file</name><path>/etc/hosts</path></tool_call>"
        calls = self._parse(text)
        self.assertEqual(calls[0]["tool"], "read_file")
        self.assertEqual(calls[0]["parameters"]["path"], "/etc/hosts")

    def test_json_form(self):
        text = '<tool_call>{"tool": "shell", "command": "ls -la"}</tool_call>'
        calls = self._parse(text)
        self.assertEqual(calls[0]["tool"], "shell")
        self.assertEqual(calls[0]["parameters"]["command"], "ls -la")

    def test_shorthand_read_file(self):
        calls = self._parse("<read_file>/home/user/notes.txt</read_file>")
        self.assertEqual(calls[0]["tool"], "read_file")
        self.assertEqual(calls[0]["parameters"]["path"], "/home/user/notes.txt")

    def test_shorthand_shell(self):
        calls = self._parse("<shell>echo hello</shell>")
        self.assertEqual(calls[0]["tool"], "shell")

    def test_multiple_calls(self):
        text = "<read_file>/a.txt</read_file>\n<shell>ls /tmp</shell>"
        calls = self._parse(text)
        self.assertEqual(len(calls), 2)
        tools = [c["tool"] for c in calls]
        self.assertIn("read_file", tools)
        self.assertIn("shell", tools)

    def test_no_calls(self):
        self.assertEqual(self._parse("Normal response."), [])

    def test_json_name_alias(self):
        text = '<tool_call>{"name": "list_dir", "path": "/tmp"}</tool_call>'
        calls = self._parse(text)
        self.assertEqual(calls[0]["tool"], "list_dir")


# ══════════════════════════════════════════════════════════════════════════════
# LocalExecutor
# ══════════════════════════════════════════════════════════════════════════════

class TestLocalExecutor(unittest.TestCase):
    def setUp(self):
        from config import AgentConfig
        from executor.local_executor import LocalExecutor
        self.tmp = tempfile.mkdtemp()
        self.config = AgentConfig(
            agent_id="test",
            chat_url="http://localhost",
            allowed_root_dirs=[self.tmp],
        )
        self.executor = LocalExecutor(self.config)

    def _exec(self, tool, **params):
        return self.executor.execute_tool(tool, params)

    def test_write_and_read(self):
        path = os.path.join(self.tmp, "hello.txt")
        self.assertIn("[OK]", self._exec("write_file", path=path, content="Hello!"))
        self.assertIn("Hello!", self._exec("read_file", path=path))

    def test_append_mode(self):
        path = os.path.join(self.tmp, "log.txt")
        self._exec("write_file", path=path, content="Line 1\n")
        self._exec("write_file", path=path, content="Line 2\n", mode="append")
        result = self._exec("read_file", path=path)
        self.assertIn("Line 1", result)
        self.assertIn("Line 2", result)

    def test_patch_mode(self):
        path = os.path.join(self.tmp, "code.py")
        self._exec("write_file", path=path, content="x = 1\ny = 2\n")
        result = self._exec("write_file", path=path, mode="patch", old="x = 1", new="x = 99")
        self.assertIn("[OK]", result)
        self.assertIn("x = 99", self._exec("read_file", path=path))

    def test_list_dir(self):
        Path(os.path.join(self.tmp, "subdir")).mkdir()
        Path(os.path.join(self.tmp, "file.txt")).write_text("hi")
        result = self._exec("list_dir", path=self.tmp)
        self.assertIn("file.txt", result)
        self.assertIn("subdir", result)

    def test_delete_file(self):
        path = os.path.join(self.tmp, "todelete.txt")
        Path(path).write_text("bye")
        self.assertIn("[OK]", self._exec("delete_file", path=path))
        self.assertFalse(Path(path).exists())

    def test_sandbox_escape_blocked(self):
        result = self._exec("read_file", path="/etc/passwd")
        self.assertIn("ERROR", result)
        self.assertIn("outside allowed", result)

    def test_read_nonexistent(self):
        self.assertIn("ERROR", self._exec("read_file",
                                          path=os.path.join(self.tmp, "ghost.txt")))

    def test_shell_echo(self):
        result = self._exec("shell", command="echo ping")
        self.assertIn("ping", result)
        self.assertIn("EXIT 0", result)

    def test_shell_blocked(self):
        self.assertIn("BLOCKED", self._exec("shell", command="rm -rf /"))

    def test_unknown_tool(self):
        self.assertIn("Unknown tool", self._exec("flying_spaghetti"))


# ══════════════════════════════════════════════════════════════════════════════
# MessageBus
# ══════════════════════════════════════════════════════════════════════════════

class TestMessageBus(unittest.IsolatedAsyncioTestCase):
    async def test_subscribe_and_receive(self):
        from orchestrator.message_bus import MessageBus, Message, MsgType
        bus = MessageBus()
        q   = bus.subscribe("agent1")
        msg = Message(type=MsgType.AGENT_TASK, content="hello", source="user", target="agent1")
        await bus.publish(msg)
        received = await asyncio.wait_for(q.get(), timeout=1.0)
        self.assertEqual(received.content, "hello")

    async def test_broadcast(self):
        from orchestrator.message_bus import MessageBus, Message, MsgType
        bus = MessageBus()
        q1  = bus.subscribe("a1")
        q2  = bus.subscribe("a2")
        msg = Message(type=MsgType.BROADCAST, content="boom", source="orch", target="broadcast")
        await bus.publish(msg)
        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        self.assertEqual(r1.content, "boom")
        self.assertEqual(r2.content, "boom")

    async def test_history(self):
        from orchestrator.message_bus import MessageBus, Message, MsgType
        bus = MessageBus()
        bus.subscribe("x")
        for i in range(5):
            await bus.publish(Message(
                type=MsgType.SYSTEM, content=f"msg{i}", source="sys", target="broadcast"
            ))
        history = bus.get_history(3)
        self.assertEqual(len(history), 3)
        self.assertEqual(history[-1].content, "msg4")

    async def test_system_publish(self):
        from orchestrator.message_bus import MessageBus, MsgType
        bus = MessageBus()
        q   = bus.subscribe("listener")
        await bus.publish_system("test notification")
        msg = await asyncio.wait_for(q.get(), timeout=1.0)
        self.assertEqual(msg.type, MsgType.SYSTEM)


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

class TestConfig(unittest.TestCase):
    def test_make_agent_configs(self):
        from config import make_agent_configs_from_specs
        specs   = ["coder|https://deepseek.com|coder", "rev|https://claude.ai|reviewer"]
        configs = make_agent_configs_from_specs(specs, base_port=9200)
        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0].agent_id, "coder")
        self.assertEqual(configs[0].role, "coder")
        self.assertEqual(configs[0].debug_port, 9200)
        self.assertEqual(configs[1].agent_id, "rev")
        self.assertEqual(configs[1].debug_port, 9201)

    def test_agent_config_defaults(self):
        from config import AgentConfig
        cfg = AgentConfig(agent_id="test", chat_url="http://x.com")
        self.assertEqual(cfg.role, "general")
        self.assertEqual(cfg.debug_port, 9222)
        self.assertTrue(cfg.headless)

    def test_swarm_config_paths(self):
        from config import SwarmConfig
        cfg = SwarmConfig(agents=[])
        self.assertIn(".walidcode", cfg.pid_file)
        self.assertIn(".walidcode", cfg.log_file)


# ══════════════════════════════════════════════════════════════════════════════
# Roles
# ══════════════════════════════════════════════════════════════════════════════

class TestRoles(unittest.TestCase):
    def test_known_role(self):
        from orchestrator.roles import get_role
        r = get_role("coder")
        self.assertEqual(r.name, "coder")
        self.assertIn("write_file", r.capabilities)

    def test_unknown_role_fallback(self):
        from orchestrator.roles import get_role, ROLE_GENERAL
        r = get_role("nonexistent")
        self.assertEqual(r.name, ROLE_GENERAL.name)

    def test_all_roles_have_system_suffix(self):
        from orchestrator.roles import ALL_ROLES
        for name, role in ALL_ROLES.items():
            self.assertIn("Role", role.system_suffix, f"{name} missing Role marker")


# ══════════════════════════════════════════════════════════════════════════════
# ProjectReader (ingestion)
# ══════════════════════════════════════════════════════════════════════════════

class TestProjectReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Create a small fake project
        (Path(self.tmp) / "main.py").write_text("print('hello')\n")
        (Path(self.tmp) / "README.md").write_text("# My Project\n")
        sub = Path(self.tmp) / "src"
        sub.mkdir()
        (sub / "utils.py").write_text("def helper(): pass\n")
        # Binary file that should be ignored
        (Path(self.tmp) / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        # .git dir that should be ignored
        git = Path(self.tmp) / ".git"
        git.mkdir()
        (git / "HEAD").write_text("ref: refs/heads/main\n")

    def test_reads_text_files(self):
        from ingestion.project_reader import ProjectReader
        reader  = ProjectReader(self.tmp)
        context = reader.build_context()
        self.assertIn("main.py", context)
        self.assertIn("README.md", context)
        self.assertIn("utils.py", context)

    def test_ignores_binary_and_git(self):
        from ingestion.project_reader import ProjectReader
        reader  = ProjectReader(self.tmp)
        context = reader.build_context()
        self.assertNotIn("image.png", context)
        self.assertNotIn(".git", context)

    def test_stats(self):
        from ingestion.project_reader import ProjectReader
        reader = ProjectReader(self.tmp)
        reader.build_context()
        stats = reader.stats()
        self.assertGreaterEqual(stats["files"], 3)
        self.assertGreater(stats["chars"], 0)

    def test_max_chars_limit(self):
        from ingestion.project_reader import ProjectReader
        # Very small budget
        reader  = ProjectReader(self.tmp, max_chars=50)
        context = reader.build_context()
        self.assertIn("<project_context", context)

    def test_contains_tree(self):
        from ingestion.project_reader import ProjectReader
        reader  = ProjectReader(self.tmp)
        context = reader.build_context()
        self.assertIn("<directory_tree>", context)


# ══════════════════════════════════════════════════════════════════════════════
# SkillRegistry
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp     = tempfile.mkdtemp()
        skill_path   = Path(self.tmp) / "echo_skill.py"
        skill_path.write_text(
            'SKILL_NAME = "echo_skill"\n'
            'SKILL_DESCRIPTION = "Echoes input."\n'
            'def run(parameters):\n'
            '    return "ECHO: " + parameters.get("text", "")\n'
        )

    def test_load_and_run(self):
        from executor.skill_registry import SkillRegistry
        reg    = SkillRegistry(self.tmp)
        result = reg.run("echo_skill", {"text": "hello"})
        self.assertEqual(result, "ECHO: hello")

    def test_list_skills(self):
        from executor.skill_registry import SkillRegistry
        reg = SkillRegistry(self.tmp)
        self.assertIn("echo_skill", reg.list_skills())

    def test_unknown_skill_returns_none(self):
        from executor.skill_registry import SkillRegistry
        reg = SkillRegistry(self.tmp)
        self.assertIsNone(reg.run("nonexistent_skill", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
