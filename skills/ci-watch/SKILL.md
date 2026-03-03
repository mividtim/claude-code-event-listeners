---
description: Watch a GitHub Actions CI run until it completes. Use when waiting for CI to pass or fail after a push.
argument-hint: <run-id | branch-name>
allowed-tools: Bash, Read
---

Register a CI source with the sidecar that fires when the run completes.

Parse `$ARGUMENTS` for a run ID (numeric) or branch name.

Generate a source name (e.g., `ci-main`, `ci-12345`).

```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' ci 'SOURCE_NAME' $ARGUMENTS")
```

Events arrive through the sidecar drain — look for `source: "runtime:SOURCE_NAME"` with `type: "ci_completed"`. The event `text` contains pass/fail details. Check `metadata.exit_code` for the result.

If the run failed, investigate with `gh run view <id> --log-failed`.

**Requirements:** `gh` CLI must be installed and authenticated.
