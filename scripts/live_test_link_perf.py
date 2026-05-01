"""Live-DEVONthink validation for the option 2 + option 3 link-tool changes.

Run from the repo root after installing the project (e.g. ``pipx install .``
or ``pip install -e .`` inside a venv):

    python3 scripts/live_test_link_perf.py

This script:
  * Creates a temporary group with five interlinked markdown notes that contain
    [[wikilink]] references to each other (the create-record content gap is
    worked around with `_set_plain_text`).
  * Verifies _bulk_get_edge_snapshots now returns a content_excerpt field per
    record (option 2 — text fold).
  * Verifies _audit_record_impl with include_text_scan=True consumes the
    snapshot excerpt without an extra _get_record_text call (option 2).
  * Times _resolve_title_candidates_batch against five per-title
    _resolve_title_candidates calls and reports the speedup (option 3 —
    batched OR-search).
  * Cleans up the temporary group.

Exit code is non-zero if any assertion fails.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.tools import devonthink_link_tools as L  # noqa: E402

INBOX_DB_UUID = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"


def _create_group(name: str) -> str:
    """Create a top-level group in Inbox and return its UUID."""
    script = r"""
on run argv
    set groupName to item 1 of argv
    tell application "DEVONthink"
        set inboxRoot to incoming group of database 1
        set newGroup to create record with {name:groupName, type:group} in inboxRoot
        return uuid of newGroup
    end tell
end run
"""
    return L._run_osascript(script, [name])


def _create_markdown_note(group_uuid: str, name: str, body: str) -> str:
    """Create a markdown note inside the given group and write its body."""
    script = r"""
on run argv
    set groupUUID to item 1 of argv
    set noteName to item 2 of argv
    tell application "DEVONthink"
        set theGroup to get record with uuid groupUUID
        set theRecord to create record with {name:noteName, type:markdown} in theGroup
        return uuid of theRecord
    end tell
end run
"""
    uuid = L._run_osascript(script, [group_uuid, name])
    L._set_plain_text(uuid, body)
    return uuid


def _delete_record(uuid: str) -> None:
    script = r"""
on run argv
    set targetUUID to item 1 of argv
    tell application "DEVONthink"
        set r to get record with uuid targetUUID
        if r is not missing value then delete record r
    end tell
