---
description: Start, stop, or check status of the el-sidecar event hub. The sidecar discovers installed plugins with sidecar/plugin.py, auto-assigns a port, and writes metadata to .claude/sidecar.json.
argument-hint: start [port] | stop | status
allowed-tools: Bash, Read
---

Manage the el-sidecar event hub. Parse `$ARGUMENTS` to determine the subcommand.

## Subcommands

### `start [port]`

Start the sidecar in the background. Optional port argument overrides auto-assign.

```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/el-sidecar.py' ${PORT_ARG} --project-root '${PROJECT_ROOT}' 2>&1 &
PID=$!
sleep 1
if kill -0 $PID 2>/dev/null; then
  echo 'Sidecar started (PID: '$PID')'
  cat '${PROJECT_ROOT}/.claude/sidecar.json' 2>/dev/null || echo 'Waiting for metadata...'
else
  echo 'Sidecar failed to start'
fi", run_in_background=true)
```

Where:
- `${PORT_ARG}` is the port from `$ARGUMENTS` (if given), or omitted for auto-assign
- `${PROJECT_ROOT}` is the current working directory

After starting, read `.claude/sidecar.json` to confirm the port and PID.

### `stop`

Stop the running sidecar by reading the PID from `.claude/sidecar.json`.

```
Bash(command="SIDECAR_JSON='$(pwd)/.claude/sidecar.json'
if [ ! -f \"$SIDECAR_JSON\" ]; then
  echo 'No sidecar.json found — sidecar may not be running'
  exit 0
fi
PID=$(python3 -c \"import json; print(json.load(open('$SIDECAR_JSON'))['pid'])\")
if kill -0 $PID 2>/dev/null; then
  kill $PID
  echo \"Sidecar stopped (PID: $PID)\"
else
  echo \"Sidecar PID $PID not running — cleaning up\"
fi
rm -f \"$SIDECAR_JSON\"")
```

### `status`

Check if the sidecar is running and show its health info.

```
Bash(command="SIDECAR_JSON='$(pwd)/.claude/sidecar.json'
if [ ! -f \"$SIDECAR_JSON\" ]; then
  echo 'No sidecar.json found — sidecar is not running'
  exit 0
fi
PORT=$(python3 -c \"import json; print(json.load(open('$SIDECAR_JSON'))['port'])\")
PID=$(python3 -c \"import json; print(json.load(open('$SIDECAR_JSON'))['pid'])\")
if ! kill -0 $PID 2>/dev/null; then
  echo \"Sidecar PID $PID not running (stale metadata)\"
  exit 1
fi
echo \"Sidecar running on port $PORT (PID: $PID)\"
curl -sf http://localhost:$PORT/health | python3 -m json.tool")
```

## Notes

- The sidecar auto-discovers plugins from `~/.claude/plugins/installed_plugins.json`
- Plugins with `sidecar/plugin.py` containing `register(api)` are loaded automatically
- Port 0 (default) lets the OS auto-assign — actual port is in `.claude/sidecar.json`
- DB is per-project: `/tmp/el-sidecar-{hash}.db`
- Use `wait=true` on `/events` for blocking drain (never returns empty)
- **Do not add `&` to commands when using `run_in_background=true`.**
