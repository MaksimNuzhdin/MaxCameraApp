#!/bin/bash
# Isolated local development preview. Uses the same three production sandboxes,
# its own IPC/state/logs, and loopback only. Never starts or stops user services.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MAXCAMERA_RUN="$ROOT/run/ai-preview"
export MAXCAMERA_LISTEN=127.0.0.1
mkdir -p "$MAXCAMERA_RUN/log"
chmod 700 "$MAXCAMERA_RUN"
components=()
cleanup() {
  for pid in "${components[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  wait || true
}
trap cleanup EXIT INT TERM
"$ROOT/deploy/sandbox-run.sh" bridge > "$MAXCAMERA_RUN/log/bridge.log" 2>&1 &
components+=("$!")
sleep 1
"$ROOT/deploy/sandbox-run.sh" web > "$MAXCAMERA_RUN/log/web.log" 2>&1 &
components+=("$!")
sleep 1
"$ROOT/deploy/sandbox-run.sh" edge 8081 > "$MAXCAMERA_RUN/log/edge.log" 2>&1 &
components+=("$!")
echo 'Local preview: http://localhost:8081/ (separate local state; same sign-in flow). Ctrl+C stops and releases the camera.'
wait -n
