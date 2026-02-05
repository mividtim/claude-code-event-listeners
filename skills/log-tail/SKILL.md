---
description: Tail a log file in the background. Returns chunks of log output as events. Use when asked to monitor, tail, or watch log files.
argument-hint: <file> [timeout=10] [max_lines=100]
allowed-tools: Bash, Read
---

Monitor the log file specified in $ARGUMENTS by starting a background event listener.

Run this command as a background task:
```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh log-tail $ARGUMENTS", run_in_background=true)
```

This blocks until either `max_lines` lines are collected or `timeout` seconds pass with no new output (per-line timeout), then returns the chunk.

When you receive the `<task-notification>`, read the output to get the log chunk. Summarize interesting lines (errors, warnings, state changes) for the user. Then start a new background listener for the next chunk to continue monitoring.

To stop monitoring, simply don't start a new listener after processing a chunk.
