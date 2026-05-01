"""Reminder management tools for DEVONthink MCP."""

from __future__ import annotations

import time
from typing import Any

from app.tools.telemetry import wrap_tool_call
from app.tools.tool_catalog import build_description, catalog_entry
from app.utils.applescript import AppleScriptExecutionError, run_applescript

VALID_ALARMS = {"none", "notification", "sound", "speech"}


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


def devonthink_list_reminders(record_uuid: str) -> dict[str, Any]:
    """List reminders attached to a DEVONthink record."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set recordUUID to item 1 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID

        set rows to {}
        repeat with r in reminders of theRecord
            set reminderID to ""
            set dueDateText to ""
            set alarmText to ""
            try
                set reminderID to (id of r) as text
            end try
            try
                set dueDateText to (due date of r) as text
            end try
            try
                set alarmText to (alarm of r) as text
            end try
            set end of rows to reminderID & (character id 9) & dueDateText & (character id 9) & alarmText
        end repeat

        set oldDelims to AppleScript's text item delimiters
        set AppleScript's text item delimiters to character id 10
        set output to rows as text
        set AppleScript's text item delimiters to oldDelims
        return output
    end tell
end run
'''
    try:
        raw = run_applescript(script, [cleaned_uuid], tool_name="devonthink-list-reminders")
        return {
            "ok": True,
            "record_uuid": cleaned_uuid,
            "reminders": _parse_rows(raw, ["id", "due_date", "alarm"]),
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def devonthink_delete_reminder(record_uuid: str, reminder_id: str) -> dict[str, Any]:
    """Delete one reminder from a DEVONthink record by reminder id."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
        cleaned_id = _validate_nonempty(reminder_id, "reminder_id")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set recordUUID to item 1 of argv
    set targetID to item 2 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID

        set targetReminder to missing value
        repeat with r in reminders of theRecord
            if ((id of r) as text) is targetID then
                set targetReminder to r
                exit repeat
            end if
        end repeat
        if targetReminder is missing value then error "Reminder not found for id: " & targetID
        delete targetReminder
    end tell
end run
'''
    try:
        run_applescript(script, [cleaned_uuid, cleaned_id], tool_name="devonthink-delete-reminder")
        return {
            "ok": True,
            "record_uuid": cleaned_uuid,
            "reminder_id": cleaned_id,
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def devonthink_update_reminder(
    record_uuid: str,
    reminder_id: str,
    due_date: str,
    alarm: str = "notification",
) -> dict[str, Any]:
    """Update one reminder's due date and alarm."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(record_uuid, "record_uuid")
        cleaned_id = _validate_nonempty(reminder_id, "reminder_id")
        cleaned_due_date = _validate_nonempty(due_date, "due_date")
        cleaned_alarm = (alarm or "").strip().lower()
        if cleaned_alarm not in VALID_ALARMS:
            raise ValueError(f"alarm must be one of: {', '.join(sorted(VALID_ALARMS))}")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set recordUUID to item 1 of argv
    set targetID to item 2 of argv
    set dueDateText to item 3 of argv
    set alarmText to item 4 of argv

    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID

        set targetReminder to missing value
        repeat with r in reminders of theRecord
            if ((id of r) as text) is targetID then
                set targetReminder to r
                exit repeat
            end if
        end repeat
        if targetReminder is missing value then error "Reminder not found for id: " & targetID

        set due date of targetReminder to date dueDateText
        if alarmText is "none" then
            set alarm of targetReminder to none
        else if alarmText is "notification" then
            set alarm of targetReminder to notification
        else if alarmText is "sound" then
            set alarm of targetReminder to sound
        else if alarmText is "speech" then
            set alarm of targetReminder to speech
        end if
    end tell
end run
'''
    try:
        run_applescript(
            script,
            [cleaned_uuid, cleaned_id, cleaned_due_date, cleaned_alarm],
            tool_name="devonthink-update-reminder",
        )
        return {
            "ok": True,
            "record_uuid": cleaned_uuid,
            "reminder_id": cleaned_id,
            "due_date": cleaned_due_date,
            "alarm": cleaned_alarm,
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def _reminder_catalog_entry(
    *,
    name: str,
    summary: str,
    use_when: str,
    safety_class: str,
    prefer_when: str,
    example: str,
    tier: str,
) -> dict[str, Any]:
    identifier_guidance = "Accepts a record UUID and reminder id where applicable."
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
        overlap_family="devonthink-reminder",
        source_path="app/tools/devonthink_reminder_tools.py",
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
        example=example,
        tags=["devonthink", "reminder"],
    )


def reminder_tool_catalog_entries() -> list[dict[str, Any]]:
    return [
        _reminder_catalog_entry(
            name="devonthink-list-reminders",
            summary="List reminders attached to a DEVONthink record.",
            use_when="you need reminder ids and current due dates before updating or deleting reminders.",
            safety_class="read_only",
            prefer_when="you need structured reminder metadata rather than raw add-reminder dictionary output.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}',
            tier="canonical",
        ),
        _reminder_catalog_entry(
            name="devonthink-delete-reminder",
            summary="Delete a reminder from a DEVONthink record by reminder id.",
            use_when="you need to remove a specific reminder discovered with devonthink-list-reminders.",
            safety_class="destructive",
            prefer_when="you have an exact reminder id and want to remove it from a record.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","reminder_id":"3"}',
            tier="advanced",
        ),
        _reminder_catalog_entry(
            name="devonthink-update-reminder",
            summary="Update the due date and alarm type for an existing DEVONthink reminder.",
            use_when="you need to reschedule a reminder without deleting and recreating it.",
            safety_class="writes_data",
            prefer_when="the target reminder already exists and you have its id from list-reminders.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","reminder_id":"3","due_date":"2026-05-01T09:00:00","alarm":"notification"}',
            tier="advanced",
        ),
    ]


def register_devonthink_reminder_tools(mcp: Any) -> None:
    catalog = {entry["name"]: entry for entry in reminder_tool_catalog_entries()}

    @mcp.tool(name="devonthink-list-reminders", description=catalog["devonthink-list-reminders"]["description"])
    def _devonthink_list_reminders(record_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-list-reminders", devonthink_list_reminders, record_uuid=record_uuid)

    @mcp.tool(name="devonthink-delete-reminder", description=catalog["devonthink-delete-reminder"]["description"])
    def _devonthink_delete_reminder(record_uuid: str, reminder_id: str) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-delete-reminder",
            devonthink_delete_reminder,
            record_uuid=record_uuid,
            reminder_id=reminder_id,
        )

    @mcp.tool(name="devonthink-update-reminder", description=catalog["devonthink-update-reminder"]["description"])
    def _devonthink_update_reminder(
        record_uuid: str,
        reminder_id: str,
        due_date: str,
        alarm: str = "notification",
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-update-reminder",
            devonthink_update_reminder,
            record_uuid=record_uuid,
            reminder_id=reminder_id,
            due_date=due_date,
            alarm=alarm,
        )
