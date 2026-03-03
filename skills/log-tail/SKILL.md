---
description: Tail a log file in the background. Returns chunks of log output as events. Use when asked to monitor, tail, or watch log files.
argument-hint: <file> [timeout=10] [max_lines=100]
allowed-tools: Bash, Read
---

Register a tail source with the sidecar that collects log lines as events.

Parse `$ARGUMENTS`: first arg is the file path, optional second is per-line timeout (default 10s), optional third is max lines per chunk (default 100).

Generate a source name (e.g., `tail-app-log`).

```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' tail 'SOURCE_NAME' FILE_PATH TIMEOUT MAX_LINES")
```

Omit TIMEOUT and MAX_LINES to use defaults (10s, 100 lines).

Events arrive through the sidecar drain — look for `source: "runtime:SOURCE_NAME"` with `type: "log_lines"`. The event `text` contains the collected lines. Check `metadata.line_count` for how many lines arrived.

The tail re-arms automatically, collecting successive chunks. To stop:
```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-remove.py' 'SOURCE_NAME'")
```
