#!/bin/bash
# context-sync — Watch for agent context file changes across a project.
#
# Only watches TRUSTED locations to prevent prompt injection from dependencies
# (e.g., a malicious node_modules/pkg/CLAUDE.md).
#
# Monitors:
#   CLAUDE.md              — root-level agent instructions
#   */CLAUDE.md            — immediate subdirectories only (submodules, worktrees)
#   worktrees/*/CLAUDE.md  — worktree-specific instructions
#   .claude/docs/*.md      — documentation
#   .claude/commands/*.md  — custom commands
#
# Does NOT monitor:
#   **/CLAUDE.md           — would include node_modules, vendor, etc.
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
  'CLAUDE.md' \
  '*/CLAUDE.md' \
  'worktrees/*/CLAUDE.md' \
  '.claude/docs/*.md' \
  '.claude/commands/*.md'
