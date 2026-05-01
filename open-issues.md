# Open Issues

Found during integration testing against the Scholar Corpus fixture (2026-04-23).
Test file: `tests/test_scholar_corpus.py`

---

## Product Gap Status · Tier 1 / Tier 2 MCP Coverage

- [product gap -> in progress] `create-smart-group`
- [product gap -> in progress] `list-databases` / `open-database` / `close-database`
- [product gap -> in progress] `set-label` / `set-rating` / `batch-set-label`
- [product gap -> in progress] `duplicate-record`
- [product gap -> in progress] `list-reminders` / `delete-reminder` / `update-reminder`
- [product gap -> in progress] `list-smart-rules` / `apply-smart-rule` (Tier 2)
- [product gap -> in progress] Script CRUD: `list-scripts`, `run-script`, `create-script`, `read-script`, `update-script`, `delete-script`
- [product gap -> in progress] RTF CRUD: `create-rtf`, `read-rtf`, `update-rtf`
- [product gap -> implemented] Attached annotation notes: `create-annotation-note`, `read-annotation-note`
  Clarification: this is DEVONthink's record-level `annotation` property backed by a note in the database Annotations group, not PDF-internal highlights/comments/markup.
- [product gap -> implemented] `summarize-annotations` wrapper.
  Correct syntax is `summarize annotations of records {list} to markdown in destination`; returns missing value for annotation-note-only records, which is expected and surfaced as an observability warning.
- [research required] `list-versions` / `restore-version` (DT4 versioning API not yet audited)
- [research required] PDF-internal annotation/markup creation (format-dependent; separate from attached annotation notes)

---

## Platform Bugs / DEVONthink API Limitations

- [DEVONthink/API limitation] `performSmartRule`: `get custom meta data` can fail from smart rule context while working from Script Editor/Scripts menu.
  Affects `devonthink-apply-smart-rule` when user-defined rules contain that command. Status: unresolved upstream, DT 4.2.2 + macOS 26.3, April 2026.
- [DEVONthink/API limitation] AppleScript performance regression in DT 4.2 / macOS 26 TCC.
  Mitigation: keep Apple Event counts low, prefer bulk wrappers, and surface timing in observability.
- [DEVONthink/API limitation] `set color` silently no-ops on non-group/non-tag records.
  Mitigation: MCP label tools use `label`, not `color`, and verify the applied value after setting so no-ops become explicit errors.
- [DEVONthink/API limitation] root of database can return class mismatch in DT4.
  Mitigation: keep using `incoming group` as the database root/Inbox proxy.
- [DEVONthink/API limitation] DEVONthink Scripts menu can randomly disable scripts in DT4.
  Mitigation: MCP tools invoke `/usr/bin/osascript` via subprocess/stdin and do not depend on DEVONthink-side script menu execution.
- [Apple/macOS limitation] macOS 26 TCC regressions affect inter-app AppleScript bridges such as Reminders.
  Risk: any future tool that automates Apple Reminders directly should degrade clearly on timeout or `-10004`/TCC failures.
- [DEVONthink/API limitation] RTFD binary attachments are not constructable via AppleScript alone.
  Workaround: MCP RTFD tools handle text/rich-text properties only; embedded binary attachments require AppKit/NSData.

---

## Server Implementation Gaps / Fixes

- [tool implementation bug -> in progress] `classify` command requires explicit `tags` argument in DT4.
  Mitigation: generated `devonthink-classify` calls now default `tags false` unless the caller provides `tags`.
- [tool implementation bug -> in progress] custom metadata numeric `0` is silently ignored by DEVONthink.
  Mitigation: generated `devonthink-add-custom-meta-data` rejects `direct=0` for numeric formats with an observability warning.
- [product gap] `devonthink-link-convert-item-links-to-wikilinks`.
  Inverse of DEVONthink's wikilink-to-item-link conversion; can reuse full content scan and record lookup paths.
- [product gap] `devonthink-bulk-create-reminders`.
  Batch reminder creation in one AppleScript pass to avoid per-reminder Apple Event overhead.
