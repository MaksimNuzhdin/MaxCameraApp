#!/bin/bash
# Opens MaxCamera Web on this computer's public address. Run it yourself: it asks for your
# sudo password and changes the firewall. Afterwards add one port-forward rule on the router.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8080
HOST_IP="$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')"

sudo ufw allow "$PORT/tcp" comment "MaxCamera Web"
sudo loginctl enable-linger "$USER"     # keeps the service running without a desktop login
sed -i 's/^MAXCAMERA_LISTEN=.*/MAXCAMERA_LISTEN=0.0.0.0/' "$ROOT/deploy/maxcamera.env"
systemctl --user restart maxcamera-control-edge.service
sleep 1
ss -ltn "sport = :$PORT"

cat <<MSG

This computer now accepts connections on port $PORT.
One step remains on the router (http://$(ip route | awk '/^default/{print $3; exit}')):
  Add a port-forward rule:  external TCP 80  ->  $HOST_IP port $PORT
  Reserve $HOST_IP for this computer in the router's DHCP settings.
Then open:  http://$(curl -s -m 8 https://api.ipify.org)/
MSG
