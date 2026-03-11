## 1.1.2

### Fixed
- **Drain skill no longer triggers parameter type coercion errors.** Two changes: (1) replaced the pseudo-code `Bash(command="...", run_in_background=true, timeout=600000)` invocation syntax with natural-language instructions that the model constructs tool calls from rather than copying verbatim, and (2) dropped the `timeout` parameter entirely — curl's `--max-time 540` already guarantees the process exits, making the Bash-level timeout redundant. This eliminates both failure points (boolean and number type coercion) without introducing polling.

## 1.1.1

### Fixed
- **Removed polling fallback from drain skill.** If `run_in_background` fails due to parameter type coercion, that's a model bug — not something the skill should work around. Polling is the anti-pattern el exists to eliminate.

## 1.1.0

### Added
- **`/el:drain` slash command** — Start the event drain as a background task with one command. No more manually constructing curl commands with wrong timeouts, ports, or foreground/background flags. The drain script reads the port from `.claude/sidecar.json` and uses the correct three-layer timeout stack (server 480s -> curl 540s -> CC task 600s).
- **`sources.d/drain.sh`** — New built-in event source that codifies the correct drain curl command.

### Upgrading
After updating, use `/el:drain` instead of manually running curl or event-listen.sh for the drain loop. The skill handles port discovery and timeout configuration automatically. Re-arm after each `<task-notification>` by running `/el:drain` again.
