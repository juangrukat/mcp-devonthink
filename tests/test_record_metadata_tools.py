from __future__ import annotations

from app.tools import devonthink_tools


def test_set_label_script_verifies_applied_label(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, tool_name))
        return ""

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_set_label("RECORD-UUID", 4)

    assert result["ok"] is True
    assert "if (label of theRecord as integer) is not labelValue" in calls[0][0]


def test_batch_set_label_script_verifies_each_label(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, tool_name))
        return "2"

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_batch_set_label(["A", "B"], 2)

    assert result["ok"] is True
    assert result["updated"] == 2
    assert "if (label of theRecord as integer) is not labelValue" in calls[0][0]


def test_set_label_validates_label_range() -> None:
    result = devonthink_tools.devonthink_set_label("RECORD-UUID", 8)

    assert result["ok"] is False
    assert "label" in result["error"]


def test_get_record_by_uuid_returns_richer_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        devonthink_tools,
        "_run_osascript",
        lambda script, args, *, tool_name, extra=None: (
            '{"uuid":"REC","id":1,"name":"Lecture.mp4","type":"multimedia",'
            '"record_type":"multimedia","kind":"MPEG-4 movie","mime_type":"video/mp4",'
            '"location":"/Inbox","url":null,"path":"/tmp/Lecture.mp4",'
            '"filename":"Lecture.mp4","size":1234,"duration":42.5}'
        ),
    )

    result = devonthink_tools.devonthink_get_record_by_uuid("REC")

    assert result["ok"] is True
    assert result["data"]["size"] == 1234
    assert result["data"]["duration"] == 42.5
    assert result["data"]["extension"] == "mp4"
    assert result["data"]["media_kind"] == "video"


