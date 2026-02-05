---
description: Unregister a user-installed event source.
argument-hint: <source-name>
allowed-tools: Bash
disable-model-invocation: true
---

Unregister the event source `$ARGUMENTS`:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh unregister $ARGUMENTS")
```

After unregistering, confirm by listing remaining sources:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh list")
```
