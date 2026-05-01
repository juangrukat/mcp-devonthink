"""Attached annotation-note tools for DEVONthink MCP."""

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


def devonthink_create_annotation_note(
    record_uuid: str,
    name: str,
    content: str,
    *,
    note_type: str = "txt",
    replace_existing: bool = False,
) -> dict[str, Any]:
    """Create a record in the database Annotations group and attach it as the record annotation."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
        cleaned_name = _validate_nonempty(name, "name")
        if content is None:
            raise ValueError("content must be a string.")
        if note_type not in {"txt", "rtf"}:
            raise ValueError("note_type must be either 'txt' or 'rtf'.")
    except ValueError as exc:
        return _error(exc, started)

    content_property = "plain text" if note_type == "txt" else "rich text"
    script = (
        _JSON_HELPERS
        + r'''
on run argv
    set targetUUID to item 1 of argv
    set noteName to item 2 of argv
    set noteContent to item 3 of argv
    set replaceFlag to item 4 of argv
    tell application id "DNtp"
        set targetRecord to get record with uuid targetUUID
        if targetRecord is missing value then error "Record not found for uuid: " & targetUUID
        set existingAnnotation to missing value
        try
            set existingAnnotation to annotation of targetRecord
        end try
        if existingAnnotation is not missing value and replaceFlag is not "true" then
            error "Record already has an attached annotation note. Pass replace_existing=true to attach a new note."
        end if
        set targetDatabase to database of targetRecord
        set annotationGroup to annotations group of targetDatabase
        if annotationGroup is missing value then error "Annotations group not available for target database."
        set noteRecord to create record with {type:__NOTE_TYPE__, name:noteName, __CONTENT_PROPERTY__:noteContent} in annotationGroup
        if noteRecord is missing value then error "DEVONthink returned missing value while creating annotation note."
        set annotation of targetRecord to noteRecord
        return "{" & "\"target\":" & my record_json(targetRecord) & "," & "\"annotation\":" & my record_json(noteRecord) & "," & "\"replaced\":" & my json_string(replaceFlag) & "}"
    end tell
end run
end using terms from
'''
    )
    script = script.replace("__NOTE_TYPE__", note_type).replace("__CONTENT_PROPERTY__", content_property)
    try:
        data = _run_json(
            script,
            [cleaned_uuid, cleaned_name, content, "true" if replace_existing else "false"],
            tool_name="devonthink-create-annotation-note",
        )
        return {
            "ok": True,
            "data": data,
            "observability": {
                "duration_ms": _duration_ms(started),
                "warnings": [
                    "attached_annotation_note: This attaches a note record through DEVONthink's annotation property; it does not create PDF-internal highlights, comments, or markup."
                ],
            },
        }
    except (AppleScriptExecutionError, json.JSONDecodeError) as exc:
        return _error(exc, started)


def devonthink_read_annotation_note(record_uuid: str) -> dict[str, Any]:
    """Read the note record attached through DEVONthink's annotation property."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
    except ValueError as exc:
        return _error(exc, started)

    script = _JSON_HELPERS + r'''
on run argv
    set targetUUID to item 1 of argv
    tell application id "DNtp"
        set targetRecord to get record with uuid targetUUID
        if targetRecord is missing value then error "Record not found for uuid: " & targetUUID
        set noteRecord to missing value
        try
            set noteRecord to annotation of targetRecord
        end try
        if noteRecord is missing value then
            return "{" & "\"target\":" & my record_json(targetRecord) & "," & "\"annotation\":null," & "\"plain_text\":\"\"," & "\"rich_text\":\"\"}"
        end if
        set plainText to ""
        set richText to ""
        try
            set plainText to plain text of noteRecord as text
        end try
        try
            set richText to rich text of noteRecord as text
        end try
        return "{" & "\"target\":" & my record_json(targetRecord) & "," & "\"annotation\":" & my record_json(noteRecord) & "," & "\"plain_text\":" & my json_string(plainText) & "," & "\"rich_text\":" & my json_string(richText) & "}"
    end tell
end run
end using terms from
'''
    try:
        data = _run_json(script, [cleaned_uuid], tool_name="devonthink-read-annotation-note")
        return {"ok": True, "data": data, "observability": {"duration_ms": _duration_ms(started)}}
    except (AppleScriptExecutionError, json.JSONDecodeError) as exc:
        return _error(exc, started)


