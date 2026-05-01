"""Cross-cutting telemetry helpers for DEVONthink MCP tools."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.tools.applescript_counter import get_applescript_total


LOGGER = logging.getLogger("devonthink.telemetry")
TRACE_ENV = "DEVONTHINK_TOOL_TRACE_JSONL"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_path() -> Path | None:
    raw = os.environ.get(TRACE_ENV, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def append_trace(event: dict[str, Any]) -> None:
    path = _trace_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=True) + "\n")


def wrap_tool_call(tool_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    started = time.perf_counter()
    started_calls = get_applescript_total()
    status = "ok"
    error = None
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        status = "exception"
        error = str(exc)
        duration_ms = int((time.perf_counter() - started) * 1000)
        apple_event_calls = get_applescript_total() - started_calls
        event = {
            "tool": tool_name,
            "status": status,
            "duration_ms": duration_ms,
            "apple_event_calls": apple_event_calls,
            "executed_at_utc": _utc_now(),
            "error": error,
        }
        LOGGER.info(
            "tool=%s status=%s duration_ms=%s apple_event_calls=%s",
            tool_name,
            status,
            duration_ms,
            apple_event_calls,
        )
        append_trace(event)
        raise

    duration_ms = int((time.perf_counter() - started) * 1000)
    apple_event_calls = get_applescript_total() - started_calls
    if isinstance(result, dict):
        if result.get("ok") is False:
            status = "error"
            error = result.get("error")
        result.setdefault("tool", tool_name)
        obs = result.setdefault("observability", {})
        obs.setdefault("executed_at_utc", _utc_now())
        obs["gateway_duration_ms"] = duration_ms
        gateway_stats = obs.setdefault("gateway_stats", {})
        gateway_stats["tool_name"] = tool_name
        gateway_stats["status"] = status
        gateway_stats["apple_event_calls"] = apple_event_calls

    event = {
        "tool": tool_name,
        "status": status,
        "duration_ms": duration_ms,
        "apple_event_calls": apple_event_calls,
        "executed_at_utc": _utc_now(),
        "error": error,
    }
    LOGGER.info(
        "tool=%s status=%s duration_ms=%s apple_event_calls=%s",
        tool_name,
        status,
        duration_ms,
        apple_event_calls,
    )
    append_trace(event)
    return result
