#!/bin/bash
# webhook-public — One-shot HTTP server exposed via ngrok tunnel.
#
# Line 1 output: WEBHOOK_URL=<public_url> (available immediately)
# Line 2+ output: event JSON when webhook fires
# Tunnel cleaned up on exit.
#
# If ngrok agent is already running (port 4040), creates a tunnel via API.
# Otherwise, starts ngrok directly.
#
# Args: [port=9999] [tunnel-name=claude-hook]
# Requires: ngrok (installed and authenticated)
#
# Event Source Protocol:
#   Outputs WEBHOOK_URL=... immediately.
#   Blocks until an HTTP request is received.
#   Outputs JSON: {"method": "...", "path": "...", "body": "..."}

set -euo pipefail

PORT="${1:-9999}"
TUNNEL_NAME="${2:-claude-hook}"
NGROK_API="${NGROK_API_URL:-http://127.0.0.1:4040}"
NGROK_MANAGED=false

cleanup() {
  if [ "$NGROK_MANAGED" = "true" ]; then
    kill "$NGROK_PID" 2>/dev/null || true
  else
    curl -s -X DELETE "$NGROK_API/api/tunnels/$TUNNEL_NAME" > /dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if curl -s --connect-timeout 2 "$NGROK_API/api/tunnels" > /dev/null 2>&1; then
  # ngrok agent already running — create tunnel via API
  RESPONSE=$(curl -s -X POST "$NGROK_API/api/tunnels" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$TUNNEL_NAME\",\"proto\":\"http\",\"addr\":\"$PORT\"}" 2>&1)
  if echo "$RESPONSE" | grep -q '"error_code"'; then
    RESPONSE=$(curl -s "$NGROK_API/api/tunnels/$TUNNEL_NAME" 2>/dev/null)
  fi
  PUBLIC_URL=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('public_url',''))" 2>/dev/null)
else
  # No ngrok running — start it for this tunnel
  ngrok http "$PORT" --log=false > /dev/null 2>&1 &
  NGROK_PID=$!
  NGROK_MANAGED=true
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
    if 'https' in t.get('public_url', ''):
        print(t['public_url']); break
else:
    print(tunnels[0]['public_url'] if tunnels else '')
" 2>/dev/null)
fi

if [ -z "$PUBLIC_URL" ]; then
  echo "ERROR: Failed to get ngrok public URL. Is ngrok installed and authenticated?" >&2
  exit 1
fi

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
