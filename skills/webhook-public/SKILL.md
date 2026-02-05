---
description: Start a one-shot HTTP server exposed via ngrok tunnel. Use for receiving webhooks from external services like GitHub, Stripe, or any service that needs a public URL to POST to.
argument-hint: [port=9999] [tunnel-name=claude-hook]
allowed-tools: Bash, Read
---

Start a public webhook listener as a background task:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh webhook-public $ARGUMENTS", run_in_background=true)
```

This automatically creates an ngrok tunnel (reuses an existing ngrok agent if running, or starts one). The tunnel is cleaned up when the webhook fires or the task is stopped.

**Two-phase output:**

1. **Immediately** (readable via non-blocking `TaskOutput`): `WEBHOOK_URL=https://xxxx.ngrok.app`
   — Use this URL to register with external services (GitHub webhooks, etc.)
2. **When webhook fires**: JSON event payload on subsequent lines

**Workflow:**
1. Start the background listener
2. Read the URL with `TaskOutput(block=false)` — it appears on the first line immediately
3. Register the URL with the external service (e.g., GitHub webhook config)
4. Wait for the `<task-notification>` — the webhook has arrived
5. Read the full output, parse the event JSON (line 2+), and react
6. Start a new listener if you want to receive more webhooks

**Requirements:** ngrok must be installed and authenticated (`ngrok config add-authtoken <token>`).
