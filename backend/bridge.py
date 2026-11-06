#!/usr/bin/env python3
"""Camera bridge: the only process allowed to talk to the camera.

It listens on a Unix socket and accepts a closed set of commands. The camera
address is fixed at start-up and never comes from a request; feature writes pass
through policy.py. All UDP sockets are opened before the seccomp lockdown, after
which this process cannot create or re-point an IP socket.

Camera etiquette: nothing touches the camera until a client asks. Control is
taken for the shortest possible time and released right after, a controller
already holding the camera is never displaced, and on stop every stream
register is cleared.
"""
import argparse
import ctypes
import json
import logging
import os
import signal
import socket
import struct
import threading
import time
from pathlib import Path

import decode
import ai
import genapi
import policy
import sandbox

log = logging.getLogger("bridge")

CCP, HEARTBEAT = 0xA00, 0x938
SCP, SCPS, SCPD, SCDA = 0xD00, 0xD04, 0xD08, 0xD18
GVCP_PORT = 3956
LEASE_IDLE_SECONDS = 1.5
HEARTBEAT_SECONDS = 1.0
STATUS_NAMES = {0x8001: "not implemented", 0x8002: "invalid parameter", 0x8003: "invalid address",
                0x8004: "write protected", 0x8005: "bad alignment", 0x8006: "access denied",
                0x8007: "camera busy", 0x800D: "camera error", 0x8FFF: "camera error"}


class CameraBusy(Exception):
    pass


class CameraOffline(Exception):
    pass


class CommandError(Exception):
    pass


class GvcpStatus(Exception):
    def __init__(self, opcode, status):
        super().__init__(f"{STATUS_NAMES.get(status, 'camera error')} (0x{status:04x})")
        self.status = status


class Gvcp:
    """GVCP client on one connected UDP socket, safe to use from several threads."""

    def __init__(self, sock):
        self.sock = sock
        self.sock.settimeout(0.25)
        self.request = 0
        self.lock = threading.Lock()

    def command(self, opcode, body=b""):
        with self.lock:
            self.request = self.request % 65535 + 1
            rid = self.request
            wire = struct.pack("!BBHHH", 0x42, 1, opcode, len(body), rid) + body
            for _ in range(4):
                try:
                    self.sock.send(wire)
                except OSError as error:
                    raise CameraOffline(str(error))
                until = time.monotonic() + 0.3
                while time.monotonic() < until:
                    try:
                        ack = self.sock.recv(2048)
                    except socket.timeout:
                        break
                    except OSError as error:
                        raise CameraOffline(str(error))
                    if len(ack) < 8:
                        continue
                    status, code, length, ack_id = struct.unpack("!HHHH", ack[:8])
                    if ack_id != rid or code != opcode + 1:
                        continue
                    if status:
                        raise GvcpStatus(opcode, status)
                    return ack[8:8 + length]
            raise CameraOffline("The camera does not answer")

    def read_regs(self, addresses):
        data = self.command(0x80, b"".join(struct.pack("!I", a) for a in addresses))
        return list(struct.unpack(f"!{len(addresses)}I", data[:4 * len(addresses)]))

    def write_reg(self, address, value):
        self.command(0x82, struct.pack("!II", address, value & 0xffffffff))

    def read_mem(self, address, length):
        out = b""
        for at in range(0, length, 512):
            size = min(512, ((length - at + 3) // 4) * 4)
            out += self.command(0x84, struct.pack("!IHH", address + at, 0, size))[4:]
        return out[:length]


class CachedPort:
    """Register view for one evaluation pass; bulk reads keep the packet count low."""

    def __init__(self, gvcp):
        self.gvcp = gvcp
        self.cache = {}

    def prefetch(self, addresses):
        for at in range(0, len(addresses), 128):
            chunk = addresses[at:at + 128]
            try:
                self.cache.update(zip(chunk, self.gvcp.read_regs(chunk)))
            except GvcpStatus:
                for address in chunk:
                    try:
                        self.cache[address] = self.gvcp.read_regs([address])[0]
                    except GvcpStatus:
                        self.cache[address] = None

    def read_reg(self, address):
        if address not in self.cache:
            try:
                self.cache[address] = self.gvcp.read_regs([address])[0]
            except GvcpStatus:
                self.cache[address] = None
        value = self.cache[address]
        if value is None:
            raise genapi.ReadFailed(f"register 0x{address:x} is unavailable")
        return value

    def write_reg(self, address, value):
        self.gvcp.write_reg(address, value)
        self.cache.pop(address, None)

    def invalidate(self):
        self.cache.clear()

    def read_mem(self, address, length):
        try:
            return self.gvcp.read_mem(address, length)
        except GvcpStatus as error:
            raise genapi.ReadFailed(str(error))


class GvspLib:
    class Info(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint32) for n in ("block", "payload_type", "pixel_format", "width", "height",
                                                   "bytes", "expected", "received", "missing")] + \
                   [(n, ctypes.c_uint64) for n in ("timestamp", "total_packets", "total_foreign")]

    def __init__(self, path):
        lib = ctypes.CDLL(path)
        lib.gvsp_open.restype = ctypes.c_void_p
        lib.gvsp_open.argtypes = [ctypes.c_int, ctypes.c_uint32]
        lib.gvsp_configure.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        lib.gvsp_next.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p]
        lib.gvsp_close.argtypes = [ctypes.c_void_p]
        self.lib = lib


