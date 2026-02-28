#!/bin/bash
# command — Run an arbitrary blocking command as an event source.
#
# Args: <command...>
#
# Accepts either:
#   command.sh 'curl -sf "http://localhost:9999/events?wait=true&timeout=55"'
#     → single arg, evaluated as shell expression (supports pipes, redirects, &)
#   command.sh curl -sf http://localhost:9999/events?wait=true
#     → multiple args, executed directly (no shell re-parsing, safe for URLs)
#
# Event Source Protocol:
#   Runs the given command.
#   Blocks until the command exits.
#   Outputs whatever the command outputs to stdout.

set -euo pipefail

[ $# -eq 0 ] && { echo "Usage: command.sh <command...>" >&2; exit 1; }

if [ $# -eq 1 ]; then
  # Single arg: treat as shell expression (eval for pipes, redirects, etc.)
  eval "$1"
else
  # Multiple args: execute directly (no re-parsing, safe for URLs with &)
  "$@"
fi
