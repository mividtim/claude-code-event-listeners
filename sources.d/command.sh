#!/bin/bash
# command — Run an arbitrary blocking command as an event source.
#
# Args: <command...>
#
# Event Source Protocol:
#   Runs the given command.
#   Blocks until the command exits.
#   Outputs whatever the command outputs to stdout.

set -euo pipefail

[ $# -eq 0 ] && { echo "Usage: command.sh <command...>" >&2; exit 1; }

eval "$@"
