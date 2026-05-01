"""RTF and RTFD record tools for DEVONthink MCP."""

from __future__ import annotations

import json
import time
from typing import Any

from app.tools.telemetry import wrap_tool_call
from app.tools.tool_catalog import build_description, catalog_entry
from app.utils.applescript import AppleScriptExecutionError, run_applescript

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

on record_json(theRecord)
    set r_uuid to ""
    set r_name to ""
    set r_type to ""
    try
        set r_uuid to (uuid of theRecord) as text
    end try
    try
        set r_name to (name of theRecord) as text
    end try
    try
        set r_type to (type of theRecord) as text
    end try
    return "{" & "\"uuid\":" & my json_string(r_uuid) & "," & "\"name\":" & my json_string(r_name) & "," & "\"type\":" & my json_string(r_type) & "}"
end record_json
'''


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _error(exc: Exception, started: float) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def _validate_nonempty(value: str, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return cleaned


def _run_json(script: str, args: list[str], *, tool_name: str) -> Any:
    raw = run_applescript(script, args, tool_name=tool_name)
    return json.loads(raw) if raw else None


def _create_rich_record(name: str, content: str, parent_group_uuid: str, record_type: str, tool_name: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        cleaned_name = _validate_nonempty(name, "name")
        cleaned_parent = _validate_nonempty(parent_group_uuid, "parent_group_uuid")
        if content is None:
            raise ValueError("content must be a string.")
    except ValueError as exc:
        return _error(exc, started)

    script = _JSON_HELPERS + rf'''
on run argv
    set recordName to item 1 of argv
    set recordContent to item 2 of argv
    set parentUUID to item 3 of argv
    tell application id "DNtp"
        set theParent to get record with uuid parentUUID
        if theParent is missing value then error "Parent group not found for uuid: " & parentUUID
        set newRecord to create record with {{type:{record_type}, name:recordName, rich text:recordContent}} in theParent
        if newRecord is missing value then error "DEVONthink returned missing value while creating {record_type} record. Pass rich text content at creation time."
        return my record_json(newRecord)
    end tell
end run
end using terms from
'''
    try:
        record = _run_json(script, [cleaned_name, content, cleaned_parent], tool_name=tool_name)
        return {"ok": True, "data": record, "observability": {"duration_ms": _duration_ms(started)}}
    except (AppleScriptExecutionError, json.JSONDecodeError) as exc:
        return _error(exc, started)


def devonthink_create_rtf(name: str, content: str, parent_group_uuid: str) -> dict[str, Any]:
    return _create_rich_record(name, content, parent_group_uuid, "rtf", "devonthink-create-rtf")


def devonthink_create_rtfd(name: str, content: str, parent_group_uuid: str) -> dict[str, Any]:
    result = _create_rich_record(name, content, parent_group_uuid, "rtfd", "devonthink-create-rtfd")
    if result.get("ok"):
        actual_type = str((result.get("data") or {}).get("type") or "").strip()
        result["actual_type"] = actual_type
        obs = result.setdefault("observability", {})
        warnings = obs.setdefault("warnings", [])
        warnings.append("rtfd_text_only: Binary RTFD attachments require AppKit/NSData and are not embedded by this tool.")
        if actual_type.lower() in {"rtf", "rich text"}:
            warnings.append("rtfd_downgraded_to_rtf: DEVONthink created RTF instead of true RTFD because no binary attachment data was provided.")
    return result


def devonthink_read_rtf(record_uuid: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
    except ValueError as exc:
        return _error(exc, started)
    script = _JSON_HELPERS + r'''
on run argv
    set recordUUID to item 1 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        set plainText to ""
        set richText to ""
        try
            set plainText to (plain text of theRecord) as text
        end try
        try
            set richText to (rich text of theRecord) as text
        end try
        return "{" & "\"record\":" & my record_json(theRecord) & "," & "\"plain_text\":" & my json_string(plainText) & "," & "\"rich_text\":" & my json_string(richText) & "}"
    end tell
