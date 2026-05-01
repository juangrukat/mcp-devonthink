from __future__ import annotations

from app.tools.devonthink_dictionary_tools import _build_command_call, _execute_command, get_dictionary_command_specs


UUID_RECORD = "5038E0B0-2134-4CDA-B443-6558CE283BCC"
UUID_GROUP = "180AA7E9-CBB5-4DEF-8F06-7DEDD2809E5B"
UUID_DATABASE = "0444C204-D8AD-4CC0-8A9A-9F6817C12896"


def _spec_by_tool(tool_name: str):
    for spec in get_dictionary_command_specs():
        if spec.tool_name == tool_name:
            return spec
    raise AssertionError(f"Missing tool spec: {tool_name}")


def test_delete_uses_single_record_specifier() -> None:
    spec = _spec_by_tool("devonthink-delete")
    command = _build_command_call(spec, None, {"record": UUID_RECORD})
    assert command == f'delete record (my require_record_with_uuid("{UUID_RECORD}"))'


def test_move_uses_record_specifiers_for_record_and_destination_group() -> None:
    spec = _spec_by_tool("devonthink-move")
    command = _build_command_call(
        spec,
        None,
        {
            "record": UUID_RECORD,
            "to": UUID_GROUP,
        },
    )
    assert command == (
        f'move record (my require_record_with_uuid("{UUID_RECORD}")) '
        f'to (my require_record_with_uuid("{UUID_GROUP}"))'
    )


def test_move_strips_x_devonthink_item_prefix_from_record_refs() -> None:
    spec = _spec_by_tool("devonthink-move")
    command = _build_command_call(
        spec,
        None,
        {
            "record": f"x-devonthink-item://{UUID_RECORD}",
            "to": f"x-devonthink-item://{UUID_GROUP}",
        },
    )
    assert command == (
        f'move record (my require_record_with_uuid("{UUID_RECORD}")) '
        f'to (my require_record_with_uuid("{UUID_GROUP}"))'
    )


def test_compress_uses_database_specifier_and_preserves_zip_path() -> None:
    spec = _spec_by_tool("devonthink-compress")
    command = _build_command_call(
        spec,
        None,
        {
            "database": UUID_DATABASE,
            "to": "/tmp/test.zip",
        },
    )
    assert command == (
        f'compress database (my require_database_with_uuid("{UUID_DATABASE}")) '
        'to "/tmp/test.zip"'
    )


def test_close_coerces_direct_database_uuid() -> None:
    spec = _spec_by_tool("devonthink-close")
    command = _build_command_call(spec, UUID_DATABASE, {})
    assert command == f'close (my require_database_with_uuid("{UUID_DATABASE}"))'


def test_classify_defaults_tags_argument_for_dt4() -> None:
    spec = _spec_by_tool("devonthink-classify")
    command = _build_command_call(spec, None, {"record": UUID_RECORD})
    assert command == (
        f'classify record (my require_record_with_uuid("{UUID_RECORD}")) tags false'
    )


def test_custom_metadata_numeric_zero_is_rejected_with_warning() -> None:
    spec = _spec_by_tool("devonthink-add-custom-meta-data")
    result = _execute_command(
        spec,
        0,
        {
            "for": "score",
            "to": UUID_RECORD,
            "as": "int",
        },
    )

    assert result["ok"] is False
    assert "silently ignores" in result["error"]
    assert "custom_metadata_zero_ignored" in result["observability"]["warnings"][0]


def test_raw_search_scope_uuid_can_be_group_or_database_uuid() -> None:
    spec = _spec_by_tool("devonthink-search")
    command = _build_command_call(spec, "library", {"in": UUID_DATABASE})

    assert command == (
        f'search "library" in (my record_or_database_incoming_group_with_uuid("{UUID_DATABASE}"))'
    )
