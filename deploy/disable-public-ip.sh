#!/bin/bash
# Closes the public port again. Remove the router's port-forward rule as well.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sed -i 's/^MAXCAMERA_LISTEN=.*/MAXCAMERA_LISTEN=127.0.0.1/' "$ROOT/deploy/maxcamera.env"
systemctl --user restart maxcamera-control-edge.service
sudo ufw delete allow 8080/tcp
echo "The HTTP port now listens on this computer only."
