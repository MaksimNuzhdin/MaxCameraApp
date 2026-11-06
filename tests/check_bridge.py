#!/usr/bin/env python3
"""Bounded direct bridge check; use only when its web gateway is stopped.

The bridge accepts one IPC client. Use check_http_ai.py with a running preview.
No captured images or labels are saved.
"""
import argparse
import json
import socket
import struct
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", nargs="?", type=float, default=5)
    parser.add_argument("socket", nargs="?", default="run/ipc/bridge.sock")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        parser.error("seconds must be between 1 and 30")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10)
        sock.connect(args.socket)
        request_id = 0
        def exact(size):
            data = b""
            while len(data) < size:
                chunk = sock.recv(size - len(data))
                if not chunk:
                    raise ConnectionError("Bridge disconnected")
                data += chunk
            return data
        def receive():
            kind, head, blob = struct.unpack("!BII", exact(9))
            return kind, json.loads(exact(head)), exact(blob)
        def send(obj):
            wire = json.dumps(obj).encode()
            sock.sendall(struct.pack("!I", len(wire)) + wire)
        def call(method, **params):
            nonlocal request_id
            request_id += 1
            send(dict(id=request_id, method=method, params=params))
            while True:
                kind, header, _ = receive()
                if kind == 0 and header.get("id") == request_id:
                    assert header.get("ok"), header
                    return header["result"]
        status = call("status")
        assert not status["stream"] and not status["owned"] and status["camera"] == "free", "Camera must be idle/free"
        schema = call("schema")
        print("Features:", len(schema["features"]), "Categories:", len(schema["categories"]))
        frames = 0
        try:
            call("stream_start")
            end = time.monotonic() + args.seconds
            send({"method": "credit"})
            while time.monotonic() < end:
                kind, _, _ = receive()
                if kind == 1:
                    frames += 1
                    send({"method": "credit"})
        finally:
            call("stream_stop")
        status = call("status", probe=False)
        assert frames and not status["owned"] and not status["stream"], status
        print("PASS frames:", frames, "Camera released:", not status["owned"])


if __name__ == "__main__":
    main()
