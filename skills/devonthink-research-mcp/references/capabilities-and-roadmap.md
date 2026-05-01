# DEVONthink MCP Capabilities

## Current State

The MCP exposes near-complete DEVONthink command coverage (except explicit exclusions), including:

- search and lookup commands,
- reliable filtered search by extension/type/tag/date/dimension/size,
- record text reading through DEVONthink's plain text/transcription property,
- record/database retrieval,
- create/import/export/convert commands,
- OCR suite commands,
- imprint commands,
- reminder-related commands,
- workspace/progress/download manager commands.

Implementation source note:
- Runtime command metadata is bundled in `app/data/devonthink_command_specs.json` for stability.

## Beyond Single Commands: Composed Workflows

The strongest usage pattern is composition:
- chain multiple DEVONthink operations into one coherent outcome,
- keep state in hub/tracker notes,
- iterate until all items are processed.

Examples:
- Inbox triage pipeline (flag -> classify -> move -> annotate -> unflag)
- OCR enrichment pipeline (OCR -> summarize -> comment/tag -> link)
- Research synthesis pipeline (search -> gather references -> summarize -> output note/table)
- Filtered retrieval pipeline (full-text search -> extension/tag/date/dimension filter -> path/size report)

## Link Intelligence Contract

The MCP now includes a layered link-ops surface:
- inspect,
- analyze,
- act.

Authoritative edge terms are used first:
- incoming references,
- outgoing references,
- incoming Wiki references,
- outgoing Wiki references.

Fallback inferred parsing is used only if authoritative properties are unavailable.

All `devonthink-link-*` tools return versioned envelopes with:
- contract version,
- signal model version,
- toolset version,
- observability stats/warnings/duration.

## Practical Research + Zettelkasten Support

- Advanced search: yes (specialized + generated command tools).
- Filtered file retrieval: yes (`devonthink-filter-records` for type/extension/tag/date/dimension/size constraints).
- Record content reading: yes (`devonthink-read-record-text` after UUID resolution).
- Media retrieval: yes (`devonthink-search-media-records` for real audio/video records).
- Note linking: yes (UUID/item-link retrieval and lookup flows).
- Catalogue/index creation: yes (record creation + search/lookup/export combinations).
- Reminders/timer-like workflow inside DEVONthink: yes (dictionary reminder commands exposed).

## Fallback Probe Workflow

`devonthink-run-applescript` can run focused ad hoc AppleScript when structured tools are insufficient.
It persists source and result transcripts under `.devonthink-mcp/osascript-runs/` so repeated probes can
be reviewed and promoted into validated wrappers. Use it only after safer wrappers fail or when testing a
specific AppleScript hypothesis.

## Format/OCR Support

- Rich-text/format-related creation/conversion commands: available.
- Markdown creation commands: available.
- PDF creation commands: available.
- OCR commands (`ocr`, `convert image`): available.

## Excluded

- print settings/print,
- download image for prompt,
- chat engine/usage commands.
