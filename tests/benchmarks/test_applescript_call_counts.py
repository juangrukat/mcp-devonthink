from __future__ import annotations

import pytest

from app.tools.applescript_counter import count_applescript_calls
from app.tools.devonthink_link_tools import (
    devonthink_link_audit_folder,
    devonthink_link_audit_record,
    devonthink_link_check_reciprocal,
    devonthink_link_detect_bridges,
    devonthink_link_find_orphans,
    devonthink_link_map_neighborhood,
    devonthink_link_score,
    devonthink_link_suggest_related,
)
from tests.test_scholar_corpus import GROUPS, RECORDS


pytestmark = [
    pytest.mark.live_devonthink,
    pytest.mark.benchmark_live,
    pytest.mark.timeout(30),
]


def test_find_orphans_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_find_orphans(GROUPS["root"], 100)
    assert result["ok"] is True
    assert counter.count <= 5, f"Too many Apple Event calls: {counter.count}"


def test_detect_bridges_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_detect_bridges(GROUPS["root"], 80)
    assert result["ok"] is True
    assert counter.count <= 5, f"Too many Apple Event calls: {counter.count}"


def test_audit_folder_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_audit_folder(GROUPS["concordance"], 50)
    assert result["ok"] is True
    assert counter.count <= 5, f"Too many Apple Event calls: {counter.count}"


def test_audit_record_authoritative_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_audit_record(RECORDS["c1_concordance_methods"], mode="authoritative")
    assert result["ok"] is True
    assert counter.count <= 2, f"Too many Apple Event calls: {counter.count}"


def test_suggest_related_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_suggest_related(RECORDS["c1_concordance_methods"], 15)
    assert result["ok"] is True
    assert counter.count <= 6, f"Too many Apple Event calls: {counter.count}"


def test_score_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_score([RECORDS["c1_concordance_methods"], RECORDS["c2_rare_term_weighting"]])
    assert result["ok"] is True
    assert counter.count <= 4, f"Too many Apple Event calls: {counter.count}"


def test_check_reciprocal_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_check_reciprocal(RECORDS["c1_concordance_methods"], RECORDS["c3_corpus_management"])
    assert result["ok"] is True
    assert counter.count <= 4, f"Too many Apple Event calls: {counter.count}"


def test_map_neighborhood_uses_bounded_apple_event_calls():
    with count_applescript_calls() as counter:
        result = devonthink_link_map_neighborhood(RECORDS["c1_concordance_methods"], 1, 20)
    assert result["ok"] is True
    assert counter.count <= 6, f"Too many Apple Event calls: {counter.count}"
