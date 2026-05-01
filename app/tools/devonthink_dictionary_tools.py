"""Auto-generated DEVONthink dictionary tool registration from sdef."""

from __future__ import annotations

import re
import subprocess
import os
import textwrap
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tools.applescript_counter import record_applescript_call
from app.tools.tool_catalog import build_description, catalog_entry
from app.tools.telemetry import wrap_tool_call


IMPLEMENTED_SPECIALIZED_TOOLS = {
    "devonthink-get-database-by-uuid",
    "devonthink-get-record-by-uuid",
    "devonthink-search-records",
    "devonthink-create-record",
}

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
READ_ONLY_PREFIXES = ("get", "search", "lookup", "exists", "classify", "compare", "extract", "count", "check")
DESTRUCTIVE_PREFIXES = ("delete", "move", "restore", "update", "synchronize", "optimize", "verify")
UI_PREFIXES = ("display", "show", "scroll", "load workspace", "save workspace", "open", "close", "quit")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    label: str
    optional: bool
    description: str
    type_name: str | None


@dataclass(frozen=True)
class CommandSpec:
    suite_name: str
    command_name: str
    command_description: str
    direct_parameter: ParameterSpec | None
    parameters: tuple[ParameterSpec, ...]
    result_description: str | None
    tool_name: str
    tier: str


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def _normalize_tool_name(command_name: str) -> str:
    return f"devonthink-{_slugify(command_name)}"


def _extract_commands() -> list[CommandSpec]:
    specs_path = Path(__file__).resolve().parent.parent / "data" / "devonthink_command_specs.json"
    raw = json.loads(specs_path.read_text())
    specs: list[CommandSpec] = []
    for item in raw:
        tool_name = _normalize_tool_name(item["command_name"])
        if tool_name in IMPLEMENTED_SPECIALIZED_TOOLS:
            continue
        direct = item.get("direct_parameter")
        direct_spec = None
        if direct:
            direct_spec = ParameterSpec(
                name=direct.get("name", "direct"),
                label=direct.get("label", ""),
                optional=bool(direct.get("optional", False)),
                description=direct.get("description", ""),
                type_name=direct.get("type_name"),
            )
        params = tuple(
            ParameterSpec(
                name=p.get("name", ""),
                label=p.get("label", p.get("name", "")),
                optional=bool(p.get("optional", False)),
                description=p.get("description", ""),
                type_name=p.get("type_name"),
            )
            for p in item.get("parameters", [])
        )
        specs.append(
            CommandSpec(
                suite_name=item.get("suite_name", ""),
                command_name=item.get("command_name", ""),
                command_description=item.get("command_description", ""),
                direct_parameter=direct_spec,
                parameters=params,
                result_description=item.get("result_description"),
                tool_name=tool_name,
                tier=item.get("tier", "advanced"),
            )
        )
    return specs


def get_dictionary_command_specs() -> list[CommandSpec]:
    return _extract_commands()


def _looks_like_record_ref(value: str) -> bool:
    value = _normalize_record_ref(value)
    return bool(UUID_RE.fullmatch(value))


def _normalize_record_ref(value: str) -> str:
    value = value.strip()
    if value.startswith("x-devonthink-item://"):
        return value.removeprefix("x-devonthink-item://")
    return value


def _as_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '" & quote & "') + '"'


def _record_uuid_specifier(value: str) -> str:
    # Wrap in a helper that surfaces a clean "Record not found for uuid: X"
    # error instead of letting AppleScript coerce a `missing value` into a
    # cryptic -1700 type error downstream (e.g. `delete record missing value`).
    return f"(my require_record_with_uuid({_as_quote(_normalize_record_ref(value))}))"


def _database_uuid_specifier(value: str) -> str:
    return f"(my require_database_with_uuid({_as_quote(_normalize_record_ref(value))}))"


def _search_scope_uuid_specifier(value: str) -> str:
    return f"(my record_or_database_incoming_group_with_uuid({_as_quote(_normalize_record_ref(value))}))"


