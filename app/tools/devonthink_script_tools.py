"""Filesystem-backed DEVONthink script tools."""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.tools.applescript_counter import record_applescript_call
from app.tools.telemetry import wrap_tool_call
from app.tools.tool_catalog import build_description, catalog_entry

SCRIPT_EXTENSIONS = {".applescript", ".scpt", ".scptd", ".js"}
SCRIPT_TYPE_EXTENSIONS = {
    "applescript": ".applescript",
    "javascript": ".js",
}
ROOT = Path(__file__).resolve().parents[2]
FALLBACK_RUNS_DIR = ROOT / ".devonthink-mcp" / "osascript-runs"


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _script_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / "Library" / "Application Scripts" / "com.devon-technologies.think",
        home / "Library" / "Scripts" / "Applications" / "DEVONthink 3",
        home / "Library" / "Scripts" / "Applications" / "DEVONthink",
    ]


def _display_path(path: Path) -> str:
    try:
        return "~/" + str(path.resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return str(path)


def _resolve_script_path(script_path: str) -> Path:
    return Path(script_path).expanduser().resolve()


def _is_approved_script_path(path: Path) -> bool:
    resolved = path.resolve()
    for base in _script_dirs():
        try:
            resolved.relative_to(base.expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


def _default_script_dir() -> Path:
    return _script_dirs()[0]


def _safe_label(value: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "fallback").strip()).strip("-")
    return cleaned[:80] or "fallback"


def _validate_script_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("name must be a non-empty string.")
    if "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."}:
        raise ValueError("name must be a filename only, not a path.")
    return Path(cleaned).stem


def _normalize_script_type(script_type: str) -> str:
    cleaned = (script_type or "applescript").strip().lower()
    if cleaned not in SCRIPT_TYPE_EXTENSIONS:
        raise ValueError("script_type must be 'applescript' or 'javascript'.")
    return cleaned


def _source_for_script_path(path: Path) -> Path:
    if path.suffix.lower() == ".scpt":
        candidate = path.with_suffix(".applescript")
        if candidate.exists():
            return candidate
    return path


def devonthink_list_scripts() -> dict[str, Any]:
    started = time.perf_counter()
    scripts: list[dict[str, str]] = []
    seen: set[Path] = set()
    for base in _script_dirs():
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path in seen or path.suffix.lower() not in SCRIPT_EXTENSIONS:
                continue
            if not path.is_file() and path.suffix.lower() != ".scptd":
                continue
            seen.add(path)
            scripts.append(
                {
                    "name": path.stem,
                    "path": _display_path(path),
                    "type": "javascript" if path.suffix.lower() == ".js" else "applescript",
                    "relative": str(path.relative_to(base)),
                    "base": _display_path(base),
                }
            )
    return {"ok": True, "scripts": scripts, "observability": {"duration_ms": _duration_ms(started)}}


