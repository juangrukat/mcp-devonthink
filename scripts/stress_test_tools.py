#!/usr/bin/env python3
"""Static stress test for the DEVONthink MCP tool surface.

This script does not execute DEVONthink mutations. Instead it:
1. Synthesizes sample AppleScript calls for every generated dictionary tool.
2. Flags malformed command assembly patterns.
3. Flags weak tool descriptions that may be harder for smaller LLMs to choose.

It complements the live integration suites in tests/ that exercise the
specialized canonical/link tools against real DEVONthink fixtures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tools.devonthink_dictionary_tools import (
    _build_command_call,
    _tool_description,
    get_dictionary_command_specs,
)


UUID_DATABASE = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"
UUID_RECORD = "5038E0B0-2134-4CDA-B443-6558CE283BCC"
UUID_GROUP = "180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"
ITEM_LINK = f"x-devonthink-item://{UUID_RECORD}"
TEXT_SAMPLE = "mcp test sample"
PATH_SAMPLE = "/tmp/devonthink-mcp-sample.txt"
ZIP_SAMPLE = "/tmp/devonthink-mcp-sample.zip"
URL_SAMPLE = "https://example.com"


def _sample_value(name: str, description: str, type_name: str | None) -> Any:
    lower_name = name.lower().strip()
    lower_desc = (description or "").lower()
    lower_type = (type_name or "").lower()

    if "database" in lower_name or "database" in lower_desc:
        return UUID_DATABASE
    if lower_name in {"record", "from", "in", "of", "for", "version", "at"}:
        return UUID_RECORD
    if lower_name == "to" and ("group" in lower_desc or "record" in lower_desc):
        return UUID_GROUP
    if "group" in lower_name or "group" in lower_desc:
        return UUID_GROUP
    if "item link" in lower_desc:
        return ITEM_LINK
    if "url" in lower_name or "url" in lower_desc:
        return URL_SAMPLE
    if "path" in lower_name or "path" in lower_desc or "folder" in lower_desc:
        if ".zip" in lower_desc or "zip archive" in lower_desc:
            return ZIP_SAMPLE
        return PATH_SAMPLE
    if "position" in lower_name or "count" in lower_name or "size" in lower_name:
        return 1
    if "save options" in lower_type:
        return "yes"
    if lower_type == "specifier" and "database" in lower_desc:
        return UUID_DATABASE
    if lower_type == "specifier":
        return UUID_RECORD
    return TEXT_SAMPLE


def _weak_description_flags(description: str) -> list[str]:
    flags: list[str] = []
    if len(description.strip()) < 140:
        flags.append("short_description")
    lowered = description.lower()
    for marker in ("use when:", "identifiers:", "safety:", "prefer this when:", "example:"):
        if marker not in lowered:
            flags.append(f"missing_{marker[:-1].replace(' ', '_')}")
    if "uuid" not in lowered and "item link" not in lowered and "path" not in lowered and "url" not in lowered:
        flags.append("missing_locator_examples")
    return flags


def _build_spec_report(spec) -> dict[str, Any]:
    direct = None
    params: dict[str, Any] = {}

    if spec.direct_parameter and not spec.direct_parameter.optional:
        direct = _sample_value(
            spec.direct_parameter.name,
            spec.direct_parameter.description,
            spec.direct_parameter.type_name,
        )

    for param in spec.parameters:
        if param.optional:
            continue
        params[param.name] = _sample_value(param.name, param.description, param.type_name)

    description = _tool_description(spec)
    report: dict[str, Any] = {
        "tool_name": spec.tool_name,
        "command_name": spec.command_name,
        "tier": spec.tier,
        "suite": spec.suite_name,
        "sample_direct": direct,
        "sample_parameters": params,
        "description_flags": _weak_description_flags(description),
    }

    try:
        applescript = _build_command_call(spec, direct, params)
        malformed_patterns = []
        for token in ("record record with uuid", "database database with uuid"):
            if token in applescript:
                malformed_patterns.append(token)
        report.update(
            {
                "build_ok": True,
                "applescript": applescript,
                "malformed_patterns": malformed_patterns,
            }
        )
    except Exception as exc:  # noqa: BLE001
        report.update(
            {
                "build_ok": False,
                "error": str(exc),
                "applescript": None,
                "malformed_patterns": [],
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    reports = [_build_spec_report(spec) for spec in get_dictionary_command_specs()]
    build_failures = [r for r in reports if not r["build_ok"]]
    malformed = [r for r in reports if r["malformed_patterns"]]
    weak_descriptions = [r for r in reports if r["description_flags"]]

    summary = {
        "total_dictionary_tools": len(reports),
        "build_ok_count": sum(1 for r in reports if r["build_ok"]),
        "build_failure_count": len(build_failures),
        "malformed_pattern_count": len(malformed),
        "weak_description_count": len(weak_descriptions),
        "requires_live_fixture_testing": [
            "all write/mutate tools",
            "all advanced database lifecycle tools",
            "all UI-coupled tools",
        ],
    }

    payload = {"summary": summary, "reports": reports}
    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2))

    print(json.dumps(summary, indent=2))
    if build_failures:
        print("\nBuild failures:")
        for item in build_failures[:20]:
            print(f"- {item['tool_name']}: {item['error']}")
    if malformed:
        print("\nMalformed patterns:")
        for item in malformed[:20]:
            print(f"- {item['tool_name']}: {', '.join(item['malformed_patterns'])}")
    if weak_descriptions:
        print("\nWeak descriptions:")
        for item in weak_descriptions[:20]:
            print(f"- {item['tool_name']}: {', '.join(item['description_flags'])}")

    return 1 if build_failures or malformed else 0


if __name__ == "__main__":
    raise SystemExit(main())
