#!/bin/bash
# context-sync — Watch for agent context file changes across a project.
#
# Monitors:
#   **/CLAUDE.md           — agent instructions anywhere
#   .claude/docs/*.md      — documentation
#   .claude/commands/*.md  — custom commands
#
# Args: [project-root]
#   If not provided, uses git root of cwd.
#
# Event Source Protocol:
#   Blocks until any context file is modified.
#   Outputs the changed file path to stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Determine project root
if [ $# -ge 1 ] && [ -d "$1" ]; then
  ROOT="$1"
else
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

exec "$SCRIPT_DIR/file-change.sh" \
  --root "$ROOT" \
  '**/CLAUDE.md' \
  '.claude/docs/*.md' \
  '.claude/commands/*.md'
