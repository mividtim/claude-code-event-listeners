#!/usr/bin/env python3
"""source-remove.py — Remove a runtime source from the sidecar.

Usage: source-remove.py <name>
"""
import json
import os
import sys
import urllib.request

if len(sys.argv) < 2:
    print("Usage: source-remove.py <name>", file=sys.stderr)
    sys.exit(1)

name = sys.argv[1]

sidecar_json = os.path.join(os.getcwd(), '.claude', 'sidecar.json')
if not os.path.exists(sidecar_json):
    print("Error: sidecar not running (no .claude/sidecar.json)", file=sys.stderr)
    sys.exit(1)

port = json.load(open(sidecar_json))['port']
body = json.dumps({"name": name}).encode()

try:
    req = urllib.request.Request(
        f'http://localhost:{port}/source', data=body, method='DELETE',
        headers={'Content-Type': 'application/json'}
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    if resp.get('ok'):
        print(resp.get('message', 'OK'))
    else:
        print(f"Error: {resp.get('message', 'unknown')}", file=sys.stderr)
        sys.exit(1)
except urllib.error.URLError as e:
    print(f"Error: sidecar not reachable ({e})", file=sys.stderr)
    sys.exit(1)
