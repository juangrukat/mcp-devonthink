"""Apple Event call counting helpers for DEVONthink tool observability."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import local
from typing import Iterator


_STATE = local()


@dataclass
class AppleScriptCallCounter:
    count: int = 0


def _ensure_state() -> None:
    if not hasattr(_STATE, "total"):
        _STATE.total = 0
    if not hasattr(_STATE, "stack"):
        _STATE.stack = []


def get_applescript_total() -> int:
    _ensure_state()
    return int(_STATE.total)


def record_applescript_call() -> None:
    _ensure_state()
    _STATE.total += 1
    for counter in _STATE.stack:
        counter.count += 1


@contextmanager
def count_applescript_calls() -> Iterator[AppleScriptCallCounter]:
    _ensure_state()
    counter = AppleScriptCallCounter()
    _STATE.stack.append(counter)
    try:
        yield counter
    finally:
        _STATE.stack.pop()
