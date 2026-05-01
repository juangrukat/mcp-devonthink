#!/usr/bin/env python3
"""Audit the DEVONthink MCP tool surface for execution latency."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tools.devonthink_dictionary_tools import _build_command_call, get_dictionary_command_specs
from app.tools.devonthink_link_tools import (
    devonthink_link_audit_folder,
    devonthink_link_audit_record,
    devonthink_link_check_reciprocal,
    devonthink_link_detect_bridges,
    devonthink_link_find_orphans,
    devonthink_link_map_neighborhood,
    devonthink_link_resolve,
    devonthink_link_score,
    devonthink_link_suggest_related,
)
from app.tools.devonthink_tools import (
    devonthink_create_record,
    devonthink_get_database_by_uuid,
    devonthink_get_database_incoming_group,
    devonthink_get_record_by_uuid,
    devonthink_list_group_children,
    devonthink_search_records,
)


DB_INBOX = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"
GROUP_ROOT = "180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"
GROUP_CONCORDANCE = "B112E1A5-2C97-49B0-AABF-738074779AE6"
RECORD_A = "434CC4D5-FF64-47CA-A412-3C090943CF9C"
RECORD_B = "8430F748-D4E3-43E3-BEA3-4ECE3C8D069B"


def _run_live(name: str, fn, *args, **kwargs) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        observability = result.get("observability") if isinstance(result, dict) else None
        return {
            "tool": name,
            "mode": "live_read_only",
            "ok": bool(isinstance(result, dict) and result.get("ok", True)),
            "duration_ms": duration_ms,
            "observability": observability,
            "error": None if not isinstance(result, dict) else result.get("error"),
        }
    except Exception as exc:  # noqa: BLE001
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "tool": name,
            "mode": "live_read_only",
            "ok": False,
            "duration_ms": duration_ms,
            "observability": None,
            "error": str(exc),
        }


def _run_static_dictionary(spec) -> dict[str, Any]:
    sample_parameters = {}
    direct = None
    if spec.direct_parameter and not spec.direct_parameter.optional:
        desc = (spec.direct_parameter.description or "").lower()
        name = spec.direct_parameter.name.lower()
        if "database" in desc or "database" in name:
            direct = DB_INBOX
        elif "path" in desc or "path" in name:
            direct = "/tmp/example.txt"
        elif "url" in desc or "url" in name:
            direct = "https://example.com"
        else:
            direct = RECORD_A
    for param in spec.parameters:
        if param.optional:
            continue
        lower_name = param.name.lower()
        lower_desc = (param.description or "").lower()
        if "database" in lower_name or "database" in lower_desc:
            sample_parameters[param.name] = DB_INBOX
        elif any(token in lower_name for token in ("record", "from", "to", "in", "of", "for", "version", "at")) or "group" in lower_desc:
            sample_parameters[param.name] = RECORD_A if "to" not in lower_name else GROUP_ROOT
        elif "path" in lower_name or "path" in lower_desc:
            sample_parameters[param.name] = "/tmp/example.txt"
        elif "url" in lower_name or "url" in lower_desc:
            sample_parameters[param.name] = "https://example.com"
        elif "position" in lower_name or "size" in lower_name:
            sample_parameters[param.name] = 1
        else:
            sample_parameters[param.name] = "example"
    started = time.perf_counter()
    try:
        _build_command_call(spec, direct, sample_parameters)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return {"tool": spec.tool_name, "mode": "static_synthesis", "ok": True, "duration_ms": duration_ms, "error": None}
    except Exception as exc:  # noqa: BLE001
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return {"tool": spec.tool_name, "mode": "static_synthesis", "ok": False, "duration_ms": duration_ms, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    reports: list[dict[str, Any]] = []

    reports.extend(
        [
            _run_live("devonthink-get-database-by-uuid", devonthink_get_database_by_uuid, DB_INBOX),
            _run_live("devonthink-get-database-incoming-group", devonthink_get_database_incoming_group, DB_INBOX),
            _run_live("devonthink-get-record-by-uuid", devonthink_get_record_by_uuid, RECORD_A),
            _run_live("devonthink-list-group-children", devonthink_list_group_children, GROUP_ROOT, 25),
            _run_live("devonthink-search-records", devonthink_search_records, "concordance", 25, DB_INBOX),
            _run_live("devonthink-link-resolve", devonthink_link_resolve, RECORD_A),
            _run_live("devonthink-link-audit-record", devonthink_link_audit_record, RECORD_A),
            _run_live("devonthink-link-audit-folder", devonthink_link_audit_folder, GROUP_CONCORDANCE, 50),
            _run_live("devonthink-link-map-neighborhood", devonthink_link_map_neighborhood, RECORD_A, 1, 20),
            _run_live("devonthink-link-find-orphans", devonthink_link_find_orphans, GROUP_ROOT, 100),
            _run_live("devonthink-link-suggest-related", devonthink_link_suggest_related, RECORD_A, 15),
            _run_live("devonthink-link-score", devonthink_link_score, [RECORD_A, RECORD_B]),
            _run_live("devonthink-link-detect-bridges", devonthink_link_detect_bridges, GROUP_ROOT, 80),
            _run_live("devonthink-link-check-reciprocal", devonthink_link_check_reciprocal, RECORD_A, RECORD_B),
        ]
    )

    for spec in get_dictionary_command_specs():
        reports.append(_run_static_dictionary(spec))

    reports.append(
        {
            "tool": "devonthink-create-record",
            "mode": "skipped_risky",
            "ok": True,
            "duration_ms": 0,
            "error": "Skipped live mutation; validate through dedicated disposable-fixture tests.",
        }
    )

    slowest = sorted((r for r in reports if r["duration_ms"] > 0), key=lambda item: item["duration_ms"], reverse=True)[:20]
    summary = {
        "report_count": len(reports),
        "live_read_only_count": sum(1 for r in reports if r["mode"] == "live_read_only"),
        "static_synthesis_count": sum(1 for r in reports if r["mode"] == "static_synthesis"),
        "skipped_risky_count": sum(1 for r in reports if r["mode"] == "skipped_risky"),
        "failure_count": sum(1 for r in reports if not r["ok"]),
        "slowest": slowest,
    }
    by_tool = {report["tool"]: report for report in reports}
    payload = {"summary": summary, "reports": reports, "by_tool": by_tool}
    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if summary["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