- [product gap -> in progress] Script CRUD and execution.
  Uses filesystem-backed tools over `~/Library/Application Scripts/com.devon-technologies.think` and legacy DEVONthink script folders; execution uses `/usr/bin/osascript`.
- [product gap -> in progress] RTF CRUD and partial RTFD text support.
  RTF creation passes rich text at creation time to avoid DEVONthink's missing-value behavior; RTFD binary attachment support remains a platform limitation.
- [tool implementation bug -> fixed] raw `devonthink-search` `in current database` / database UUID scope can hit `-50`.
  Mitigation: raw search now resolves UUID scope through a group-or-database incoming-group helper; `devonthink-search-records` remains the preferred structured wrapper.
- [tool implementation bug -> fixed] duplicate source must be resolved before duplicate calls.
  Mitigation: `devonthink-duplicate-record` uses `set theRecord to get record with uuid ...` and `duplicate record theRecord to destinationGroup`; lint catches unresolved generated duplicate patterns.
- [confirmed caveat] `create-rtfd` can silently downgrade to `RTF` without binary attachment data.
  Mitigation: response includes `actual_type` and emits `rtfd_downgraded_to_rtf` when DEVONthink returns RTF.
- [confirmed working] `create-annotation-note` works on PDFs, groups, smart groups, and annotation-note records.
  Replace guard verified: `replace_existing=false` fails clearly when a note exists; `replace_existing=true` updates the attached annotation pointer.
- [live fixture] `MCP Chaos Lab 20260424-080344` (`3EFAAB4A-5BCD-4699-A472-1F66EF3C7882`) is retained as an edge-case stress fixture.

---

## ~~BUG-001~~ FIXED · `record_json` in `devonthink_tools.py` — uuid / location / url always null

**Severity**: High — affects `devonthink-get-record-by-uuid` and `devonthink-create-record`

**Symptom**: The fields `uuid`, `location`, and `url` are always `null` in the response, even
though the record exists and DEVONthink can return those values via raw AppleScript.

**Root cause**: `_DEVONTHINK_JSON_HELPERS` in `devonthink_tools.py` does not include the
`using terms from application "DEVONthink"` directive at the top. The handlers (`record_json`,
`database_json`) are called via `my handler()` from inside a `tell application "DEVONthink"`
block, which routes execution back to script scope. In script scope, without the `using terms`
directive, AppleScript cannot resolve DEVONthink-specific properties like `uuid`, `location`,
and `reference URL` — the `try` blocks silently swallow the error and leave those fields as
`missing value`.

The link tools (`devonthink_link_tools.py`) use `_JSON_HELPERS` which opens with
`using terms from application "DEVONthink"` and correctly return uuid.

**Fix**: Add `using terms from application "DEVONthink"` as the first line of
`_DEVONTHINK_JSON_HELPERS` in `devonthink_tools.py`.

```python
_DEVONTHINK_JSON_HELPERS = r'''
using terms from application "DEVONthink"
on escape_json(s)
...
```

---

## ~~BUG-002~~ FIXED · `devonthink-search-records` — crashes with DEVONthink error -50 when scoped to a database UUID

**Severity**: High — tool parameter `database_uuid` is documented but silently broken for database UUIDs

**Symptom**: Passing a database UUID (e.g. the Inbox database UUID) to `database_uuid` returns
`{"ok": false, "error": "...execution error: DEVONthink got an error: Invalid argument in (-50)"}`.

**Root cause**: The AppleScript `search searchQuery in theDatabase` is called after
`set theDatabase to get database with uuid databaseUUID`. DEVONthink's `search` command only
accepts a *record/group* as the `in` argument, not a database object. Passing a database object
causes the `-50` Invalid argument error.

The parameter works correctly when a *group* UUID is passed (which is what the internal
benchmark scripts use). The parameter name `database_uuid` is therefore misleading — it must
actually be a group UUID.

**Observed**: Searching with a group UUID (the concordance group) works. Searching with no scope
also works (global search). Only database-object scoping fails.

**Fix options**:
1. Rename the parameter to `scope_uuid` (accepts group UUID only) and update the tool description.
2. Auto-detect whether the UUID is a database (by calling `get database with uuid`) and if so,
   fetch the database's root group and use that as the search scope.
