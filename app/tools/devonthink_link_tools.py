"""Link intelligence tools for DEVONthink MCP.

These tools compose native DEVONthink commands into a higher-level link-ops layer:
inspect -> analyze -> act.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.tools.applescript_counter import record_applescript_call
from app.tools.tool_catalog import build_description, catalog_entry
from app.tools.telemetry import wrap_tool_call

log = logging.getLogger(__name__)

CONTRACT_VERSION = "1.1.0"
SIGNAL_MODEL_VERSION = "1.1.0"
TOOLSET_VERSION = "2026.04.23.1"
SNAPSHOT_META_SCHEMA_VERSION = "1.0.0"

ITEM_LINK_RE = re.compile(r"x-devonthink-item://[0-9a-fA-F-]{36}")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
WIKILINK_RE = re.compile(r"\[\[([^\]\n\r]{1,200})\]\]")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "with",
}

SIGNAL_WEIGHTS = {
    "outgoing_reference": 5,
    "incoming_reference": 5,
    "outgoing_wiki_reference": 4,
    "incoming_wiki_reference": 4,
    "explicit_item_link": 4,
    "explicit_mention": 4,
    "wikilink": 3,
    "alias_match": 3,
    "title_fuzzy_match": 2,
    "shared_tag": 2,
    "concordance_overlap": 1,
    "same_group_context": 1,
    "hub_cooccurrence": 1,
}

SIGNAL_TIER_LEVEL = {
    "authoritative": 1,
    "structural": 2,
    "inferred": 3,
}

REASON_SIGNAL_TIER = {
    "outgoing_reference": "authoritative",
    "incoming_reference": "authoritative",
    "outgoing_wiki_reference": "authoritative",
    "incoming_wiki_reference": "authoritative",
    "explicit_item_link": "structural",
    "wikilink": "structural",
    "shared_tag": "structural",
    "alias_match": "structural",
    "same_group_context": "structural",
    "hub_cooccurrence": "structural",
    "explicit_mention": "inferred",
    "title_fuzzy_match": "inferred",
    "concordance_overlap": "inferred",
}

SEARCH_MIN_LENGTH = int(os.environ.get("DEVONTHINK_LINK_SEARCH_MIN_LENGTH", "2"))
FUZZY_SKIP_THRESHOLD = int(os.environ.get("DEVONTHINK_LINK_FUZZY_SKIP_THRESHOLD", "3"))
MAX_TRAVERSAL_DEPTH_DEFAULT = int(os.environ.get("DEVONTHINK_LINK_MAX_TRAVERSAL_DEPTH", "3"))
SHAPE_THRESHOLDS = {
    "hub_outgoing": int(os.environ.get("DEVONTHINK_LINK_HUB_OUTGOING", "5")),
    "hub_incoming": int(os.environ.get("DEVONTHINK_LINK_HUB_INCOMING", "2")),
    "spoke_incoming": int(os.environ.get("DEVONTHINK_LINK_SPOKE_INCOMING", "2")),
    "spoke_outgoing_max": int(os.environ.get("DEVONTHINK_LINK_SPOKE_OUTGOING_MAX", "2")),
    "bridge_outgoing": int(os.environ.get("DEVONTHINK_LINK_BRIDGE_OUTGOING", "3")),
    "bridge_clusters": int(os.environ.get("DEVONTHINK_LINK_BRIDGE_CLUSTERS", "2")),
    "sink_incoming": int(os.environ.get("DEVONTHINK_LINK_SINK_INCOMING", "3")),
    "near_orphan_total_max": int(os.environ.get("DEVONTHINK_LINK_NEAR_ORPHAN_TOTAL_MAX", "1")),
}

LINK_TOOL_TIERS = {
    "devonthink-link-resolve": "canonical",
    "devonthink-link-audit-record": "canonical",
    "devonthink-link-audit-folder": "canonical",
    "devonthink-link-map-neighborhood": "canonical",
    "devonthink-link-find-orphans": "canonical",
    "devonthink-link-suggest-related": "canonical",
    "devonthink-link-score": "canonical",
    "devonthink-link-detect-bridges": "canonical",
    "devonthink-link-check-reciprocal": "canonical",
    "devonthink-link-build-hub": "advanced",
    "devonthink-link-enrich-metadata": "advanced",
    "devonthink-link-repair-links": "advanced",
    "devonthink-link-maintenance-pass": "advanced",
    "devonthink-link-traverse-folder": "advanced",
    "devonthink-link-compare-snapshots": "advanced",
    "devonthink-link-prune-snapshots": "advanced",
}


def link_tool_catalog_entries(*, include_tiers: set[str] | None = None) -> list[dict[str, Any]]:
    specs = [
        {
            "name": "devonthink-link-resolve",
            "tier": "canonical",
            "summary": "Normalize a DEVONthink record reference into canonical structured identity.",
            "use_when": "you need to turn a UUID or x-devonthink-item link into a stable record object before later link or maintenance steps.",
            "identifier_guidance": "Accepts a record UUID or x-devonthink-item link. Prefer a UUID when you already have one; item links are useful when copied from DEVONthink content.",
            "safety_class": "read_only",
            "prefer_when": "you need identity normalization before another step; prefer get-record tools when you already know you just need metadata.",
            "degradation_contract": None,
            "example": '{"record_ref":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}',
        },
        {
            "name": "devonthink-link-audit-record",
            "tier": "canonical",
            "summary": "Audit one record for native references, wiki references, and link-risk flags.",
            "use_when": "you want a single-record inspect pass before building hubs, repairing links, or reviewing weak notes. Default mode=authoritative is fast and uses native DEVONthink edge properties only; use mode=full when you explicitly need content-scan and inferred wikilink signals.",
            "identifier_guidance": "Accepts a record UUID or x-devonthink-item link. Prefer the record UUID.",
            "safety_class": "read_only",
            "prefer_when": "you need link diagnostics, references, and risk flags; use read-rtf when you only need rich text content.",
            "degradation_contract": "mode=authoritative returns native edge properties only and does not content-scan. mode=full may fall back to text and wikilink scanning and reports that in observability.warnings instead of throwing.",
            "example": '{"record_ref":"5038E0B0-2134-4CDA-B443-6558CE283BCC","mode":"authoritative"}',
        },
        {
            "name": "devonthink-link-audit-folder",
            "tier": "canonical",
            "summary": "Audit a folder or group for child-record link quality, weakly connected notes, and tag clusters.",
            "use_when": "you need a read-only health review across one folder before maintenance or curation work. This tool uses the fast authoritative bulk-edge path by default and is the right choice when you would otherwise run audit-record in a loop.",
            "identifier_guidance": "Accepts a group UUID. Prefer a real group UUID; use the database incoming-group helper first if you start from a database UUID.",
            "safety_class": "read_only",
            "prefer_when": "you need aggregate folder-level diagnostics; prefer traverse-folder for recursive graph export and snapshots.",
            "degradation_contract": "Uses authoritative bulk edge snapshots and does not content-scan child notes by default. If a future full/content-scan path is needed, treat it as slower and unsuitable for loops or large folders.",
            "example": '{"folder_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","limit":50}',
        },
        {
            "name": "devonthink-link-map-neighborhood",
            "tier": "canonical",
            "summary": "Map a local neighborhood graph around one record and return nodes, edges, reasons, and strengths.",
            "use_when": "you want to inspect local link structure around a note before hub building or relationship review. The current default path is authoritative-only and optimized for low Apple Event count per hop.",
            "identifier_guidance": "Accepts a record UUID or x-devonthink-item link. Prefer the record UUID.",
            "safety_class": "read_only",
            "prefer_when": "you need graph context around one note; prefer score for aggregate connectivity scoring only.",
            "degradation_contract": "Uses authoritative edges only by default and avoids content-scan fallback. If inferred [[wikilink]] expansion is reintroduced later, it should be an explicit slower mode rather than the default.",
            "example": '{"record_ref":"5038E0B0-2134-4CDA-B443-6558CE283BCC","radius":1,"per_hop_limit":20}',
        },
        {
            "name": "devonthink-link-find-orphans",
            "tier": "canonical",
            "summary": "Find orphan and near-orphan records in one folder using authoritative and weaker connectivity signals.",
            "use_when": "you need to identify weakly connected notes for review or curation.",
            "identifier_guidance": "Accepts a group UUID. Prefer a concrete group UUID.",
            "safety_class": "read_only",
            "prefer_when": "you specifically care about weak connectivity; prefer audit-folder for a broader quality report.",
            "degradation_contract": "Returns observability.warnings when some records require degraded text-based link discovery instead of native link properties.",
            "example": '{"folder_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","limit":100}',
        },
        {
            "name": "devonthink-link-suggest-related",
            "tier": "canonical",
            "summary": "Suggest related records using weighted explicit, structural, and inferred signals.",
            "use_when": "you want candidate links for a note without mutating anything.",
            "identifier_guidance": "Accepts a record UUID or x-devonthink-item link. Prefer the record UUID.",
            "safety_class": "read_only",
            "prefer_when": "you need candidate related notes; prefer score when you already know the records you want to compare.",
            "degradation_contract": "If some signals are unavailable, the tool still returns suggestions and records the degraded signal paths in observability.warnings.",
            "example": '{"record_ref":"5038E0B0-2134-4CDA-B443-6558CE283BCC","limit":15}',
        },
        {
            "name": "devonthink-link-score",
            "tier": "canonical",
            "summary": "Score connectivity for one or more records and return score components plus risk flags.",
            "use_when": "you already have a candidate set and want to compare how strongly connected it is.",
            "identifier_guidance": "Accepts a list of record UUIDs or x-devonthink-item links. Prefer UUIDs for stable scoring input.",
            "safety_class": "read_only",
            "prefer_when": "you need scored comparisons, not open-ended candidate discovery.",
            "degradation_contract": "If some signal sources are unavailable, the score still returns with signal-tier annotations and observability warnings.",
            "example": '{"record_refs":["5038E0B0-2134-4CDA-B443-6558CE283BCC","180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"]}',
        },
        {
            "name": "devonthink-link-detect-bridges",
            "tier": "canonical",
            "summary": "Detect bridge notes that connect multiple topical clusters inside a folder.",
            "use_when": "you want to identify cross-topic notes for hub planning or structural cleanup.",
            "identifier_guidance": "Accepts a group UUID. Prefer a concrete group UUID.",
            "safety_class": "read_only",
            "prefer_when": "you specifically need bridge-note detection rather than general audit output.",
            "degradation_contract": "If some cluster signals degrade, the tool still returns bridge candidates and records degraded signal counts in observability stats.",
            "example": '{"folder_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","limit":80}',
        },
        {
            "name": "devonthink-link-check-reciprocal",
            "tier": "canonical",
            "summary": "Validate whether a source and target record agree on a reciprocal relationship.",
            "use_when": "you need to verify one pair before editing links or asserting graph consistency.",
            "identifier_guidance": "Accepts source and target record UUIDs or x-devonthink-item links. Prefer UUIDs.",
            "safety_class": "read_only",
            "prefer_when": "you need one pairwise consistency check; prefer map-neighborhood for broader context.",
            "degradation_contract": "If authoritative incoming/outgoing properties are unavailable, the tool reports degraded reasoning instead of silently failing.",
            "example": '{"source_ref":"5038E0B0-2134-4CDA-B443-6558CE283BCC","target_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
        },
        {
            "name": "devonthink-link-build-hub",
            "tier": "advanced",
            "summary": "Create or update a markdown hub note in a target group from seed records.",
            "use_when": "you want an act-layer summary note built from existing notes and links.",
            "identifier_guidance": "Accepts a target group UUID and a list of seed record UUIDs or item links. Prefer UUIDs for both.",
            "safety_class": "writes_data",
            "prefer_when": "you want a curated hub note; prefer audit or suggest tools when you are still exploring candidates.",
            "degradation_contract": None,
            "example": '{"group_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","seed_record_refs":["5038E0B0-2134-4CDA-B443-6558CE283BCC"],"hub_name":"Link Hub","mode":"overview"}',
        },
        {
            "name": "devonthink-link-enrich-metadata",
            "tier": "advanced",
            "summary": "Suggest or apply metadata enrichment derived from record title and content.",
            "use_when": "you want better tags, comments, or custom metadata for linkability.",
            "identifier_guidance": "Accepts a record UUID or x-devonthink-item link. Prefer the record UUID.",
            "safety_class": "writes_data",
            "prefer_when": "you are ready to improve metadata quality; use mode=suggest first for read-only review.",
            "degradation_contract": "Mode=suggest avoids writes and still returns warnings for unsupported extraction paths rather than failing hard.",
            "example": '{"record_ref":"5038E0B0-2134-4CDA-B443-6558CE283BCC","mode":"suggest"}',
        },
        {
            "name": "devonthink-link-repair-links",
            "tier": "advanced",
            "summary": "Report or apply safe text-link repairs inside one record.",
            "use_when": "you need to clean unresolved item links, wikilinks, or canonicalize bare UUID references.",
            "identifier_guidance": "Accepts a record UUID or x-devonthink-item link. Prefer the record UUID.",
            "safety_class": "writes_data",
            "prefer_when": "you are intentionally repairing link text and can start with mode=report before apply.",
            "degradation_contract": "Mode=report is dry-run; mode=apply writes only the safe repairs the tool can justify and records skipped cases in warnings.",
            "example": '{"record_ref":"5038E0B0-2134-4CDA-B443-6558CE283BCC","mode":"report"}',
        },
        {
            "name": "devonthink-link-maintenance-pass",
            "tier": "advanced",
            "summary": "Run a folder-level maintenance pass against the latest snapshot baseline and return deltas plus optional actions.",
            "use_when": "you need recurring graph-health maintenance rather than one-off inspection.",
            "identifier_guidance": "Accepts a folder or group UUID. Prefer a group UUID and an explicit snapshot_dir when you manage multiple baselines.",
            "safety_class": "writes_data",
            "prefer_when": "you need scheduled or repeatable folder maintenance with deltas; prefer audit-folder for one-shot inspection.",
            "degradation_contract": "Mode=report is read-only. If no baseline exists, the tool captures one and reports first_run instead of throwing.",
            "example": '{"folder_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","mode":"report","limit":50,"snapshot_dir":"snapshots"}',
        },
        {
            "name": "devonthink-link-traverse-folder",
            "tier": "advanced",
            "summary": "Traverse folder records into node-first adjacency output with pagination, deduplication, and optional snapshots.",
            "use_when": "you need hierarchy and graph traversal, snapshot baselines, or recursive folder inspection.",
            "identifier_guidance": "Accepts a folder or group UUID. Prefer a concrete group UUID; use the incoming-group helper first when starting from a database UUID.",
            "safety_class": "read_only",
            "prefer_when": "you need recursive traversal or snapshot export; prefer list-group-children for direct child listing only.",
            "degradation_contract": "Smart groups are handled as saved-query virtual membership and search degradations are reported in observability warnings/stats instead of throwing.",
            "example": '{"folder_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","limit":200,"mode":"recursive","max_depth":2,"write_snapshot":true}',
        },
        {
            "name": "devonthink-link-compare-snapshots",
            "tier": "advanced",
            "summary": "Compare two traversal snapshots and report node, edge, and health deltas.",
            "use_when": "you need a before/after view of graph changes rather than a fresh traversal alone.",
            "identifier_guidance": "Accepts explicit snapshot paths or a folder UUID for auto-discovery. Prefer explicit paths when reproducibility matters.",
            "safety_class": "read_only",
            "prefer_when": "you already have snapshots and want delta analysis; prefer traverse-folder to create or refresh baselines.",
            "degradation_contract": None,
            "example": '{"folder_ref":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","snapshot_dir":"snapshots"}',
        },
        {
            "name": "devonthink-link-prune-snapshots",
            "tier": "advanced",
            "summary": "Report, archive, or delete old snapshot pairs according to a retention policy.",
            "use_when": "you need lifecycle management for traversal snapshots after repeated maintenance runs.",
            "identifier_guidance": "Accepts filesystem paths and retention settings rather than DEVONthink UUIDs. Prefer report mode first.",
            "safety_class": "destructive",
            "prefer_when": "you are cleaning snapshot artifacts, not DEVONthink records.",
            "degradation_contract": "Mode=report is a dry run; mode=apply archives first by default and records manual-review cases instead of silently deleting everything.",
            "example": '{"snapshot_dir":"snapshots","mode":"report"}',
        },
    ]

    entries: list[dict[str, Any]] = []
    for item in specs:
        if include_tiers is not None and item["tier"] not in include_tiers:
            continue
        description = build_description(
            summary=item["summary"],
            use_when=item["use_when"],
            identifier_guidance=item["identifier_guidance"],
            safety_class=item["safety_class"],
            prefer_when=item["prefer_when"],
            degradation_contract=item["degradation_contract"],
            example=item["example"],
        )
        entries.append(
            catalog_entry(
                name=item["name"],
                description=description,
                group="devonthink.link-intelligence",
                tier=item["tier"],
                status="active",
                canonical_tool=item["name"],
                overlap_family="devonthink-link-intelligence",
                source_path="app/tools/devonthink_link_tools.py",
                catalog_path=f"catalog-runtime/tools/devonthink.link-intelligence/{item['tier']}/{item['name']}.json",
                executable="osascript",
                priority=100 if item["tier"] == "canonical" else 60,
                default_exposed=(item["tier"] == "canonical"),
                accepted_identifiers=["record_uuid", "group_uuid", "x-devonthink-item://"] if "UUID" in item["identifier_guidance"] or "uuid" in item["identifier_guidance"].lower() else ["posix_path"],
                preferred_identifier="record_uuid" if "record UUID" in item["identifier_guidance"] or "record UUIDs" in item["identifier_guidance"] else ("group_uuid" if "group UUID" in item["identifier_guidance"] else None),
                identifier_guidance=item["identifier_guidance"],
                safety_class=item["safety_class"],
                profile_availability=["canonical", "full"] if item["tier"] == "canonical" else ["full"],
                prefer_when=item["prefer_when"],
                degradation_contract=item["degradation_contract"],
                example=item["example"],
                tags=["devonthink", "link-intelligence", item["tier"]],
            )
        )
    return entries


class AppleScriptExecutionError(RuntimeError):
    """Raised when an AppleScript execution fails."""


def _new_observability() -> dict[str, int]:
    return {
        "search_calls_made": 0,
        "search_calls_degraded": 0,
        "missing_value_coercions": 0,
    }


def _merge_observability(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base or {})
    for key, value in (extra or {}).items():
        if isinstance(value, int):
            merged[key] = int(merged.get(key, 0)) + value
        else:
            merged[key] = value
    return merged


def _signal_tier_for_reason(reason_code: str) -> str:
    return REASON_SIGNAL_TIER.get(reason_code, "inferred")


def _lowest_signal_tier_for_edges(edges: list[dict[str, Any]]) -> str:
    if not edges:
        return "authoritative"
    tier = "authoritative"
    for edge in edges:
        edge_tier = _signal_tier_for_reason(str(edge.get("reason_code", "")))
        if SIGNAL_TIER_LEVEL[edge_tier] > SIGNAL_TIER_LEVEL[tier]:
            tier = edge_tier
    return tier


def _coerce_text(value: Any, obs: dict[str, int] | None = None) -> str:
    if value is None:
        if obs is not None:
            obs["missing_value_coercions"] += 1
        return ""
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        if obs is not None:
            obs["missing_value_coercions"] += 1
        return ""


def _sanitize_search_query(text: str) -> str:
    cleaned = text
    for c in ["/", ":", "\"", "<", ">", "\\", "-"]:
        cleaned = cleaned.replace(c, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


_RECORD_DB_CACHE: dict[str, str] = {}


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_osascript_error(stderr: str) -> str:
    lowered = (stderr or "").lower()
    if "not authorized" in lowered or "-1743" in lowered:
        return (
            "Apple Events permission denied. In macOS System Settings > Privacy & Security > "
            "Automation, allow your terminal/Python host to control DEVONthink."
        )
    if "application isn't running" in lowered:
        return "DEVONthink is not running. Start DEVONthink and try again."
    if "can't get application" in lowered:
        return "DEVONthink is not installed or not available to AppleScript on this Mac."
    return (stderr or "Unknown AppleScript execution error.").strip()


def _run_osascript(script: str, args: list[str]) -> str:
    record_applescript_call()
    if log.isEnabledFor(logging.DEBUG):
        log.debug("AppleScript:\n%s", script)
    proc = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-", *args],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AppleScriptExecutionError(_classify_osascript_error(proc.stderr))
    return proc.stdout.strip()


def _run_json(script: str, args: list[str]) -> Any:
    raw = _run_osascript(script, args)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppleScriptExecutionError(f"Failed to parse AppleScript JSON output: {exc}") from exc


def _response(
    *,
    tool: str,
    started_at: float,
    inputs: dict[str, Any],
    ok: bool,
    data: dict[str, Any] | None = None,
    error: str | None = None,
    warnings: list[str] | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tool": tool,
        "contract": {
            "contract_version": CONTRACT_VERSION,
            "signal_model_version": SIGNAL_MODEL_VERSION,
            "toolset_version": TOOLSET_VERSION,
        },
        "observability": {
            "executed_at_utc": _iso_utc_now(),
            "duration_ms": int((time.time() - started_at) * 1000),
            "warnings": warnings or [],
            "stats": stats or {},
        },
        "inputs": inputs,
        "data": data,
        "error": error,
    }


_JSON_HELPERS = r'''
using terms from application "DEVONthink"
on escape_json(s)
    set t to s as text
    set t to my replace_text(t, "\\", "\\\\")
    set t to my replace_text(t, "\"", "\\\"")
    set t to my replace_text(t, return, "\\n")
    set t to my replace_text(t, linefeed, "\\n")
    set t to my replace_text(t, tab, "\\t")
    return t
end escape_json

on replace_text(theText, searchString, replacementString)
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to searchString
    set parts to text items of theText
    set AppleScript's text item delimiters to replacementString
    set newText to parts as text
    set AppleScript's text item delimiters to oldDelims
    return newText
end replace_text

on json_string(valueText)
    return "\"" & my escape_json(valueText) & "\""
end json_string

on maybe_text(valueAny)
    if valueAny is missing value then
        return "null"
    end if
    return my json_string(valueAny as text)
end maybe_text

on list_json(theList)
    if theList is missing value then return "[]"
    set n to count of theList
    if n is 0 then return "[]"
    set output to "["
    repeat with i from 1 to n
        set output to output & my json_string(item i of theList as text)
        if i is not n then set output to output & ","
    end repeat
    return output & "]"
end list_json

on record_json(theRecord)
    set r_uuid to missing value
    set r_id to missing value
    set r_name to missing value
    set r_type to missing value
    set r_location to missing value
    set r_url to missing value
    set r_reference_url to missing value
    set r_path to missing value
    set r_comment to missing value
    set r_tags to {}
    set r_aliases to {}
    set r_database_uuid to missing value
    set r_database_read_only to missing value
    set r_locked to missing value
    set r_created to missing value
    set r_modified to missing value

    try
        set r_uuid to uuid of theRecord
    end try
    try
        set r_id to id of theRecord
    end try
    try
        set r_name to name of theRecord
    end try
    try
        set r_type to type of theRecord
    end try
    try
        set r_location to location of theRecord
    end try
    try
        set r_url to URL of theRecord
    end try
    try
        set r_reference_url to reference URL of theRecord
    end try
    try
        set r_path to path of theRecord
    end try
    try
        set r_comment to comment of theRecord
    end try
    try
        set r_tags to tags of theRecord
    end try
    try
        set r_aliases to aliases of theRecord
    end try
    try
        set r_created to creation date of theRecord
    end try
    try
        set r_modified to modification date of theRecord
    end try
    try
        set r_database_uuid to uuid of (database of theRecord)
    end try
    try
        set r_database_read_only to read only of (database of theRecord)
    end try
    try
        set r_locked to locked of theRecord
    end try

    return "{" & ¬
        "\"uuid\":" & my maybe_text(r_uuid) & "," & ¬
        "\"id\":" & my maybe_text(r_id) & "," & ¬
        "\"name\":" & my maybe_text(r_name) & "," & ¬
        "\"type\":" & my maybe_text(r_type) & "," & ¬
        "\"location\":" & my maybe_text(r_location) & "," & ¬
        "\"url\":" & my maybe_text(r_url) & "," & ¬
        "\"reference_url\":" & my maybe_text(r_reference_url) & "," & ¬
        "\"path\":" & my maybe_text(r_path) & "," & ¬
        "\"comment\":" & my maybe_text(r_comment) & "," & ¬
        "\"database_uuid\":" & my maybe_text(r_database_uuid) & "," & ¬
        "\"database_read_only\":" & my maybe_text(r_database_read_only) & "," & ¬
        "\"locked\":" & my maybe_text(r_locked) & "," & ¬
        "\"created\":" & my maybe_text(r_created) & "," & ¬
        "\"modified\":" & my maybe_text(r_modified) & "," & ¬
        "\"tags\":" & my list_json(r_tags) & "," & ¬
        "\"aliases\":" & my list_json(r_aliases) & ¬
        "}"
end record_json
end using terms from
'''


def _normalize_record_ref(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("record_ref must be a non-empty string.")

    match = ITEM_LINK_RE.search(cleaned)
    if match:
        return match.group(0).split("//", 1)[1]

    match = UUID_RE.search(cleaned)
    if match:
        return match.group(0)

    return cleaned


def _validate_limit(value: int, *, min_value: int = 1, max_value: int = 200, field: str = "limit") -> int:
    if value < min_value or value > max_value:
        raise ValueError(f"{field} must be between {min_value} and {max_value}.")
    return value


def _database_is_open(database_uuid: str) -> bool:
    db_uuid = (database_uuid or "").strip()
    if not db_uuid:
        return False

    script = r'''
on run argv
    set dbUUID to item 1 of argv
    tell application "DEVONthink"
        try
            set db to get database with uuid dbUUID
            if db is missing value then
                return "false"
            end if
            return "true"
        on error
            return "false"
        end try
    end tell
end run
'''
    return _run_osascript(script, [db_uuid]).strip().lower() == "true"


def _dt_resolve_record(record_ref: str) -> dict[str, Any]:
    normalized = _normalize_record_ref(record_ref)
    script = _JSON_HELPERS + r'''
on run argv
    set recordRef to item 1 of argv
    tell application "DEVONthink"
        set theRecord to get record with uuid recordRef
        return my record_json(theRecord)
    end tell
end run
'''
    try:
        obj = _run_json(script, [normalized])
    except AppleScriptExecutionError as exc:
        # If the record has been resolved before, we can convert missing-record lookups
        # into a deterministic database_unavailable signal when that database is currently closed.
        cached_db = _RECORD_DB_CACHE.get(normalized)
        if cached_db and not _database_is_open(cached_db):
            raise AppleScriptExecutionError(
                f"database_unavailable: Database {cached_db} is closed or unavailable for record {normalized}."
            ) from exc
        raise
    if not isinstance(obj, dict):
        raise AppleScriptExecutionError("Record lookup returned non-object output.")
    if not any(obj.get(k) is not None for k in ("uuid", "id", "name", "database_uuid")):
        cached_db = _RECORD_DB_CACHE.get(normalized)
        if cached_db and not _database_is_open(cached_db):
            raise AppleScriptExecutionError(
                f"database_unavailable: Database {cached_db} is closed or unavailable for record {normalized}."
            )
        raise AppleScriptExecutionError(f"record_not_found: Could not resolve record {normalized}.")
    db_uuid = _coerce_text(obj.get("database_uuid")).strip()
    if db_uuid and not _database_is_open(db_uuid):
        raise AppleScriptExecutionError(
            f"database_unavailable: Database {db_uuid} is closed or unavailable for record {obj.get('uuid') or normalized}."
        )
    if db_uuid:
        _RECORD_DB_CACHE[normalized] = db_uuid
        resolved_uuid = _coerce_text(obj.get("uuid")).strip()
        if resolved_uuid:
            _RECORD_DB_CACHE[resolved_uuid] = db_uuid
    return obj


def _get_record(record_ref: str) -> dict[str, Any]:
    return _dt_resolve_record(record_ref)


def _is_truthy(value: Any) -> bool:
    s = _coerce_text(value).strip().lower()
    return s in {"true", "yes", "1"}


def _assert_record_writable(record: dict[str, Any], *, operation: str) -> None:
    if _is_truthy(record.get("database_read_only")):
        db_uuid = _coerce_text(record.get("database_uuid")).strip() or "unknown"
        rec_uuid = _coerce_text(record.get("uuid")).strip() or "unknown"
        raise AppleScriptExecutionError(
            f"database_readonly: Database {db_uuid} is read-only/audit-proof; cannot perform {operation} on record {rec_uuid}."
        )


def _assert_content_writable(record: dict[str, Any], *, operation: str) -> None:
    _assert_record_writable(record, operation=operation)
    if _is_truthy(record.get("locked")):
        db_uuid = _coerce_text(record.get("database_uuid")).strip() or "unknown"
        rec_uuid = _coerce_text(record.get("uuid")).strip() or "unknown"
        raise AppleScriptExecutionError(
            f"record_locked: Record {rec_uuid} in database {db_uuid} is locked; cannot perform {operation} content edits."
        )


def _get_record_text(record_ref: str, *, max_chars: int = 12000) -> str:
    normalized = _normalize_record_ref(record_ref)
    max_chars = max(500, min(max_chars, 200000))
    script = r'''
on run argv
    set recordRef to item 1 of argv
    set maxChars to (item 2 of argv as integer)

    tell application "DEVONthink"
        set theRecord to get record with uuid recordRef
        set t to ""
        try
            set t to (get text of theRecord) as text
        on error
            try
                set t to plain text of theRecord as text
            on error
                set t to ""
            end try
        end try

        if (count of t) > maxChars then
            set t to text 1 thru maxChars of t
        end if
        return t
    end tell
end run
'''
    return _run_osascript(script, [normalized, str(max_chars)])


def _search_records(
    query: str,
    *,
    limit: int = 25,
    database_uuid: str | None = None,
    obs: dict[str, int] | None = None,
    sanitize: bool = False,
) -> list[dict[str, Any]]:
    raw = (query or "").strip()
    q = _sanitize_search_query(raw) if sanitize else raw
    if obs is not None:
        obs["search_calls_made"] += 1
    min_len = max(1, SEARCH_MIN_LENGTH)
    if not q or len(q) < min_len:
        if obs is not None:
            obs["search_calls_degraded"] += 1
        return []
    limit = _validate_limit(limit)
    db = (database_uuid or "").strip()

    script = _JSON_HELPERS + r'''
on run argv
    set searchQuery to item 1 of argv
    set maxCount to (item 2 of argv as integer)
    set databaseUUID to item 3 of argv

    tell application "DEVONthink"
        if databaseUUID is "" then
            set foundRecords to search searchQuery
        else
            set theDatabase to get database with uuid databaseUUID
            set foundRecords to search searchQuery in theDatabase
        end if

        set totalCount to count of foundRecords
        if maxCount > totalCount then set maxCount to totalCount

        set output to "["
        repeat with i from 1 to maxCount
            set output to output & my record_json(item i of foundRecords)
            if i is not maxCount then set output to output & ","
        end repeat
        set output to output & "]"
        return output
    end tell
end run
'''
    try:
        data = _run_json(script, [q, str(limit), db])
        return data if isinstance(data, list) else []
    except AppleScriptExecutionError as exc:
        if "Invalid argument" not in str(exc):
            raise

        # Some DEVONthink builds can reject `search ... in database` for otherwise valid
        # queries. Retry globally before degrading the signal.
        if db:
            try:
                data = _run_json(script, [q, str(limit), ""])
                return data if isinstance(data, list) else []
            except AppleScriptExecutionError as global_exc:
                if "Invalid argument" not in str(global_exc):
                    raise

        if obs is not None:
            obs["search_calls_degraded"] += 1
        return []


def _get_children(group_ref: str, *, limit: int = 200) -> list[dict[str, Any]]:
    normalized = _normalize_record_ref(group_ref)
    limit = _validate_limit(limit)
    script = _JSON_HELPERS + r'''
on run argv
    set groupRef to item 1 of argv
    set maxCount to (item 2 of argv as integer)

    tell application "DEVONthink"
        set g to get record with uuid groupRef
        set kids to children of g
        set totalCount to count of kids
        if maxCount > totalCount then set maxCount to totalCount

        set output to "["
        repeat with i from 1 to maxCount
            set output to output & my record_json(item i of kids)
            if i is not maxCount then set output to output & ","
        end repeat
        set output to output & "]"
        return output
    end tell
end run
'''
    data = _run_json(script, [normalized, str(limit)])
    return data if isinstance(data, list) else []


def _bulk_get_child_graph_snapshot(group_ref: str, *, limit: int = 200) -> dict[str, Any]:
    normalized = _normalize_record_ref(group_ref)
    limit = _validate_limit(limit, max_value=250)
    script = r'''
using terms from application "DEVONthink"
on esc(s)
    set t to s as text
    set t to my rep(t, "\\", "\\\\")
    set t to my rep(t, "\"", "\\\"")
    set t to my rep(t, return, "\\n")
    set t to my rep(t, linefeed, "\\n")
    set t to my rep(t, tab, "\\t")
    return t
end esc

on rep(theText, searchString, replacementString)
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to searchString
    set parts to text items of theText
    set AppleScript's text item delimiters to replacementString
    set newText to parts as text
    set AppleScript's text item delimiters to oldDelims
    return newText
end rep

on q(s)
    return "\"" & my esc(s) & "\""
end q

on maybe_q(v)
    if v is missing value then return "null"
    return my q(v as text)
end maybe_q

on maybe_bool(v)
    if v is missing value then return "null"
    if v is true then return "true"
    if v is false then return "false"
    return my q(v as text)
end maybe_bool

on text_list_json(theList)
    if theList is missing value then return "[]"
    set n to count of theList
    if n is 0 then return "[]"
    set output to "["
    repeat with i from 1 to n
        set output to output & my q(item i of theList as text)
        if i is not n then set output to output & ","
    end repeat
    return output & "]"
end text_list_json

on rec_json_from_values(recUUID, recName, recType, recLocation, recURL, recRefURL, recPath, recComment, recDatabaseUUID, recDatabaseReadOnly, recLocked, recTags, recAliases)
    return "{" & ¬
        "\"uuid\":" & my maybe_q(recUUID) & "," & ¬
        "\"name\":" & my maybe_q(recName) & "," & ¬
        "\"type\":" & my maybe_q(recType) & "," & ¬
        "\"location\":" & my maybe_q(recLocation) & "," & ¬
        "\"url\":" & my maybe_q(recURL) & "," & ¬
        "\"reference_url\":" & my maybe_q(recRefURL) & "," & ¬
        "\"path\":" & my maybe_q(recPath) & "," & ¬
        "\"comment\":" & my maybe_q(recComment) & "," & ¬
        "\"database_uuid\":" & my maybe_q(recDatabaseUUID) & "," & ¬
        "\"database_read_only\":" & my maybe_bool(recDatabaseReadOnly) & "," & ¬
        "\"locked\":" & my maybe_bool(recLocked) & "," & ¬
        "\"tags\":" & my text_list_json(recTags) & "," & ¬
        "\"aliases\":" & my text_list_json(recAliases) & ¬
        "}"
end rec_json_from_values

on content_excerpt_json(recType, recText)
    set normalizedType to ""
    if recType is not missing value then set normalizedType to (recType as text)
    set normalizedType to normalizedType as text
    if normalizedType is not "markdown" and normalizedType is not "txt" then
        return "null"
    end if
    if recText is missing value then return "null"
    set excerpt to recText as text
    if (count of excerpt) > 4096 then
        set excerpt to text 1 thru 4096 of excerpt
    end if
    return my q(excerpt)
end content_excerpt_json

on ref_json(r)
    set u to missing value
    set n to missing value
    set linkURL to missing value
    try
        set u to uuid of r
    end try
    try
        set n to name of r
    end try
    try
        set linkURL to reference URL of r
    end try
    return "{" & "\"uuid\":" & my maybe_q(u) & "," & "\"name\":" & my maybe_q(n) & "," & "\"reference_url\":" & my maybe_q(linkURL) & "}"
end ref_json

on ref_list_json(theList)
    if theList is missing value then return "[]"
    set n to count of theList
    if n is 0 then return "[]"
    set output to "["
    repeat with i from 1 to n
        set output to output & my ref_json(item i of theList)
        if i is not n then set output to output & ","
    end repeat
    return output & "]"
end ref_list_json

on maybe_item(theList, idx)
    try
        return item idx of theList
    on error
        return missing value
    end try
end maybe_item

on run argv
    set groupRef to item 1 of argv
    set maxCount to (item 2 of argv as integer)

    tell application "DEVONthink"
        set g to get record with uuid groupRef
        set kids to children of g
        set totalCount to count of kids
        if maxCount > totalCount then set maxCount to totalCount
        if maxCount is 0 then return "[]"
        if maxCount < totalCount then set kids to items 1 thru maxCount of kids

        set recUUIDs to {}
        set recNames to {}
        set recTypes to {}
        set recLocations to {}
        set recURLs to {}
        set recRefURLs to {}
        set recPaths to {}
        set recComments to {}
        set recTagsList to {}
        set recAliasesList to {}
        set recDatabaseUUIDs to {}
        set recDatabaseReadOnlyList to {}
        set recLockedList to {}
        set recOutgoingLists to {}
        set recIncomingLists to {}
        set recOutgoingWikiLists to {}
        set recIncomingWikiLists to {}
        set recPlainTexts to {}

        try
            set recUUIDs to uuid of kids
        end try
        try
            set recNames to name of kids
        end try
        try
            set recTypes to type of kids
        end try
        try
            set recLocations to location of kids
        end try
        try
            set recURLs to URL of kids
        end try
        try
            set recRefURLs to reference URL of kids
        end try
        try
            set recPaths to path of kids
        end try
        try
            set recComments to comment of kids
        end try
        try
            set recTagsList to tags of kids
        end try
        try
            set recAliasesList to aliases of kids
        end try
        try
            set recDatabaseUUIDs to uuid of (database of kids)
        end try
        try
            set recDatabaseReadOnlyList to read only of (database of kids)
        end try
        try
            set recLockedList to locked of kids
        end try
        try
            set recOutgoingLists to outgoing references of kids
        end try
        try
            set recIncomingLists to incoming references of kids
        end try
        try
            set recOutgoingWikiLists to outgoing wiki references of kids
        end try
        try
            set recIncomingWikiLists to incoming wiki references of kids
        end try
        try
            set recPlainTexts to plain text of kids
        end try

        set output to "["
        repeat with i from 1 to maxCount
            set output to output & "{"
            set output to output & "\"record\":" & my rec_json_from_values(¬
                my maybe_item(recUUIDs, i), ¬
                my maybe_item(recNames, i), ¬
                my maybe_item(recTypes, i), ¬
                my maybe_item(recLocations, i), ¬
                my maybe_item(recURLs, i), ¬
                my maybe_item(recRefURLs, i), ¬
                my maybe_item(recPaths, i), ¬
                my maybe_item(recComments, i), ¬
                my maybe_item(recDatabaseUUIDs, i), ¬
                my maybe_item(recDatabaseReadOnlyList, i), ¬
                my maybe_item(recLockedList, i), ¬
                my maybe_item(recTagsList, i), ¬
                my maybe_item(recAliasesList, i)) & ","
            set output to output & "\"outgoing_references\":" & my ref_list_json(my maybe_item(recOutgoingLists, i)) & ","
            set output to output & "\"incoming_references\":" & my ref_list_json(my maybe_item(recIncomingLists, i)) & ","
            set output to output & "\"outgoing_wiki_references\":" & my text_list_json(my maybe_item(recOutgoingWikiLists, i)) & ","
            set output to output & "\"incoming_wiki_references\":" & my text_list_json(my maybe_item(recIncomingWikiLists, i)) & ","
            set output to output & "\"content_excerpt\":" & my content_excerpt_json(my maybe_item(recTypes, i), my maybe_item(recPlainTexts, i)) & "}"
            if i is not maxCount then set output to output & ","
        end repeat
        return output & "]"
    end tell
end run
end using terms from
'''
    data = _run_json(script, [normalized, str(limit)])
    return {"items": data if isinstance(data, list) else []}


def _is_smart_group_type(type_name: str) -> bool:
    t = (type_name or "").strip().lower()
    return "smart" in t and "group" in t


def _get_smart_group_virtual_children(
    smart_group_ref: str,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """Resolve smart-group runtime children by re-running its saved search."""
    normalized = _normalize_record_ref(smart_group_ref)
    limit = _validate_limit(limit)
    script = _JSON_HELPERS + r'''
on run argv
    set smartGroupRef to item 1 of argv
    set maxCount to (item 2 of argv as integer)

    tell application "DEVONthink"
        set sg to get record with uuid smartGroupRef

        set predicatesText to ""
        try
            set predicatesText to (search predicates of sg) as text
        end try

        set scopeRecord to missing value
        try
            set scopeRecord to search group of sg
        end try
        if scopeRecord is missing value then
            set scopeRecord to root of (database of sg)
        end if

        set scopeUUID to ""
        set scopeName to ""
        try
            set scopeUUID to (uuid of scopeRecord) as text
        end try
        try
            set scopeName to (name of scopeRecord) as text
        end try

        set foundRecords to {}
        try
            set foundRecords to search predicatesText in scopeRecord
        on error
            set foundRecords to {}
        end try

        set totalCount to count of foundRecords
        if maxCount > totalCount then set maxCount to totalCount

        set output to "{"
        set output to output & "\"search_predicates\":" & my maybe_text(predicatesText) & ","
        set output to output & "\"search_group_uuid\":" & my maybe_text(scopeUUID) & ","
        set output to output & "\"search_group_name\":" & my maybe_text(scopeName) & ","
        set output to output & "\"children\":["
        repeat with i from 1 to maxCount
            set output to output & my record_json(item i of foundRecords)
            if i is not maxCount then set output to output & ","
        end repeat
        set output to output & "]}"
        return output
    end tell
end run
'''
    data = _run_json(script, [normalized, str(limit)])
    if not isinstance(data, dict):
        raise AppleScriptExecutionError("Smart-group query returned invalid output.")
    children = data.get("children")
    if not isinstance(children, list):
        data["children"] = []
    return data


def _lookup_records_with_tags(tags: list[str], *, limit: int = 40, database_uuid: str | None = None) -> list[dict[str, Any]]:
    if not tags:
        return []
    joined = "||".join(t for t in tags if t)
    if not joined:
        return []
    limit = _validate_limit(limit)
    db = (database_uuid or "").strip()

    script = _JSON_HELPERS + r'''
on split_tags(s)
    if s is "" then return {}
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "||"
    set parts to text items of s
    set AppleScript's text item delimiters to oldDelims
    return parts
end split_tags

on run argv
    set tagsJoined to item 1 of argv
    set maxCount to (item 2 of argv as integer)
    set databaseUUID to item 3 of argv

    tell application "DEVONthink"
        set tagList to my split_tags(tagsJoined)
        if databaseUUID is "" then
            set foundRecords to lookup records with tags tagList
        else
            set theDatabase to get database with uuid databaseUUID
            set foundRecords to lookup records with tags tagList in theDatabase
        end if

        set totalCount to count of foundRecords
        if maxCount > totalCount then set maxCount to totalCount

        set output to "["
        repeat with i from 1 to maxCount
            set output to output & my record_json(item i of foundRecords)
            if i is not maxCount then set output to output & ","
        end repeat
        set output to output & "]"
        return output
    end tell
end run
'''

    data = _run_json(script, [joined, str(limit), db])
    return data if isinstance(data, list) else []


def _search_records_any_tags(
    tags: list[str],
    *,
    limit: int = 40,
    database_uuid: str | None = None,
    obs: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    normalized_tags = sorted({str(tag).strip().lower() for tag in tags if str(tag).strip()})
    if not normalized_tags:
        return []
    query = "any: " + " ".join(f"tags:{tag}" for tag in normalized_tags)
    return _search_records(query, limit=limit, database_uuid=database_uuid, obs=obs, sanitize=False)


def _set_comment_and_tags(record_ref: str, comment: str | None, tags: list[str] | None) -> None:
    normalized = _normalize_record_ref(record_ref)
    comment_text = comment or ""
    tags_joined = "||".join(tags or [])

    script = r'''
on split_tags(s)
    if s is "" then return {}
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "||"
    set parts to text items of s
    set AppleScript's text item delimiters to oldDelims
    return parts
end split_tags

on merge_tags(oldTags, newTags)
    set outTags to oldTags
    repeat with t in newTags
        if (outTags does not contain (t as text)) then set end of outTags to (t as text)
    end repeat
    return outTags
end merge_tags

on run argv
    set recordRef to item 1 of argv
    set commentText to item 2 of argv
    set tagsJoined to item 3 of argv

    tell application "DEVONthink"
        set r to get record with uuid recordRef
        if commentText is not "" then
            set comment of r to commentText
        end if
        if tagsJoined is not "" then
            set existingTags to {}
            try
                set existingTags to tags of r
            end try
            set merged to my merge_tags(existingTags, my split_tags(tagsJoined))
            set tags of r to merged
        end if
    end tell
end run
'''

    _run_osascript(script, [normalized, comment_text, tags_joined])


def _set_custom_metadata(record_ref: str, key: str, value: str) -> None:
    normalized = _normalize_record_ref(record_ref)
    script = r'''
on run argv
    set recordRef to item 1 of argv
    set metadataKey to item 2 of argv
    set metadataValue to item 3 of argv

    tell application "DEVONthink"
        set r to get record with uuid recordRef
        add custom meta data metadataKey for r to metadataValue
    end tell
end run
'''
    _run_osascript(script, [normalized, key, value])


def _get_links_of(record_ref: str) -> list[str]:
    normalized = _normalize_record_ref(record_ref)
    script = _JSON_HELPERS + r'''
on run argv
    set recordRef to item 1 of argv

    tell application "DEVONthink"
        set r to get record with uuid recordRef
        set linksList to get links of r
        return my list_json(linksList)
    end tell
end run
'''
    data = _run_json(script, [normalized])
    return [str(v) for v in (data or []) if isinstance(v, str)]





def _get_record_edge_snapshot(record_ref: str) -> dict[str, Any]:
    snapshots = _bulk_get_edge_snapshots([record_ref])
    normalized = _normalize_record_ref(record_ref)
    snapshot = snapshots.get(normalized)
    if snapshot is None:
        raise AppleScriptExecutionError(f"record_not_found: Could not resolve record {normalized}.")
    return snapshot


def _bulk_get_edge_snapshots(record_refs: list[str]) -> dict[str, dict[str, Any]]:
    normalized_refs: list[str] = []
    for ref in record_refs:
        try:
            normalized = _normalize_record_ref(ref)
        except ValueError:
            continue
        if normalized not in normalized_refs:
            normalized_refs.append(normalized)
    if not normalized_refs:
        return {}

    joined = "||".join(normalized_refs)
    script = r'''
using terms from application "DEVONthink"
on esc(s)
    set t to s as text
    set t to my rep(t, "\\", "\\\\")
    set t to my rep(t, "\"", "\\\"")
    set t to my rep(t, return, "\\n")
    set t to my rep(t, linefeed, "\\n")
    set t to my rep(t, tab, "\\t")
    return t
end esc

on rep(theText, searchString, replacementString)
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to searchString
    set parts to text items of theText
    set AppleScript's text item delimiters to replacementString
    set newText to parts as text
    set AppleScript's text item delimiters to oldDelims
    return newText
end rep

on q(s)
    return "\"" & my esc(s) & "\""
end q

on maybe_q(v)
    if v is missing value then return "null"
    return my q(v as text)
end maybe_q

on maybe_bool(v)
    if v is missing value then return "null"
    if v is true then return "true"
    if v is false then return "false"
    return my q(v as text)
end maybe_bool

on split_refs(s)
    if s is "" then return {}
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "||"
    set parts to text items of s
    set AppleScript's text item delimiters to oldDelims
    return parts
end split_refs

on text_list_json(theList)
    if theList is missing value then return "[]"
    set n to count of theList
    if n is 0 then return "[]"
    set output to "["
    repeat with i from 1 to n
        set output to output & my q(item i of theList as text)
        if i is not n then set output to output & ","
    end repeat
    return output & "]"
end text_list_json

on content_excerpt_json(recType, recText)
    set normalizedType to ""
    if recType is not missing value then set normalizedType to (recType as text)
    set normalizedType to normalizedType as text
    if normalizedType is not "markdown" and normalizedType is not "txt" then
        return "null"
    end if
    if recText is missing value then return "null"
    set excerpt to recText as text
    if (count of excerpt) > 4096 then
        set excerpt to text 1 thru 4096 of excerpt
    end if
    return my q(excerpt)
end content_excerpt_json

on rec_json(r)
    set r_uuid to missing value
    set r_id to missing value
    set r_name to missing value
    set r_type to missing value
    set r_location to missing value
    set r_url to missing value
    set r_reference_url to missing value
    set r_path to missing value
    set r_comment to missing value
    set r_tags to {}
    set r_aliases to {}
    set r_database_uuid to missing value
    set r_database_read_only to missing value
    set r_locked to missing value

    try
        set r_uuid to uuid of r
    end try
    try
        set r_id to id of r
    end try
    try
        set r_name to name of r
    end try
    try
        set r_type to type of r
    end try
    try
        set r_location to location of r
    end try
    try
        set r_url to URL of r
    end try
    try
        set r_reference_url to reference URL of r
    end try
    try
        set r_path to path of r
    end try
    try
        set r_comment to comment of r
    end try
    try
        set r_tags to tags of r
    end try
    try
        set r_aliases to aliases of r
    end try
    try
        set r_database_uuid to uuid of (database of r)
    end try
    try
        set r_database_read_only to read only of (database of r)
    end try
    try
        set r_locked to locked of r
    end try

    return "{" & ¬
        "\"uuid\":" & my maybe_q(r_uuid) & "," & ¬
        "\"id\":" & my maybe_q(r_id) & "," & ¬
        "\"name\":" & my maybe_q(r_name) & "," & ¬
        "\"type\":" & my maybe_q(r_type) & "," & ¬
        "\"location\":" & my maybe_q(r_location) & "," & ¬
        "\"url\":" & my maybe_q(r_url) & "," & ¬
        "\"reference_url\":" & my maybe_q(r_reference_url) & "," & ¬
        "\"path\":" & my maybe_q(r_path) & "," & ¬
        "\"comment\":" & my maybe_q(r_comment) & "," & ¬
        "\"database_uuid\":" & my maybe_q(r_database_uuid) & "," & ¬
        "\"database_read_only\":" & my maybe_bool(r_database_read_only) & "," & ¬
        "\"locked\":" & my maybe_bool(r_locked) & "," & ¬
        "\"tags\":" & my text_list_json(r_tags) & "," & ¬
        "\"aliases\":" & my text_list_json(r_aliases) & ¬
        "}"
end rec_json

on ref_json(r)
    set u to missing value
    set n to missing value
    set linkURL to missing value
    try
        set u to uuid of r
    end try
    try
        set n to name of r
    end try
    try
        set linkURL to reference URL of r
    end try
    return "{" & "\"uuid\":" & my maybe_q(u) & "," & "\"name\":" & my maybe_q(n) & "," & "\"reference_url\":" & my maybe_q(linkURL) & "}"
end ref_json

on ref_list_json(theList)
    if theList is missing value then return "[]"
    set n to count of theList
    if n is 0 then return "[]"
    set output to "["
    repeat with i from 1 to n
        set output to output & my ref_json(item i of theList)
        if i is not n then set output to output & ","
    end repeat
    return output & "]"
end ref_list_json

on run argv
    set refsJoined to item 1 of argv
    set refList to my split_refs(refsJoined)

    tell application "DEVONthink"
        set output to "["
        set emitted to 0
        repeat with refUUID in refList
            set recUUID to refUUID as text
            try
                set r to get record with uuid recUUID
            on error
                set r to missing value
            end try
            if r is not missing value then
                set outgoingRefs to {}
                set incomingRefs to {}
                set outgoingWikiRefs to {}
                set incomingWikiRefs to {}
                set recType to missing value
                set recPlainText to missing value
                try
                    set outgoingRefs to outgoing references of r
                end try
                try
                    set incomingRefs to incoming references of r
                end try
                try
                    set outgoingWikiRefs to outgoing wiki references of r
                end try
                try
                    set incomingWikiRefs to incoming wiki references of r
                end try
                try
                    set recType to type of r
                end try
                if recType is not missing value then
                    set recTypeText to recType as text
                    if recTypeText is "markdown" or recTypeText is "txt" then
                        try
                            set recPlainText to plain text of r
                        end try
                    end if
                end if

                if emitted > 0 then set output to output & ","
                set emitted to emitted + 1
                set output to output & "{"
                set output to output & "\"record\":" & my rec_json(r) & ","
                set output to output & "\"outgoing_references\":" & my ref_list_json(outgoingRefs) & ","
                set output to output & "\"incoming_references\":" & my ref_list_json(incomingRefs) & ","
                set output to output & "\"outgoing_wiki_references\":" & my text_list_json(outgoingWikiRefs) & ","
                set output to output & "\"incoming_wiki_references\":" & my text_list_json(incomingWikiRefs) & ","
                set output to output & "\"content_excerpt\":" & my content_excerpt_json(recType, recPlainText) & "}"
            end if
        end repeat
        return output & "]"
    end tell
end run
end using terms from
'''
    data = _run_json(script, [joined])
    rows = data if isinstance(data, list) else []
    snapshots: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rec = row.get("record") or {}
        rec_uuid = _coerce_text(rec.get("uuid")).strip()
        if rec_uuid:
            snapshots[rec_uuid] = row
    return snapshots


def _build_edge(to_ref: str, reason_code: str, edge_type: str, strength: str, evidence: str) -> dict[str, Any]:
    return {
        "to": to_ref,
        "reason_code": reason_code,
        "edge_type": edge_type,
        "strength": strength,
        "weight": SIGNAL_WEIGHTS.get(reason_code, 1),
        "evidence": evidence,
    }


def _extract_wikilinks(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in WIKILINK_RE.findall(text or ""):
        cleaned = match.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            links.append(cleaned)
    return links


def _edge_target_uuid_set(edges: list[dict[str, Any]], obs: dict[str, int] | None = None) -> set[str]:
    out: set[str] = set()
    for edge in edges or []:
        target = _coerce_text((edge or {}).get("uuid"), obs).strip() or _coerce_text((edge or {}).get("reference_url"), obs).strip()
        match = UUID_RE.search(target)
        if match:
            out.add(match.group(0))
    return out


def _edge_title_set(values: list[str], *, obs: dict[str, int] | None = None) -> set[str]:
    out: set[str] = set()
    for value in values or []:
        cleaned = _coerce_text(value, obs).strip().lower()
        if cleaned:
            out.add(cleaned)
    return out


def _extract_item_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in ITEM_LINK_RE.findall(text or ""):
        if match not in seen:
            seen.add(match)
            links.append(match)
    return links


def _resolve_title_candidates(
    title: str,
    *,
    database_uuid: str | None = None,
    limit: int = 10,
    obs: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    title = _sanitize_search_query(_coerce_text(title, obs)).strip()
    if not title:
        return []
    candidates = _search_records(title, limit=limit, database_uuid=database_uuid, obs=obs)
    exact = [r for r in candidates if _coerce_text(r.get("name"), obs).strip().lower() == title.lower()]
    return exact or candidates


# Maximum titles per batched OR-search query. DEVONthink's search has no documented
# upper bound on query length, but very long disjunctions can hit a `-50` invalid
# argument error. 20 keeps the query well under that threshold while still giving
# 20x speedup over per-title round trips.
_TITLE_BATCH_CHUNK_SIZE = 20

# Per-chunk hard ceiling on returned hits, regardless of titles*per_title math, to
# avoid pulling unnecessarily large result sets back through osascript stdout.
_TITLE_BATCH_AGGREGATE_LIMIT_MAX = 200


def _resolve_title_candidates_batch(
    titles: list[str],
    *,
    database_uuid: str | None = None,
    limit_per_title: int = 5,
    obs: dict[str, int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Resolve many titles in a single OR-search per chunk instead of N round trips.

    Mirrors `_resolve_title_candidates` semantics: if the search yields a record
    whose name equals the sanitized title (case-insensitive), only exact matches
    are returned for that title; otherwise substring-name matches in the result
    set are returned. Falls back to per-title `_resolve_title_candidates` for any
    chunk where the batched query fails (e.g., DEVONthink rejects the syntax).

    Returns: dict[input_title -> list[record]]. Every input title is a key (even
    if its match list is empty), so callers can iterate the original list and
    look up results without missing-key handling.
    """
    buckets: dict[str, list[dict[str, Any]]] = {raw: [] for raw in titles}

    sanitized: dict[str, str] = {}
    for raw in titles:
        clean = _sanitize_search_query(_coerce_text(raw, obs)).strip()
        if clean:
            sanitized[raw] = clean
    if not sanitized:
        return buckets

    raws = list(sanitized.keys())
    for chunk_start in range(0, len(raws), _TITLE_BATCH_CHUNK_SIZE):
        chunk_raws = raws[chunk_start : chunk_start + _TITLE_BATCH_CHUNK_SIZE]
        # Group chunk titles by their sanitized form so duplicates share work.
        chunk_clean_to_raws: dict[str, list[str]] = defaultdict(list)
        for raw in chunk_raws:
            chunk_clean_to_raws[sanitized[raw]].append(raw)
        unique_clean = list(chunk_clean_to_raws.keys())

        # DEVONthink's `any:` prefix only honors field-qualified atoms (e.g.,
        # `tags:foo`, `name:bar`); bare quoted phrases are silently dropped, so
        # we use the boolean `OR` operator on quoted phrases instead. Quoting
        # every phrase keeps multi-word titles intact and gives single-word
        # titles consistent treatment, matching the free-text semantics of
        # `_search_records(title)` used by `_resolve_title_candidates`.
        atoms = [f'"{clean}"' for clean in unique_clean]
        query = " OR ".join(atoms)

        aggregate_limit = min(
            max(limit_per_title * len(unique_clean), limit_per_title),
            _TITLE_BATCH_AGGREGATE_LIMIT_MAX,
        )

        try:
            hits = _search_records(
                query,
                limit=aggregate_limit,
                database_uuid=database_uuid,
                obs=obs,
                sanitize=False,
            )
        except AppleScriptExecutionError:
            for raw in chunk_raws:
                try:
                    buckets[raw] = _resolve_title_candidates(
                        raw,
                        database_uuid=database_uuid,
                        limit=limit_per_title,
                        obs=obs,
                    )
                except AppleScriptExecutionError:
                    pass
            continue

        for clean, raws_for_clean in chunk_clean_to_raws.items():
            clean_lower = clean.lower()
            exact: list[dict[str, Any]] = []
            substring: list[dict[str, Any]] = []
            seen_uuids: set[str] = set()
            for hit in hits:
                uid = _coerce_text(hit.get("uuid"), obs).strip()
                if not uid or uid in seen_uuids:
                    continue
                name_lower = _coerce_text(hit.get("name"), obs).strip().lower()
                if name_lower == clean_lower:
                    exact.append(hit)
                    seen_uuids.add(uid)
                elif clean_lower and clean_lower in name_lower:
                    substring.append(hit)
                    seen_uuids.add(uid)
            chosen = (exact or substring)[:limit_per_title]
            for raw in raws_for_clean:
                buckets[raw] = chosen

    return buckets


