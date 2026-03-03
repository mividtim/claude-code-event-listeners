#!/usr/bin/env python3
"""source-register.py — Register a runtime source with the sidecar.

Supports both CLI args (type-specific) and piped JSON.

CLI usage:
    source-register.py poll <name> <interval> <command...>
    source-register.py heartbeat <name> [interval]
    source-register.py command <name> <command...>
    source-register.py watch <name> [--root dir] <path> [path...]
    source-register.py tail <name> <file> [timeout] [max_lines]
    source-register.py ci <name> <run-id-or-branch>
    source-register.py webhook <name> <path>

JSON usage:
    echo '{"name":"x","type":"poll",...}' | source-register.py
"""
import json
import os
import sys
import urllib.request


def usage():
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def build_source(args):
    if len(args) < 2:
        usage()

    stype = args[0]
    name = args[1]
    rest = args[2:]

    if stype == 'poll':
        if len(rest) < 2:
            usage()
        return {
            "name": name, "type": "poll",
            "command": ' '.join(rest[1:]),
            "interval": int(rest[0]),
            "diff": True,
        }
    elif stype == 'heartbeat':
        return {
            "name": name, "type": "heartbeat",
            "interval": int(rest[0]) if rest else 60,
        }
    elif stype == 'command':
        if not rest:
            usage()
        return {
            "name": name, "type": "command",
            "command": ' '.join(rest),
        }
    elif stype == 'watch':
        root = None
        paths = []
        i = 0
        while i < len(rest):
            if rest[i] == '--root' and i + 1 < len(rest):
                root = rest[i + 1]
                i += 2
            else:
                paths.append(rest[i])
                i += 1
        if not paths:
            usage()
        source = {"name": name, "type": "watch", "paths": paths}
        if root:
            source["root"] = root
        return source
    elif stype == 'tail':
        if not rest:
            usage()
        source = {"name": name, "type": "tail", "file": rest[0]}
        if len(rest) > 1:
            source["timeout"] = int(rest[1])
        if len(rest) > 2:
            source["max_lines"] = int(rest[2])
        return source
    elif stype == 'ci':
        if not rest:
            usage()
        arg = rest[0]
        if arg.isdigit():
            return {"name": name, "type": "ci", "run_id": arg}
        else:
            return {"name": name, "type": "ci", "branch": arg}
    elif stype == 'webhook':
        path = rest[0] if rest else '/hook'
        return {"name": name, "type": "webhook", "path": path}
    else:
        print(f"Unknown type: {stype}", file=sys.stderr)
        usage()


# Find sidecar port
sidecar_json = os.path.join(os.getcwd(), '.claude', 'sidecar.json')
if not os.path.exists(sidecar_json):
    print("Error: sidecar not running (no .claude/sidecar.json)", file=sys.stderr)
    sys.exit(1)

port = json.load(open(sidecar_json))['port']

# Build source from CLI args or stdin JSON
if len(sys.argv) > 1:
    source = build_source(sys.argv[1:])
else:
    if sys.stdin.isatty():
        usage()
    source = json.loads(sys.stdin.read())

body = json.dumps(source).encode()

try:
    req = urllib.request.Request(
        f'http://localhost:{port}/source', data=body,
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
