---
description: Register a webhook endpoint exposed via ngrok tunnel. Use for receiving webhooks from external services like GitHub, Stripe, or any service that needs a public URL.
argument-hint: [path=/hook] [subdomain=]
allowed-tools: Bash, Read
---

Register a webhook source on the sidecar and report the ngrok public URL.

Parse `$ARGUMENTS` for optional path (default: `/hook`) and optional ngrok subdomain.

1. Register the webhook source:
```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-register.py' webhook 'SOURCE_NAME' '/PATH'")
```

2. Check if ngrok is tunneling to the sidecar port. Read `.claude/sidecar.json` for the port, then:
```
Bash(command="curl -sf http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c \"import sys,json; tunnels=json.load(sys.stdin).get('tunnels',[]); [print(t['public_url']) for t in tunnels if 'https' in t.get('public_url','')]\" 2>/dev/null || echo 'ngrok not running'")
```

3. If ngrok is not running, start it tunneled to the sidecar port:
```
Bash(command="ngrok http SIDECAR_PORT --log=false &>/dev/null &")
```

4. Report the public webhook URL to the user: `https://<ngrok-domain>/PATH`

Events arrive through the sidecar drain — look for `source: "runtime:SOURCE_NAME"` with `type: "webhook_received"`.

To stop:
```
Bash(command="python3 '${CLAUDE_PLUGIN_ROOT}/scripts/source-remove.py' 'SOURCE_NAME'")
```

**Requirements:** ngrok must be installed and authenticated.
