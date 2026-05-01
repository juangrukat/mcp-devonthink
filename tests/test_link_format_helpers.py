"""Unit tests for the link-format helpers and build-hub markdown output.

These tests do not hit DEVONthink. They cover the bug where build-hub used the
record UUID as link text (instead of the record name) and where descriptions
leaked the leading `#` from a markdown H1.
"""

from __future__ import annotations

from unittest.mock import patch

from app.tools import devonthink_link_tools as L
from app.tools.devonthink_link_tools import (
    AppleScriptExecutionError,
    _audit_record_impl,
    _brief_description,
    _bulk_get_edge_snapshots,
    _md_link,
    _resolve_title_candidates_batch,
    _set_plain_text,
    _TITLE_BATCH_CHUNK_SIZE,
    devonthink_link_build_hub,
)


def test_md_link_basic():
    assert _md_link("Rossi-2021", "x-devonthink-item://AAA") == "[Rossi-2021](x-devonthink-item://AAA)"


def test_md_link_escapes_brackets_in_name():
    assert _md_link("Foo [bar]", "x-devonthink-item://AAA") == r"[Foo [bar\]](x-devonthink-item://AAA)"


def test_md_link_escapes_paren_in_url():
    assert _md_link("Foo", "x-devonthink-item://AAA(bad)") == r"[Foo](x-devonthink-item://AAA(bad\))"


def test_md_link_falls_back_for_empty_name():
    assert _md_link("", "x-devonthink-item://AAA") == "[Untitled](x-devonthink-item://AAA)"


def test_brief_description_strips_heading_marker():
    assert _brief_description({}, "# Heading One\nbody") == "Heading One"
    assert _brief_description({}, "###   Spaced heading\nbody") == "Spaced heading"


def test_brief_description_skips_blank_lines():
    assert _brief_description({}, "\n\n  \nplain line\n") == "plain line"


def test_brief_description_falls_back_to_metadata():
    assert _brief_description({"type": "group", "location": "/foo/"}, "") == "group in /foo/"


def test_set_plain_text_refuses_rtf():
    with patch.object(L, "_get_record", return_value={"uuid": "AAA", "type": "rtf", "database_uuid": "DDD"}):
        try:
            _set_plain_text("AAA", "x")
        except AppleScriptExecutionError as exc:
            assert "rich_text_record_not_writable" in str(exc)
        else:
            raise AssertionError("expected AppleScriptExecutionError for rtf record")


def test_set_plain_text_refuses_rtfd():
    with patch.object(L, "_get_record", return_value={"uuid": "AAA", "type": "rtfd", "database_uuid": "DDD"}):
        try:
            _set_plain_text("AAA", "x")
        except AppleScriptExecutionError as exc:
            assert "rich_text_record_not_writable" in str(exc)
        else:
            raise AssertionError("expected AppleScriptExecutionError for rtfd record")


def test_set_plain_text_passes_through_for_markdown():
    with patch.object(L, "_get_record", return_value={"uuid": "BBB", "type": "markdown", "database_uuid": "DDD"}), \
         patch.object(L, "_run_osascript", return_value="") as mock_run:
        _set_plain_text("BBB", "plain")
        mock_run.assert_called_once()


def _capture_hub_body(mode: str) -> str:
    captured: dict[str, str] = {}

    def fake_get_record(ref):
        return {
            "uuid": ref,
            "name": f"Rec-{ref[:4]}",
            "reference_url": f"x-devonthink-item://{ref}",
            "tags": [],
            "database_read_only": False,
            "locked": False,
            "database_uuid": "DDD",
            "type": "markdown",
        }

    def fake_get_text(ref, max_chars=2000):
        return f"# Heading {ref[:4]}\nbody line"

    def fake_create(group_ref, note_name, body):
        captured["body"] = body
        return {"uuid": "HUB-UUID", "name": note_name}

    with patch.object(L, "_get_record", side_effect=fake_get_record), \
         patch.object(L, "_get_record_text", side_effect=fake_get_text), \
         patch.object(L, "_create_or_update_markdown_note", side_effect=fake_create), \
         patch.object(L, "_assert_record_writable", return_value=None):
        devonthink_link_build_hub(
            group_ref="GGG",
            seed_record_refs=["A8A798B5-8085-47F5-B72F-08D4D9C8C15C"],
            hub_name="_TestHub",
            mode=mode,
        )

    return captured["body"]


def test_build_hub_overview_uses_name_as_link_text():
    body = _capture_hub_body("overview")
    assert "[Rec-A8A7](x-devonthink-item://A8A798B5-8085-47F5-B72F-08D4D9C8C15C)" in body
    # Must NOT use the UUID as link text any more.
    assert "[A8A798B5-8085-47F5-B72F-08D4D9C8C15C](" not in body
    # H1 marker must be stripped from description column.
    assert "# Heading" not in body


def test_build_hub_index_renders_bullet_list():
    body = _capture_hub_body("index")
    assert "- [Rec-A8A7](x-devonthink-item://" in body