def _tokenize(text: str, *, min_len: int = 4) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", (text or "").lower())
    return [w for w in words if len(w) >= min_len and w not in STOPWORDS]


def _brief_description(record: dict[str, Any], text: str) -> str:
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^#+\s*", "", line).strip()
        if line:
            return line[:180]
    return f"{record.get('type') or 'record'} in {record.get('location') or 'unknown location'}"


def _md_link(name: str, ref_url: str) -> str:
    """Render a Markdown link `[name](url)` safely for hub/list output.

    Escapes `]` and `\\` in the link text and `)` and `\\` in the URL so that
    a record name containing brackets or a URL with parentheses cannot break
    the link out of its enclosing `[ ... ]( ... )` syntax.
    """
    text = (name or "Untitled").replace("\\", "\\\\").replace("]", r"\]")
    target = (ref_url or "").replace("\\", "\\\\").replace(")", r"\)")
    return f"[{text}]({target})"


def _audit_record_impl(
    record_ref: str,
    *,
    include_text_scan: bool = True,
    obs: dict[str, int] | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    obs = obs or _new_observability()
    warnings: list[str] = []

    snapshot = _get_record_edge_snapshot(record_ref)
    rec = snapshot.get("record") or {}

    text = ""
    item_links: list[str] = []
    wikilinks: list[str] = []
    api_links: list[str] = []

    if include_text_scan:
        text = _coerce_text(snapshot.get("content_excerpt"), obs)
        if text:
            item_links = _extract_item_links(text)
            wikilinks = _extract_wikilinks(text)

    outgoing_edges: list[dict[str, Any]] = []
    incoming_edges: list[dict[str, Any]] = []
    wikilink_edges: list[dict[str, Any]] = []
    unresolved_references: list[dict[str, str]] = []

    outgoing_records = snapshot.get("outgoing_references") or []
    incoming_records = snapshot.get("incoming_references") or []
    outgoing_wiki = [str(v) for v in (snapshot.get("outgoing_wiki_references") or []) if isinstance(v, str)]
    incoming_wiki = [str(v) for v in (snapshot.get("incoming_wiki_references") or []) if isinstance(v, str)]

    authoritative_signal = bool(outgoing_records or incoming_records or outgoing_wiki or incoming_wiki)

    outgoing_edges.extend(
        _build_edge(
            r.get("uuid") or r.get("reference_url") or "unknown",
            "outgoing_reference",
            "item_link",
            "strong",
            "authoritative outgoing references property",
        )
        for r in outgoing_records
        if isinstance(r, dict)
    )
    incoming_edges.extend(
        _build_edge(
            r.get("uuid") or r.get("reference_url") or "unknown",
            "incoming_reference",
            "item_link",
            "strong",
            "authoritative incoming references property",
        )
        for r in incoming_records
        if isinstance(r, dict)
    )

    for r in [*outgoing_records, *incoming_records]:
        if not isinstance(r, dict):
            continue
        target_uuid = _coerce_text(r.get("uuid"), obs).strip()
        if not target_uuid:
            unresolved_references.append(
                {
                    "kind": "item_link",
                    "direction": "outgoing_or_incoming",
                    "target": _coerce_text(r.get("reference_url"), obs) or "unknown",
                    "reason": "missing_uuid",
                }
            )
            continue
        try:
            _get_record(target_uuid)
        except Exception:  # noqa: BLE001
            unresolved_references.append(
                {
                    "kind": "item_link",
                    "direction": "outgoing_or_incoming",
                    "target": target_uuid,
                    "reason": "unresolvable_target",
                }
            )

    wikilink_edges.extend(
        _build_edge(v, "outgoing_wiki_reference", "wiki_link", "strong", "authoritative outgoing Wiki references property")
        for v in outgoing_wiki
    )
    wikilink_edges.extend(
        _build_edge(v, "incoming_wiki_reference", "wiki_link", "strong", "authoritative incoming Wiki references property")
        for v in incoming_wiki
    )

    if not authoritative_signal:
        try:
            api_links = _get_links_of(record_ref)
        except AppleScriptExecutionError as exc:
            warnings.append(f"Could not read links via DEVONthink command: {exc}")
            api_links = []

        reference_url = rec.get("reference_url") or ""
        incoming: list[dict[str, Any]] = []
        if reference_url:
            try:
                mentions = _search_records(f'"{reference_url}"', limit=50, database_uuid=rec.get("database_uuid"), obs=obs)
                for m in mentions:
                    if (m.get("uuid") or "") != (rec.get("uuid") or ""):
                        incoming.append(m)
            except AppleScriptExecutionError as exc:
                warnings.append(f"Could not search incoming mentions: {exc}")

        explicit_out = sorted({*api_links, *item_links})
        outgoing_edges.extend(
            _build_edge(link, "explicit_item_link", "item_link", "medium", "fallback from content/get links of")
            for link in explicit_out
        )
        incoming_edges.extend(
            _build_edge(m.get("uuid") or m.get("reference_url") or "unknown", "explicit_mention", "mention_text", "medium", "fallback mention search")
            for m in incoming
        )
        wikilink_edges.extend(
            _build_edge(name, "wikilink", "wikilink", "medium", "fallback [[wikilink]] scan") for name in wikilinks
        )

    risk_flags: list[str] = []
    if not rec.get("aliases"):
        risk_flags.append("no_aliases")
    if not rec.get("tags"):
        risk_flags.append("no_tags")
    if not rec.get("comment"):
        risk_flags.append("no_comment")
    if not outgoing_edges:
        risk_flags.append("no_outgoing")
    if not incoming_edges:
        risk_flags.append("no_incoming")

    all_edges = [*incoming_edges, *outgoing_edges, *wikilink_edges]
    signal_source = "authoritative" if authoritative_signal else ("structural" if all_edges else "inferred")
    lowest_signal_tier = _lowest_signal_tier_for_edges(all_edges)

    audit = {
        "record": rec,
        "edges": {
            "incoming": incoming_edges,
            "outgoing": outgoing_edges,
            "wikilinks": wikilink_edges,
            "counts": {
                "incoming": len(incoming_edges),
                "outgoing": len(outgoing_edges),
                "wikilinks": len(wikilink_edges),
            },
        },
        "risk_flags": risk_flags,
        "text_scan": {
            "item_links": item_links,
            "wikilinks": wikilinks,
        },
        "signal_source": signal_source,
        "lowest_signal_tier": lowest_signal_tier,
        "unresolved_references": unresolved_references,
    }
    return audit, warnings, obs


def _audit_from_edge_snapshot(
    snapshot: dict[str, Any],
    *,
    include_text_scan: bool = False,
    item_links: list[str] | None = None,
    wikilinks: list[str] | None = None,
) -> dict[str, Any]:
    rec = snapshot.get("record") or {}
    outgoing_records = snapshot.get("outgoing_references") or []
    incoming_records = snapshot.get("incoming_references") or []
    outgoing_wiki = [str(v) for v in (snapshot.get("outgoing_wiki_references") or []) if isinstance(v, str)]
    incoming_wiki = [str(v) for v in (snapshot.get("incoming_wiki_references") or []) if isinstance(v, str)]

    outgoing_edges = [
        _build_edge(
            r.get("uuid") or r.get("reference_url") or "unknown",
            "outgoing_reference",
            "item_link",
            "strong",
            "authoritative outgoing references property",
        )
        for r in outgoing_records
        if isinstance(r, dict)
    ]
    incoming_edges = [
        _build_edge(
            r.get("uuid") or r.get("reference_url") or "unknown",
            "incoming_reference",
            "item_link",
            "strong",
            "authoritative incoming references property",
        )
        for r in incoming_records
        if isinstance(r, dict)
    ]
    wikilink_edges = [
        *[
            _build_edge(v, "outgoing_wiki_reference", "wiki_link", "strong", "authoritative outgoing Wiki references property")
            for v in outgoing_wiki
        ],
        *[
            _build_edge(v, "incoming_wiki_reference", "wiki_link", "strong", "authoritative incoming Wiki references property")
            for v in incoming_wiki
        ],
    ]

    risk_flags: list[str] = []
    if not rec.get("aliases"):
        risk_flags.append("no_aliases")
    if not rec.get("tags"):
        risk_flags.append("no_tags")
    if not rec.get("comment"):
        risk_flags.append("no_comment")
    if not outgoing_edges:
        risk_flags.append("no_outgoing")
    if not incoming_edges:
        risk_flags.append("no_incoming")

    all_edges = [*incoming_edges, *outgoing_edges, *wikilink_edges]
    return {
        "record": rec,
        "edges": {
            "incoming": incoming_edges,
            "outgoing": outgoing_edges,
            "wikilinks": wikilink_edges,
            "counts": {
                "incoming": len(incoming_edges),
                "outgoing": len(outgoing_edges),
                "wikilinks": len(wikilink_edges),
            },
        },
        "risk_flags": risk_flags,
        "text_scan": {
            "item_links": item_links or [],
            "wikilinks": wikilinks or [],
        } if include_text_scan else {"item_links": [], "wikilinks": []},
        "signal_source": "authoritative" if all_edges else "inferred",
        "lowest_signal_tier": _lowest_signal_tier_for_edges(all_edges),
        "unresolved_references": [],
    }


def _audit_folder_snapshot_items(
    items: list[dict[str, Any]],
    *,
    obs: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, int]]:
    obs = obs or _new_observability()
    warnings: list[str] = []
    audits: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []

    title_to_uuid: dict[str, str] = {}
    reference_url_to_mentions: dict[str, list[str]] = defaultdict(list)
    uuid_to_mentions: dict[str, list[str]] = defaultdict(list)

    for item in items:
        rec = item.get("record") or {}
        rec_uuid = _coerce_text(rec.get("uuid"), obs).strip()
        rec_name = _coerce_text(rec.get("name"), obs).strip().lower()
        if rec_uuid and rec_name and rec_name not in title_to_uuid:
            title_to_uuid[rec_name] = rec_uuid

    for item in items:
        rec = item.get("record") or {}
        rec_uuid = _coerce_text(rec.get("uuid"), obs).strip()
        content_excerpt = _coerce_text(item.get("content_excerpt"), obs)
        if not content_excerpt:
            continue
        for link in _extract_item_links(content_excerpt):
            if rec_uuid:
                reference_url_to_mentions[link].append(rec_uuid)
                uuid_match = UUID_RE.search(link)
                if uuid_match:
                    uuid_to_mentions[uuid_match.group(0)].append(rec_uuid)

    for item in items:
        rec = item.get("record") or {}
        rec_uuid = _coerce_text(rec.get("uuid"), obs).strip()
        rec_name = _coerce_text(rec.get("name"), obs)
        content_excerpt = _coerce_text(item.get("content_excerpt"), obs)
        item_links = _extract_item_links(content_excerpt) if content_excerpt else []
        wikilinks = _extract_wikilinks(content_excerpt) if content_excerpt else []

        outgoing_records = item.get("outgoing_references") or []
        incoming_records = item.get("incoming_references") or []
        outgoing_wiki = [str(v).strip() for v in (item.get("outgoing_wiki_references") or []) if str(v).strip()]
        incoming_wiki = [str(v).strip() for v in (item.get("incoming_wiki_references") or []) if str(v).strip()]

        outgoing_edges: list[dict[str, Any]] = []
        incoming_edges: list[dict[str, Any]] = []
        wikilink_edges: list[dict[str, Any]] = []
        unresolved_references: list[dict[str, str]] = []

        authoritative_signal = bool(outgoing_records or incoming_records or outgoing_wiki or incoming_wiki)

        outgoing_edges.extend(
            _build_edge(
                r.get("uuid") or r.get("reference_url") or "unknown",
                "outgoing_reference",
                "item_link",
                "strong",
                "authoritative outgoing references property",
            )
            for r in outgoing_records
            if isinstance(r, dict)
        )
        incoming_edges.extend(
            _build_edge(
                r.get("uuid") or r.get("reference_url") or "unknown",
                "incoming_reference",
                "item_link",
                "strong",
                "authoritative incoming references property",
            )
            for r in incoming_records
            if isinstance(r, dict)
        )
        wikilink_edges.extend(
            _build_edge(v, "outgoing_wiki_reference", "wiki_link", "strong", "authoritative outgoing Wiki references property")
            for v in outgoing_wiki
        )
        wikilink_edges.extend(
            _build_edge(v, "incoming_wiki_reference", "wiki_link", "strong", "authoritative incoming Wiki references property")
            for v in incoming_wiki
        )

        if not authoritative_signal:
            explicit_out = sorted(set(item_links))
            outgoing_edges.extend(
                _build_edge(link, "explicit_item_link", "item_link", "medium", "bulk content item-link scan")
                for link in explicit_out
            )

            incoming_mentions = []
            reference_url = _coerce_text(rec.get("reference_url"), obs).strip()
            if reference_url:
                incoming_mentions.extend(reference_url_to_mentions.get(reference_url, []))
            if rec_uuid:
                incoming_mentions.extend(uuid_to_mentions.get(rec_uuid, []))
            dedup_incoming = [src for src in sorted(set(incoming_mentions)) if src and src != rec_uuid]
            incoming_edges.extend(
                _build_edge(src_uuid, "explicit_mention", "mention_text", "medium", "bulk folder mention scan")
                for src_uuid in dedup_incoming
            )

            wikilink_edges.extend(
                _build_edge(name, "wikilink", "wikilink", "medium", "bulk folder [[wikilink]] scan")
                for name in wikilinks
            )

            for name in wikilinks:
                resolved = title_to_uuid.get(name.strip().lower())
                if not resolved:
                    unresolved_references.append(
                        {
                            "kind": "wikilink",
                            "direction": "outgoing",
                            "target": name,
                            "reason": "unresolvable_local_target",
                        }
                    )

        risk_flags: list[str] = []
        if not rec.get("aliases"):
            risk_flags.append("no_aliases")
        if not rec.get("tags"):
            risk_flags.append("no_tags")
        if not rec.get("comment"):
            risk_flags.append("no_comment")
        if not outgoing_edges:
            risk_flags.append("no_outgoing")
        if not incoming_edges:
            risk_flags.append("no_incoming")

        all_edges = [*incoming_edges, *outgoing_edges, *wikilink_edges]
        signal_source = "authoritative" if authoritative_signal else ("structural" if all_edges else "inferred")
        lowest_signal_tier = _lowest_signal_tier_for_edges(all_edges)

        audit = {
            "record": rec,
            "edges": {
                "incoming": incoming_edges,
                "outgoing": outgoing_edges,
                "wikilinks": wikilink_edges,
                "counts": {
                    "incoming": len(incoming_edges),
                    "outgoing": len(outgoing_edges),
                    "wikilinks": len(wikilink_edges),
                },
            },
            "risk_flags": risk_flags,
            "text_scan": {
                "item_links": item_links,
                "wikilinks": wikilinks,
            },
            "signal_source": signal_source,
            "lowest_signal_tier": lowest_signal_tier,
            "unresolved_references": unresolved_references,
        }
        audits.append(audit)

        counts = audit["edges"]["counts"]
        if counts["incoming"] + counts["outgoing"] + counts["wikilinks"] <= 1:
            weak.append(
                {
                    "uuid": rec_uuid,
                    "name": rec_name,
                    "risk_flags": risk_flags,
                }
            )

    return audits, weak, warnings, obs


