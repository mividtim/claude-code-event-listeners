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
| `/el:sidecar start [port]` | Starting the event hub (discovers plugins automatically) |
| `/el:sidecar stop` | Stopping the running sidecar |
| `/el:sidecar status` | Checking sidecar health and loaded plugins |
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

The sidecar (`el-sidecar.py`) is a source-agnostic event hub with a self-registering
plugin system. It uses `ThreadingHTTPServer` so long-poll requests don't block ingestion.

### Plugin System

Plugins self-register via `sidecar/plugin.py` with a `register(api)` function. On startup,
the sidecar reads `~/.claude/plugins/installed_plugins.json` and loads any installed plugin
that has this file. Six hooks are available:

| Hook | Purpose |
|------|---------|
| `register_route(method, path, handler)` | Add HTTP endpoints |
| `register_poller(name, func)` | Background polling threads |
| `register_init(name, func)` | Run once after all plugins load |
| `register_on_pick(name, func)` | Called when events are drained |
| `register_enrichment(name, func)` | Enrich events during insertion |
| `register_watch_handler(name, add, remove)` | PR-scoped watch callbacks |

### Per-Agent Isolation

- **Port**: Auto-assigned (port 0) unless `SIDECAR_PORT` is set. Actual port in `.claude/sidecar.json`.
- **DB**: `/tmp/el-sidecar-{hash}.db` — deterministic per project, no collisions.
- **Metadata**: `.claude/sidecar.json` with port, PID, DB path. Drain commands read this.

### Single Drain Pattern

Use exactly ONE consumer draining events from the sidecar. Multiple consumers compete
for the `picked_up` flag, causing missed events. The correct architecture:

1. One `el:listen` background task running a drain script
2. The drain script long-polls `GET /events?wait=true`
3. On receiving events, output them to stdout and exit
4. The agent reads the output, routes each event by `source` field, re-invokes the listener
5. With `wait=true`, the sidecar blocks forever — never returns `[]`

### Drain Script Example

```bash
#!/usr/bin/env bash
SIDECAR_JSON="$(pwd)/.claude/sidecar.json"
PORT=$(python3 -c "import json; print(json.load(open('$SIDECAR_JSON'))['port'])")
SIDECAR_URL="http://localhost:$PORT"
while true; do
  RESPONSE=$(curl -sf "${SIDECAR_URL}/events?wait=true" 2>/dev/null) || { sleep 5; continue; }
  if [ -z "$RESPONSE" ]; then continue; fi
  echo "$RESPONSE"
  exit 0
done
```
