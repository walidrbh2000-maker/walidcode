# Skill Prompts Directory

Drop `.md` files here to inject them as additional system-prompt context
into every agent's session at startup.

## Example

Create `skills/prompts/coding_style.md`:

```markdown
## Coding Style Requirements
- Use Python 3.11+ type hints on all function signatures.
- Prefer dataclasses over plain dicts for structured data.
- Write docstrings for every public function.
- Maximum line length: 100 characters.
```

The file will be appended to the system prompt that walidcode injects
into each web chat session automatically.

## Loading at runtime

You can also use the `/skills path1 path2` slash command in the TUI to
attach skill prompt files on the fly.
