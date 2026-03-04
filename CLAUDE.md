# Event Listeners Plugin (`el`)

When you need to wait for something — CI results, log output, webhooks, file
changes, services coming up — use the `/el:*` slash commands instead of polling.

## Why

Polling burns turns and tokens on sleep-check-repeat loops. Event listeners
register a source with the sidecar that fires when the event occurs. You do
nothing while waiting. Events arrive through the drain loop, and you react.

## Commands

| Command | Use when |
|---------|----------|
| `/el:ci-watch <run-id \| branch>` | Waiting for a GitHub Actions run to finish |
| `/el:pr-checks <pr-number>` | Waiting for all PR checks to resolve |
| `/el:log-tail <file> [timeout] [max_lines]` | Tailing a log file for new output |
| `/el:file-change [--root dir] <path>...` | Watching files for modifications |
| `/el:webhook [path]` | Receiving HTTP requests on the sidecar |
| `/el:webhook-public [path] [subdomain]` | Receiving HTTP requests via ngrok tunnel |
| `/el:poll <interval> <command>` | Polling a command for output changes |
| `/el:listen <command...>` | Running any blocking command as an event source |
| `/el:context-sync [root]` | Watching trusted context files for changes |
| `/el:sidecar start [port]` | Starting the event hub (discovers plugins automatically) |
| `/el:sidecar stop` | Stopping the running sidecar |
| `/el:sidecar status` | Checking sidecar health and loaded plugins |
| `/el:sidecar sources` | Listing active runtime sources |
| `/el:list` | Seeing all available event sources |

## Common Patterns

**After pushing code**, use `/el:ci-watch <branch>` instead of polling
`gh run list`. You'll be notified the moment CI finishes.

**After opening a PR**, use `/el:pr-checks <pr-number>` to wait for all
checks to pass or fail.

**When tailing logs**, use `/el:log-tail <file>`. Log chunks arrive as
events through the drain — no need to restart the listener.

**When waiting for a service**, use `/el:listen` with a blocking command like
`while ! curl -s localhost:3000/health; do sleep 1; done`.

**Run multiple sources concurrently** — they all feed into the same drain.
Handle events by checking the `source` field.

## Rules

- Never poll in a loop when an event source exists for that use case.
- Prefer `/el:ci-watch` over `gh run watch` — it integrates with the sidecar.
- Watch sources (file-change, context-sync) re-arm automatically.
- One-shot sources (ci-watch, listen) deactivate after firing.
- Use `/el:sidecar sources` to see what's registered.

## Sidecar Architecture

The sidecar (`el-sidecar.py`) is a source-agnostic event hub with two parallel
systems for event sources:

### 1. Plugin Pollers (static)

Plugins self-register via `sidecar/plugin.py` with a `register(api)` function.
On startup, the sidecar reads `~/.claude/plugins/installed_plugins.json` and
loads plugins automatically. Six hooks are available:

| Hook | Purpose |
|------|---------|
| `register_route(method, path, handler)` | Add HTTP endpoints |
| `register_poller(name, func)` | Background polling threads |
| `register_init(name, func)` | Run once after all plugins load |
| `register_on_pick(name, func)` | Called when events are drained |
| `register_enrichment(name, func)` | Enrich events during insertion |
| `register_watch_handler(name, add, remove)` | PR-scoped watch callbacks |

### 2. Runtime Sources (dynamic)

Agents register event sources at runtime via HTTP API:

| Endpoint | Purpose |
|----------|---------|
| `POST /source` | Register a new source |
| `DELETE /source` | Remove a source by name |
| `GET /sources` | List active sources with status |

Source types:

| Type | Behavior | Key Config |
|------|----------|------------|
| `poll` | Run command every N seconds, fire on output change | `command`, `interval`, `diff` |
| `heartbeat` | Insert neutral tick event every N seconds | `interval` |
| `watch` | Watch files for modifications | `paths` (list), `root` |
| `tail` | Tail a log file, insert chunks as events | `file`, `timeout`, `max_lines` |
| `ci` | Watch a GitHub Actions run until completion | `run_id` or `branch` |
| `command` | Run blocking command, insert event when done | `command` |
| `webhook` | Register HTTP route, fire on request | `path` |

