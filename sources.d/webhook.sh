#!/bin/bash
# webhook — One-shot HTTP server on localhost.
#
# Accepts a single HTTP request (any method), prints it as JSON, exits.
#
# Args: [port=9999]
#
# Event Source Protocol:
#   Blocks until an HTTP request is received.
#   Outputs JSON: {"method": "...", "path": "...", "body": "..."}

set -euo pipefail

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
