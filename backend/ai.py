"""MaxAI snapshot protocol from the pinned upstream SW/ai/README.md and GigeAI.c.

All I/O goes through the XML node map. Called with the bridge's state lock held;
never claims ownership or changes the camera's inference configuration.
"""
MAX_AGE_US = 15_000_000
DIAGNOSTICS = {
    "available": "MaxAIAvailable", "supported": "MaxAISupportedFeatures",
    "enabled": "MaxAIEnabledFeatures", "worker": "MaxAIWorkerState",
    "error_code": "MaxAIErrorCode", "config_generation": "MaxAIConfigGeneration",
    "inference_us": "MaxAIInferenceTimeUs",
}
METADATA = {
    "sequence": "MaxAIResultSequence", "capture_sequence": "MaxAICaptureSequence",
    "timestamp_low": "MaxAITimestampLow", "timestamp_high": "MaxAITimestampHigh",
    "width": "MaxAIResultWidth", "height": "MaxAIResultHeight",
    "pixel_format": "MaxAIResultPixelFormat", "config_generation": "MaxAIResultConfigGeneration",
    "age_us": "MaxAIResultAgeUs", "count": "MaxAIObjectCount",
}
OBJECT = {key: "MaxAIObject" + suffix for key, suffix in
          zip(("feature", "class_id", "score", "x", "y", "width", "height", "track_id"),
              ("Feature", "Class", "Score", "X", "Y", "Width", "Height", "TrackID"))}
TEXT = [f"MaxAIObjectTextWord{i}" for i in range(8)]


def read(device, port, names):
    port.invalidate()
    port.prefetch(device.readable_addresses(names))
    return {name: device.value(name) for name in names}


def mapped(device, port, fields):
    values = read(device, port, list(fields.values()))
    return {key: values[name] for key, name in fields.items()}


def snapshot(bridge):
    device, port = bridge.device, bridge.port
    out = {"state": "unavailable", "message": "This firmware has no MaxAI contract.",
           "diagnostics": {}, "result": None, "max_age_us": MAX_AGE_US}
    if not all(name in device.nodes for name in DIAGNOSTICS.values()):
        return out
    try:
        diag = mapped(device, port, DIAGNOSTICS)
        out["diagnostics"] = diag
        if not diag["available"]:
            out["message"] = "AI worker unavailable. Loaded models and a recent worker heartbeat are required."
            return out
        if not bridge.owned or not bridge.stream_on:
            out.update(state="stopped", message="Start the stream, then enable AI for this control session.")
            return out
        if not diag["enabled"]:
            out.update(state="disabled", message="AI is off for this control session.")
            return out
        route = mapped(device, port, {"fpga": "FpgaProcessingEnable", "format": "PixelFormat", "compression": "ImageCompressionMode"})
        formats = {device.enum_value("PixelFormat", name) for name in ("RGB8", "BGR8")}
        if not route["fpga"] or route["format"] not in formats or route["compression"] != device.enum_value("ImageCompressionMode", "Off"):
            out.update(state="unavailable", message="No AI frame offers on this route. Select direct FPGA RGB8/BGR8 output with compression off.")
            return out
        if diag["error_code"]:
            messages = {1: "Worker exception", 2: "Rejected worker result", 3: "Cancelled or stale configuration; waiting for a new result"}
            out.update(state="error", message=messages.get(diag["error_code"], "Unknown worker error"))
            return out
        # Even a failed latch may have reached the camera: always attempt release.
        try:
            device.set_value("MaxAIResultLatch", 1)
            if not read(device, port, ["MaxAIResultValid"])["MaxAIResultValid"]:
                out.update(state="waiting", message="No valid result yet. Inference is asynchronous; objects can take about 8–10 seconds.")
                return out
            result = mapped(device, port, METADATA)
            if not 0 <= result["count"] <= 32:
                raise ValueError("Invalid AI object count")
            if not (0 < result["width"] <= 2592 and 0 < result["height"] <= 1944):
                raise ValueError("Invalid AI source dimensions")
            result["objects"] = []
            for index in range(result["count"]):
                device.set_value("MaxAIObjectSelector", index)
                raw = read(device, port, list(OBJECT.values()) + TEXT)
                obj = {key: raw[name] for key, name in OBJECT.items()}
                if not (0 <= obj["feature"] < 8 and 0 <= obj["score"] <= 1_000_000 and
                        obj["width"] > 0 and obj["height"] > 0 and obj["x"] >= 0 and obj["y"] >= 0 and
                        obj["x"] + obj["width"] <= result["width"] and obj["y"] + obj["height"] <= result["height"]):
                    raise ValueError("Invalid AI object bounds or score")
                obj["text"] = b"".join(int(raw[name]).to_bytes(4, "little") for name in TEXT).split(b"\0", 1)[0].decode("utf-8", "replace")
                result["objects"].append(obj)
            # Recheck liveness/validity/configuration after a potentially long read.
            final = mapped(device, port, {**DIAGNOSTICS, "valid": "MaxAIResultValid", "age_us": "MaxAIResultAgeUs",
                                          "source_width": "Width", "source_height": "Height", "source_format": "PixelFormat"})
            result["age_us"] = final["age_us"]
            out["diagnostics"] = {key: final[key] for key in DIAGNOSTICS}
            stale = (not final["available"] or not final["valid"] or
                     result["config_generation"] != final["config_generation"] or
                     result["age_us"] > MAX_AGE_US or result["width"] != final["source_width"] or
                     result["height"] != final["source_height"] or result["pixel_format"] != final["source_format"] or
                     not final["enabled"] or bool(final["error_code"]))
            out.update(state="stale" if stale else "fresh", result=result,
                       message="Stale snapshot; do not associate it with the live frame." if stale else
                       "Snapshot within the 15 s age limit. It is not synchronized to the live frame.")
        finally:
            device.set_value("MaxAIResultRelease", 1)
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        out.update(state="error", message=f"AI read failed: {error}", result=None)
    finally:
        port.invalidate()
    return out
