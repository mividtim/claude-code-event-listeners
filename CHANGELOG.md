## 1.1.1

### Fixed
- **Removed polling fallback from drain skill.** If `run_in_background` fails due to parameter type coercion, that's a model bug — not something the skill should work around. Polling is the anti-pattern el exists to eliminate.

## 1.1.0

### Added
- **`/el:drain` slash command** — Start the event drain as a background task with one command. No more manually constructing curl commands with wrong timeouts, ports, or foreground/background flags. The drain script reads the port from `.claude/sidecar.json` and uses the correct three-layer timeout stack (server 480s -> curl 540s -> CC task 600s).
- **`sources.d/drain.sh`** — New built-in event source that codifies the correct drain curl command.

### Upgrading
After updating, use `/el:drain` instead of manually running curl or event-listen.sh for the drain loop. The skill handles port discovery and timeout configuration automatically. Re-arm after each `<task-notification>` by running `/el:drain` again.
