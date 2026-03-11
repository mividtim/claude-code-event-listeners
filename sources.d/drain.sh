#!/bin/bash
# drain — Long-poll the sidecar for events.
#
# Usage:
#   drain.sh           — Foreground mode: blocks until events arrive, prints to stdout.
#   drain.sh --bg      — Background mode: forks curl, returns immediately, prints output path.
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
SIDECAR_URL="http://localhost:$PORT"

# --- Background mode ---
# Forks the long-poll into the background and returns immediately.
# The agent can check the output file periodically or use /el:poll to watch it.
if [ "${1:-}" = "--bg" ]; then
  # Deterministic output path per project (same hash the sidecar uses)
  PROJECT_HASH=$(python3 -c "import hashlib; print(hashlib.sha256('$(pwd)'.encode()).hexdigest()[:12])")
  DRAIN_OUT="/tmp/el-drain-${PROJECT_HASH}.out"
  DRAIN_PID="/tmp/el-drain-${PROJECT_HASH}.pid"

  # Kill any previous drain
  if [ -f "$DRAIN_PID" ]; then
    OLD_PID=$(cat "$DRAIN_PID" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
      kill "$OLD_PID" 2>/dev/null || true
    fi
  fi

  # Start the long-poll in background
  : > "$DRAIN_OUT"  # truncate
  nohup curl -sf "${SIDECAR_URL}/events?wait=true&timeout=480" --max-time 540 \
    -o "$DRAIN_OUT" > /dev/null 2>&1 &
  echo $! > "$DRAIN_PID"

  echo "Drain started in background (PID $(cat "$DRAIN_PID"))"
  echo "Output file: $DRAIN_OUT"
  echo "PID file: $DRAIN_PID"
  exit 0
fi

# --- Foreground mode (default) ---
curl -sf "${SIDECAR_URL}/events?wait=true&timeout=480" --max-time 540
