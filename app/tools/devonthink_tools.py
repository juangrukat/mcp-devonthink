"""DEVONthink AppleScript-backed MCP tools."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import date, datetime, timedelta
from typing import Any

from app.tools.applescript_counter import record_applescript_call
from app.tools.tool_catalog import build_description, catalog_entry
from app.tools.telemetry import wrap_tool_call

log = logging.getLogger(__name__)

RECORD_TYPE_ALIASES = {
    "markdown": "markdown",
    "md": "markdown",
    "txt": "txt",
    "text": "txt",
    "plain text": "txt",
    "plain-text": "txt",
    "rtf": "rtf",
    "rtfd": "rtfd",
    "rich text": "rtf",
    "formatted note": "formatted note",
    "formatted-note": "formatted note",
    "html": "html",
    "group": "group",
    "pdf": "pdf document",
    "pdf document": "pdf document",
    "picture": "picture",
    "image": "picture",
    "images": "picture",
    "multimedia": "multimedia",
    "media": "multimedia",
    "video": "multimedia",
    "audio": "multimedia",
    "bookmark": "bookmark",
}

VIDEO_EXTENSIONS = {
    "3g2",
    "3gp",
    "avi",
    "m4v",
    "mkv",
    "mov",
    "mp4",
    "mpeg",
    "mpg",
    "mts",
    "mxf",
    "ogv",
    "webm",
    "wmv",
}

AUDIO_EXTENSIONS = {
    "aac",
    "aif",
    "aiff",
    "alac",
    "flac",
    "m4a",
    "mp3",
    "ogg",
    "opus",
    "wav",
    "wma",
}

MEDIA_KINDS = {"any", "audio", "video", "multimedia"}
TEXTISH_RECORD_TYPES = {"txt", "rtf", "rtfd", "markdown", "html", "formatted note"}
FILTER_CONTENT_MODES = {"auto", "search", "plain_text"}
TAG_MATCH_MODES = {"all", "any"}
DEDUPE_MODES = {"none", "uuid", "path"}


class AppleScriptExecutionError(RuntimeError):
    """Raised when an AppleScript command fails."""


def _validate_uuid(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return cleaned


# Reject characters that turn a record title into a filesystem-traversal hazard.
# DEVONthink sanitizes the on-disk filename but stores the raw title verbatim,
# and downstream scripts (export, rename, link audits) routinely consume `name`
# directly. We block path separators, NUL/control bytes, and the literal `..`
# segment; common punctuation like `:` is left alone since it appears in real
# titles ("Re: meeting").
def _validate_record_name(value: str, field_name: str = "name") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be a non-empty string.")
    if "\x00" in cleaned:
        raise ValueError(f"{field_name} must not contain NUL bytes.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned):
        raise ValueError(f"{field_name} must not contain control characters.")
    if "/" in cleaned or "\\" in cleaned:
        raise ValueError(
            f"{field_name} must not contain path separators ('/' or '\\\\'); "
            "DEVONthink stores names verbatim and these can cause path-traversal "
            "issues in downstream consumers."
        )
    # Block bare or boundary-positioned `..` segments. We don't treat `..` inside
    # a longer token (e.g. `foo..bar`) as traversal — only the literal segment.
    parts = cleaned.replace("\\", "/").split("/")
    if any(p == ".." for p in parts) or cleaned == "..":
        raise ValueError(f"{field_name} must not contain '..' path segments.")
    return cleaned


def _validate_limit(limit: int) -> int:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200.")
    return limit


def _validate_offset(offset: int) -> int:
    if offset < 0:
        raise ValueError("offset must be >= 0.")
    return offset


def _validate_scan_limit(scan_limit: int) -> int:
    if scan_limit < 1 or scan_limit > 10000:
        raise ValueError("scan_limit must be between 1 and 10000.")
    return scan_limit


def _validate_range(value: int, field_name: str, minimum: int, maximum: int) -> int:
    if value < minimum or value > maximum:
        raise ValueError(f"{field_name} must be an integer between {minimum} and {maximum}.")
    return value


def _normalize_record_type(value: str) -> str:
    cleaned = value.strip().lower()
    if not cleaned:
        raise ValueError("record_type must be a non-empty string.")
    normalized = RECORD_TYPE_ALIASES.get(cleaned)
    if normalized is None:
        valid = ", ".join(sorted(set(RECORD_TYPE_ALIASES.values())))
        raise ValueError(
            f"Unsupported record_type '{value}'. Use one of: {valid}. "
            "Common aliases accepted: plain text -> txt, rich text -> rtf, image -> picture, pdf -> pdf document."
        )
    return normalized


def _validate_media_kind(value: str) -> str:
    cleaned = (value or "video").strip().lower()
    if cleaned not in MEDIA_KINDS:
        valid = ", ".join(sorted(MEDIA_KINDS))
        raise ValueError(f"media_kind must be one of: {valid}.")
    return cleaned


def _validate_tags(tags: list[str] | None) -> list[str]:
    if tags is None:
        return []
    cleaned: list[str] = []
    for tag in tags:
        value = str(tag).strip()
        if not value:
            continue
        if "||" in value:
            raise ValueError("tags cannot contain the delimiter '||'.")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def _validate_comment_mode(value: str) -> str:
    cleaned = (value or "replace").strip().lower()
    if cleaned not in {"append", "prepend", "replace"}:
        raise ValueError("comment_mode must be one of: append, prepend, replace.")
    return cleaned


def _normalize_filter_record_types(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    normalized: set[str] = set()
    for value in values:
        normalized.add(_normalize_record_type(str(value)))
    return normalized


def _normalize_extensions(values: list[str] | None) -> set[str]:
    if not values:
        return set()
    normalized: set[str] = set()
    for value in values:
        cleaned = str(value).strip().lower().removeprefix(".")
        if cleaned:
            normalized.add(cleaned)
    return normalized


def _normalize_nonempty_list(values: list[str] | None, field_name: str) -> list[str]:
    if not values:
        return []
    cleaned = []
    for value in values:
        item = str(value).strip()
        if item and item not in cleaned:
            cleaned.append(item)
    if values and not cleaned:
        raise ValueError(f"{field_name} must contain at least one non-empty value when provided.")
    return cleaned


def _validate_choice(value: str, field_name: str, allowed: set[str]) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned not in allowed:
        valid = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {valid}.")
    return cleaned


def _parse_iso_date(value: str | None, field_name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date in YYYY-MM-DD format.") from exc


def _date_start_ts(value: date | None) -> float | None:
    if value is None:
        return None
    return datetime.combine(value, datetime.min.time()).timestamp()


def _date_end_ts(value: date | None) -> float | None:
    if value is None:
        return None
    return datetime.combine(value, datetime.max.time()).timestamp()


def _within_days_start_ts(days: int | None, field_name: str) -> float | None:
    if days is None:
        return None
    if days < 0 or days > 36500:
        raise ValueError(f"{field_name} must be between 0 and 36500.")
    return datetime.combine(date.today() - timedelta(days=days), datetime.min.time()).timestamp()


def _classify_osascript_error(stderr: str) -> str:
    lowered = stderr.lower()
    if "not authorized" in lowered or "-1743" in lowered:
        return (
            "Apple Events permission denied. In macOS System Settings > Privacy & Security > "
            "Automation, allow your terminal/Python host to control DEVONthink."
        )
    if "application isn't running" in lowered:
        return "DEVONthink is not running. Start DEVONthink and try again."
    if "can't get application" in lowered:
        return "DEVONthink is not installed or not available to AppleScript on this Mac."
    return stderr.strip() or "Unknown AppleScript execution error."


def _run_osascript(script: str, args: list[str], *, tool_name: str, extra: dict[str, Any] | None = None) -> str:
    record_applescript_call()
    if log.isEnabledFor(logging.DEBUG):
        log.debug("AppleScript for %s:\n%s", tool_name, script)
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


def _run_json_script(
    script: str,
    args: list[str],
    *,
    tool_name: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = _run_osascript(script, args, tool_name=tool_name, extra=extra)
    if not raw:
        return {"ok": True, "data": None}
    try:
        return {"ok": True, "data": json.loads(raw)}
    except json.JSONDecodeError as exc:
        raise AppleScriptExecutionError(f"Failed to parse AppleScript JSON output: {exc}") from exc


def _extension_from_record(record: dict[str, Any]) -> str | None:
    for key in ("filename", "path", "name"):
        value = record.get(key)
        if not isinstance(value, str) or "." not in value:
            continue
        suffix = value.rsplit(".", 1)[-1].strip().lower()
        if suffix:
            return suffix
    return None


def _record_media_kind(record: dict[str, Any]) -> str | None:
    extension = _extension_from_record(record)
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"

    mime_type = str(record.get("mime_type") or "").strip().lower()
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"

    record_type = str(record.get("type") or record.get("record_type") or "").strip().lower()
    kind = str(record.get("kind") or "").strip().lower()
    if record_type == "multimedia":
        if any(token in kind for token in ("movie", "video", "mpeg-4", "quicktime")):
            return "video"
        if any(token in kind for token in ("audio", "sound", "music", "mp3")):
            return "audio"
        return "multimedia"

    return None


def _enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    extension = _extension_from_record(record)
    if extension:
        record["extension"] = extension
    media_kind = _record_media_kind(record)
    if media_kind:
        record["media_kind"] = media_kind
        record["is_media"] = True
    else:
        record["is_media"] = False
    return record


def _enrich_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_enrich_record(record) for record in records]


def _numeric_value(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _matches_numeric_range(
    record: dict[str, Any],
    key: str,
    minimum: int | float | None,
    maximum: int | float | None,
) -> bool:
    if minimum is None and maximum is None:
        return True
    value = _numeric_value(record, key)
    if value is None:
        return False
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


def _matches_date_range(
    record: dict[str, Any],
    key: str,
    start_ts: float | None,
    end_ts: float | None,
) -> bool:
    if start_ts is None and end_ts is None:
        return True
    value = _numeric_value(record, key)
    if value is None:
        return False
    if start_ts is not None and value < start_ts:
        return False
    if end_ts is not None and value > end_ts:
        return False
    return True


def _matches_tags(record: dict[str, Any], tags: list[str], tag_match: str) -> bool:
    if not tags:
        return True
    record_tags = {str(tag).casefold() for tag in (record.get("tags") or [])}
    wanted = {tag.casefold() for tag in tags}
    if tag_match == "any":
        return bool(record_tags.intersection(wanted))
    return wanted.issubset(record_tags)


def _dedupe_records(records: list[dict[str, Any]], dedupe_by: str) -> tuple[list[dict[str, Any]], int]:
    if dedupe_by == "none":
        return records, 0
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_count = 0
    for record in records:
        key_value = record.get(dedupe_by)
        key = str(key_value) if key_value else ""
        if not key:
            deduped.append(record)
            continue
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        deduped.append(record)
    return deduped, duplicate_count


_DEVONTHINK_JSON_HELPERS = r'''
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

on maybe_number(valueAny)
    if valueAny is missing value then
        return "null"
    end if
    return valueAny as text
end maybe_number

on epoch_date()
    set d to current date
    set year of d to 1970
    set month of d to January
    set day of d to 1
    set time of d to 0
    return d
end epoch_date

on maybe_date_seconds(valueAny)
    if valueAny is missing value then
        return "null"
    end if
    try
        return (valueAny - my epoch_date()) as text
    on error
        return "null"
    end try
end maybe_date_seconds

on maybe_bool(valueAny)
    if valueAny is missing value then
        return "null"
    end if
    if valueAny as boolean then
        return "true"
    end if
    return "false"
end maybe_bool

on list_json(valueAny)
    if valueAny is missing value then return "[]"
    try
        if class of valueAny is text then
            if valueAny as text is "" then return "[]"
            return "[" & my json_string(valueAny as text) & "]"
        end if
    end try
    try
        set n to count of valueAny
    on error
        return "[]"
    end try
    if n is 0 then return "[]"
    set output to "["
    repeat with i from 1 to n
        set output to output & my json_string(item i of valueAny as text)
        if i is not n then set output to output & ","
    end repeat
    return output & "]"
end list_json

on record_json(theRecord)
    set r_uuid to missing value
    set r_id to missing value
    set r_name to missing value
    set r_type to missing value
    set r_kind to missing value
    set r_mime_type to missing value
    set r_location to missing value
    set r_location_with_name to missing value
    set r_url to missing value
    set r_reference_url to missing value
    set r_path to missing value
    set r_filename to missing value
    set r_comment to missing value
    set r_tags to {}
    set r_aliases to missing value
    set r_label to missing value
    set r_rating to missing value
    set r_size to missing value
    set r_duration to missing value
    set r_width to missing value
    set r_height to missing value
    set r_page_count to missing value
    set r_word_count to missing value
    set r_created to missing value
    set r_modified to missing value
    set r_added to missing value
    set r_database_uuid to missing value
    set r_indexed to missing value
    set r_unread to missing value
    set r_flagged to missing value
    set r_locked to missing value

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
        set r_type to record type of theRecord
    end try
    try
        set r_kind to kind of theRecord
    end try
    try
        set r_mime_type to MIME type of theRecord
    end try
    try
        set r_location to location of theRecord
    end try
    try
        set r_location_with_name to location with name of theRecord
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
        set r_filename to filename of theRecord
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
        set r_label to label of theRecord
    end try
    try
        set r_rating to rating of theRecord
    end try
    try
        set r_size to size of theRecord
    end try
    try
        set r_duration to duration of theRecord
    end try
    try
        set r_width to width of theRecord
    end try
    try
        set r_height to height of theRecord
    end try
    try
        set r_page_count to page count of theRecord
    end try
    try
        set r_word_count to word count of theRecord
    end try
    try
        set r_created to creation date of theRecord
    end try
    try
        set r_modified to modification date of theRecord
    end try
    try
        set r_added to addition date of theRecord
    end try
    try
        set r_database_uuid to uuid of (database of theRecord)
    end try
    try
        set r_indexed to indexed of theRecord
    end try
    try
        set r_unread to unread of theRecord
    end try
    try
        set r_flagged to flag of theRecord
    end try
    try
        set r_locked to locked of theRecord
    end try

    return "{" & ¬
        "\"uuid\":" & my maybe_text(r_uuid) & "," & ¬
        "\"id\":" & my maybe_number(r_id) & "," & ¬
        "\"name\":" & my maybe_text(r_name) & "," & ¬
        "\"type\":" & my maybe_text(r_type) & "," & ¬
        "\"record_type\":" & my maybe_text(r_type) & "," & ¬
        "\"kind\":" & my maybe_text(r_kind) & "," & ¬
        "\"mime_type\":" & my maybe_text(r_mime_type) & "," & ¬
        "\"location\":" & my maybe_text(r_location) & "," & ¬
        "\"location_with_name\":" & my maybe_text(r_location_with_name) & "," & ¬
        "\"url\":" & my maybe_text(r_url) & "," & ¬
        "\"reference_url\":" & my maybe_text(r_reference_url) & "," & ¬
        "\"path\":" & my maybe_text(r_path) & "," & ¬
        "\"filename\":" & my maybe_text(r_filename) & "," & ¬
        "\"comment\":" & my maybe_text(r_comment) & "," & ¬
        "\"tags\":" & my list_json(r_tags) & "," & ¬
        "\"aliases\":" & my list_json(r_aliases) & "," & ¬
        "\"label\":" & my maybe_number(r_label) & "," & ¬
        "\"rating\":" & my maybe_number(r_rating) & "," & ¬
        "\"size\":" & my maybe_number(r_size) & "," & ¬
        "\"duration\":" & my maybe_number(r_duration) & "," & ¬
        "\"width\":" & my maybe_number(r_width) & "," & ¬
        "\"height\":" & my maybe_number(r_height) & "," & ¬
        "\"page_count\":" & my maybe_number(r_page_count) & "," & ¬
        "\"word_count\":" & my maybe_number(r_word_count) & "," & ¬
        "\"created\":" & my maybe_text(r_created) & "," & ¬
        "\"created_ts\":" & my maybe_date_seconds(r_created) & "," & ¬
        "\"modified\":" & my maybe_text(r_modified) & "," & ¬
        "\"modified_ts\":" & my maybe_date_seconds(r_modified) & "," & ¬
        "\"added\":" & my maybe_text(r_added) & "," & ¬
        "\"added_ts\":" & my maybe_date_seconds(r_added) & "," & ¬
        "\"database_uuid\":" & my maybe_text(r_database_uuid) & "," & ¬
        "\"indexed\":" & my maybe_bool(r_indexed) & "," & ¬
        "\"unread\":" & my maybe_bool(r_unread) & "," & ¬
        "\"flagged\":" & my maybe_bool(r_flagged) & "," & ¬
        "\"locked\":" & my maybe_bool(r_locked) & ¬
        "}"
end record_json

on database_json(theDatabase)
    set db_uuid to missing value
    set db_id to missing value
    set db_name to missing value
    set db_path to missing value

    try
        set db_uuid to uuid of theDatabase
    end try
    try
        set db_id to id of theDatabase
    end try
    try
        set db_name to name of theDatabase
    end try
    try
        set db_path to path of theDatabase
    end try

    return "{" & "\"uuid\":" & my maybe_text(db_uuid) & "," & "\"id\":" & my maybe_text(db_id) & "," & "\"name\":" & my maybe_text(db_name) & "," & "\"path\":" & my maybe_text(db_path) & "}"
end database_json
end using terms from
'''


def devonthink_get_database_by_uuid(database_uuid: str) -> dict[str, Any]:
    """Get a DEVONthink database by UUID (read-only lookup)."""

    try:
        cleaned_uuid = _validate_uuid(database_uuid, "database_uuid")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set databaseUUID to item 1 of argv

    tell application "DEVONthink"
        set theDatabase to get database with uuid databaseUUID
        return my database_json(theDatabase)
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [cleaned_uuid],
            tool_name="devonthink-get-database-by-uuid",
            extra={"database_uuid": cleaned_uuid},
        )
        if not result.get("ok"):
            return result
        data = result.get("data") or {}
        if not any(data.get(k) is not None for k in ("uuid", "id", "name", "path")):
            return {
                "ok": False,
                "error": f"No database found for database_uuid='{cleaned_uuid}'.",
            }
        return result
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_get_record_by_uuid(record_uuid: str, database_uuid: str | None = None) -> dict[str, Any]:
    """Get a DEVONthink record by UUID or item link (read-only lookup)."""

    try:
        cleaned_record_uuid = _validate_uuid(record_uuid, "record_uuid")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    cleaned_database_uuid = database_uuid.strip() if database_uuid else ""

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set recordUUID to item 1 of argv
    set databaseUUID to item 2 of argv

    tell application "DEVONthink"
        if databaseUUID is "" then
            set theRecord to get record with uuid recordUUID
        else
            set theDatabase to get database with uuid databaseUUID
            set theRecord to get record with uuid recordUUID in theDatabase
        end if
        return my record_json(theRecord)
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [cleaned_record_uuid, cleaned_database_uuid],
            tool_name="devonthink-get-record-by-uuid",
            extra={"record_uuid": cleaned_record_uuid, "database_uuid": cleaned_database_uuid or None},
        )
        if not result.get("ok"):
            return result
        data = result.get("data") or {}
        if not any(data.get(k) is not None for k in ("uuid", "id", "name", "type", "location", "url")):
            return {
                "ok": False,
                "error": f"No record found for record_uuid='{cleaned_record_uuid}'.",
            }
        result["data"] = _enrich_record(data)
        return result
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_read_record_text(record_uuid: str, max_chars: int = 20000) -> dict[str, Any]:
    """Read the DEVONthink plain text/transcription property for one record."""

    try:
        cleaned_record_uuid = _validate_uuid(record_uuid, "record_uuid")
        if max_chars < 1 or max_chars > 200000:
            raise ValueError("max_chars must be between 1 and 200000.")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set recordUUID to item 1 of argv
    set maxChars to item 2 of argv as integer

    tell application "DEVONthink"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        set textValue to ""
        try
            if plain text of theRecord is not missing value then set textValue to plain text of theRecord as text
        end try
        set textLength to length of textValue
        set truncatedFlag to false
        if textLength is greater than maxChars then
            set textValue to text 1 thru maxChars of textValue
            set truncatedFlag to true
        end if
        return "{" & ¬
            "\"record\":" & my record_json(theRecord) & "," & ¬
            "\"text\":" & my json_string(textValue) & "," & ¬
            "\"text_length\":" & textLength & "," & ¬
            "\"truncated\":" & my maybe_bool(truncatedFlag) & ¬
            "}"
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [cleaned_record_uuid, str(max_chars)],
            tool_name="devonthink-read-record-text",
            extra={"record_uuid": cleaned_record_uuid, "max_chars": max_chars},
        )
        data = result.get("data") or {}
        record = data.get("record")
        if isinstance(record, dict):
            data["record"] = _enrich_record(record)
        return {
            "ok": True,
            "record_uuid": cleaned_record_uuid,
            "record": data.get("record"),
            "text": data.get("text") or "",
            "text_length": data.get("text_length") or 0,
            "truncated": bool(data.get("truncated")),
            "observability": {
                "warnings": [
                    "Uses DEVONthink's plain text/transcription property. For exact PDF page ranges, DEVONthink AppleScript may not expose page-delimited text."
                ]
            },
        }
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_search_records(
    query: str,
    limit: int = 25,
    database_uuid: str | None = None,
    comparison: str | None = None,
    exclude_subgroups: bool = False,
) -> dict[str, Any]:
    """Search DEVONthink records by query string (read-only lookup)."""

    cleaned_query = query.strip() if query else ""
    if not cleaned_query:
        return {"ok": False, "error": "query must be a non-empty string."}

    try:
        cleaned_limit = _validate_limit(limit)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    cleaned_database_uuid = database_uuid.strip() if database_uuid else ""
    cleaned_comparison = comparison.strip() if comparison else ""

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set searchQuery to item 1 of argv
    set maxCount to (item 2 of argv as integer)
    set databaseUUID to item 3 of argv
    set comparisonMode to item 4 of argv
    set excludeSubgroupsText to item 5 of argv
    set excludeSubgroupsFlag to false
    if excludeSubgroupsText is "true" then
        set excludeSubgroupsFlag to true
    end if

    tell application "DEVONthink"
        if databaseUUID is "" then
            if comparisonMode is "" then
                set foundRecords to search searchQuery exclude subgroups excludeSubgroupsFlag
            else
                set foundRecords to search searchQuery comparison comparisonMode exclude subgroups excludeSubgroupsFlag
            end if
        else
            -- Accept either a group UUID or a database UUID.
            -- Try group/record first; fall back to database root group.
            set theScope to missing value
            try
                set theScope to get record with uuid databaseUUID
            end try
            if theScope is missing value then
                try
                    set theScope to incoming group of (get database with uuid databaseUUID)
                end try
            end if
            if theScope is missing value then
                error "Scope not found for uuid: " & databaseUUID
            end if
            if comparisonMode is "" then
                set foundRecords to search searchQuery in theScope exclude subgroups excludeSubgroupsFlag
            else
                set foundRecords to search searchQuery comparison comparisonMode in theScope exclude subgroups excludeSubgroupsFlag
            end if
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
        result = _run_json_script(
            script,
            [
                cleaned_query,
                str(cleaned_limit),
                cleaned_database_uuid,
                cleaned_comparison,
                "true" if exclude_subgroups else "false",
            ],
            tool_name="devonthink-search-records",
            extra={
                "query": cleaned_query,
                "limit": cleaned_limit,
                "database_uuid": cleaned_database_uuid or None,
                "comparison": cleaned_comparison or None,
                "exclude_subgroups": exclude_subgroups,
            },
        )
        if result.get("ok"):
            items = _enrich_records(result.get("data") or [])
            return {
                "ok": True,
                "query": cleaned_query,
                "count": len(items),
                "limit": cleaned_limit,
                "database_uuid": cleaned_database_uuid or None,
                "comparison": cleaned_comparison or None,
                "exclude_subgroups": exclude_subgroups,
                "records": items,
            }
        return result
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_search_media_records(
    media_kind: str = "video",
    limit: int = 25,
    database_uuid: str | None = None,
) -> dict[str, Any]:
    """Find DEVONthink audio/video records by record type, not localized kind text."""

    try:
        cleaned_media_kind = _validate_media_kind(media_kind)
        cleaned_limit = _validate_limit(limit)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    cleaned_database_uuid = database_uuid.strip() if database_uuid else ""
    scan_limit = 200 if cleaned_media_kind in {"audio", "video"} else cleaned_limit

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set maxCount to (item 1 of argv as integer)
    set databaseUUID to item 2 of argv

    tell application "DEVONthink"
        if databaseUUID is "" then
            set sourceDatabases to databases
        else
            set sourceDatabases to {get database with uuid databaseUUID}
        end if

        set output to "["
        set emittedCount to 0
        repeat with theDatabase in sourceDatabases
            try
                set candidateRecords to contents of theDatabase whose record type is multimedia
            on error
                set candidateRecords to {}
            end try
            repeat with theRecord in candidateRecords
                if emittedCount is greater than 0 then set output to output & ","
                set output to output & my record_json(theRecord)
                set emittedCount to emittedCount + 1
                if emittedCount is greater than or equal to maxCount then
                    set output to output & "]"
                    return output
                end if
            end repeat
        end repeat
        set output to output & "]"
        return output
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [str(scan_limit), cleaned_database_uuid],
            tool_name="devonthink-search-media-records",
            extra={
                "media_kind": cleaned_media_kind,
                "limit": cleaned_limit,
                "database_uuid": cleaned_database_uuid or None,
                "scan_limit": scan_limit,
            },
        )
        records = _enrich_records(result.get("data") or [])
        if cleaned_media_kind in {"audio", "video"}:
            records = [record for record in records if record.get("media_kind") == cleaned_media_kind]
        elif cleaned_media_kind == "any":
            records = [record for record in records if record.get("is_media")]
        records = records[:cleaned_limit]
        return {
            "ok": True,
            "media_kind": cleaned_media_kind,
            "count": len(records),
            "limit": cleaned_limit,
            "database_uuid": cleaned_database_uuid or None,
            "records": records,
            "observability": {
                "scan_limit": scan_limit,
                "warnings": [
                    "Searches DEVONthink record type 'multimedia' instead of localized kind:Movie/kind:Audio text. "
                    "This avoids false positives from documents whose kind metadata is misleading."
                ],
            },
        }
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_filter_records(
    query: str | None = None,
    query_terms_any: list[str] | None = None,
    record_types: list[str] | None = None,
    file_extensions: list[str] | None = None,
    name_contains: str | None = None,
    filename_contains: str | None = None,
    path_contains: str | None = None,
    tags: list[str] | None = None,
    tag_match: str = "all",
    created_from: str | None = None,
    created_to: str | None = None,
    modified_from: str | None = None,
    modified_to: str | None = None,
    added_from: str | None = None,
    added_to: str | None = None,
    created_within_days: int | None = None,
    modified_within_days: int | None = None,
    added_within_days: int | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
    min_height: int | None = None,
    max_height: int | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    content_mode: str = "auto",
    dedupe_by: str = "uuid",
    limit: int = 50,
    scan_limit: int = 2000,
    database_uuid: str | None = None,
) -> dict[str, Any]:
    """Search/enumerate DEVONthink records, then filter by structured record properties."""

    cleaned_query = (query or "").strip()
    cleaned_name_contains = (name_contains or "").strip().casefold()
    cleaned_filename_contains = (filename_contains or "").strip().casefold()
    cleaned_path_contains = (path_contains or "").strip().casefold()
    try:
        cleaned_terms_any = _normalize_nonempty_list(query_terms_any, "query_terms_any")
        cleaned_record_types = _normalize_filter_record_types(record_types)
        cleaned_extensions = _normalize_extensions(file_extensions)
        cleaned_tags = _validate_tags(tags)
        cleaned_tag_match = _validate_choice(tag_match, "tag_match", TAG_MATCH_MODES)
        cleaned_content_mode = _validate_choice(content_mode, "content_mode", FILTER_CONTENT_MODES)
        cleaned_dedupe_by = _validate_choice(dedupe_by, "dedupe_by", DEDUPE_MODES)
        cleaned_limit = _validate_limit(limit)
        cleaned_scan_limit = _validate_scan_limit(scan_limit)
        created_from_date = _parse_iso_date(created_from, "created_from")
        created_to_date = _parse_iso_date(created_to, "created_to")
        modified_from_date = _parse_iso_date(modified_from, "modified_from")
        modified_to_date = _parse_iso_date(modified_to, "modified_to")
        added_from_date = _parse_iso_date(added_from, "added_from")
        added_to_date = _parse_iso_date(added_to, "added_to")
        created_start = _date_start_ts(created_from_date)
        modified_start = _date_start_ts(modified_from_date)
        added_start = _date_start_ts(added_from_date)
        created_within_start = _within_days_start_ts(created_within_days, "created_within_days")
        modified_within_start = _within_days_start_ts(modified_within_days, "modified_within_days")
        added_within_start = _within_days_start_ts(added_within_days, "added_within_days")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if created_within_start is not None:
        created_start = max(created_start, created_within_start) if created_start is not None else created_within_start
    if modified_within_start is not None:
        modified_start = max(modified_start, modified_within_start) if modified_start is not None else modified_within_start
    if added_within_start is not None:
        added_start = max(added_start, added_within_start) if added_start is not None else added_within_start

    created_end = _date_end_ts(created_to_date)
    modified_end = _date_end_ts(modified_to_date)
    added_end = _date_end_ts(added_to_date)
    cleaned_database_uuid = database_uuid.strip() if database_uuid else ""

    textish_filter = bool(cleaned_record_types.intersection(TEXTISH_RECORD_TYPES) or cleaned_extensions.intersection(TEXTISH_RECORD_TYPES))
    plain_text_filter = bool(cleaned_query or cleaned_terms_any) and (
        cleaned_content_mode == "plain_text" or (cleaned_content_mode == "auto" and textish_filter)
    )
    search_query = cleaned_query
    if cleaned_terms_any and not plain_text_filter:
        terms_query = "any: " + " ".join(cleaned_terms_any)
        search_query = f"{search_query} {terms_query}".strip() if search_query else terms_query
    use_search_source = bool(search_query) and not plain_text_filter
    source_mode = "search" if use_search_source else "all"

    script = _DEVONTHINK_JSON_HELPERS + r'''
on split_terms(s)
    if s is "" then return {}
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "||"
    set parts to text items of s
    set AppleScript's text item delimiters to oldDelims
    return parts
end split_terms

on list_contains_text(itemsList, candidateText)
    repeat with itemText in itemsList
        if (itemText as text) is candidateText then return true
    end repeat
    return false
end list_contains_text

on extension_of_path(pathText)
    if pathText is "" then return ""
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "."
    set parts to text items of pathText
    set AppleScript's text item delimiters to oldDelims
    if (count of parts) is less than 2 then return ""
    return item (count of parts) of parts as text
end extension_of_path

on record_matches_prefilter(theRecord, wantedTypes, wantedExtensions)
    tell application id "DNtp"
        if (count of wantedTypes) is greater than 0 then
            set typeText to ""
            try
                set typeText to record type of theRecord as text
            end try
            if typeText is "" or not my list_contains_text(wantedTypes, typeText) then return false
        end if

        if (count of wantedExtensions) is greater than 0 then
            set pathText to ""
            try
                if filename of theRecord is not missing value then set pathText to filename of theRecord as text
            end try
            if pathText is "" then
                try
                    if path of theRecord is not missing value then set pathText to path of theRecord as text
                end try
            end if
            if pathText is "" then
                try
                    set pathText to name of theRecord as text
                end try
            end if
            set extText to my extension_of_path(pathText)
            if extText is "" or not my list_contains_text(wantedExtensions, extText) then return false
        end if
    end tell
    return true
end record_matches_prefilter

on record_matches_plain_text(theRecord, requiredText, anyTerms)
    if requiredText is "" and (count of anyTerms) is 0 then return true
    set recordText to ""
    tell application id "DNtp"
        try
            if plain text of theRecord is not missing value then set recordText to plain text of theRecord as text
        end try
    end tell
    if requiredText is not "" and recordText does not contain requiredText then return false
    if (count of anyTerms) is greater than 0 then
        repeat with candidate in anyTerms
            set candidateText to candidate as text
            if candidateText is not "" and recordText contains candidateText then return true
        end repeat
        return false
    end if
    return true
end record_matches_plain_text

on run argv
    set sourceMode to item 1 of argv
    set searchQuery to item 2 of argv
    set plainTextQuery to item 3 of argv
    set anyTermsJoined to item 4 of argv
    set recordTypesJoined to item 5 of argv
    set extensionsJoined to item 6 of argv
    set maxCount to (item 7 of argv as integer)
    set databaseUUID to item 8 of argv
    set anyTerms to my split_terms(anyTermsJoined)
    set wantedTypes to my split_terms(recordTypesJoined)
    set wantedExtensions to my split_terms(extensionsJoined)

    tell application "DEVONthink"
        if sourceMode is "search" then
            if databaseUUID is "" then
                set candidateRecords to search searchQuery
            else
                set theScope to missing value
                try
                    set theScope to get record with uuid databaseUUID
                end try
                if theScope is missing value then
                    try
                        set theScope to incoming group of (get database with uuid databaseUUID)
                    end try
                end if
                if theScope is missing value then error "Scope not found for uuid: " & databaseUUID
                set candidateRecords to search searchQuery in theScope
            end if
        else
            if databaseUUID is "" then
                set sourceDatabases to databases
            else
                set sourceDatabases to {get database with uuid databaseUUID}
            end if
            set candidateRecords to {}
            repeat with theDatabase in sourceDatabases
                try
                    set candidateRecords to candidateRecords & (contents of theDatabase)
                end try
            end repeat
        end if

        set output to "["
        set emittedCount to 0
        repeat with theRecord in candidateRecords
            if my record_matches_prefilter(theRecord, wantedTypes, wantedExtensions) and my record_matches_plain_text(theRecord, plainTextQuery, anyTerms) then
                if emittedCount is greater than 0 then set output to output & ","
                set output to output & my record_json(theRecord)
                set emittedCount to emittedCount + 1
                if emittedCount is greater than or equal to maxCount then exit repeat
            end if
        end repeat
        set output to output & "]"
        return output
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [
                source_mode,
                search_query if use_search_source else "",
                cleaned_query if plain_text_filter else "",
                "||".join(cleaned_terms_any) if plain_text_filter else "",
                "||".join(sorted(cleaned_record_types)),
                "||".join(sorted(cleaned_extensions)),
                str(cleaned_scan_limit),
                cleaned_database_uuid,
            ],
            tool_name="devonthink-filter-records",
            extra={
                "query": cleaned_query or None,
                "query_terms_any": cleaned_terms_any or None,
                "source_mode": source_mode,
                "content_mode": cleaned_content_mode,
                "plain_text_filter": plain_text_filter,
                "record_types": sorted(cleaned_record_types),
                "file_extensions": sorted(cleaned_extensions),
                "name_contains": cleaned_name_contains or None,
                "filename_contains": cleaned_filename_contains or None,
                "path_contains": cleaned_path_contains or None,
                "tags": cleaned_tags,
                "tag_match": cleaned_tag_match,
                "scan_limit": cleaned_scan_limit,
                "database_uuid": cleaned_database_uuid or None,
            },
        )
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}

    candidates = _enrich_records(result.get("data") or [])
    filtered = []
    for record in candidates:
        record_type = str(record.get("record_type") or record.get("type") or "").strip().lower()
        extension = str(record.get("extension") or "").strip().lower()
        if cleaned_record_types and record_type not in cleaned_record_types:
            continue
        if cleaned_extensions and extension not in cleaned_extensions:
            continue
        if cleaned_name_contains and cleaned_name_contains not in str(record.get("name") or "").casefold():
            continue
        if cleaned_filename_contains and cleaned_filename_contains not in str(record.get("filename") or "").casefold():
            continue
        if cleaned_path_contains and cleaned_path_contains not in str(record.get("path") or "").casefold():
            continue
        if not _matches_tags(record, cleaned_tags, cleaned_tag_match):
            continue
        if not _matches_date_range(record, "created_ts", created_start, created_end):
            continue
        if not _matches_date_range(record, "modified_ts", modified_start, modified_end):
            continue
        if not _matches_date_range(record, "added_ts", added_start, added_end):
            continue
        if not _matches_numeric_range(record, "width", min_width, max_width):
            continue
        if not _matches_numeric_range(record, "height", min_height, max_height):
            continue
        if not _matches_numeric_range(record, "size", min_size, max_size):
            continue
        filtered.append(record)

    deduped, duplicate_count = _dedupe_records(filtered, cleaned_dedupe_by)
    records = deduped[:cleaned_limit]
    warnings = [
        "This tool avoids DEVONthink's brittle kind:/label:/date predicate parsing by filtering structured record properties."
    ]
    if source_mode == "all":
        warnings.append("Enumerated records directly; increase scan_limit if you suspect relevant records appear after the scan window.")
    if plain_text_filter:
        warnings.append("Verified content with the record plain text property; this can be slower but works for rich text and other text-bearing records.")

    return {
        "ok": True,
        "query": cleaned_query or None,
        "query_terms_any": cleaned_terms_any or None,
        "count": len(records),
        "matched_before_limit": len(deduped),
        "candidate_count": len(candidates),
        "filtered_count": len(filtered),
        "limit": cleaned_limit,
        "scan_limit": cleaned_scan_limit,
        "database_uuid": cleaned_database_uuid or None,
        "source_mode": source_mode,
        "content_mode": cleaned_content_mode,
        "plain_text_filter": plain_text_filter,
        "dedupe_by": cleaned_dedupe_by,
        "duplicates_removed": duplicate_count,
        "filters": {
            "record_types": sorted(cleaned_record_types),
            "file_extensions": sorted(cleaned_extensions),
            "name_contains": name_contains,
            "filename_contains": filename_contains,
            "path_contains": path_contains,
            "tags": cleaned_tags,
            "tag_match": cleaned_tag_match,
            "created_from": created_from,
            "created_to": created_to,
            "modified_from": modified_from,
            "modified_to": modified_to,
            "added_from": added_from,
            "added_to": added_to,
            "created_within_days": created_within_days,
            "modified_within_days": modified_within_days,
            "added_within_days": added_within_days,
            "min_width": min_width,
            "max_width": max_width,
            "min_height": min_height,
            "max_height": max_height,
            "min_size": min_size,
            "max_size": max_size,
        },
        "records": records,
        "observability": {"warnings": warnings},
    }


def devonthink_set_custom_metadata(
    record_uuid: str,
    key: str,
    value: Any,
) -> dict[str, Any]:
    """Typed wrapper around DEVONthink's `add custom meta data` command.

    The raw dictionary command takes (direct=value, for=key, to=record), which
    led callers to mistake `direct` for a record UUID. This wrapper accepts
    explicit (record_uuid, key, value) and returns the round-tripped value to
    confirm the write took effect.
    """
    try:
        cleaned_uuid = _validate_uuid(record_uuid, "record_uuid")
        cleaned_key = (key or "").strip()
        if not cleaned_key:
            raise ValueError("key must be a non-empty string.")
        if value is None:
            raise ValueError("value must not be None.")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if isinstance(value, bool):
        literal = "true" if value else "false"
        is_numeric = True
    elif isinstance(value, (int, float)):
        literal = repr(value)
        is_numeric = True
    else:
        literal = json.dumps(str(value))  # quoted, escaped string literal
        is_numeric = False

    # AppleScript embeds the literal directly so booleans and numbers stay typed.
    script = r'''
on run argv
    set recordUUID to item 1 of argv
    set metaKey to item 2 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        add custom meta data __VALUE__ for metaKey to theRecord
        set roundTrip to get custom meta data for metaKey from theRecord
        try
            set roundTripText to roundTrip as text
        on error
            set roundTripText to ""
        end try
        return roundTripText
    end tell
end run
'''.replace("__VALUE__", literal)

    try:
        raw = _run_osascript(
            script,
            [cleaned_uuid, cleaned_key],
            tool_name="devonthink-set-custom-metadata",
            extra={"record_uuid": cleaned_uuid, "key": cleaned_key, "value_kind": "numeric" if is_numeric else "string"},
        )
        return {
            "ok": True,
            "data": {
                "record_uuid": cleaned_uuid,
                "key": cleaned_key,
                "value": value,
                "round_trip": raw.strip() if isinstance(raw, str) else raw,
            },
            "verification": {
                "value_persisted": bool(raw),
            },
        }
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_create_record(
    name: str,
    record_type: str,
    group_uuid: str | None = None,
    content: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Create a DEVONthink record (advanced, writes data).

    For textual record types (txt, markdown, html, formatted note, rtf, rtfd) the
    optional `content` is written into the record body at creation time. For non-textual
    types (group, picture, pdf document, multimedia, bookmark) `content` is ignored
    and a warning is returned. For `bookmark` records the optional `url` is set on
    the record at creation time.
    """

    try:
        cleaned_name = _validate_record_name(name, "name")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cleaned_record_type = _normalize_record_type(record_type)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    cleaned_group_uuid = group_uuid.strip() if group_uuid else ""
    body = "" if content is None else str(content)
    write_content = bool(body) and cleaned_record_type in TEXTISH_RECORD_TYPES
    rich_target = cleaned_record_type in {"rtf", "rtfd"}
    cleaned_url = url.strip() if isinstance(url, str) else ""
    write_url = bool(cleaned_url) and cleaned_record_type == "bookmark"

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set recordName to item 1 of argv
    set recordType to item 2 of argv
    set groupUUID to item 3 of argv
    set recordBody to item 4 of argv
    set writeFlag to item 5 of argv
    set richFlag to item 6 of argv
    set recordURL to item 7 of argv
    set urlFlag to item 8 of argv

    tell application "DEVONthink"
        if groupUUID is "" then
            set createdRecord to create record with {name:recordName, record type:recordType}
        else
            set destinationGroup to get record with uuid groupUUID
            if destinationGroup is missing value then
                error "Group not found for uuid: " & groupUUID
            end if
            set createdRecord to create record with {name:recordName, record type:recordType} in destinationGroup
        end if
        if urlFlag is "1" then
            try
                set URL of createdRecord to recordURL
            end try
        end if
        if writeFlag is "1" then
            if richFlag is "1" then
                try
                    set rich text of createdRecord to recordBody
                end try
            else
                try
                    set plain text of createdRecord to recordBody
                end try
            end if
        end if
        return my record_json(createdRecord)
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [
                cleaned_name,
                cleaned_record_type,
                cleaned_group_uuid,
                body,
                "1" if write_content else "0",
                "1" if rich_target else "0",
                cleaned_url,
                "1" if write_url else "0",
            ],
            tool_name="devonthink-create-record",
            extra={
                "name": cleaned_name,
                "record_type": cleaned_record_type,
                "group_uuid": cleaned_group_uuid or None,
                "content_written": write_content,
                "url_written": write_url,
            },
        )
        data = result.get("data")
        if isinstance(data, dict):
            result["data"] = _enrich_record(data)
            # Surface a tight verification block so callers don't need a second
            # get-record-by-uuid round-trip just to confirm content landed.
            verification = {
                "content_requested": bool(body),
                "content_written": write_content,
                "size": data.get("size"),
                "word_count": data.get("word_count"),
                "uuid": data.get("uuid"),
            }
            if write_content:
                verification["content_persisted"] = bool(data.get("size"))
            if write_url:
                verification["url_requested"] = True
                verification["url_persisted"] = bool(data.get("url"))
            result["verification"] = verification
        if body and not write_content:
            obs = result.setdefault("observability", {})
            obs.setdefault("warnings", []).append(
                f"content_ignored: record_type '{cleaned_record_type}' does not accept inline content; "
                "use devonthink-import-path or a type-specific tool instead."
            )
        if cleaned_url and not write_url:
            obs = result.setdefault("observability", {})
            obs.setdefault("warnings", []).append(
                f"url_ignored: record_type '{cleaned_record_type}' is not a bookmark; "
                "url is only persisted when record_type is 'bookmark'."
            )
        if rich_target and write_content:
            obs = result.setdefault("observability", {})
            obs.setdefault("warnings", []).append(
                "rtf_plaintext_only: content was written as plain rich text. For styled RTF use devonthink-create-rtf."
            )
        return result
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_get_database_incoming_group(database_uuid: str) -> dict[str, Any]:
    """Get a database's incoming group (Inbox/root search scope helper)."""

    try:
        cleaned_uuid = _validate_uuid(database_uuid, "database_uuid")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set databaseUUID to item 1 of argv

    tell application "DEVONthink"
        set theDatabase to get database with uuid databaseUUID
        set targetGroup to incoming group of theDatabase
        return my record_json(targetGroup)
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [cleaned_uuid],
            tool_name="devonthink-get-database-incoming-group",
            extra={"database_uuid": cleaned_uuid},
        )
        data = result.get("data") or {}
        if not any(data.get(k) is not None for k in ("uuid", "id", "name", "type", "location", "url")):
            return {"ok": False, "error": f"No incoming group found for database_uuid='{cleaned_uuid}'."}
        result["data"] = _enrich_record(data)
        return result
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_list_group_children(
    group_uuid: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List direct child records for a group UUID.

    Returns paginated results with `total`, `has_more`, and `next_offset` so
    callers can detect truncation and walk through large groups deterministically.
    `total` is the total number of direct children in the group at the moment of
    the call; `has_more` is True when more children exist past `offset + count`.
    """

    try:
        cleaned_uuid = _validate_uuid(group_uuid, "group_uuid")
        cleaned_limit = _validate_limit(limit)
        cleaned_offset = _validate_offset(offset)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set groupUUID to item 1 of argv
    set maxCount to (item 2 of argv as integer)
    set startOffset to (item 3 of argv as integer)

    tell application "DEVONthink"
        set targetGroup to get record with uuid groupUUID
        if targetGroup is missing value then error "Record not found for uuid: " & groupUUID
        set childRecords to children of targetGroup
        set totalCount to count of childRecords

        set startIndex to startOffset + 1
        if startIndex > totalCount then
            set startIndex to totalCount + 1
        end if
        set endIndex to startIndex + maxCount - 1
        if endIndex > totalCount then set endIndex to totalCount

        set output to "{\"total\":" & totalCount & ",\"items\":["
        if startIndex is less than or equal to endIndex then
            repeat with i from startIndex to endIndex
                set output to output & my record_json(item i of childRecords)
                if i is not endIndex then set output to output & ","
            end repeat
        end if
        set output to output & "]}"
        return output
    end tell
end run
'''

    try:
        result = _run_json_script(
            script,
            [cleaned_uuid, str(cleaned_limit), str(cleaned_offset)],
            tool_name="devonthink-list-group-children",
            extra={
                "group_uuid": cleaned_uuid,
                "limit": cleaned_limit,
                "offset": cleaned_offset,
            },
        )
        data = result.get("data") or {}
        items = _enrich_records(data.get("items") or [])
        total = int(data.get("total") or 0)
        end_index = cleaned_offset + len(items)
        has_more = end_index < total
        return {
            "ok": True,
            "group_uuid": cleaned_uuid,
            "count": len(items),
            "limit": cleaned_limit,
            "offset": cleaned_offset,
            "total": total,
            "has_more": has_more,
            "next_offset": end_index if has_more else None,
            "records": items,
        }
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_set_label(record_uuid: str, label: int) -> dict[str, Any]:
    try:
        cleaned_uuid = _validate_uuid(record_uuid, "record_uuid")
        cleaned_label = _validate_range(label, "label", 0, 7)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = r'''
on run argv
    set recordUUID to item 1 of argv
    set labelValue to item 2 of argv as integer
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        set label of theRecord to labelValue
        if (label of theRecord as integer) is not labelValue then
            error "DEVONthink did not apply label to record " & recordUUID & ". This may be a DEVONthink/API limitation for the record type."
        end if
    end tell
end run
'''
    try:
        _run_osascript(script, [cleaned_uuid, str(cleaned_label)], tool_name="devonthink-set-label")
        return {"ok": True, "record_uuid": cleaned_uuid, "label": cleaned_label}
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_batch_set_label(record_uuids: list[str], label: int) -> dict[str, Any]:
    try:
        if not record_uuids:
            raise ValueError("record_uuids must contain at least one UUID.")
        cleaned_uuids = [_validate_uuid(value, "record_uuid") for value in record_uuids]
        cleaned_label = _validate_range(label, "label", 0, 7)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = r'''
on run argv
    set labelValue to item 1 of argv as integer
    set updatedCount to 0
    tell application id "DNtp"
        repeat with i from 2 to count of argv
            set recordUUID to item i of argv
            set theRecord to get record with uuid recordUUID
            if theRecord is missing value then error "Record not found for uuid: " & recordUUID
            set label of theRecord to labelValue
            if (label of theRecord as integer) is not labelValue then
                error "DEVONthink did not apply label to record " & recordUUID & ". This may be a DEVONthink/API limitation for the record type."
            end if
            set updatedCount to updatedCount + 1
        end repeat
    end tell
    return updatedCount as text
end run
'''
    try:
        raw = _run_osascript(script, [str(cleaned_label), *cleaned_uuids], tool_name="devonthink-batch-set-label")
        return {"ok": True, "updated": int(raw or len(cleaned_uuids)), "label": cleaned_label}
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_set_rating(record_uuid: str, rating: int) -> dict[str, Any]:
    try:
        cleaned_uuid = _validate_uuid(record_uuid, "record_uuid")
        cleaned_rating = _validate_range(rating, "rating", 0, 5)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = r'''
on run argv
    set recordUUID to item 1 of argv
    set ratingValue to item 2 of argv as integer
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        set rating of theRecord to ratingValue
    end tell
end run
'''
    try:
        _run_osascript(script, [cleaned_uuid, str(cleaned_rating)], tool_name="devonthink-set-rating")
        return {"ok": True, "record_uuid": cleaned_uuid, "rating": cleaned_rating}
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_batch_update_record_metadata(
    record_uuids: list[str],
    tags: list[str] | None = None,
    comment: str | None = None,
    comment_mode: str = "replace",
    merge_tags: bool = True,
    label: int | None = None,
    rating: int | None = None,
) -> dict[str, Any]:
    """Update tags/comment and optional label/rating for records in one AppleScript pass."""

    try:
        if not record_uuids:
            raise ValueError("record_uuids must contain at least one UUID.")
        cleaned_uuids = [_validate_uuid(value, "record_uuid") for value in record_uuids]
        cleaned_tags = _validate_tags(tags)
        cleaned_comment_mode = _validate_comment_mode(comment_mode)
        cleaned_label = _validate_range(label, "label", 0, 7) if label is not None else None
        cleaned_rating = _validate_range(rating, "rating", 0, 5) if rating is not None else None
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    apply_tags = tags is not None
    apply_comment = comment is not None
    if not any((apply_tags, apply_comment, cleaned_label is not None, cleaned_rating is not None)):
        return {
            "ok": False,
            "error": "Provide at least one metadata field: tags, comment, label, or rating.",
        }

    comment_text = "" if comment is None else str(comment)
    tags_joined = "||".join(cleaned_tags)

    script = _DEVONTHINK_JSON_HELPERS + r'''
on split_tags(s)
    if s is "" then return {}
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "||"
    set parts to text items of s
    set AppleScript's text item delimiters to oldDelims
    return parts
end split_tags

on normalized_tag_list(valueAny)
    if valueAny is missing value then return {}
    try
        if class of valueAny is text then
            if valueAny as text is "" then return {}
            return {valueAny as text}
        end if
    end try
    return valueAny
end normalized_tag_list

on tag_list_contains(tagList, candidate)
    repeat with existingTag in tagList
        if (existingTag as text) is candidate then return true
    end repeat
    return false
end tag_list_contains

on merge_tag_lists(existingTags, newTags)
    set mergedTags to my normalized_tag_list(existingTags)
    repeat with newTag in newTags
        set tagText to newTag as text
        if tagText is not "" and not my tag_list_contains(mergedTags, tagText) then
            set end of mergedTags to tagText
        end if
    end repeat
    return mergedTags
end merge_tag_lists

on run argv
    set tagsJoined to item 1 of argv
    set applyTagsText to item 2 of argv
    set mergeTagsText to item 3 of argv
    set commentText to item 4 of argv
    set applyCommentText to item 5 of argv
    set commentMode to item 6 of argv
    set labelText to item 7 of argv
    set ratingText to item 8 of argv

    set newTags to my split_tags(tagsJoined)
    set updatedCount to 0
    set output to "["

    tell application id "DNtp"
        repeat with i from 9 to count of argv
            set recordUUID to item i of argv
            set theRecord to get record with uuid recordUUID
            if theRecord is missing value then error "Record not found for uuid: " & recordUUID

            if applyTagsText is "true" then
                if mergeTagsText is "true" then
                    set tags of theRecord to my merge_tag_lists(tags of theRecord, newTags)
                else
                    set tags of theRecord to newTags
                end if
            end if

            if applyCommentText is "true" then
                if commentMode is "append" then
                    set oldComment to ""
                    try
                        if comment of theRecord is not missing value then set oldComment to comment of theRecord as text
                    end try
                    if oldComment is "" then
                        set comment of theRecord to commentText
                    else if commentText is "" then
                        set comment of theRecord to oldComment
                    else
                        set comment of theRecord to oldComment & linefeed & commentText
                    end if
                else if commentMode is "prepend" then
                    set oldComment to ""
                    try
                        if comment of theRecord is not missing value then set oldComment to comment of theRecord as text
                    end try
                    if oldComment is "" then
                        set comment of theRecord to commentText
                    else if commentText is "" then
                        set comment of theRecord to oldComment
                    else
                        set comment of theRecord to commentText & linefeed & oldComment
                    end if
                else
                    set comment of theRecord to commentText
                end if
            end if

            if labelText is not "" then
                set labelValue to labelText as integer
                set label of theRecord to labelValue
                if (label of theRecord as integer) is not labelValue then
                    error "DEVONthink did not apply label to record " & recordUUID & ". This may be a DEVONthink/API limitation for the record type."
                end if
            end if

            if ratingText is not "" then
                set ratingValue to ratingText as integer
                set rating of theRecord to ratingValue
                if (rating of theRecord as integer) is not ratingValue then
                    error "DEVONthink did not apply rating to record " & recordUUID & "."
                end if
            end if

            if updatedCount is greater than 0 then set output to output & ","
            set output to output & my record_json(theRecord)
            set updatedCount to updatedCount + 1
        end repeat
    end tell

    set output to output & "]"
    return output
end run
'''
    args = [
        tags_joined,
        "true" if apply_tags else "false",
        "true" if merge_tags else "false",
        comment_text,
        "true" if apply_comment else "false",
        cleaned_comment_mode,
        "" if cleaned_label is None else str(cleaned_label),
        "" if cleaned_rating is None else str(cleaned_rating),
        *cleaned_uuids,
    ]
    try:
        result = _run_json_script(
            script,
            args,
            tool_name="devonthink-batch-update-record-metadata",
            extra={
                "record_uuids": cleaned_uuids,
                "tags": cleaned_tags if apply_tags else None,
                "comment": comment_text if apply_comment else None,
                "comment_mode": cleaned_comment_mode,
                "merge_tags": merge_tags,
                "label": cleaned_label,
                "rating": cleaned_rating,
            },
        )
        records = _enrich_records(result.get("data") or [])
        return {
            "ok": True,
            "updated": len(records),
            "records": records,
            "applied": {
                "tags": apply_tags,
                "comment": apply_comment,
                "label": cleaned_label is not None,
                "rating": cleaned_rating is not None,
            },
            "observability": {
                "warnings": [
                    "DEVONthink labels are numeric color labels only. Use tags/comment for textual markers such as TODO."
                ]
            },
        }
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_duplicate_record(record_uuid: str, destination_group_uuid: str) -> dict[str, Any]:
    try:
        cleaned_record_uuid = _validate_uuid(record_uuid, "record_uuid")
        cleaned_destination_uuid = _validate_uuid(destination_group_uuid, "destination_group_uuid")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set recordUUID to item 1 of argv
    set destinationUUID to item 2 of argv
    tell application id "DNtp"
        set theRecord to get record with uuid recordUUID
        if theRecord is missing value then error "Record not found for uuid: " & recordUUID
        set destinationGroup to get record with uuid destinationUUID
        if destinationGroup is missing value then error "Destination group not found for uuid: " & destinationUUID
        set newRecord to duplicate record theRecord to destinationGroup
        return my record_json(newRecord)
    end tell
end run
'''
    try:
        result = _run_json_script(
            script,
            [cleaned_record_uuid, cleaned_destination_uuid],
            tool_name="devonthink-duplicate-record",
            extra={"record_uuid": cleaned_record_uuid, "destination_group_uuid": cleaned_destination_uuid},
        )
        data = result.get("data")
        return {
            "ok": True,
            "data": _enrich_record(data) if isinstance(data, dict) else data,
            "record_uuid": cleaned_record_uuid,
            "destination_group_uuid": cleaned_destination_uuid,
        }
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc)}


def devonthink_summarize_annotations(record_uuids: list[str], destination_group_uuid: str) -> dict[str, Any]:
    """Summarize PDF/internal annotations into a markdown record in a destination group."""

    started = time.perf_counter()
    try:
        if not record_uuids:
            raise ValueError("record_uuids must contain at least one UUID.")
        cleaned_uuids = [_validate_uuid(value, "record_uuid") for value in record_uuids]
        cleaned_destination_uuid = _validate_uuid(destination_group_uuid, "destination_group_uuid")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    script = _DEVONTHINK_JSON_HELPERS + r'''
on run argv
    set destinationUUID to item 1 of argv
    set theRecords to {}
    tell application id "DNtp"
        set destinationGroup to get record with uuid destinationUUID
        if destinationGroup is missing value then error "Destination group not found for uuid: " & destinationUUID
        repeat with i from 2 to count of argv
            set recordUUID to item i of argv
            set theRecord to get record with uuid recordUUID
            if theRecord is missing value then error "Record not found for uuid: " & recordUUID
            set end of theRecords to theRecord
        end repeat
        set summaryRecord to summarize annotations of records theRecords to markdown in destinationGroup
        if summaryRecord is missing value then return "null"
        return my record_json(summaryRecord)
    end tell
end run
'''
    try:
        result = _run_json_script(
            script,
            [cleaned_destination_uuid, *cleaned_uuids],
            tool_name="devonthink-summarize-annotations",
            extra={"record_uuids": cleaned_uuids, "destination_group_uuid": cleaned_destination_uuid},
        )
        data = result.get("data")
        if isinstance(data, dict):
            data = _enrich_record(data)
        observability: dict[str, Any] = {"duration_ms": int((time.perf_counter() - started) * 1000)}
        if data is None:
            observability["warnings"] = [
                "missing_value: DEVONthink returned no summary. The records may have attached annotation notes only and no PDF-internal highlights/comments/markup."
            ]
        return {
            "ok": True,
            "data": data,
            "record_uuids": cleaned_uuids,
            "destination_group_uuid": cleaned_destination_uuid,
            "observability": observability,
        }
    except AppleScriptExecutionError as exc:
        return {"ok": False, "error": str(exc), "observability": {"duration_ms": int((time.perf_counter() - started) * 1000)}}


def specialized_tool_catalog_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    entries.append(
        catalog_entry(
            name="devonthink-get-database-by-uuid",
            description=build_description(
                summary="Get one DEVONthink database by UUID and return structured metadata.",
                use_when="you already know the database UUID and need its id, name, or path before scoping later operations.",
                identifier_guidance="Accepts a database UUID. Prefer the database UUID over path lookups for stable automation.",
                safety_class="read_only",
                prefer_when="you need exact database identity; prefer devonthink-get-database-incoming-group or devonthink-search-records when your next step is folder-scoped retrieval.",
                example='{"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-get-database-by-uuid",
            overlap_family="devonthink-get",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-get-database-by-uuid.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["database_uuid"],
            preferred_identifier="database_uuid",
            identifier_guidance="Accepts a database UUID only.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need exact database metadata rather than a search scope helper.",
            example='{"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            tags=["devonthink", "specialized", "database"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-get-database-incoming-group",
            description=build_description(
                summary="Resolve a database UUID to its incoming group record so models can access the database root/Inbox scope directly.",
                use_when="you need the concrete group record that DEVONthink uses as the default search/creation scope inside a database.",
                identifier_guidance="Accepts a database UUID. Prefer the database UUID; this tool returns the incoming group UUID you can reuse in search, move, and traversal calls.",
                safety_class="read_only",
                prefer_when="you need explicit Inbox/root-group access; prefer devonthink-get-database-by-uuid when you only need database metadata.",
                example='{"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-get-database-incoming-group",
            overlap_family="devonthink-get",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-get-database-incoming-group.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["database_uuid"],
            preferred_identifier="database_uuid",
            identifier_guidance="Accepts a database UUID and returns the incoming-group record object as structured metadata.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need the actual searchable/writable group scope behind a database UUID.",
            example='{"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            tags=["devonthink", "specialized", "database", "inbox"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-get-record-by-uuid",
            description=build_description(
                summary="Get one DEVONthink record by UUID and return structured metadata.",
                use_when="you already have a stable record identifier and need to inspect the record before follow-up actions.",
                identifier_guidance="Accepts a record UUID, optionally with a database UUID for scoping. Prefer the record UUID over title/path lookups.",
                safety_class="read_only",
                prefer_when="you need exact record lookup; prefer devonthink-search-records when you only have query text or metadata clues.",
                example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-get-record-by-uuid",
            overlap_family="devonthink-get",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-get-record-by-uuid.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid", "database_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts a record UUID and optional database UUID scope.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need one exact record by UUID.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}',
            tags=["devonthink", "specialized", "record"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-read-record-text",
            description=build_description(
                summary="Read the DEVONthink plain text/transcription content for one record.",
                use_when="you need the current text content of a DEVONthink record, including txt/markdown/RTF/PDF records where DEVONthink exposes searchable text.",
                identifier_guidance="Accepts a record UUID and max_chars limit.",
                safety_class="read_only",
                prefer_when="the user asks to open/read/show contents or extract text and you already have or can find a record UUID.",
                degradation_contract="Returns DEVONthink's record-level plain text property. Exact PDF page ranges may not be available through DEVONthink AppleScript and should be treated as a degraded page-specific request.",
                example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","max_chars":20000}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-read-record-text",
            overlap_family="devonthink-get",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-read-record-text.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts a record UUID and max_chars limit.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need record text content without leaving the MCP for filesystem extraction first.",
            degradation_contract="Uses DEVONthink plain text/transcription; exact PDF page slicing is not guaranteed.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","max_chars":20000}',
            tags=["devonthink", "specialized", "record", "text", "content"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-list-group-children",
            description=build_description(
                summary="List the direct children of a DEVONthink group as structured record objects.",
                use_when="you need to inspect folder hierarchy or enumerate a group's immediate contents without running the heavier graph traversal tools.",
                identifier_guidance="Accepts a group UUID and optional limit. Prefer a group UUID returned by search, incoming-group lookup, or traversal tools.",
                safety_class="read_only",
                prefer_when="you need direct child listing only; prefer devonthink-link-traverse-folder for recursive graph traversal, snapshots, or smart-group handling.",
                example='{"group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","limit":25}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-list-group-children",
            overlap_family="devonthink-get",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-list-group-children.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["group_uuid"],
            preferred_identifier="group_uuid",
            identifier_guidance="Accepts a group UUID only.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need only the immediate folder children and not a recursive graph walk.",
            example='{"group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B","limit":25}',
            tags=["devonthink", "specialized", "group", "hierarchy"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-search-records",
            description=build_description(
                summary="Search DEVONthink records and return structured record objects rather than raw AppleScript results.",
                use_when="you need query-driven lookup with safer scoping than the raw dictionary search command.",
                identifier_guidance="Accepts a query string plus an optional group UUID or database UUID in database_uuid. Prefer a group UUID for exact folder scope; when you pass a database UUID this tool auto-resolves it to the database's incoming group.",
                safety_class="read_only",
                prefer_when="you need structured search results and automatic database-root scoping; prefer raw devonthink-search only when you need the dictionary command exactly.",
                degradation_contract="When database_uuid points at a database, the tool degrades intentionally by resolving it to the incoming group instead of throwing the raw DEVONthink Invalid argument (-50) failure.",
                example='{"query":"tag:inbox","limit":25,"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-search-records",
            overlap_family="devonthink-search",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-search-records.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["database_uuid", "group_uuid"],
            preferred_identifier="group_uuid",
            identifier_guidance="Accepts a query string plus optional group UUID or database UUID scope; database UUID is auto-resolved to the incoming group.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you want structured results and safe scoping behavior.",
            degradation_contract="Database UUID scope auto-resolves to the incoming group to avoid raw search errors.",
            example='{"query":"tag:inbox","limit":25,"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            tags=["devonthink", "specialized", "search"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-filter-records",
            description=build_description(
                summary="Search or enumerate DEVONthink records, then filter by reliable structured properties.",
                use_when="you need content plus constraints such as PDF/RTF/PNG extension, exact tags, creation/modified/added date ranges, dimensions, or file size. Use this instead of brittle kind:, label:, width:, created:, or filename-only search guesses.",
                identifier_guidance="Accepts optional query/query_terms_any, record_types, file_extensions, name/filename/path contains filters, exact tags, date ranges as YYYY-MM-DD, within-days filters, dimension/size ranges, optional database UUID, and scan/limit controls.",
                safety_class="read_only",
                prefer_when="the user asks for files/documents/images/PDFs/RTFs with metadata constraints or asks for path and size with search results.",
                degradation_contract="Defaults to DEVONthink full-text search when query text is provided, then applies Python-side structured filtering. For text/rich-text filters or content_mode=plain_text it enumerates records and verifies the record plain text property.",
                example='{"query":"financial statement","file_extensions":["pdf"],"limit":25}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-filter-records",
            overlap_family="devonthink-search",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-filter-records.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["database_uuid", "group_uuid"],
            preferred_identifier="database_uuid",
            identifier_guidance="Accepts search text plus optional structured filters for type, extension, name/filename/path, tag, dates, dimensions, and size.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need reliable filtered search results with path, size, dates, dimensions, and tags.",
            degradation_contract="Avoids localized kind/date/tag predicates and filters the structured record objects directly.",
            example='{"query":"invoice","record_types":["pdf"],"file_extensions":["pdf"],"limit":50}',
            tags=["devonthink", "specialized", "search", "filter", "metadata"],
            invocation_pitfalls=[
                "Use file_extensions=['pdf'] or record_types=['pdf'] instead of query='kind:PDF'.",
                "Use filename_contains='todo.txt' or name_contains='Meeting Notes' for filename/title lookup instead of content search.",
                "Use tags=['archived'] instead of label:archived; DEVONthink label is a color number.",
                "Use created_from/created_to ISO dates instead of free-form created: search predicates.",
                "Use content_mode='plain_text' for rich text phrase searches when DEVONthink full-text search misses them.",
            ],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-search-media-records",
            description=build_description(
                summary="Find DEVONthink audio/video records using the native multimedia record type and return file metadata.",
                use_when="you need actual media files and DEVONthink kind:Movie/kind:Audio search is returning documents or other false positives.",
                identifier_guidance="Accepts media_kind video, audio, multimedia, or any; optional database UUID scope; and a limit.",
                safety_class="read_only",
                prefer_when="you are looking for real .mp4/.mov/.mkv/.mp3/etc. records and need duration, size, path, MIME type, and record type metadata.",
                degradation_contract="Uses record type=multimedia rather than localized kind metadata. For media_kind=video/audio it scans up to 200 multimedia records then filters by MIME type, extension, and multimedia kind hints.",
                example='{"media_kind":"video","limit":25,"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-search-media-records",
            overlap_family="devonthink-search",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-search-media-records.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["database_uuid"],
            preferred_identifier="database_uuid",
            identifier_guidance="Accepts media_kind video, audio, multimedia, or any and an optional database UUID.",
            safety_class="read_only",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need true audio/video records instead of localized kind search results.",
            degradation_contract="Avoids kind:Movie false positives by using record type=multimedia and structured file metadata.",
            example='{"media_kind":"video","limit":25}',
            tags=["devonthink", "specialized", "search", "media", "video", "audio"],
            invocation_pitfalls=[
                "Do not use kind:Movie as proof that a record is a movie; DEVONthink warns that kind is localized and not reliable for type checks.",
                "Use the returned type, MIME type, extension, duration, and size fields to validate results.",
            ],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-create-record",
            description=build_description(
                summary="Create a DEVONthink record with validated record-type normalization and optional target group placement.",
                use_when="you need simple non-query record creation without invoking the raw create-record-with dictionary command directly. Use devonthink-create-smart-group for saved searches, and use devonthink-create-rtf for RTF content because it works around DEVONthink's empty-RTF missing-value behavior.",
                identifier_guidance="Accepts a record name, a normalized record type token or common alias, and an optional group UUID. Prefer a group UUID when the record must land in a specific folder.",
                safety_class="writes_data",
                prefer_when="you need a plain static record or group; prefer create-rtf for RTF records and create-smart-group for query-backed smart groups.",
                example='{"name":"MCP Test Note","record_type":"plain text","group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            ),
            group="devonthink.native",
            tier="advanced",
            status="active",
            canonical_tool="devonthink-create-record",
            overlap_family="devonthink-create",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/advanced/devonthink-create-record.json",
            executable="osascript",
            priority=60,
            default_exposed=False,
            accepted_identifiers=["group_uuid"],
            preferred_identifier="group_uuid",
            identifier_guidance="Accepts a name, record type token or alias, and optional group UUID destination.",
            safety_class="writes_data",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need a plain static record or group; prefer create-rtf for RTF records and create-smart-group for query-backed smart groups.",
            example='{"name":"MCP Test Note","record_type":"plain text","group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            tags=["devonthink", "specialized", "create"],
            invocation_pitfalls=[
                "Use plain text -> txt, rich text -> rtf, image -> picture, pdf -> pdf document.",
                "For RTF records, prefer devonthink-create-rtf because it passes rich text at creation time.",
                "For smart groups, prefer devonthink-create-smart-group because plain groups do not have query semantics.",
            ],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-set-label",
            description=build_description(
                summary="Set the DEVONthink color label on one record.",
                use_when="you need to change only a record's color label without editing content or metadata fields.",
                identifier_guidance="Accepts a record UUID and label integer 0-7.",
                safety_class="writes_data",
                prefer_when="you want a small validated wrapper that verifies DEVONthink actually applied the label instead of silently no-oping.",
                example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","label":1}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-set-label",
            overlap_family="devonthink-metadata",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-set-label.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts a record UUID and label integer 0-7.",
            safety_class="writes_data",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need to set only the record label and detect DEVONthink/API no-op behavior.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","label":1}',
            tags=["devonthink", "specialized", "metadata", "label"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-batch-set-label",
            description=build_description(
                summary="Set the same DEVONthink color label on multiple records in one AppleScript pass.",
                use_when="you need to label a batch of records without issuing one Apple Event per record.",
                identifier_guidance="Accepts a list of record UUIDs and label integer 0-7.",
                safety_class="writes_data",
                prefer_when="you are applying one label to many records and want bulk execution with no-op detection.",
                example='{"record_uuids":["5038E0B0-2134-4CDA-B443-6558CE283BCC"],"label":4}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-batch-set-label",
            overlap_family="devonthink-metadata",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-batch-set-label.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts record UUIDs and a label integer 0-7.",
            safety_class="writes_data",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need batch label updates in one tool call and want failed label application to surface clearly.",
            example='{"record_uuids":["5038E0B0-2134-4CDA-B443-6558CE283BCC"],"label":4}',
            tags=["devonthink", "specialized", "metadata", "label", "batch"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-batch-update-record-metadata",
            description=build_description(
                summary="Batch-update textual record metadata such as tags and comments, with optional color label/rating.",
                use_when="you need to mark records with textual intent like TODO, review, or project state; use tags/comment for text and label only for DEVONthink's numeric color label.",
                identifier_guidance="Accepts record UUIDs, optional tags, optional comment, comment_mode replace/append/prepend, merge_tags, optional label 0-7, and optional rating 0-5.",
                safety_class="writes_data",
                prefer_when="you want to update several records in one AppleScript pass without confusing text tags with DEVONthink color labels.",
                degradation_contract="Verifies label/rating when requested and returns updated structured records including tags, comment, label, rating, path, size, and media metadata.",
                example='{"record_uuids":["5038E0B0-2134-4CDA-B443-6558CE283BCC"],"tags":["TODO"],"comment":"TODO","comment_mode":"append"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-batch-update-record-metadata",
            overlap_family="devonthink-metadata",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-batch-update-record-metadata.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts record UUIDs plus tags/comment and optional label/rating.",
            safety_class="writes_data",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need textual markers such as TODO; use label tools only for color labels.",
            degradation_contract="Text markers are applied as tags/comments; labels remain numeric color labels.",
            example='{"record_uuids":["5038E0B0-2134-4CDA-B443-6558CE283BCC"],"tags":["TODO"],"comment":"TODO","comment_mode":"append"}',
            tags=["devonthink", "specialized", "metadata", "tags", "comment", "batch"],
            invocation_pitfalls=[
                "DEVONthink label is color-only: 0=None, 1=Red, 2=Orange, 3=Yellow, 4=Green, 5=Blue, 6=Purple, 7=Teal.",
                "For textual state like TODO, pass tags/comment instead of label.",
            ],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-set-rating",
            description=build_description(
                summary="Set the star rating on one DEVONthink record.",
                use_when="you need to change only a record's rating without editing content.",
                identifier_guidance="Accepts a record UUID and rating integer 0-5.",
                safety_class="writes_data",
                prefer_when="you want a validated wrapper for the rating property.",
                example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","rating":4}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-set-rating",
            overlap_family="devonthink-metadata",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-set-rating.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts a record UUID and rating integer 0-5.",
            safety_class="writes_data",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need to set only the record rating.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","rating":4}',
            tags=["devonthink", "specialized", "metadata", "rating"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-duplicate-record",
            description=build_description(
                summary="Duplicate one DEVONthink record into a destination group and return the new record.",
                use_when="you need an independent copy of a record in another group.",
                identifier_guidance="Accepts a source record UUID and destination group UUID.",
                safety_class="writes_data",
                prefer_when="you want an independent duplicate; use replicate when copies should stay linked.",
                example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","destination_group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-duplicate-record",
            overlap_family="devonthink-duplicate",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-duplicate-record.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid", "group_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts a source record UUID and destination group UUID.",
            safety_class="writes_data",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need a standalone copy and structured response metadata.",
            example='{"record_uuid":"5038E0B0-2134-4CDA-B443-6558CE283BCC","destination_group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            tags=["devonthink", "specialized", "duplicate"],
        )
    )
    entries.append(
        catalog_entry(
            name="devonthink-summarize-annotations",
            description=build_description(
                summary="Summarize DEVONthink PDF/internal annotations from records into a markdown summary record.",
                use_when="you need DEVONthink's built-in summary of PDF-internal highlights, comments, or markup. This is separate from attached annotation notes/annotation-note records.",
                identifier_guidance="Accepts one or more source record UUIDs and a destination group UUID for the generated summary record.",
                safety_class="writes_data",
                prefer_when="you want internal PDF annotation markup summarized; use devonthink-read-annotation-note for the attached annotation note stored via the record annotation property.",
                degradation_contract="Returns data=null with a missing_value warning when DEVONthink returns missing value, commonly because records have attached annotation notes only and no internal PDF markup.",
                example='{"record_uuids":["5038E0B0-2134-4CDA-B443-6558CE283BCC"],"destination_group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            ),
            group="devonthink.native",
            tier="canonical",
            status="active",
            canonical_tool="devonthink-summarize-annotations",
            overlap_family="devonthink-annotation-summary",
            source_path="app/tools/devonthink_tools.py",
            catalog_path="catalog-runtime/tools/devonthink.native/canonical/devonthink-summarize-annotations.json",
            executable="osascript",
            priority=100,
            default_exposed=True,
            accepted_identifiers=["record_uuid", "group_uuid"],
            preferred_identifier="record_uuid",
            identifier_guidance="Accepts source record UUIDs and a destination group UUID.",
            safety_class="writes_data",
            profile_availability=["minimal", "canonical", "full"],
            prefer_when="you need PDF/internal annotation summaries, not attached annotation-note content.",
            degradation_contract="Missing value is expected for records with attached annotation notes only.",
            example='{"record_uuids":["5038E0B0-2134-4CDA-B443-6558CE283BCC"],"destination_group_uuid":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}',
            tags=["devonthink", "specialized", "annotations", "pdf"],
        )
    )
    return entries


def register_devonthink_tools(mcp: Any) -> None:
    """Register DEVONthink-backed MCP tools."""

    catalog = {entry["name"]: entry for entry in specialized_tool_catalog_entries()}

    @mcp.tool(
        name="devonthink-get-database-by-uuid",
        description=catalog["devonthink-get-database-by-uuid"]["description"],
    )
    def _devonthink_get_database_by_uuid(database_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-get-database-by-uuid", devonthink_get_database_by_uuid, database_uuid)

    @mcp.tool(
        name="devonthink-get-database-incoming-group",
        description=catalog["devonthink-get-database-incoming-group"]["description"],
    )
    def _devonthink_get_database_incoming_group(database_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-get-database-incoming-group", devonthink_get_database_incoming_group, database_uuid)

    @mcp.tool(
        name="devonthink-get-record-by-uuid",
        description=catalog["devonthink-get-record-by-uuid"]["description"],
    )
    def _devonthink_get_record_by_uuid(record_uuid: str, database_uuid: str | None = None) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-get-record-by-uuid",
            devonthink_get_record_by_uuid,
            record_uuid=record_uuid,
            database_uuid=database_uuid,
        )

    @mcp.tool(
        name="devonthink-read-record-text",
        description=catalog["devonthink-read-record-text"]["description"],
    )
    def _devonthink_read_record_text(record_uuid: str, max_chars: int = 20000) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-read-record-text",
            devonthink_read_record_text,
            record_uuid=record_uuid,
            max_chars=max_chars,
        )

    @mcp.tool(
        name="devonthink-list-group-children",
        description=catalog["devonthink-list-group-children"]["description"],
    )
    def _devonthink_list_group_children(
        group_uuid: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-list-group-children",
            devonthink_list_group_children,
            group_uuid=group_uuid,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(
        name="devonthink-search-records",
        description=catalog["devonthink-search-records"]["description"],
    )
    def _devonthink_search_records(
        query: str,
        limit: int = 25,
        database_uuid: str | None = None,
        comparison: str | None = None,
        exclude_subgroups: bool = False,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-search-records",
            devonthink_search_records,
            query=query,
            limit=limit,
            database_uuid=database_uuid,
            comparison=comparison,
            exclude_subgroups=exclude_subgroups,
        )

    @mcp.tool(
        name="devonthink-filter-records",
        description=catalog["devonthink-filter-records"]["description"],
    )
    def _devonthink_filter_records(
        query: str | None = None,
        query_terms_any: list[str] | None = None,
        record_types: list[str] | None = None,
        file_extensions: list[str] | None = None,
        name_contains: str | None = None,
        filename_contains: str | None = None,
        path_contains: str | None = None,
        tags: list[str] | None = None,
        tag_match: str = "all",
        created_from: str | None = None,
        created_to: str | None = None,
        modified_from: str | None = None,
        modified_to: str | None = None,
        added_from: str | None = None,
        added_to: str | None = None,
        created_within_days: int | None = None,
        modified_within_days: int | None = None,
        added_within_days: int | None = None,
        min_width: int | None = None,
        max_width: int | None = None,
        min_height: int | None = None,
        max_height: int | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        content_mode: str = "auto",
        dedupe_by: str = "uuid",
        limit: int = 50,
        scan_limit: int = 2000,
        database_uuid: str | None = None,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-filter-records",
            devonthink_filter_records,
            query=query,
            query_terms_any=query_terms_any,
            record_types=record_types,
            file_extensions=file_extensions,
            name_contains=name_contains,
            filename_contains=filename_contains,
            path_contains=path_contains,
            tags=tags,
            tag_match=tag_match,
            created_from=created_from,
            created_to=created_to,
            modified_from=modified_from,
            modified_to=modified_to,
            added_from=added_from,
            added_to=added_to,
            created_within_days=created_within_days,
            modified_within_days=modified_within_days,
            added_within_days=added_within_days,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            min_size=min_size,
            max_size=max_size,
            content_mode=content_mode,
            dedupe_by=dedupe_by,
            limit=limit,
            scan_limit=scan_limit,
            database_uuid=database_uuid,
        )

    @mcp.tool(
        name="devonthink-search-media-records",
        description=catalog["devonthink-search-media-records"]["description"],
    )
    def _devonthink_search_media_records(
        media_kind: str = "video",
        limit: int = 25,
        database_uuid: str | None = None,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-search-media-records",
            devonthink_search_media_records,
            media_kind=media_kind,
            limit=limit,
            database_uuid=database_uuid,
        )

    @mcp.tool(
        name="devonthink-set-custom-metadata",
        description=(
            "Set a single custom metadata key/value on a DEVONthink record. "
            "Typed wrapper around the raw `add custom meta data` command (direct=value, for=key, to=record) "
            "that returns the round-tripped value so you can verify the write. "
            "Accepts string, int, float, or bool values. Example: "
            '{"record_uuid":"5038E0B0-...","key":"years_of_experience","value":12}'
        ),
    )
    def _devonthink_set_custom_metadata(
        record_uuid: str,
        key: str,
        value: Any,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-set-custom-metadata",
            devonthink_set_custom_metadata,
            record_uuid=record_uuid,
            key=key,
            value=value,
        )

    @mcp.tool(
        name="devonthink-create-record",
        description=catalog["devonthink-create-record"]["description"],
    )
    def _devonthink_create_record(
        name: str,
        record_type: str,
        group_uuid: str | None = None,
        content: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-create-record",
            devonthink_create_record,
            name=name,
            record_type=record_type,
            group_uuid=group_uuid,
            content=content,
            url=url,
        )

    @mcp.tool(
        name="devonthink-set-label",
        description=catalog["devonthink-set-label"]["description"],
    )
    def _devonthink_set_label(record_uuid: str, label: int) -> dict[str, Any]:
        return wrap_tool_call("devonthink-set-label", devonthink_set_label, record_uuid=record_uuid, label=label)

    @mcp.tool(
        name="devonthink-batch-set-label",
        description=catalog["devonthink-batch-set-label"]["description"],
    )
    def _devonthink_batch_set_label(record_uuids: list[str], label: int) -> dict[str, Any]:
        return wrap_tool_call("devonthink-batch-set-label", devonthink_batch_set_label, record_uuids=record_uuids, label=label)

    @mcp.tool(
        name="devonthink-batch-update-record-metadata",
        description=catalog["devonthink-batch-update-record-metadata"]["description"],
    )
    def _devonthink_batch_update_record_metadata(
        record_uuids: list[str],
        tags: list[str] | None = None,
        comment: str | None = None,
        comment_mode: str = "replace",
        merge_tags: bool = True,
        label: int | None = None,
        rating: int | None = None,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-batch-update-record-metadata",
            devonthink_batch_update_record_metadata,
            record_uuids=record_uuids,
            tags=tags,
            comment=comment,
            comment_mode=comment_mode,
            merge_tags=merge_tags,
            label=label,
            rating=rating,
        )

    @mcp.tool(
        name="devonthink-set-rating",
        description=catalog["devonthink-set-rating"]["description"],
    )
    def _devonthink_set_rating(record_uuid: str, rating: int) -> dict[str, Any]:
        return wrap_tool_call("devonthink-set-rating", devonthink_set_rating, record_uuid=record_uuid, rating=rating)

    @mcp.tool(
        name="devonthink-duplicate-record",
        description=catalog["devonthink-duplicate-record"]["description"],
    )
    def _devonthink_duplicate_record(record_uuid: str, destination_group_uuid: str) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-duplicate-record",
            devonthink_duplicate_record,
            record_uuid=record_uuid,
            destination_group_uuid=destination_group_uuid,
        )

    @mcp.tool(
        name="devonthink-summarize-annotations",
        description=catalog["devonthink-summarize-annotations"]["description"],
    )
    def _devonthink_summarize_annotations(record_uuids: list[str], destination_group_uuid: str) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-summarize-annotations",
            devonthink_summarize_annotations,
            record_uuids=record_uuids,
            destination_group_uuid=destination_group_uuid,
        )
