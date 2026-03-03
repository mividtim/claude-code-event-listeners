---
description: List all available event sources (built-in standalone and runtime sidecar sources).
allowed-tools: Bash
disable-model-invocation: true
---

List standalone sources and runtime sidecar sources:

```
Bash(command="${CLAUDE_PLUGIN_ROOT}/scripts/event-listen.sh list 2>/dev/null; echo ''; echo 'Runtime sources:'; python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-list.py' 2>/dev/null || echo '  (sidecar not running)'")
```
