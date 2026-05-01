from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("pytest_benchmark") is None:
    pytest.skip("pytest-benchmark is not installed.", allow_module_level=True)

from app.tools.devonthink_link_tools import (
    devonthink_link_audit_folder,
    devonthink_link_detect_bridges,
    devonthink_link_find_orphans,
)
from tests.test_scholar_corpus import GROUPS


pytestmark = [
    pytest.mark.live_devonthink,
    pytest.mark.benchmark_live,
    pytest.mark.timeout(30),
]


def test_find_orphans_perf(benchmark):
    result = benchmark(devonthink_link_find_orphans, GROUPS["root"], 100)
    assert result["ok"] is True


def test_detect_bridges_perf(benchmark):
    result = benchmark(devonthink_link_detect_bridges, GROUPS["root"], 80)
    assert result["ok"] is True


def test_audit_folder_perf(benchmark):
    result = benchmark(devonthink_link_audit_folder, GROUPS["concordance"], 50)
    assert result["ok"] is True
