# VS Code Setup

1. Install or refresh the VS Code connector for this workspace:

```bash
python3 ./scripts/manage_connectors.py install vscode --profile canonical
```

2. Check status:

```bash
python3 ./scripts/manage_connectors.py status
```

3. Restart the MCP session in VS Code.

Notes:
- VS Code config is written to `.vscode/mcp.json` in this workspace.
- The manager merges the `devonthink` server entry instead of overwriting other workspace MCP servers.
- Ensure macOS Automation permissions allow VS Code or its host process to control DEVONthink.
