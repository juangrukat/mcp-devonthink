#!/usr/bin/env python3
"""Build catalog-runtime registry artifacts from tool metadata."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.tools.devonthink_dictionary_tools import dictionary_tool_catalog_entries
from app.tools.devonthink_annotation_tools import annotation_tool_catalog_entries
from app.tools.devonthink_database_tools import database_tool_catalog_entries
from app.tools.devonthink_link_tools import link_tool_catalog_entries
from app.tools.devonthink_reminder_tools import reminder_tool_catalog_entries
from app.tools.devonthink_richtext_tools import richtext_tool_catalog_entries
from app.tools.devonthink_script_tools import script_tool_catalog_entries
from app.tools.devonthink_smart_tools import smart_tool_catalog_entries
from app.tools.devonthink_tools import specialized_tool_catalog_entries


TOOLS_PATH = ROOT / "catalog-runtime" / "registry" / "tools.json"
OVERLAPS_PATH = ROOT / "catalog-runtime" / "registry" / "overlaps.json"


def build_tools() -> list[dict]:
    entries = []
    entries.extend(specialized_tool_catalog_entries())
    entries.extend(annotation_tool_catalog_entries())
    entries.extend(database_tool_catalog_entries())
    entries.extend(smart_tool_catalog_entries())
    entries.extend(reminder_tool_catalog_entries())
    entries.extend(script_tool_catalog_entries())
    entries.extend(richtext_tool_catalog_entries())
    entries.extend(link_tool_catalog_entries())
    entries.extend(dictionary_tool_catalog_entries())
    return sorted(entries, key=lambda item: item["name"])


def update_overlaps(entries: list[dict]) -> list[dict]:
    current = json.loads(OVERLAPS_PATH.read_text())
    by_family = {item["family"]: item for item in current}

    for entry in entries:
        family = entry.get("overlap_family")
        if not family:
            continue
        item = by_family.setdefault(
            family,
            {
                "family": family,
                "canonical_tool": entry["canonical_tool"],
                "members": [],
                "note": f"Overlap family for {family}.",
            },
        )
        item["canonical_tool"] = item.get("canonical_tool") or entry["canonical_tool"]
        if entry["name"] not in item["members"]:
            item["members"].append(entry["name"])
    for item in by_family.values():
        item["members"] = sorted(set(item["members"]))
    return sorted(by_family.values(), key=lambda item: item["family"])


def main() -> int:
    entries = build_tools()
    TOOLS_PATH.write_text(json.dumps(entries, indent=2) + "\n")
    overlaps = update_overlaps(entries)
    OVERLAPS_PATH.write_text(json.dumps(overlaps, indent=2) + "\n")
    print(f"wrote {len(entries)} tools to {TOOLS_PATH}")
    print(f"wrote {len(overlaps)} overlap families to {OVERLAPS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
