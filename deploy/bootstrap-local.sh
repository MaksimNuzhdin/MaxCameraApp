#!/bin/bash
# Prepare this checkout locally; no camera access, service/network changes or sudo.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Linux x86-64 is required by the current seccomp filter.' >&2; exit 1; }
for bin in gcc bwrap gzip; do command -v "$bin" >/dev/null || { echo "Missing dependency: $bin" >&2; exit 1; }; done
/usr/bin/python3 - <<'PY'
import sys
assert sys.version_info >= (3, 10), 'Python 3.10 or newer is required'
PY
/usr/bin/python3 -m pip install --target "$ROOT/pydeps" -r "$ROOT/requirements.txt"
gcc -O2 -std=c11 -Wall -Wextra -Werror -shared -fPIC backend/gvsp_rx.c -o backend/libgvsp_rx.so
[[ -f deploy/maxcamera.env ]] || cp deploy/maxcamera.env.example deploy/maxcamera.env
/usr/bin/python3 deploy/update-camera-source.py
printf '\nReady for configuration. Set your verified camera MAC/IP in deploy/maxcamera.env.\nRun deploy/discover-camera.py after setting the MAC, then deploy/preview-local.sh.\nNo service, firewall, router, camera or account settings were changed.\n'
