---
description: Start the event drain loop. Events from all sources (Slack, webhooks, polls, file watches) arrive through this single background task. Re-arm after each notification. Use at session start and after every task-notification from a previous drain.
argument-hint:
allowed-tools: Bash, Read
---

Start the sidecar event drain as a background task.

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh drain", run_in_background=true, timeout=600000)
```

That's it. One command. No parameters to configure.

**When a `<task-notification>` arrives for the drain task:**

1. Read the output file
2. If events arrived (not `[]`), process them by routing on the `source` field
3. **Re-arm immediately** by running `/el:drain` again

**Rules:**
- NEVER drain in the foreground — that blocks the conversation
- NEVER use `source-register.py` for the drain — that creates circular nesting
- NEVER run multiple drains — one consumer per session
- ALWAYS re-arm after each notification, including empty `[]` returns
