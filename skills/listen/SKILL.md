---
description: Run any blocking command as an event source. The command runs until it exits, then fires an event with its output. Use as a generic escape hatch for event sources not covered by the other skills.
argument-hint: <command...>
allowed-tools: Bash, Read
---

Register a command source with the sidecar that fires when the command exits.

Parse `$ARGUMENTS` as the full command to run.

Generate a source name (e.g., `cmd-port-wait`, `cmd-docker-wait`).

```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' command 'SOURCE_NAME' $ARGUMENTS")
```

Events arrive through the sidecar drain — look for `source: "runtime:SOURCE_NAME"` with `type: "command_completed"`. The event `text` contains the command's stdout. Check `metadata.exit_code` for the exit status.

This is a one-shot source — it deactivates after the command exits.

Examples:
- Wait for a port to open: `/el:listen bash -c "while ! nc -z localhost 3000; do sleep 1; done; echo 'port 3000 open'"`
- Wait for a Docker container: `/el:listen docker wait my-container`
- Wait for a process to exit: `/el:listen tail --pid=12345 -f /dev/null`
