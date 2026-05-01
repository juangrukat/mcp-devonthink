from __future__ import annotations

import plistlib

from app.tools import devonthink_smart_tools as smart_tools


def test_create_smart_group_passes_predicates_and_scope(monkeypatch) -> None:
    calls = []

    def fake_run(script, args=None, *, tool_name=None):
        calls.append((script, args, tool_name))
        return "NEW-SMART-GROUP"

    monkeypatch.setattr(smart_tools, "run_applescript", fake_run)

    result = smart_tools.devonthink_create_smart_group(
        "PDF Files",
        "kind:PDF",
        "PARENT-UUID",
        "SCOPE-UUID",
    )

    assert result["ok"] is True
    assert result["uuid"] == "NEW-SMART-GROUP"
    assert calls[0][1] == ["PDF Files", "kind:PDF", "PARENT-UUID", "SCOPE-UUID"]
    assert "get record with uuid" in calls[0][0]


def test_create_smart_group_requires_predicates() -> None:
    result = smart_tools.devonthink_create_smart_group("PDF Files", "", "PARENT-UUID")

    assert result["ok"] is False
    assert "search_predicates" in result["error"]


def test_list_smart_rules_reads_devonthink_plist(monkeypatch, tmp_path) -> None:
    rules_dir = tmp_path / "Library" / "Application Support" / "DEVONthink"
    rules_dir.mkdir(parents=True)
    (rules_dir / "SmartRules.plist").write_bytes(
        plistlib.dumps(
            [
                {"name": "Auto Tag", "Enabled": True, "sync": {"UUID": "RULE-1"}},
                {"name": "Review", "Enabled": False, "sync": {"UUID": "RULE-2"}},
            ]
        )
    )
    monkeypatch.setattr(smart_tools.Path, "home", lambda: tmp_path)

    result = smart_tools.devonthink_list_smart_rules()

    assert result["ok"] is True
    assert result["smart_rules"] == [
        {"id": "RULE-1", "name": "Auto Tag", "enabled": True},
        {"id": "RULE-2", "name": "Review", "enabled": False},
    ]


def test_apply_smart_rule_uses_dictionary_command(monkeypatch) -> None:
    calls = []

    def fake_run(script, args=None, *, tool_name=None):
        calls.append((script, args, tool_name))
        return "true"

    monkeypatch.setattr(smart_tools, "run_applescript", fake_run)

    result = smart_tools.devonthink_apply_smart_rule("Auto Tag", "RECORD-UUID")

    assert result["ok"] is True
    assert result["result"] == "true"
    assert "perform_smart_rule_context_limitation" in result["observability"]["warnings"][0]
    assert "perform smart rule name ruleName record theRecord" in calls[0][0]


def test_smart_catalog_entries_include_create_tool() -> None:
    names = {entry["name"] for entry in smart_tools.smart_tool_catalog_entries()}

    assert "devonthink-create-smart-group" in names
    assert "devonthink-apply-smart-rule" in names
