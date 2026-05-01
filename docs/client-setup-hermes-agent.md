# Hermes Agent Setup

1. Install or refresh the Hermes skill pointer:

```bash
python3 ./scripts/manage_connectors.py install hermes --profile canonical
```

2. Check status:

```bash
python3 ./scripts/manage_connectors.py status
```

Notes:
- Hermes skill is written to `~/.hermes/skills/devonthink-mcp.md`.
- The skill file points Hermes at this workspace and the local `main.py` entrypoint.
- Hermes still launches the stdio MCP server on demand; it is not a separate always-on daemon.
