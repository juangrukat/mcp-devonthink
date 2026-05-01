# OpenClaw Setup

1. Install or refresh the OpenClaw MCP entry:

```bash
python3 ./scripts/manage_connectors.py install openclaw --profile canonical
```

2. Check status:

```bash
python3 ./scripts/manage_connectors.py status
```

Notes:
- OpenClaw config is updated at `~/.openclaw/openclaw.json`.
- The manager preserves existing OpenClaw MCP servers and upserts only the `devonthink` entry.
- If your OpenClaw deployment expects a different config shape, inspect the file after install and adjust locally.
