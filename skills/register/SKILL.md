---
description: Register a custom event source script so it can be used with event-listen.sh.
argument-hint: <path-to-script>
allowed-tools: Bash
disable-model-invocation: true
---

Register the event source script at `$ARGUMENTS`:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh register $ARGUMENTS")
```

After registering, confirm by listing all sources:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh list")
```
