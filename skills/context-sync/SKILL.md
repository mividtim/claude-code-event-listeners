---
description: Watch for CLAUDE.md and .claude/ docs changes in trusted locations. Use at session start to sync context from other sessions.
argument-hint: [project-root]
allowed-tools: Bash, Read
---

Register a watch source for trusted context file locations.

Only monitors TRUSTED locations to prevent prompt injection from dependencies:
- `CLAUDE.md` — root-level instructions
- `*/CLAUDE.md` — immediate subdirs (submodules)
- `worktrees/*/CLAUDE.md` — worktree-specific instructions
- `.claude/docs/*.md` — documentation
- `.claude/commands/*.md` — custom commands

Does NOT watch `**/CLAUDE.md` recursively — that would include node_modules, vendor, etc.

Parse `$ARGUMENTS` for an optional project root. If not provided, use the git root of the current directory.

```
Bash(command="ROOT=${ARGUMENTS:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)} && python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' watch context-sync --root \"$ROOT\" 'CLAUDE.md' '*/CLAUDE.md' 'worktrees/*/CLAUDE.md' '.claude/docs/*.md' '.claude/commands/*.md'")
```

Events arrive through the sidecar drain — look for `source: "runtime:context-sync"` with `type: "file_changed"`. The event `text` contains the changed file path.

When an event arrives:
1. Read the changed file to see what's new
2. If it contains new instructions, follow them
3. The watch re-arms automatically — no need to restart