def test_read_record_text_returns_plain_text_and_metadata(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, tool_name, extra))
        return (
            '{"record":{"uuid":"REC","name":"TODO.txt","type":"txt","record_type":"txt",'
            '"filename":"TODO.txt"},"text":"buy milk","text_length":8,"truncated":false}'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_read_record_text("REC", max_chars=100)

    assert result["ok"] is True
    assert result["text"] == "buy milk"
    assert result["text_length"] == 8
    assert result["record"]["extension"] == "txt"
    assert "plain text of theRecord" in calls[0][0]
    assert calls[0][1] == ["REC", "100"]


def test_search_media_records_uses_multimedia_record_type(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, tool_name, extra))
        return (
            '[{"uuid":"VIDEO","name":"clip.mov","type":"multimedia",'
            '"record_type":"multimedia","kind":"QuickTime movie","mime_type":"video/quicktime",'
            '"filename":"clip.mov","duration":12.5,"size":1000},'
            '{"uuid":"AUDIO","name":"voice.mp3","type":"multimedia",'
            '"record_type":"multimedia","kind":"MP3 audio","mime_type":"audio/mpeg",'
            '"filename":"voice.mp3","duration":8.0,"size":500}]'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_search_media_records(media_kind="video", limit=10)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["records"][0]["uuid"] == "VIDEO"
    assert "record type is multimedia" in calls[0][0]
    assert calls[0][1][0] == "200"


def test_filter_records_filters_search_results_by_extension(monkeypatch) -> None:
    def fake_run(script, args, *, tool_name, extra=None):
        assert tool_name == "devonthink-filter-records"
        assert args[0] == "search"
        assert args[1] == "invoice"
        return (
            '[{"uuid":"BOOK","name":"book.epub","type":"unknown","record_type":"unknown",'
            '"filename":"book.epub","mime_type":"application/epub+zip","size":10},'
            '{"uuid":"PDF","name":"invoice.pdf","type":"PDF document","record_type":"PDF document",'
            '"filename":"invoice.pdf","mime_type":"application/pdf","size":20}]'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_filter_records(query="invoice", file_extensions=["pdf"])

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["records"][0]["uuid"] == "PDF"
    assert result["records"][0]["size"] == 20


def test_filter_records_filters_exact_tags_without_label_search(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, extra))
        return (
            '[{"uuid":"A","name":"a","type":"txt","record_type":"txt","tags":["archived"]},'
            '{"uuid":"B","name":"b","type":"txt","record_type":"txt","tags":["active"]}]'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_filter_records(tags=["archived"])

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["records"][0]["uuid"] == "A"
    assert calls[0][1][0] == "all"
    assert "label:archived" not in calls[0][0]


def test_filter_records_filters_filename_contains(monkeypatch) -> None:
    def fake_run(script, args, *, tool_name, extra=None):
        return (
            '[{"uuid":"TODO","name":"TODO","type":"txt","record_type":"txt","filename":"TODO.txt"},'
            '{"uuid":"OTHER","name":"notes","type":"txt","record_type":"txt","filename":"notes.txt"}]'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_filter_records(file_extensions=["txt"], filename_contains="todo.txt")

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["records"][0]["uuid"] == "TODO"


def test_filter_records_uses_plain_text_for_rtf_phrase_search(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, extra))
        return (
            '[{"uuid":"RTF","name":"Meeting Notes","type":"rtf","record_type":"rtf",'
            '"filename":"Meeting Notes.rtf"}]'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_filter_records(
        query="meeting notes",
        record_types=["rtf"],
        content_mode="auto",
    )

    assert result["ok"] is True
    assert result["plain_text_filter"] is True
    assert calls[0][1][:6] == ["all", "", "meeting notes", "", "rtf", ""]
    assert "plain text of theRecord" in calls[0][0]


def test_filter_records_applies_date_and_dimension_filters(monkeypatch) -> None:
    def fake_run(script, args, *, tool_name, extra=None):
        return (
            '[{"uuid":"SMALL","name":"small.png","type":"picture","record_type":"picture",'
            '"filename":"small.png","width":800,"created_ts":1704067200},'
            '{"uuid":"WIDE","name":"wide.png","type":"picture","record_type":"picture",'
            '"filename":"wide.png","width":1600,"created_ts":1706745600}]'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_filter_records(
        file_extensions=["png"],
        min_width=1200,
        created_from="2024-01-01",
        created_to="2024-03-31",
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["records"][0]["uuid"] == "WIDE"


def test_batch_update_record_metadata_sets_tags_and_comment(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, tool_name, extra))
        return (
            '[{"uuid":"REC","name":"Item","type":"txt","record_type":"txt",'
            '"tags":["TODO"],"comment":"TODO","label":null,"rating":null}]'
        )

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_batch_update_record_metadata(
        ["REC"],
        tags=["TODO"],
        comment="TODO",
        comment_mode="append",
    )

    assert result["ok"] is True
    assert result["updated"] == 1
    assert result["records"][0]["tags"] == ["TODO"]
    assert calls[0][1][:9] == ["TODO", "true", "true", "TODO", "true", "append", "", "", "REC"]
    assert "set tags of theRecord" in calls[0][0]


def test_batch_update_record_metadata_requires_a_field() -> None:
    result = devonthink_tools.devonthink_batch_update_record_metadata(["REC"])

    assert result["ok"] is False
    assert "at least one metadata field" in result["error"]


def test_summarize_annotations_uses_required_records_keyword(monkeypatch) -> None:
    calls = []

    def fake_run(script, args, *, tool_name, extra=None):
        calls.append((script, args, tool_name, extra))
        return '{"uuid":"SUMMARY","name":"Annotations","type":"markdown"}'

    monkeypatch.setattr(devonthink_tools, "_run_osascript", fake_run)

    result = devonthink_tools.devonthink_summarize_annotations(["REC"], "DEST")

    assert result["ok"] is True
    assert result["data"]["uuid"] == "SUMMARY"
    assert calls[0][1] == ["DEST", "REC"]
    assert "summarize annotations of records theRecords to markdown in destinationGroup" in calls[0][0]


def test_summarize_annotations_missing_value_warns(monkeypatch) -> None:
    monkeypatch.setattr(
        devonthink_tools,
        "_run_osascript",
        lambda script, args, *, tool_name, extra=None: "null",
    )

    result = devonthink_tools.devonthink_summarize_annotations(["REC"], "DEST")

    assert result["ok"] is True
    assert result["data"] is None
    assert "missing_value" in result["observability"]["warnings"][0]