3. Both: rename for clarity and add auto-resolution of database UUIDs to their root group.

**Immediate mitigation**: Update the tool description to say "group UUID" not "database UUID".

---

## ~~BUG-003~~ FIXED · `devonthink-create-record` — invalid `group_uuid` silently succeeds instead of returning error

**Severity**: Medium — silent data placement, no user feedback

**Symptom**: Calling `devonthink_create_record("test", "txt", "00000000-0000-0000-0000-000000000000")`
returns `{"ok": true, ...}` and creates a record, even though the group UUID doesn't exist.

**Root cause**: DEVONthink's `get record with uuid "00000000-..."` returns `missing value` rather
than raising an AppleScript error. The script then calls
`create record with {...} in missing value`. DEVONthink interprets `in missing value` as "create
in the default location" (global inbox), so a record is created silently in the wrong place.

**Fix**: Add an explicit guard before the create call in the AppleScript:
```applescript
set destinationGroup to get record with uuid groupUUID
if destinationGroup is missing value then
    error "Group not found: " & groupUUID
end if
set createdRecord to create record with {name:recordName, record type:recordType} in destinationGroup
```

---

## BUG-004 · `devonthink-link-audit-record` — `get links of` command fails with -1708 on all tested records

**Severity**: Medium — the links-of command is silently degraded to wikilink/mention fallback

**Symptom**: Every audit produces this warning:
> `Could not read links via DEVONthink command: execution error: DEVONthink got an error: content id X of database id Y doesn't understand the "get links of" message. (-1708)`

**Root cause**: The `get links of` AppleScript command (which would return explicit DEVONthink item
links stored in the record) is unsupported for `markdown` and `txt` record types in the tested
DEVONthink version (4.1.1). Error -1708 means the object doesn't understand the message.
The audit tool degrades gracefully to wikilink scanning, but the link graph misses explicit
item links that would normally be the highest-confidence signal.

**Action**: Confirm whether `get links of` works for PDF or rich-text records. If it is
universally broken in DT 4.1.1, add a suppression path in `_get_links_of` to skip the call for
known-unsupported types and avoid polluting observability warnings.

---

## DX-001 · Response shape inconsistency between `devonthink_tools.py` and `devonthink_link_tools.py`

**Severity**: Low — makes calling code harder to write

**Symptom**: The two tool families return structurally different response shapes:

- `devonthink_tools.py` returns flat `{"ok": bool, "data": {flat record}}` with no `tool`,
  `contract`, `observability`, or `inputs` envelope.
- `devonthink_link_tools.py` returns `{"ok": bool, "tool": "...", "contract": {...},
  "observability": {...}, "inputs": {...}, "data": {...}}`.

This forces calling code (and tests) to handle two different patterns for what are logically
the same kinds of operations (record lookup, search).

**Note discovered during test writing**: Because the basic tools lack the `tool` envelope, it is
easy to confuse which tool produced a given response when debugging multi-tool workflows.

**Recommendation**: Align `devonthink_tools.py` to use the same envelope as link tools, or at
minimum add a `"tool"` key to basic tool responses.

---

## DX-002 · `devonthink-search-records` parameter named `database_uuid` but semantics require group UUID

**Severity**: Low — naming confusion leads to usage errors (see BUG-002)

Documented as part of BUG-002, but listed separately as a standalone DX issue because
renaming the parameter would be the primary DX fix even if the database-UUID auto-resolution
is also implemented.

---

## DX-003 · `devonthink_link_audit_record` — `edges` field is a dict, not a list

**Severity**: Low — documentation / discoverability issue

**Symptom**: The `data.edges` field returned by `devonthink-link-audit-record` is a dict with
keys `incoming`, `outgoing`, and `wikilinks` — each of which is a list. Callers expecting a
flat list of edge objects will get a `str has no attribute get` error if they iterate
`data.edges` directly.

**Note**: The tool's behavior is internally consistent; this is purely a documentation issue.
The shape should be documented in the tool description so callers know to access
`data.edges.incoming`, `data.edges.outgoing`, and `data.edges.wikilinks`.