def _record_label(key: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        return key
    return f"|{key}|"


def _looks_like_database_target(param_name: str, description: str, type_name: str | None) -> bool:
    lower_name = param_name.lower().strip()
    lower_desc = (description or "").lower()
    lower_type = (type_name or "").lower()

    if lower_name == "database":
        return True

    if lower_type == "specifier" and "database" in lower_desc:
        ambiguous_tokens = ("record", "group")
        if not any(token in lower_desc for token in ambiguous_tokens):
            return True

    return False


def _looks_like_record_target(param_name: str, description: str) -> bool:
    lower_name = param_name.lower().strip()
    lower_desc = (description or "").lower()

    likely_record_fields = {
        "record",
        "to",
        "from",
        "in",
        "of",
        "for",
        "version",
        "at",
    }
    if lower_name in likely_record_fields:
        return True

    record_tokens = ("record", "group", "item", "version")
    return any(token in lower_desc for token in record_tokens)


def _coerce_value_for_param(param_name: str, description: str, value: Any, *, tool_name: str = "") -> Any:
    if not isinstance(value, str):
        return value

    if not _looks_like_record_ref(value):
        return value

    if tool_name == "devonthink-search" and param_name.lower().strip() == "in":
        return {"__specifier__": _search_scope_uuid_specifier(value)}

    if _looks_like_database_target(param_name, description, None):
        return {"__specifier__": _database_uuid_specifier(value)}

    if _looks_like_record_target(param_name, description):
        return {"__specifier__": _record_uuid_specifier(value)}

    return value


def _coerce_direct_value(spec: CommandSpec, value: Any) -> Any:
    if spec.direct_parameter is None or not isinstance(value, str):
        return value
    if not _looks_like_record_ref(value):
        return value

    direct_spec = spec.direct_parameter
    if _looks_like_database_target(direct_spec.name, direct_spec.description, direct_spec.type_name):
        return {"__specifier__": _database_uuid_specifier(value)}

    if _looks_like_record_target(direct_spec.name, direct_spec.description):
        return {"__specifier__": _record_uuid_specifier(value)}

    return value


def _to_applescript_literal(value: Any) -> str:
    if value is None:
        return "missing value"

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, str):
        return _as_quote(value)

    if isinstance(value, dict):
        if "__specifier__" in value:
            return str(value["__specifier__"])
        if "__raw_applescript__" in value:
            return str(value["__raw_applescript__"])
        items = []
        for k, v in value.items():
            items.append(f"{_record_label(str(k))}:{_to_applescript_literal(v)}")
        return "{" + ", ".join(items) + "}"

    if isinstance(value, list):
        return "{" + ", ".join(_to_applescript_literal(v) for v in value) + "}"

    raise ValueError(f"Unsupported parameter type: {type(value).__name__}")


def _append_labeled_argument(chunks: list[str], label: str, literal: str) -> None:
    if literal.startswith(f"{label} "):
        chunks.append(literal)
        return
    chunks.append(f"{label} {literal}")


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


def _run_osascript(script: str, *, tool_name: str, command_name: str, extra: dict[str, Any] | None = None) -> str:
    record_applescript_call()
    if log.isEnabledFor(logging.DEBUG):
        log.debug("AppleScript for %s/%s:\n%s", tool_name, command_name, script)
    proc = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(_classify_osascript_error(proc.stderr))
    return proc.stdout.strip()


def _build_command_call(spec: CommandSpec, direct: Any, parameters: dict[str, Any]) -> str:
    provided = dict(parameters)
    if spec.tool_name == "devonthink-classify" and "tags" not in provided:
        provided["tags"] = False
    chunks = [spec.command_name]

    if spec.direct_parameter is not None:
        direct_value = direct if direct is not None else provided.pop("direct", None)
        if direct_value is None and not spec.direct_parameter.optional:
            raise ValueError("Missing required direct parameter 'direct'.")
        if direct_value is not None:
            direct_value = _coerce_direct_value(spec, direct_value)
            chunks.append(_to_applescript_literal(direct_value))

    known = {p.name for p in spec.parameters}
    unknown = sorted(set(provided.keys()) - known)
    if unknown:
        raise ValueError(
            "Unknown parameters: "
            + ", ".join(unknown)
            + ". Use only: "
            + ", ".join(sorted(known))
        )

    for param in spec.parameters:
        value = provided.get(param.name)
        if value is None:
            if not param.optional:
                raise ValueError(f"Missing required parameter '{param.name}'.")
            continue

        value = _coerce_value_for_param(param.name, param.description, value, tool_name=spec.tool_name)
        _append_labeled_argument(chunks, param.label, _to_applescript_literal(value))

    return " ".join(chunks)


