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
                    │  (event source script)    │
                    │                           │
  Event source ───► │  Blocks until event ───►  │ ──► Task completes
  (log, webhook,    │  Outputs event data       │      ↓
   CI, file, ...)   │  Exits cleanly            │     Claude gets notified
                    └──────────────────────────┘      reads output, reacts,
                                                      starts new listener
```

## Install

```bash
# From the marketplace
claude plugin marketplace add mividtim/claude-code-event-listeners
claude plugin install event-listeners

# Or load directly for a single session
claude --plugin-dir /path/to/claude-code-event-listeners
```

## Slash Commands

### Event Sources

| Command | What it does |
|---------|-------------|
| `/event-listeners:log-tail <file> [timeout] [max_lines]` | Tail a log file, return chunks of output |
| `/event-listeners:webhook [port]` | One-shot HTTP server on localhost |
| `/event-listeners:webhook-public [port] [name]` | One-shot HTTP server with automatic ngrok tunnel |
| `/event-listeners:ci-watch <run-id \| branch>` | Watch a GitHub Actions run until completion |
| `/event-listeners:pr-checks <pr-number>` | Watch all PR checks until they resolve |
| `/event-listeners:file-change <path>` | Watch a file for modifications |
| `/event-listeners:listen <command...>` | Run any blocking command as an event source |

### Management

| Command | What it does |
|---------|-------------|
| `/event-listeners:list` | Show all available sources (built-in + user) |
| `/event-listeners:register <script>` | Register a custom event source |
| `/event-listeners:unregister <name>` | Remove a user-installed source |

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
         registers URL with GitHub
... waits ...
<task-notification> → GitHub POSTed a review event → Claude reads and reacts
```

## Architecture: Pluggable Event Sources

The plugin is designed as a **platform**, not a monolith. Every event source —
including the built-in ones — is a standalone script in `sources.d/`.

```
event-listen.sh (dispatcher)
    │
    ├── looks up source type in:
    │   1. ~/.config/claude-event-listeners/sources.d/  (user, wins)
    │   2. <plugin>/sources.d/                          (built-in)
    │
    └── exec's the matching script with remaining args
```

Built-in sources are not special. They can be overridden, replaced, or used
as templates for new ones.

### The Event Source Protocol

An event source is any executable script that:

1. **Receives args** as `$@`
2. **Blocks** until an event occurs
3. **Outputs event data** to stdout
4. **Exits cleanly**

That's the entire contract. Here's a minimal example:

```bash
#!/bin/bash
# sources.d/port-ready.sh — Wait for a TCP port to open.
# Args: <host> <port>
set -euo pipefail
HOST="${1:?}" PORT="${2:?}"
while ! nc -z "$HOST" "$PORT" 2>/dev/null; do sleep 1; done
echo "PORT_OPEN=$HOST:$PORT"
```

### Managing Sources

Use the slash commands or the script directly:

```bash
/event-listeners:list                              # List all sources
/event-listeners:register ./my-custom-source.sh    # Register a new source
/event-listeners:unregister my-custom-source       # Remove a user source
```

User sources override built-ins with the same name — so you can replace
`log-tail` with your own implementation by registering a script named
`log-tail.sh`.

### Creating Community Event Sources

Write your script following the protocol. Publish it as:

1. **A standalone script** — users `event-listen.sh register` it
2. **A Claude Code plugin** — with a skill that references `event-listen.sh`
   and a post-install instruction to register the source
3. **A PR to this repo** — to make it a built-in

Example community sources we'd love to see:

- `postgres-changes.sh` — LISTEN/NOTIFY on a Postgres channel
- `slack-message.sh` — Watch a Slack channel for new messages
- `docker-health.sh` — Wait for a container health check to pass/fail
- `redis-subscribe.sh` — Subscribe to a Redis pub/sub channel
- `http-poll.sh` — Poll a URL until the response matches a condition
- `mqtt-subscribe.sh` — Subscribe to an MQTT topic
- `s3-object.sh` — Wait for an S3 object to appear

## Requirements

- **bash** (3.2+ for macOS, 4.0+ for Linux)
- **python3** (for webhook sources)
- **gh** CLI (for ci-watch and pr-checks) — [install](https://cli.github.com/)
- **ngrok** (for webhook-public only) — [install](https://ngrok.com/download)

## Contributing

The best way to contribute is to write new event sources. See the
[Event Source Protocol](#the-event-source-protocol) above and the scripts in
`sources.d/` for examples.

## License

MIT
