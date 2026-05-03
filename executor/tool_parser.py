"""
ToolParser — extracts structured tool calls from raw AI message text.
Supports XML full form, JSON form, and tag shorthands.
"""

import re
import json
import xml.etree.ElementTree as ET
from typing import List, Dict, Any


SHORTHAND_TOOLS = {
    "read_file":   "path",
    "write_file":  "path",
    "shell":       "command",
    "list_dir":    "path",
    "mcp_call":    "endpoint",
    "mcp_github":  "action",
    "http_get":    "url",
    "search_web":  "query",
}


class ToolParser:
    """Parses tool calls from AI-generated text."""

    _BLOCK_RE = re.compile(
        r"<tool_call>(.*?)</tool_call>",
        re.DOTALL | re.IGNORECASE,
    )
    _SHORTHAND_RE = re.compile(
        r"<({tags})>(.*?)</\1>".format(tags="|".join(SHORTHAND_TOOLS.keys())),
        re.DOTALL | re.IGNORECASE,
    )

    def extract_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        results   : List[Dict[str, Any]] = []
        seen_spans = []

        for match in self._BLOCK_RE.finditer(text):
            body = match.group(1).strip()
            call = self._parse_block(body)
            if call:
                results.append(call)
                seen_spans.append(match.span())

        for match in self._SHORTHAND_RE.finditer(text):
            if any(s[0] <= match.start() < s[1] for s in seen_spans):
                continue
            tag       = match.group(1).lower()
            value     = match.group(2).strip()
            param_key = SHORTHAND_TOOLS[tag]
            results.append({"tool": tag, "parameters": {param_key: value}})

        return results

    @staticmethod
    def _parse_block(body: str) -> Dict[str, Any] | None:
        if body.startswith("{"):
            try:
                data = json.loads(body)
                tool = data.pop("tool", data.pop("name", None))
                if not tool:
                    return None
                return {"tool": tool, "parameters": data}
            except json.JSONDecodeError:
                pass

        try:
            root    = ET.fromstring(f"<root>{body}</root>")
            name_el = root.find("name")
            if name_el is None or not name_el.text:
                return None
            tool   = name_el.text.strip()
            params = {
                child.tag: (child.text or "").strip()
                for child in root
                if child.tag != "name"
            }
            return {"tool": tool, "parameters": params}
        except ET.ParseError:
            pass

        return None