def test_build_hub_reading_list_renders_checklist():
    body = _capture_hub_body("reading-list")
    assert "- [ ] [Rec-A8A7](x-devonthink-item://" in body


def test_build_hub_topic_map_groups_by_tag():
    body = _capture_hub_body("topic-map")
    assert "## (untagged)" in body
    assert "- [Rec-A8A7](x-devonthink-item://" in body


# ---------------------------------------------------------------------------
# _resolve_title_candidates_batch (option 3: batched OR-search)
# ---------------------------------------------------------------------------


def test_resolve_title_candidates_batch_empty_returns_empty_buckets():
    # Should not call DEVONthink at all when there are no titles to resolve.
    with patch.object(L, "_search_records") as mock_search:
        result = _resolve_title_candidates_batch([])
    assert result == {}
    mock_search.assert_not_called()


def test_resolve_title_candidates_batch_all_blank_titles_skip_search():
    # Sanitized whitespace-only titles should not trigger a search; every input
    # title is still represented in the result with an empty hit list.
    with patch.object(L, "_search_records") as mock_search:
        result = _resolve_title_candidates_batch(["", "   ", "\t"])
    assert result == {"": [], "   ": [], "\t": []}
    mock_search.assert_not_called()


def test_resolve_title_candidates_batch_single_search_for_chunk():
    # Three titles should fan out into one OR-search rather than three separate
    # calls; results bucket to the title whose name matches. The query uses
    # boolean OR over quoted phrases — DEVONthink's `any:` prefix only honors
    # field-qualified atoms so we cannot use it for free-text title lookup.
    fake_hits = [
        {"uuid": "U1", "name": "Alpha"},
        {"uuid": "U2", "name": "Beta"},
        {"uuid": "U3", "name": "Gamma"},
    ]
    with patch.object(L, "_search_records", return_value=fake_hits) as mock_search:
        result = _resolve_title_candidates_batch(["Alpha", "Beta", "Gamma"])
    assert mock_search.call_count == 1
    args, kwargs = mock_search.call_args
    assert " OR " in args[0]
    assert '"Alpha"' in args[0] and '"Beta"' in args[0] and '"Gamma"' in args[0]
    assert kwargs.get("sanitize") is False
    # Each title gets exactly its matching record back.
    assert [r["uuid"] for r in result["Alpha"]] == ["U1"]
    assert [r["uuid"] for r in result["Beta"]] == ["U2"]
    assert [r["uuid"] for r in result["Gamma"]] == ["U3"]


def test_resolve_title_candidates_batch_quotes_every_atom():
    # Every atom is quoted so single-word and multi-word titles share the same
    # phrase-matching semantics under DEVONthink's boolean OR operator.
    with patch.object(L, "_search_records", return_value=[]) as mock_search:
        _resolve_title_candidates_batch(["Two Words", "Single"])
    query = mock_search.call_args[0][0]
    assert '"Two Words"' in query
    assert '"Single"' in query
    assert query.count(" OR ") == 1


def test_resolve_title_candidates_batch_exact_match_wins_over_substring():
    # When a hit's name exactly equals the title, prefer it and drop the wider
    # substring matches to mirror per-title `_resolve_title_candidates`.
    fake_hits = [
        {"uuid": "U1", "name": "Foo Bar Baz"},
        {"uuid": "U2", "name": "foo"},
    ]
    with patch.object(L, "_search_records", return_value=fake_hits):
        result = _resolve_title_candidates_batch(["foo"])
    assert [r["uuid"] for r in result["foo"]] == ["U2"]


def test_resolve_title_candidates_batch_substring_fallback_when_no_exact():
    # No record's name equals "foo" exactly, so the substring match comes back.
    fake_hits = [
        {"uuid": "U1", "name": "Foo Bar Baz"},
        {"uuid": "U2", "name": "Unrelated"},
    ]
    with patch.object(L, "_search_records", return_value=fake_hits):
        result = _resolve_title_candidates_batch(["foo"])
    assert [r["uuid"] for r in result["foo"]] == ["U1"]


def test_resolve_title_candidates_batch_chunks_large_inputs():
    # More than _TITLE_BATCH_CHUNK_SIZE titles must split into multiple search
    # calls so we don't exceed DEVONthink's query length limit.
    titles = [f"Title{i}" for i in range(_TITLE_BATCH_CHUNK_SIZE + 5)]
    with patch.object(L, "_search_records", return_value=[]) as mock_search:
        _resolve_title_candidates_batch(titles)
    assert mock_search.call_count == 2


