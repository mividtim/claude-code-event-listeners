---
description: Start the event drain loop. Events from all sources (Slack, webhooks, polls, file watches) arrive through this single background task. Re-arm after each notification. Use at session start and after every task-notification from a previous drain.
argument-hint:
allowed-tools: Bash, Read
---

Start the sidecar event drain. Use **one** of the methods below.

## Method A: Background task (preferred)

Run the drain as a background Bash task. This gives you `<task-notification>` when events arrive.

**IMPORTANT — parameter types must be exact:**
- `run_in_background` MUST be boolean `true`, NOT the string `"true"`
- `timeout` MUST be integer `600000`, NOT the string `"600000"`

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh drain", run_in_background=true, timeout=600000)
```

When a `<task-notification>` arrives:
1. Read the task output
2. If events arrived (not `[]`), process them by routing on the `source` field
3. **Re-arm immediately** by running `/el:drain` again

**If the Bash call fails with an `InputValidationError` about parameter types, use Method B.**

## Method B: Self-backgrounding fallback

If Method A fails due to type coercion errors, use the `--bg` flag which handles backgrounding internally. No `run_in_background` or `timeout` parameters needed:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh drain --bg")
```

This returns immediately with an output file path (e.g., `/tmp/el-drain-HASH.out`).

To check for events later, read the output file:
```
Read(file_path="/tmp/el-drain-HASH.out")
```

Use the actual path printed by the `--bg` command. Re-arm by running `/el:drain` again after reading events.

**Note:** Method B does not provide automatic `<task-notification>`. You must check the output file between other tasks or set a reminder to check it periodically.

## Rules
- NEVER drain in the foreground without `--bg` — that blocks the conversation for up to 8 minutes
- NEVER use `source-register.py` for the drain — that creates circular nesting
- NEVER run multiple drains — one consumer per session
- ALWAYS re-arm after each notification or after reading events
- The `--bg` flag kills any previous background drain automatically — safe to re-arm
