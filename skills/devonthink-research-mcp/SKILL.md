# DEVONthink Research MCP Skill

## Use This Skill When
- The user is researching in DEVONthink, managing projects, or using Zettelkasten workflows.
- The user asks to search, link/retrieve notes, create records, run OCR/import/export workflows, or manage reminders via DEVONthink commands.

## Core Principle

Use this MCP as a composable workflow engine, not only as isolated commands.

Agents should actively combine steps to deliver outcomes:
- collect -> analyze -> organize -> link -> report,
- not just call one tool and stop.

## Coverage

This MCP exposes DEVONthink dictionary commands as MCP tools from the bundled spec file `app/data/devonthink_command_specs.json`, excluding explicit blocked commands (print/chat-image engine related).

Tool naming:
- `devonthink-<action>`
- stable lowercase hyphenated form of dictionary command names

## High-Precision Tools (Prefer First)

1. `devonthink-search-records`
- Structured search output with `query`, `limit`, optional `database_uuid`, optional `comparison`, `exclude_subgroups`.

2. `devonthink-filter-records`
- Reliable search/enumeration followed by structured filters: `file_extensions`, `record_types`, exact `tags`, ISO date ranges, dimensions, size, and `content_mode`.
- Prefer for questions like “PDFs containing invoice”, “PNG images”, “images wider than 1200px”, “created between dates”, “RTF containing phrase”, “filename todo.txt”, and “show path and size”.

3. `devonthink-search-media-records`
- Finds real audio/video records via `record type=multimedia`; do not use `kind:Movie` as a type check.

4. `devonthink-get-record-by-uuid`
- Structured record lookup by UUID or `x-devonthink-item://` link.

5. `devonthink-read-record-text`
- Read DEVONthink's `plain text`/transcription property for current record content after resolving a UUID.

6. `devonthink-get-database-by-uuid`
- Structured database lookup by UUID.

7. `devonthink-create-record`
- Structured record creation wrapper.

8. `devonthink-batch-update-record-metadata`
- Batch tags/comments/optional label/rating. Use for textual workflow states such as TODO; DEVONthink label is color-only.

9. Link-intelligence canonical tools
- `devonthink-link-resolve`
- `devonthink-link-audit-record`
- `devonthink-link-audit-folder`
- `devonthink-link-map-neighborhood`
- `devonthink-link-find-orphans`
- `devonthink-link-suggest-related`
- `devonthink-link-score`
- `devonthink-link-detect-bridges`

10. Link-intelligence advanced tools (full profile)
- `devonthink-link-build-hub`
- `devonthink-link-enrich-metadata`
- `devonthink-link-repair-links`
- `devonthink-link-maintenance-pass`
- `devonthink-link-traverse-folder`
- `devonthink-link-compare-snapshots`
- `devonthink-link-prune-snapshots`

Primary link edge signals are authoritative record properties in DEVONthink:
- `incoming references`, `outgoing references`
- `incoming Wiki references`, `outgoing Wiki references`

## Generated Dictionary Tools (Broad Coverage)

Most additional tools use this call shape:
- `direct`: direct command object/value
- `parameters`: dictionary keyed by exact dictionary parameter names

Use generated tools when specialized wrappers do not cover the needed command.

## Common High-Value Workflow Patterns

- Build hub/tracker notes with filename + item link + short description.
- Process Inbox in batch: classify/tag/comment/move across groups.
- OCR images/PDFs and write searchable text/comments for retrieval.
- Extract metadata and convert it into actionable project notes.
- Send links or attachments via Mail/Messages where local account routing permits.

## Routing Rules For Ambiguous Requests
- “find/search/related notes” -> `devonthink-search-records` first for broad content lookup.
- File type, extension, exact tag, date range, dimension, or size constraints -> `devonthink-filter-records`.
- Filename/title lookup -> `devonthink-filter-records` with `filename_contains` or `name_contains`.
- Current record content -> resolve the UUID, then `devonthink-read-record-text`.
- Audio/video lookup -> `devonthink-search-media-records`, not `kind:Movie`.
- UUID or `x-devonthink-item://` provided -> `devonthink-get-record-by-uuid`.
- “create note/document” -> `devonthink-create-record` or specific generated create/import command.
- “OCR this” -> generated OCR command tools (`devonthink-ocr`, `devonthink-convert-image`).
- “set reminder/timer in DEVONthink” -> generated reminder commands (e.g., `devonthink-add-reminder`).
- “mark TODO/archive/review” -> `devonthink-batch-update-record-metadata` with tags/comment; use label only for color labels.

## Search Pitfalls

- `kind` is localized/human-readable. Avoid `kind:PDF`, `kind:Movie`, `kind:PNG` for type checks.
- `label` is a numeric color label, not a tag. Avoid `label:archived`; use exact `tags`.
- Free-form created/modified/added date predicates can fail through AppleScript. Use ISO date filters on `devonthink-filter-records`.
- For RTF phrase search misses, use `content_mode:"plain_text"` with an RTF filter.
- For filename/title lookup, use `filename_contains` or `name_contains`; do not assume a content query searches only filenames.

## AppleScript Fallback

Use `devonthink-run-applescript` only after safer wrappers fail or for a focused probe of a
DEVONthink AppleScript limitation. It stores source and result transcripts in
`.devonthink-mcp/osascript-runs/` for later review. Promote repeated successful probes into
dedicated wrappers and tests.

## Boundaries
Excluded by design:
- print commands,
- download-image-for-prompt,
- chat engine/usage commands.

## Safety Rules
- Default to canonical read tools unless write/mutation is explicitly requested.
- Treat delete/move/update/synchronize operations as high risk.
- Require explicit identifiers (UUID/item link/path) for targeted mutations.
- Use `devonthink-link-*` act tools only when the user explicitly asks for mutations (hub creation, metadata apply, link repairs).
