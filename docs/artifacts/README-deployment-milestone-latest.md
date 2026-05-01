# DEVONthink MCP

DEVONthink-focused MCP server for macOS.

This repo exposes DEVONthink automation as MCP tools (AppleScript-backed), with a practical default toolset for search, metadata work, OCR flows, linking, reminders, messaging actions, and batch organization.

## Contents

- [What You Can Do](#what-you-can-do)
- [Signal Model](#signal-model)
- [Link Intelligence Layer](#link-intelligence-layer-inspect---analyze---act)
- [Connectivity Shape Classifier](#connectivity-shape-classifier)
- [Tool Surface](#tool-surface)
- [Tool Profiles](#tool-profiles)
- [Configuration](#configuration)
- [Snapshots](#snapshots)
- [Maintenance Pass](#maintenance-pass)
- [Recommended Workflow](#recommended-workflow)
- [Requirements](#requirements)
- [Install](#install)
- [Start Server](#start-server)
- [Client Connector Setup (VS Code / Codex / Claude Code)](#client-connector-setup-vs-code--codex--claude-code)
- [Validation](#validation)
- [Project Layout](#project-layout)

## What You Can Do

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

> Note: DEVONthink can return `Invalid argument (-50)` for certain group-type search patterns. Link tools degrade gracefully and increment `observability.stats.search_calls_degraded` instead of failing.
> Note: Smart Groups are traversed as saved queries (via `search predicates` + `search group`), not as physical children. Output marks this with `membership_type: virtual` and `children_source: smart_group_query`.

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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
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

Generate local connector files with absolute paths:

```bash
./scripts/install-connectors.sh
```

Or generate with full tool exposure:

```bash
./scripts/install-connectors.sh --profile full
```

Benchmark AppleScript/traversal performance baseline:

```bash
./scripts/benchmark_applescript.sh [group_uuid]
```

This writes a benchmark artifact JSON into `benchmarks/`.
If no CLI argument is provided, the script uses `DEVONTHINK_LINK_BENCHMARK_GROUP_UUID` when set, otherwise it defaults to the Inbox group UUID.

Generated outputs:
- `clients/generated/vscode.mcp.json`
- `clients/generated/codex.config.toml`
- `clients/generated/claude-add.sh`
- `clients/generated/codex-add.sh`

Client guides:
- `docs/client-setup-vscode.md`
- `docs/client-setup-codex.md`
- `docs/client-setup-claude-code.md`

If `codex` or `claude` CLI is missing, use manual config merge from generated files.

## Validation

```bash
python3 -m compileall app main.py
python3 main.py --help
```

## Project Layout

- `app/tools/devonthink_tools.py`: specialized wrappers
- `app/tools/devonthink_dictionary_tools.py`: broad generated command tools
- `app/data/devonthink_command_specs.json`: bundled command spec source
- `catalog-runtime/registry/`: runtime catalog metadata
- `clients/`: connector templates
- `scripts/install-connectors.sh`: connector generator
- `scripts/benchmark_applescript.sh`: AppleScript/traversal benchmark runner
- `skills/devonthink-research-mcp/`: reusable skill package

## License

MIT. See `LICENSE`.
