#!/usr/bin/env python3
"""Two-second read-only GVCP discovery, restricted to the configured camera MAC.

Run locally before updating MAXCAMERA_IP in deploy/maxcamera.env. This helper is
never available through the web API or from within the confined camera bridge.
"""
import json
from pathlib import Path
import socket
import struct
import time


def main():
    config = dict(line.split("=", 1) for line in (Path(__file__).resolve().parent / "maxcamera.env").read_text().splitlines()
                  if line and not line.startswith("#") and "=" in line)
    expected = config["MAXCAMERA_MAC"].lower()
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(.25)
        sock.sendto(struct.pack("!BBHHH", 0x42, 0x11, 2, 0, 517), ("255.255.255.255", 3956))
        end = time.monotonic() + 2
        while time.monotonic() < end:
            try:
                packet, peer = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if len(packet) != 256 or struct.unpack("!HHHH", packet[:8]) != (0, 3, 248, 517):
                continue
            data = packet[8:]
            mac = ":".join(f"{byte:02x}" for byte in data[10:16])
            if mac != expected:
                continue
            ip = socket.inet_ntoa(data[36:40])
            if ip != peer[0]:
                continue
            found[ip] = dict(ip=ip, mac=mac, firmware=data[136:168].split(b"\0")[0].decode("ascii", "replace"))
    if len(found) != 1:
        raise SystemExit(f"Expected one camera with MAC {expected}; discovered {len(found)}. No configuration changed.")
    print(json.dumps(next(iter(found.values()))))


if __name__ == "__main__":
    main()
