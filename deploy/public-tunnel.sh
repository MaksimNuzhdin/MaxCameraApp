#!/bin/bash
# Publishes MaxCamera Web through an HTTPS tunnel, with no router or firewall changes.
# Run it yourself and keep it running; press Ctrl+C to close public access.
# The tunnel provider (localhost.run) terminates HTTPS and can see the traffic it carries.
# No SSH keys are offered to the provider. The address can change on each reconnect.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCKET="$ROOT/run/ipc/web.sock"
mkdir -p "$ROOT/run/ssh"
chmod 700 "$ROOT/run/ssh"
[ -S "$SOCKET" ] || { echo "The web service is not running: $SOCKET is missing." >&2; exit 1; }

while true; do
  ssh -F /dev/null -o PubkeyAuthentication=no -o IdentitiesOnly=yes -o IdentityAgent=none \
      -o UserKnownHostsFile="$ROOT/run/ssh/known_hosts" -o StrictHostKeyChecking=accept-new \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
      -T -R "80:$SOCKET" nokey@localhost.run 2>&1 \
    | grep --line-buffered -E 'https://[a-zA-Z0-9.-]+\.(lhr\.life|localhost\.run)|denied|refused|rror' \
    | grep --line-buffered -v 'admin.localhost.run'
  echo "The tunnel closed. Reconnecting in 5 seconds; press Ctrl+C to stop."
  sleep 5
done
