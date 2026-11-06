#!/usr/bin/env python3
"""Bounded isolated-preview (8081) check; never stores scenes or labels.

Default: HTTP/auth/security only. --live adds an 18-second all-feature run with
stop/release in finally. Uses existing image format and volatile AI settings.
"""
import argparse
import asyncio
import json
import time

import aiohttp


async def main(live):
    url = "http://127.0.0.1:8081"
    headers = {"Origin": url}
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True), timeout=timeout) as session:
        async def request(path, body=None, expected=200, origin=True):
            async with session.request("GET" if body is None else "POST", url + path,
                                       json=body, headers=headers if origin else {"Origin": "https://untrusted.invalid"}) as response:
                data = await response.json()
                assert response.status == expected, (path, response.status, data)
                return data
        await request("/api/ai/results", {}, expected=401)
        await request("/api/login", {"username": "admin", "password": "admin"})
        await request("/api/ai/results", {}, expected=403, origin=False)
        schema = await request("/api/schema")
        assert "MaxAIAvailable" in schema["features"]
        assert all(name not in schema["features"] for name in ("MaxAIResultLatch", "MaxAIResultRelease", "MaxAIObjectSelector"))
        await request("/api/apply", {"items": [["GevSCDA", 1]]}, expected=400)
        await request("/api/apply", {"items": [["MaxAIResultLatch", 1]]}, expected=400)
        status = await request("/api/status")
        assert not status["stream"] and not status["owned"], "Preview already active; refusing to interrupt it"
        await request("/api/apply", {"items": [["MaxAIEnabledFeatures", 1]]}, expected=400)
        idle = await request("/api/ai/results", {})
        assert idle["state"] in ("stopped", "unavailable")
        print("PASS HTTP: auth, origin, schema, protected transport/AI commands, stopped-write rejection", flush=True)
        if not live:
            return
        assert idle["diagnostics"].get("available") and idle["diagnostics"].get("supported") == 255
        before = await request("/api/values")
        assert before["PixelFormat"]["value"] in ("RGB8", "BGR8") and before["FpgaProcessingEnable"]["value"]
        observed = set()
        counts = []
        states = set()
        saved = {name: before[name]["value"] for name in ("MaxAISamplePeriodUs", "MaxAIMinimumScore", "MaxAIFaceCosineThreshold")}
        async with session.ws_connect(url + "/api/ws", headers=headers) as ws:
            async def drain():
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        await ws.send_str("ack")
            consumer = asyncio.create_task(drain())
            try:
                await request("/api/stream/start", {})
                await request("/api/stream/start", {})
                applied = await request("/api/apply", {"items": [["MaxAIEnabledFeatures", 255]]})
                assert not applied["error"], applied["error"]
                generation = applied["values"]["MaxAIConfigGeneration"]["value"]
                repeated = await request("/api/apply", {"items": [["MaxAIEnabledFeatures", 255]]})
                assert repeated["values"]["MaxAIConfigGeneration"]["value"] == generation
                end = time.monotonic() + 18
                while time.monotonic() < end:
                    snapshot = await request("/api/ai/results", {})
                    states.add(snapshot["state"])
                    if snapshot["result"]:
                        result = snapshot["result"]
                        observed.add(result["sequence"])
                        counts.append(result["count"])
                        assert result["config_generation"] == generation
                        assert len(result["objects"]) == result["count"] <= 32
                    await asyncio.sleep(1)
                status = await request("/api/status")
                assert observed, "No result in bounded window"
                off = await request("/api/apply", {"items": [["MaxAIEnabledFeatures", 0]]})
                assert not off["error"]
                assert (await request("/api/ai/results", {}))["state"] == "disabled"
                print(json.dumps(dict(passed=True, mask=255, distinct_results=len(observed), max_objects=max(counts),
                                      states=sorted(states), stats=status["stats"])), flush=True)
            finally:
                try:
                    if (await request("/api/status"))["stream"]:
                        await request("/api/apply", {"items": [[name, value] for name, value in saved.items()]})
                finally:
                    await request("/api/stream/stop", {})
                    await request("/api/stream/stop", {})
                    consumer.cancel()
                    await asyncio.gather(consumer, return_exceptions=True)
        status = await request("/api/status")
        values = await request("/api/values")
        assert not status["stream"] and not status["owned"]
        assert values["MaxAIEnabledFeatures"]["value"] == 0
        assert values["GevSCDA"]["value"] == 0 and values["GevSCPHostPort"]["value"] == 0
        assert values["GevSCPD"]["value"] == before["GevSCPD"]["value"]
        assert (await request("/api/ai/results", {}))["result"] is None
        print("PASS repeated start/apply/stop, AI disable, transport cleanup, packet-delay restore and control release", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    asyncio.run(main(parser.parse_args().live))
