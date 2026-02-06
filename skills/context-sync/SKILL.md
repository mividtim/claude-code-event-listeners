---
description: Watch for CLAUDE.md and .claude/ docs changes across the entire project. Use at session start to sync context from other sessions.
argument-hint: [project-root]
allowed-tools: Bash, Read
---

Start watching for context file changes as a background task. This monitors:
- `**/CLAUDE.md` — agent instructions anywhere in the tree
- `.claude/docs/*.md` — documentation files
- `.claude/commands/*.md` — custom commands

If no project root is provided, uses the git root of the current directory.

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh file-change --root ${ARGUMENTS:-$(git rev-parse --show-toplevel)} '**/CLAUDE.md' '.claude/docs/*.md' '.claude/commands/*.md'", run_in_background=true)
```

When the `<task-notification>` arrives, another session (or the user) modified
a context file. Re-read the changed file to incorporate updates, then start a
new listener to keep watching:

1. Check output for the changed file path
2. Read the changed file
3. If it contains new instructions relevant to your work, follow them
4. Start a new `/el:context-sync` listener

This enables multi-session context propagation: when any session updates
CLAUDE.md and pushes to the shared project root, all other sessions get
notified and can incorporate the changes.
