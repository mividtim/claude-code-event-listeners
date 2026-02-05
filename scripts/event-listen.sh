#!/bin/bash
# event-listen.sh — Event listener for Claude Code background tasks.
#
# Each source type is a blocking command that outputs event data and exits.
# Run as: Bash(command="<plugin>/scripts/event-listen.sh <type> [args...]", run_in_background=true)
#
# When the background task completes, Claude gets a <task-notification>,
# reads the output (= the event payload), processes it, and optionally
# re-subscribes for the next event.
#
# Part of the claude-code-event-listeners plugin.
# https://github.com/mividtim/claude-code-event-listeners

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NGROK_API="${NGROK_API_URL:-http://127.0.0.1:4040}"

usage() {
  cat <<EOF
Usage: event-listen.sh <type> [args...]

Event source types:
  log-tail <file> [timeout=10] [max_lines=100]   Tail a log file, return chunks
  webhook [port=9999]                             One-shot HTTP server (localhost)
  webhook-public [port=9999] [name=claude-hook]   One-shot HTTP server via ngrok
  file-change <path>                              Watch for file modification
  ci-watch <run-id | branch>                      Watch GitHub Actions run
  pr-checks <pr-number>                           Watch PR checks
  command <cmd...>                                Run arbitrary blocking command
EOF
  exit 1
}

SOURCE_TYPE="${1:-}"
[ -z "$SOURCE_TYPE" ] && usage
shift

case "$SOURCE_TYPE" in
  log-tail)
    # Tail a log file and return a chunk.
    # Uses a FIFO + explicit kill for macOS compatibility (tail -f ignores SIGPIPE).
    # Per-line timeout: returns partial results if log goes quiet.
    # Args: <file> [per_line_timeout_secs=10] [max_lines=100]
    FILE="${1:?Missing file path}"
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
    ;;

  webhook)
    # One-shot HTTP server. Accepts a single request, prints it as JSON, exits.
    # Args: [port=9999]
    PORT="${1:-9999}"
    python3 - "$PORT" << 'PYEOF'
import json, os, sys
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode() if length else ''
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
        event = {
            'method': self.command,
            'path': self.path,
            'body': body
        }
        print(json.dumps(event), flush=True)
        os._exit(0)
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle
    def log_message(self, *a): pass

