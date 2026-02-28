---
description: Run any blocking command as an event source. The command blocks until something happens, outputs the event data, and exits. Use this as a generic escape hatch for event sources not covered by the other skills.
argument-hint: <command...>
allowed-tools: Bash, Read
---

Run an arbitrary blocking command as a background event listener:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh command $ARGUMENTS", run_in_background=true)
```

The command should:
1. Block until something interesting happens
2. Output the event data to stdout
3. Exit

When the `<task-notification>` arrives, the command has exited. Read the output to get the event payload.

IMPORTANT: For URLs containing `&` (like `?wait=true&timeout=55`), wrap the
entire command in single quotes so the shell doesn't split on `&`:
```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh command 'curl -sf \"http://localhost:9999/events?wait=true&timeout=55\"'", run_in_background=true)
```

Examples:
- Wait for a port to open: `event-listen.sh command "while ! nc -z localhost 3000; do sleep 1; done; echo 'port 3000 open'"`
- Wait for a Docker container: `event-listen.sh command "docker wait my-container"`
- Wait for a process to exit: `event-listen.sh command "tail --pid=12345 -f /dev/null; echo 'process exited'"`
- Long-poll sidecar: `event-listen.sh command 'curl -sf "http://localhost:9999/events?wait=true&timeout=55"'`
