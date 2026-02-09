#!/bin/bash
# event-listen.sh — Generic event listener dispatcher for Claude Code.
#
# Looks up event source scripts in (priority order):
#   1. User sources:    ~/.config/claude-event-listeners/sources.d/<type>.sh
#   2. Built-in sources: <plugin-root>/sources.d/<type>.sh
#   3. Community plugins: ~/.claude/plugins/cache/*/*/sources.d/<type>.sh
#
# User sources take priority, so you can override any built-in or community source.
# Community plugins are auto-discovered — install a plugin with sources.d/ and
# its sources become available immediately. No registration needed.
#
# Event Source Protocol:
#   - Receive args as $@
#   - Block until an event occurs
#   - Output event data to stdout
#   - Exit cleanly
#
# Part of the claude-code-event-listeners plugin.
# https://github.com/mividtim/claude-code-event-listeners

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_SOURCES="${CLAUDE_EVENT_LISTENERS_DIR:-$HOME/.config/claude-event-listeners}/sources.d"
PLUGIN_SOURCES="$PLUGIN_ROOT/sources.d"
PLUGIN_CACHE="${CLAUDE_PLUGINS_CACHE:-$HOME/.claude/plugins/cache}"

# Find a source script by name. User > built-in > community plugin.
find_source() {
  local name="$1"
  if [ -x "$USER_SOURCES/$name.sh" ]; then
    echo "$USER_SOURCES/$name.sh"
  elif [ -x "$PLUGIN_SOURCES/$name.sh" ]; then
    echo "$PLUGIN_SOURCES/$name.sh"
  elif [ -d "$PLUGIN_CACHE" ]; then
    # Scan installed community plugins for sources.d/
    for f in "$PLUGIN_CACHE"/*/*/sources.d/"$name.sh"; do
      [ -x "$f" ] || continue
      # Skip our own plugin directory
      local plugin_dir
      plugin_dir="$(cd "$(dirname "$f")/.." && pwd)"
      [ "$plugin_dir" = "$PLUGIN_ROOT" ] && continue
      echo "$f"
      return
    done
  fi
}

# Check if a name is in the seen array.
_is_seen() {
  local name="$1"; shift
  for s in "$@"; do [ "$s" = "$name" ] && return 0; done
  return 1
}

# List all available sources (deduplicated, user overrides noted).
list_sources() {
  local seen=()

  # 1. User-registered sources (highest priority)
  if [ -d "$USER_SOURCES" ]; then
    for f in "$USER_SOURCES"/*.sh; do
      [ -x "$f" ] || continue
      local name=$(basename "$f" .sh)
      seen+=("$name")
      echo "  $name (user)"
    done
  fi

  # 2. Built-in sources
  for f in "$PLUGIN_SOURCES"/*.sh; do
    [ -x "$f" ] || continue
    local name=$(basename "$f" .sh)
    if _is_seen "$name" "${seen[@]+"${seen[@]}"}"; then
      echo "  $name (built-in, overridden by user)"
    else
      seen+=("$name")
      echo "  $name (built-in)"
    fi
  done

  # 3. Community plugin sources (auto-discovered)
  if [ -d "$PLUGIN_CACHE" ]; then
    for f in "$PLUGIN_CACHE"/*/*/sources.d/*.sh; do
      [ -x "$f" ] || continue
      local plugin_dir
      plugin_dir="$(cd "$(dirname "$f")/.." && pwd)"
      [ "$plugin_dir" = "$PLUGIN_ROOT" ] && continue
      local name=$(basename "$f" .sh)
      if _is_seen "$name" "${seen[@]+"${seen[@]}"}"; then
        continue  # Already provided by user or built-in
      fi
      seen+=("$name")
      echo "  $name (community plugin)"
    done
  fi
}

usage() {
  cat <<EOF
Usage: event-listen.sh <source-type> [args...]
       event-listen.sh list
       event-listen.sh register <path-to-script>

Dispatcher for event source scripts. Looks up <source-type>.sh in:
  1. $USER_SOURCES/ (user, takes priority)
  2. $PLUGIN_SOURCES/ (built-in)
  3. Installed community plugins with sources.d/

Available sources:
$(list_sources)

To create a new source, write a script that:
  - Receives args as \$@
  - Blocks until an event occurs
  - Outputs event data to stdout
  - Exits cleanly
Then register it or drop it in the sources directory.
EOF
  exit "${1:-0}"
}

SOURCE_TYPE="${1:-}"
[ -z "$SOURCE_TYPE" ] && usage 1
shift

case "$SOURCE_TYPE" in
  list)
    echo "Available event sources:"
    list_sources
    ;;
  register)
    SCRIPT="${1:?Usage: event-listen.sh register <path-to-script>}"
    if [ ! -f "$SCRIPT" ]; then
      echo "Error: $SCRIPT not found" >&2
      exit 1
    fi
    if [ ! -x "$SCRIPT" ]; then
      echo "Error: $SCRIPT is not executable (chmod +x it first)" >&2
      exit 1
    fi
    mkdir -p "$USER_SOURCES"
    SCRIPT="$(cd "$(dirname "$SCRIPT")" && pwd)/$(basename "$SCRIPT")"
    NAME=$(basename "$SCRIPT" .sh)
    ln -sf "$SCRIPT" "$USER_SOURCES/$NAME.sh"
    echo "Registered source '$NAME' → $USER_SOURCES/$NAME.sh (symlink)"
    ;;
  unregister)
    NAME="${1:?Usage: event-listen.sh unregister <source-name>}"
    TARGET="$USER_SOURCES/$NAME.sh"
    if [ ! -f "$TARGET" ]; then
      echo "Error: no user source '$NAME' registered" >&2
      exit 1
    fi
    rm "$TARGET"
    echo "Unregistered source '$NAME'"
    ;;
  -h|--help|help)
    usage 0
    ;;
  *)
    # Look up the source script and run it
    SOURCE_SCRIPT=$(find_source "$SOURCE_TYPE")
    if [ -z "$SOURCE_SCRIPT" ]; then
      echo "Unknown source type: $SOURCE_TYPE" >&2
      echo "" >&2
      echo "Available sources:" >&2
      list_sources >&2
      echo "" >&2
      echo "To add a new source: event-listen.sh register <path-to-script>" >&2
      exit 1
    fi
    exec "$SOURCE_SCRIPT" "$@"
    ;;
esac
