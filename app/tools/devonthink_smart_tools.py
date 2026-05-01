"""Smart group and smart rule tools for DEVONthink MCP."""

from __future__ import annotations

import time
import plistlib
from pathlib import Path
from typing import Any

from app.tools.telemetry import wrap_tool_call
from app.tools.tool_catalog import build_description, catalog_entry
from app.utils.applescript import AppleScriptExecutionError, run_applescript


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _error(exc: Exception, started: float) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def _validate_nonempty(value: str, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return cleaned


def _parse_rows(raw: str, columns: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != len(columns):
            continue
        rows.append(dict(zip(columns, parts, strict=True)))
    return rows


def devonthink_create_smart_group(
    name: str,
    search_predicates: str,
    parent_group_uuid: str,
    search_group_uuid: str | None = None,
) -> dict[str, Any]:
    """Create a DEVONthink smart group with saved search predicates."""
    started = time.perf_counter()
    try:
        cleaned_name = _validate_nonempty(name, "name")
        cleaned_predicates = _validate_nonempty(search_predicates, "search_predicates")
        cleaned_parent = _validate_nonempty(parent_group_uuid, "parent_group_uuid")
        cleaned_scope = (search_group_uuid or "").strip()
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set groupName to item 1 of argv
    set predicatesText to item 2 of argv
    set parentUUID to item 3 of argv
    set scopeUUID to item 4 of argv

    tell application id "DNtp"
        set theParent to get record with uuid parentUUID
        if theParent is missing value then error "Parent group not found for uuid: " & parentUUID

        if scopeUUID is "" then
            set newGroup to create record with {name:groupName, type:smart group, search predicates:predicatesText} in theParent
        else
            set scopeGroup to get record with uuid scopeUUID
            if scopeGroup is missing value then error "Search group not found for uuid: " & scopeUUID
            set newGroup to create record with {name:groupName, type:smart group, search predicates:predicatesText, search group:scopeGroup} in theParent
        end if

        if newGroup is missing value then error "DEVONthink did not create a smart group."
        return (uuid of newGroup) as text
    end tell
end run
'''
    try:
        new_uuid = run_applescript(
            script,
            [cleaned_name, cleaned_predicates, cleaned_parent, cleaned_scope],
            tool_name="devonthink-create-smart-group",
        )
        return {
            "ok": True,
            "uuid": new_uuid.strip(),
            "name": cleaned_name,
            "search_predicates": cleaned_predicates,
            "parent_group_uuid": cleaned_parent,
            "search_group_uuid": cleaned_scope or None,
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def devonthink_list_smart_rules() -> dict[str, Any]:
    """List DEVONthink smart rules."""
    started = time.perf_counter()
    try:
        rules_path = Path.home() / "Library" / "Application Support" / "DEVONthink" / "SmartRules.plist"
        raw_rules = plistlib.loads(rules_path.read_bytes()) if rules_path.exists() else []
        rules = []
        for index, item in enumerate(raw_rules):
            if not isinstance(item, dict):
                continue
            sync = item.get("sync") if isinstance(item.get("sync"), dict) else {}
            rules.append(
                {
                    "id": str(sync.get("UUID") or item.get("UUID") or index),
                    "name": str(item.get("name") or ""),
                    "enabled": bool(item.get("Enabled", False)),
                }
            )
        return {
            "ok": True,
            "smart_rules": rules,
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except Exception as exc:  # noqa: BLE001
        return _error(exc, started)


def devonthink_apply_smart_rule(rule_name: str, record_uuid: str) -> dict[str, Any]:
    """Apply a named DEVONthink smart rule to a record."""
    started = time.perf_counter()
    try:
        cleaned_rule = _validate_nonempty(rule_name, "rule_name")
        cleaned_record = _validate_nonempty(record_uuid, "record_uuid")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set ruleName to item 1 of argv
    set recordUUID to item 2 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        return (perform smart rule name ruleName record theRecord) as text
    end tell
end run
'''
    try:
        result = run_applescript(script, [cleaned_rule, cleaned_record], tool_name="devonthink-apply-smart-rule")
        return {
            "ok": True,
            "rule_name": cleaned_rule,
            "record_uuid": cleaned_record,
            "result": result,
            "observability": {
                "duration_ms": _duration_ms(started),
                "warnings": [
                    "perform_smart_rule_context_limitation: DEVONthink smart rules that call get custom meta data may fail in smart rule context on affected DT/macOS versions."
                ],
            },
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def _smart_catalog_entry(
    *,
    name: str,
    summary: str,
    use_when: str,
    identifier_guidance: str,
    safety_class: str,
    prefer_when: str,
    example: str,
    accepted_identifiers: list[str],
    preferred_identifier: str | None,
    tier: str,
) -> dict[str, Any]:
    return catalog_entry(
        name=name,
        description=build_description(
            summary=summary,
            use_when=use_when,
            identifier_guidance=identifier_guidance,
            safety_class=safety_class,
            prefer_when=prefer_when,
            example=example,
        ),
        group="devonthink.native",
        tier=tier,
        status="active",
        canonical_tool=name,
        overlap_family="devonthink-smart",
        source_path="app/tools/devonthink_smart_tools.py",
        catalog_path=f"catalog-runtime/tools/devonthink.native/{tier}/{name}.json",
        executable="osascript",
        priority=100 if tier == "canonical" else 60,
        default_exposed=(tier == "canonical"),
        accepted_identifiers=accepted_identifiers,
        preferred_identifier=preferred_identifier,
        identifier_guidance=identifier_guidance,
        safety_class=safety_class,
        profile_availability=["minimal", "canonical", "full"],
        prefer_when=prefer_when,
        example=example,
        tags=["devonthink", "smart-group", "smart-rule"],
    )


def smart_tool_catalog_entries() -> list[dict[str, Any]]:
    return [
        _smart_catalog_entry(
            name="devonthink-create-smart-group",
            summary="Create a DEVONthink smart group with saved search predicates.",
            use_when="you need a saved dynamic query group such as all PDF files rather than a static folder-like group.",
            identifier_guidance="Accepts a parent group UUID, optional search group UUID, and DEVONthink search predicate string.",
            safety_class="writes_data",
            prefer_when="you need query-backed membership from search predicates; use devonthink-create-record type=group for a plain static group.",
            example='{"name":"PDF Files","search_predicates":"kind:PDF","parent_group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            accepted_identifiers=["group_uuid"],
            preferred_identifier="group_uuid",
            tier="canonical",
        ),
        _smart_catalog_entry(
            name="devonthink-list-smart-rules",
            summary="List smart rules configured in DEVONthink.",
            use_when="you need to discover named DEVONthink automation rules before applying one with devonthink-apply-smart-rule.",
            identifier_guidance="Takes no identifiers and returns smart rule ids and names.",
            safety_class="read_only",
            prefer_when="you need saved rule names, not filesystem script files; use devonthink-list-scripts for user AppleScript/JXA files.",
            example="{}",
            accepted_identifiers=[],
            preferred_identifier=None,
            tier="advanced",
        ),
        _smart_catalog_entry(
            name="devonthink-apply-smart-rule",
            summary="Apply a named DEVONthink smart rule to one record.",
            use_when="you want DEVONthink to run an existing named smart rule against a specific record, not execute arbitrary script code.",
            identifier_guidance="Accepts a smart rule name and record UUID.",
            safety_class="writes_data",
            prefer_when="the named rule logic already exists in DEVONthink; use devonthink-run-script only for filesystem scripts and arbitrary AppleScript/JXA execution.",
            example='{"rule_name":"Auto-Tag Invoices","record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}',
            accepted_identifiers=["record_uuid"],
            preferred_identifier="record_uuid",
            tier="advanced",
        ),
    ]


def register_devonthink_smart_tools(mcp: Any) -> None:
    catalog = {entry["name"]: entry for entry in smart_tool_catalog_entries()}

    @mcp.tool(name="devonthink-create-smart-group", description=catalog["devonthink-create-smart-group"]["description"])
    def _devonthink_create_smart_group(
        name: str,
        search_predicates: str,
        parent_group_uuid: str,
        search_group_uuid: str | None = None,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-create-smart-group",
            devonthink_create_smart_group,
            name=name,
            search_predicates=search_predicates,
            parent_group_uuid=parent_group_uuid,
            search_group_uuid=search_group_uuid,
        )

    @mcp.tool(name="devonthink-list-smart-rules", description=catalog["devonthink-list-smart-rules"]["description"])
    def _devonthink_list_smart_rules() -> dict[str, Any]:
        return wrap_tool_call("devonthink-list-smart-rules", devonthink_list_smart_rules)

    @mcp.tool(name="devonthink-apply-smart-rule", description=catalog["devonthink-apply-smart-rule"]["description"])
    def _devonthink_apply_smart_rule(rule_name: str, record_uuid: str) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-apply-smart-rule",
            devonthink_apply_smart_rule,
            rule_name=rule_name,
            record_uuid=record_uuid,
        )
