#!/usr/bin/env python3
"""Loopback-only synthetic GUI fixture. No camera socket, scene capture or model.

PYTHONPATH=pydeps:backend python3 tests/preview_ai_visuals.py
Open http://localhost:8082/, sign in to this disposable fixture, start the
synthetic stream, enable AI, then turn on visual overlays. Test scene selects
fresh, stale, error, unavailable, empty and mismatched-source responses.
"""
import asyncio
import copy
import math
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace

from aiohttp import web
import cv2
import numpy as np

import web as product
from test_ai import fixture, object_record

ROOT = Path(__file__).resolve().parents[1]


class SyntheticBridge:
    def __init__(self, gateway):
        self.gateway = gateway
        self.b, self.w = fixture()
        self.running = False
        self.mode = "fresh"
        self.mask = 0
        self.frames = 0

    def values(self):
        values = {}
        for name, info in self.b.schema["features"].items():
            if name.startswith("MaxAI") or name in ("Width", "Height", "PixelFormat", "FpgaProcessingEnable", "ImageCompressionMode"):
                self.b.port.invalidate()
                values[name] = self.b.device.state(name)
            else:
                value = info.get("entries", [{}])[0].get("name", 0) if info["kind"] == "enum" else 0
                values[name] = dict(value=value, available=True, locked=False, min=0, max=100, inc=1)
        values["DeviceModelName"]["value"] = "SYNTHETIC · no camera"
        values["MaxAIEnabledFeatures"]["value"] = self.mask
        values["MaxAIAvailable"]["value"] = int(self.mode != "unavailable")
        values["MaxAIWorkerState"]["value"] = "Ready" if self.mask else "Idle"
        return values

    async def credit(self):
        pass

    async def call(self, method, **params):
        result = {}
        if method == "schema":
            result = self.b.schema
        elif method in ("values", "telemetry"):
            result = self.values()
        elif method == "status":
            result = dict(camera="streaming" if self.running else "free", owned=self.running, stream=self.running,
                          stats=dict(width=640, height=480, fps=10, frames=self.frames), preview={})
        elif method in ("stream_start", "stream_stop"):
            self.running = method == "stream_start"
            if not self.running:
                self.mask = 0
            await self.gateway.on_event(dict(event="stream", running=self.running))
            result = dict(running=self.running)
        elif method == "apply":
            for name, value in params["items"]:
                if name == "MaxAIEnabledFeatures":
                    self.mask = int(value)
                elif name in ("MaxAISamplePeriodUs", "MaxAIMinimumScore", "MaxAIFaceCosineThreshold"):
                    self.w.values[name] = int(value)
            result = dict(applied=len(params["items"]), error=None, values=self.values())
        elif method == "preview":
            result = {}
        elif method == "ai_results":
            seq = int(time.monotonic() // 2)
            shift = int(12 * math.sin(seq / 3))
            objects = [
                dict(object_record(1, "Demo identity", 90 + shift), class_id=1, y=90, width=100, height=110, score=720000, track_id=11),
                dict(object_record(1, "unknown", 240), y=105, width=84, height=94, track_id=12),
                dict(object_record(2, "bicycle", 345 + shift), class_id=1, y=210, width=185, height=125, score=880000, track_id=21),
                dict(object_record(4, "motion", 75), y=300, width=180, height=90, score=1000000, track_id=31),
                dict(object_record(6, "DEMO-QR", 470), y=70, width=78, height=78, score=1000000, track_id=0),
            ]
            r = dict(copy.deepcopy(self.w.latest), sequence=seq, age_us=700000, objects=objects, count=len(objects))
            state, message = "fresh", "Synthetic snapshot for visual inspection"
            if self.mode == "stale":
                r["age_us"] = 20_000_000
            if self.mode == "empty":
                r.update(objects=[], count=0)
            if self.mode == "mismatch":
                r["width"] = 1280
            if self.mode == "unsafe-label":
                r["objects"][2]["text"] = "<img src=x onerror=alert(1)>"
            if not self.running:
                state, r = "stopped", None
            elif not self.mask:
                state, r = "disabled", None
            elif self.mode in ("error", "unavailable"):
                state, r = self.mode, None
            result = dict(state=state, message=message, result=r, max_age_us=15_000_000,
                          diagnostics=dict(available=int(self.mode != "unavailable"), supported=255, enabled=self.mask,
                                           worker=2 if self.mask else 1, error_code=int(self.mode == "error"),
                                           config_generation=4, inference_us=310000))
        return dict(ok=True, result=result)

    async def frames_loop(self):
        while True:
            await asyncio.sleep(.1)
            if not self.running or not self.gateway.viewers or self.gateway.credit_out < 1:
                continue
            image = np.full((480, 640, 3), (28, 39, 45), np.uint8)
            for x in range(0, 640, 40):
                cv2.line(image, (x, 0), (x, 480), (40, 53, 62), 1)
            for y in range(0, 480, 40):
                cv2.line(image, (0, y), (640, y), (40, 53, 62), 1)
            cv2.putText(image, "SYNTHETIC TEST DATA - NO CAMERA", (24, 35), cv2.FONT_HERSHEY_SIMPLEX, .65, (150, 190, 200), 1)
            for center in ((140, 143), (282, 148)):
                cv2.ellipse(image, center, (28, 38), 0, 0, 360, (84, 103, 116), -1)
            for center in ((390, 308), (497, 308)):
                cv2.circle(image, center, 27, (108, 126, 140), 2)
            cv2.line(image, (390, 308), (440, 255), (108, 126, 140), 2)
            cv2.line(image, (440, 255), (497, 308), (108, 126, 140), 2)
            cv2.rectangle(image, (485, 85), (530, 130), (160, 160, 160), 3)
            ok, encoded = cv2.imencode(".jpg", image)
            assert ok
            self.frames += 1
            await self.gateway.on_frame(dict(src_w=640, src_h=480, w=640, h=480,
                                            stats=dict(width=640, height=480, fps=10, frames=self.frames)), encoded.tobytes())


def main():
    with tempfile.TemporaryDirectory(prefix="maxcamera-visual-fixture-") as private:
        product.args = SimpleNamespace(bridge="/unused-synthetic-bridge")
        app = web.Application(middlewares=[product.guard])
        app["auth"] = product.Auth(Path(private) / "auth.json")
        gateway = product.Gateway(app["auth"])
        gateway.bridge = SyntheticBridge(gateway)
        app["gateway"] = gateway
        app["static"] = ROOT / "frontend"

        async def index(_request):
            html = (ROOT / "frontend/index.html").read_text()
            controls = '<label class="tool-label">Synthetic test <select id="fixture-mode" aria-label="Synthetic test scenario">' + ''.join(
                f'<option value="{mode}">{mode}</option>' for mode in
                ("fresh", "stale", "empty", "unavailable", "error", "mismatch", "unsafe-label")) + '</select></label>'
            html = html.replace('<div class="transport">', '<div class="transport">' + controls)
            html = html.replace('</body>', '<script src="/fixture-controls.js" defer></script></body>')
            return web.Response(text=html, content_type="text/html")

        async def controls(_request):
            return web.Response(text='document.getElementById("fixture-mode").addEventListener("change",e=>fetch("/api/fixture/mode",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:e.target.value})}));', content_type="application/javascript")

        async def mode(request):
            gateway.bridge.mode = (await request.json())["mode"]
            return web.json_response({"mode": gateway.bridge.mode})

        async def start(_app):
            _app["task"] = asyncio.create_task(gateway.bridge.frames_loop())

        async def stop(_app):
            _app["task"].cancel()
            await asyncio.gather(_app["task"], return_exceptions=True)

        app.on_startup.append(start)
        app.on_cleanup.append(stop)
        app.router.add_get("/", index)
        app.router.add_get("/fixture-controls.js", controls)
        app.router.add_post("/api/fixture/mode", mode)
        for path, handler in (("/api/me", product.me), ("/api/schema", product.schema), ("/api/values", product.values),
                              ("/api/status", product.status), ("/api/ws", product.websocket)):
            app.router.add_get(path, handler)
        for path, handler in (("/api/login", product.login), ("/api/logout", product.logout), ("/api/apply", product.apply_values),
                              ("/api/ai/results", product.ai_results), ("/api/stream/start", product.stream_start),
                              ("/api/stream/stop", product.stream_stop), ("/api/preview", product.preview)):
            app.router.add_post(path, handler)
        app.router.add_static("/static", ROOT / "frontend")
        web.run_app(app, host="127.0.0.1", port=8082, access_log=None)


if __name__ == "__main__":
    main()
