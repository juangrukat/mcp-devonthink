#!/usr/bin/env python3
"""Cheap no-DEVONthink lint gate for tool catalog quality."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_PATH = ROOT / "catalog-runtime" / "registry" / "tools.json"
OVERLAPS_PATH = ROOT / "catalog-runtime" / "registry" / "overlaps.json"
APPLESCRIPT_SOURCE_PATHS = [
    ROOT / "app" / "tools" / "devonthink_tools.py",
    ROOT / "app" / "tools" / "devonthink_annotation_tools.py",
    ROOT / "app" / "tools" / "devonthink_database_tools.py",
    ROOT / "app" / "tools" / "devonthink_dictionary_tools.py",
    ROOT / "app" / "tools" / "devonthink_link_tools.py",
    ROOT / "app" / "tools" / "devonthink_reminder_tools.py",
    ROOT / "app" / "tools" / "devonthink_richtext_tools.py",
    ROOT / "app" / "tools" / "devonthink_script_tools.py",
    ROOT / "app" / "tools" / "devonthink_smart_tools.py",
]

REQUIRED_FIELDS = [
    "name",
    "description",
    "group",
    "tier",
    "status",
    "canonical_tool",
    "overlap_family",
    "source_path",
    "catalog_path",
    "executable",
    "priority",
    "default_exposed",
    "accepted_identifiers",
    "preferred_identifier",
    "identifier_guidance",
    "safety_class",
    "profile_availability",
    "prefer_when",
    "example",
]

MODE_DOC_REQUIRED_TOOLS = {
    "devonthink-link-audit-record",
}

BARE_RECORD_UUID_RE = re.compile(r"(?<!get )record with uuid")
DUPLICATE_UNRESOLVED_RE = re.compile(r"^\s*set\s+\w+\s+to\s+duplicate\s+(?!record\b|\(get\b|theRecord\b)")

OVERLAP_GUIDANCE_REQUIRED = {
    "devonthink-create-rtf": ["generic create-record", "missing-value"],
    "devonthink-create-record": ["create-rtf", "create-smart-group"],
    "devonthink-run-script": ["arbitrary script code", "apply-smart-rule"],
    "devonthink-apply-smart-rule": ["named rule", "run-script"],
    "devonthink-list-scripts": ["filesystem script", "list-smart-rules"],
    "devonthink-list-smart-rules": ["saved rule", "list-scripts"],
    "devonthink-create-smart-group": ["query-backed", "static group"],
    "devonthink-link-audit-record": ["link diagnostics", "read-rtf"],
    "devonthink-read-rtf": ["record content", "link extraction"],
    "devonthink-create-annotation-note": ["attached annotation note", "pdf-internal"],
    "devonthink-read-annotation-note": ["attached annotation note", "summarize-annotations"],
    "devonthink-summarize-annotations": ["internal PDF", "attached annotation notes"],
}


def main() -> int:
    tools = json.loads(TOOLS_PATH.read_text())
    overlaps = json.loads(OVERLAPS_PATH.read_text())
    errors: list[str] = []

    overlap_families = {item["family"]: item for item in overlaps}
    tools_by_name = {item["name"]: item for item in tools}

    for item in tools:
        for field in REQUIRED_FIELDS:
            value = item.get(field)
            if field == "preferred_identifier":
                if field not in item:
                    errors.append(f"{item['name']}: missing {field}")
                continue
            if field == "accepted_identifiers":
                if field not in item:
                    errors.append(f"{item['name']}: missing {field}")
                continue
            if value is None or value == "" or value == []:
                errors.append(f"{item['name']}: missing {field}")
        desc = item.get("description", "")
        for marker in ("Use when:", "Identifiers:", "Safety:", "Prefer this when:", "Example:"):
            if marker not in desc:
                errors.append(f"{item['name']}: description missing marker {marker}")
        if item["name"] in MODE_DOC_REQUIRED_TOOLS:
            if "mode=authoritative" not in desc or "mode=full" not in desc:
                errors.append(f"{item['name']}: description missing authoritative/full mode guidance")
        required_guidance = OVERLAP_GUIDANCE_REQUIRED.get(item["name"])
        if required_guidance:
            haystack = " ".join(
                [
                    item.get("description", ""),
                    item.get("prefer_when", ""),
                    " ".join(item.get("invocation_pitfalls") or []),
                ]
            ).lower()
            for phrase in required_guidance:
                if phrase.lower() not in haystack:
                    errors.append(f"{item['name']}: overlap guidance missing phrase {phrase!r}")
        family = item.get("overlap_family")
        if family and family not in overlap_families:
            errors.append(f"{item['name']}: overlap_family {family} missing from overlaps.json")

    for family, item in overlap_families.items():
        canonical_tool = item["canonical_tool"]
        if canonical_tool not in tools_by_name:
            errors.append(f"{family}: canonical_tool {canonical_tool} missing from tools.json")
        for member in item["members"]:
            if member in tools_by_name and not tools_by_name[member].get("prefer_when"):
                errors.append(f"{family}: member {member} missing prefer_when")

    for path in APPLESCRIPT_SOURCE_PATHS:
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if BARE_RECORD_UUID_RE.search(line):
                errors.append(
                    f"{path.relative_to(ROOT)}:{lineno}: AppleScript record UUID specifier must use 'get record with uuid'"
                )
            if DUPLICATE_UNRESOLVED_RE.search(line):
                errors.append(
                    f"{path.relative_to(ROOT)}:{lineno}: duplicate source must be resolved, e.g. 'duplicate record theRecord to destinationGroup'"
                )

    if errors:
        print("catalog lint failed")
        for error in errors[:200]:
            print(f"- {error}")
        return 1

    print(f"catalog lint passed for {len(tools)} tools and {len(overlaps)} overlap families")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