def _annotation_catalog_entry(
    *,
    name: str,
    summary: str,
    use_when: str,
    safety_class: str,
    prefer_when: str,
    example: str,
    tier: str,
) -> dict[str, Any]:
    identifier_guidance = "Accepts a DEVONthink record UUID for the target record or PDF."
    return catalog_entry(
        name=name,
        description=build_description(
            summary=summary,
            use_when=use_when,
            identifier_guidance=identifier_guidance,
            safety_class=safety_class,
            prefer_when=prefer_when,
            degradation_contract=(
                "Creates or reads the attached annotation note record only. It does not create, edit, "
                "or summarize PDF-internal highlights, comments, drawing markup, or page annotations."
            ),
            example=example,
        ),
        group="devonthink.native",
        tier=tier,
        status="active",
        canonical_tool=name,
        overlap_family="devonthink-annotation-note",
        source_path="app/tools/devonthink_annotation_tools.py",
        catalog_path=f"catalog-runtime/tools/devonthink.native/{tier}/{name}.json",
        executable="osascript",
        priority=100 if tier == "canonical" else 60,
        default_exposed=(tier == "canonical"),
        accepted_identifiers=["record_uuid"],
        preferred_identifier="record_uuid",
        identifier_guidance=identifier_guidance,
        safety_class=safety_class,
        profile_availability=["minimal", "canonical", "full"],
        prefer_when=prefer_when,
        degradation_contract=(
            "Attached annotation note only; PDF-internal highlights and markup remain a separate DEVONthink/PDF annotation capability."
        ),
        example=example,
        tags=["devonthink", "annotation", "annotation-note", "pdf"],
    )


def annotation_tool_catalog_entries() -> list[dict[str, Any]]:
    return [
        _annotation_catalog_entry(
            name="devonthink-create-annotation-note",
            summary="Create a DEVONthink annotation note record and attach it to a target record or PDF.",
            use_when="you want the record's attached annotation note, stored in the database Annotations group, not a PDF-internal highlight or markup annotation. Valid targets include PDFs, normal records, groups, smart groups, and annotation-note records.",
            safety_class="writes_data",
            prefer_when="the request says attach an annotation note, annotation file, or sample text annotation to a record/PDF/group; use PDF-specific tools or DEVONthink's summarize-annotations command for internal PDF highlights and markup.",
            example='{"record_uuid":"94E29B42-FBE9-482A-9500-8945DF69568D","name":"MCP PDF Annotation File","content":"Sample annotation note","note_type":"txt","replace_existing":false}',
            tier="canonical",
        ),
        _annotation_catalog_entry(
            name="devonthink-read-annotation-note",
            summary="Read the DEVONthink annotation note record attached to a target record or PDF.",
            use_when="you need to inspect the attached annotation note and its text content.",
            safety_class="read_only",
            prefer_when="the request asks for the annotation note attached through the record annotation property; use summarize-annotations for PDF-internal highlights and markup.",
            example='{"record_uuid":"94E29B42-FBE9-482A-9500-8945DF69568D"}',
            tier="canonical",
        ),
    ]


def register_devonthink_annotation_tools(mcp: Any) -> None:
    catalog = {entry["name"]: entry for entry in annotation_tool_catalog_entries()}

    @mcp.tool(
        name="devonthink-create-annotation-note",
        description=catalog["devonthink-create-annotation-note"]["description"],
    )
    def _devonthink_create_annotation_note(
        record_uuid: str,
        name: str,
        content: str,
        note_type: str = "txt",
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-create-annotation-note",
            devonthink_create_annotation_note,
            record_uuid=record_uuid,
            name=name,
            content=content,
            note_type=note_type,
            replace_existing=replace_existing,
        )

    @mcp.tool(
        name="devonthink-read-annotation-note",
        description=catalog["devonthink-read-annotation-note"]["description"],
    )
    def _devonthink_read_annotation_note(record_uuid: str) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-read-annotation-note",
            devonthink_read_annotation_note,
            record_uuid=record_uuid,
        )