def test_resolve_title_candidates_batch_falls_back_per_title_on_search_error():
    # When the batched OR-search fails (e.g., DEVONthink returns -50 Invalid
    # argument for an unusual query), the helper must not bubble the error;
    # it should retry each title individually so the caller still gets results.
    titles = ["Alpha", "Beta"]

    def fake_resolve_single(title, **kwargs):
        return [{"uuid": f"U-{title}", "name": title}]

    with patch.object(
        L,
        "_search_records",
        side_effect=AppleScriptExecutionError("Invalid argument"),
    ), patch.object(L, "_resolve_title_candidates", side_effect=fake_resolve_single) as mock_single:
        result = _resolve_title_candidates_batch(titles)
    assert mock_single.call_count == 2
    assert result["Alpha"][0]["uuid"] == "U-Alpha"
    assert result["Beta"][0]["uuid"] == "U-Beta"


def test_resolve_title_candidates_batch_dedups_repeated_titles():
    # Repeated titles share search work; their bucket entries are independent
    # but populated from the same single search call.
    fake_hits = [{"uuid": "U1", "name": "Same"}]
    with patch.object(L, "_search_records", return_value=fake_hits) as mock_search:
        result = _resolve_title_candidates_batch(["Same", "Same"])
    # Single sanitized form means the OR query has just one atom and no OR.
    assert mock_search.call_count == 1
    assert mock_search.call_args[0][0] == '"Same"'
    assert [r["uuid"] for r in result["Same"]] == ["U1"]


# ---------------------------------------------------------------------------
# _bulk_get_edge_snapshots (option 2: content_excerpt folded into snapshot)
# ---------------------------------------------------------------------------


def test_bulk_edge_snapshot_returns_content_excerpt_field():
    # The bulk script now returns content_excerpt alongside edges so callers
    # don't need a second osascript trip to read text. The Python wrapper just
    # forwards whatever the script returns; we mock _run_json directly.
    fake_payload = [
        {
            "record": {"uuid": "AAA-1", "name": "Note", "type": "markdown"},
            "outgoing_references": [],
            "incoming_references": [],
            "outgoing_wiki_references": [],
            "incoming_wiki_references": [],
            "content_excerpt": "# Note\nbody text with [[wiki]] link",
        }
    ]
    with patch.object(L, "_run_json", return_value=fake_payload):
        snapshots = _bulk_get_edge_snapshots(["AAA-1"])
    assert "AAA-1" in snapshots
    assert snapshots["AAA-1"]["content_excerpt"].startswith("# Note")


def test_audit_record_impl_uses_snapshot_excerpt_without_extra_text_call():
    # _audit_record_impl should consume content_excerpt from the snapshot and
    # never invoke _get_record_text — that call cost is now zero on the audit
    # hot path.
    fake_snapshot = {
        "record": {
            "uuid": "AAA-2",
            "name": "Note",
            "type": "markdown",
            "tags": ["x"],
            "aliases": [],
            "comment": "",
            "database_uuid": "DDD",
        },
        "outgoing_references": [],
        "incoming_references": [],
        "outgoing_wiki_references": [],
        "incoming_wiki_references": [],
        "content_excerpt": "see [[Other Note]] and x-devonthink-item://BBBBBBBB-1111-2222-3333-444444444444",
    }
    with patch.object(L, "_get_record_edge_snapshot", return_value=fake_snapshot), \
         patch.object(L, "_get_record_text") as mock_text, \
         patch.object(L, "_get_links_of", return_value=[]), \
         patch.object(L, "_search_records", return_value=[]), \
         patch.object(L, "_get_record", return_value=fake_snapshot["record"]):
        audit, warnings, _obs = _audit_record_impl("AAA-2", include_text_scan=True)
    mock_text.assert_not_called()
    # The wikilink and item-link extracted from the cached excerpt should show
    # up in text_scan output, proving the snapshot text fed the scanner.
    assert "Other Note" in audit["text_scan"]["wikilinks"]
    assert any("BBBBBBBB" in link for link in audit["text_scan"]["item_links"])


def test_audit_record_impl_skips_text_scan_when_disabled():
    # include_text_scan=False should leave text_scan empty regardless of any
    # excerpt the snapshot happens to carry.
    fake_snapshot = {
        "record": {
            "uuid": "AAA-3",
            "name": "Note",
            "type": "markdown",
            "tags": [],
            "aliases": [],
            "comment": "",
            "database_uuid": "DDD",
        },
        "outgoing_references": [{"uuid": "CCC", "name": "Target", "reference_url": "x-devonthink-item://CCC"}],
        "incoming_references": [],
        "outgoing_wiki_references": [],
        "incoming_wiki_references": [],
        "content_excerpt": "would-be scanned text",
    }
    with patch.object(L, "_get_record_edge_snapshot", return_value=fake_snapshot), \
         patch.object(L, "_get_record_text") as mock_text, \
         patch.object(L, "_get_record", return_value=fake_snapshot["record"]):
        audit, _warnings, _obs = _audit_record_impl("AAA-3", include_text_scan=False)
    mock_text.assert_not_called()
    assert audit["text_scan"]["wikilinks"] == []
    assert audit["text_scan"]["item_links"] == []
    # Authoritative outgoing reference should still produce an outgoing edge.
    assert audit["edges"]["counts"]["outgoing"] == 1
