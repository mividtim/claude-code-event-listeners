---
description: Watch a file for modifications. Use when waiting for a config file to change, a build output to appear, or any file system event.
argument-hint: <file-path>
allowed-tools: Bash, Read
---

Watch a file for changes as a background task:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh file-change $ARGUMENTS", run_in_background=true)
```

Uses `fswatch` (macOS), `inotifywait` (Linux), or stat-polling fallback. Fires once when the file is modified.

When the `<task-notification>` arrives, the file has been modified. Read the file to see what changed, and start a new listener if you want to keep watching.