end run
end using terms from
'''
    try:
        data = _run_json(script, [cleaned_uuid], tool_name="devonthink-read-rtf")
        return {"ok": True, "data": data, "observability": {"duration_ms": _duration_ms(started)}}
    except (AppleScriptExecutionError, json.JSONDecodeError) as exc:
        return _error(exc, started)


def devonthink_update_rtf(record_uuid: str, content: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
        if content is None:
            raise ValueError("content must be a string.")
    except ValueError as exc:
        return _error(exc, started)
    script = r'''
using terms from application "DEVONthink"
on run argv
    set recordUUID to item 1 of argv
    set recordContent to item 2 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        set recordKind to (type of theRecord) as text
        if recordKind is "rtf" or recordKind is "rtfd" then
            set rich text of theRecord to recordContent
        else
            set plain text of theRecord to recordContent
        end if
    end tell
end run
end using terms from
'''
    try:
        run_applescript(script, [cleaned_uuid, content], tool_name="devonthink-update-rtf")
        return {"ok": True, "record_uuid": cleaned_uuid, "observability": {"duration_ms": _duration_ms(started)}}
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


_UPDATE_CONTENT_MODES = {"replace", "append", "prepend"}


def devonthink_update_record_content(
    record_uuid: str,
    content: str,
    mode: str = "replace",
) -> dict[str, Any]:
    """Update body content of any text-bearing DEVONthink record.

    Routes to `rich text` for rtf/rtfd records and `plain text` for everything else
    (markdown, txt, html, formatted note). Supports replace / append / prepend modes.
    Returns the post-update word_count and size so the caller can verify content
    persisted without a second round-trip.
    """
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
        if content is None:
            raise ValueError("content must be a string.")
        cleaned_mode = (mode or "replace").strip().lower()
        if cleaned_mode not in _UPDATE_CONTENT_MODES:
            raise ValueError(
                f"mode must be one of: {', '.join(sorted(_UPDATE_CONTENT_MODES))}."
            )
    except ValueError as exc:
        return _error(exc, started)

    script = _JSON_HELPERS + r'''
on run argv
    set recordUUID to item 1 of argv
    set recordContent to item 2 of argv
    set updateMode to item 3 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        set recordKind to (type of theRecord) as text
        set isRich to (recordKind is "rtf" or recordKind is "rtfd")
        set existing to ""
        if updateMode is not "replace" then
            try
                if isRich then
                    set existing to (rich text of theRecord) as text
                else
                    set existing to (plain text of theRecord) as text
                end if
            end try
        end if
        if updateMode is "append" then
            set newBody to existing & recordContent
        else if updateMode is "prepend" then
            set newBody to recordContent & existing
        else
            set newBody to recordContent
        end if
        if isRich then
            set rich text of theRecord to newBody
        else
            set plain text of theRecord to newBody
        end if
        set finalSize to 0
        set finalWords to 0
        try
            set finalSize to (size of theRecord) as integer
        end try
        try
            set finalWords to (word count of theRecord) as integer
        end try
        return "{" & "\"record\":" & my record_json(theRecord) & "," & "\"size\":" & finalSize & "," & "\"word_count\":" & finalWords & "," & "\"mode\":" & my json_string(updateMode) & "}"
    end tell
