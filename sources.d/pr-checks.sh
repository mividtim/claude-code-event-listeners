#!/bin/bash
# pr-checks — Watch all checks on a pull request until they resolve.
#
# Args: <pr-number> | <url> | <owner/repo#number>
# Requires: gh CLI (authenticated)
#
# Event Source Protocol:
#   Blocks until all PR checks complete.
#   Outputs check results to stdout.

set -euo pipefail

command -v gh &>/dev/null || { echo "ERROR: gh CLI not installed" >&2; exit 1; }

PR="${1:?Usage: pr-checks.sh <pr-number | url | owner/repo#number>}"

# Parse owner/repo#number format into -R flag
if [[ "$PR" =~ ^([^#]+)#([0-9]+)$ ]]; then
  REPO="${BASH_REMATCH[1]}"
  NUM="${BASH_REMATCH[2]}"
  gh pr checks "$NUM" -R "$REPO" --watch 2>&1 || true
else
  gh pr checks "$PR" --watch 2>&1 || true
fi
