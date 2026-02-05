#!/bin/bash
# log-tail — Tail a log file and return a chunk.
#
# Uses a FIFO + explicit kill for macOS compatibility (tail -f ignores SIGPIPE).
# Per-line timeout: returns partial results if the log goes quiet.
#
# Args: <file> [per_line_timeout_secs=10] [max_lines=100]
#
# Event Source Protocol:
#   Blocks until max_lines collected or timeout elapses per-line.
#   Outputs collected log lines to stdout.

set -euo pipefail

FILE="${1:?Usage: log-tail.sh <file> [timeout=10] [max_lines=100]}"
TIMEOUT="${2:-10}"
MAX_LINES="${3:-100}"

FIFO=$(mktemp -u)
mkfifo "$FIFO"
trap 'rm -f "$FIFO"' EXIT

tail -f "$FILE" 2>/dev/null > "$FIFO" &
TAIL_PID=$!

COUNT=0
while IFS= read -r -t "$TIMEOUT" LINE; do
  echo "$LINE"
  COUNT=$((COUNT + 1))
  [ "$COUNT" -ge "$MAX_LINES" ] && break
done < "$FIFO"

kill "$TAIL_PID" 2>/dev/null
wait "$TAIL_PID" 2>/dev/null || true
