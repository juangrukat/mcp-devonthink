from __future__ import annotations

from app.tools import devonthink_richtext_tools as richtext_tools


def test_create_rtf_uses_rich_text_at_creation(monkeypatch) -> None:
    calls = []

    def fake_json(script, args, *, tool_name):
        calls.append((script, args, tool_name))
        return {"uuid": "NEW", "name": "Meeting Notes", "type": "rtf"}

    monkeypatch.setattr(richtext_tools, "_run_json", fake_json)

    result = richtext_tools.devonthink_create_rtf("Meeting Notes", "Body", "PARENT")

    assert result["ok"] is True
    assert result["data"]["uuid"] == "NEW"
    assert "type:rtf" in calls[0][0]
    assert "rich text:recordContent" in calls[0][0]
    assert calls[0][1] == ["Meeting Notes", "Body", "PARENT"]


def test_create_rtfd_adds_text_only_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        richtext_tools,
        "_run_json",
        lambda script, args, *, tool_name: {"uuid": "NEW", "name": "Report", "type": "RTF"},
    )

    result = richtext_tools.devonthink_create_rtfd("Report", "Body", "PARENT")

    assert result["ok"] is True
    assert result["actual_type"] == "RTF"
    assert "rtfd_text_only" in result["observability"]["warnings"][0]
    assert any("rtfd_downgraded_to_rtf" in warning for warning in result["observability"]["warnings"])


def test_read_rtf_returns_json_data(monkeypatch) -> None:
    monkeypatch.setattr(
        richtext_tools,
        "_run_json",
        lambda script, args, *, tool_name: {
            "record": {"uuid": "REC"},
            "plain_text": "Plain",
            "rich_text": "Rich",
        },
    )

    result = richtext_tools.devonthink_read_rtf("REC")

    assert result["ok"] is True
    assert result["data"]["plain_text"] == "Plain"


def test_richtext_catalog_examples_do_not_hardcode_home_username() -> None:
    forbidden_home = "/Users/" + "kat"
    assert not any(forbidden_home in entry["example"] for entry in richtext_tools.richtext_tool_catalog_entries())
