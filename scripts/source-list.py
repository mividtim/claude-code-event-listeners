#!/usr/bin/env python3
"""source-list.py — List runtime sources from the sidecar."""
import json
import os
import sys
import urllib.request

sidecar_json = os.path.join(os.getcwd(), '.claude', 'sidecar.json')
if not os.path.exists(sidecar_json):
    print("Error: sidecar not running (no .claude/sidecar.json)", file=sys.stderr)
    sys.exit(1)

port = json.load(open(sidecar_json))['port']

try:
    req = urllib.request.Request(f'http://localhost:{port}/sources')
    sources = json.loads(urllib.request.urlopen(req).read())
    if not sources:
        print("No runtime sources registered")
    else:
        for s in sources:
            status = "active" if s.get('active') else "inactive"
            stype = s.get('type', '?')
            name = s.get('name', '?')
            print(f"  {name:24s} {stype:10s} [{status}]")
except urllib.error.URLError as e:
    print(f"Error: sidecar not reachable ({e})", file=sys.stderr)
    sys.exit(1)
