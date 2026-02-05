---
description: Watch all checks on a pull request until they complete. Use when waiting for CI checks, code review bots, or other PR status checks.
argument-hint: <pr-number>
allowed-tools: Bash, Read
---

Watch all PR checks as a background task:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh pr-checks $ARGUMENTS", run_in_background=true)
```

When the `<task-notification>` arrives, all checks have resolved. Read the output to see which passed and which failed.

**Requirements:** `gh` CLI must be installed and authenticated.
