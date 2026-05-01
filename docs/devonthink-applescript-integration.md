# DEVONthink AppleScript MCP Integration

This integration maps DEVONthink AppleScript dictionary commands (bundled as `app/data/devonthink_command_specs.json`) into MCP tools backed by `osascript`.

## Coverage

- Full command-family coverage for suites:
  - Standard Suite
  - Extended Text Suite
  - DEVONthink Suite
  - OCR Commands Suite
  - Imprint Commands Suite
- Generated MCP tools follow `devonthink-<action>` naming.
- Specialized high-fidelity wrappers are provided for:
  - `devonthink-search-records`
  - `devonthink-get-record-by-uuid`
  - `devonthink-get-database-by-uuid`
  - `devonthink-create-record`

## Explicit Exclusions

The following commands are intentionally not exposed:

- `print settings`
- `print`
- `download image for prompt`
- chat engine/usage commands (`display chat dialog`, `get chat capabilities for engine`, `get chat models for engine`, `get chat response for message`)

## Tool Interface Pattern (Generated Tools)

Most generated dictionary tools accept:

- `direct`: direct object value for the AppleScript command (if required)
- `parameters`: dictionary of labeled parameters using exact dictionary parameter names

Result format:

- `ok`: success flag
- `tool`, `command`, `suite`
- `raw_result`: AppleScript-returned text/value representation
- `applescript`: executed command snippet for traceability

## Preconditions and Permissions

- DEVONthink must be installed and scriptable via AppleScript.
- DEVONthink should be running for predictable behavior.
- macOS Automation (Apple Events) permission is required:
  - `System Settings` -> `Privacy & Security` -> `Automation`
  - allow your invoking host process to control `DEVONthink`

## Tiering Policy

- `canonical`: read-oriented operations (`get*`, `search*`, `lookup*`, `exists*`, etc.)
- `advanced`: operational but non-default actions (`create*`, `import*`, `export*`, `open*`, etc.)
- `internal`: destructive/high-risk mutations (`delete*`, `move*`, `update*`, `synchronize*`, etc.)

## Safety Notes

- No hidden side-effect commands are placed in canonical tier.
- Input validation checks required direct/labeled parameters and unknown parameter keys.
- Permission and app-availability errors are surfaced with actionable messages.
