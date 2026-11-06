#!/usr/bin/env python3
"""Web gateway: login, static files, JSON API and the video WebSocket.

Runs inside a network namespace with no interfaces except its own loopback. It
reaches the outside only through two Unix sockets: the one it serves on and the
camera bridge's. Exploiting this process therefore yields no route to the LAN.
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

log = logging.getLogger("web")
COOKIE = "mc_session"
SESSION_SECONDS = 12 * 3600
DEFAULT_USER, DEFAULT_PASSWORD = "admin", "admin"
NO_VIEWER_GRACE_SECONDS = 15
TELEMETRY_SECONDS = 1.5
STATUS_SECONDS = 10
MAX_PENDING_PER_CLIENT = 2

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob: data:; "
       "connect-src 'self' ws: wss:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


class Auth:
    def __init__(self, path):
        self.path = Path(path)
        self.sessions = {}
        self.failures = {}
        self.global_failures = []
        if self.path.exists():
            self.record = json.loads(self.path.read_text())
        else:
            self._store(DEFAULT_USER, DEFAULT_PASSWORD, is_default=True)

    def _store(self, user, password, is_default=False):
        salt = secrets.token_bytes(16)
        self.record = {"user": user, "salt": base64.b64encode(salt).decode(),
                       "hash": base64.b64encode(_hash(password, salt)).decode(), "default": is_default}
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(self.record, out)
        os.replace(temp, self.path)

    def check(self, user, password):
        computed = _hash(password, base64.b64decode(self.record["salt"]))
        stored = base64.b64decode(self.record["hash"])
        return hmac.compare_digest(computed, stored) and hmac.compare_digest(user, self.record["user"])

    def retry_after(self, client):
        now = time.time()
        self.global_failures = [t for t in self.global_failures if now - t < 60]
        if len(self.global_failures) >= 30:
            return 60 - (now - self.global_failures[0])
        count, last = self.failures.get(client, (0, 0))
        if count >= 5:
            wait = min(2 ** (count - 4), 60) - (now - last)
            if wait > 0:
                return wait
        return 0

    def note_failure(self, client):
        now = time.time()
        self.global_failures.append(now)
        count, _ = self.failures.get(client, (0, 0))
        self.failures[client] = (count + 1, now)
        if len(self.failures) > 10000:
            self.failures.clear()

    def open_session(self, client):
        self.failures.pop(client, None)
        token = secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = time.time() + SESSION_SECONDS
        return token

    def valid(self, token):
        if not token:
            return False
        key = hashlib.sha256(token.encode()).hexdigest()
        expires = self.sessions.get(key)
        if expires is None:
            return False
        if expires < time.time():
            del self.sessions[key]
            return False
        return True

    def close_session(self, token):
        if token:
            self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def change_password(self, new_password, keep_token):
        self._store(self.record["user"], new_password)
        keep = hashlib.sha256(keep_token.encode()).hexdigest()
        self.sessions = {k: v for k, v in self.sessions.items() if k == keep}


class BridgeClient:
    """One Unix-socket connection to the bridge with request/response matching."""

    def __init__(self, path, on_event, on_frame):
        self.path = path
        self.on_event = on_event
        self.on_frame = on_frame
        self.writer = None
        self.pending = {}
        self.next_id = 0
        self.connected = asyncio.Event()

    async def run(self):
        while True:
            try:
                reader, self.writer = await asyncio.open_unix_connection(self.path, limit=1 << 26)
                self.connected.set()
                log.info("bridge connected")
                while True:
                    kind, head_len, blob_len = struct.unpack("!BII", await reader.readexactly(9))
                    header = json.loads(await reader.readexactly(head_len))
                    blob = await reader.readexactly(blob_len) if blob_len else b""
                    if kind == 1:
                        await self.on_frame(header, blob)
                    elif "id" in header:
                        future = self.pending.pop(header["id"], None)
                        if future and not future.done():
                            future.set_result(header)
                    else:
                        await self.on_event(header)
            except (OSError, asyncio.IncompleteReadError) as error:
                log.warning("bridge connection: %s", error)
            self.connected.clear()
            self.writer = None
            await self.on_event({"event": "stream", "running": False})
            for future in self.pending.values():
                if not future.done():
                    future.set_result({"ok": False, "code": "offline", "error": "The camera service is restarting"})
            self.pending.clear()
            await asyncio.sleep(1)

    async def _write(self, request):
        body = json.dumps(request).encode()
        self.writer.write(struct.pack("!I", len(body)) + body)
        await self.writer.drain()

    async def call(self, method, timeout=60, **params):
        if not self.connected.is_set():
            return {"ok": False, "code": "offline", "error": "The camera service is not running"}
        self.next_id += 1
        rid = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        await self._write({"id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(rid, None)
            return {"ok": False, "code": "error", "error": "The camera did not respond in time"}

    async def credit(self):
        if self.connected.is_set():
            await self._write({"method": "credit"})


class Viewer:
    def __init__(self, ws):
        self.ws = ws
        self.pending = 0
        self.wants_video = True


class Gateway:
    def __init__(self, auth):
        self.auth = auth
        self.viewers = set()
        self.credit_out = 0
        self.stream_running = False
        self.empty_since = None
        self.bridge = BridgeClient(args.bridge, self.on_event, self.on_frame)

    async def pump(self):
        if self.credit_out < 1 and any(v.wants_video and v.pending < MAX_PENDING_PER_CLIENT for v in self.viewers):
            self.credit_out += 1
            await self.bridge.credit()

    async def broadcast(self, message):
        text = json.dumps(message)
        for viewer in list(self.viewers):
            try:
                await viewer.ws.send_str(text)
            except (ConnectionError, RuntimeError):
                self.viewers.discard(viewer)

    async def on_event(self, event):
        if event.get("event") == "stream":
            self.stream_running = bool(event.get("running"))
            if not self.stream_running:
                self.credit_out = 0
                for viewer in self.viewers:
                    viewer.pending = 0
            await self.pump()
        await self.broadcast(event)

    async def on_frame(self, header, blob):
        self.credit_out = max(0, self.credit_out - 1)
        head = json.dumps(header).encode()
        wire = struct.pack("!I", len(head)) + head + blob
        for viewer in list(self.viewers):
            if viewer.wants_video and viewer.pending < MAX_PENDING_PER_CLIENT:
                viewer.pending += 1
                try:
                    await viewer.ws.send_bytes(wire)
                except (ConnectionError, RuntimeError):
                    self.viewers.discard(viewer)
        await self.pump()

    async def housekeeping(self):
        """Polls only while somebody is watching; nobody watching means no camera traffic."""
        tick = 0
        while True:
            await asyncio.sleep(TELEMETRY_SECONDS)
            tick += 1
            if self.viewers:
                self.empty_since = None
                if self.stream_running:
                    reply = await self.bridge.call("telemetry")
                    if reply.get("ok"):
                        await self.broadcast({"event": "values", "values": reply["result"]})
                if tick * TELEMETRY_SECONDS >= STATUS_SECONDS or self.stream_running:
                    tick = 0
                    reply = await self.bridge.call("status")
                    if reply.get("ok"):
                        await self.broadcast({"event": "status", **reply["result"]})
            elif self.stream_running:
                self.empty_since = self.empty_since or time.monotonic()
                if time.monotonic() - self.empty_since > NO_VIEWER_GRACE_SECONDS:
                    log.info("no viewers left; stopping the stream to free the camera")
                    await self.bridge.call("stream_stop")
                    self.stream_running = False
                    self.empty_since = None


def client_id(request):
    return request.headers.get("CF-Connecting-IP") or request.remote or "local"


def origin_ok(request):
    origin = request.headers.get("Origin")
    if not origin:
        return request.method in ("GET", "HEAD")
    # A tunnel may rewrite Host and pass the public name separately. Browsers do not let
    # a cross-site page set this header without a preflight, which this server never grants.
    expected = {request.headers.get("Host", ""), request.headers.get("X-Forwarded-Host", "")}
    return urlsplit(origin).netloc in expected


def reply(result):
    if result.get("ok"):
        return web.json_response(result["result"])
    status = {"busy": 409, "offline": 503}.get(result.get("code"), 400)
    return web.json_response({"error": result.get("error"), "code": result.get("code")}, status=status)


@web.middleware
async def guard(request, handler):
    path = request.path
    if path.startswith("/api/"):
        if request.method not in ("GET", "HEAD") and not origin_ok(request):
            return web.json_response({"error": "Blocked cross-site request"}, status=403)
        if path != "/api/login" and not request.app["auth"].valid(request.cookies.get(COOKIE)):
            return web.json_response({"error": "Sign in first", "code": "auth"}, status=401)
    response = await handler(request)
    if not isinstance(response, web.WebSocketResponse):
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
    return response


async def login(request):
    auth = request.app["auth"]
    client = client_id(request)
    wait = auth.retry_after(client)
    if wait > 0:
        return web.json_response({"error": f"Too many attempts. Try again in {int(wait) + 1} seconds."}, status=429)
    try:
        body = await request.json()
        user, password = str(body["username"]), str(body["password"])
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "Enter a username and password"}, status=400)
    ok = await asyncio.get_running_loop().run_in_executor(None, auth.check, user[:256], password[:1024])
    if not ok:
        auth.note_failure(client)
        log.warning("failed sign-in from %s", client)
        return web.json_response({"error": "Wrong username or password"}, status=401)
    log.info("sign-in from %s", client)
    token = auth.open_session(client)
    response = web.json_response({"user": auth.record["user"], "default_password": auth.record.get("default", False)})
    secure = request.headers.get("X-Forwarded-Proto") == "https"
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="Strict", secure=secure, path="/")
    return response


async def logout(request):
    request.app["auth"].close_session(request.cookies.get(COOKIE))
    response = web.json_response({})
    response.del_cookie(COOKIE, path="/")
    return response


async def me(request):
    auth = request.app["auth"]
    return web.json_response({"user": auth.record["user"], "default_password": auth.record.get("default", False)})


async def change_password(request):
    auth = request.app["auth"]
    body = await request.json()
    current, new = str(body.get("current", "")), str(body.get("new", ""))
    if len(new) < 8:
        return web.json_response({"error": "Use at least 8 characters"}, status=400)
    loop = asyncio.get_running_loop()
    if not await loop.run_in_executor(None, auth.check, auth.record["user"], current):
        auth.note_failure(client_id(request))
        return web.json_response({"error": "The current password is wrong"}, status=403)
    await loop.run_in_executor(None, auth.change_password, new, request.cookies.get(COOKIE))
    return web.json_response({"changed": True})


async def schema(request):
    return reply(await request.app["gateway"].bridge.call("schema"))


async def values(request):
    return reply(await request.app["gateway"].bridge.call("values"))


async def ai_results(request):
    return reply(await request.app["gateway"].bridge.call("ai_results"))


async def status(request):
    return reply(await request.app["gateway"].bridge.call("status"))


async def apply_values(request):
    body = await request.json()
    return reply(await request.app["gateway"].bridge.call(
        "apply", timeout=120, items=body.get("items"), restart=bool(body.get("restart", True))))


async def stream_start(request):
    return reply(await request.app["gateway"].bridge.call("stream_start"))


async def stream_stop(request):
    return reply(await request.app["gateway"].bridge.call("stream_stop"))


async def preview(request):
    body = await request.json()
    return reply(await request.app["gateway"].bridge.call(
        "preview", max_width=body.get("max_width"), quality=body.get("quality"), max_fps=body.get("max_fps")))


async def snapshot(request):
    fmt = "png" if request.query.get("format") == "png" else "jpeg"
    result = await request.app["gateway"].bridge.call("capture", format=fmt)
    if not result.get("ok"):
        return reply(result)
    kind = result["result"]["kind"]
    name = time.strftime("maxcamera-%Y%m%d-%H%M%S.") + ("png" if kind == "png" else "jpg")
    return web.Response(body=base64.b64decode(result["result"]["data"]), content_type=f"image/{kind}",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def websocket(request):
    if not origin_ok(request):
        return web.Response(status=403)
    gateway = request.app["gateway"]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
    await ws.prepare(request)
    viewer = Viewer(ws)
    gateway.viewers.add(viewer)
    gateway.empty_since = None
    await gateway.pump()
    reply_status = await gateway.bridge.call("status")
    if reply_status.get("ok"):
        gateway.stream_running = bool(reply_status["result"].get("stream"))
        await ws.send_str(json.dumps({"event": "status", **reply_status["result"]}))
    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            if message.data == "ack":
                viewer.pending = max(0, viewer.pending - 1)
                await gateway.pump()
            elif message.data in ("video-on", "video-off"):
                viewer.wants_video = message.data == "video-on"
                await gateway.pump()
    finally:
        gateway.viewers.discard(viewer)
    return ws


async def index(request):
    return web.FileResponse(request.app["static"] / "index.html")


async def strip_server_header(_request, response):
    response.headers.pop("Server", None)


async def on_startup(app):
    gateway = app["gateway"]
    app["tasks"] = [asyncio.create_task(gateway.bridge.run()), asyncio.create_task(gateway.housekeeping())]


async def on_cleanup(app):
    for task in app["tasks"]:
        task.cancel()


def main():
    global args
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--socket", required=True)
    p.add_argument("--bridge", required=True)
    p.add_argument("--static", required=True)
    p.add_argument("--auth-file", required=True)
    p.add_argument("--seccomp", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s web %(levelname)s %(message)s")
    app = web.Application(middlewares=[guard], client_max_size=4 * 1024 * 1024)
    app["auth"] = Auth(args.auth_file)
    app["gateway"] = Gateway(app["auth"])
    app["static"] = Path(args.static)
    app.on_response_prepare.append(strip_server_header)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_post("/api/password", change_password)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/schema", schema)
    app.router.add_get("/api/values", values)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/apply", apply_values)
    app.router.add_post("/api/ai/results", ai_results)
    app.router.add_post("/api/stream/start", stream_start)
    app.router.add_post("/api/stream/stop", stream_stop)
    app.router.add_post("/api/preview", preview)
    app.router.add_get("/api/snapshot", snapshot)
    app.router.add_get("/api/ws", websocket)
    app.router.add_static("/static", args.static)
    if os.path.exists(args.socket):
        os.unlink(args.socket)
    if args.seccomp:
        import sandbox
        # The listener opens before the lockdown, which refuses bind().
        import socket
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        listener.bind(args.socket)
        os.umask(old)
        listener.listen(128)
        sandbox.lock_network(allow_unix_connect=True, allow_listen=True)
        web.run_app(app, sock=listener, access_log=None, print=None, handle_signals=True)
    else:
        web.run_app(app, path=args.socket, access_log=None, print=None)


if __name__ == "__main__":
    main()
