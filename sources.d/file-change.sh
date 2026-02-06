#!/bin/bash
# file-change — Watch file(s) for modifications.
#
# Uses fswatch (macOS), inotifywait (Linux), or stat-polling fallback.
#
# Args: [--root <dir>] <path-or-glob> [path-or-glob...]
#
# Options:
#   --root <dir>  Watch from this directory instead of cwd. Useful in git
#                 worktrees to watch the main project root so changes from
#                 other worktrees are visible.
#
# Supports glob patterns:
#   **/CLAUDE.md        — any CLAUDE.md in the tree
#   .claude/docs/*.md   — all .md files in .claude/docs/
#   src/**/*.ts         — all .ts files under src/
#
# A single direct file path works as before (backwards compatible).
#
# Event Source Protocol:
#   Blocks until any matching file is modified.
#   Outputs the changed file path to stdout.

set -euo pipefail

# --- Parse --root flag ---
WATCH_ROOT="$(pwd)"
if [ "${1:-}" = "--root" ]; then
  WATCH_ROOT="${2:?--root requires a directory}"
  shift 2
fi

[ $# -eq 0 ] && {
  echo "Usage: file-change.sh [--root <dir>] <path-or-glob> [...]" >&2
  exit 1
}

# Convert a shell glob pattern to an extended regex.
# Handles **, *, ? wildcards and escapes regex metacharacters.
# Uses placeholders so regex output from ** doesn't get re-processed by *.
glob_to_regex() {
  echo "$1" | sed \
    -e 's/\./\\./g' \
    -e 's/\*\*\//___GS___/g' \
    -e 's/\*\*/___G___/g' \
    -e 's/\*/[^\/]*/g' \
    -e 's/?/[^\/]/g' \
    -e 's/___GS___/(.*\/)?/g' \
    -e 's/___G___/.*/g'
}

is_glob() {
  case "$1" in
    *'*'*|*'?'*|*'['*) return 0 ;;
    *) return 1 ;;
  esac
}

# Classify arguments into direct files vs glob patterns.
DIRECT_FILES=()
GLOB_PATTERNS=()
for arg in "$@"; do
  if is_glob "$arg"; then
    GLOB_PATTERNS+=("$arg")
  else
    DIRECT_FILES+=("$arg")
  fi
done
HAS_GLOBS=false
[ ${#GLOB_PATTERNS[@]} -gt 0 ] && HAS_GLOBS=true

# --- fswatch (macOS) ---
if command -v fswatch &>/dev/null; then
  if [ "$HAS_GLOBS" = false ] && [ ${#DIRECT_FILES[@]} -eq 1 ]; then
    # Single direct file — original fast path.
    exec fswatch -1 "${DIRECT_FILES[0]}"
  fi
  # -E enables extended regex for patterns like (.*/)?
  FSWATCH_ARGS=(-1 -E)
  for arg in "$@"; do
    if is_glob "$arg"; then
      FSWATCH_ARGS+=(--include "$(glob_to_regex "$arg")$")
    else
      # Resolve to absolute path for precise matching.
      if [ -e "$arg" ]; then
        abs="$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")"
      else
        abs="$WATCH_ROOT/$arg"
      fi
      FSWATCH_ARGS+=(--include "$(echo "$abs" | sed 's/\./\\./g')$")
    fi
  done
  # Default-deny: only report events matching an include filter.
  FSWATCH_ARGS+=(--exclude '.*')
  exec fswatch "${FSWATCH_ARGS[@]}" "$WATCH_ROOT"

# --- inotifywait (Linux) ---
elif command -v inotifywait &>/dev/null; then
  if [ "$HAS_GLOBS" = false ] && [ ${#DIRECT_FILES[@]} -eq 1 ]; then
    inotifywait -e modify -q "${DIRECT_FILES[0]}" 2>/dev/null
    exit $?
  fi
  REGEX_PARTS=()
  for arg in "$@"; do
    if is_glob "$arg"; then
      REGEX_PARTS+=("$(glob_to_regex "$arg")$")
    else
      if [ -e "$arg" ]; then
        abs="$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")"
      else
        abs="$WATCH_ROOT/$arg"
      fi
      REGEX_PARTS+=("$(echo "$abs" | sed 's/\./\\./g')$")
    fi
  done
  COMBINED=$(printf '%s|' "${REGEX_PARTS[@]}")
  COMBINED="${COMBINED%|}"
  inotifywait -r -e modify --include "$COMBINED" -q "$WATCH_ROOT" 2>/dev/null

# --- Fallback: stat polling ---
else
  if [ "$HAS_GLOBS" = true ]; then
    echo "Glob patterns require fswatch (brew install fswatch) or inotifywait" >&2
    exit 1
  fi
  if stat -f %m /dev/null >/dev/null 2>&1; then
    STAT_FMT="-f %m"
  else
    STAT_FMT="-c %Y"
  fi
  # Record initial mtimes for all direct files.
  INITIALS=()
  for f in "${DIRECT_FILES[@]}"; do
    INITIALS+=("$(stat $STAT_FMT "$f" 2>/dev/null || echo 0)")
  done
  while true; do
    for i in "${!DIRECT_FILES[@]}"; do
      CURRENT=$(stat $STAT_FMT "${DIRECT_FILES[$i]}" 2>/dev/null || echo 0)
      if [ "$CURRENT" != "${INITIALS[$i]}" ]; then
        echo "${DIRECT_FILES[$i]}"
        exit 0
      fi
    done
    sleep 1
  done
fi
