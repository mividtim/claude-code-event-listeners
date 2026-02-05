#!/bin/bash
# file-change — Watch a file for modifications.
#
# Uses fswatch (macOS), inotifywait (Linux), or stat-polling fallback.
#
# Args: <path>
#
# Event Source Protocol:
#   Blocks until the file is modified.
#   Outputs the file path to stdout.

set -euo pipefail

TARGET="${1:?Usage: file-change.sh <path>}"

if command -v fswatch &>/dev/null; then
  fswatch -1 "$TARGET"
elif command -v inotifywait &>/dev/null; then
  inotifywait -e modify -q "$TARGET" 2>/dev/null
else
  # Fallback: poll stat every second
  if stat -f %m "$TARGET" > /dev/null 2>&1; then
    STAT_CMD="stat -f %m"
  else
    STAT_CMD="stat -c %Y"
  fi
  INITIAL=$($STAT_CMD "$TARGET" 2>/dev/null)
  while true; do
    CURRENT=$($STAT_CMD "$TARGET" 2>/dev/null)
    [ "$CURRENT" != "$INITIAL" ] && break
    sleep 1
  done
  echo "$TARGET"
fi
