"""Shared AppleScript execution helpers."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


class AppleScriptExecutionError(RuntimeError):
    """Raised when an AppleScript command fails."""


def as_quote(value: str) -> str:
    """Return an AppleScript expression for a safely quoted string."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '" & quote & "') + '"'


def classify_osascript_error(stderr: str) -> str:
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


def run_applescript(script: str, args: list[str] | None = None, *, tool_name: str | None = None) -> str:
    """Run AppleScript via stdin so generated scripts remain inspectable."""
    from app.tools.applescript_counter import record_applescript_call

    record_applescript_call()
    if log.isEnabledFor(logging.DEBUG):
        label = f" for {tool_name}" if tool_name else ""
        log.debug("AppleScript%s:\n%s", label, script)
    proc = subprocess.run(
        ["/usr/bin/osascript", "-l", "AppleScript", "-", *(args or [])],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AppleScriptExecutionError(classify_osascript_error(proc.stderr))
    return proc.stdout.strip()