def _execute_command(spec: CommandSpec, direct: Any, parameters: dict[str, Any] | None) -> dict[str, Any]:
    parameters = parameters or {}

    if (
        spec.tool_name == "devonthink-add-custom-meta-data"
        and direct == 0
        and str(parameters.get("as", "")).strip().lower() in {"int", "real"}
    ):
        return {
            "ok": False,
            "tool": spec.tool_name,
            "command": spec.command_name,
            "suite": spec.suite_name,
            "error": "DEVONthink silently ignores numeric custom metadata value 0; use an empty string to clear the field or a non-zero sentinel value.",
            "observability": {
                "warnings": [
                    "custom_metadata_zero_ignored: DEVONthink treats numeric custom metadata value 0 as unset."
                ]
            },
        }

    try:
        command_call = _build_command_call(spec, direct, parameters)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "tool": spec.tool_name}

    script = textwrap.dedent(
        f"""
        on record_or_database_incoming_group_with_uuid(theUUID)
            tell application "DEVONthink"
                try
                    return get record with uuid theUUID
                end try
                try
                    return incoming group of (get database with uuid theUUID)
                end try
                error "Scope not found for uuid: " & theUUID
            end tell
        end record_or_database_incoming_group_with_uuid

        on require_record_with_uuid(theUUID)
            tell application "DEVONthink"
                set theRecord to get record with uuid theUUID
                if theRecord is missing value then
                    error "Record not found for uuid: " & theUUID
                end if
                return theRecord
            end tell
        end require_record_with_uuid

        on require_database_with_uuid(theUUID)
            tell application "DEVONthink"
                try
                    set theDB to get database with uuid theUUID
                on error
                    error "Database not found for uuid: " & theUUID
                end try
                if theDB is missing value then
                    error "Database not found for uuid: " & theUUID
                end if
                return theDB
            end tell
        end require_database_with_uuid

        tell application "DEVONthink"
            set _result to {command_call}
            try
                return _result as text
            on error
                return _result
            end try
        end tell
        """
    ).strip()

    try:
        raw = _run_osascript(
            script,
            tool_name=spec.tool_name,
            command_name=spec.command_name,
            extra={"direct": direct, "parameters": parameters},
        )
        result: dict[str, Any] = {
            "ok": True,
            "tool": spec.tool_name,
            "command": spec.command_name,
            "suite": spec.suite_name,
            "raw_result": raw,
            "applescript": command_call,
        }
        if spec.tool_name in {"devonthink-create-database", "devonthink-open-database"}:
            db_path_raw = direct if isinstance(direct, str) else None
            if db_path_raw:
                # Expand ~ and resolve so we can match DEVONthink's absolute path string.
                db_path_abs = os.path.abspath(os.path.expanduser(db_path_raw))
                # Escape any embedded quotes/backslashes for safe AppleScript embedding.
                escaped = db_path_abs.replace("\\", "\\\\").replace('"', '\\"')
                lookup_script = textwrap.dedent(
                    f'''
                    tell application "DEVONthink"
                        try
                            open database "{escaped}"
                        end try
                        repeat with db in databases
                            try
                                if (path of db) is "{escaped}" then
                                    return (uuid of db) & "|" & (name of db)
                                end if
                            end try
                        end repeat
                        return ""
                    end tell
                    '''
                ).strip()
                try:
                    info = _run_osascript(
                        lookup_script,
                        tool_name=spec.tool_name + ":resolve",
                        command_name=spec.command_name,
                        extra={"path": db_path_abs},
                    ).strip()
                    if "|" in info:
                        db_uuid, db_name = info.split("|", 1)
                        result["database"] = {"uuid": db_uuid, "name": db_name, "path": db_path_abs}
                    else:
                        result.setdefault("observability", {}).setdefault("warnings", []).append(
                            f"database_not_resolved: created/opened database not visible at {db_path_abs}."
                        )
                except RuntimeError:
                    pass
        return result
    except RuntimeError as exc:
        return {
            "ok": False,
            "tool": spec.tool_name,
            "command": spec.command_name,
            "suite": spec.suite_name,
            "error": str(exc),
            "applescript": command_call,
        }


