## 1.1.1

### Fixed
- **`/el:drain` type coercion bug** — Claude Opus 4.6 intermittently serializes `run_in_background` and `timeout` as strings instead of boolean/number, causing `InputValidationError`. The drain skill now has explicit type warnings and a `--bg` fallback mode that handles backgrounding internally via `nohup`, requiring no special Bash tool parameters. Fixes #1.

### Added
- **`drain.sh --bg` mode** — Self-backgrounding drain that forks the long-poll curl, writes events to `/tmp/el-drain-{hash}.out`, and returns immediately. No `run_in_background` or `timeout` parameters needed. Kills any previous drain automatically on re-arm.

## 1.1.0

### Added
- **`/el:drain` slash command** — Start the event drain as a background task with one command. No more manually constructing curl commands with wrong timeouts, ports, or foreground/background flags. The drain script reads the port from `.claude/sidecar.json` and uses the correct three-layer timeout stack (server 480s -> curl 540s -> CC task 600s).
- **`sources.d/drain.sh`** — New built-in event source that codifies the correct drain curl command.

### Upgrading
After updating, use `/el:drain` instead of manually running curl or event-listen.sh for the drain loop. The skill handles port discovery and timeout configuration automatically. Re-arm after each `<task-notification>` by running `/el:drain` again.
