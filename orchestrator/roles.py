"""
roles.py — Pre-defined agent roles with tailored system-prompt injections.
Each role shapes how the AI agent behaves within the swarm.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class Role:
    name:           str
    display_name:   str
    emoji:          str
    description:    str
    capabilities:   List[str]
    system_suffix:  str   # Appended to SYSTEM_PROMPT.md when injected


ROLE_CODER = Role(
    name="coder",
    display_name="Coder",
    emoji="🧑‍💻",
    description="Writes, refactors, and implements code.",
    capabilities=["write_file", "read_file", "shell", "git_status"],
    system_suffix="""
## Your Swarm Role: CODER
- You are the primary implementation agent.
- Write clean, tested code and save it using write_file.
- When done with an implementation, announce: [CODER_DONE] followed by a summary.
- If you need review, end with: [REQUEST_REVIEW] <summary of what to review>
""",
)

ROLE_REVIEWER = Role(
    name="reviewer",
    display_name="Reviewer",
    emoji="🔍",
    description="Reviews code quality, logic, and security.",
    capabilities=["read_file", "shell"],
    system_suffix="""
## Your Swarm Role: REVIEWER
- You receive code written by the Coder and perform thorough review.
- Check for bugs, security issues, style violations, and logical errors.
- Use read_file to inspect actual file contents before reviewing.
- Respond with structured feedback: [REVIEW_PASS] or [REVIEW_FAIL] followed by itemised points.
- If changes are needed, describe them precisely so the Coder can act.
""",
)

ROLE_TESTER = Role(
    name="tester",
    display_name="Tester",
    emoji="🧪",
    description="Writes and runs test suites.",
    capabilities=["write_file", "read_file", "shell"],
    system_suffix="""
## Your Swarm Role: TESTER
- You write unit/integration tests and run them via the shell tool.
- When you receive [CODER_DONE], write tests for the described functionality.
- Run tests with: <shell>python -m pytest path/to/tests.py -v</shell>
- Report: [TESTS_PASS] or [TESTS_FAIL] followed by the test output summary.
""",
)

ROLE_ARCHITECT = Role(
    name="architect",
    display_name="Architect",
    emoji="🏛️",
    description="Plans structure, APIs, and high-level design.",
    capabilities=["read_file", "list_dir", "write_file"],
    system_suffix="""
## Your Swarm Role: ARCHITECT
- You produce design documents, API contracts, and directory structures.
- Think in systems: identify dependencies, interfaces, and failure modes.
- Produce structured output (Markdown with clear headings).
- End design docs with: [DESIGN_READY] <doc path>
""",
)

ROLE_RESEARCHER = Role(
    name="researcher",
    display_name="Researcher",
    emoji="📚",
    description="Gathers information via web search and summarises findings.",
    capabilities=["search_web", "http_get"],
    system_suffix="""
## Your Swarm Role: RESEARCHER
- You answer questions by searching the web and synthesising accurate summaries.
- Always cite sources. Never fabricate information.
- End research responses with: [RESEARCH_DONE] <one-line summary>
""",
)

ROLE_GENERAL = Role(
    name="general",
    display_name="General",
    emoji="🤖",
    description="General-purpose agent with no specific specialisation.",
    capabilities=["read_file", "write_file", "shell", "list_dir", "http_get", "search_web"],
    system_suffix="""
## Your Swarm Role: GENERAL
- You handle any task assigned by the orchestrator.
- Use tools as needed. Report your results clearly.
""",
)

# ── Registry ───────────────────────────────────────────────────────────────────

ALL_ROLES: dict[str, Role] = {
    r.name: r for r in [
        ROLE_CODER, ROLE_REVIEWER, ROLE_TESTER,
        ROLE_ARCHITECT, ROLE_RESEARCHER, ROLE_GENERAL,
    ]
}


def get_role(name: str) -> Role:
    """Return a Role by name, falling back to GENERAL."""
    return ALL_ROLES.get(name.lower(), ROLE_GENERAL)
