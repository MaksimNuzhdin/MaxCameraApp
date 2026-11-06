#!/bin/bash
# Publishes MaxCamera Web through a Cloudflare quick tunnel. Run it yourself and keep it running;
# press Ctrl+C to close public access. Cloudflare terminates HTTPS and can see the traffic it
# carries. The address changes each time this script starts.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCKET="$ROOT/run/ipc/web.sock"
BIN="$ROOT/vendor/cloudflared"
[ -S "$SOCKET" ] || { echo "The web service is not running: $SOCKET is missing." >&2; exit 1; }
if [ ! -x "$BIN" ]; then
  mkdir -p "$ROOT/vendor"
  echo "Downloading cloudflared from Cloudflare's GitHub releases..."
  curl -fL --proto '=https' -o "$BIN.tmp" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$BIN.tmp"
  mv "$BIN.tmp" "$BIN"
fi
"$BIN" --version
# Quick tunnels require --url. The local listener only forwards to the web process.
exec "$BIN" tunnel --no-autoupdate --url http://127.0.0.1:8080
