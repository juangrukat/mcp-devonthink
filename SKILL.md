# DEVONthink MCP Skill

## Purpose
Use this skill when an AI/agent needs to operate DEVONthink through this MCP server.

This repository is DEVONthink-only and exposes AppleScript dictionary commands as MCP tools.

## Key Point: Composable Power

Do not treat this as a narrow single-command API.

The agent can combine DEVONthink primitives into higher-level workflows, for example:
- search -> classify -> move -> tag/comment -> tracker note
- OCR -> extract key facts -> generate concise metadata -> cross-link related notes
- gather item links -> build hub/index notes -> maintain done/remaining task state
- retrieve records -> summarize/compare -> produce project-oriented outputs

In other words: the capability surface is both broad (dictionary coverage) and creative (workflow composition).

## Coverage Summary

- Broad dictionary coverage from bundled command specs in `app/data/devonthink_command_specs.json` via generated tools.
- Specialized structured wrappers for common workflows:
  - `devonthink-search-records`
  - `devonthink-filter-records`
  - `devonthink-search-media-records`
  - `devonthink-get-record-by-uuid`
  - `devonthink-read-record-text`
  - `devonthink-get-database-by-uuid`
  - `devonthink-create-record`
  - `devonthink-batch-update-record-metadata`
- Link-intelligence workflow tools:
  - canonical inspect/analyze: `devonthink-link-resolve`, `devonthink-link-audit-*`, `devonthink-link-map-neighborhood`, `devonthink-link-find-orphans`, `devonthink-link-suggest-related`, `devonthink-link-score`, `devonthink-link-detect-bridges`
  - advanced act: `devonthink-link-build-hub`, `devonthink-link-enrich-metadata`, `devonthink-link-repair-links`, `devonthink-link-maintenance-pass`
  - advanced analyze/report: `devonthink-link-traverse-folder`, `devonthink-link-compare-snapshots`, `devonthink-link-prune-snapshots`

Link graph signals are authoritative when available from DEVONthink record properties:
- `incoming references`
- `outgoing references`
- `incoming Wiki references`
- `outgoing Wiki references`

Fallback inferred parsing is only used when those properties are unavailable.

## Naming and Invocation Model

- Tool names: `devonthink-<action>` (lowercase, hyphenated).
- Generated tools usually accept:
  - `direct`: direct AppleScript object/value
  - `parameters`: dictionary keyed by exact dictionary parameter labels

## Prioritized Tool Selection

1. Prefer specialized wrappers for search/lookup/create when they fit.
2. Use `devonthink-filter-records` for combined content + metadata questions:
   - file type/extension: PDF, PNG, RTF, EPUB, etc.
   - exact tags such as `archived`
   - name/filename lookup such as `todo.txt` or `Meeting Notes`
   - date ranges (`created_from`, `created_to`, etc.)
   - dimensions and file size
   - path/size reporting
3. Use `devonthink-search-media-records` for audio/video instead of `kind:Movie` or `kind:Audio`.
4. Use `devonthink-batch-update-record-metadata` for textual state such as TODO; DEVONthink labels are only numeric color labels.
5. Use `devonthink-read-record-text` for record contents after resolving a UUID.
6. Otherwise use generated dictionary tools that match the requested DEVONthink command.
7. Prefer canonical (read) tools before advanced/internal tools unless user asks for mutation.

## Search Semantics To Avoid

DEVONthink `kind` is human-readable/localized and not a reliable type check. Do not use `kind:PDF`,
`kind:Movie`, `kind:PNG`, etc. as proof of file type. Prefer structured filters:
- `file_extensions:["pdf"]`
- `record_types:["pdf"]`
- `file_extensions:["png"]`
- `record_types:["image"]`

Do not use `label:archived` for tags. `label` is a color number only. Use `tags:["archived"]`.

Do not rely on free-form date predicates for created/modified/added ranges. Use
`devonthink-filter-records` with ISO date parameters.

For rich text phrase searches that full-text search misses, use `content_mode:"plain_text"` with
`record_types:["rtf"]` or `file_extensions:["rtf"]`.

For filename/title lookup, use `name_contains` or `filename_contains` on `devonthink-filter-records`.
For current record content, use `devonthink-read-record-text` before external text extraction tools.

## AppleScript Fallback Policy

Use `devonthink-run-applescript` only after structured tools fail or when probing a suspected
DEVONthink AppleScript limitation. It writes source and result transcripts under:

`.devonthink-mcp/osascript-runs/`

This scratch directory is for reviewable fallback probes, not permanent implementation. Repeatedly
useful scripts should be promoted into validated MCP wrappers and tests.

## Tool Profile Awareness

The running server may expose different tool sets via `DEVONTHINK_TOOL_PROFILE`:
- `minimal`: specialized tools only
- `canonical`: specialized + canonical link-intelligence + canonical dictionary tools (default)
- `full`: specialized + canonical/advanced link-intelligence + all dictionary tools

If a needed command tool is missing, check the active profile before assuming unsupported functionality.

## Exclusions (Do Not Use)

- `print settings`
- `print`
- `download image for prompt`
- chat/image engine usage commands

## Preconditions

- DEVONthink installed on macOS.
- DEVONthink running.
- macOS Automation permission granted for the host process running MCP.

## Error-Handling Behavior

Return clear actionable failures for:
- missing/invalid required parameters,
- app not running/unavailable,
- Automation permission denial,
- unresolved UUID/item-link references.

## Research + PM + Zettelkasten Fit

Current toolset supports:
- advanced query search,
- note lookup by UUID/item links,
- record creation flows,
- reminder/OCR/markdown/PDF-related dictionary commands through generated tools,
- batch organization workflows (classify/tag/comment/move + hub tracking),
- metadata-driven linking via `reference URL`.
