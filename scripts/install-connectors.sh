#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${DEVONTHINK_TOOL_PROFILE:-canonical}"
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-canonical}"
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

exec python3 "$ROOT_DIR/scripts/manage_connectors.py" install --profile "$PROFILE" "${ARGS[@]}"
