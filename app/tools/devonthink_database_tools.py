"""Database lifecycle tools for DEVONthink MCP."""

from __future__ import annotations

import time
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


def devonthink_list_databases() -> dict[str, Any]:
    """Return all open DEVONthink databases."""
    started = time.perf_counter()
    script = r'''
using terms from application "DEVONthink"
tell application id "DNtp"
    set rows to {}
    repeat with db in databases
        set dbUUID to ""
        set dbName to ""
        set dbPath to ""
        try
            set dbUUID to (uuid of db) as text
        end try
        try
            set dbName to (name of db) as text
        end try
        try
            set dbPath to (path of db) as text
        end try
        set end of rows to dbUUID & (character id 9) & dbName & (character id 9) & dbPath
    end repeat
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to character id 10
    set output to rows as text
    set AppleScript's text item delimiters to oldDelims
    return output
end tell
end using terms from
'''
    try:
        raw = run_applescript(script, tool_name="devonthink-list-databases")
        return {
            "ok": True,
            "databases": _parse_rows(raw, ["uuid", "name", "path"]),
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def devonthink_open_database(path: str) -> dict[str, Any]:
    """Open an existing DEVONthink database by POSIX path."""
    started = time.perf_counter()
    try:
        cleaned_path = _validate_nonempty(path, "path")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set dbPath to item 1 of argv
    tell application id "DNtp"
        set openedDB to open database dbPath
        return (uuid of openedDB) as text
    end tell
end run
'''
    try:
        uuid = run_applescript(script, [cleaned_path], tool_name="devonthink-open-database").strip()
        return {"ok": True, "uuid": uuid, "observability": {"duration_ms": _duration_ms(started)}}
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def devonthink_close_database(database_uuid: str) -> dict[str, Any]:
    """Close an open DEVONthink database by UUID."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(database_uuid, "database_uuid")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set databaseUUID to item 1 of argv
    tell application id "DNtp"
        set theDB to get database with uuid databaseUUID
        if theDB is missing value then error "Database not found for uuid: " & databaseUUID
        close theDB
    end tell
end run
'''
    try:
        run_applescript(script, [cleaned_uuid], tool_name="devonthink-close-database")
        return {"ok": True, "database_uuid": cleaned_uuid, "observability": {"duration_ms": _duration_ms(started)}}
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def devonthink_sync_database(database_uuid: str) -> dict[str, Any]:
    """Synchronize a DEVONthink database by UUID."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(database_uuid, "database_uuid")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set databaseUUID to item 1 of argv
    tell application id "DNtp"
        set theDB to get database with uuid databaseUUID
        if theDB is missing value then error "Database not found for uuid: " & databaseUUID
        return (synchronize database theDB) as text
    end tell
end run
'''
    try:
        result = run_applescript(script, [cleaned_uuid], tool_name="devonthink-sync-database")
        return {
            "ok": True,
            "database_uuid": cleaned_uuid,
            "result": result,
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def devonthink_verify_database(database_uuid: str) -> dict[str, Any]:
    """Run DEVONthink's database verification by UUID."""
    started = time.perf_counter()
    try:
        cleaned_uuid = _validate_nonempty(database_uuid, "database_uuid")
    except ValueError as exc:
        return _error(exc, started)

    script = r'''
on run argv
    set databaseUUID to item 1 of argv
    tell application id "DNtp"
        set theDB to get database with uuid databaseUUID
        if theDB is missing value then error "Database not found for uuid: " & databaseUUID
        return (verify database theDB) as text
    end tell
end run
'''
    try:
        result = run_applescript(script, [cleaned_uuid], tool_name="devonthink-verify-database")
        return {
            "ok": True,
            "database_uuid": cleaned_uuid,
            "result": result,
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except AppleScriptExecutionError as exc:
        return _error(exc, started)


def _database_catalog_entry(
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
    tier: str = "canonical",
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
        overlap_family="devonthink-database",
        source_path="app/tools/devonthink_database_tools.py",
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
        tags=["devonthink", "database", "lifecycle"],
    )


def database_tool_catalog_entries() -> list[dict[str, Any]]:
    return [
        _database_catalog_entry(
            name="devonthink-list-databases",
            summary="List all open DEVONthink databases with UUID, name, and path.",
            use_when="you need to discover open database UUIDs before database-scoped operations.",
            identifier_guidance="Takes no identifiers and returns database UUIDs for later calls.",
            safety_class="read_only",
            prefer_when="the database UUID is unknown; prefer get-database-by-uuid when you already have the UUID.",
            example="{}",
            accepted_identifiers=[],
            preferred_identifier=None,
        ),
        _database_catalog_entry(
            name="devonthink-open-database",
            summary="Open an existing DEVONthink database by POSIX path.",
            use_when="the target database is not currently open and you know its .dtBase2 path.",
            identifier_guidance="Accepts an absolute POSIX path to a DEVONthink database package.",
            safety_class="writes_data",
            prefer_when="you need to make a database available before scoped search or record operations.",
            example='{"path":"~/Databases/Research.dtBase2"}',
            accepted_identifiers=["posix_path"],
            preferred_identifier="posix_path",
            tier="advanced",
        ),
        _database_catalog_entry(
            name="devonthink-close-database",
            summary="Close an open DEVONthink database by UUID without deleting it.",
            use_when="you need to cleanly close a database after batch automation.",
            identifier_guidance="Accepts a database UUID returned by list-databases or get-database-by-uuid.",
            safety_class="writes_data",
            prefer_when="you are automating database lifecycle and want a non-UI close operation.",
            example='{"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            accepted_identifiers=["database_uuid"],
            preferred_identifier="database_uuid",
            tier="advanced",
        ),
        _database_catalog_entry(
            name="devonthink-sync-database",
            summary="Trigger DEVONthink synchronization for a database by UUID.",
            use_when="a database has configured sync locations and you need to start sync from automation.",
            identifier_guidance="Accepts a database UUID.",
            safety_class="writes_data",
            prefer_when="you need database sync rather than filesystem record synchronization.",
            example='{"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            accepted_identifiers=["database_uuid"],
            preferred_identifier="database_uuid",
            tier="advanced",
        ),
        _database_catalog_entry(
            name="devonthink-verify-database",
            summary="Run DEVONthink's built-in verification for a database.",
            use_when="you need a read-only health check for an open database.",
            identifier_guidance="Accepts a database UUID.",
            safety_class="read_only",
            prefer_when="you want DEVONthink's database verification result before maintenance or backup work.",
            example='{"database_uuid":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}',
            accepted_identifiers=["database_uuid"],
            preferred_identifier="database_uuid",
        ),
    ]


def register_devonthink_database_tools(mcp: Any) -> None:
    catalog = {entry["name"]: entry for entry in database_tool_catalog_entries()}

    @mcp.tool(name="devonthink-list-databases", description=catalog["devonthink-list-databases"]["description"])
    def _devonthink_list_databases() -> dict[str, Any]:
        return wrap_tool_call("devonthink-list-databases", devonthink_list_databases)

    @mcp.tool(name="devonthink-open-database", description=catalog["devonthink-open-database"]["description"])
    def _devonthink_open_database(path: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-open-database", devonthink_open_database, path=path)

    @mcp.tool(name="devonthink-close-database", description=catalog["devonthink-close-database"]["description"])
    def _devonthink_close_database(database_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-close-database", devonthink_close_database, database_uuid=database_uuid)

    @mcp.tool(name="devonthink-sync-database", description=catalog["devonthink-sync-database"]["description"])
    def _devonthink_sync_database(database_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-sync-database", devonthink_sync_database, database_uuid=database_uuid)

    @mcp.tool(name="devonthink-verify-database", description=catalog["devonthink-verify-database"]["description"])
    def _devonthink_verify_database(database_uuid: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-verify-database", devonthink_verify_database, database_uuid=database_uuid)
