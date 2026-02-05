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
