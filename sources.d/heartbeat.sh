#!/bin/bash
# heartbeat — Run multiple commands on an interval, fire when ANY output changes.
#
# Args: <interval-seconds> <cmd1> [-- <cmd2> [-- <cmd3> ...]]
#
# Event Source Protocol:
#   Takes an interval and one or more commands separated by --.
#   First run: captures baseline output for each command.
#   Each cycle: re-runs all commands, compares against previous output.
#   When ANY command's output changes, prints a JSON report and exits.
#
# Output format:
#   {"changed":["cmd_0"],"results":{"cmd_0":{"old":"...","new":"..."},...},"timestamp":"..."}

set -euo pipefail

INTERVAL="${1:?Usage: heartbeat.sh <interval-seconds> <cmd1> [-- <cmd2> ...]}"
shift
[ $# -eq 0 ] && { echo "Usage: heartbeat.sh <interval-seconds> <cmd1> [-- <cmd2> ...]" >&2; exit 1; }

# Parse commands separated by --
# Each "segment" between -- delimiters becomes one command string.
CMDS=()
current=""
for arg in "$@"; do
  if [ "$arg" = "--" ]; then
    if [ -n "$current" ]; then
      CMDS+=("$current")
    fi
    current=""
  else
    if [ -n "$current" ]; then
      current="$current $arg"
    else
      current="$arg"
    fi
  fi
done
# Don't forget the last command (no trailing --)
if [ -n "$current" ]; then
  CMDS+=("$current")
fi

if [ ${#CMDS[@]} -eq 0 ]; then
  echo "Error: no commands provided" >&2
  echo "Usage: heartbeat.sh <interval-seconds> <cmd1> [-- <cmd2> ...]" >&2
  exit 1
fi

NUM_CMDS=${#CMDS[@]}

# Capture baseline for all commands
PREV=()
for i in $(seq 0 $((NUM_CMDS - 1))); do
  PREV+=("$(eval "${CMDS[$i]}" 2>/dev/null || true)")
done

# Poll loop
while true; do
  sleep "$INTERVAL"

  CURR=()
  CHANGED=()
  for i in $(seq 0 $((NUM_CMDS - 1))); do
    output="$(eval "${CMDS[$i]}" 2>/dev/null || true)"
    CURR+=("$output")
    if [ "$output" != "${PREV[$i]}" ]; then
      CHANGED+=("$i")
    fi
  done

  if [ ${#CHANGED[@]} -gt 0 ]; then
    # Build JSON report using python3 for safe encoding
    python3 -c "
import json, sys, datetime

num_cmds = int(sys.argv[1])
changed_indices = sys.argv[2].split(',')
# Read old/new pairs from stdin, separated by null bytes
import sys as _sys
data = _sys.stdin.buffer.read().decode('utf-8', errors='replace')
parts = data.split('\x00')

results = {}
for i in range(num_cmds):
    old_val = parts[i * 2] if i * 2 < len(parts) else ''
    new_val = parts[i * 2 + 1] if i * 2 + 1 < len(parts) else ''
    results[f'cmd_{i}'] = {'old': old_val, 'new': new_val}

changed = [f'cmd_{i}' for i in changed_indices]
report = {
    'changed': changed,
    'results': results,
    'timestamp': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
}
print(json.dumps(report))
" "$NUM_CMDS" "$(IFS=,; echo "${CHANGED[*]}")" < <(
      # Feed old and new values separated by null bytes
      for i in $(seq 0 $((NUM_CMDS - 1))); do
        printf '%s\0' "${PREV[$i]}"
        printf '%s\0' "${CURR[$i]}"
      done
    )
    exit 0
  fi

  # Update previous values for next cycle
  for i in $(seq 0 $((NUM_CMDS - 1))); do
    PREV[$i]="${CURR[$i]}"
  done
done