def _tool_description(spec: CommandSpec) -> str:
    meta = build_dictionary_tool_metadata(spec)
    return meta["description"]


def _profile_availability(spec: CommandSpec) -> list[str]:
    if spec.tier == "canonical":
        return ["canonical", "full"]
    return ["full"]


def _safety_class(spec: CommandSpec) -> str:
    name = spec.command_name.lower().strip()
    if name.startswith(UI_PREFIXES):
        return "ui_coupled"
    if name.startswith(DESTRUCTIVE_PREFIXES) or spec.tier == "internal":
        return "destructive" if name.startswith(("delete", "restore")) else "writes_data"
    if name.startswith(READ_ONLY_PREFIXES) or spec.tier == "canonical":
        return "read_only"
    return "writes_data"


def _collect_identifier_hints(spec: CommandSpec) -> tuple[list[str], str | None]:
    identifiers: list[str] = []

    def _record_hint(name: str, desc: str, type_name: str | None) -> None:
        lower_name = name.lower()
        lower_desc = (desc or "").lower()
        lower_type = (type_name or "").lower()
        if "url" in lower_name or "url" in lower_desc:
            identifiers.append("url")
        if "path" in lower_name or "path" in lower_desc or "file url" in lower_desc or "posix" in lower_desc:
            identifiers.append("posix_path")
        if _looks_like_database_target(name, desc, type_name):
            identifiers.append("database_uuid")
        if _looks_like_record_target(name, desc) or "item link" in lower_desc or lower_type == "specifier":
            identifiers.extend(["record_uuid", "x-devonthink-item://"])
        if "group" in lower_desc:
            identifiers.append("group_uuid")

    if spec.direct_parameter:
        _record_hint(spec.direct_parameter.name, spec.direct_parameter.description, spec.direct_parameter.type_name)
    for param in spec.parameters:
        _record_hint(param.name, param.description, param.type_name)

    ordered: list[str] = []
    for item in identifiers:
        if item not in ordered:
            ordered.append(item)

    preferred = None
    for candidate in ("record_uuid", "database_uuid", "group_uuid", "x-devonthink-item://", "posix_path", "url"):
        if candidate in ordered:
            preferred = candidate
            break
    return ordered, preferred


def _identifier_guidance(spec: CommandSpec) -> str:
    identifiers, preferred = _collect_identifier_hints(spec)
    if not identifiers:
        return (
            "Use exact dictionary parameter names. This command mainly takes plain strings, numbers, booleans, or lists rather than DEVONthink UUID references."
        )

    phrases = []
    if "record_uuid" in identifiers:
        phrases.append("record UUIDs")
    if "x-devonthink-item://" in identifiers:
        phrases.append("x-devonthink-item links")
    if "group_uuid" in identifiers:
        phrases.append("group UUIDs")
    if "database_uuid" in identifiers:
        phrases.append("database UUIDs")
    if "posix_path" in identifiers:
        phrases.append("POSIX paths")
    if "url" in identifiers:
        phrases.append("URLs")

    guidance = "Accepts " + ", ".join(phrases) + "."
    if preferred:
        guidance += f" Prefer {preferred} when you already have a stable locator."
    if spec.tool_name == "devonthink-search":
        guidance += " Prefer a group UUID for the `in` parameter; passing a database UUID directly can be invalid in DEVONthink."
    return guidance