def devonthink_link_resolve(record_ref: str) -> dict[str, Any]:
    started = time.time()
    inputs = {"record_ref": record_ref}
    try:
        rec = _get_record(record_ref)
        canonical_ref = rec.get("reference_url") or ("x-devonthink-item://" + (rec.get("uuid") or ""))
        return _response(
            tool="devonthink-link-resolve",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "record": rec,
                "canonical": {
                    "uuid": rec.get("uuid"),
                    "item_link": canonical_ref,
                    "name": rec.get("name"),
                    "database_uuid": rec.get("database_uuid"),
                },
            },
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-resolve", started_at=started, inputs=inputs, ok=False, error=str(exc))


def devonthink_link_audit_record(
    record_ref: str,
    include_text_scan: bool = False,
    mode: str = "authoritative",
) -> dict[str, Any]:
    started = time.time()
    if include_text_scan and mode == "authoritative":
        mode = "full"
    elif not include_text_scan and mode == "full":
        include_text_scan = True
    inputs = {"record_ref": record_ref, "include_text_scan": include_text_scan, "mode": mode}
    obs = _new_observability()
    try:
        if mode not in {"authoritative", "full"}:
            raise ValueError("mode must be 'authoritative' or 'full'.")
        if mode == "authoritative":
            snapshot = _get_record_edge_snapshot(record_ref)
            audit = _audit_from_edge_snapshot(snapshot, include_text_scan=False)
            warnings: list[str] = []
        else:
            audit, warnings, obs = _audit_record_impl(record_ref, include_text_scan=True, obs=obs)
        return _response(
            tool="devonthink-link-audit-record",
            started_at=started,
            inputs=inputs,
            ok=True,
            data=audit,
            warnings=warnings,
            stats=_merge_observability({"risk_flag_count": len(audit.get("risk_flags") or [])}, obs),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-audit-record", started_at=started, inputs=inputs, ok=False, error=str(exc))


def devonthink_link_audit_folder(folder_ref: str, limit: int = 50) -> dict[str, Any]:
    started = time.time()
    inputs = {"folder_ref": folder_ref, "limit": limit}
    warnings: list[str] = []
    obs = _new_observability()

    try:
        limit = _validate_limit(limit, field="limit", max_value=200)
        snapshot = _bulk_get_child_graph_snapshot(folder_ref, limit=limit)
        items = snapshot.get("items") or []
        audits, weak, local_warnings, obs = _audit_folder_snapshot_items(items, obs=obs)
        warnings.extend(local_warnings)

        tag_clusters: dict[str, int] = defaultdict(int)
        for a in audits:
            for t in (a.get("record", {}).get("tags") or []):
                if t:
                    tag_clusters[str(t)] += 1

        data = {
            "folder": _get_record(folder_ref),
            "audited_count": len(audits),
            "weakly_connected": weak,
            "tag_clusters": sorted(
                [{"tag": tag, "count": count} for tag, count in tag_clusters.items()],
                key=lambda x: x["count"],
                reverse=True,
            )[:20],
            "records": audits,
        }
        outgoing_any = sum(1 for a in audits if (a.get("edges", {}).get("counts", {}).get("outgoing", 0) > 0))
        incoming_any = sum(1 for a in audits if (a.get("edges", {}).get("counts", {}).get("incoming", 0) > 0))
        both = sum(
            1
            for a in audits
            if (a.get("edges", {}).get("counts", {}).get("incoming", 0) > 0)
            and (a.get("edges", {}).get("counts", {}).get("outgoing", 0) > 0)
        )
        total = len(audits)
        data["link_coverage"] = {
            "records_with_any_outgoing": outgoing_any,
            "records_with_any_incoming": incoming_any,
            "records_with_both": both,
            "total_records": total,
            "coverage_pct": round((both / total) * 100, 2) if total else 0.0,
        }
        return _response(
            tool="devonthink-link-audit-folder",
            started_at=started,
            inputs=inputs,
            ok=True,
            data=data,
            warnings=warnings,
            stats=_merge_observability(
                {
                    "weakly_connected_count": len(weak),
                    "bulk_snapshot_count": len(items),
                },
                obs,
            ),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-audit-folder", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def devonthink_link_map_neighborhood(record_ref: str, radius: int = 1, per_hop_limit: int = 20) -> dict[str, Any]:
    started = time.time()
    inputs = {"record_ref": record_ref, "radius": radius, "per_hop_limit": per_hop_limit}
    warnings: list[str] = []
    obs = _new_observability()

    try:
        if radius < 1 or radius > 5:
            raise ValueError("radius must be between 1 and 5.")
        per_hop_limit = _validate_limit(per_hop_limit, field="per_hop_limit")

        root_snapshot = _get_record_edge_snapshot(record_ref)
        root = root_snapshot.get("record") or {}
        visited: set[str] = set()
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        frontier = [root.get("uuid") or ""]

        for hop in range(1, radius + 1):
            next_frontier: list[str] = []
            batch = [current_uuid for current_uuid in frontier if current_uuid and current_uuid not in visited]
            snapshots = _bulk_get_edge_snapshots(batch)

            # First pass: build edges, collect wiki-link targets per database
            # for batched resolution. Splitting by database keeps each search
            # scoped correctly even when the frontier spans multiple databases.
            pending_wiki_targets: dict[str | None, list[str]] = defaultdict(list)
            for current_uuid in batch:
                if not current_uuid or current_uuid in visited:
                    continue
                visited.add(current_uuid)
                try:
                    snapshot = snapshots.get(current_uuid)
                    if snapshot is None:
                        continue
                    curr = snapshot.get("record") or {}
                    nodes[curr.get("uuid") or current_uuid] = curr

                    out_edges = [
                        (_build_edge(e.get("uuid") or e.get("reference_url") or "unknown", "outgoing_reference", "item_link", "strong", "authoritative outgoing references property"), "outgoing")
                        for e in (snapshot.get("outgoing_references") or [])[:per_hop_limit]
                        if isinstance(e, dict)
                    ]
                    in_edges = [
                        (_build_edge(e.get("uuid") or e.get("reference_url") or "unknown", "incoming_reference", "item_link", "strong", "authoritative incoming references property"), "incoming")
                        for e in (snapshot.get("incoming_references") or [])[:per_hop_limit]
                        if isinstance(e, dict)
                    ]
                    wiki_edges = [
                        (_build_edge(title, "outgoing_wiki_reference", "wiki_link", "strong", "authoritative outgoing Wiki references property"), "outgoing")
                        for title in (snapshot.get("outgoing_wiki_references") or [])[:per_hop_limit]
                    ]
                    for e, direction in [*out_edges, *in_edges, *wiki_edges]:
                        target = e["to"]
                        edge = {
                            "from": current_uuid,
                            "to": target,
                            "hop": hop,
                            "direction": direction,
                            **e,
                        }
                        edges.append(edge)
                        target_uuid_match = UUID_RE.search(target)
                        if target_uuid_match:
                            next_frontier.append(target_uuid_match.group(0))
                        elif e.get("edge_type") in {"wiki_link", "wikilink"}:
                            target_text = _coerce_text(target, obs)
                            if target_text:
                                pending_wiki_targets[curr.get("database_uuid")].append(target_text)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"Neighborhood step failed for {current_uuid}: {exc}")

            # Second pass: one batched OR-search per database covers every wiki
            # target collected during the first pass. Even with a single wiki
            # target this is at most one extra osascript call per database.
            for db_uuid, wiki_titles in pending_wiki_targets.items():
                if not wiki_titles:
                    continue
                try:
                    resolved = _resolve_title_candidates_batch(
                        wiki_titles,
                        database_uuid=db_uuid,
                        limit_per_title=3,
                        obs=obs,
                    )
                except AppleScriptExecutionError as exc:
                    warnings.append(f"Wiki title resolution failed for db={db_uuid or 'global'}: {exc}")
                    continue
                for hits in resolved.values():
                    for candidate in hits:
                        candidate_uuid = _coerce_text(candidate.get("uuid"), obs)
                        if candidate_uuid and UUID_RE.fullmatch(candidate_uuid):
                            next_frontier.append(candidate_uuid)
            frontier = next_frontier

        return _response(
            tool="devonthink-link-map-neighborhood",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "root": root,
                "nodes": list(nodes.values()),
                "edges": edges,
            },
            warnings=warnings,
            stats=_merge_observability(
                {
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                    "lowest_signal_tier": _lowest_signal_tier_for_edges(edges),
                },
                obs,
            ),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-map-neighborhood", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def devonthink_link_find_orphans(folder_ref: str, limit: int = 100) -> dict[str, Any]:
    started = time.time()
    inputs = {"folder_ref": folder_ref, "limit": limit}
    warnings: list[str] = []
    obs = _new_observability()

    try:
        limit = _validate_limit(limit, field="limit")
        snapshot = _bulk_get_child_graph_snapshot(folder_ref, limit=limit)
        items = snapshot.get("items") or []
        strong_orphans: list[dict[str, Any]] = []
        near_orphans: list[dict[str, Any]] = []
        title_to_uuid: dict[str, str] = {}
        for item in items:
            rec = item.get("record") or {}
            name = _coerce_text(rec.get("name"), obs).strip().lower()
            uuid = _coerce_text(rec.get("uuid"), obs).strip()
            if name and uuid and name not in title_to_uuid:
                title_to_uuid[name] = uuid

        for item in items:
            try:
                rec = item.get("record") or {}
                outgoing_records = item.get("outgoing_references") or []
                incoming_records = item.get("incoming_references") or []
                outgoing_wiki = [str(v).strip() for v in (item.get("outgoing_wiki_references") or []) if str(v).strip()]
                incoming_wiki = [str(v).strip() for v in (item.get("incoming_wiki_references") or []) if str(v).strip()]
                strong = len(outgoing_records) + len(incoming_records)
                weak_targets = set()
                for title in [*outgoing_wiki, *incoming_wiki]:
                    resolved = title_to_uuid.get(title.lower())
                    weak_targets.add(resolved or title)
                weak = len(weak_targets)
                row = {
                    "uuid": rec.get("uuid"),
                    "name": rec.get("name"),
                    "strong_edges": strong,
                    "weak_edges": weak,
                    "risk_flags": [],
                }
                if strong == 0 and weak == 0:
                    strong_orphans.append(row)
                elif strong == 0 and weak > 0:
                    near_orphans.append(row)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Orphan scan failed for {((item.get('record') or {}).get('uuid'))}: {exc}")

        data = {
            "folder": _get_record(folder_ref),
            "strong_orphans": strong_orphans,
            "near_orphans": near_orphans,
        }
        return _response(
            tool="devonthink-link-find-orphans",
            started_at=started,
            inputs=inputs,
            ok=True,
            data=data,
            warnings=warnings,
            stats=_merge_observability(
                {
                    "strong_orphan_count": len(strong_orphans),
                    "near_orphan_count": len(near_orphans),
                    "bulk_snapshot_count": len(items),
                },
                obs,
            ),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-find-orphans", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def devonthink_link_suggest_related(record_ref: str, limit: int = 15) -> dict[str, Any]:
    started = time.time()
    inputs = {"record_ref": record_ref, "limit": limit}
    warnings: list[str] = []
    obs = _new_observability()

    try:
        limit = _validate_limit(limit, field="limit", max_value=100)
        snapshot = _get_record_edge_snapshot(record_ref)
        rec = snapshot.get("record") or {}
        db_uuid = rec.get("database_uuid")

        candidates: dict[str, dict[str, Any]] = {}

        # Strong signal: authoritative incoming/outgoing links.
        authoritative_candidates = _edge_target_uuid_set(snapshot.get("incoming_references") or [], obs)
        authoritative_candidates.update(_edge_target_uuid_set(snapshot.get("outgoing_references") or [], obs))
        authoritative_candidates.discard(_coerce_text(rec.get("uuid"), obs))
        candidate_snapshots = _bulk_get_edge_snapshots(sorted(authoritative_candidates))
        for uuid, target_snapshot in candidate_snapshots.items():
            target = target_snapshot.get("record") or {}
            row = candidates.setdefault(
                uuid,
                {
                    "record": target,
                    "signals": [],
                    "score": 0,
                },
            )
            if uuid in _edge_target_uuid_set(snapshot.get("incoming_references") or [], obs):
                edge = _build_edge(uuid, "incoming_reference", "item_link", "strong", "authoritative incoming references property")
                row["signals"].append(edge)
                row["score"] += edge["weight"]
            if uuid in _edge_target_uuid_set(snapshot.get("outgoing_references") or [], obs):
                edge = _build_edge(uuid, "outgoing_reference", "item_link", "strong", "authoritative outgoing references property")
                row["signals"].append(edge)
                row["score"] += edge["weight"]

        # Medium/weak: shared tags via a single OR-style search query.
        try:
            own_uuid = rec.get("uuid")
            own_tags = {str(t).lower() for t in (rec.get("tags") or [])}
            for hit in _search_records_any_tags(list(own_tags), limit=40, database_uuid=db_uuid, obs=obs):
                uuid = hit.get("uuid")
                if not uuid or uuid == own_uuid:
                    continue
                overlap = own_tags.intersection({str(t).lower() for t in (hit.get("tags") or [])})
                if not overlap:
                    continue
                signal = _build_edge(
                    uuid,
                    "shared_tag",
                    "shared_tag",
                    "weak",
                    f"shared tags: {', '.join(sorted(overlap)[:5])}",
                )
                row = candidates.setdefault(uuid, {"record": hit, "signals": [], "score": 0})
                row["signals"].append(signal)
                row["score"] += signal["weight"]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Shared-tag signal unavailable: {exc}")

        min_authoritative_candidates = max(0, FUZZY_SKIP_THRESHOLD)
        title_search_skipped = len(candidates) >= min_authoritative_candidates
        if title_search_skipped:
            warnings.append("Title-context signal skipped due to sufficient authoritative candidates.")
        else:
            try:
                title_hits = _resolve_title_candidates(rec.get("name") or "", database_uuid=db_uuid, limit=50, obs=obs)
                for hit in title_hits:
                    uuid = hit.get("uuid")
                    if not uuid or uuid == rec.get("uuid"):
                        continue
                    ratio = SequenceMatcher(None, (rec.get("name") or "").lower(), (hit.get("name") or "").lower()).ratio()
                    if ratio < 0.35:
                        continue
                    signal = _build_edge(
                        uuid,
                        "title_fuzzy_match",
                        "alias_match",
                        "medium" if ratio > 0.65 else "weak",
                        f"title similarity ratio={ratio:.2f}",
                    )
                    row = candidates.setdefault(uuid, {"record": hit, "signals": [], "score": 0})
                    row["signals"].append(signal)
                    row["score"] += signal["weight"]
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Title-context signal unavailable: {exc}")

        ranked = sorted(candidates.values(), key=lambda v: v["score"], reverse=True)[:limit]
        suggestions = [
            {
                "record": row["record"],
                "score": row["score"],
                "signals": row["signals"],
            }
            for row in ranked
        ]

        return _response(
            tool="devonthink-link-suggest-related",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "source_record": rec,
                "suggestions": suggestions,
                "reason_codes": sorted(SIGNAL_WEIGHTS.keys()),
                "lowest_signal_tier": _lowest_signal_tier_for_edges(
                    [sig for row in suggestions for sig in (row.get("signals") or [])]
                ),
            },
            warnings=warnings,
            stats=_merge_observability(
                {
                    "candidate_count": len(candidates),
                    "suggestion_count": len(suggestions),
                    "title_search_skipped": int(title_search_skipped),
                },
                obs,
            ),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-suggest-related", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def devonthink_link_score(record_refs: list[str]) -> dict[str, Any]:
    started = time.time()
    inputs = {"record_refs": record_refs}
    warnings: list[str] = []
    obs = _new_observability()

    try:
        if not record_refs:
            raise ValueError("record_refs must contain at least one record reference.")

        snapshots = _bulk_get_edge_snapshots(record_refs)
        scored: list[dict[str, Any]] = []
        for ref in record_refs:
            try:
                snapshot = snapshots.get(_normalize_record_ref(ref))
                if snapshot is None:
                    continue
                rec = snapshot.get("record") or {}
                counts = {
                    "incoming": len(snapshot.get("incoming_references") or []),
                    "outgoing": len(snapshot.get("outgoing_references") or []),
                    "wikilinks": len(snapshot.get("incoming_wiki_references") or []) + len(snapshot.get("outgoing_wiki_references") or []),
                }
                raw = (
                    counts["incoming"] * SIGNAL_WEIGHTS["incoming_reference"]
                    + counts["outgoing"] * SIGNAL_WEIGHTS["outgoing_reference"]
                    + counts["wikilinks"] * SIGNAL_WEIGHTS["outgoing_wiki_reference"]
                    + len(rec.get("tags") or []) * SIGNAL_WEIGHTS["shared_tag"]
                    + len(rec.get("aliases") or []) * SIGNAL_WEIGHTS["alias_match"]
                )
                normalized = min(100, raw)
                scored.append(
                    {
                        "record": rec,
                        "raw_score": raw,
                        "score": normalized,
                        "components": {
                            "incoming": counts["incoming"],
                            "outgoing": counts["outgoing"],
                            "wikilinks": counts["wikilinks"],
                            "tags": len(rec.get("tags") or []),
                            "aliases": len(rec.get("aliases") or []),
                        },
                        "risk_flags": [],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Score failed for {ref}: {exc}")

        scored.sort(key=lambda x: x["score"], reverse=True)
        return _response(
            tool="devonthink-link-score",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={"scores": scored},
            warnings=warnings,
            stats=_merge_observability({"scored_count": len(scored)}, obs),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-score", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def _create_or_update_markdown_note(group_ref: str, note_name: str, markdown_body: str) -> dict[str, Any]:
    group_uuid = _normalize_record_ref(group_ref)
    script = _JSON_HELPERS + r'''
on run argv
    set groupRef to item 1 of argv
    set noteName to item 2 of argv
    set noteBody to item 3 of argv

    tell application "DEVONthink"
        set g to get record with uuid groupRef
        set existing to (children of g whose name is noteName and type is markdown)
        if (count of existing) > 0 then
            set target to item 1 of existing
            set plain text of target to noteBody
            return my record_json(target)
        else
            set created to create record with {name:noteName, type:markdown, plain text:noteBody} in g
            return my record_json(created)
        end if
    end tell
end run
'''
    data = _run_json(script, [group_uuid, note_name, markdown_body])
    if not isinstance(data, dict):
        raise AppleScriptExecutionError("Hub note create/update returned invalid output.")
    return data


def devonthink_link_build_hub(
    group_ref: str,
    seed_record_refs: list[str],
    hub_name: str = "Link Hub",
    mode: str = "overview",
) -> dict[str, Any]:
    started = time.time()
    inputs = {
        "group_ref": group_ref,
        "seed_record_refs": seed_record_refs,
        "hub_name": hub_name,
        "mode": mode,
    }
    warnings: list[str] = []

    try:
        if not seed_record_refs:
            raise ValueError("seed_record_refs must contain at least one record reference.")
        if mode not in {"overview", "index", "reading-list", "topic-map"}:
            raise ValueError("mode must be one of: overview, index, reading-list, topic-map.")
        group_record = _get_record(group_ref)
        _assert_record_writable(group_record, operation="build_hub")

        rows = []
        for ref in seed_record_refs:
            try:
                rec = _get_record(ref)
                text = _get_record_text(ref, max_chars=2000)
                rows.append(
                    {
                        "name": rec.get("name") or "Untitled",
                        "uuid": rec.get("uuid") or "",
                        "link": rec.get("reference_url") or f"x-devonthink-item://{rec.get('uuid')}",
                        "description": _brief_description(rec, text),
                        "tags": rec.get("tags") or [],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Hub seed skipped for {ref}: {exc}")

        heading = {
            "overview": "# Hub Overview",
            "index": "# Hub Index",
            "reading-list": "# Reading List",
            "topic-map": "# Topic Map",
        }[mode]

        lines: list[str] = [heading, "", f"Generated: {_iso_utc_now()}", ""]
        if mode == "overview":
            lines.extend(["| Name | Description |", "|---|---|"])
            for r in rows:
                cell_link = _md_link(r["name"], r["link"]).replace("|", r"\|")
                cell_desc = (r["description"] or "").replace("|", r"\|").replace("\n", " ")
                lines.append(f"| {cell_link} | {cell_desc} |")
        elif mode == "reading-list":
            for r in rows:
                lines.append(f"- [ ] {_md_link(r['name'], r['link'])} — {r['description']}")
        elif mode == "topic-map":
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in rows:
                key = (r.get("tags") or ["(untagged)"])[0]
                grouped[key].append(r)
            for tag in sorted(grouped):
                lines.append(f"## {tag}")
                for r in grouped[tag]:
                    lines.append(f"- {_md_link(r['name'], r['link'])} — {r['description']}")
                lines.append("")
        else:  # index
            for r in rows:
                lines.append(f"- {_md_link(r['name'], r['link'])} — {r['description']}")

        body = "\n".join(lines)
        hub_record = _create_or_update_markdown_note(group_ref, hub_name, body)

        return _response(
            tool="devonthink-link-build-hub",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "hub_record": hub_record,
                "entries": rows,
                "mode": mode,
            },
            warnings=warnings,
            stats={"entry_count": len(rows)},
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-build-hub", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def _derive_metadata_suggestions(record: dict[str, Any], text: str) -> dict[str, Any]:
    title_tokens = _tokenize(record.get("name") or "", min_len=4)
    text_tokens = _tokenize(text, min_len=5)
    top_text_tokens = [w for w, _ in Counter(text_tokens).most_common(8)]

    candidate_tags = []
    for token in [*title_tokens, *top_text_tokens]:
        if token not in candidate_tags:
            candidate_tags.append(token)
        if len(candidate_tags) >= 8:
            break

    desc = _brief_description(record, text)
    comment = desc if desc else f"{record.get('name') or 'Record'}"

    return {
        "suggested_tags": candidate_tags,
        "suggested_comment": comment,
    }


def devonthink_link_enrich_metadata(
    record_ref: str,
    mode: str = "suggest",
    custom_key: str | None = None,
) -> dict[str, Any]:
    started = time.time()
    inputs = {"record_ref": record_ref, "mode": mode, "custom_key": custom_key}

    try:
        if mode not in {"suggest", "apply"}:
            raise ValueError("mode must be 'suggest' or 'apply'.")

        rec = _get_record(record_ref)
        text = _get_record_text(record_ref, max_chars=3000)
        suggestions = _derive_metadata_suggestions(rec, text)

        applied = {"tags": False, "comment": False, "custom_metadata": False}
        if mode == "apply":
            _assert_record_writable(rec, operation="enrich_metadata")
            _set_comment_and_tags(record_ref, suggestions["suggested_comment"], suggestions["suggested_tags"])
            applied["tags"] = True
            applied["comment"] = True
            if custom_key:
                _set_custom_metadata(record_ref, custom_key, ", ".join(suggestions["suggested_tags"][:5]))
                applied["custom_metadata"] = True

        return _response(
            tool="devonthink-link-enrich-metadata",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "record": _get_record(record_ref),
                "suggestions": suggestions,
                "applied": applied,
            },
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-enrich-metadata", started_at=started, inputs=inputs, ok=False, error=str(exc))


def _set_plain_text(record_ref: str, text: str) -> None:
    rec = _get_record(record_ref)
    record_kind = _coerce_text(rec.get("type")).strip().lower()
    if record_kind in {"rtf", "rtfd"}:
        db_uuid = _coerce_text(rec.get("database_uuid")).strip() or "unknown"
        rec_uuid = _coerce_text(rec.get("uuid")).strip() or _normalize_record_ref(record_ref)
        raise AppleScriptExecutionError(
            f"rich_text_record_not_writable: Record {rec_uuid} (type {record_kind}) in database {db_uuid} stores "
            "URLs as rich-text attributes; setting plain text would strip embedded link targets. "
            "Use devonthink-update-rtf or a richtext-aware repair pathway instead."
        )

    normalized = _normalize_record_ref(record_ref)
    script = r'''
on run argv
    set recordRef to item 1 of argv
    set newText to item 2 of argv

    tell application "DEVONthink"
        set r to get record with uuid recordRef
        set plain text of r to newText
    end tell
end run
'''
    try:
        _run_osascript(script, [normalized, text])
    except AppleScriptExecutionError as exc:
        msg = str(exc).lower()
        if "locked" in msg:
            db_uuid = _coerce_text(rec.get("database_uuid")).strip() or "unknown"
            rec_uuid = _coerce_text(rec.get("uuid")).strip() or normalized
            raise AppleScriptExecutionError(
                f"record_locked: Record {rec_uuid} in database {db_uuid} is locked; cannot modify content."
            ) from exc
        if "can't be modified" in msg or "can’t be modified" in msg or "can't set" in msg or "can’t set" in msg:
            db_uuid = _coerce_text(rec.get("database_uuid")).strip() or "unknown"
            rec_uuid = _coerce_text(rec.get("uuid")).strip() or normalized
            raise AppleScriptExecutionError(
                f"record_content_immutable: Record {rec_uuid} in database {db_uuid} cannot be modified by script."
            ) from exc
        raise


def devonthink_link_repair_links(
    record_ref: str,
    mode: str = "report",
    remove_uuids: list[str] | None = None,
) -> dict[str, Any]:
    started = time.time()
    inputs = {"record_ref": record_ref, "mode": mode, "remove_uuids": remove_uuids or []}
    warnings: list[str] = []

    try:
        if mode not in {"report", "apply"}:
            raise ValueError("mode must be 'report' or 'apply'.")
        tombstone_uuids: list[str] = []
        for u in (remove_uuids or []):
            n = _normalize_record_ref(u)
            if UUID_RE.fullmatch(n) and n not in tombstone_uuids:
                tombstone_uuids.append(n)

        rec = _get_record(record_ref)
        text = _get_record_text(record_ref, max_chars=100000)

        item_links = _extract_item_links(text)
        wikilinks = _extract_wikilinks(text)
        raw_uuids = sorted(set(UUID_RE.findall(text)))

        unresolved_item_links = []
        for link in item_links:
            uuid = link.split("//", 1)[1]
            try:
                _get_record(uuid)
            except Exception:  # noqa: BLE001
                unresolved_item_links.append(link)

        # Batch-resolve every wiki link in a single OR-search per chunk instead
        # of one round trip per name; for a doc with N wikilinks this collapses
        # N osascript calls down to ceil(N / _TITLE_BATCH_CHUNK_SIZE).
        unresolved_wikilinks: list[str] = []
        if wikilinks:
            wiki_hits = _resolve_title_candidates_batch(
                wikilinks,
                database_uuid=rec.get("database_uuid"),
                limit_per_title=5,
            )
            for name in wikilinks:
                if not wiki_hits.get(name):
                    unresolved_wikilinks.append(name)

        replacements: list[dict[str, str]] = []
        new_text = text
        tombstone_links_found = 0

        if mode == "apply":
            _assert_content_writable(rec, operation="repair_links")
            # Canonicalize scheme casing and convert resolvable bare UUIDs to item links.
            for uuid in raw_uuids:
                try:
                    rec = _get_record(uuid)
                    canonical = rec.get("reference_url") or f"x-devonthink-item://{uuid}"
                    # Do not rewrite UUIDs already embedded in canonical item-link scheme.
                    pattern = re.compile(rf"(?<!x-devonthink-item://)\b{re.escape(uuid)}\b")
                    if pattern.search(new_text):
                        new_text = pattern.sub(canonical, new_text)
                        replacements.append({"from": uuid, "to": canonical})
                except Exception:  # noqa: BLE001
                    continue

            new_text = re.sub(r"X-DEVONTHINK-ITEM://", "x-devonthink-item://", new_text)

            for tombstone_uuid in tombstone_uuids:
                item_link = f"x-devonthink-item://{tombstone_uuid}"
                before = new_text
                # Replace markdown links targeting tombstoned UUIDs with plain label text.
                new_text = re.sub(
                    rf"\[([^\]]+)\]\(\s*{re.escape(item_link)}\s*\)",
                    r"\1",
                    new_text,
                )
                # Remove remaining raw item-link URLs for tombstoned UUIDs.
                new_text = re.sub(rf"\b{re.escape(item_link)}\b", "", new_text)
                if new_text != before:
                    replacements.append({"from": item_link, "to": "", "reason": "tombstone_cleanup"})

            if new_text != text:
                _set_plain_text(record_ref, new_text)
        else:
            for tombstone_uuid in tombstone_uuids:
                item_link = f"x-devonthink-item://{tombstone_uuid}"
                tombstone_links_found += text.count(item_link)

        return _response(
            tool="devonthink-link-repair-links",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "unresolved_item_links": unresolved_item_links,
                "unresolved_wikilinks": unresolved_wikilinks,
                "replacements": replacements,
                "changed": bool(replacements),
                "tombstone_uuids": tombstone_uuids,
                "tombstone_links_found": tombstone_links_found,
            },
            warnings=warnings,
            stats={"unresolved_count": len(unresolved_item_links) + len(unresolved_wikilinks)},
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-repair-links", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def devonthink_link_maintenance_pass(
    folder_ref: str,
    mode: str = "report",
    limit: int = 50,
    snapshot_dir: str = "snapshots",
) -> dict[str, Any]:
    started = time.time()
    inputs = {"folder_ref": folder_ref, "mode": mode, "limit": limit, "snapshot_dir": snapshot_dir}
    warnings: list[str] = []

    row_specs = {
        "new_isolated": ("⚠️ became isolated", "high", "consider linking or archiving"),
        "resolved_orphans": ("✅ orphan resolved", "info", "no action needed"),
        "new_sinks": ("⚠️ new sink node", "medium", "add outgoing links"),
        "lost_hubs": ("⚠️ hub degraded", "high", "check for removed links"),
        "nodes_removed": ("🗑 record tombstoned", "info", "check hub notes for dead links"),
        "shape_changes": ("→ shape changed", "info", "review if expected"),
        "edges_added": ("➕ edge added", "info", "no action needed"),
        "edges_removed": ("➖ edge removed", "medium", "review unlink intent"),
    }

    try:
        if mode not in {"report", "apply"}:
            raise ValueError("mode must be 'report' or 'apply'.")
        limit = _validate_limit(limit, field="limit", max_value=1000)

        folder = _get_record(folder_ref)
        folder_uuid = _normalize_record_ref(folder.get("uuid") or folder_ref)
        label = f"maintenance_{folder_uuid[:8]}"

        traversal = devonthink_link_traverse_folder(
            folder_ref=folder_uuid,
            limit=limit,
            mode="recursive",
            write_snapshot=True,
            snapshot_label=label,
            snapshot_dir=snapshot_dir,
        )
        if not traversal.get("ok"):
            raise ValueError(traversal.get("error") or "Traversal failed in maintenance pass.")
        warnings.extend((traversal.get("observability") or {}).get("warnings") or [])

        traversal_data = traversal.get("data") or {}
        current_snapshot_paths = traversal_data.get("snapshot_paths") or {}
        current_snapshot_path = current_snapshot_paths.get("adjacency_json")
        current_meta_path = current_snapshot_paths.get("meta_json")
        current_adj = traversal_data.get("adjacency_map") or {}
        current_shapes = traversal_data.get("shape_distribution") or {}
        current_coverage = _compute_coverage_pct(current_adj)
        traversal_meta = traversal_data.get("traversal_meta") or {}

        # First-run mode: only one snapshot exists for folder (the one we just wrote).
        try:
            baseline_path, latest_path, baseline_meta_path, latest_meta_path = _find_recent_snapshot_pair_for_folder(
                folder_uuid, snapshot_dir
            )
        except ValueError:
            return _response(
                tool="devonthink-link-maintenance-pass",
                started_at=started,
                inputs=inputs,
                ok=True,
                data={
                    "first_run": True,
                    "mode": mode,
                    "folder_uuid": folder_uuid,
                    "folder": folder,
                    "message": "No baseline found. Current state captured as baseline. Run again to see deltas.",
                    "shape_distribution": current_shapes,
                    "coverage_pct": current_coverage,
                    "snapshot_written": current_snapshot_path,
                    "snapshot_meta_written": current_meta_path,
                    "traversal_meta": traversal_meta,
                    "actionable_rows": [],
                    "summary": {
                        "rows_by_severity": {"high": 0, "medium": 0, "info": 0},
                        "tombstoned_count": 0,
                        "resolved_orphan_count": 0,
                        "hub_notes_repaired": 0,
                    },
                },
                warnings=warnings,
                stats={
                    "first_run": 1,
                    "row_count": 0,
                    "node_count": len(current_adj),
                },
            )

        # Compare current snapshot against previous baseline snapshot.
        compare_resp = devonthink_link_compare_snapshots(
            baseline_snapshot=str(baseline_path),
            current_snapshot=str(latest_path),
            baseline_meta=str(baseline_meta_path),
            current_meta=str(latest_meta_path),
            snapshot_dir=snapshot_dir,
        )
        if not compare_resp.get("ok"):
            raise ValueError(compare_resp.get("error") or "compare-snapshots failed in maintenance pass.")
        warnings.extend((compare_resp.get("observability") or {}).get("warnings") or [])
        diff = ((compare_resp.get("data") or {}).get("diff") or {})

        actionable_rows: list[dict[str, Any]] = []
        rows_by_severity = {"high": 0, "medium": 0, "info": 0}

        def _node_title(uuid: str, fallback: str = "Untitled") -> str:
            node = current_adj.get(uuid) or {}
            return _extract_node_title(node) if node else fallback

        for uuid in diff.get("new_isolated") or []:
            rt, sev, suggestion = row_specs["new_isolated"]
            row = {
                "row_type": rt,
                "severity": sev,
                "uuid": uuid,
                "title": _node_title(uuid),
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[sev] += 1

        for uuid in diff.get("resolved_orphans") or []:
            rt, sev, suggestion = row_specs["resolved_orphans"]
            row = {
                "row_type": rt,
                "severity": sev,
                "uuid": uuid,
                "title": _node_title(uuid),
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[sev] += 1

        for uuid in diff.get("new_sinks") or []:
            rt, sev, suggestion = row_specs["new_sinks"]
            row = {
                "row_type": rt,
                "severity": sev,
                "uuid": uuid,
                "title": _node_title(uuid),
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[sev] += 1

        for uuid in diff.get("lost_hubs") or []:
            rt, sev, suggestion = row_specs["lost_hubs"]
            row = {
                "row_type": rt,
                "severity": sev,
                "uuid": uuid,
                "title": _node_title(uuid),
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[sev] += 1

        for n in diff.get("nodes_removed") or []:
            rt, sev, suggestion = row_specs["nodes_removed"]
            row = {
                "row_type": rt,
                "severity": sev,
                "uuid": n.get("uuid"),
                "title": n.get("title") or "Untitled",
                "from_shape": n.get("shape"),
                "to_shape": None,
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[sev] += 1

        for c in diff.get("shape_changes") or []:
            rt, sev, suggestion = row_specs["shape_changes"]
            row = {
                "row_type": rt,
                "severity": sev,
                "uuid": c.get("uuid"),
                "title": c.get("title") or _node_title(c.get("uuid") or ""),
                "from_shape": c.get("from"),
                "to_shape": c.get("to"),
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[sev] += 1

        edge_added_seen: set[tuple[str, str, str]] = set()
        for e in diff.get("edges_added") or []:
            if _coerce_text(e.get("direction")).strip().lower() != "outgoing":
                continue
            key = (
                _coerce_text(e.get("source")).strip(),
                _coerce_text(e.get("target")).strip(),
                _coerce_text(e.get("edge_type")).strip(),
            )
            if not all(key) or key in edge_added_seen:
                continue
            edge_added_seen.add(key)
            rt, sev, suggestion = row_specs["edges_added"]
            row = {
                "row_type": rt,
                "severity": sev,
                "uuid": e.get("source"),
                "title": _node_title(_coerce_text(e.get("source"))),
                "source_uuid": e.get("source"),
                "target_uuid": e.get("target"),
                "edge_type": e.get("edge_type"),
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[sev] += 1

        edge_removed_seen: set[tuple[str, str, str]] = set()
        for e in diff.get("edges_removed") or []:
            if _coerce_text(e.get("direction")).strip().lower() != "outgoing":
                continue
            key = (
                _coerce_text(e.get("source")).strip(),
                _coerce_text(e.get("target")).strip(),
                _coerce_text(e.get("edge_type")).strip(),
            )
            if not all(key) or key in edge_removed_seen:
                continue
            edge_removed_seen.add(key)
            rt, sev, suggestion = row_specs["edges_removed"]
            severity = "info" if e.get("removal_reason") == "tombstoned" else sev
            row = {
                "row_type": rt,
                "severity": severity,
                "uuid": e.get("source"),
                "title": _node_title(_coerce_text(e.get("source"))),
                "source_uuid": e.get("source"),
                "target_uuid": e.get("target"),
                "edge_type": e.get("edge_type"),
                "removal_reason": e.get("removal_reason"),
                "suggestion": suggestion,
            }
            actionable_rows.append(row)
            rows_by_severity[severity] += 1

        hub_notes_repaired = 0
        hub_repairs: list[dict[str, Any]] = []
        if mode == "apply":
            tombstoned_uuids = [n.get("uuid") for n in (diff.get("nodes_removed") or []) if n.get("uuid")]
            if tombstoned_uuids:
                hub_uuids = [
                    uuid
                    for uuid, node in current_adj.items()
                    if _coerce_text(node.get("connectivity_shape")).strip().lower() == "hub"
                ]
                for hub_uuid in hub_uuids:
                    repair = devonthink_link_repair_links(
                        hub_uuid,
                        mode="apply",
                        remove_uuids=tombstoned_uuids,
                    )
                    changed = bool((repair.get("data") or {}).get("changed"))
                    if repair.get("ok") and changed:
                        hub_notes_repaired += 1
                    hub_repairs.append(
                        {
                            "hub_uuid": hub_uuid,
                            "ok": repair.get("ok"),
                            "changed": changed,
                            "error": repair.get("error"),
                        }
                    )

        prune_advisory = None
        if mode == "apply":
            prune_advisory = devonthink_link_prune_snapshots(snapshot_dir=snapshot_dir, mode="report")

        summary = {
            "rows_by_severity": rows_by_severity,
            "tombstoned_count": len(diff.get("nodes_removed") or []),
            "resolved_orphan_count": len(diff.get("resolved_orphans") or []),
            "hub_notes_repaired": hub_notes_repaired,
        }

        return _response(
            tool="devonthink-link-maintenance-pass",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "first_run": False,
                "mode": mode,
                "folder_uuid": folder_uuid,
                "folder": folder,
                "health_verdict": diff.get("health_verdict") or "stable",
                "coverage_delta_pct": ((diff.get("coverage_delta") or {}).get("delta_pct")),
                "actionable_rows": actionable_rows,
                "summary": summary,
                "snapshot_written": current_snapshot_path,
                "snapshot_meta_written": current_meta_path,
                "snapshot_prune_advisory": prune_advisory,
                "diff": diff,
                "hub_repairs": hub_repairs,
                "traversal_meta": traversal_meta,
            },
            warnings=warnings,
            stats=_merge_observability(
                {
                    "row_count": len(actionable_rows),
                    "hub_notes_repaired": hub_notes_repaired,
                    "tombstoned_count": len(diff.get("nodes_removed") or []),
                },
                (compare_resp.get("observability") or {}).get("stats") or {},
            ),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-maintenance-pass", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def devonthink_link_detect_bridges(folder_ref: str, limit: int = 80) -> dict[str, Any]:
    started = time.time()
    inputs = {"folder_ref": folder_ref, "limit": limit}
    warnings: list[str] = []

    try:
        limit = _validate_limit(limit, field="limit", max_value=250)
        snapshot = _bulk_get_child_graph_snapshot(folder_ref, limit=limit)
        items = snapshot.get("items") or []

        # Cluster proxy: first tag (if any), else type.
        cluster_of: dict[str, str] = {}
        record_by_uuid: dict[str, dict[str, Any]] = {}
        title_to_uuids: dict[str, set[str]] = defaultdict(set)
        candidate_items: list[dict[str, Any]] = []
        for item in items:
            rec = item.get("record") or {}
            uuid = rec.get("uuid")
            if not uuid:
                continue
            record_by_uuid[uuid] = rec
            tags = rec.get("tags") or []
            cluster_of[uuid] = (tags[0] if tags else rec.get("type") or "untyped")
            name = _coerce_text(rec.get("name")).strip().lower()
            if name:
                title_to_uuids[name].add(uuid)
            outgoing_count = len(item.get("outgoing_references") or [])
            if outgoing_count >= SHAPE_THRESHOLDS["bridge_outgoing"]:
                candidate_items.append(item)

        bridge_rows = []
        for item in candidate_items:
            rec = item.get("record") or {}
            uuid = rec.get("uuid")
            if not uuid:
                continue
            try:
                cluster_hits = set()
                for s in (item.get("outgoing_references") or []):
                    target = (s or {}).get("uuid")
                    if target and target in cluster_of:
                        cluster_hits.add(cluster_of[target])
                for title in (item.get("outgoing_wiki_references") or []):
                    for target_uuid in title_to_uuids.get(str(title).strip().lower(), set()):
                        if target_uuid != uuid and target_uuid in cluster_of:
                            cluster_hits.add(cluster_of[target_uuid])

                if len(cluster_hits) >= 2:
                    bridge_rows.append(
                        {
                            "record": rec,
                            "connected_clusters": sorted(cluster_hits),
                            "cluster_count": len(cluster_hits),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Bridge detection failed for {uuid}: {exc}")

        bridge_rows.sort(key=lambda x: x["cluster_count"], reverse=True)
        return _response(
            tool="devonthink-link-detect-bridges",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "folder": _get_record(folder_ref),
                "bridges": bridge_rows,
            },
            warnings=warnings,
            stats={
                "bridge_count": len(bridge_rows),
                "candidate_count": len(candidate_items),
                "bulk_snapshot_count": len(items),
            },
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-detect-bridges", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def _connectivity_shape(
    *,
    incoming: int,
    outgoing: int,
    neighbor_cluster_count: int,
    thresholds: dict[str, int] | None = None,
) -> str:
    t = {**SHAPE_THRESHOLDS, **(thresholds or {})}
    total = incoming + outgoing
    if incoming == 0 and outgoing == 0:
        return "isolated"
    if total <= t["near_orphan_total_max"]:
        return "near_orphan"
    if incoming >= t["sink_incoming"] and outgoing == 0:
        return "sink"
    if outgoing >= t["hub_outgoing"] and incoming >= t["hub_incoming"]:
        return "hub"
    if incoming >= t["spoke_incoming"] and outgoing <= t["spoke_outgoing_max"]:
        return "spoke"
    if outgoing >= t["bridge_outgoing"] and neighbor_cluster_count >= t["bridge_clusters"]:
        return "bridge"
    return "connected"


def devonthink_link_check_reciprocal(source_ref: str, target_ref: str) -> dict[str, Any]:
    """Check whether source has outgoing edge to target and target has incoming from source."""
    started = time.time()
    inputs = {"source_ref": source_ref, "target_ref": target_ref}
    warnings: list[str] = []
    obs = _new_observability()

    try:
        snapshots = _bulk_get_edge_snapshots([source_ref, target_ref])
        source = snapshots.get(_normalize_record_ref(source_ref), {}).get("record") or _get_record(source_ref)
        target = snapshots.get(_normalize_record_ref(target_ref), {}).get("record") or _get_record(target_ref)
        source_uuid = source.get("uuid") or ""
        target_uuid = target.get("uuid") or ""
        source_snapshot = snapshots.get(source_uuid) or {}
        target_snapshot = snapshots.get(target_uuid) or {}

        source_out_targets = _edge_target_uuid_set(source_snapshot.get("outgoing_references") or [], obs)
        target_in_sources = _edge_target_uuid_set(target_snapshot.get("incoming_references") or [], obs)
        source_out_wiki = _edge_title_set(source_snapshot.get("outgoing_wiki_references") or [], obs=obs)
        target_in_wiki = _edge_title_set(target_snapshot.get("incoming_wiki_references") or [], obs=obs)

        source_points_to_target = target_uuid in source_out_targets or (_coerce_text(target.get("name"), obs).strip().lower() in source_out_wiki)
        target_reports_source_incoming = source_uuid in target_in_sources or (_coerce_text(source.get("name"), obs).strip().lower() in target_in_wiki)

        return _response(
            tool="devonthink-link-check-reciprocal",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "source": source,
                "target": target,
                "source_points_to_target": source_points_to_target,
                "target_reports_source_incoming": target_reports_source_incoming,
                "consistent": source_points_to_target and target_reports_source_incoming,
            },
            warnings=warnings,
            stats=_merge_observability({}, obs),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-check-reciprocal", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def devonthink_link_traverse_folder(
    folder_ref: str,
    limit: int = 200,
    cursor: str | None = None,
    mode: str = "shallow",
    max_depth: int | None = None,
    include_smart_groups: bool = False,
    include_replicants: bool = False,
    group_path_tracking: bool = True,
    shape_thresholds: dict[str, int] | None = None,
    write_snapshot: bool = False,
    snapshot_label: str = "inbox_adjacency_baseline",
    snapshot_dir: str = "snapshots",
) -> dict[str, Any]:
    """Traverse folder records and build adjacency map + edge list with pagination cursor support."""
    started = time.time()
    inputs = {
        "folder_ref": folder_ref,
        "limit": limit,
        "cursor": cursor,
        "mode": mode,
        "max_depth": max_depth,
        "include_smart_groups": include_smart_groups,
        "include_replicants": include_replicants,
        "group_path_tracking": group_path_tracking,
        "shape_thresholds": shape_thresholds or {},
        "write_snapshot": write_snapshot,
        "snapshot_label": snapshot_label,
        "snapshot_dir": snapshot_dir,
    }
    warnings: list[str] = []
    obs = _new_observability()

    try:
        limit = _validate_limit(limit, field="limit", max_value=1000)
        if mode not in {"shallow", "recursive"}:
            raise ValueError("mode must be 'shallow' or 'recursive'.")
        max_depth = MAX_TRAVERSAL_DEPTH_DEFAULT if max_depth is None else int(max_depth)
        if max_depth < 1:
            raise ValueError("max_depth must be >= 1 when using recursive traversal.")
        if max_depth > 5:
            warnings.append("max_depth > 5 can be expensive on large databases.")
        cursor_uuid = _normalize_record_ref(cursor) if cursor else None

        try:
            folder = _get_record(folder_ref)
        except AppleScriptExecutionError as exc:
            if "database_unavailable" in str(exc):
                traversal_meta = {
                    "started_at": _iso_utc_now(),
                    "cursor": cursor_uuid,
                    "completed": True,
                    "records_processed": 0,
                    "records_skipped": 1,
                    "total_records": 1,
                    "skipped_entries": [{"uuid": _normalize_record_ref(folder_ref), "reason": str(exc)}],
                }
                return _response(
                    tool="devonthink-link-traverse-folder",
                    started_at=started,
                    inputs=inputs,
                    ok=True,
                    data={
                        "folder": None,
                        "traversal_meta": traversal_meta,
                        "adjacency_map": {},
                        "edge_list": [],
                        "shape_distribution": {},
                        "snapshot_paths": {},
                    },
                    warnings=[str(exc)],
                    stats=_merge_observability({"node_count": 0, "edge_count": 0, "shape_count": 0}, obs),
                )
            raise
        records: list[dict[str, Any]] = []
        discovery_skipped_entries: list[dict[str, Any]] = []
        smart_group_query_count = 0
        smart_group_skipped_count = 0
        folder_uuid = _normalize_record_ref(folder.get("uuid") or folder_ref)

        if mode == "shallow":
            folder_type = _coerce_text(folder.get("type"), obs).lower()
            if _is_smart_group_type(folder_type):
                if not include_smart_groups:
                    smart_group_skipped_count += 1
                    discovery_skipped_entries.append({"uuid": folder_uuid, "reason": "smart_group_excluded"})
                else:
                    smart_group_query_count += 1
                    smart_data = _get_smart_group_virtual_children(folder_uuid, limit=limit)
                    for rec in smart_data.get("children") or []:
                        rec["_group_path"] = [folder.get("name") or "root"] if group_path_tracking else []
                        rec["_depth"] = 1
                        rec["_membership_type"] = "virtual"
                        rec["_children_source"] = "smart_group_query"
                        rec["_virtual_parent_uuid"] = folder_uuid
                        rec["_smart_group_context"] = {
                            "group_uuid": folder_uuid,
                            "search_predicates": smart_data.get("search_predicates") or "",
                            "search_group_uuid": smart_data.get("search_group_uuid") or "",
                            "search_group_name": smart_data.get("search_group_name") or "",
                        }
                        records.append(rec)
            else:
                for rec in _get_children(folder_ref, limit=limit):
                    rec["_group_path"] = [folder.get("name") or "root"] if group_path_tracking else []
                    rec["_depth"] = 1
                    rec["_membership_type"] = "physical"
                    rec["_children_source"] = "group_children"
                    records.append(rec)
        else:
            seen_groups: set[str] = set()
            stack: list[tuple[str, int, list[str]]] = [(folder_uuid, 1, [folder.get("name") or "root"])]
            while stack and len(records) < limit:
                group_uuid, depth, path = stack.pop()
                if group_uuid in seen_groups:
                    continue
                seen_groups.add(group_uuid)

                try:
                    group_rec = folder if group_uuid == folder_uuid else _get_record(group_uuid)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"Could not resolve group {group_uuid}: {exc}")
                    discovery_skipped_entries.append({"uuid": group_uuid, "reason": f"group_resolve_failed: {exc}"})
                    continue

                group_type = _coerce_text(group_rec.get("type"), obs).lower()
                is_smart_group = _is_smart_group_type(group_type)
                children_source = "group_children"
                membership_type = "physical"
                smart_context: dict[str, Any] | None = None
                try:
                    if is_smart_group:
                        if not include_smart_groups:
                            smart_group_skipped_count += 1
                            discovery_skipped_entries.append({"uuid": group_uuid, "reason": "smart_group_excluded"})
                            continue
                        smart_group_query_count += 1
                        smart_data = _get_smart_group_virtual_children(group_uuid, limit=limit)
                        children = smart_data.get("children") or []
                        children_source = "smart_group_query"
                        membership_type = "virtual"
                        smart_context = {
                            "group_uuid": group_uuid,
                            "search_predicates": smart_data.get("search_predicates") or "",
                            "search_group_uuid": smart_data.get("search_group_uuid") or "",
                            "search_group_name": smart_data.get("search_group_name") or "",
                        }
                    else:
                        children = _get_children(group_uuid, limit=limit)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"Could not read children for group {group_uuid}: {exc}")
                    discovery_skipped_entries.append({"uuid": group_uuid, "reason": f"group_children_failed: {exc}"})
                    continue

                for child in children:
                    if len(records) >= limit:
                        break
                    child_uuid = child.get("uuid") or ""
                    child_name = child.get("name") or ""
                    child_type = _coerce_text(child.get("type"), obs).lower()
                    child["_group_path"] = [*path, child_name] if group_path_tracking else []
                    child["_depth"] = depth
                    child["_membership_type"] = membership_type
                    child["_children_source"] = children_source
                    if membership_type == "virtual":
                        child["_virtual_parent_uuid"] = group_uuid
                        child["_smart_group_context"] = smart_context or {}
                    records.append(child)
                    if (
                        mode == "recursive"
                        and depth < max_depth
                        and "group" in child_type
                        and child_uuid
                        and children_source == "group_children"
                    ):
                        stack.append((child_uuid, depth + 1, [*path, child_name]))

        total_records = len(records) + len(discovery_skipped_entries)

        traversal_meta = {
            "started_at": _iso_utc_now(),
            "cursor": cursor_uuid,
            "completed": True,
            "records_processed": 0,
            "records_skipped": len(discovery_skipped_entries),
            "total_records": total_records,
            "skipped_entries": discovery_skipped_entries,
            "folder_uuid": folder.get("uuid"),
            "folder_name": folder.get("name"),
            "mode": mode,
            "snapshot_generated_by": "devonthink-link-traverse-folder",
            "meta_schema_version": SNAPSHOT_META_SCHEMA_VERSION,
        }

        seen_uuids: set[str] = set()
        adjacency_map: dict[str, dict[str, Any]] = {}
        edge_list: list[dict[str, Any]] = []

        process_started = cursor_uuid is None
        if cursor_uuid and all((rec.get("uuid") or "") != cursor_uuid for rec in records):
            warnings.append(f"Cursor UUID {cursor_uuid} not found in selected traversal scope; processing from start.")
            process_started = True

        for rec in records:
            uuid = rec.get("uuid") or ""
            if not uuid:
                traversal_meta["records_skipped"] += 1
                traversal_meta["skipped_entries"].append({"uuid": None, "reason": "missing_uuid"})
                continue
            if not process_started:
                if uuid == cursor_uuid:
                    process_started = True
                continue
            if not include_replicants and uuid in seen_uuids:
                traversal_meta["records_skipped"] += 1
                traversal_meta["skipped_entries"].append({"uuid": uuid, "reason": "duplicate_uuid"})
                continue
            seen_uuids.add(uuid)

            try:
                audit, local_warnings, obs = _audit_record_impl(uuid, include_text_scan=False, obs=obs)
                warnings.extend(local_warnings)

                incoming_edges = audit.get("edges", {}).get("incoming") or []
                outgoing_edges = audit.get("edges", {}).get("outgoing") or []
                wiki_edges = audit.get("edges", {}).get("wikilinks") or []

                adjacency_map[uuid] = {
                    "meta": audit.get("record"),
                    "signal_source": audit.get("signal_source"),
                    "lowest_signal_tier": audit.get("lowest_signal_tier"),
                    "unresolved_references": audit.get("unresolved_references") or [],
                    "group_path": rec.get("_group_path") or [],
                    "depth": rec.get("_depth"),
                    "membership_type": rec.get("_membership_type") or "physical",
                    "children_source": rec.get("_children_source") or "group_children",
                    "virtual_parent_uuid": rec.get("_virtual_parent_uuid"),
                    "smart_group_context": rec.get("_smart_group_context") or None,
                    "incoming": incoming_edges,
                    "outgoing": outgoing_edges,
                    "wikilinks": wiki_edges,
                    "counts": audit.get("edges", {}).get("counts") or {},
                }

                for e in outgoing_edges:
                    edge_list.append(
                        {
                            "source_uuid": uuid,
                            "target": e.get("to"),
                            "edge_type": e.get("edge_type"),
                            "direction": "outgoing",
                            "reason_code": e.get("reason_code"),
                        }
                    )
                for e in incoming_edges:
                    edge_list.append(
                        {
                            "source_uuid": uuid,
                            "target": e.get("to"),
                            "edge_type": e.get("edge_type"),
                            "direction": "incoming",
                            "reason_code": e.get("reason_code"),
                        }
                    )
                for e in wiki_edges:
                    edge_list.append(
                        {
                            "source_uuid": uuid,
                            "target": e.get("to"),
                            "edge_type": e.get("edge_type"),
                            "direction": "outgoing",
                            "reason_code": e.get("reason_code"),
                        }
                    )

                traversal_meta["records_processed"] += 1
                traversal_meta["cursor"] = uuid
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Traversal failed for {uuid}: {exc}")
                traversal_meta["records_skipped"] += 1
                traversal_meta["cursor"] = uuid
                traversal_meta["skipped_entries"].append({"uuid": uuid, "reason": str(exc)})
                continue

        # Derive connectivity shapes.
        for node_uuid, node in adjacency_map.items():
            incoming_count = len(node.get("incoming") or [])
            outgoing_count = len(node.get("outgoing") or [])
            neighbor_clusters: set[str] = set()
            for e in [*(node.get("incoming") or []), *(node.get("outgoing") or [])]:
                match = UUID_RE.search(_coerce_text(e.get("to"), obs))
                if not match:
                    continue
                neighbor_uuid = match.group(0)
                neighbor = adjacency_map.get(neighbor_uuid)
                if not neighbor:
                    continue
                neighbor_tags = ((neighbor.get("meta") or {}).get("tags") or [])
                cluster = (neighbor_tags[0] if neighbor_tags else (neighbor.get("meta") or {}).get("type") or "untyped")
                neighbor_clusters.add(str(cluster))
            node["connectivity_shape"] = _connectivity_shape(
                incoming=incoming_count,
                outgoing=outgoing_count,
                neighbor_cluster_count=len(neighbor_clusters),
                thresholds=shape_thresholds,
            )

        shape_distribution = dict(Counter(node.get("connectivity_shape", "connected") for node in adjacency_map.values()))

        snapshot_paths: dict[str, str] = {}
        if write_snapshot:
            ts = datetime.now().strftime("%Y%m%dT%H%M%S")
            safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", snapshot_label).strip("_") or "adjacency_baseline"
            out_dir = Path(snapshot_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            data_path = out_dir / f"{safe_label}_{ts}.json"
            meta_path = out_dir / f"{safe_label}_{ts}.meta.json"
            data_path.write_text(json.dumps(adjacency_map, indent=2) + "\n")
            meta_path.write_text(json.dumps(traversal_meta, indent=2) + "\n")
            snapshot_paths = {
                "adjacency_json": str(data_path.resolve()),
                "meta_json": str(meta_path.resolve()),
            }

        return _response(
            tool="devonthink-link-traverse-folder",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "folder": folder,
                "traversal_meta": traversal_meta,
                "adjacency_map": adjacency_map,
                "edge_list": edge_list,
                "shape_distribution": shape_distribution,
                "snapshot_paths": snapshot_paths,
            },
            warnings=warnings,
            stats=_merge_observability(
                {
                    "node_count": len(adjacency_map),
                    "edge_count": len(edge_list),
                    "shape_count": len(shape_distribution),
                    "smart_group_query_count": smart_group_query_count,
                    "smart_group_skipped_count": smart_group_skipped_count,
                },
                obs,
            ),
        )
    except (AppleScriptExecutionError, ValueError) as exc:
        return _response(tool="devonthink-link-traverse-folder", started_at=started, inputs=inputs, ok=False, error=str(exc), warnings=warnings)


def _snapshot_base_and_meta_paths(snapshot_path: str, meta_path: str | None = None) -> tuple[Path, Path]:
    base = Path(snapshot_path).expanduser().resolve()
    if not base.exists() or not base.is_file():
        raise ValueError(f"Snapshot file not found: {base}")
    if base.name.endswith(".meta.json"):
        guessed_base = base.with_name(base.name[: -len(".meta.json")] + ".json")
        if not guessed_base.exists():
            raise ValueError(f"Snapshot JSON counterpart not found for meta file: {base}")
        base = guessed_base
    if meta_path:
        meta = Path(meta_path).expanduser().resolve()
    else:
        if base.name.endswith(".json"):
            meta = base.with_name(base.name[: -len(".json")] + ".meta.json")
        else:
            meta = base.with_suffix(base.suffix + ".meta.json")
    if not meta.exists() or not meta.is_file():
        raise ValueError(f"Snapshot meta file not found: {meta}")
    return base, meta


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def _extract_folder_uuid_from_meta(meta: dict[str, Any]) -> str:
    candidates = [
        meta.get("folder_uuid"),
        (meta.get("folder") or {}).get("uuid"),
        (meta.get("inputs") or {}).get("folder_ref"),
        (meta.get("traversal_meta") or {}).get("folder_uuid"),
    ]
    for value in candidates:
        s = _coerce_text(value).strip()
        if UUID_RE.fullmatch(s):
            return s
    return ""


def _infer_folder_uuid_from_adjacency(adjacency: dict[str, Any]) -> str:
    """Best-effort fallback for older snapshot metas that lack folder UUID.

    Heuristic:
    - Prefer UUID-looking values at the root of `group_path`.
    - Fall back to UUID-looking values in location roots when present.
    """
    counts: Counter[str] = Counter()
    for node in adjacency.values():
        if not isinstance(node, dict):
            continue
        group_path = node.get("group_path") or []
        if isinstance(group_path, list) and group_path:
            root = _coerce_text(group_path[0]).strip()
            if UUID_RE.fullmatch(root):
                counts[root] += 1

        meta = node.get("meta") or {}
        if isinstance(meta, dict):
            loc = _coerce_text(meta.get("location")).strip()
            if loc:
                parts = [p for p in loc.split("/") if p]
                if parts:
                    maybe = parts[0].strip()
                    if UUID_RE.fullmatch(maybe):
                        counts[maybe] += 1

    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _find_recent_snapshot_pair_for_folder(folder_ref: str, snapshot_dir: str) -> tuple[Path, Path, Path, Path]:
    folder_uuid = _normalize_record_ref(folder_ref)
    root = Path(snapshot_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Snapshot directory not found: {root}")

    meta_candidates = sorted(root.glob("*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    matches: list[tuple[Path, Path]] = []
    for meta_path in meta_candidates:
        meta = _load_json_file(meta_path)
        if not isinstance(meta, dict):
            continue
        extracted_folder_uuid = _extract_folder_uuid_from_meta(meta)
        if not extracted_folder_uuid:
            base_name = meta_path.name[: -len(".meta.json")] + ".json"
            base_path = meta_path.with_name(base_name)
            if not base_path.exists():
                continue
            adjacency = _load_json_file(base_path)
            if isinstance(adjacency, dict):
                extracted_folder_uuid = _infer_folder_uuid_from_adjacency(adjacency)
        if extracted_folder_uuid != folder_uuid:
            continue
        base_name = meta_path.name[: -len(".meta.json")] + ".json"
        base_path = meta_path.with_name(base_name)
        if base_path.exists():
            matches.append((base_path, meta_path))
        if len(matches) >= 2:
            break

    if len(matches) < 2:
        raise ValueError(
            "Could not find two snapshots for this folder UUID in snapshot_dir. "
            "Pass baseline/current snapshot paths explicitly, or write new snapshots with folder metadata."
        )

    current_base, current_meta = matches[0]
    baseline_base, baseline_meta = matches[1]
    return baseline_base, current_base, baseline_meta, current_meta


def _extract_node_shape(node: dict[str, Any]) -> str:
    return _coerce_text(node.get("connectivity_shape")).strip() or "connected"


def _extract_node_title(node: dict[str, Any]) -> str:
    meta = node.get("meta") or {}
    if not isinstance(meta, dict):
        return "Untitled"
    return _coerce_text(meta.get("name")).strip() or "Untitled"


def _edge_target_uuid(target: Any) -> str:
    target_text = _coerce_text(target).strip()
    match = UUID_RE.search(target_text)
    return match.group(0) if match else ""


def _snapshot_edge_set(adjacency: dict[str, Any]) -> set[tuple[str, str, str, str, str]]:
    edges: set[tuple[str, str, str, str, str]] = set()
    for source_uuid, node in adjacency.items():
        if not isinstance(node, dict):
            continue
        for bucket, direction in (("outgoing", "outgoing"), ("incoming", "incoming"), ("wikilinks", "outgoing")):
            for edge in node.get(bucket) or []:
                if not isinstance(edge, dict):
                    continue
                target_raw = _coerce_text(edge.get("to")).strip()
                target = _edge_target_uuid(target_raw) or target_raw
                edge_type = _coerce_text(edge.get("edge_type")).strip() or "unknown"
                reason_code = _coerce_text(edge.get("reason_code")).strip() or "unknown"
                if not source_uuid or not target:
                    continue
                edges.add((source_uuid, target, edge_type, direction, reason_code))
    return edges


def _compute_coverage_pct(adjacency: dict[str, Any]) -> float:
    total = len(adjacency)
    if total == 0:
        return 0.0
    with_both = 0
    for node in adjacency.values():
        if not isinstance(node, dict):
            continue
        incoming = len(node.get("incoming") or [])
        outgoing = len(node.get("outgoing") or [])
        if incoming > 0 and outgoing > 0:
            with_both += 1
    return round((with_both / total) * 100, 2)


def _parse_meta_started_at(meta: dict[str, Any], fallback_path: Path) -> tuple[str, datetime]:
    ts = (
        _coerce_text((meta.get("traversal_meta") or {}).get("started_at")).strip()
        or _coerce_text(meta.get("started_at")).strip()
    )
    if ts:
        normalized = ts.replace("Z", "+00:00")
        try:
            return ts, datetime.fromisoformat(normalized)
        except ValueError:
            pass
    dt = datetime.fromtimestamp(fallback_path.stat().st_mtime, tz=timezone.utc)
    return dt.isoformat(), dt


def devonthink_link_compare_snapshots(
    baseline_snapshot: str | None = None,
    current_snapshot: str | None = None,
    baseline_meta: str | None = None,
    current_meta: str | None = None,
    folder_ref: str | None = None,
    snapshot_dir: str = "snapshots",
) -> dict[str, Any]:
    started = time.time()
    inputs = {
        "baseline_snapshot": baseline_snapshot,
        "current_snapshot": current_snapshot,
        "baseline_meta": baseline_meta,
        "current_meta": current_meta,
        "folder_ref": folder_ref,
        "snapshot_dir": snapshot_dir,
    }

    try:
        if baseline_snapshot and current_snapshot:
            baseline_base_path, baseline_meta_path = _snapshot_base_and_meta_paths(baseline_snapshot, baseline_meta)
            current_base_path, current_meta_path = _snapshot_base_and_meta_paths(current_snapshot, current_meta)
        elif folder_ref:
            baseline_base_path, current_base_path, baseline_meta_path, current_meta_path = _find_recent_snapshot_pair_for_folder(
                folder_ref, snapshot_dir
            )
        else:
            raise ValueError(
                "Provide baseline_snapshot/current_snapshot, or provide folder_ref to auto-locate the two most recent snapshots."
            )

        baseline_adj = _load_json_file(baseline_base_path)
        current_adj = _load_json_file(current_base_path)
        baseline_meta_obj = _load_json_file(baseline_meta_path)
        current_meta_obj = _load_json_file(current_meta_path)
        if not isinstance(baseline_adj, dict) or not isinstance(current_adj, dict):
            raise ValueError("Snapshot JSON files must be adjacency maps (object keyed by UUID).")
        if not isinstance(baseline_meta_obj, dict) or not isinstance(current_meta_obj, dict):
            raise ValueError("Snapshot meta files must be JSON objects.")

        baseline_ts_text, baseline_ts = _parse_meta_started_at(baseline_meta_obj, baseline_meta_path)
        current_ts_text, current_ts = _parse_meta_started_at(current_meta_obj, current_meta_path)
        elapsed_seconds = round((current_ts - baseline_ts).total_seconds(), 3)

        baseline_nodes = set(baseline_adj.keys())
        current_nodes = set(current_adj.keys())
        nodes_added_uuids = sorted(current_nodes - baseline_nodes)
        nodes_removed_uuids = sorted(baseline_nodes - current_nodes)
        nodes_shared_uuids = sorted(baseline_nodes & current_nodes)
        tombstoned = set(nodes_removed_uuids)

        nodes_added = [
            {"uuid": u, "shape": _extract_node_shape(current_adj.get(u, {})), "title": _extract_node_title(current_adj.get(u, {}))}
            for u in nodes_added_uuids
        ]
        nodes_removed = [
            {"uuid": u, "shape": _extract_node_shape(baseline_adj.get(u, {})), "title": _extract_node_title(baseline_adj.get(u, {}))}
            for u in nodes_removed_uuids
        ]

        baseline_edges = _snapshot_edge_set(baseline_adj)
        current_edges = _snapshot_edge_set(current_adj)
        edges_added_raw = sorted(current_edges - baseline_edges)
        edges_removed_raw = sorted(baseline_edges - current_edges)

        def _edge_to_obj(edge: tuple[str, str, str, str, str]) -> dict[str, Any]:
            source, target, edge_type, direction, reason_code = edge
            target_uuid = _edge_target_uuid(target)
            return {
                "source": source,
                "target": target_uuid or target,
                "edge_type": edge_type,
                "direction": direction,
                "reason_code": reason_code,
            }

        edges_added = [_edge_to_obj(e) for e in edges_added_raw]
        edges_removed: list[dict[str, Any]] = []
        for edge in edges_removed_raw:
            item = _edge_to_obj(edge)
            target_uuid = _edge_target_uuid(item["target"])
            source_tombstoned = item["source"] in tombstoned
            target_tombstoned = bool(target_uuid and target_uuid in tombstoned)
            item["source_tombstoned"] = source_tombstoned
            item["target_tombstoned"] = target_tombstoned
            item["removal_reason"] = "tombstoned" if (source_tombstoned or target_tombstoned) else "unlinked"
            edges_removed.append(item)

        shape_changes: list[dict[str, Any]] = []
        new_isolated: list[str] = []
        resolved_orphans: list[str] = []
        new_sinks: list[str] = []
        lost_hubs: list[str] = []
        for u in nodes_shared_uuids:
            old_shape = _extract_node_shape(baseline_adj.get(u, {}))
            new_shape = _extract_node_shape(current_adj.get(u, {}))
            if old_shape != new_shape:
                shape_changes.append(
                    {
                        "uuid": u,
                        "title": _extract_node_title(current_adj.get(u, {})),
                        "from": old_shape,
                        "to": new_shape,
                    }
                )
            if new_shape == "isolated" and old_shape != "isolated":
                new_isolated.append(u)
            if old_shape in {"near_orphan", "isolated"} and new_shape not in {"near_orphan", "isolated"}:
                resolved_orphans.append(u)
            if new_shape == "sink" and old_shape != "sink":
                new_sinks.append(u)
            if old_shape == "hub" and new_shape != "hub":
                lost_hubs.append(u)

        baseline_coverage = _compute_coverage_pct(baseline_adj)
        current_coverage = _compute_coverage_pct(current_adj)
        coverage_delta = round(current_coverage - baseline_coverage, 2)
        recommended_interval_seconds = 30
        diff_confidence = {
            "elapsed_seconds": elapsed_seconds,
            "recommended_min_interval_seconds": recommended_interval_seconds,
        }
        if elapsed_seconds < recommended_interval_seconds:
            diff_confidence["warning"] = (
                "snapshots taken < 30s apart — changes may reflect indexing lag, not real edits"
            )

        churn_ratio = 0.0
        baseline_count = max(1, len(baseline_nodes))
        churn_ratio = round(((len(nodes_added) + len(nodes_removed)) / baseline_count) * 100, 2)
        if coverage_delta > 0 and not new_isolated:
            health_verdict = "improved"
        elif new_isolated or lost_hubs or coverage_delta < -0.05:
            health_verdict = "degraded"
        elif churn_ratio > 10 and abs(coverage_delta) <= 0.05:
            health_verdict = "restructured"
        else:
            health_verdict = "stable"

        diff = {
            "baseline_snapshot": str(baseline_base_path),
            "current_snapshot": str(current_base_path),
            "baseline_meta": str(baseline_meta_path),
            "current_meta": str(current_meta_path),
            "baseline_timestamp": baseline_ts_text,
            "current_timestamp": current_ts_text,
            "elapsed_seconds": elapsed_seconds,
            "nodes_added": nodes_added,
            "nodes_removed": nodes_removed,
            "nodes_unchanged": len(nodes_shared_uuids),
            "edges_added": edges_added,
            "edges_removed": edges_removed,
            "shape_changes": shape_changes,
            "new_isolated": sorted(new_isolated),
            "resolved_orphans": sorted(resolved_orphans),
            "new_sinks": sorted(new_sinks),
            "lost_hubs": sorted(lost_hubs),
            "health_verdict": health_verdict,
            "diff_confidence": diff_confidence,
            "coverage_delta": {
                "baseline_pct": baseline_coverage,
                "current_pct": current_coverage,
                "delta_pct": coverage_delta,
            },
        }

        return _response(
            tool="devonthink-link-compare-snapshots",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={"diff": diff},
            stats={
                "nodes_added_count": len(nodes_added),
                "nodes_removed_count": len(nodes_removed),
                "edges_added_count": len(edges_added),
                "edges_removed_count": len(edges_removed),
                "shape_changes_count": len(shape_changes),
                "tombstoned_edges_removed_count": sum(1 for e in edges_removed if e["removal_reason"] == "tombstoned"),
            },
        )
    except ValueError as exc:
        return _response(
            tool="devonthink-link-compare-snapshots",
            started_at=started,
            inputs=inputs,
            ok=False,
            error=str(exc),
        )


DEFAULT_SNAPSHOT_RETENTION = {
    "keep_last_n": 5,
    "keep_daily_for": 30,
    "keep_weekly_for": 180,
    "keep_monthly_for": 730,
    "hard_delete_after": None,
}


def _coerce_retention(value: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_SNAPSHOT_RETENTION)
    if isinstance(value, dict):
        merged.update(value)
    try:
        merged["keep_last_n"] = max(1, int(merged["keep_last_n"]))
        merged["keep_daily_for"] = max(0, int(merged["keep_daily_for"]))
        merged["keep_weekly_for"] = max(0, int(merged["keep_weekly_for"]))
        merged["keep_monthly_for"] = max(0, int(merged["keep_monthly_for"]))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid retention values: {exc}") from exc
    hard_delete_after = merged.get("hard_delete_after")
    if hard_delete_after is None:
        merged["hard_delete_after"] = None
    else:
        merged["hard_delete_after"] = max(0, int(hard_delete_after))
    return merged


def _parse_snapshot_label_and_timestamp(base_path: Path) -> tuple[str, datetime]:
    stem = base_path.stem
    match = re.search(r"_(\d{8}T\d{6})$", stem)
    if match:
        ts_raw = match.group(1)
        label = stem[: -len(match.group(0))] or "snapshot"
        try:
            dt = datetime.strptime(ts_raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            return label, dt
        except ValueError:
            pass
    return stem, datetime.fromtimestamp(base_path.stat().st_mtime, tz=timezone.utc)


def devonthink_link_prune_snapshots(
    snapshot_dir: str = "snapshots",
    retention: dict[str, Any] | None = None,
    mode: str = "report",
    archive_dir: str | None = None,
) -> dict[str, Any]:
    started = time.time()
    inputs = {
        "snapshot_dir": snapshot_dir,
        "retention": retention or {},
        "mode": mode,
        "archive_dir": archive_dir,
    }

    try:
        if mode not in {"report", "apply"}:
            raise ValueError("mode must be 'report' or 'apply'.")
        policy = _coerce_retention(retention)
        root = Path(snapshot_dir).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Snapshot directory not found: {root}")

        now = datetime.now(timezone.utc)
        valid_pairs: list[dict[str, Any]] = []
        manual_review: list[dict[str, Any]] = []

        for base_path in sorted(root.glob("*.json")):
            if base_path.name.endswith(".meta.json"):
                continue
            meta_path = base_path.with_name(base_path.stem + ".meta.json")
            if not meta_path.exists():
                manual_review.append(
                    {
                        "base_snapshot": str(base_path),
                        "reason": "missing_meta_sidecar",
                    }
                )
                continue
            try:
                meta_obj = _load_json_file(meta_path)
            except ValueError as exc:
                manual_review.append(
                    {
                        "base_snapshot": str(base_path),
                        "meta_snapshot": str(meta_path),
                        "reason": f"invalid_meta_json: {exc}",
                    }
                )
                continue
            if not isinstance(meta_obj, dict):
                manual_review.append(
                    {
                        "base_snapshot": str(base_path),
                        "meta_snapshot": str(meta_path),
                        "reason": "meta_not_object",
                    }
                )
                continue

            label, parsed_ts = _parse_snapshot_label_and_timestamp(base_path)
            meta_ts_text, meta_ts = _parse_meta_started_at(meta_obj, meta_path)
            ts = meta_ts if meta_ts else parsed_ts
            age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
            generated_by = _coerce_text(meta_obj.get("snapshot_generated_by")).strip()
            explicit_named = generated_by != "devonthink-link-traverse-folder"
            size_bytes = base_path.stat().st_size + meta_path.stat().st_size

            valid_pairs.append(
                {
                    "label": label,
                    "base_path": base_path,
                    "meta_path": meta_path,
                    "timestamp_text": meta_ts_text,
                    "timestamp": ts,
                    "age_days": round(age_days, 2),
                    "size_bytes": size_bytes,
                    "explicit_named": explicit_named,
                    "generated_by": generated_by or "unknown",
                }
            )

        valid_pairs.sort(key=lambda x: x["timestamp"], reverse=True)
        oldest_anchor_path = valid_pairs[-1]["base_path"] if valid_pairs else None

        keep_paths: set[Path] = set()
        protected_reasons: dict[Path, str] = {}

        # Keep most recent N pairs always.
        for pair in valid_pairs[: policy["keep_last_n"]]:
            keep_paths.add(pair["base_path"])
            protected_reasons[pair["base_path"]] = "keep_last_n"

        # Keep one per day/week/month in rolling windows.
        day_buckets: set[str] = set()
        week_buckets: set[str] = set()
        month_buckets: set[str] = set()
        for pair in valid_pairs:
            path = pair["base_path"]
            ts = pair["timestamp"]
            age_days = pair["age_days"]
            if path in keep_paths:
                continue

            if age_days <= policy["keep_daily_for"]:
                b = ts.strftime("%Y-%m-%d")
                if b not in day_buckets:
                    day_buckets.add(b)
                    keep_paths.add(path)
                    protected_reasons[path] = "keep_daily"
                continue

            if age_days <= policy["keep_weekly_for"]:
                b = ts.strftime("%G-W%V")
                if b not in week_buckets:
                    week_buckets.add(b)
                    keep_paths.add(path)
                    protected_reasons[path] = "keep_weekly"
                continue

            if age_days <= policy["keep_monthly_for"]:
                b = ts.strftime("%Y-%m")
                if b not in month_buckets:
                    month_buckets.add(b)
                    keep_paths.add(path)
                    protected_reasons[path] = "keep_monthly"

        if oldest_anchor_path is not None:
            keep_paths.add(oldest_anchor_path)
            protected_reasons[oldest_anchor_path] = "oldest_anchor"

        to_prune: list[dict[str, Any]] = []
        protected: list[dict[str, Any]] = []
        for pair in valid_pairs:
            path = pair["base_path"]
            if pair["explicit_named"]:
                protected.append(
                    {
                        "base_snapshot": str(pair["base_path"]),
                        "meta_snapshot": str(pair["meta_path"]),
                        "reason": "explicit_named_snapshot",
                    }
                )
                continue
            if path in keep_paths:
                protected.append(
                    {
                        "base_snapshot": str(pair["base_path"]),
                        "meta_snapshot": str(pair["meta_path"]),
                        "reason": protected_reasons.get(path, "retention"),
                    }
                )
                continue
            to_prune.append(pair)

        actions: list[dict[str, Any]] = []
        bytes_archived = 0
        bytes_deleted = 0
        archive_root = Path(archive_dir).expanduser().resolve() if archive_dir else (root / "archive")
        if mode == "apply":
            archive_root.mkdir(parents=True, exist_ok=True)
            for pair in to_prune:
                hard_delete_after = policy["hard_delete_after"]
                do_hard_delete = hard_delete_after is not None and pair["age_days"] > hard_delete_after
                if do_hard_delete:
                    pair["base_path"].unlink(missing_ok=True)
                    pair["meta_path"].unlink(missing_ok=True)
                    bytes_deleted += pair["size_bytes"]
                    actions.append(
                        {
                            "base_snapshot": str(pair["base_path"]),
                            "meta_snapshot": str(pair["meta_path"]),
                            "action": "deleted",
                        }
                    )
                else:
                    target_base = archive_root / pair["base_path"].name
                    target_meta = archive_root / pair["meta_path"].name
                    shutil.move(str(pair["base_path"]), str(target_base))
                    shutil.move(str(pair["meta_path"]), str(target_meta))
                    bytes_archived += pair["size_bytes"]
                    actions.append(
                        {
                            "base_snapshot": str(pair["base_path"]),
                            "meta_snapshot": str(pair["meta_path"]),
                            "archived_base_snapshot": str(target_base),
                            "archived_meta_snapshot": str(target_meta),
                            "action": "archived",
                        }
                    )

        report_candidates = [
            {
                "base_snapshot": str(pair["base_path"]),
                "meta_snapshot": str(pair["meta_path"]),
                "label": pair["label"],
                "age_days": pair["age_days"],
                "size_bytes": pair["size_bytes"],
            }
            for pair in to_prune
        ]
        bytes_prunable = sum(p["size_bytes"] for p in to_prune)

        return _response(
            tool="devonthink-link-prune-snapshots",
            started_at=started,
            inputs=inputs,
            ok=True,
            data={
                "mode": mode,
                "snapshot_dir": str(root),
                "archive_dir": str(archive_root),
                "retention_policy": policy,
                "summary": {
                    "valid_pair_count": len(valid_pairs),
                    "candidate_count": len(to_prune),
                    "protected_count": len(protected),
                    "manual_review_count": len(manual_review),
                    "bytes_prunable": bytes_prunable,
                    "bytes_archived": bytes_archived,
                    "bytes_deleted": bytes_deleted,
                },
                "candidates": report_candidates,
                "protected": protected,
                "manual_review": manual_review,
                "actions": actions,
            },
            stats={
                "candidate_count": len(to_prune),
                "manual_review_count": len(manual_review),
                "bytes_prunable": bytes_prunable,
                "bytes_archived": bytes_archived,
                "bytes_deleted": bytes_deleted,
            },
        )
    except ValueError as exc:
        return _response(
            tool="devonthink-link-prune-snapshots",
            started_at=started,
            inputs=inputs,
            ok=False,
            error=str(exc),
        )


def register_devonthink_link_tools(
    mcp: Any,
    *,
    include_tiers: set[str] | None = None,
) -> None:
    """Register composed link intelligence tools."""

    catalog = {entry["name"]: entry for entry in link_tool_catalog_entries(include_tiers=include_tiers)}

    def _enabled(tool_name: str) -> bool:
        return tool_name in catalog

    if _enabled("devonthink-link-resolve"):
        @mcp.tool(
            name="devonthink-link-resolve",
            description=catalog["devonthink-link-resolve"]["description"],
        )
        def _tool_link_resolve(record_ref: str) -> dict[str, Any]:
            return wrap_tool_call("devonthink-link-resolve", devonthink_link_resolve, record_ref)

    if _enabled("devonthink-link-audit-record"):
        @mcp.tool(
            name="devonthink-link-audit-record",
            description=catalog["devonthink-link-audit-record"]["description"],
        )
        def _tool_link_audit_record(
            record_ref: str,
            include_text_scan: bool = False,
            mode: str = "authoritative",
        ) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-audit-record",
                devonthink_link_audit_record,
                record_ref,
                include_text_scan=include_text_scan,
                mode=mode,
            )

    if _enabled("devonthink-link-audit-folder"):
        @mcp.tool(
            name="devonthink-link-audit-folder",
            description=catalog["devonthink-link-audit-folder"]["description"],
        )
        def _tool_link_audit_folder(folder_ref: str, limit: int = 50) -> dict[str, Any]:
            return wrap_tool_call("devonthink-link-audit-folder", devonthink_link_audit_folder, folder_ref, limit=limit)

    if _enabled("devonthink-link-map-neighborhood"):
        @mcp.tool(
            name="devonthink-link-map-neighborhood",
            description=catalog["devonthink-link-map-neighborhood"]["description"],
        )
        def _tool_link_map_neighborhood(record_ref: str, radius: int = 1, per_hop_limit: int = 20) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-map-neighborhood",
                devonthink_link_map_neighborhood,
                record_ref,
                radius=radius,
                per_hop_limit=per_hop_limit,
            )

    if _enabled("devonthink-link-find-orphans"):
        @mcp.tool(
            name="devonthink-link-find-orphans",
            description=catalog["devonthink-link-find-orphans"]["description"],
        )
        def _tool_link_find_orphans(folder_ref: str, limit: int = 100) -> dict[str, Any]:
            return wrap_tool_call("devonthink-link-find-orphans", devonthink_link_find_orphans, folder_ref, limit=limit)

    if _enabled("devonthink-link-suggest-related"):
        @mcp.tool(
            name="devonthink-link-suggest-related",
            description=catalog["devonthink-link-suggest-related"]["description"],
        )
        def _tool_link_suggest_related(record_ref: str, limit: int = 15) -> dict[str, Any]:
            return wrap_tool_call("devonthink-link-suggest-related", devonthink_link_suggest_related, record_ref, limit=limit)

    if _enabled("devonthink-link-score"):
        @mcp.tool(
            name="devonthink-link-score",
            description=catalog["devonthink-link-score"]["description"],
        )
        def _tool_link_score(record_refs: list[str]) -> dict[str, Any]:
            return wrap_tool_call("devonthink-link-score", devonthink_link_score, record_refs)

    if _enabled("devonthink-link-build-hub"):
        @mcp.tool(
            name="devonthink-link-build-hub",
            description=catalog["devonthink-link-build-hub"]["description"],
        )
        def _tool_link_build_hub(
            group_ref: str,
            seed_record_refs: list[str],
            hub_name: str = "Link Hub",
            mode: str = "overview",
        ) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-build-hub",
                devonthink_link_build_hub,
                group_ref=group_ref,
                seed_record_refs=seed_record_refs,
                hub_name=hub_name,
                mode=mode,
            )

    if _enabled("devonthink-link-enrich-metadata"):
        @mcp.tool(
            name="devonthink-link-enrich-metadata",
            description=catalog["devonthink-link-enrich-metadata"]["description"],
        )
        def _tool_link_enrich_metadata(record_ref: str, mode: str = "suggest", custom_key: str | None = None) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-enrich-metadata",
                devonthink_link_enrich_metadata,
                record_ref=record_ref,
                mode=mode,
                custom_key=custom_key,
            )

    if _enabled("devonthink-link-repair-links"):
        @mcp.tool(
            name="devonthink-link-repair-links",
            description=catalog["devonthink-link-repair-links"]["description"],
        )
        def _tool_link_repair_links(
            record_ref: str,
            mode: str = "report",
            remove_uuids: list[str] | None = None,
        ) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-repair-links",
                devonthink_link_repair_links,
                record_ref=record_ref,
                mode=mode,
                remove_uuids=remove_uuids,
            )

    if _enabled("devonthink-link-maintenance-pass"):
        @mcp.tool(
            name="devonthink-link-maintenance-pass",
            description=catalog["devonthink-link-maintenance-pass"]["description"],
        )
        def _tool_link_maintenance_pass(
            folder_ref: str,
            mode: str = "report",
            limit: int = 50,
            snapshot_dir: str = "snapshots",
        ) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-maintenance-pass",
                devonthink_link_maintenance_pass,
                folder_ref=folder_ref,
                mode=mode,
                limit=limit,
                snapshot_dir=snapshot_dir,
            )

    if _enabled("devonthink-link-detect-bridges"):
        @mcp.tool(
            name="devonthink-link-detect-bridges",
            description=catalog["devonthink-link-detect-bridges"]["description"],
        )
        def _tool_link_detect_bridges(folder_ref: str, limit: int = 80) -> dict[str, Any]:
            return wrap_tool_call("devonthink-link-detect-bridges", devonthink_link_detect_bridges, folder_ref=folder_ref, limit=limit)

    if _enabled("devonthink-link-check-reciprocal"):
        @mcp.tool(
            name="devonthink-link-check-reciprocal",
            description=catalog["devonthink-link-check-reciprocal"]["description"],
        )
        def _tool_link_check_reciprocal(source_ref: str, target_ref: str) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-check-reciprocal",
                devonthink_link_check_reciprocal,
                source_ref=source_ref,
                target_ref=target_ref,
            )

    if _enabled("devonthink-link-traverse-folder"):
        @mcp.tool(
            name="devonthink-link-traverse-folder",
            description=catalog["devonthink-link-traverse-folder"]["description"],
        )
        def _tool_link_traverse_folder(
            folder_ref: str,
            limit: int = 200,
            cursor: str | None = None,
            mode: str = "shallow",
            max_depth: int | None = None,
            include_smart_groups: bool = False,
            include_replicants: bool = False,
            group_path_tracking: bool = True,
            shape_thresholds: dict[str, int] | None = None,
            write_snapshot: bool = False,
            snapshot_label: str = "inbox_adjacency_baseline",
            snapshot_dir: str = "snapshots",
        ) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-traverse-folder",
                devonthink_link_traverse_folder,
                folder_ref=folder_ref,
                limit=limit,
                cursor=cursor,
                mode=mode,
                max_depth=max_depth,
                include_smart_groups=include_smart_groups,
                include_replicants=include_replicants,
                group_path_tracking=group_path_tracking,
                shape_thresholds=shape_thresholds,
                write_snapshot=write_snapshot,
                snapshot_label=snapshot_label,
                snapshot_dir=snapshot_dir,
            )

    if _enabled("devonthink-link-compare-snapshots"):
        @mcp.tool(
            name="devonthink-link-compare-snapshots",
            description=catalog["devonthink-link-compare-snapshots"]["description"],
        )
        def _tool_link_compare_snapshots(
            baseline_snapshot: str | None = None,
            current_snapshot: str | None = None,
            baseline_meta: str | None = None,
            current_meta: str | None = None,
            folder_ref: str | None = None,
            snapshot_dir: str = "snapshots",
        ) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-compare-snapshots",
                devonthink_link_compare_snapshots,
                baseline_snapshot=baseline_snapshot,
                current_snapshot=current_snapshot,
                baseline_meta=baseline_meta,
                current_meta=current_meta,
                folder_ref=folder_ref,
                snapshot_dir=snapshot_dir,
            )

    if _enabled("devonthink-link-prune-snapshots"):
        @mcp.tool(
            name="devonthink-link-prune-snapshots",
            description=catalog["devonthink-link-prune-snapshots"]["description"],
        )
        def _tool_link_prune_snapshots(
            snapshot_dir: str = "snapshots",
            retention: dict[str, Any] | None = None,
            mode: str = "report",
            archive_dir: str | None = None,
        ) -> dict[str, Any]:
            return wrap_tool_call(
                "devonthink-link-prune-snapshots",
                devonthink_link_prune_snapshots,
                snapshot_dir=snapshot_dir,
                retention=retention,
                mode=mode,
                archive_dir=archive_dir,
            )
