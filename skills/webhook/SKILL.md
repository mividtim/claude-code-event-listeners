---
description: Start a one-shot HTTP server that waits for a single request. Use for receiving webhooks or HTTP callbacks on localhost.
argument-hint: [port=9999]
allowed-tools: Bash, Read
---

Start a localhost webhook listener on port $ARGUMENTS (default 9999) as a background task:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh webhook $ARGUMENTS", run_in_background=true)
```

The server accepts a single HTTP request (any method), prints the request as JSON, and exits. The output format is:
```json
{"method": "POST", "path": "/hook", "body": "{...}"}
```

When you receive the `<task-notification>`, read the output to get the request payload. Parse and react to it. Start a new listener if you want to receive more webhooks.

This listener is localhost-only. For a publicly accessible webhook URL (via ngrok), use `/event-listeners:webhook-public` instead.