def _prefer_when(spec: CommandSpec) -> str:
    name = spec.tool_name
    if name == "devonthink-delete":
        return "you need raw dictionary-level deletion and already have the target record UUID; use the higher-level wrappers first for safer workflows."
    if name == "devonthink-move":
        return "you need to relocate an existing DEVONthink record between groups; use move-into-database only when crossing from external/imported state."
    if name == "devonthink-search":
        return "you need the native DEVONthink search command exactly; prefer devonthink-search-records for structured record output and safer database scoping."
    if name == "devonthink-create-record-with":
        return "you need the raw dictionary command; prefer devonthink-create-record for validated record creation."
    if name.startswith("devonthink-get-record"):
        return "you need exact record lookup by a stable identifier; prefer search tools when you only have text or metadata clues."
    if name.startswith("devonthink-get-database"):
        return "you already know the database UUID or scripting id; prefer scoped search wrappers for content retrieval."
    return "you need the exact DEVONthink dictionary command instead of a higher-level wrapper."


def _degradation_contract(spec: CommandSpec) -> str | None:
    if spec.tool_name == "devonthink-search":
        return (
            "This raw command mirrors DEVONthink directly and may fail on unsupported database-object scoping instead of degrading. "
            "Prefer devonthink-search-records when you want automatic database-UUID to incoming-group resolution."
        )
    return None


def _example_for_spec(spec: CommandSpec) -> str:
    if spec.tool_name == "devonthink-delete":
        return '{"parameters":{"record":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}}'
    if spec.tool_name == "devonthink-move":
        return '{"parameters":{"record":"5038E0B0-2134-4CDA-B443-6558CE283BCC","to":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}}'
    if spec.tool_name == "devonthink-compress":
        return '{"parameters":{"database":"0444C204-D8AD-4CC0-8A9A-9F6817C12896","to":"/tmp/inbox.zip"}}'
    if spec.tool_name == "devonthink-search":
        return '{"direct":"tag:inbox","parameters":{"in":"180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"}}'
    if spec.tool_name.startswith("devonthink-get-record"):
        return '{"direct":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}'
    if spec.tool_name.startswith("devonthink-get-database"):
        return '{"direct":"0444C204-D8AD-4CC0-8A9A-9F6817C12896"}'
    if spec.tool_name == "devonthink-lookup-records-with-path":
        return '{"direct":"~/Documents/example.pdf"}'
    if spec.tool_name == "devonthink-create-database":
        return '{"direct":"~/Desktop/Test.dtBase2"}'
    if spec.tool_name == "devonthink-open-database":
        return '{"direct":"~/Desktop/Test.dtBase2"}'
    if spec.tool_name == "devonthink-add-custom-meta-data":
        # `direct` is the metadata VALUE, `for` is the key name, `to` is the record UUID.
        # The default 5038... placeholder was misleading callers into treating direct as a UUID.
        return '{"direct":"my value","parameters":{"for":"my_key","to":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}}'
    if spec.tool_name == "devonthink-get-custom-meta-data":
        return '{"parameters":{"for":"my_key","from":"5038E0B0-2134-4CDA-B443-6558CE283BCC"}}'
    if spec.tool_name == "devonthink-create-markdown-from":
        return '{"direct":"https://example.com/article"}'

    params: dict[str, Any] = {}
    direct: Any = None
    if spec.direct_parameter and not spec.direct_parameter.optional:
        direct_name = spec.direct_parameter.name.lower()
        if "database" in direct_name:
            direct = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"
        elif "url" in direct_name:
            direct = "https://example.com"
        elif "path" in direct_name:
            direct = "/tmp/example.txt"
        else:
            direct = "5038E0B0-2134-4CDA-B443-6558CE283BCC"

    for p in spec.parameters:
        if p.optional:
            continue
        lower_name = p.name.lower()
        lower_desc = p.description.lower()
        if _looks_like_database_target(p.name, p.description, p.type_name):
            params[p.name] = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"
        elif _looks_like_record_target(p.name, p.description):
            params[p.name] = "5038E0B0-2134-4CDA-B443-6558CE283BCC"
        elif "path" in lower_name or "posix" in lower_desc:
            params[p.name] = "/tmp/example.txt"
        elif "url" in lower_name or "url" in lower_desc:
            params[p.name] = "https://example.com"
        elif "position" in lower_name or "size" in lower_name:
            params[p.name] = 1
        else:
            params[p.name] = "example"

    payload: dict[str, Any] = {}
    if direct is not None:
        payload["direct"] = direct
    if params:
        payload["parameters"] = params
    return json.dumps(payload)


