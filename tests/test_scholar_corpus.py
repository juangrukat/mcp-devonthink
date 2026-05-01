"""Integration tests against the Scholar Corpus test fixture in DEVONthink.

The fixture lives in Inbox / MCP Test - Scholar Corpus and consists of 12
records across three groups.  Tests cover all canonical MCP tools and report
pass/fail + timing so bottlenecks are visible at a glance.

Run:
    python -m pytest tests/test_scholar_corpus.py -v
or:
    python tests/test_scholar_corpus.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.devonthink_tools import (
    devonthink_get_database_by_uuid,
    devonthink_get_database_incoming_group,
    devonthink_get_record_by_uuid,
    devonthink_list_group_children,
    devonthink_search_records,
    devonthink_create_record,
)
from app.tools.devonthink_link_tools import (
    devonthink_link_resolve,
    devonthink_link_audit_record,
    devonthink_link_audit_folder,
    devonthink_link_map_neighborhood,
    devonthink_link_find_orphans,
    devonthink_link_suggest_related,
    devonthink_link_score,
    devonthink_link_detect_bridges,
    devonthink_link_check_reciprocal,
)

# ---------------------------------------------------------------------------
# Fixture UUIDs (created 2026-04-23 in Inbox database)
# ---------------------------------------------------------------------------

DB_INBOX = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"

GROUPS = {
    "root":         "180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B",
    "concordance":  "B112E1A5-2C97-49B0-AABF-738074779AE6",
    "archive":      "1627579C-2B82-40D2-967C-E1B7923F20FB",
    "chronological":"5DF10BDC-CCDE-45AD-B929-DDAFD230D5D1",
}

RECORDS = {
    # Concordance group
    "c1_concordance_methods":  "434CC4D5-FF64-47CA-A412-3C090943CF9C",
    "c2_rare_term_weighting":  "8430F748-D4E3-43E3-BEA3-4ECE3C8D069B",
    "c3_corpus_management":    "85E11430-B331-419C-950A-BAD35DBC49F4",
    "c4_workflow_notes":       "42579007-8F4C-4C5B-8B31-9BC29D78DE49",
    # Archive group
    "a1_batch_import":         "8005A2A7-1FD5-4F8C-9BB0-C2EC2BE81DED",
    "a2_ocr_protocol":         "B7357CDA-5972-4173-B0E1-160CC61E9BEA",
    "a3_citation_logic":       "A6A558D1-20EF-4BCD-A75D-82DC9BB2CBBB",
    "a4_finding_aid":          "A0AD2CA6-5CF5-4FE7-A807-2283EE3C3E27",
    # Chronological group
    "ch1_2023_concordance":    "7D0EBC6E-1FB6-41E9-B245-362F0A6F5BC8",
    "ch2_2023_florence":       "9BD3AC08-B6E0-4620-B85B-AAF8459B6B88",
    "ch3_2024_smart_groups":   "0872BDCD-B291-4529-852D-8AF8B9FFD992",
    "ch4_2024_corpus_review":  "A1037B82-464F-4B5F-BC77-FCAA2715D3EE",
}

# ---------------------------------------------------------------------------
# Minimal test harness (works standalone or under pytest)
# ---------------------------------------------------------------------------

_results: list[dict[str, Any]] = []


def _run(name: str, fn, *args, **kwargs):
    """Run a tool call expecting ok=True."""
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        ok = bool(result.get("ok") if isinstance(result, dict) else result)
        _results.append({"name": name, "ok": ok, "ms": elapsed_ms, "result": result})
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name:60s} {elapsed_ms:7.0f}ms")
        return result
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        _results.append({"name": name, "ok": False, "ms": elapsed_ms, "error": str(exc)})
        print(f"  [ERR ] {name:60s} {elapsed_ms:7.0f}ms  — {exc}")
        return None


def _run_expect_error(name: str, fn, *args, **kwargs):
    """Run a tool call expecting ok=False (validation / error path)."""
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        returned_error = isinstance(result, dict) and not result.get("ok")
        _results.append({"name": name, "ok": returned_error, "ms": elapsed_ms, "result": result})
        status = "PASS" if returned_error else "FAIL"
        suffix = "" if returned_error else f"  — expected ok=False, got ok=True"
        print(f"  [{status}] {name:60s} {elapsed_ms:7.0f}ms{suffix}")
        return result
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        _results.append({"name": name, "ok": False, "ms": elapsed_ms, "error": str(exc)})
        print(f"  [ERR ] {name:60s} {elapsed_ms:7.0f}ms  — {exc}")
        return None


def _assert(name: str, condition: bool, detail: str = ""):
    ok = bool(condition)
    _results.append({"name": name, "ok": ok, "ms": 0.0})
    status = "PASS" if ok else "FAIL"
    suffix = f"  — {detail}" if detail and not ok else ""
    print(f"  [{status}] {name:60s}{suffix}")


# ---------------------------------------------------------------------------
# Test sections
# ---------------------------------------------------------------------------

def test_basic_tools():
    print("\n=== BASIC TOOLS ===")

    r = _run("get_database_by_uuid (Inbox)", devonthink_get_database_by_uuid, DB_INBOX)
    if r:
        _assert("  database name is 'Inbox'", r.get("data", {}).get("name") == "Inbox")

    r = _run("get_database_incoming_group (Inbox)", devonthink_get_database_incoming_group, DB_INBOX)
    if r:
        _assert("  incoming group has uuid", bool(r.get("data", {}).get("uuid")))

    r = _run("get_record_by_uuid (c1 concordance methods)",
             devonthink_get_record_by_uuid, RECORDS["c1_concordance_methods"])
    if r:
        _assert("  record name matches", "Concordance" in (r.get("data", {}).get("name") or ""))

    r = _run("list_group_children (root group)", devonthink_list_group_children, GROUPS["root"], 25)
    if r:
        _assert("  root group children found", (r.get("count") or 0) >= 1)

    r = _run("get_record_by_uuid with db scope",
             devonthink_get_record_by_uuid, RECORDS["a1_batch_import"], DB_INBOX)
    if r:
        _assert("  scoped lookup returns record", r.get("ok") is True)

    _run_expect_error("get_record_by_uuid (invalid uuid)",
                      devonthink_get_record_by_uuid, "00000000-0000-0000-0000-000000000000")

    _run_expect_error("get_database_by_uuid (invalid uuid)",
                      devonthink_get_database_by_uuid, "00000000-0000-0000-0000-000000000000")

    _run_expect_error("get_record_by_uuid (empty string)", devonthink_get_record_by_uuid, "")


def test_search():
    print("\n=== SEARCH ===")

    r = _run("search 'concordance'", devonthink_search_records, "concordance", 25, DB_INBOX)
    if r:
        _assert("  at least 3 results", (r.get("count") or 0) >= 3,
                f"got {r.get('count')}")

    r = _run("search 'palimpsest' (rare term)", devonthink_search_records, "palimpsest", 10, DB_INBOX)
    if r:
        _assert("  rare term finds at least 1 result", (r.get("count") or 0) >= 1,
                f"got {r.get('count')}")

    r = _run("search 'florence archive'", devonthink_search_records, "florence archive", 25, DB_INBOX)
    if r:
        _assert("  florence+archive finds results", (r.get("count") or 0) >= 1)

    r = _run("search with limit=5", devonthink_search_records, "archive", 5, DB_INBOX)
    if r:
        _assert("  limit respected", (r.get("count") or 0) <= 5)

    r = _run("search 'OCR quality control'", devonthink_search_records, "OCR quality control", 25, DB_INBOX)
    if r:
        _assert("  OCR protocol found", (r.get("count") or 0) >= 1)

    r = _run("search tag:concordance", devonthink_search_records, "tag:concordance", 25, DB_INBOX)
    if r:
        _assert("  tag search returns results", (r.get("count") or 0) >= 1)

    _run_expect_error("search empty query (validation)", devonthink_search_records, "", 10)
    _run_expect_error("search limit=0 (validation)", devonthink_search_records, "test", 0)
    _run_expect_error("search limit=201 (validation)", devonthink_search_records, "test", 201)

    r = _run("search '2023.01.10' (date-format name)", devonthink_search_records, "2023.01.10", 25, DB_INBOX)
    if r:
        _assert("  date-format named file found", (r.get("count") or 0) >= 1)

    r = _run("search scoped to concordance group",
             devonthink_search_records, "weighted word frequency", 10, GROUPS["concordance"])
    if r:
        _assert("  group-scoped search returns results", (r.get("count") or 0) >= 1)


def test_create_record():
    print("\n=== CREATE RECORD ===")

    r = _run("create_record (markdown in root group)",
             devonthink_create_record,
             "MCP Test - Ephemeral Note", "markdown", GROUPS["root"])
    created_uuid = None
    if r and r.get("ok"):
        data = r.get("data") or {}
        created_uuid = data.get("uuid")
        _assert("  created record has uuid", bool(created_uuid))
        _assert("  created record has name", bool(data.get("name")))

    r = _run("create_record (no group, inbox default)",
             devonthink_create_record, "MCP Test - No Group", "txt")
    if r and r.get("ok"):
        data = r.get("data") or {}
        _assert("  no-group record has uuid", bool(data.get("uuid")))

    r = _run("create_record (alias plain text -> txt)",
             devonthink_create_record, "MCP Test - Alias Plain Text", "plain text", GROUPS["root"])
    if r and r.get("ok"):
        _assert("  alias record created", bool((r.get("data") or {}).get("uuid")))

    _run_expect_error("create_record (empty name)", devonthink_create_record, "", "markdown")
    _run_expect_error("create_record (empty type)", devonthink_create_record, "Test Record", "")
    _run_expect_error("create_record (invalid group uuid)",
                      devonthink_create_record, "Test", "markdown", "00000000-0000-0000-0000-000000000000")


def test_link_resolve():
    print("\n=== LINK RESOLVE ===")

    r = _run("link_resolve (c1 by uuid)", devonthink_link_resolve,
             RECORDS["c1_concordance_methods"])
    if r:
        _assert("  resolve returns ok=True", r.get("ok") is True)
        # data shape: {"record": {uuid, ...}, "canonical": "x-devonthink-item://..."}
        _assert("  resolve has data.record.uuid",
                bool(((r.get("data") or {}).get("record") or {}).get("uuid")))

    r = _run("link_resolve (item link scheme)",
             devonthink_link_resolve,
             f"x-devonthink-item://{RECORDS['a1_batch_import']}")
    if r:
        _assert("  item link resolves ok", r.get("ok") is True)

    _run_expect_error("link_resolve (invalid ref)", devonthink_link_resolve,
                      "00000000-0000-0000-0000-000000000000")


def test_link_audit_record():
    print("\n=== LINK AUDIT RECORD ===")

    r = _run("audit_record c1 (concordance methods)",
             devonthink_link_audit_record, RECORDS["c1_concordance_methods"])
    if r:
        _assert("  audit ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        # edges shape: {"incoming": [...], "outgoing": [...], "wikilinks": [...]}
        _assert("  audit has edges dict", isinstance(data.get("edges"), dict))

    r = _run("audit_record c3 (corpus management, has wikilinks)",
             devonthink_link_audit_record, RECORDS["c3_corpus_management"], True)
    if r:
        data = r.get("data") or {}
        edges = data.get("edges") or {}
        wikilinks = edges.get("wikilinks") or []
        _assert("  wikilinks list present", isinstance(wikilinks, list))

    r = _run("audit_record a2 (OCR protocol, has wikilinks)",
             devonthink_link_audit_record, RECORDS["a2_ocr_protocol"], True)
    if r:
        _assert("  audit ok=True", r.get("ok") is True)


def test_link_audit_folder():
    print("\n=== LINK AUDIT FOLDER ===")

    r = _run("audit_folder concordance group",
             devonthink_link_audit_folder, GROUPS["concordance"], 50)
    if r:
        _assert("  folder audit ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        _assert("  audited_count present", "audited_count" in data)

    r = _run("audit_folder archive group",
             devonthink_link_audit_folder, GROUPS["archive"], 50)
    if r:
        _assert("  archive audit ok=True", r.get("ok") is True)

    r = _run("audit_folder root group (all 12 records)",
             devonthink_link_audit_folder, GROUPS["root"], 50)
    if r:
        data = r.get("data") or {}
        # data shape: {folder, audited_count, weakly_connected, tag_clusters, records, link_coverage}
        _assert("  audited_count > 0", int(data.get("audited_count") or 0) > 0,
                f"audited_count={data.get('audited_count')}")


def test_link_map_neighborhood():
    print("\n=== LINK MAP NEIGHBORHOOD ===")

    r = _run("map_neighborhood c1 radius=1",
             devonthink_link_map_neighborhood, RECORDS["c1_concordance_methods"], 1, 20)
    if r:
        _assert("  neighborhood ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        _assert("  nodes list present", "nodes" in data)

    r = _run("map_neighborhood a3 (citation logic, has wikilinks) radius=2",
             devonthink_link_map_neighborhood, RECORDS["a3_citation_logic"], 2, 15)
    if r:
        _assert("  neighborhood ok=True", r.get("ok") is True)


def test_link_find_orphans():
    print("\n=== LINK FIND ORPHANS ===")

    r = _run("find_orphans in root group",
             devonthink_link_find_orphans, GROUPS["root"], 100)
    if r:
        _assert("  find_orphans ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        orphans = data.get("orphans") or []
        _assert("  orphans list is a list", isinstance(orphans, list))
        # c4 and a4 have no wikilinks so may appear as orphans
        print(f"         orphan count: {len(orphans)}")

    r = _run("find_orphans in chronological group",
             devonthink_link_find_orphans, GROUPS["chronological"], 50)
    if r:
        _assert("  chrono orphans ok=True", r.get("ok") is True)


def test_link_suggest_related():
    print("\n=== LINK SUGGEST RELATED ===")

    r = _run("suggest_related c1 (concordance methods)",
             devonthink_link_suggest_related, RECORDS["c1_concordance_methods"], 15)
    if r:
        _assert("  suggest ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        suggestions = data.get("suggestions") or []
        _assert("  suggestions list present", isinstance(suggestions, list))
        print(f"         suggestion count: {len(suggestions)}")

    r = _run("suggest_related a1 (archive batch import)",
             devonthink_link_suggest_related, RECORDS["a1_batch_import"], 10)
    if r:
        _assert("  a1 suggest ok=True", r.get("ok") is True)


def test_link_score():
    print("\n=== LINK SCORE ===")

    pair = [RECORDS["c1_concordance_methods"], RECORDS["c2_rare_term_weighting"]]
    r = _run("score c1+c2 (concordance pair)", devonthink_link_score, pair)
    if r:
        _assert("  score ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        _assert("  score value present", "scores" in data)

    trio = [RECORDS["a1_batch_import"], RECORDS["a2_ocr_protocol"], RECORDS["a3_citation_logic"]]
    r = _run("score archive trio", devonthink_link_score, trio)
    if r:
        _assert("  trio score ok=True", r.get("ok") is True)

    r = _run("score single record (edge case)", devonthink_link_score,
             [RECORDS["ch4_2024_corpus_review"]])
    _assert("  single-record score is graceful", r is not None)

    _run_expect_error("score empty list (validation)", devonthink_link_score, [])


def test_link_detect_bridges():
    print("\n=== LINK DETECT BRIDGES ===")

    r = _run("detect_bridges in root group",
             devonthink_link_detect_bridges, GROUPS["root"], 80)
    if r:
        _assert("  detect_bridges ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        bridges = data.get("bridges") or []
        _assert("  bridges list present", isinstance(bridges, list))
        print(f"         bridge count: {len(bridges)}")

    r = _run("detect_bridges concordance group",
             devonthink_link_detect_bridges, GROUPS["concordance"], 50)
    if r:
        _assert("  concordance bridges ok=True", r.get("ok") is True)


def test_link_check_reciprocal():
    print("\n=== LINK CHECK RECIPROCAL ===")

    # c3 wikilinks to c1; c1 wikilinks to c3 — should detect reciprocal
    r = _run("check_reciprocal c1 <-> c3",
             devonthink_link_check_reciprocal,
             RECORDS["c1_concordance_methods"],
             RECORDS["c3_corpus_management"])
    if r:
        _assert("  check_reciprocal ok=True", r.get("ok") is True)
        data = r.get("data") or {}
        # data shape: {source, target, source_points_to_target, target_reports_source_incoming, consistent}
        _assert("  consistent field present", "consistent" in data)

    # a2 and a3 both link each other via wikilinks
    r = _run("check_reciprocal a2 <-> a3",
             devonthink_link_check_reciprocal,
             RECORDS["a2_ocr_protocol"],
             RECORDS["a3_citation_logic"])
    if r:
        _assert("  a2<->a3 reciprocal ok=True", r.get("ok") is True)

    # unrelated pair
    r = _run("check_reciprocal unrelated c4 <-> ch4",
             devonthink_link_check_reciprocal,
             RECORDS["c4_workflow_notes"],
             RECORDS["ch4_2024_corpus_review"])
    if r:
        _assert("  unrelated pair is graceful", r.get("ok") is True)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary():
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    failed = total - passed
    timed = [r for r in _results if r.get("ms", 0) > 0]
    if timed:
        slowest = sorted(timed, key=lambda x: -x["ms"])[:5]
        print("\n--- TOP 5 SLOWEST CALLS ---")
        for r in slowest:
            print(f"  {r['ms']:7.0f}ms  {r['name']}")
    print(f"\n{'='*70}")
    print(f"TOTAL: {total}   PASSED: {passed}   FAILED: {failed}")
    print(f"{'='*70}")
    if failed:
        print("\nFAILED TESTS:")
        for r in _results:
            if not r["ok"]:
                err = r.get("error") or ""
                result = r.get("result") or {}
                detail = err or result.get("error") or ""
                print(f"  ✗ {r['name']}" + (f"  — {detail}" if detail else ""))
    return failed


def run_all():
    print("DEVONthink MCP — Scholar Corpus Integration Tests")
    print("=" * 70)
    test_basic_tools()
    test_search()
    test_create_record()
    test_link_resolve()
    test_link_audit_record()
    test_link_audit_folder()
    test_link_map_neighborhood()
    test_link_find_orphans()
    test_link_suggest_related()
    test_link_score()
    test_link_detect_bridges()
    test_link_check_reciprocal()
    return _print_summary()


# ---------------------------------------------------------------------------
# pytest compatibility
# ---------------------------------------------------------------------------

# The Scholar Corpus fixture is a hand-built DEVONthink group with 12 stable
# UUIDs (see RECORDS/GROUPS above). It has been deleted from this database and
# cannot be regenerated automatically — DEVONthink mints fresh UUIDs on every
# create. We keep the constants exported because tests/benchmarks import them,
# but skip the live tests at runtime when the corpus is gone, so the suite stays
# green without papering over the missing fixture.
def _corpus_present() -> bool:
    probe = devonthink_get_record_by_uuid(GROUPS["root"])
    return bool(probe and probe.get("ok"))


_skip_if_no_corpus = pytest.mark.skipif(
    not _corpus_present(),
    reason=(
        "Scholar Corpus fixture group not present in DEVONthink "
        f"(expected group UUID {GROUPS['root']}). Recreate the corpus to enable."
    ),
)


@_skip_if_no_corpus
def test_basic_tools_pytest():
    test_basic_tools()
    failed = [
        r for r in _results
        if (not r["ok"]) and ("get_database" in r["name"] or "get_record" in r["name"])
    ]
    assert not failed, failed


@_skip_if_no_corpus
def test_search_pytest():
    test_search()


@_skip_if_no_corpus
def test_create_record_pytest():
    test_create_record()


@_skip_if_no_corpus
def test_link_tools_pytest():
    test_link_resolve()
    test_link_audit_record()
    test_link_audit_folder()
    test_link_map_neighborhood()
    test_link_find_orphans()
    test_link_suggest_related()
    test_link_score()
    test_link_detect_bridges()
    test_link_check_reciprocal()


if __name__ == "__main__":
    sys.exit(0 if run_all() == 0 else 1)
