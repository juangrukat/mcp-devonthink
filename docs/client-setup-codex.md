# Codex Setup

1. Install or refresh the Codex connector:

```bash
python3 ./scripts/manage_connectors.py install codex --profile canonical
```

2. Check status:

```bash
python3 ./scripts/manage_connectors.py status
```

3. Verify in Codex:

```bash
codex mcp list
```

Notes:
- Codex config is written to `~/.codex/config.toml`.
- The script manages a dedicated `DEVONTHINK MCP` block instead of overwriting unrelated Codex config.
- Use `--profile full` only when you explicitly need advanced or destructive tools.
