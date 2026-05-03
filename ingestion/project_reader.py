"""
ingestion/project_reader.py — Mass Project Context Builder

Reads an entire directory tree (up to max_tokens worth of content),
respects .gitignore, compresses the output into an LLM-ready context block.

Usage
─────
  reader  = ProjectReader("~/my_project", max_chars=3_000_000)
  context = reader.build_context()      # returns the formatted string
  stats   = reader.stats()              # {"files": N, "chars": N, "skipped": N}

The formatted context follows the pattern used by tools like "Repomix":
  <project_context root="…">
    <file path="src/main.py">
    … file content …
    </file>
    …
  </project_context>
"""

import os
import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("ingestion")


# ── Defaults ───────────────────────────────────────────────────────────────────

DEFAULT_IGNORE_PATTERNS = [
    # VCS
    ".git", ".svn", ".hg",
    # Build outputs
    "__pycache__", "*.pyc", "*.pyo", ".mypy_cache", ".pytest_cache",
    "node_modules", "dist", "build", ".next", ".nuxt", "out",
    "target", "*.egg-info", ".eggs",
    # Compiled / binary
    "*.so", "*.dylib", "*.dll", "*.exe", "*.o", "*.a",
    "*.jar", "*.war", "*.class",
    # Media / large assets
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico", "*.svg",
    "*.mp4", "*.mp3", "*.wav", "*.ogg",
    "*.zip", "*.tar.gz", "*.tgz", "*.gz", "*.bz2", "*.7z", "*.rar",
    "*.pdf", "*.docx", "*.xlsx",
    # Secrets / env
    ".env", ".env.*", "*.pem", "*.key", "*.crt",
    # Misc
    ".DS_Store", "Thumbs.db", "*.lock",
    # Logs / DB
    "*.log", "*.sqlite", "*.db",
]

# Max single-file size to include (bytes)
MAX_FILE_BYTES = 500_000

# File extensions considered text
TEXT_EXTENSIONS = {
    ".py",".js",".ts",".jsx",".tsx",".mjs",".cjs",
    ".html",".htm",".css",".scss",".sass",".less",
    ".json",".yaml",".yml",".toml",".ini",".cfg",".conf",
    ".sh",".bash",".zsh",".fish",".bat",".ps1",
    ".md",".rst",".txt",".csv",
    ".c",".cpp",".cc",".h",".hpp",
    ".java",".kt",".kts",".gradle",
    ".go",".rs",".rb",".php",".lua",".r",".jl",
    ".xml",".svg",".graphql",".proto",
    ".dockerfile","dockerfile",".gitignore",".gitattributes",
    ".env.example",".editorconfig",
    "",  # extensionless files (Makefile, Dockerfile, etc.)
}


def _load_pathspec_gitignore(root: Path):
    """Load .gitignore rules using pathspec if available, else return None."""
    try:
        import pathspec
        gitignore = root / ".gitignore"
        if gitignore.exists():
            patterns = gitignore.read_text(errors="replace").splitlines()
            return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    except ImportError:
        logger.debug("pathspec not installed — .gitignore won't be respected")
    return None