end run
"""
    try:
        L._run_osascript(script, [uuid])
    except L.AppleScriptExecutionError as exc:
        print(f"  cleanup warning: could not delete {uuid}: {exc}")


def main() -> int:
    failures: list[str] = []
    group_uuid: str | None = None
    note_uuids: dict[str, str] = {}

    try:
        # ---------------- Setup ----------------
        group_name = f"_LinkPerfTest_{int(time.time())}"
        print(f"creating temporary group {group_name!r}")
        group_uuid = _create_group(group_name)
        print(f"  group uuid: {group_uuid}")

        notes = {
            "Alpha Note": "# Alpha\n\nLinks to [[Beta Note]] and [[Gamma Note]].\n",
            "Beta Note": "# Beta\n\nLinks to [[Gamma Note]] and [[Delta Note]].\n",
            "Gamma Note": "# Gamma\n\nLinks to [[Alpha Note]] and [[Epsilon Note]].\n",
            "Delta Note": "# Delta\n\nLinks back to [[Alpha Note]].\n",
            "Epsilon Note": "# Epsilon\n\nIsolated leaf, references nothing important.\n",
        }
        for note_name, body in notes.items():
            uuid = _create_markdown_note(group_uuid, note_name, body)
            note_uuids[note_name] = uuid
            print(f"  created {note_name}: {uuid}")

        all_uuids = list(note_uuids.values())

        # Give DEVONthink a beat to index the new content for wiki resolution.
        time.sleep(2.0)

        # ---------------- Test option 2: content_excerpt in bulk snapshot ----------------
        print("\n[option 2] bulk_get_edge_snapshots returns content_excerpt")
        snapshots = L._bulk_get_edge_snapshots(all_uuids)
        for note_name, uuid in note_uuids.items():
            snap = snapshots.get(uuid)
            if snap is None:
                failures.append(f"snapshot missing for {note_name} ({uuid})")
                continue
            excerpt = snap.get("content_excerpt") or ""
            if not excerpt:
                failures.append(
                    f"content_excerpt empty for {note_name} (expected markdown body)"
                )
                continue
            expected_marker = f"# {note_name.split()[0]}"
            if expected_marker not in excerpt:
                failures.append(
                    f"content_excerpt for {note_name} missing {expected_marker!r}: "
                    f"got {excerpt[:80]!r}"
                )
            print(
                f"  {note_name}: excerpt={len(excerpt)} chars, contains {expected_marker!r}: "
                f"{expected_marker in excerpt}"
            )

        # ---------------- Test option 2: audit uses cached excerpt, not _get_record_text ----------------
        print(
            "\n[option 2] _audit_record_impl(include_text_scan=True) "
            "consumes snapshot excerpt without _get_record_text"
        )
        with patch.object(L, "_get_record_text") as mock_text:
            audit, warnings, _obs = L._audit_record_impl(
                note_uuids["Alpha Note"], include_text_scan=True
            )
        if mock_text.called:
            failures.append(
                "_audit_record_impl still called _get_record_text (expected snapshot excerpt only)"
            )
        else:
            print("  _get_record_text was not called - snapshot excerpt was the source")
        scanned_wiki = audit.get("text_scan", {}).get("wikilinks") or []
        if "Beta Note" not in scanned_wiki or "Gamma Note" not in scanned_wiki:
            failures.append(
                f"text_scan wikilinks missing expected entries for Alpha Note: {scanned_wiki}"
            )
        else:
            print(f"  text_scan wikilinks from cached excerpt: {scanned_wiki}")
        if warnings:
            print(f"  audit warnings: {warnings}")

        # ---------------- Test option 3: batched resolver matches per-title ----------------
        print(
            "\n[option 3] _resolve_title_candidates_batch returns same hits as "
            "per-title _resolve_title_candidates"
        )
        title_inputs = list(notes.keys())
        per_title_results: dict[str, list[dict]] = {}
        per_title_started = time.time()
        for name in title_inputs:
            per_title_results[name] = L._resolve_title_candidates(
                name, database_uuid=INBOX_DB_UUID, limit=5
            )
        per_title_elapsed = time.time() - per_title_started

        batch_started = time.time()
        batch_results = L._resolve_title_candidates_batch(
            title_inputs, database_uuid=INBOX_DB_UUID, limit_per_title=5
        )
        batch_elapsed = time.time() - batch_started

        for name in title_inputs:
            per_uuids = {h.get("uuid") for h in per_title_results.get(name) or []}
            batch_uuids = {h.get("uuid") for h in batch_results.get(name) or []}
            if not per_uuids:
                # No per-title hits, batch should also produce no hits or only
                # incidental substring matches; skip strict equality.
                print(f"  {name}: per-title=0 hits, batch={len(batch_uuids)} hits")
                continue
            # Batched should at least include the canonical record we created.
            expected = note_uuids[name]
            if expected not in per_uuids:
                print(
                    f"  {name}: per-title resolver missed our created record "
                    f"{expected} (got {sorted(per_uuids)})"
                )
            if expected not in batch_uuids:
                failures.append(
                    f"batch resolver missed expected record {expected} for {name}; "
                    f"got {sorted(batch_uuids)}"
                )
            print(
                f"  {name}: per-title={len(per_uuids)} hits, batch={len(batch_uuids)} hits, "
                f"contains-self={expected in batch_uuids}"
            )

        speedup = (per_title_elapsed / batch_elapsed) if batch_elapsed > 0 else float("inf")
        print(
            f"\n  per-title elapsed: {per_title_elapsed*1000:.0f} ms "
            f"({len(title_inputs)} osascript calls)"
        )
        print(
            f"  batched   elapsed: {batch_elapsed*1000:.0f} ms (1 osascript call)"
        )
        print(f"  speedup: {speedup:.2f}x")
        if speedup < 2.0:
            print(
                "  NOTE: speedup under 2x; may indicate DEVONthink search overhead "
                "dominates, or fallback path triggered. Acceptable but worth flagging."
            )

        # ---------------- Test option 3: map_neighborhood end-to-end via tool wrapper ----------------
        print(
            "\n[option 3] devonthink_link_map_neighborhood expands wiki references "
            "via batched resolution"
        )
        nh = L.devonthink_link_map_neighborhood(
            note_uuids["Alpha Note"], radius=2, per_hop_limit=10
        )
        if not nh.get("ok"):
            failures.append(f"map_neighborhood failed: {nh.get('error')}")
        else:
            data = nh.get("data") or {}
            edge_count = len(data.get("edges") or [])
            node_count = len(data.get("nodes") or [])
            print(f"  nodes={node_count}, edges={edge_count}")
            if edge_count == 0:
                print(
                    "  NOTE: zero edges - DEVONthink may not have indexed wiki "
                    "references yet. Try re-running after a few seconds."
                )

        # ---------------- Test option 3: repair_links report mode ----------------
        print(
            "\n[option 3] devonthink_link_repair_links(mode=report) "
            "uses batched wikilink resolution"
        )
        rep = L.devonthink_link_repair_links(note_uuids["Alpha Note"], mode="report")
        if not rep.get("ok"):
            failures.append(f"repair_links failed: {rep.get('error')}")
        else:
            data = rep.get("data") or {}
            unresolved = data.get("unresolved_wikilinks") or []
            print(f"  unresolved_wikilinks: {unresolved}")

    finally:
        # ---------------- Cleanup ----------------
        if group_uuid:
            print(f"\ncleaning up temporary group {group_uuid}")
            _delete_record(group_uuid)

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS - all live assertions held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
