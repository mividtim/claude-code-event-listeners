---
description: Watch a GitHub Actions CI run until it completes. Use when waiting for CI to pass or fail after a push.
argument-hint: <run-id | branch-name>
allowed-tools: Bash, Read
---

Watch a GitHub Actions run as a background task:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh ci-watch $ARGUMENTS", run_in_background=true)
```

Pass either a numeric run ID or a branch name. If a branch name is given, it watches the most recent run on that branch.

When the `<task-notification>` arrives, the CI run has completed. Read the output to see pass/fail status and details. If it failed, investigate with `gh run view <id> --log-failed`.

**Requirements:** `gh` CLI must be installed and authenticated.
