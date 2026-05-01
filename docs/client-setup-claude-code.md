# Claude Code Setup

1. Install or refresh the Claude Code connector:

```bash
python3 ./scripts/manage_connectors.py install claude --profile canonical
```

2. Check status:

```bash
python3 ./scripts/manage_connectors.py status
```

3. Verify in Claude Code MCP list/health view.

Notes:
- Claude config is written to `~/.claude/settings.json`.
- The manager merges `mcpServers.devonthink` and preserves other Claude settings.
- If calls fail only in Claude Code, re-check macOS Automation permissions for Claude Code and the host process chain.
