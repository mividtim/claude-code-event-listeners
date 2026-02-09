---
description: Poll a command at regular intervals and get notified when its output changes. Use this for monitoring APIs, checking status endpoints, watching counters, or any periodic check where you care about changes.
argument-hint: <interval-seconds> <command>
allowed-tools: Bash, Read
---

Run a polling event source that fires when the command's output changes:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh poll $ARGUMENTS", run_in_background=true)
```

The source:
1. Runs the command once to capture a baseline
2. Re-runs it every `<interval>` seconds
3. When the output differs from the previous run, prints the new output and exits

When the `<task-notification>` arrives, the output changed. Read the output file to see the new value.

IMPORTANT: Do not add `&` to the command — `run_in_background=true` handles backgrounding.

Examples:
- Monitor an API every 30s: `/el:poll 30 "curl -s https://api.example.com/status | jq .count"`
- Watch a file's line count: `/el:poll 10 "wc -l < /var/log/app.log"`
- Check PR merge status: `/el:poll 60 "gh pr view 42 --json state -q .state"`
