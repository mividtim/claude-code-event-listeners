#!/bin/bash
# drain — Long-poll the sidecar for events.
#
# Args: (none)
#
# Reads the sidecar port from .claude/sidecar.json (relative to working directory).
# Long-polls once with the correct three-layer timeout stack, returns the result,
# and exits. The agent re-arms by calling /el:drain again after each task-notification.
#
# Event Source Protocol:
#   Blocks until events arrive or the server timeout (480s) expires.
#   Outputs the events JSON (or [] on timeout).
#   Exits cleanly.

set -euo pipefail

SIDECAR_JSON="$(pwd)/.claude/sidecar.json"

if [ ! -f "$SIDECAR_JSON" ]; then
  echo "Error: $SIDECAR_JSON not found. Is the sidecar running? Start it with /el:sidecar start" >&2
  exit 1
fi

PORT=$(python3 -c "import json; print(json.load(open('$SIDECAR_JSON'))['port'])")

curl -sf "http://localhost:$PORT/events?wait=true&timeout=480" --max-time 540
