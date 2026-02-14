# Event Listeners Plugin (`el`)

When you need to wait for something — CI results, log output, webhooks, file
changes, services coming up — use the `/el:*` slash commands instead of polling.

## Why

Polling burns turns and tokens on sleep-check-repeat loops. Event listeners
launch a background task that blocks until the event occurs. You do nothing
while waiting. When the event fires, you get a `<task-notification>`, read the
output, and react.

## Commands

| Command | Use when |
|---------|----------|
| `/el:ci-watch <run-id \| branch>` | Waiting for a GitHub Actions run to finish |
| `/el:pr-checks <pr-number>` | Waiting for all PR checks to resolve |
| `/el:log-tail <file> [timeout] [max_lines]` | Tailing a log file for new output |
| `/el:file-change <path>` | Watching a file for modifications |
| `/el:webhook [port]` | Receiving an HTTP request on localhost |
| `/el:webhook-public [port] [name] [subdomain]` | Receiving an HTTP request via ngrok tunnel |
| `/el:listen <command...>` | Running any blocking command as an event source |
| `/el:list` | Seeing all available event sources |

## Common Patterns

**After pushing code**, use `/el:ci-watch <branch>` instead of polling
`gh run list`. You'll be notified the moment CI finishes.

**After opening a PR**, use `/el:pr-checks <pr-number>` to wait for all
checks to pass or fail.

**When tailing logs**, use `/el:log-tail <file>`. When the chunk arrives,
process it and start another listener for the next chunk.

**When waiting for a service**, use `/el:listen` with a blocking command like
`while ! curl -s localhost:3000/health; do sleep 1; done`.

**Run multiple listeners concurrently** — they are independent background
tasks. Handle whichever fires first.

## Rules

- Never poll in a loop when an event listener exists for that source.
- Prefer `/el:ci-watch` over `gh run watch` — it integrates with the
  background task notification system.
- After handling an event, re-subscribe if you need continuous monitoring.
- **Do not add `&` to commands when using `run_in_background=true`.** The
  `run_in_background` parameter already handles backgrounding. Adding `&`
  double-backgrounds the command: the shell exits immediately, the task system
  reports "completed", and the listener continues as an orphan with no
  notification mechanism.

## Sidecar Architecture

The `el-sidecar.py` uses `ThreadingHTTPServer` (not Python's default single-threaded `HTTPServer`). This is critical: long-poll requests like `GET /events?wait=true` block for up to 30 seconds. Without threading, a pending drain blocks all webhook ingestion — Slack retries with exponential backoff, causing 6+ minute event delays.

### Single Drain Pattern

Use exactly ONE consumer draining events from the sidecar. Multiple consumers compete for the `picked_up` flag, causing missed events. The correct architecture:

1. One `el:listen` background task running a drain script
2. The drain script long-polls `GET /events?wait=true`
3. On receiving events, output them to stdout and exit
4. The agent reads the output, routes each event by `source` field, re-invokes the listener
5. Empty responses (timeout, `[]`) should be retried internally — only exit on real events

### Drain Script Example

```bash
#!/usr/bin/env bash
SIDECAR_URL="${SIDECAR_URL:-http://localhost:9999}"
while true; do
  RESPONSE=$(curl -sf "${SIDECAR_URL}/events?wait=true" 2>/dev/null) || { sleep 5; continue; }
  if [ "$RESPONSE" = "[]" ] || [ -z "$RESPONSE" ]; then continue; fi
  echo "$RESPONSE"
  exit 0
done
```