def build_dictionary_tool_metadata(spec: CommandSpec) -> dict[str, Any]:
    accepted_identifiers, preferred_identifier = _collect_identifier_hints(spec)
    identifier_guidance = _identifier_guidance(spec)
    safety_class = _safety_class(spec)
    profile_availability = _profile_availability(spec)
    prefer_when = _prefer_when(spec)
    degradation_contract = _degradation_contract(spec)
    example = _example_for_spec(spec)
    summary = (
        f"Run the native DEVONthink dictionary command '{spec.command_name}' from {spec.suite_name}."
        if spec.command_description
        else f"Run the native DEVONthink dictionary command '{spec.command_name}'."
    )
    use_when = (
        spec.command_description.strip()
        if spec.command_description
        else "you need the exact low-level DEVONthink AppleScript command rather than a specialized MCP wrapper."
    )
    description = build_description(
        summary=summary,
        use_when=use_when,
        identifier_guidance=identifier_guidance,
        safety_class=safety_class,
        prefer_when=prefer_when,
        degradation_contract=degradation_contract,
        example=example,
    )
    tags = ["devonthink", "dictionary", spec.suite_name.lower().replace(" suite", "").replace(" ", "-")]
    return catalog_entry(
        name=spec.tool_name,
        description=description,
        group="devonthink.native",
        tier=spec.tier,
        status="active",
        canonical_tool=spec.tool_name,
        overlap_family=f"devonthink-{spec.command_name.split()[0].lower()}" if spec.command_name else None,
        source_path=f"app/data/devonthink_command_specs.json#command:{_slugify(spec.command_name)}",
        catalog_path=f"catalog-runtime/tools/devonthink.native/{spec.tier}/{spec.tool_name}.json",
        executable="osascript",
        priority=100 if spec.tier == "canonical" else 60,
        default_exposed=(spec.tier == "canonical"),
        accepted_identifiers=accepted_identifiers,
        preferred_identifier=preferred_identifier,
        identifier_guidance=identifier_guidance,
        safety_class=safety_class,
        profile_availability=profile_availability,
        prefer_when=prefer_when,
        degradation_contract=degradation_contract,
        example=example,
        tags=tags,
        invocation_pitfalls=[
            "Use exact dictionary parameter names inside the parameters object.",
            "Prefer specialized wrappers for common workflows when available.",
        ],
    )


def dictionary_tool_catalog_entries(*, include_tiers: set[str] | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for spec in get_dictionary_command_specs():
        if include_tiers is not None and spec.tier not in include_tiers:
            continue
        entries.append(build_dictionary_tool_metadata(spec))
    return entries


def register_devonthink_dictionary_tools(
    mcp: Any,
    *,
    include_tiers: set[str] | None = None,
) -> None:
    for spec in get_dictionary_command_specs():
        if include_tiers is not None and spec.tier not in include_tiers:
            continue
        description = _tool_description(spec)

        def _factory(inner_spec: CommandSpec):
            def _tool(direct: Any = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
                return wrap_tool_call(
                    inner_spec.tool_name,
                    _execute_command,
                    inner_spec,
                    direct=direct,
                    parameters=parameters,
                )

            _tool.__name__ = f"tool_{inner_spec.tool_name.replace('-', '_')}"
            _tool.__doc__ = description
            return _tool

        mcp.tool(name=spec.tool_name, description=description)(_factory(spec))
