---
description: Watch for CLAUDE.md and .claude/ docs changes in trusted locations. Use at session start to sync context from other sessions.
argument-hint: [project-root]
allowed-tools: Bash, Read
---

Start watching for context file changes as a background task. Only monitors
TRUSTED locations to prevent prompt injection from dependencies:

- `CLAUDE.md` — root-level instructions
- `*/CLAUDE.md` — immediate subdirs (submodules)
- `worktrees/*/CLAUDE.md` — worktree-specific instructions
- `.claude/docs/*.md` — documentation
- `.claude/commands/*.md` — custom commands

Does NOT watch `**/CLAUDE.md` recursively — that would include node_modules,
vendor directories, etc., which could contain malicious instructions.

If no project root is provided, uses the git root of the current directory.

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh context-sync ${ARGUMENTS:-}", run_in_background=true)
```

When the `<task-notification>` arrives, another session (or the user) modified
a context file. Re-read the changed file to incorporate updates, then start a
new listener to keep watching:

1. Check output for the changed file path
2. Read the changed file
3. If it contains new instructions relevant to your work, follow them
4. Start a new `/el:context-sync` listener

This enables multi-session context propagation: when any session updates
CLAUDE.md, all other sessions get notified.
