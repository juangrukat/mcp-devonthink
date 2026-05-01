from __future__ import annotations

from app.tools import devonthink_annotation_tools as annotation_tools


def test_create_annotation_note_attaches_note_record(monkeypatch) -> None:
    calls = []

    def fake_json(script, args, *, tool_name):
        calls.append((script, args, tool_name))
        return {
            "target": {"uuid": "PDF", "name": "Source.pdf", "type": "pdf"},
            "annotation": {"uuid": "NOTE", "name": "Annotation", "type": "txt"},
            "replaced": "false",
        }

    monkeypatch.setattr(annotation_tools, "_run_json", fake_json)

    result = annotation_tools.devonthink_create_annotation_note("PDF", "Annotation", "Body")

    assert result["ok"] is True
    assert result["data"]["annotation"]["uuid"] == "NOTE"
    assert calls[0][1] == ["PDF", "Annotation", "Body", "false"]
    assert "annotations group of targetDatabase" in calls[0][0]
    assert "set annotation of targetRecord to noteRecord" in calls[0][0]
    assert "PDF-internal highlights" in result["observability"]["warnings"][0]


def test_create_annotation_note_can_replace_existing_and_create_rtf(monkeypatch) -> None:
    calls = []

    def fake_json(script, args, *, tool_name):
        calls.append((script, args, tool_name))
        return {
            "target": {"uuid": "PDF"},
            "annotation": {"uuid": "NOTE", "type": "rtf"},
            "replaced": "true",
        }

    monkeypatch.setattr(annotation_tools, "_run_json", fake_json)

    result = annotation_tools.devonthink_create_annotation_note(
        "PDF",
        "Annotation",
        "Body",
        note_type="rtf",
        replace_existing=True,
    )

    assert result["ok"] is True
    assert calls[0][1] == ["PDF", "Annotation", "Body", "true"]
    assert "type:rtf" in calls[0][0]
    assert "rich text:noteContent" in calls[0][0]


def test_create_annotation_note_validates_note_type() -> None:
    result = annotation_tools.devonthink_create_annotation_note("PDF", "Annotation", "Body", note_type="pdf")

    assert result["ok"] is False
    assert "note_type" in result["error"]


def test_read_annotation_note_returns_json_data(monkeypatch) -> None:
    monkeypatch.setattr(
        annotation_tools,
        "_run_json",
        lambda script, args, *, tool_name: {
            "target": {"uuid": "PDF"},
            "annotation": {"uuid": "NOTE"},
            "plain_text": "Plain",
            "rich_text": "",
        },
    )

    result = annotation_tools.devonthink_read_annotation_note("PDF")

    assert result["ok"] is True
    assert result["data"]["annotation"]["uuid"] == "NOTE"
    assert result["data"]["plain_text"] == "Plain"


def test_annotation_catalog_distinguishes_pdf_internal_markup() -> None:
    entries = annotation_tools.annotation_tool_catalog_entries()
    descriptions = " ".join(entry["description"] for entry in entries)

    assert "attached annotation note" in descriptions
    assert "PDF-internal" in descriptions
    assert "smart groups" in descriptions
    assert "annotation-note records" in descriptions
    assert not any(("/Users/" + "kat") in entry["example"] for entry in entries)
