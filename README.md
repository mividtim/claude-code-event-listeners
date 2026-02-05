# claude-code-event-listeners

Event-driven background task listeners for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Replace polling with real event notifications.

## The Problem

Claude Code uses a request-response model. When waiting for CI, log output, webhooks, or file changes, the typical approach is **polling**: sleep, check, repeat. This burns turns, wastes tokens, and feels janky.

## The Insight

Claude Code's background task mechanism (`run_in_background: true`) provides genuine event-driven behavior:

1. A **background task** runs a blocking command that waits for an event
2. While waiting, **Claude does nothing** — no turns burned, no tokens spent
3. When the event occurs, the command exits and **Claude gets a `<task-notification>`**
4. Claude reads the output, reacts, and optionally re-subscribes

This isn't polling dressed up. The OS does the blocking. Claude only wakes up when something actually happens.

```
                    ┌──────────────────────────┐
                    │  Background Task          │
                    │  (event-listen.sh)        │
                    │                           │
  Event source ───► │  Blocks until event ───►  │ ──► Task completes
  (log, webhook,    │  Outputs event data       │      ↓
   CI, file, ...)   │  Exits cleanly            │     Claude gets notified
                    └──────────────────────────┘      reads output, reacts,
                                                      starts new listener
```

## Install

```bash
# Add the marketplace (one-time)
claude plugin marketplace add mividtim/claude-code-event-listeners

# Install
claude plugin install event-listeners

# Or load directly for a single session
claude --plugin-dir /path/to/claude-code-event-listeners
```

## Event Sources

| Skill | What it does |
|-------|-------------|
| `/event-listeners:log-tail` | Tail a log file, return chunks of output |
| `/event-listeners:webhook` | One-shot HTTP server on localhost |
| `/event-listeners:webhook-public` | One-shot HTTP server with automatic ngrok tunnel |
| `/event-listeners:ci-watch` | Watch a GitHub Actions run until completion |
| `/event-listeners:pr-checks` | Watch all PR checks until they resolve |
| `/event-listeners:file-change` | Watch a file for modifications |
| `/event-listeners:listen` | Run any blocking command as an event source |

## Quick Start

### Tail a log file

```
You: "tail the API server output"
Claude: starts background task → event-listen.sh log-tail api.log 10 100
... time passes, log lines arrive ...
<task-notification> → Claude reads the chunk, summarizes errors/warnings
Claude: starts another listener for the next chunk
```

### Wait for CI

```
You: "push and watch CI"
Claude: git push, then background task → event-listen.sh ci-watch my-branch
... minutes pass, Claude does nothing ...
<task-notification> → CI passed! (or failed → Claude investigates)
```

### Receive a webhook

```
You: "set up a webhook for GitHub review events"
Claude: background task → event-listen.sh webhook-public 9999 gh-review
         immediately reads URL: WEBHOOK_URL=https://xxxx.ngrok.app
         registers URL with GitHub as a webhook target
... waits ...
<task-notification> → GitHub POSTed a review event → Claude reads and reacts
```

### Multiple concurrent sources

Claude can run several background listeners at once. Whichever fires first gets handled:

```
event-listen.sh ci-watch my-branch          (background)
event-listen.sh log-tail api.log 30 200     (background)
event-listen.sh file-change config.json     (background)
```

## How It Works

### log-tail

Uses a named FIFO with `tail -f` and `read -t` for macOS compatibility. The per-line timeout means: "give me lines as fast as they come, but if nothing new arrives for N seconds, return what you have." Explicitly kills `tail` on exit (macOS `tail -f` ignores SIGPIPE).

### webhook / webhook-public

Python one-shot HTTP server. Accepts a single request, prints it as JSON, exits. The `webhook-public` variant auto-creates an ngrok tunnel (reuses existing ngrok agent if running, or starts one). Tunnel is cleaned up on exit via `trap`.

### ci-watch / pr-checks

Wraps `gh run watch` and `gh pr checks --watch` — these are already blocking commands that exit when done. The script adds branch-name lookup and error handling.

### file-change

Uses `fswatch` (macOS), `inotifywait` (Linux), or stat-polling fallback. Fires once per modification.

## Requirements

- **bash** (3.2+ for macOS, 4.0+ for Linux)
- **python3** (for webhook servers)
- **gh** CLI (for ci-watch and pr-checks) — [install](https://cli.github.com/)
- **ngrok** (for webhook-public only) — [install](https://ngrok.com/download)

## Contributing

PRs welcome. The best contributions would be new event source types:
- Slack message listener
- Docker container health/exit watcher
- Database change listener
- RSS/Atom feed watcher
- MQTT subscriber

## License

MIT