Both systems insert events into the same DB and arrive through the same drain.

### Per-Agent Isolation

- **Port**: Auto-assigned (port 0) unless `SIDECAR_PORT` is set. Actual port in `.claude/sidecar.json`.
- **DB**: `/tmp/el-sidecar-{hash}.db` — deterministic per project, no collisions.
- **Metadata**: `.claude/sidecar.json` with port, PID, DB path. Drain commands read this.
- **Sources**: Persisted in DB, restored on sidecar restart.

## Draining Events

**This is the most important section.** The drain is how your Claude session
receives events from the sidecar. Getting this wrong breaks the entire event
system.

### The Rule

**Always drain with a background Bash task. Never use source-register.**

### Correct Pattern

```
Bash(command='curl -sf "http://localhost:PORT/events?wait=true&timeout=480" --max-time 540', run_in_background=true, timeout=600000)
```

The lifecycle:

1. Read `.claude/sidecar.json` to get the current port
2. Start drain as a **background** Bash task (`run_in_background: true`, `timeout: 600000`)
3. Session is free to do other work while the drain blocks at the sidecar
4. When events arrive, curl returns → task completes → `<task-notification>` delivered
5. Process events (route by `source` field)
6. Re-arm drain immediately — including after empty `[]` timeout returns

### Three-Layer Timeout

| Layer | Value | Purpose |
|-------|-------|---------|
| Server `?timeout=480` | 480s (8 min) | Handler exits cleanly, returns `[]` |
| curl `--max-time 540` | 540s (9 min) | Safety net if server hangs |
| CC `timeout=600000` | 600s (10 min) | Background task ceiling |

Each layer is a backstop for the one above. The server always closes the
connection before curl disconnects, preventing ghost handler threads from
consuming events. This is how `wait=true` achieves instant responsiveness
with zero wasted turns — at most one empty `[]` return every 8 minutes.

### Port Discovery

The sidecar port is dynamic. Always read it from `.claude/sidecar.json`:

```bash
PORT=$(python3 -c "import json; print(json.load(open('.claude/sidecar.json'))['port'])")
```

### Anti-Patterns (DO NOT DO THESE)

**1. source-register command for drain — CIRCULAR NESTING**

```
# WRONG — creates infinite loop
source-register.py command 'drain' curl /events?wait=true
```

Why this breaks: The drain output becomes a `command_completed` event in the
sidecar. That event needs ANOTHER drain to pick it up, which creates another
`command_completed` event, ad infinitum. The sidecar's `command` source type
is for one-shot blocking commands that produce a single result — not for the
drain itself.

**2. Foreground drain — BLOCKS CONVERSATION**

```
# WRONG — blocks until events arrive, agent can't do anything
Bash(command='curl -sf "http://localhost:PORT/events?wait=true"')
```

The entire point of `el` is to avoid blocking. A foreground drain defeats
the purpose — the agent sits idle waiting for curl to return instead of
doing useful work.

**3. Non-blocking drain in a loop — BURNS TOKENS**

```
# WRONG — polling, the thing el was built to eliminate
while true; do
  curl -sf "http://localhost:PORT/events?wait=false"
  sleep 5
done
```

This is just polling with extra steps. Use `wait=true` with a background
task instead.

### Single Consumer Rule

Use exactly ONE drain consumer per session. Multiple consumers compete for the
`picked_up` flag on events, causing missed events. The correct architecture is
one background drain task that feeds all event processing.

### After Sidecar Restart

When the sidecar restarts on a new port:

1. Read the new port from `.claude/sidecar.json`
2. Update any ngrok tunnels to point to the new port
3. Start a new background drain with the new port
4. Events from before restart are preserved in the DB (same file per project)

### Drain Script Example

```bash
#!/usr/bin/env bash
SIDECAR_JSON="$(pwd)/.claude/sidecar.json"
PORT=$(python3 -c "import json; print(json.load(open('$SIDECAR_JSON'))['port'])")
SIDECAR_URL="http://localhost:$PORT"
curl -sf "${SIDECAR_URL}/events?wait=true&timeout=480" --max-time 540
```