def devonthink_run_script(script_path: str, args: list[str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        resolved = _resolve_script_path(script_path)
        if not _is_approved_script_path(resolved):
            raise ValueError(f"Script path is outside DEVONthink script directories: {_display_path(resolved)}")
        if not resolved.exists():
            raise ValueError(f"Script not found: {_display_path(resolved)}")
        command = ["/usr/bin/osascript", str(resolved), *(str(arg) for arg in (args or []))]
        record_applescript_call()
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {
                "ok": False,
                "error": result.stderr.strip() or "Script execution failed.",
                "observability": {"duration_ms": _duration_ms(started)},
            }
        return {
            "ok": True,
            "output": result.stdout.strip(),
            "script_path": _display_path(resolved),
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def devonthink_run_applescript(
    source: str,
    args: list[str] | None = None,
    label: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run ad hoc AppleScript from a managed scratch directory and persist the transcript."""

    started = time.perf_counter()
    try:
        if source is None or not str(source).strip():
            raise ValueError("source must be a non-empty AppleScript string.")
        if len(source) > 50000:
            raise ValueError("source is too large; keep fallback scripts under 50000 characters.")
        if timeout_seconds < 1 or timeout_seconds > 300:
            raise ValueError("timeout_seconds must be between 1 and 300.")

        FALLBACK_RUNS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        base = f"{stamp}-{_safe_label(label)}"
        source_path = FALLBACK_RUNS_DIR / f"{base}.applescript"
        result_path = FALLBACK_RUNS_DIR / f"{base}.result.json"
        source_path.write_text(source, encoding="utf-8")

        command = ["/usr/bin/osascript", str(source_path), *(str(arg) for arg in (args or []))]
        record_applescript_call()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            timed_out = False
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            returncode = result.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
            returncode = None

        transcript = {
            "ok": (not timed_out and returncode == 0),
            "timed_out": timed_out,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "source_path": str(source_path),
            "args": [str(arg) for arg in (args or [])],
            "duration_ms": _duration_ms(started),
        }
        result_path.write_text(json.dumps(transcript, indent=2) + "\n", encoding="utf-8")

        if timed_out:
            return {
                "ok": False,
                "error": f"AppleScript timed out after {timeout_seconds} seconds.",
                "output": stdout,
                "stderr": stderr,
                "source_path": str(source_path),
                "result_path": str(result_path),
                "observability": {"duration_ms": _duration_ms(started)},
            }
        if returncode != 0:
            return {
                "ok": False,
                "error": stderr or "AppleScript execution failed.",
                "output": stdout,
                "stderr": stderr,
                "source_path": str(source_path),
                "result_path": str(result_path),
                "observability": {"duration_ms": _duration_ms(started)},
            }
        return {
            "ok": True,
            "output": stdout,
            "source_path": str(source_path),
            "result_path": str(result_path),
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def devonthink_create_script(name: str, source: str, script_type: str = "applescript") -> dict[str, Any]:
    started = time.perf_counter()
    try:
        cleaned_name = _validate_script_name(name)
        cleaned_type = _normalize_script_type(script_type)
        if source is None:
            raise ValueError("source must be a string.")
        base = _default_script_dir()
        base.mkdir(parents=True, exist_ok=True)
        source_path = base / f"{cleaned_name}{SCRIPT_TYPE_EXTENSIONS[cleaned_type]}"
        source_path.write_text(source, encoding="utf-8")

        response: dict[str, Any] = {
            "ok": True,
            "path": _display_path(source_path),
            "script_type": cleaned_type,
            "observability": {"duration_ms": _duration_ms(started)},
        }
        if cleaned_type == "applescript":
            compiled_path = base / f"{cleaned_name}.scpt"
            result = subprocess.run(
                ["/usr/bin/osacompile", "-o", str(compiled_path), str(source_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": f"Compile failed: {result.stderr.strip()}",
                    "source_path": _display_path(source_path),
                    "observability": {"duration_ms": _duration_ms(started)},
                }
            response["compiled_path"] = _display_path(compiled_path)
        return response
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def devonthink_read_script(script_path: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        resolved = _resolve_script_path(script_path)
        if not _is_approved_script_path(resolved):
            raise ValueError(f"Script path is outside DEVONthink script directories: {_display_path(resolved)}")
        source_path = _source_for_script_path(resolved)
        if source_path.exists() and source_path.suffix.lower() != ".scpt":
            return {
                "ok": True,
                "source": source_path.read_text(encoding="utf-8"),
                "path": _display_path(source_path),
                "observability": {"duration_ms": _duration_ms(started)},
            }
        if not resolved.exists():
            raise ValueError(f"Script not found: {_display_path(resolved)}")
        result = subprocess.run(["/usr/bin/osadecompile", str(resolved)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {
                "ok": False,
                "error": result.stderr.strip() or "Could not read script source.",
                "observability": {"duration_ms": _duration_ms(started)},
            }
        return {
            "ok": True,
            "source": result.stdout,
            "path": _display_path(resolved),
            "observability": {"duration_ms": _duration_ms(started)},
        }
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def devonthink_update_script(script_path: str, source: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        resolved = _resolve_script_path(script_path)
        if not _is_approved_script_path(resolved):
            raise ValueError(f"Script path is outside DEVONthink script directories: {_display_path(resolved)}")
        if not resolved.exists() and not resolved.with_suffix(".applescript").exists():
            raise ValueError(f"Script not found: {_display_path(resolved)}")
        source_path = _source_for_script_path(resolved)
        script_type = "javascript" if source_path.suffix.lower() == ".js" else "applescript"
        source_path.write_text(source, encoding="utf-8")
        response = {
            "ok": True,
            "path": _display_path(source_path),
            "script_type": script_type,
            "observability": {"duration_ms": _duration_ms(started)},
        }
        if script_type == "applescript":
            compiled_path = source_path.with_suffix(".scpt")
            result = subprocess.run(
                ["/usr/bin/osacompile", "-o", str(compiled_path), str(source_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": f"Compile failed: {result.stderr.strip()}",
                    "source_path": _display_path(source_path),
                    "observability": {"duration_ms": _duration_ms(started)},
                }
            response["compiled_path"] = _display_path(compiled_path)
        return response
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def devonthink_delete_script(script_path: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        resolved = _resolve_script_path(script_path)
        if not _is_approved_script_path(resolved):
            raise ValueError(f"Script path is outside DEVONthink script directories: {_display_path(resolved)}")
        candidates = {resolved}
        if resolved.suffix.lower() in {".scpt", ".applescript"}:
            candidates.add(resolved.with_suffix(".applescript"))
            candidates.add(resolved.with_suffix(".scpt"))
        deleted = []
        for candidate in sorted(candidates):
            if candidate.exists():
                if candidate.is_dir():
                    raise ValueError(f"Refusing to delete script directory: {_display_path(candidate)}")
                candidate.unlink()
                deleted.append(_display_path(candidate))
        return {"ok": True, "deleted": deleted, "observability": {"duration_ms": _duration_ms(started)}}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "observability": {"duration_ms": _duration_ms(started)}}


def _script_catalog_entry(
    *,
    name: str,
    summary: str,
    use_when: str,
    safety_class: str,
    prefer_when: str,
    example: str,
    tier: str,
) -> dict[str, Any]:
    identifier_guidance = "Accepts DEVONthink script paths under ~/Library/Application Scripts/com.devon-technologies.think or ~/Library/Scripts/Applications/DEVONthink*."
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
        overlap_family="devonthink-script",
        source_path="app/tools/devonthink_script_tools.py",
        catalog_path=f"catalog-runtime/tools/devonthink.native/{tier}/{name}.json",
        executable="filesystem+osascript",
        priority=100 if tier == "canonical" else 60,
        default_exposed=(tier == "canonical"),
        accepted_identifiers=["posix_path"],
        preferred_identifier="posix_path",
        identifier_guidance=identifier_guidance,
        safety_class=safety_class,
        profile_availability=["minimal", "canonical", "full"],
        prefer_when=prefer_when,
        example=example,
        tags=["devonthink", "script", "filesystem"],
    )


def script_tool_catalog_entries() -> list[dict[str, Any]]:
    return [
        _script_catalog_entry(
            name="devonthink-list-scripts",
            summary="List user scripts available to DEVONthink from filesystem script folders.",
            use_when="you need to discover filesystem AppleScript/JXA script files before reading or running one.",
            safety_class="read_only",
            prefer_when="you need filesystem script inventory, not saved DEVONthink smart rule names; use devonthink-list-smart-rules for rules.",
            example="{}",
            tier="canonical",
        ),
        _script_catalog_entry(
            name="devonthink-run-script",
            summary="Execute a user script with /usr/bin/osascript.",
            use_when="you need to run an existing filesystem AppleScript/JXA script by path; this executes arbitrary script code.",
            safety_class="destructive",
            prefer_when="the script already exists in an approved DEVONthink script directory; use devonthink-apply-smart-rule for named saved rules.",
            example='{"script_path":"~/Library/Application Scripts/com.devon-technologies.think/My Script.scpt","args":[]}',
            tier="advanced",
        ),
        _script_catalog_entry(
            name="devonthink-run-applescript",
            summary="Run ad hoc AppleScript from a managed scratch folder and save source plus execution transcript.",
            use_when="all safer structured tools failed or DEVONthink needs a one-off AppleScript probe; this executes arbitrary AppleScript and persists the run for later review.",
            safety_class="destructive",
            prefer_when="you need an explicit fallback/probe before deciding whether to promote the AppleScript into a first-class MCP wrapper.",
            example='{"label":"date-range-probe","source":"tell application id \\"DNtp\\"\\nreturn version\\nend tell","timeout_seconds":30}',
            tier="advanced",
        ),
        _script_catalog_entry(
            name="devonthink-create-script",
            summary="Create a DEVONthink user script source file and compile AppleScript to .scpt.",
            use_when="you need to add a new AppleScript or JavaScript automation script for DEVONthink.",
            safety_class="writes_data",
            prefer_when="you want filesystem-backed script creation with osacompile for AppleScript.",
            example='{"name":"Tag Selected Records","source":"tell application id \\"DNtp\\"\\nend tell","script_type":"applescript"}',
            tier="advanced",
        ),
        _script_catalog_entry(
            name="devonthink-read-script",
            summary="Read source text for a DEVONthink user script.",
            use_when="you need to inspect or edit an existing script before updating it.",
            safety_class="read_only",
            prefer_when="you want source text and can pass a path returned by list-scripts.",
            example='{"script_path":"~/Library/Application Scripts/com.devon-technologies.think/My Script.applescript"}',
            tier="canonical",
        ),
        _script_catalog_entry(
            name="devonthink-update-script",
            summary="Overwrite an existing DEVONthink user script and recompile AppleScript.",
            use_when="you need to replace the source of an existing script.",
            safety_class="writes_data",
            prefer_when="you already resolved the script path through list-scripts.",
            example='{"script_path":"~/Library/Application Scripts/com.devon-technologies.think/My Script.applescript","source":"tell application id \\"DNtp\\"\\nend tell"}',
            tier="advanced",
        ),
        _script_catalog_entry(
            name="devonthink-delete-script",
            summary="Delete a DEVONthink user script file and its paired source/compiled file.",
            use_when="you need to remove a script from DEVONthink's user script folders.",
            safety_class="destructive",
            prefer_when="you have confirmed the script path from list-scripts.",
            example='{"script_path":"~/Library/Application Scripts/com.devon-technologies.think/Old Script.scpt"}',
            tier="advanced",
        ),
    ]


def register_devonthink_script_tools(mcp: Any) -> None:
    catalog = {entry["name"]: entry for entry in script_tool_catalog_entries()}

    @mcp.tool(name="devonthink-list-scripts", description=catalog["devonthink-list-scripts"]["description"])
    def _devonthink_list_scripts() -> dict[str, Any]:
        return wrap_tool_call("devonthink-list-scripts", devonthink_list_scripts)

    @mcp.tool(name="devonthink-run-script", description=catalog["devonthink-run-script"]["description"])
    def _devonthink_run_script(script_path: str, args: list[str] | None = None) -> dict[str, Any]:
        return wrap_tool_call("devonthink-run-script", devonthink_run_script, script_path=script_path, args=args)

    @mcp.tool(name="devonthink-run-applescript", description=catalog["devonthink-run-applescript"]["description"])
    def _devonthink_run_applescript(
        source: str,
        args: list[str] | None = None,
        label: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-run-applescript",
            devonthink_run_applescript,
            source=source,
            args=args,
            label=label,
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool(name="devonthink-create-script", description=catalog["devonthink-create-script"]["description"])
    def _devonthink_create_script(name: str, source: str, script_type: str = "applescript") -> dict[str, Any]:
        return wrap_tool_call(
            "devonthink-create-script",
            devonthink_create_script,
            name=name,
            source=source,
            script_type=script_type,
        )

    @mcp.tool(name="devonthink-read-script", description=catalog["devonthink-read-script"]["description"])
    def _devonthink_read_script(script_path: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-read-script", devonthink_read_script, script_path=script_path)

    @mcp.tool(name="devonthink-update-script", description=catalog["devonthink-update-script"]["description"])
    def _devonthink_update_script(script_path: str, source: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-update-script", devonthink_update_script, script_path=script_path, source=source)

    @mcp.tool(name="devonthink-delete-script", description=catalog["devonthink-delete-script"]["description"])
    def _devonthink_delete_script(script_path: str) -> dict[str, Any]:
        return wrap_tool_call("devonthink-delete-script", devonthink_delete_script, script_path=script_path)
