from __future__ import annotations

from app.tools import devonthink_script_tools as script_tools


def test_script_dirs_use_home_and_application_scripts_first(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(script_tools.Path, "home", lambda: tmp_path)

    dirs = script_tools._script_dirs()

    assert dirs[0] == tmp_path / "Library" / "Application Scripts" / "com.devon-technologies.think"
    assert all(str(tmp_path) in str(path) for path in dirs)


def test_list_scripts_returns_tilde_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(script_tools.Path, "home", lambda: tmp_path)
    base = tmp_path / "Library" / "Application Scripts" / "com.devon-technologies.think"
    base.mkdir(parents=True)
    (base / "Demo.applescript").write_text("return \"ok\"", encoding="utf-8")

    result = script_tools.devonthink_list_scripts()

    assert result["ok"] is True
    assert result["scripts"][0]["path"] == "~/Library/Application Scripts/com.devon-technologies.think/Demo.applescript"
    assert result["scripts"][0]["base"] == "~/Library/Application Scripts/com.devon-technologies.think"


def test_run_script_rejects_paths_outside_script_dirs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(script_tools.Path, "home", lambda: tmp_path)
    outside = tmp_path / "Desktop" / "Demo.applescript"
    outside.parent.mkdir()
    outside.write_text("return \"ok\"", encoding="utf-8")

    result = script_tools.devonthink_run_script(str(outside))

    assert result["ok"] is False
    assert "outside DEVONthink script directories" in result["error"]


def test_create_script_writes_source_and_compiles(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(script_tools.Path, "home", lambda: tmp_path)
    calls = []

    class Proc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(script_tools.subprocess, "run", fake_run)

    result = script_tools.devonthink_create_script("Demo", "return \"ok\"")

    assert result["ok"] is True
    assert result["path"] == "~/Library/Application Scripts/com.devon-technologies.think/Demo.applescript"
    assert result["compiled_path"] == "~/Library/Application Scripts/com.devon-technologies.think/Demo.scpt"
    assert calls[0][0] == "/usr/bin/osacompile"


def test_run_applescript_persists_source_and_transcript(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(script_tools, "FALLBACK_RUNS_DIR", tmp_path / "runs")
    calls = []

    class Proc:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Proc()

    monkeypatch.setattr(script_tools.subprocess, "run", fake_run)

    result = script_tools.devonthink_run_applescript('return "ok"', label="probe")

    assert result["ok"] is True
    assert result["output"] == "ok"
    assert calls[0][0][0] == "/usr/bin/osascript"
    assert calls[0][1]["timeout"] == 30
    assert result["source_path"].endswith(".applescript")
    assert result["result_path"].endswith(".result.json")
    assert (tmp_path / "runs").exists()


def test_script_catalog_examples_do_not_hardcode_home_username() -> None:
    entries = script_tools.script_tool_catalog_entries()
    forbidden_home = "/Users/" + "kat"

    assert not any(forbidden_home in entry["example"] for entry in entries)
    assert any("~/Library/Application Scripts/com.devon-technologies.think" in entry["example"] for entry in entries)
    assert any(entry["name"] == "devonthink-run-applescript" for entry in entries)
