#!/bin/bash
# Starts one MaxCamera Web component inside its bubblewrap sandbox.
#   sandbox-run.sh bridge | web | edge [port]
# Each sandbox sees /usr, the read-only application and only the directories it needs.
# Nothing from the home directory or /etc is visible.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Existing environment values win; direct local runs use the reviewed config too.
while IFS='=' read -r key value; do
  case "$key" in
    MAXCAMERA_IP|MAXCAMERA_MAC|MAXCAMERA_LISTEN)
      if [[ ! -v "$key" ]]; then export "$key=$value"; fi ;;
  esac
done < "$ROOT/deploy/maxcamera.env"
RUN="${MAXCAMERA_RUN:-$ROOT/run}"
mkdir -p "$RUN/ipc" "$RUN/state"
chmod 700 "$RUN" "$RUN/ipc" "$RUN/state"

CAMERA_IP="${MAXCAMERA_IP:-10.0.0.3}"
CAMERA_MAC="${MAXCAMERA_MAC:-02:00:00:00:00:01}"

common=(
  --unshare-user --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup
  --die-with-parent --new-session --clearenv
  --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin
  --proc /proc --dev /dev --tmpfs /tmp
  --ro-bind "$ROOT/backend" /app/backend
  --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PYTHONUNBUFFERED 1 --setenv HOME /tmp
)

case "${1:-}" in
  bridge)
    # Keeps the host network, because it must reach the camera; seccomp then
    # confines it to the one UDP conversation it opened at start-up.
    exec bwrap "${common[@]}" \
      --ro-bind "$ROOT/pydeps" /app/pydeps --setenv PYTHONPATH /app/pydeps:/app/backend \
      --setenv OPENBLAS_NUM_THREADS 1 --setenv OMP_NUM_THREADS 2 \
      --bind "$RUN/ipc" /ipc \
      /usr/bin/python3 /app/backend/bridge.py --camera-ip "$CAMERA_IP" --camera-mac "$CAMERA_MAC" \
      --xml /app/backend/data/camera.xml --gvsp-lib /app/backend/libgvsp_rx.so --socket /ipc/bridge.sock
    ;;
  web)
    # Precompressed copies are served automatically and keep slow tunnels usable.
    for file in index.html app.js ai.js ai-visuals.js style.css icon.svg; do
      gzip -9 -k -f -n "$ROOT/frontend/$file"
    done
    # No network interfaces at all: only the Unix sockets in /ipc lead outside.
    exec bwrap "${common[@]}" --unshare-net \
      --ro-bind "$ROOT/pydeps" /app/pydeps --setenv PYTHONPATH /app/pydeps:/app/backend \
      --ro-bind "$ROOT/frontend" /app/frontend \
      --bind "$RUN/ipc" /ipc --bind "$RUN/state" /state \
      /usr/bin/python3 /app/backend/web.py --seccomp --socket /ipc/web.sock --bridge /ipc/bridge.sock \
      --static /app/frontend --auth-file /state/auth.json
    ;;
  edge)
    # Needs the host network only to accept connections on its one port.
    exec bwrap "${common[@]}" \
      --setenv PYTHONPATH /app/backend \
      --bind "$RUN/ipc" /ipc \
      /usr/bin/python3 /app/backend/edge.py --listen "${MAXCAMERA_LISTEN:-0.0.0.0}" --port "${2:-8080}" --upstream /ipc/web.sock
    ;;
  *)
    echo "usage: $0 bridge|web|edge [port]" >&2
    exit 2
    ;;
esac