end run
end using terms from
'''
    try:
        data = _run_json(
            script,
            [cleaned_uuid, content, cleaned_mode],
            tool_name="devonthink-update-record-content",
        )
        verification: dict[str, Any] = {}
        if isinstance(data, dict):
            verification = {
                "size": data.get("size"),
                "word_count": data.get("word_count"),
                "mode": data.get("mode"),
                "content_persisted": bool(data.get("size")),
            }
        return {
            "ok": True,
            "data": data,
            "verification": verification,
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except (AppleScriptExecutionError, json.JSONDecodeError) as exc:
        return _error(exc, started)


def devonthink_read_rtfd(record_uuid: str) -> dict[str, Any]:
    result = devonthink_read_rtf(record_uuid)
    if result.get("ok"):
        result.setdefault("observability", {}).setdefault("warnings", []).append(
            "rtfd_text_only: read-rtfd returns text/rich-text properties, not embedded binary attachments."
        )
    return result


def devonthink_update_rtfd(record_uuid: str, content: str) -> dict[str, Any]:
    result = devonthink_update_rtf(record_uuid, content)
    if result.get("ok"):
        result.setdefault("observability", {}).setdefault("warnings", []).append(
            "rtfd_text_only: update-rtfd updates rich text content only, not embedded binary attachments."
        )
    return result


def _richtext_catalog_entry(
    *,
    name: str,
    summary: str,
    use_when: str,
    safety_class: str,
    prefer_when: str,
    example: str,
    tier: str,
    degradation_contract: str | None = None,
) -> dict[str, Any]:
    identifier_guidance = "Accepts a record UUID for read/update operations, or a parent group UUID for create operations."
    return catalog_entry(
        name=name,
        description=build_description(
            summary=summary,
            use_when=use_when,
            identifier_guidance=identifier_guidance,
            safety_class=safety_class,
            prefer_when=prefer_when,
            degradation_contract=degradation_contract,
            example=example,
        ),
        group="devonthink.native",
        tier=tier,
        status="active",
        canonical_tool=name,
        overlap_family="devonthink-richtext",
        source_path="app/tools/devonthink_richtext_tools.py",
        catalog_path=f"catalog-runtime/tools/devonthink.native/{tier}/{name}.json",
        executable="osascript",
        priority=100 if tier == "canonical" else 60,
        default_exposed=(tier == "canonical"),
        accepted_identifiers=["record_uuid", "group_uuid"],
        preferred_identifier="record_uuid",
        identifier_guidance=identifier_guidance,
        safety_class=safety_class,
        profile_availability=["minimal", "canonical", "full"],
        prefer_when=prefer_when,
        degradation_contract=degradation_contract,
        example=example,
        tags=["devonthink", "rich-text", "rtf", "rtfd"],
    )


def richtext_tool_catalog_entries() -> list[dict[str, Any]]:
    return [
        _richtext_catalog_entry(
            name="devonthink-create-rtf",
            summary="Create an RTF record with initial rich text content.",
            use_when="you need reliable RTF creation and want to avoid DEVONthink's empty-RTF missing-value behavior.",
            safety_class="writes_data",
            prefer_when="creating RTF records because it passes rich text at creation time; generic create-record type=rtf can hit DEVONthink's missing-value bug.",
            example='{"name":"Meeting Notes","content":"Attendees:\\n- Alice","parent_group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            tier="canonical",
        ),
        _richtext_catalog_entry(
            name="devonthink-read-rtf",
            summary="Read plain text and rich text properties from an RTF record.",
            use_when="you need the plain/rich text content of an RTF record, not link diagnostics or graph signals.",
            safety_class="read_only",
            prefer_when="you need record content text; use devonthink-link-audit-record mode=full for link extraction and risk diagnostics.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}',
            tier="canonical",
        ),
        _richtext_catalog_entry(
            name="devonthink-update-rtf",
            summary="Replace the rich text content of an RTF record.",
            use_when="you need to overwrite rich text content for an existing RTF record.",
            safety_class="writes_data",
            prefer_when="you are updating text content only.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","content":"Updated content"}',
            tier="canonical",
        ),
        _richtext_catalog_entry(
            name="devonthink-update-record-content",
            summary="Update body content of any text-bearing record (markdown/txt/html/rtf/rtfd) with replace/append/prepend.",
            use_when="you need a typed alternative to the raw devonthink-update dictionary command without guessing the mode parameter or AppleScript property.",
            safety_class="writes_data",
            prefer_when="you are updating any text-bearing record; this auto-routes to rich text for rtf/rtfd and plain text for markdown/txt/html.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","content":"\\n\\nappended","mode":"append"}',
            tier="canonical",
        ),
        _richtext_catalog_entry(
            name="devonthink-create-rtfd",
            summary="Create an RTFD record with text content.",
            use_when="you need an RTFD-style rich text record without embedded binary attachments.",
            safety_class="writes_data",
            prefer_when="plain text/rich text content is sufficient and binary attachments are not needed.",
            degradation_contract="Binary RTFD attachments are not constructable through this AppleScript-only tool; use AppKit/NSData for embedded assets.",
            example='{"name":"Report","content":"Initial text","parent_group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            tier="advanced",
        ),
        _richtext_catalog_entry(
            name="devonthink-read-rtfd",
            summary="Read text properties from an RTFD record.",
            use_when="you need text/rich-text content from an RTFD record.",
            safety_class="read_only",
            prefer_when="you do not need embedded binary attachment extraction.",
            degradation_contract="Returns text/rich-text properties only, not embedded binary attachments.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}',
            tier="advanced",
        ),
        _richtext_catalog_entry(
            name="devonthink-update-rtfd",
            summary="Update text content for an RTFD record.",
            use_when="you need to update text content without changing embedded attachments.",
            safety_class="writes_data",
            prefer_when="you are editing text only and can leave binary attachments untouched.",
            degradation_contract="Updates rich text content only; embedded binary attachments require a separate AppKit/NSData path.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","content":"Updated content"}',
            tier="advanced",
        ),
    ]


def register_devonthink_richtext_tools(mcp: Any) -> None:
    catalog = {entry["name"]: entry for entry in richtext_tool_catalog_entries()}

    @mcp.tool(name="devonthink-create-rtf", description=catalog["devonthink-create-rtf"]["description"])
    def _devonthink_create_rtf(name: str, content: str, parent_group_uuid: str) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-create-rtf",
            devonthink_create_rtf,
            name=name,
            content=content,
            parent_group_uuid=parent_group_uuid,
        )

    @mcp.tool(name="devonthink-read-rtf", description=catalog["devonthink-read-rtf"]["description"])
    def _devonthink_read_rtf(record_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-read-rtf", devonthink_read_rtf, record_uuid=record_uuid)

    @mcp.tool(name="devonthink-update-rtf", description=catalog["devonthink-update-rtf"]["description"])
    def _devonthink_update_rtf(record_uuid: str, content: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-update-rtf", devonthink_update_rtf, record_uuid=record_uuid, content=content)

    @mcp.tool(
        name="devonthink-update-record-content",
        description=catalog["devonthink-update-record-content"]["description"],
    )
    def _devonthink_update_record_content(
        record_uuid: str,
        content: str,
        mode: str = "replace",
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-update-record-content",
            devonthink_update_record_content,
            record_uuid=record_uuid,
            content=content,
            mode=mode,
        )

    @mcp.tool(name="devonthink-create-rtfd", description=catalog["devonthink-create-rtfd"]["description"])
    def _devonthink_create_rtfd(name: str, content: str, parent_group_uuid: str) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-create-rtfd",
            devonthink_create_rtfd,
            name=name,
            content=content,
            parent_group_uuid=parent_group_uuid,
        )

    @mcp.tool(name="devonthink-read-rtfd", description=catalog["devonthink-read-rtfd"]["description"])
    def _devonthink_read_rtfd(record_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-read-rtfd", devonthink_read_rtfd, record_uuid=record_uuid)

    @mcp.tool(name="devonthink-update-rtfd", description=catalog["devonthink-update-rtfd"]["description"])
    def _devonthink_update_rtfd(record_uuid: str, content: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-update-rtfd", devonthink_update_rtfd, record_uuid=record_uuid, content=content)
