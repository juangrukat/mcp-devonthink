"""Unit tests for input validation, name sanitization, and pagination shape.

These tests live above the AppleScript layer — they exercise pure-Python helpers
and the script-template generator without launching DEVONthink.
"""

from __future__ import annotations

import pytest

from app.tools.devonthink_tools import (
    _validate_offset,
    _validate_record_name,
    devonthink_create_record,
    devonthink_list_group_children,
)
from app.tools.devonthink_dictionary_tools import (
    _build_command_call,
    _execute_command,
    get_dictionary_command_specs,
)


UUID_RECORD = "5038E0B0-2134-4CDA-B443-6558CE283BCC"


def _spec_by_tool(tool_name: str):
    for spec in get_dictionary_command_specs():
        if spec.tool_name == tool_name:
            return spec
    raise AssertionError(f"Missing tool spec: {tool_name}")


# ---------------------------------------------------------------------------
# _validate_record_name


def test_validate_record_name_accepts_normal_titles() -> None:
    assert _validate_record_name("Note 001") == "Note 001"
    assert _validate_record_name("  Re: meeting  ") == "Re: meeting"
    assert _validate_record_name("café — résumé (v2)") == "café — résumé (v2)"
    assert _validate_record_name("foo..bar") == "foo..bar"  # `..` inside a token is fine


def test_validate_record_name_rejects_empty_or_whitespace() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _validate_record_name("")
    with pytest.raises(ValueError, match="non-empty"):
        _validate_record_name("   ")


def test_validate_record_name_rejects_path_separators() -> None:
    with pytest.raises(ValueError, match="path separators"):
        _validate_record_name("foo/bar")
    with pytest.raises(ValueError, match="path separators"):
        _validate_record_name("foo\\bar")
    with pytest.raises(ValueError, match="path separators"):
        _validate_record_name("../../../etc/passwd")


def test_validate_record_name_rejects_dotdot_segment() -> None:
    # The bare `..` form (no separators) — explicit segment check.
    with pytest.raises(ValueError, match=r"\.\."):
        _validate_record_name("..")


def test_validate_record_name_rejects_nul_and_control_chars() -> None:
    with pytest.raises(ValueError, match="NUL"):
        _validate_record_name("foo\x00bar")
    with pytest.raises(ValueError, match="control characters"):
        _validate_record_name("foo\x01bar")
    with pytest.raises(ValueError, match="control characters"):
        _validate_record_name("foo\nbar")


def test_create_record_rejects_path_traversal_without_apple_event() -> None:
    """Path-traversal title is rejected before any AppleScript runs."""
    result = devonthink_create_record(
        name="../../../etc/passwd",
        record_type="txt",
        group_uuid=UUID_RECORD,
    )
    assert result["ok"] is False
    assert "path separators" in result["error"]


# ---------------------------------------------------------------------------
# _validate_offset


def test_validate_offset_accepts_zero_and_positive() -> None:
    assert _validate_offset(0) == 0
    assert _validate_offset(50) == 50


def test_validate_offset_rejects_negative() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        _validate_offset(-1)


def test_list_group_children_rejects_negative_offset() -> None:
    result = devonthink_list_group_children(group_uuid=UUID_RECORD, offset=-5)
    assert result["ok"] is False
    assert "offset" in result["error"]


# ---------------------------------------------------------------------------
# Script-template guarded resolvers (delete/move/etc. NOT_FOUND normalization)


def test_delete_command_uses_guarded_resolver() -> None:
    spec = _spec_by_tool("devonthink-delete")
    command = _build_command_call(spec, None, {"record": UUID_RECORD})
    # The specifier now invokes the require_* helper, so a missing UUID will
    # surface "Record not found for uuid: ..." instead of a -1700 type error.
    assert "require_record_with_uuid" in command
    assert "get record with uuid" not in command


def test_close_command_uses_database_guarded_resolver() -> None:
    spec = _spec_by_tool("devonthink-close")
    command = _build_command_call(spec, "0444C204-D8AD-4CC0-8A9A-9F6817C12896", {})
    assert "require_database_with_uuid" in command


def test_execute_command_script_defines_require_helpers() -> None:
    """The runtime AppleScript template must include both require_* handlers."""
    spec = _spec_by_tool("devonthink-delete")
    # We need the script body without actually launching osascript. _execute_command
    # builds and runs the script — we synthesize the body via the same path the
    # generator uses by inspecting the source. The simplest end-to-end check is
    # to confirm the helper definitions are present in the module source so the
    # template generator emits them.
    import inspect
    from app.tools import devonthink_dictionary_tools as mod

    src = inspect.getsource(mod._execute_command)
    assert "on require_record_with_uuid" in src
    assert "on require_database_with_uuid" in src
    assert 'error "Record not found for uuid: " & theUUID' in src
    assert 'error "Database not found for uuid: " & theUUID' in src