port = int(sys.argv[1])
HTTPServer(('127.0.0.1', port), Handler).serve_forever()
PYEOF
    ;;

  webhook-public)
    # One-shot HTTP server exposed via ngrok tunnel.
    # Output line 1: WEBHOOK_URL=<public_url> (available immediately via non-blocking TaskOutput)
    # Output line 2+: event JSON when webhook fires
    # Cleans up tunnel/process on exit.
    # Args: [port=9999] [tunnel-name=claude-hook]
    PORT="${1:-9999}"
    TUNNEL_NAME="${2:-claude-hook}"
    NGROK_MANAGED=false

    cleanup() {
      if [ "$NGROK_MANAGED" = "true" ]; then
        # We started ngrok — kill it
        kill "$NGROK_PID" 2>/dev/null || true
      else
        # We used existing ngrok agent — just remove our tunnel
        curl -s -X DELETE "$NGROK_API/api/tunnels/$TUNNEL_NAME" > /dev/null 2>&1 || true
      fi
    }
    trap cleanup EXIT

    if curl -s --connect-timeout 2 "$NGROK_API/api/tunnels" > /dev/null 2>&1; then
      # ngrok agent already running — create tunnel via API
      RESPONSE=$(curl -s -X POST "$NGROK_API/api/tunnels" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$TUNNEL_NAME\",\"proto\":\"http\",\"addr\":\"$PORT\"}" 2>&1)
      # Check if tunnel already exists
      if echo "$RESPONSE" | grep -q '"error_code"'; then
        RESPONSE=$(curl -s "$NGROK_API/api/tunnels/$TUNNEL_NAME" 2>/dev/null)
      fi
      PUBLIC_URL=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('public_url',''))" 2>/dev/null)
    else
      # No ngrok running — start it for this tunnel
      ngrok http "$PORT" --log=false > /dev/null 2>&1 &
      NGROK_PID=$!
      NGROK_MANAGED=true
      # Wait for ngrok to start (up to 10 seconds)
      for i in $(seq 1 20); do
        if curl -s --connect-timeout 1 "$NGROK_API/api/tunnels" > /dev/null 2>&1; then
          break
        fi
        sleep 0.5
      done
      PUBLIC_URL=$(curl -s "$NGROK_API/api/tunnels" | python3 -c "
import sys, json
tunnels = json.load(sys.stdin).get('tunnels', [])
for t in tunnels:
    if t.get('proto') == 'https' or 'https' in t.get('public_url', ''):
        print(t['public_url'])
        break
else:
    print(tunnels[0]['public_url'] if tunnels else '')
" 2>/dev/null)
    fi

    if [ -z "$PUBLIC_URL" ]; then
      echo "ERROR: Failed to get ngrok public URL. Is ngrok installed and authenticated?" >&2
      exit 1
    fi

    # Output URL as first line
    echo "WEBHOOK_URL=$PUBLIC_URL"

    # Block until a request arrives
    python3 - "$PORT" << 'PYEOF'
import json, os, sys
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode() if length else ''
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
        event = {
            'method': self.command,
            'path': self.path,
            'body': body
        }
        print(json.dumps(event), flush=True)
        os._exit(0)
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle
    def log_message(self, *a): pass

port = int(sys.argv[1])
HTTPServer(('127.0.0.1', port), Handler).serve_forever()
PYEOF
    ;;

  file-change)
    # Block until a file is modified, then print the filename.
    # Uses fswatch (macOS), inotifywait (Linux), or stat-polling fallback.
    # Args: <path>
    TARGET="${1:?Missing file path}"
    if command -v fswatch &>/dev/null; then
      fswatch -1 "$TARGET"
    elif command -v inotifywait &>/dev/null; then
      inotifywait -e modify -q "$TARGET" 2>/dev/null
    else
      # Fallback: poll stat every second
      if stat -f %m "$TARGET" > /dev/null 2>&1; then
        STAT_CMD="stat -f %m"   # macOS
      else
        STAT_CMD="stat -c %Y"   # Linux
      fi
      INITIAL=$($STAT_CMD "$TARGET" 2>/dev/null)
      while true; do
        CURRENT=$($STAT_CMD "$TARGET" 2>/dev/null)
        [ "$CURRENT" != "$INITIAL" ] && break
        sleep 1
      done
      echo "$TARGET"
    fi
    ;;

  ci-watch)
    # Block until a GitHub Actions run completes. Outputs result.
    # Args: <run-id | branch>
    command -v gh &>/dev/null || { echo "ERROR: gh CLI not installed" >&2; exit 1; }
    ARG="${1:?Missing run ID or branch name}"
    if [[ "$ARG" =~ ^[0-9]+$ ]]; then
      gh run watch "$ARG" --exit-status 2>&1 || true
    else
      RUN_ID=$(gh run list --branch "$ARG" --limit 1 --json databaseId -q '.[0].databaseId')
      if [ -z "$RUN_ID" ]; then
        echo "No runs found for branch: $ARG"
        exit 1
      fi
      gh run watch "$RUN_ID" --exit-status 2>&1 || true
    fi
    ;;

  pr-checks)
    # Block until all PR checks complete.
    # Args: <pr-number>
    command -v gh &>/dev/null || { echo "ERROR: gh CLI not installed" >&2; exit 1; }
    PR="${1:?Missing PR number}"
    gh pr checks "$PR" --watch 2>&1 || true
    ;;

  command)
    # Run an arbitrary blocking command.
    # Args: <command...>
    eval "$@"
    ;;

  -h|--help|help)
    usage
    ;;

  *)
    echo "Unknown source type: $SOURCE_TYPE" >&2
    usage
    ;;
esac
