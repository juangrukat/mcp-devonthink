#!/usr/bin/env bash
# Usage: ./scripts/benchmark_applescript.sh [group_uuid]
# Runs devonthink-link-traverse-folder against a target group and saves
# a benchmark artifact for DEVONthink version comparison.

set -euo pipefail

GROUP_UUID="${1:-${DEVONTHINK_LINK_BENCHMARK_GROUP_UUID:-}}"
DT_VERSION="$(osascript -l AppleScript -e 'tell application "DEVONthink" to version')"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%S")"
OUTDIR="benchmarks"
OUTFILE="${OUTDIR}/applescript_perf_baseline_DT${DT_VERSION}_${TIMESTAMP}.json"

mkdir -p "${OUTDIR}"

if [[ -z "${GROUP_UUID}" ]]; then
  GROUP_UUID="$(osascript -l AppleScript -e 'tell application "DEVONthink"' -e 'return uuid of incoming group' -e 'end tell')"
fi

echo "Running benchmark against group ${GROUP_UUID} on DEVONthink ${DT_VERSION}..."

export GROUP_UUID DT_VERSION TIMESTAMP OUTFILE

python3 - <<'PY'
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from app.tools.devonthink_link_tools import devonthink_link_traverse_folder

group_uuid = os.environ["GROUP_UUID"]
dt_version = os.environ["DT_VERSION"]
timestamp = os.environ["TIMESTAMP"]
outfile = os.environ["OUTFILE"]

start = time.perf_counter()
result = devonthink_link_traverse_folder(
    folder_ref=group_uuid,
    mode="shallow",
    write_snapshot=False,
    limit=200,
)
elapsed_ms = (time.perf_counter() - start) * 1000

obs = result.get("observability", {}) or {}
stats = obs.get("stats", {}) or {}
search_calls = int(stats.get("search_calls_made", 0) or 0)
processed = int((((result.get("data") or {}).get("traversal_meta") or {}).get("records_processed", 0) or 0))

artifact = {
    "dt_version": dt_version,
    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    "timestamp": timestamp,
    "group_uuid": group_uuid,
    "ok": bool(result.get("ok")),
    "records_processed": processed,
    "duration_ms": round(elapsed_ms, 2),
    "search_calls_made": search_calls,
    "avg_ms_per_call": round(elapsed_ms / max(search_calls, 1), 3),
    "search_calls_degraded": int(stats.get("search_calls_degraded", 0) or 0),
    "missing_value_coercions": int(stats.get("missing_value_coercions", 0) or 0),
    "tool_stats": stats,
}

Path(outfile).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
print(json.dumps(artifact, indent=2))
print(f"\nArtifact saved: {outfile}")
PY
