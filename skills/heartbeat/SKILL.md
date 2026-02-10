---
description: Run a heartbeat that monitors multiple commands on an interval. Fires when any command's output changes. Use this instead of multiple separate poll listeners when you need to watch several things at once.
argument-hint: <interval-seconds> <cmd1> -- <cmd2> [-- <cmd3> ...]
allowed-tools: Bash, Read
---

Run a heartbeat event source that monitors multiple commands and fires when any of them change:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh heartbeat $ARGUMENTS", run_in_background=true)
```

The source:
1. Parses commands separated by `--` delimiters
2. Runs all commands once to capture baselines
3. Re-runs all commands every `<interval>` seconds
4. When ANY command's output differs from its previous run, prints a JSON report and exits

The JSON report includes which commands changed, their old and new output, and a timestamp:
```json
{"changed": ["cmd_0"], "results": {"cmd_0": {"old": "5", "new": "6"}, "cmd_1": {"old": "ok", "new": "ok"}}, "timestamp": "2026-02-10T12:00:00Z"}
```

When the `<task-notification>` arrives, at least one command's output changed. Read the output file to see the JSON report with details about what changed.

IMPORTANT: Do not add `&` to the command — `run_in_background=true` handles backgrounding.

Examples:
- Monitor two APIs every 60s: `/el:heartbeat 60 "curl -s https://api.example.com/count" -- "curl -s https://api.example.com/status"`
- Watch comment count and deploy status every 90s: `/el:heartbeat 90 "scripts/moltbook-counts.sh" -- "gh run list -b main -L1 --json status -q '.[0].status'"`
- Track three metrics every 30s: `/el:heartbeat 30 "wc -l < /var/log/app.log" -- "docker ps -q | wc -l" -- "curl -s localhost:8080/health"`
