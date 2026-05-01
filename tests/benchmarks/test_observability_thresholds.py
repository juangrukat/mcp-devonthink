from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


THRESHOLDS_MS = {
    "devonthink-link-find-orphans": 2_000,
    "devonthink-link-detect-bridges": 2_000,
    "devonthink-link-audit-folder": 6_000,
}


pytestmark = [
    pytest.mark.live_devonthink,
    pytest.mark.benchmark_live,
    pytest.mark.timeout(60),
]


def test_observability_report_thresholds(tmp_path: Path):
    report_path = tmp_path / "tool-observability-report.json"
    subprocess.run(
        [sys.executable, "scripts/audit_tool_observability.py", "--json-out", str(report_path)],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    payload = json.loads(report_path.read_text())
    by_tool = payload["by_tool"]
    for tool_name, max_ms in THRESHOLDS_MS.items():
        duration_ms = float(by_tool[tool_name]["duration_ms"])
        assert duration_ms < max_ms, f"{tool_name} exceeded threshold: {duration_ms}ms > {max_ms}ms"
