---
description: Poll a command at regular intervals and get notified when its output changes. Use this for monitoring APIs, checking status endpoints, watching counters, or any periodic check where you care about changes.
argument-hint: <interval-seconds> <command>
allowed-tools: Bash, Read
---

Register a poll source with the sidecar that fires when command output changes.

Parse `$ARGUMENTS`: first word is the interval (seconds), the rest is the command.

Generate a source name from the command (e.g., `poll-api-status`, `poll-line-count`).

```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' poll 'SOURCE_NAME' INTERVAL COMMAND_WORDS")
```

Replace `SOURCE_NAME`, `INTERVAL`, and `COMMAND_WORDS` with values from the parsed arguments.

Events arrive through the sidecar drain — look for `source: "runtime:SOURCE_NAME"` with `type: "poll_changed"`. The event `text` contains the new command output.

To stop polling later:
```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-remove.py' 'SOURCE_NAME'")
```

Examples:
- Monitor an API every 30s: `/el:poll 30 curl -s https://api.example.com/status | jq .count`
- Watch a file's line count: `/el:poll 10 wc -l < /var/log/app.log`
- Check PR merge status: `/el:poll 60 gh pr view 42 --json state -q .state`
