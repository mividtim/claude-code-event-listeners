#!/bin/bash
# poll — Run a command periodically, fire when output changes.
#
# Args: <interval-seconds> <command...>
#
# Event Source Protocol:
#   Runs the given command every <interval> seconds.
#   Compares output against the previous run.
#   When output changes, prints the new output to stdout and exits.
#   On first run, captures baseline silently (no event fired).

set -euo pipefail

INTERVAL="${1:?Usage: poll.sh <interval-seconds> <command...>}"
shift
[ $# -eq 0 ] && { echo "Usage: poll.sh <interval-seconds> <command...>" >&2; exit 1; }

# Capture baseline
PREV=$(eval "$@" 2>/dev/null) || true

while true; do
  sleep "$INTERVAL"
  CURR=$(eval "$@" 2>/dev/null) || true
  if [ "$CURR" != "$PREV" ]; then
    echo "$CURR"
    exit 0
  fi
  PREV="$CURR"
done
