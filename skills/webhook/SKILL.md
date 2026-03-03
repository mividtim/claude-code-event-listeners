---
description: Register a webhook endpoint on the sidecar. Incoming HTTP requests become events. Use for receiving webhooks or HTTP callbacks.
argument-hint: [path=/hook]
allowed-tools: Bash, Read
---

Register a webhook source on the sidecar that fires on each incoming HTTP request.

Parse `$ARGUMENTS` for an optional path (default: `/hook`). Generate a source name (e.g., `webhook-hook`, `webhook-github`).

```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' webhook 'SOURCE_NAME' '/PATH'")
```

After registration, report the webhook URL to the user:
```
http://localhost:<sidecar-port>/PATH
```

Read `.claude/sidecar.json` to get the sidecar port.

Events arrive through the sidecar drain — look for `source: "runtime:SOURCE_NAME"` with `type: "webhook_received"`. The event `text` is a JSON object with `method`, `path`, and `body` fields.

Unlike the old one-shot webhook, this stays active and fires on every request. To stop:
```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-remove.py' 'SOURCE_NAME'")
```

For a publicly accessible URL (via ngrok), use `/el:webhook-public` instead.
