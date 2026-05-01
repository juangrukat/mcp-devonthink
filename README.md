# DEVONthink MCP

DEVONthink-focused MCP server for macOS.

This repo exposes DEVONthink automation as MCP tools (AppleScript-backed), with a practical default toolset for search, metadata work, OCR flows, linking, reminders, messaging actions, and batch organization.

## Contents

- [What You Can Do](#what-you-can-do)
- [Signal Model](#signal-model)
- [Link Intelligence Layer](#link-intelligence-layer-inspect---analyze---act)
- [AppleScript Compatibility Notes](#applescript-compatibility-notes)
- [Connectivity Shape Classifier](#connectivity-shape-classifier)
- [Tool Surface](#tool-surface)
- [Tool Profiles](#tool-profiles)
- [Safe Composition Patterns](#safe-composition-patterns)
- [Configuration](#configuration)
- [Snapshots](#snapshots)
- [Maintenance Pass](#maintenance-pass)
- [Recommended Workflow](#recommended-workflow)
- [Testing](#testing)
- [Requirements](#requirements)
- [Install](#install)
- [Start Server](#start-server)
- [Client Connector Setup (VS Code / Codex / Claude Code)](#client-connector-setup-vs-code--codex--claude-code)
- [Validation](#validation)
- [Project Layout](#project-layout)

## What You Can Do

Prompted with the following: "identify the tags in DEVONthink, and create a hub note that presents the tags in a hierarchy with related tags grouped together" Deepseek 4 output: 

![Example 1](image/example1.png)

Core workflows verified in this repo:

- Search and lookup
  - advanced search queries
  - lookup by UUID / `x-devonthink-item://` reference URL
  - list recent documents in a folder (e.g., Inbox)
- Organize and process
  - create/move records and groups
  - batch classify/move/tag/comment operations
  - build tracker/hub notes during processing
- Metadata and linking
  - tags, comments, custom metadata
  - record links (`reference URL`) for cross-link workflows
- OCR and extraction
  - OCR images/PDFs
  - set searchable text on image/PDF records
  - pull page/time/frame-oriented media properties where supported
- Reminder and notifications
  - add reminders with alarm types (`notification`, `sound`, etc.)
- Messaging/email actions
  - send record links by email
  - send file attachments through Messages (subject to local account routing)

## Recommended Wiki-Link Setup (Large Knowledge Bases)

For long-running wiki-style databases (zettelkasten / research graph), use:

1. Square-bracket links as primary (`[[...]]`)
- best precision and maintainability at scale
- fewer accidental auto-links than free-form matching

2. Names/aliases with discipline
- enable only when aliases are curated and specific
- avoid broad/common aliases that create noisy links

3. Use links + mentions as retrieval signals
- rely on `reference URL` (`x-devonthink-item://...`) for durable linking
- use mention/backlink workflows to find related notes and maintain hub indexes

Practical pattern:
- record link generation -> mention search -> summarize mentions -> tracker/hub updates

## Link/Mention Workflows (Verified)

This repo supports actionable link intelligence:

- Generate item links from records (`reference URL`)
- Find mention/backlink candidates by searching for item-link strings
- Build mention summaries with `summarize mentions of`
- Use authoritative edge properties on records:
  - `incoming references`
  - `outgoing references`
  - `incoming Wiki references`
  - `outgoing Wiki references`

These can be chained in batch workflows (e.g., gather related notes, create hub pages, keep done/remaining state).

## Signal Model

Link-intelligence tools use a three-tier signal hierarchy:

- `authoritative`: native DEVONthink properties (`incoming references`, `outgoing references`, `incoming Wiki references`, `outgoing Wiki references`, `aliases`, `tags`, `reference URL`)
- `structural`: deterministic derivations (title/path/group context, metadata-derived structure)
- `inferred`: probabilistic derivations (fuzzy title similarity, mention-text and related heuristics)

Every response includes `lowest_signal_tier`.
Graph outputs derived from inferred signals are labeled and should not be treated as authoritative edge data.

## Authoritative vs Full

Use `mode=authoritative` when a tool exposes it unless you explicitly need inferred content-derived signals. `mode=authoritative` uses only native DEVONthink graph properties such as incoming/outgoing references, wiki references, aliases, tags, and reference URL; these paths are the fast default and typically complete in 1-3 Apple Events. `mode=full` adds content scan and inferred wikilink extraction, so it is slower and should be used only when `[[wikilink]]`-style or mention-text signals are required.

Operational rule:
- never call `mode=full` inside a loop, batch operation, or broad folder traversal
- prefer `devonthink-link-audit-folder` over repeated `devonthink-link-audit-record` calls when you need folder-wide health data
- treat inferred outputs as advisory, not authoritative graph truth

## Link Intelligence Layer (Inspect -> Analyze -> Act)

Composed tools are available for zettelkasten-scale link operations:

- Inspect (canonical)
  - `devonthink-link-resolve`
  - `devonthink-link-audit-record`
  - `devonthink-link-audit-folder`
- Analyze (canonical)
  - `devonthink-link-map-neighborhood`
  - `devonthink-link-find-orphans`
  - `devonthink-link-suggest-related`
  - `devonthink-link-score`
  - `devonthink-link-detect-bridges`
  - `devonthink-link-check-reciprocal`
- Act (advanced/full profile)
  - `devonthink-link-build-hub`
  - `devonthink-link-enrich-metadata`
  - `devonthink-link-repair-links`
  - `devonthink-link-maintenance-pass`
  - `devonthink-link-traverse-folder`
  - `devonthink-link-compare-snapshots`
  - `devonthink-link-prune-snapshots`

All link-intelligence tools return a versioned response envelope with:
- `contract.contract_version`
- `contract.signal_model_version`
- `contract.toolset_version`
- `observability` (duration, warnings, stats)

`devonthink-link-traverse-folder` additionally supports:
- cursor-based resume (`cursor`)
- node-first adjacency map + flat edge list
- dedup by UUID (replicant-safe)
- recursive traversal (`mode=recursive`, `max_depth`, cycle-safe group traversal)
- optional group path tracking (`group_path_tracking=true`)
- optional baseline snapshot files (`snapshots/*.json` + `.meta.json`)

`devonthink-link-compare-snapshots` additionally supports:
- explicit baseline/current snapshot paths (with optional explicit meta paths)
- auto-discovery mode by `folder_ref` using the two most recent snapshots in `snapshot_dir`
- tombstone-aware edge removals (`removal_reason: tombstoned|unlinked`)
- `diff_confidence` and `health_verdict` summary fields

`devonthink-link-prune-snapshots` additionally supports:
- conservative tiered retention policy with non-destructive defaults
- explicit dry-run mode (`mode=report`) before any file mutations
- archive-first apply mode (`mode=apply`) with optional hard-delete threshold

> Note: DEVONthink returns `Invalid argument (-50)` when `search` is called with a *database* object as the `in` argument. Link tools handle this by degrading gracefully and incrementing `observability.stats.search_calls_degraded`. The `devonthink-search-records` tool resolves this automatically: if `database_uuid` receives a database UUID, it fetches the database's `incoming group` and uses that as the search scope instead. Pass a **group UUID** to scope search to a specific folder; pass a **database UUID** to scope to the database root. The raw `devonthink-search` dictionary wrapper now applies the same group-or-database UUID guard for its `in` parameter.
> Note: Smart Groups are traversed as saved queries (via `search predicates` + `search group`), not as physical children. Output marks this with `membership_type: virtual` and `children_source: smart_group_query`.

## AppleScript Compatibility Notes

DEVONthink's AppleScript model mixes **typed enumerations** and **plain-string properties**.
Knowing which is which prevents silent failures.

### `type` vs `kind`

| Property | Comparison form | Example values |
|---|---|---|
| `type` | AppleScript enum token (unquoted) | `markdown`, `txt`, `rtf`, `group`, `pdf document`, `picture`, `bookmark` |
| `kind` | String (quoted) | `"Markdown"`, `"Plain Text"`, `"Formatted Note"`, `"PDF Document"` |

Use `type of theRecord is markdown` (enum), not `type of theRecord is "markdown"` (string).
Forum-confirmed: `if type of theRecord is markdown then ...` works; string comparison against `type` does not.

For media, prefer `devonthink-search-media-records` over `kind:Movie`/`kind:Audio` searches.
DEVONthink's own dictionary warns that `kind` is human-readable/localized and should not be used
for type checks. The media wrapper filters on `record type=multimedia` and returns structured
`mime_type`, `filename`, `extension`, `duration`, and `size` fields so EPUB/PDF false positives are
visible immediately.

For combined search + metadata constraints, prefer `devonthink-filter-records`. It searches or
enumerates records and filters the returned structured properties directly:

- PDF/RTF/PNG queries: use `file_extensions:["pdf"]`, `record_types:["rtf"]`, etc. instead of `kind:*`.
- Filename/title lookup: use `filename_contains:"todo.txt"` or `name_contains:"Meeting Notes"` instead of broad content search.
- Exact tags: use `tags:["archived"]` instead of `label:archived` or browsing the Tags group.
- Date ranges: use `created_from:"2024-01-01"`, `created_to:"2024-03-31"` instead of free-form date predicates.
- Dimensions and size: use `min_width`, `max_width`, `min_size`, etc.
- Rich text phrase checks: use `content_mode:"plain_text"` when full-text search misses RTF content.

For record content, prefer `devonthink-read-record-text` after resolving the record UUID. It reads
DEVONthink's own `plain text`/transcription property for text, rich text, Markdown, and searchable
PDF records before falling back to external filesystem tools.

### Labels vs tags/comments

DEVONthink `label` is a numeric color label only:

| Value | Meaning |
|---|---|
| `0` | None |
| `1` | Red |
| `2` | Orange |
| `3` | Yellow |
| `4` | Green |
| `5` | Blue |
| `6` | Purple |
| `7` | Teal |

For textual workflow state such as `TODO`, use `devonthink-batch-update-record-metadata` with
`tags:["TODO"]`, `comment:"TODO"`, or both. Use `devonthink-set-label` /
`devonthink-batch-set-label` only when you intentionally want a color label.

### AppleScript fallback probes

Use structured MCP wrappers first. When a workflow is blocked by DEVONthink AppleScript quirks,
`devonthink-run-applescript` can run an ad hoc AppleScript probe from a managed scratch directory:

```
.devonthink-mcp/osascript-runs/
```

The tool writes both the `.applescript` source and a `.result.json` transcript. This gives agents a
controlled fallback without scattering scripts in `/tmp`, and makes it easy to review repeated
fallbacks later and promote common patterns into first-class wrappers. The scratch directory is
ignored by git. Treat this tool as advanced/high-risk because it executes arbitrary AppleScript.

### Annotation notes vs PDF-internal annotations

DEVONthink has two distinct annotation concepts:

- `devonthink-create-annotation-note` creates a record in the database Annotations group and attaches it through the target record's `annotation` property. Live testing confirmed this works for PDFs, normal records, groups, smart groups, and annotation-note records.
- `devonthink-summarize-annotations` summarizes PDF/internal highlights, comments, and page markup using the required syntax `summarize annotations of records theRecords to markdown in destinationGroup`.

Attached annotation notes do not count as PDF-internal markup. If a record only has an attached annotation note, DEVONthink can return `missing value` from `summarize annotations`; the wrapper reports `data: null` with an observability warning.

### Duplicate and search command forms

DEVONthink is strict about some dictionary command shapes:

- Use `duplicate record theRecord to destinationGroup`, after resolving `theRecord` with `get record with uuid ...`. The shorter `duplicate theRecord to destinationGroup` form can fail with a missing-parameter error.
- Use a group object for `search ... in ...`. Database UUID scopes must be converted to the database's `incoming group`.
- Use `summarize annotations of records {recordList} to markdown in destinationGroup`; omitting `records`, output format, or destination can fail or return no output.

### RTFD caveat

`devonthink-create-rtfd` is text-only. DEVONthink can silently create an `RTF` record instead of a true `RTFD` record when no binary attachment data is provided. The wrapper now returns `actual_type` and warns when DEVONthink downgrades the result.

### Valid `record_type` values for `devonthink-create-record`

Pass the AppleScript enum token, not the human-readable label:

| Token | Human label |
|---|---|
| `markdown` | Markdown |
| `txt` | Plain Text |
| `rtf` | Rich Text |
| `group` | Group |
| `pdf document` | PDF Document |
| `picture` | Image |
| `bookmark` | Bookmark |

Passing an unsupported string (e.g., `"plain text"` instead of `"txt"`) causes DEVONthink to return
`missing value`, which surfaces as an opaque `-1728` AppleScript error. The tool validates this
before dispatching the AppleScript call and returns `{"ok": false, "error": "..."}` with a
clear message.

### `lookup records with tags` — AND semantics

DEVONthink's `lookup records with tags tagList` requires a record to have **every** tag in the list.
To find records sharing *any* of a source record's tags (OR semantics), query one tag at a time
and deduplicate by UUID. `devonthink-link-suggest-related` does this automatically.

To replicate OR-style tag search manually use DEVONthink search syntax:
```
any: tags:concordance tags:methodology
```

### `get links of` — not supported for markdown/txt records

`get links of` fails with `-1708` ("doesn't understand the message") for `markdown` and `txt`
record types in DEVONthink 4.1.1. Link-intelligence tools degrade gracefully to wikilink and
mention-text scanning when this command fails, and record the degradation in `observability.warnings`.
Explicit `x-devonthink-item://` links pasted *into* markdown files are still auto-converted by
DEVONthink and are detectable via content scanning.

---

## Connectivity Shape Classifier

| Shape | Default criteria | Meaning |
|---|---|---|
| `hub` | outgoing >= 5 and incoming >= 2 | Central organizing note |
| `spoke` | incoming >= 2 and outgoing <= 2 | Well-referenced leaf |
| `bridge` | outgoing >= 3 and connects >= 2 clusters | Cross-topic linker |
| `sink` | incoming >= 3 and outgoing = 0 | Referenced but no outbound links |
| `near_orphan` | total edges <= 1 | Weakly connected |
| `isolated` | incoming = 0 and outgoing = 0 | True orphan |
| `connected` | has links but no stronger shape rule match | General connectivity |

## Tool Surface

The server exposes two layers:

1. Specialized structured tools (recommended default)
- `devonthink-search-records`
- `devonthink-get-record-by-uuid`
- `devonthink-get-database-by-uuid`
- `devonthink-get-database-incoming-group`
- `devonthink-list-group-children`
- `devonthink-create-record`
- `devonthink-link-*` (tiered link intelligence tools)
- `devonthink-link-maintenance-pass` — delta-based health reporting against snapshot baseline

2. Generated dictionary tools (broad coverage)
- names follow `devonthink-<action>`
- powered from bundled command specs: `app/data/devonthink_command_specs.json`

## Tool Profiles

Control how many tools are exposed:

- `minimal`: specialized tools only
- `canonical`: specialized + canonical link tools + canonical dictionary tools (default)
- `full`: specialized + canonical/advanced link tools + all dictionary tools

Treat these as an escalation ladder, not a flat menu:

1. `minimal`
- use when you want the smallest, safest surface
- best for retrieval, lookup, simple creation, Inbox/database-root resolution, and direct child listing

2. `canonical`
- use when you need the normal operating surface
- adds read-heavy dictionary tools and canonical link-analysis tools
- best default for most clients and weaker models

3. `full`
- use only when you explicitly need advanced/native act-layer commands
- exposes destructive, UI-coupled, and maintenance-oriented tools
- required for tools like `devonthink-link-repair-links`, `devonthink-link-enrich-metadata`, `devonthink-link-build-hub`, `devonthink-link-maintenance-pass`, and `devonthink-link-traverse-folder`

> Warning: act-layer tools mutate DEVONthink records (`devonthink-link-repair-links`, `devonthink-link-enrich-metadata`, `devonthink-link-maintenance-pass`, `devonthink-link-build-hub`). Prefer `--profile canonical` for read-only usage and use report/dry-run modes first.

> Deployment status:
> - `devonthink-link-repair-links`: production ready (idempotent apply verified)
> - `devonthink-link-build-hub`: production ready (dedup on re-run verified)
> - `devonthink-link-enrich-metadata`: production ready
> - `devonthink-link-maintenance-pass`: production ready (delta-wired against snapshot baseline)
> - `devonthink-link-compare-snapshots`: production ready
> - `devonthink-link-prune-snapshots`: production ready (`mode=apply` requires explicit invocation)

Example:

```bash
DEVONTHINK_TOOL_PROFILE=full python3 main.py --transport=stdio
```

Profile guidance for clients:

- start in `minimal` if the client is only doing stable lookups, search, or simple creation
- move to `canonical` when the client needs analysis or native dictionary coverage
- escalate to `full` only after the task clearly requires act-layer or low-level DEVONthink commands

## Safe Composition Patterns

Use these sanctioned workflows instead of improvising ad hoc multi-step edits.

### Database root / Inbox scope

1. `devonthink-get-database-by-uuid`
2. `devonthink-get-database-incoming-group`
3. pass the returned group UUID into `devonthink-search-records` or `devonthink-list-group-children`

Notes:
- `devonthink-search-records` accepts either a group UUID or a database UUID in `database_uuid`
- when given a database UUID, it auto-resolves to the database's incoming group instead of issuing the raw DEVONthink `search ... in database` call that can fail with `Invalid argument (-50)`

### Safe relocation

1. `devonthink-get-record-by-uuid`
2. `devonthink-list-group-children` or `devonthink-get-database-incoming-group` to resolve the destination scope
3. `devonthink-move`
4. verify with `devonthink-get-record-by-uuid` or `devonthink-search-records`

### Graph-health maintenance

1. `devonthink-link-traverse-folder` with `write_snapshot=true`
2. `devonthink-link-maintenance-pass` in `mode=report`
3. review `observability.warnings`, `health_verdict`, and candidate actions
4. only then use `mode=apply` where supported

### Search / filter / batch-update

1. `devonthink-search-records`
2. inspect returned UUIDs with `devonthink-get-record-by-uuid` or `devonthink-link-audit-record`
3. apply writes with the smallest possible mutating tool
4. verify by re-running search or lookup after the write

### Folder hierarchy inspection

1. `devonthink-get-database-incoming-group` if you start from a database UUID
2. `devonthink-list-group-children` for direct children
3. `devonthink-link-traverse-folder` only when you need recursive traversal, smart-group handling, adjacency output, or snapshots

## Configuration

Link-analysis tuning is configurable via environment variables:
- `DEVONTHINK_LINK_SEARCH_MIN_LENGTH` (default `2`)
- `DEVONTHINK_LINK_FUZZY_SKIP_THRESHOLD` (default `3`)
- `DEVONTHINK_LINK_MAX_TRAVERSAL_DEPTH` (default `3`)
- shape thresholds: `DEVONTHINK_LINK_HUB_OUTGOING`, `DEVONTHINK_LINK_HUB_INCOMING`, `DEVONTHINK_LINK_SPOKE_INCOMING`, `DEVONTHINK_LINK_SPOKE_OUTGOING_MAX`, `DEVONTHINK_LINK_BRIDGE_OUTGOING`, `DEVONTHINK_LINK_BRIDGE_CLUSTERS`, `DEVONTHINK_LINK_SINK_INCOMING`, `DEVONTHINK_LINK_NEAR_ORPHAN_TOTAL_MAX`
- `DEVONTHINK_LINK_BENCHMARK_GROUP_UUID` (optional, default group UUID for benchmark runner when CLI arg is omitted)

## Snapshots

`devonthink-link-traverse-folder` can write snapshot pairs to:

```text
snapshots/
  <prefix>_<timestamp>.json
  <prefix>_<timestamp>.meta.json
```

Snapshots are append-only; no automatic cleanup is performed.
For long-running knowledge bases, prune old snapshots based on your retention window.
The most recent snapshot pair for a folder is automatically used by `maintenance-pass` as its baseline.
Snapshots are never auto-deleted; use `devonthink-link-prune-snapshots` for lifecycle management.

## Maintenance Pass

`devonthink-link-maintenance-pass` is the scheduled health tool for long-running
knowledge bases. It compares your current graph state against the most recent
snapshot baseline and reports only what changed — not the full absolute state.

### First run

On the first run against a folder, no baseline exists yet. The tool captures
the current state as the baseline and returns:

```json
{
  "first_run": true,
  "message": "No baseline found. Current state captured as baseline. Run again to see deltas."
}
```

Run it a second time after making changes to see the first real delta report.

### Subsequent runs

Every run after the first compares against the previous snapshot and returns
a `health_verdict` plus actionable rows:

| `health_verdict` | Meaning |
|---|---|
| `stable` | No significant changes — zero actionable rows |
| `improved` | Coverage increased, no new isolated records |
| `degraded` | New isolated/sink records or lost hub nodes |
| `restructured` | Large node churn but coverage is stable |

### Actionable rows

Rows are only emitted when something changed. A stable run produces zero rows.

| Row type | Severity | Suggested action |
|---|---|---|
| `⚠️ became isolated` | high | Link or archive the record |
| `⚠️ new sink node` | medium | Add outgoing links |
| `⚠️ hub degraded` | high | Check for removed links |
| `🗑 record tombstoned` | info | Hub notes auto-cleaned in apply mode |
| `✅ orphan resolved` | info | No action needed |
| `→ shape changed` | info | Review if expected |

### Modes

- `mode=report` — dry run, no writes, no snapshot update
- `mode=apply` — writes repairs, updates snapshot, includes prune advisory

### Apply mode behavior

In apply mode, the tool:
1. Runs a fresh traversal and writes a new snapshot
2. Cleans dead links from hub notes for any tombstoned records
3. Includes a `snapshot_prune_advisory` showing what old snapshots
   could be pruned (pruning requires a separate explicit
   `devonthink-link-prune-snapshots(mode=apply)` call)

### Example output (stable run)

```json
{
  "health_verdict": "stable",
  "coverage_delta_pct": 0.0,
  "actionable_rows": [],
  "summary": {
    "rows_by_severity": {"high": 0, "medium": 0, "info": 0},
    "tombstoned_count": 0,
    "resolved_orphan_count": 0,
    "hub_notes_repaired": 0
  }
}
```

### Example output (degraded run)

```json
{
  "health_verdict": "degraded",
  "coverage_delta_pct": -4.2,
  "actionable_rows": [
    {
      "row_type": "⚠️ became isolated",
      "severity": "high",
      "uuid": "4B3ED807-...",
      "title": "Book Card Catalogue Template",
      "suggestion": "consider linking or archiving",
      "from_shape": "spoke",
      "to_shape": "isolated"
    }
  ]
}
```

## Recommended Workflow

For a new group or database, run tools in this order:

```bash
# 1. Resolve a record to confirm connectivity
devonthink-link-resolve <uuid>

# 2. Audit the folder baseline
devonthink-link-audit-folder <folder-uuid>

# 3. Find orphans and weak nodes
devonthink-link-find-orphans <folder-uuid>

# 4. Get related suggestions for weak nodes
devonthink-link-suggest-related <record-uuid>

# 5. Build hub notes for well-connected groups
devonthink-link-build-hub <group-uuid> --title "My Hub"

# 6. Run first maintenance pass to capture baseline
devonthink-link-maintenance-pass <folder-uuid> --mode apply

# 7. On subsequent runs, maintenance-pass reports only deltas
devonthink-link-maintenance-pass <folder-uuid> --mode apply
```

For ongoing maintenance, run step 7 periodically. Use
`devonthink-link-compare-snapshots` for ad-hoc graph health checks
between maintenance passes.

## Explicit Exclusions

Not exposed by design:

- `print settings`
- `print`
- `download image for prompt`
- chat/image engine usage commands (`display chat dialog`, `get chat capabilities/models/response`)

## Testing

Integration tests run against a live DEVONthink instance using a disposable fixture corpus:

```bash
python tests/test_scholar_corpus.py
# or
python -m pytest tests/ -v
```

The main test fixture (`MCP Test - Scholar Corpus` in Inbox) consists of 12 records across three
groups (Concordance Discovery, Archive Reconstruction, Chronological Assembly) and covers all
canonical tools.

The edge-case stress fixture is `MCP Chaos Lab 20260424-080344`
(`3EFAAB4A-5BCD-4699-A472-1F66EF3C7882`). It was created from live files to validate unusual
combinations: annotation notes on PDFs/groups/smart groups/annotation notes, duplicate-record
syntax, smart groups, RTF/RTFD caveats, labels/ratings on annotation notes, and summarize-annotation
missing-value behavior. Keep it as a named fixture unless intentionally regenerating live stress data.

Fixture UUIDs are exposed in `tests/conftest.py`. Test data is erasable unless explicitly kept as a
fixture. Tests require DEVONthink to be running and Automation permission granted to the Python host.

---

## Requirements

- macOS
- Python 3.11+
- DEVONthink installed and running
- macOS Automation permission granted to the host app/process

## macOS Permission Setup

If a tool call fails with automation errors, check:

- `System Settings` -> `Privacy & Security` -> `Automation`
- allow your host app (Terminal, VS Code, Codex host, etc.) to control `DEVONthink`

## Install

The recommended install uses [`pipx`](https://pipx.pypa.io/), which puts the
server in its own isolated environment and exposes a `devonthink-mcp` command
on your `PATH` — no manual venv to activate, and no absolute paths in your MCP
client config.

```bash
brew install pipx          # if you don't already have it
pipx install .             # run from the repo root
cp .env.example .env       # optional: local overrides
```

After install, point your MCP client at the command directly:

```yaml
command: devonthink-mcp
```

### Alternative: virtualenv

If you prefer a manual venv (e.g. you don't want pipx, or you're hacking on
the source):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Then point your MCP client at the venv's Python and the entry script — these
need to be absolute paths because most MCP clients don't honor `PATH` or `cwd`
the way a shell does:

```yaml
command: /absolute/path/to/devonthink-mcp/.venv/bin/python
args: ["/absolute/path/to/devonthink-mcp/main.py"]
```

## Start Server

Default (`stdio`):

```bash
python3 main.py
```

Explicit transports:

```bash
python3 main.py --transport=stdio
python3 main.py --transport=sse
MCP_TRANSPORT=web python3 main.py
```

Precedence:
1. `--transport`
2. `MCP_TRANSPORT`
3. default `stdio`

## Client Connector Setup (VS Code / Codex / Claude Code)

Use the macOS connector manager:

```bash
python3 ./scripts/manage_connectors.py
```

Non-interactive install examples:

```bash
python3 ./scripts/manage_connectors.py install codex vscode claude --profile canonical
python3 ./scripts/manage_connectors.py install hermes openclaw --profile full
python3 ./scripts/manage_connectors.py status
python3 ./scripts/manage_connectors.py stop-server
```

Compatibility wrapper:

```bash
./scripts/install-connectors.sh --profile full codex vscode claude
```

Benchmark AppleScript/traversal performance baseline:

```bash
./scripts/benchmark_applescript.sh [group_uuid]
```

This writes a benchmark artifact JSON into `benchmarks/`.
If no CLI argument is provided, the script uses `DEVONTHINK_LINK_BENCHMARK_GROUP_UUID` when set, otherwise it defaults to the Inbox group UUID.

Client guides:
- `docs/client-setup-vscode.md`
- `docs/client-setup-codex.md`
- `docs/client-setup-claude-code.md`
- `docs/client-setup-hermes-agent.md`
- `docs/client-setup-openclaw.md`

## Validation

```bash
python3 -m compileall app main.py
python3 main.py --help
```

## Project Layout

- `app/tools/devonthink_tools.py`: specialized wrappers (search, get, create)
- `app/tools/devonthink_link_tools.py`: link-intelligence layer
- `app/tools/devonthink_dictionary_tools.py`: broad generated command tools
- `app/data/devonthink_command_specs.json`: bundled command spec source
- `tests/test_scholar_corpus.py`: integration test suite (91 tests, live DEVONthink)
- `open-issues.md`: confirmed bugs, DX gaps, and performance findings with fix status
- `catalog-runtime/registry/`: runtime catalog metadata
- `clients/`: connector templates
- `scripts/manage_connectors.py`: interactive connector manager and server stopper
- `scripts/install-connectors.sh`: compatibility wrapper around the connector manager
- `scripts/benchmark_applescript.sh`: AppleScript/traversal benchmark runner
- `benchmarks/`: saved benchmark artifacts (JSON)
- `snapshots/`: link-graph snapshot files written by `traverse-folder`
- `skills/devonthink-research-mcp/`: reusable skill package

