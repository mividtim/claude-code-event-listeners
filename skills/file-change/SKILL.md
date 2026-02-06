---
description: Watch file(s) for modifications. Supports glob patterns and multiple paths. Use when waiting for config changes, build outputs, or cross-session context updates.
argument-hint: [--root <dir>] <path-or-glob> [path-or-glob...]
allowed-tools: Bash, Read
---

Watch file(s) for changes as a background task. Quote glob patterns in single
quotes to prevent shell expansion:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh file-change $ARGUMENTS", run_in_background=true)
```

Supports direct file paths, multiple files, and glob patterns:
- `CLAUDE.md` — one specific file
- `'**/CLAUDE.md'` — any CLAUDE.md in the tree
- `'.claude/docs/*.md'` — all .md files in .claude/docs/
- `'**/CLAUDE.md' '.claude/commands/*.md' '.claude/docs/*.md'` — all three

Use `--root <dir>` to watch from a parent directory (e.g., the main worktree
root so changes from other worktrees are visible):
- `--root /path/to/project '**/CLAUDE.md'`

Glob patterns require `fswatch` (macOS: `brew install fswatch`) or `inotifywait`
(Linux). Direct file paths work everywhere via stat-polling fallback.

When the `<task-notification>` arrives, a matching file was modified. Read the
changed file to see what's new, then start a new listener to keep watching.
