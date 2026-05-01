from __future__ import annotations

from app.tools import devonthink_database_tools as db_tools


def test_list_databases_parses_tab_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        db_tools,
        "run_applescript",
        lambda *args, **kwargs: "DB-1\tInbox\t/Users/example/Inbox.dtBase2\nDB-2\tResearch\t/Users/example/Research.dtBase2",
    )

    result = db_tools.devonthink_list_databases()

    assert result["ok"] is True
    assert result["databases"] == [
        {"uuid": "DB-1", "name": "Inbox", "path": "/Users/example/Inbox.dtBase2"},
        {"uuid": "DB-2", "name": "Research", "path": "/Users/example/Research.dtBase2"},
    ]


def test_open_database_requires_path() -> None:
    result = db_tools.devonthink_open_database("")

    assert result["ok"] is False
    assert "path" in result["error"]


def test_verify_database_returns_result(monkeypatch) -> None:
    calls = []

    def fake_run(script, args=None, *, tool_name=None):
        calls.append((script, args, tool_name))
        return "0"

    monkeypatch.setattr(db_tools, "run_applescript", fake_run)

    result = db_tools.devonthink_verify_database("0444C204-D8AD-4CC0-8A9A-9F6817C12896")

    assert result["ok"] is True
    assert result["result"] == "0"
    assert calls[0][1] == ["0444C204-D8AD-4CC0-8A9A-9F6817C12896"]


def test_database_catalog_entries_have_required_markers() -> None:
    entries = db_tools.database_tool_catalog_entries()

    assert {entry["name"] for entry in entries} >= {
        "devonthink-list-databases",
        "devonthink-open-database",
        "devonthink-close-database",
        "devonthink-sync-database",
        "devonthink-verify-database",
    }
    for entry in entries:
        assert "Use when:" in entry["description"]
        assert "Safety:" in entry["description"]