---

## PERF-001 · Folder-level link operations are slow at scale

**Measured (12-record Scholar Corpus, 2026-04-24)**:

| Tool | Time |
|---|---|
| `audit_record` (single) | ~1 s |
| `audit_folder` (4 records) | ~4 s |
| `audit_folder` (12 records) | ~7.7 s |
| `find_orphans` (12 records) | ~8.9 s |
| `map_neighborhood` radius=2 | ~4.7 s |
| `detect_bridges` (12 records) | ~15.5 s |
| `check_reciprocal` (pair) | ~3.2 s |
| `score` (pair) | ~2.7 s |

**Bottleneck**: Each record audit issues one or more DEVONthink full-text `search` calls to build
concordance/mention signals. At the folder level these multiply: 12 records × ~1 s = ~12 s minimum.
`detect_bridges` is worst because it traverses all records then scores pairs.

**Ideas**:
- Batch title searches: combine multiple title queries into a single boolean `OR` search.
- Cache search results within a single `audit_folder` or `traverse_folder` call.
- Add a `fast` mode to `audit_folder` that skips concordance/mention signals and only
  runs wikilink + tag signals (much cheaper).
- Investigate whether the `search` command has a batched form in DT's AppleScript dictionary.

---

## ~~TEST-001~~ FIXED · Test file must be run from project root (no `tests/__init__.py`)

**Symptom**: `python tests/test_scholar_corpus.py` requires the project root on `sys.path`.
The test file handles this via `sys.path.insert(0, ...)` but pytest requires being invoked
from the project root.

**Fix**: Add `tests/__init__.py` and a `[tool.pytest.ini_options] testpaths = ["tests"]`
entry to `pyproject.toml`, or use `pytest-pythonpath`.

---

## TEST-002 · `devonthink_create_record` returns type `record type` not validated

**Symptom**: Passing an unsupported `record_type` string (e.g. `"plain text"` instead of `"txt"`)
causes DEVONthink to silently fail record creation and return `missing value`, which then
triggers the `-1728` UUID error. The error message exposed to the caller is opaque.

**Fix**: Validate `record_type` against a known set (`"markdown"`, `"txt"`, `"rtf"`, `"group"`, etc.)
before issuing the AppleScript, and return a clear `{"ok": false, "error": "..."}` message.

---

## ~~PERF-002~~ FIXED · `devonthink-link-suggest-related` returned 0 suggestions — AND vs OR semantics bug in tag lookup

**Root cause (confirmed from source)**: Three compounding failures produced 0 candidates:

1. `get links of` raises -1708 for markdown/txt records (BUG-004) → no authoritative edges.
2. `_lookup_records_with_tags(all_tags)` passes every tag at once to DEVONthink's
   `lookup records with tags tagList`, which uses **AND semantics** — only records possessing
   every tag in the list are returned. The source record is the only record that has all three
   of its own tags, so the lookup returns only the source record itself, then filters it out
   via `uuid == own_uuid`. Zero candidates from tag signals.
3. `_resolve_title_candidates` finds only the source document by exact title → also filtered out.

**Fix applied**: Changed the shared-tag loop in `devonthink_link_suggest_related` to query one
tag at a time (OR semantics) and deduplicate results by UUID using a `seen_tag_uuids` set.
Records are scored by the count of overlapping tags via the `overlap.intersection` logic
already in place.

**Result**: 4 suggestions returned for c1 (Textual Concordance Methods) on the 12-record corpus.

**Note**: The README does not document `lookup records with tags` AND semantics or
`suggest_related` thresholds. The bug was implementation-level, not corpus-size-dependent.

---

## DISCOVERY · `record type:plain text` vs `record type:txt` in AppleScript

During test fixture creation, `create record with {name:..., record type:plain text, ...}` caused
the execution to fail with `-1728` (uuid of missing value). The correct AppleScript type name for
a plain-text note is `txt`, not `plain text`. This burned test setup time and confirms TEST-002
above — the tool needs to validate or document the accepted type strings.
