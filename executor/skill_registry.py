"""
SkillRegistry — hot-loading plugin loader.

Each Python skill exposes SKILL_NAME and run(parameters).
Each Markdown file in skills/prompts/ is treated as a system-prompt injection.
"""

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("skills")


class SkillRegistry:
    def __init__(self, skills_dir: str = "skills"):
        self._dir    = Path(skills_dir)
        self._skills: Dict[str, Any] = {}
        self._load_all()

    def _load_all(self):
        if not self._dir.exists():
            logger.debug("Skills dir '%s' not found — skipping.", self._dir)
            return
        for f in sorted(self._dir.glob("*.py")):
            if not f.name.startswith("_"):
                self._load_file(f)

    def _load_file(self, path: Path):
        try:
            spec   = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            name   = getattr(module, "SKILL_NAME", path.stem)
            if not hasattr(module, "run"):
                logger.warning("Skill '%s' has no run() — skipped.", path.name)
                return
            self._skills[name] = module
            desc = getattr(module, "SKILL_DESCRIPTION", "(no description)")
            logger.info("Loaded skill: %s — %s", name, desc)
        except Exception as exc:
            logger.error("Failed to load skill '%s': %s", path.name, exc)

    def reload(self):
        self._skills.clear()
        self._load_all()

    def run(self, tool_name: str, parameters: Dict) -> Optional[str]:
        module = self._skills.get(tool_name)
        if module is None:
            return None
        try:
            return str(module.run(parameters))
        except Exception as exc:
            logger.exception("Skill '%s' raised.", tool_name)
            return f"[SKILL ERROR] {tool_name}: {exc}"

    def list_skills(self) -> list:
        return list(self._skills.keys())

    def list_prompt_files(self) -> list:
        """Return paths of all .md prompt files under skills/prompts/."""
        prompts_dir = self._dir / "prompts"
        if not prompts_dir.is_dir():
            return []
        return sorted(prompts_dir.glob("*.md"))