def _matches_default_ignore(rel_path: str) -> bool:
    """Return True if any path component matches a default ignore pattern."""
    import fnmatch
    parts = Path(rel_path).parts
    for pattern in DEFAULT_IGNORE_PATTERNS:
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
        if fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def _is_text_file(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return True
    # Extensionless: sniff first 512 bytes
    if not ext:
        try:
            sample = path.read_bytes()[:512]
            # If no null bytes, assume text
            return b"\x00" not in sample
        except Exception:
            return False
    return False


def _estimate_chars(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ── Main class ─────────────────────────────────────────────────────────────────

class ProjectReader:
    def __init__(
        self,
        root:           str,
        max_chars:      int  = 3_000_000,   # ~750k tokens @ 4 chars/tok
        max_file_bytes: int  = MAX_FILE_BYTES,
        include_hidden: bool = False,
        extra_ignore:   Optional[List[str]] = None,
    ):
        self.root           = Path(root).expanduser().resolve()
        self.max_chars      = max_chars
        self.max_file_bytes = max_file_bytes
        self.include_hidden = include_hidden
        self.extra_ignore   = extra_ignore or []

        self._files_read   = 0
        self._files_skip   = 0
        self._total_chars  = 0

        self._gitignore_spec = _load_pathspec_gitignore(self.root)

    # ── Public API ─────────────────────────────────────────────────────────────

    def build_context(self) -> str:
        """Return a formatted XML-ish context block for the LLM."""
        if not self.root.is_dir():
            raise ValueError(f"Not a directory: {self.root}")

        sections = []
        char_budget = self.max_chars

        for file_path in self._walk():
            rel = file_path.relative_to(self.root)
            content, truncated = self._read_file(file_path)
            if content is None:
                self._files_skip += 1
                continue

            # Char budget enforcement
            if len(content) > char_budget:
                content   = content[:char_budget]
                truncated = True
                char_budget = 0
            else:
                char_budget -= len(content)

            trunc_note = "\n[FILE TRUNCATED]" if truncated else ""
            sections.append(
                f'  <file path="{rel.as_posix()}">\n{content}{trunc_note}\n  </file>'
            )
            self._files_read += 1
            self._total_chars += len(content)

            if char_budget <= 0:
                logger.info("Ingestion: char budget exhausted after %d files.", self._files_read)
                break

        tree = self._build_tree_summary()
        header = (
            f'<project_context root="{self.root}" '
            f'files="{self._files_read}" chars="{self._total_chars}">\n\n'
            f'<directory_tree>\n{tree}\n</directory_tree>\n\n'
        )
        footer = "\n</project_context>"
        return header + "\n\n".join(sections) + footer

    def stats(self) -> dict:
        return {
            "files":   self._files_read,
            "skipped": self._files_skip,
            "chars":   self._total_chars,
        }

    # ── Directory walk ─────────────────────────────────────────────────────────

    def _walk(self):
        """Yield text file paths in a stable order, respecting ignore rules."""
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            cur = Path(dirpath)
            rel_dir = cur.relative_to(self.root)

            # Prune hidden directories
            if not self.include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            # Prune ignored directories in-place (modifies walk)
            pruned = []
            for d in dirnames:
                rel = (rel_dir / d).as_posix()
                if _matches_default_ignore(rel):
                    continue
                if self.extra_ignore and any(d == e for e in self.extra_ignore):
                    continue
                if self._gitignore_spec and self._gitignore_spec.match_file(rel + "/"):
                    continue
                pruned.append(d)
            dirnames[:] = sorted(pruned)

            for fname in sorted(filenames):
                if not self.include_hidden and fname.startswith("."):
                    # Still include dotfiles like .gitignore, .env.example
                    if Path(fname).suffix not in (".gitignore", ".env.example", ".editorconfig"):
                        continue

                fpath = cur / fname
                rel   = fpath.relative_to(self.root).as_posix()

                if _matches_default_ignore(rel):
                    continue
                if self._gitignore_spec and self._gitignore_spec.match_file(rel):
                    continue
                if not _is_text_file(fpath):
                    continue

                yield fpath

    def _read_file(self, path: Path) -> Tuple[Optional[str], bool]:
        """Read a file; return (content, was_truncated) or (None, False) on error."""
        try:
            size = path.stat().st_size
            if size == 0:
                return "", False
            truncated = size > self.max_file_bytes
            with open(path, "r", errors="replace") as fh:
                content = fh.read(self.max_file_bytes)
            return content, truncated
        except Exception as e:
            logger.debug("Cannot read %s: %s", path, e)
            return None, False

    def _build_tree_summary(self, max_lines: int = 200) -> str:
        """Build a compact directory tree string."""
        lines = [str(self.root)]
        count = [0]

        def _walk_tree(path: Path, prefix: str, level: int):
            if count[0] >= max_lines or level > 6:
                return
            try:
                entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name))
            except PermissionError:
                return
            for i, entry in enumerate(entries):
                if count[0] >= max_lines:
                    lines.append(prefix + "└── …")
                    return
                is_last   = (i == len(entries) - 1)
                connector = "└── " if is_last else "├── "
                ext_prefix= "    " if is_last else "│   "
                rel       = entry.relative_to(self.root).as_posix()
                if _matches_default_ignore(rel):
                    continue
                lines.append(prefix + connector + entry.name)
                count[0] += 1
                if entry.is_dir():
                    _walk_tree(entry, prefix + ext_prefix, level + 1)

        _walk_tree(self.root, "", 0)
        return "\n".join(lines)