class Bridge:
    def __init__(self, args):
        self.camera_ip = args.camera_ip
        self.control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.control.connect((args.camera_ip, GVCP_PORT))
        self.host_ip = self.control.getsockname()[0]
        self.stream_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stream_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        self.stream_sock.bind((self.host_ip, 0))
        self.stream_port = self.stream_sock.getsockname()[1]
        self.gvcp = Gvcp(self.control)
        self.port = CachedPort(self.gvcp)
        self.device = genapi.Device(Path(args.xml).read_bytes(), self.port)
        self.schema = self._build_schema()
        self.mac = args.camera_mac
        self.identity = None

        self.gvsp = GvspLib(args.gvsp_lib).lib
        self.rx = self.gvsp.gvsp_open(self.stream_sock.fileno(),
                                      struct.unpack("=I", socket.inet_aton(args.camera_ip))[0])

        self.state_lock = threading.RLock()
        self.owned = False
        self.lease_until = 0.0
        self.stream_on = False
        self.stream_paused = False
        self.stream_error = None
        self.saved_scpd = None
        self.camera_state = "unknown"
        self.last_probe = 0.0
        self.last_values = {}

        self.frame_lock = threading.Condition()
        self.frame = None
        self.frame_seq = 0
        self.sent_seq = 0
        self.credits = 0
        self.encoding_buffer = None
        self.preview = {"max_width": 1280, "quality": 75, "max_fps": 30}
        self.stats = {}
        self.rx_thread = None

        self.client = None
        self.client_lock = threading.Lock()
        threading.Thread(target=self._keeper, daemon=True).start()
        threading.Thread(target=self._encoder, daemon=True).start()

    # ---- schema -----------------------------------------------------------
    def _build_schema(self):
        features, order = {}, []
        for category, names in self.device.categories:
            shown = []
            for name in names:
                if name not in self.device.nodes or name in policy.HIDDEN:
                    continue
                if name not in features:
                    info = self.device.describe(name)
                    if info is None:
                        continue
                    info["writable"] = info["access"] != "RO" and policy.writable(name)
                    info["confirm"] = policy.CONFIRM.get(name, "")
                    features[name] = info
                shown.append(name)
            if shown:
                order.append({"name": category, "features": shown})
        return {"features": features, "categories": order}

    # ---- control lease ----------------------------------------------------
    def _verify_identity(self):
        """Refuses control if a different device has taken over the camera's address."""
        high, low = self.gvcp.read_regs([0x8, 0xC])
        mac = ":".join(f"{b:02x}" for b in struct.pack("!HI", high & 0xffff, low))
        if mac != self.mac:
            raise CommandError(f"The device at {self.camera_ip} is not the configured camera")

    def _acquire(self):
        with self.state_lock:
            if self.owned:
                self.lease_until = time.monotonic() + LEASE_IDLE_SECONDS
                return
            try:
                self._verify_identity()
                ccp = self.gvcp.read_regs([CCP])[0]
                if ccp & 3:
                    self.camera_state = "busy"
                    raise CameraBusy("Another application controls the camera. Try again when it is released.")
                self.gvcp.write_reg(CCP, 2)
            except GvcpStatus as error:
                self.camera_state = "busy"
                raise CameraBusy("Another application controls the camera. Try again when it is released.") from error
            self.owned = True
            self.camera_state = "controlled"
            self.lease_until = time.monotonic() + LEASE_IDLE_SECONDS
            log.info("control acquired")

    def _release(self):
        with self.state_lock:
            if not self.owned:
                return
            try:
                self.gvcp.write_reg(CCP, 0)
                log.info("control released")
            except (GvcpStatus, CameraOffline) as error:
                log.warning("release reported %s; the heartbeat timeout frees the camera", error)
            self.owned = False
            self.camera_state = "free"

    def _keeper(self):
        """Heartbeat while holding control, then release as soon as the lease goes idle."""
        while True:
            time.sleep(HEARTBEAT_SECONDS / 2)
            with self.state_lock:
                if not self.owned:
                    continue
                if not self.stream_on and not self.stream_paused and time.monotonic() > self.lease_until:
                    self._release()
                    continue
                try:
                    self.gvcp.read_regs([CCP])
                except (GvcpStatus, CameraOffline) as error:
                    log.warning("heartbeat failed: %s", error)
                    self._lost_control(str(error))

    def _lost_control(self, reason):
        self.owned = False
        self.camera_state = "unknown"
        if self.stream_on:
            self.stream_on = False
            self.stream_error = f"Connection to the camera was lost: {reason}"
            self._notify({"event": "stream", "running": False, "error": self.stream_error})

    # ---- values -----------------------------------------------------------
    def values(self, names=None):
        with self.state_lock:
            self.port.invalidate()
            self.port.prefetch(self.device.readable_addresses(names or list(self.schema["features"])))
            out = {}
            for name in names or self.schema["features"]:
                info = self.schema["features"].get(name)
                if info is None:
                    continue
                try:
                    out[name] = self.device.state(name)
                except (genapi.ReadFailed, KeyError, ZeroDivisionError, ValueError) as error:
                    out[name] = {"available": False, "locked": True, "value": None, "error": str(error)}
            self.port.invalidate()
            self.last_values = out
            return out

    def status(self, probe=True):
        with self.state_lock:
            if probe and not self.owned and time.monotonic() - self.last_probe > 5:
                self.last_probe = time.monotonic()
                try:
                    ccp = self.gvcp.read_regs([CCP])[0]
                    self.camera_state = "busy" if ccp & 3 else "free"
                except GvcpStatus:
                    self.camera_state = "busy"
                except CameraOffline:
                    self.camera_state = "offline"
            return {"camera": self.camera_state, "owned": self.owned, "stream": self.stream_on,
                    "error": self.stream_error, "stats": self.stats, "preview": self.preview}

    # ---- writes -----------------------------------------------------------
    def _coerce(self, name, value):
        info = self.schema["features"].get(name)
        if info is None or not policy.writable(name) or info["access"] == "RO":
            raise CommandError(f"{name} cannot be changed from this application")
        kind = info["kind"]
        if kind == "enum":
            return self.device.enum_value(name, str(value))
        if kind == "bool":
            return bool(value)
        if kind in ("int", "float"):
            number = float(value)
            if number != number or abs(number) > 1e12:
                raise CommandError(f"{name}: invalid number")
            return int(round(number)) if kind == "int" else number
        if kind == "command":
            return 1
        raise CommandError(f"{name} cannot be changed from this application")

    def _write_one(self, name, value):
        info = self.schema["features"][name]
        state = self.device.state(name)
        if name in policy.AI_SETTINGS:
            if not self.owned or not self.stream_on:
                raise CommandError("Start the stream before changing AI settings; AI enablement is cleared on control release")
            if name == "MaxAIEnabledFeatures":
                supported = self.device.value("MaxAISupportedFeatures")
                if value and (not self.device.value("MaxAIAvailable") or value & ~supported):
                    raise CommandError("The AI worker does not support that selection")
                if value == 128:
                    raise CommandError("Tracking requires at least one detector")
            if state.get("value") == value:
                return  # Repeated clicks must not invalidate the current generation.
        if not state["available"]:
            raise CommandError(f"{name} is not available in the current configuration")
        if info["kind"] in ("int", "float"):
            low, high = state.get("min"), state.get("max")
            if low is not None and value < low or high is not None and value > high:
                raise CommandError(f"{name} must be between {low} and {high}")
        if state["locked"]:
            raise policy.Locked(name)
        if info["kind"] == "command":
            self.device.set_value(name, 1)
        else:
            self.device.set_value(name, value)
        self.port.invalidate()

    def apply(self, items, restart=True):
        """items: [[name, value], ...] in order. Pauses the stream if a value needs it."""
        if not isinstance(items, list) or not items or len(items) > 5000:
            raise CommandError("Send between 1 and 5000 values")
        parsed = [(str(n), self._coerce(str(n), v)) for n, v in items]
        with self.state_lock:
            if any(n in policy.AI_SETTINGS for n, _ in parsed) and not self.stream_on:
                raise CommandError("Start the stream before changing AI settings")
            self._acquire()
            paused = False
            done, error = 0, None
            try:
                for name, value in parsed:
                    self.lease_until = time.monotonic() + LEASE_IDLE_SECONDS
                    try:
                        try:
                            self._write_one(name, value)
                        except (policy.Locked, GvcpStatus) as blocked:
                            if not (self.stream_on and restart and not paused):
                                raise
                            self._pause_stream()
                            paused = True
                            self._write_one(name, value)
                        done += 1
                    except policy.Locked as blocked:
                        error = f"{blocked} is locked in the current state"
                        break
                    except (GvcpStatus, CommandError, genapi.ReadFailed, ZeroDivisionError) as failure:
                        error = f"{name}: {failure}"
                        break
            finally:
                if paused:
                    self._resume_stream()
            self.lease_until = time.monotonic() + LEASE_IDLE_SECONDS
            values = self.values()
        return {"applied": done, "error": error, "restarted": paused, "values": values}

    # ---- stream -----------------------------------------------------------
    def _named(self, name):
        return self.device.value(name)

    def _wait_idle(self, seconds=25):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.port.invalidate()
            try:
                if not self._named("SensorBusy"):
                    return
            except genapi.ReadFailed:
                return
            time.sleep(0.02)

    def _program_stream(self):
        payload = self._named("PayloadSize")
        self.port.invalidate()
        self.saved_scpd = self.gvcp.read_regs([SCPD])[0]
        self.gvcp.write_reg(SCDA, struct.unpack("!I", socket.inet_aton(self.host_ip))[0])
        self.gvcp.write_reg(SCP, self.stream_port)
        self.gvcp.write_reg(SCPS, 1500)
        self.gvcp.write_reg(SCPD, self._pacing(payload))
        self.gvsp.gvsp_configure(self.rx, payload, 1464)
        self.payload = payload
        self.device.set_value("TLParamsLocked", 1)
        self.port.invalidate()

    def _pacing(self, payload):
        """Spaces packets so the host socket buffer never overflows, within the link budget."""
        if self.saved_scpd:
            return self.saved_scpd
        try:
            fps = max(float(self._named("ResultingFrameRate")), 1.0)
        except (genapi.ReadFailed, ValueError):
            fps = 15.0
        budget = 1e6 * 1464 / max(payload * fps * 1.15, 1)
        return int(max(0, min(20, budget - 12)))

    def _start_acquisition(self):
        self.device.set_value("AcquisitionStart", 1)
        self.port.invalidate()

    def _stop_acquisition(self):
        try:
            self.device.set_value("AcquisitionStop", 1)
            self.port.invalidate()
            self._wait_idle()
            self.device.set_value("TLParamsLocked", 0)
            self.port.invalidate()
        except (GvcpStatus, CameraOffline, genapi.ReadFailed) as error:
            log.warning("stop reported %s", error)

    def _clear_stream_registers(self):
        for address, value in ((SCDA, 0), (SCP, 0), (SCPD, self.saved_scpd or 0)):
            try:
                self.gvcp.write_reg(address, value)
            except (GvcpStatus, CameraOffline) as error:
                log.warning("clearing 0x%x reported %s", address, error)
        self.saved_scpd = None

    def _pause_stream(self):
        self.stream_paused = True
        self.stream_on = False
        self._join_rx()
        self._stop_acquisition()
        self._clear_stream_registers()
        self._notify({"event": "stream", "running": False, "paused": True})

    def _resume_stream(self):
        try:
            self._start_stream_locked()
        except (GvcpStatus, CameraOffline, genapi.ReadFailed) as error:
            self.stream_error = f"The stream did not restart: {error}"
            self._notify({"event": "stream", "running": False, "error": self.stream_error})
            self._stop_stream_locked()
        finally:
            self.stream_paused = False

    def _start_stream_locked(self):
        self._program_stream()
        self.stream_on = True
        self.stream_error = None
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()
        self._start_acquisition()
        self._notify({"event": "stream", "running": True})

    def _join_rx(self):
        thread = self.rx_thread
        self.rx_thread = None
        if thread is not None:
            thread.join(timeout=2)

    def _stop_stream_locked(self):
        self.stream_on = False
        self._join_rx()
        self._stop_acquisition()
        self._clear_stream_registers()
        self._release()
        with self.frame_lock:
            self.frame = None
            self.stats = {}
        self._notify({"event": "stream", "running": False})

    def stream_start(self):
        with self.state_lock:
            if self.stream_on:
                return {"running": True}
            self._acquire()
            try:
                self._start_stream_locked()
            except (GvcpStatus, CameraOffline, genapi.ReadFailed) as error:
                self._stop_stream_locked()
                raise CommandError(f"The stream did not start: {error}")
            return {"running": True}

    def stream_stop(self):
        with self.state_lock:
            if self.owned or self.stream_on:
                self._stop_stream_locked()
            return {"running": False}

    def _rx_loop(self):
        """Receives frames and keeps only the latest; slow clients simply skip frames."""
        info = GvspLib.Info()
        capacity = self.payload
        # Three buffers rotate without a Python-side copy: a large frame copied under the
        # GIL would stall this thread long enough to overflow the socket buffer.
        pool = [bytearray(capacity) for _ in range(3)]
        addresses = [ctypes.addressof(ctypes.c_char.from_buffer(b)) for b in pool]
        current = 0
        times, received, damaged, dropped = [], 0, 0, 0
        silent_since = time.monotonic()
        while self.stream_on:
            result = self.gvsp.gvsp_next(self.rx, addresses[current], capacity, 200, ctypes.byref(info))
            now = time.monotonic()
            if result < 0:
                self.stream_error = f"Receive error {-result}"
                break
            if result == 0:
                if now - silent_since > 8 and not self.stream_paused:
                    self.stats = dict(self.stats, stalled=True)
                continue
            silent_since = now
            jpeg = info.payload_type == 6
            if (jpeg and info.missing) or (not jpeg and info.missing > info.expected // 8):
                dropped += 1
                continue
            received += 1
            damaged += 1 if info.missing else 0
            times.append(now)
            times = [t for t in times if now - t < 2]
            with self.frame_lock:
                self.frame = {"data": memoryview(pool[current])[:info.bytes], "width": info.width, "height": info.height,
                              "format": info.pixel_format, "jpeg": jpeg, "block": info.block,
                              "missing": info.missing, "buffer": current}
                current = next(i for i in range(3) if i not in (current, self.encoding_buffer))
                self.frame_seq += 1
                self.stats = {"fps": round((len(times) - 1) / max(now - times[0], 0.001), 2) if len(times) > 1 else 0,
                              "frames": received, "partial": damaged, "dropped": dropped,
                              "width": info.width, "height": info.height,
                              "bitrate": round((len(times) * info.bytes * 8 / 2) / 1e6, 1)}
                self.frame_lock.notify_all()

    def _encoder(self):
        """Encodes a frame only when a client has room for one."""
        import cv2
        import numpy as np
        last = 0.0
        while True:
            with self.frame_lock:
                while not (self.credits > 0 and self.frame is not None and self.frame_seq != self.sent_seq):
                    self.frame_lock.wait()
                frame, seq = self.frame, self.frame_seq
                self.sent_seq = seq
                self.encoding_buffer = frame["buffer"]
                self.credits -= 1
                preview = dict(self.preview)
            wait = 1 / max(preview["max_fps"], 1) - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            try:
                if frame["jpeg"]:
                    data = bytes(frame["data"])
                    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    if image is None:
                        raise ValueError("The camera sent an unreadable JPEG frame")
                    h, w = image.shape[:2]
                    if preview["max_width"] and w > preview["max_width"]:
                        data, w, h = decode.preview_jpeg(image, preview["max_width"], preview["quality"])
                else:
                    image = decode.to_bgr(frame["data"], frame["width"], frame["height"], frame["format"])
                    data, w, h = decode.preview_jpeg(image, preview["max_width"], preview["quality"])
                self._send_frame({"w": w, "h": h, "src_w": frame["width"] or w, "src_h": frame["height"] or h,
                                  "partial": frame["missing"], "stats": self.stats}, data)
            except ValueError as error:
                with self.frame_lock:
                    self.credits += 1
                    self.encoding_buffer = None
                self._notify({"event": "decode_error", "message": str(error)})
                time.sleep(0.5)

    def capture(self, fmt):
        """Full-resolution snapshot of the newest frame."""
        import cv2
        import base64
        with self.frame_lock:
            frame = self.frame and dict(self.frame, data=bytes(self.frame["data"]))
        if frame is None:
            raise CommandError("Start the stream to take a snapshot")
        if frame["jpeg"]:
            data = frame["data"]
            kind = "jpeg"
        else:
            image = decode.to_bgr(frame["data"], frame["width"], frame["height"], frame["format"])
            ok, encoded = cv2.imencode(".png" if fmt == "png" else ".jpg", image,
                                       [cv2.IMWRITE_PNG_COMPRESSION, 1] if fmt == "png" else [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                raise CommandError("The snapshot could not be encoded")
            data, kind = encoded.tobytes(), "png" if fmt == "png" else "jpeg"
        return {"kind": kind, "data": base64.b64encode(data).decode()}

    def set_preview(self, max_width=None, quality=None, max_fps=None):
        with self.frame_lock:
            if max_width is not None:
                self.preview["max_width"] = max(0, min(int(max_width), 2592))
            if quality is not None:
                self.preview["quality"] = max(20, min(int(quality), 95))
            if max_fps is not None:
                self.preview["max_fps"] = max(1, min(int(max_fps), 60))
            return dict(self.preview)

    # ---- transport to the web process -------------------------------------
    def _send(self, kind, header, blob=b""):
        head = json.dumps(header, separators=(",", ":")).encode()
        wire = struct.pack("!BII", kind, len(head), len(blob)) + head + blob
        with self.client_lock:
            if self.client is None:
                return
            try:
                self.client.sendall(wire)
            except OSError:
                self.client = None

    def _notify(self, event):
        self._send(0, event)

    def _send_frame(self, header, blob):
        self._send(1, header, blob)

    def dispatch(self, method, params):
        if method == "ai_results":
            with self.state_lock:
                try:
                    return ai.snapshot(self)
                except (GvcpStatus, genapi.ReadFailed, CameraOffline) as error:
                    return {"state": "error", "message": f"AI read failed: {error}", "result": None,
                            "diagnostics": {}, "max_age_us": ai.MAX_AGE_US}
        if method == "schema":
            return self.schema
        if method == "values":
            names = params.get("names")
            return self.values(names if isinstance(names, list) else None)
        if method == "status":
            return self.status(bool(params.get("probe", True)))
        if method == "apply":
            return self.apply(params.get("items"), bool(params.get("restart", True)))
        if method == "stream_start":
            return self.stream_start()
        if method == "stream_stop":
            return self.stream_stop()
        if method == "preview":
            return self.set_preview(params.get("max_width"), params.get("quality"), params.get("max_fps"))
        if method == "capture":
            return self.capture("png" if params.get("format") == "png" else "jpeg")
        if method == "telemetry":
            return self.telemetry()
        raise CommandError("Unknown command")

    def telemetry(self):
        """Refreshes values during streaming; latches FPGA statistics for a coherent read."""
        with self.state_lock:
            if self.stream_on:
                try:
                    self.device.set_value("FpgaStatisticsLatch", 1)
                except (GvcpStatus, genapi.ReadFailed, KeyError):
                    pass
            return self.values()

    def _handle_request(self, request):
        rid = request.get("id")
        method = request.get("method")
        if method == "credit":
            with self.frame_lock:
                self.credits = min(self.credits + 1, 4)
                self.frame_lock.notify_all()
            return
        try:
            result = {"id": rid, "ok": True, "result": self.dispatch(str(method), request.get("params") or {})}
        except CameraBusy as error:
            result = {"id": rid, "ok": False, "code": "busy", "error": str(error)}
        except CameraOffline as error:
            with self.state_lock:
                self.camera_state = "offline"
            result = {"id": rid, "ok": False, "code": "offline", "error": f"Camera unreachable: {error}"}
        except (CommandError, GvcpStatus, genapi.ReadFailed, ValueError, TypeError) as error:
            result = {"id": rid, "ok": False, "code": "error", "error": str(error)}
        except Exception:
            log.exception("command failed")
            result = {"id": rid, "ok": False, "code": "error", "error": "Internal bridge error"}
        self._send(0, result)

    def serve(self, listener):
        pool_sem = threading.Semaphore(8)

        def run(request):
            try:
                self._handle_request(request)
            finally:
                pool_sem.release()

        while True:
            conn, _ = listener.accept()
            log.info("web process connected")
            with self.client_lock:
                if self.client is not None:
                    try:
                        self.client.close()
                    except OSError:
                        pass
                self.client = conn
            reader = conn.makefile("rb")
            try:
                while True:
                    header = reader.read(4)
                    if len(header) < 4:
                        break
                    length = struct.unpack("!I", header)[0]
                    if length > 1 << 20:
                        break
                    body = reader.read(length)
                    if len(body) < length:
                        break
                    request = json.loads(body)
                    if not isinstance(request, dict):
                        break
                    if request.get("method") == "credit":
                        self._handle_request(request)
                        continue
                    pool_sem.acquire()
                    threading.Thread(target=run, args=(request,), daemon=True).start()
            except (OSError, ValueError) as error:
                log.warning("client read failed: %s", error)
            log.info("web process disconnected; freeing the camera")
            with self.client_lock:
                self.client = None
            self.shutdown_camera()

    def shutdown_camera(self):
        try:
            self.stream_stop()
        except Exception as error:
            log.warning("cleanup reported %s", error)
        with self.state_lock:
            self._release()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--camera-ip", default="10.0.0.3")
    p.add_argument("--camera-mac", default="02:00:00:00:00:01")
    p.add_argument("--xml", required=True)
    p.add_argument("--gvsp-lib", required=True)
    p.add_argument("--socket", required=True)
    p.add_argument("--no-seccomp", action="store_true", help="for development only")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s bridge %(levelname)s %(message)s")

    import numpy  # noqa: F401  -- imported before the lockdown, like every native library
    import cv2  # noqa: F401
    bridge = Bridge(args)
    path = args.socket
    if os.path.exists(path):
        os.unlink(path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    listener.bind(path)
    os.umask(old)
    listener.listen(4)
    if not args.no_seccomp:
        sandbox.lock_network()
        log.info("network lockdown active; camera %s only", args.camera_ip)

    def on_signal(_sig, _frame):
        bridge.shutdown_camera()
        os._exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    bridge.serve(listener)


if __name__ == "__main__":
    main()
