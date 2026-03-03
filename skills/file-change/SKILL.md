---
description: Watch file(s) for modifications. Supports glob patterns and multiple paths. Use when waiting for config changes, build outputs, or cross-session context updates.
argument-hint: [--root <dir>] <path-or-glob> [path-or-glob...]
allowed-tools: Bash, Read
---

Register a watch source with the sidecar that fires when matching files change.

Parse `$ARGUMENTS` for optional `--root <dir>` and the file paths/glob patterns.

Generate a source name (e.g., `watch-claude-md`, `watch-docs`).

```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' watch 'SOURCE_NAME' $ARGUMENTS")
```

The `source-register.py watch` subcommand accepts the same `--root` flag and path arguments as the old `file-change.sh`.

Events arrive through the sidecar drain — look for `source: "runtime:SOURCE_NAME"` with `type: "file_changed"`. The event `text` contains the changed file path.

The watch re-arms automatically after each event — no need to restart. To stop watching:
```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-remove.py' 'SOURCE_NAME'")
```

Supports:
- Direct file paths: `CLAUDE.md`
- Glob patterns (require fswatch/inotifywait): `'**/CLAUDE.md'`, `'.claude/docs/*.md'`
- Multiple paths: `'**/CLAUDE.md' '.claude/commands/*.md'`
- `--root <dir>` to watch from a parent directory (e.g., main worktree root)
