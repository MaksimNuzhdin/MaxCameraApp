"""Focused bridge contract checks using the real pinned XML and a fake wire peer.

Run: PYTHONPATH=pydeps:backend python3 -m unittest discover -s tests -v
"""
import copy
import hashlib
import json
from pathlib import Path
import threading
import unittest

import ai
import bridge
import genapi
import policy

ROOT = Path(__file__).resolve().parents[1]
XML = (ROOT / "backend/data/camera.xml").read_bytes()


def object_record(feature=0, text="unknown", x=10):
    return dict(feature=feature, class_id=0, score=700000, x=x, y=20, width=30, height=40, track_id=7, text=text)


class Wire:
    """Emulates firmware latch immutability, selectors and generation changes."""
    def __init__(self, device):
        self.by_address = {}
        for name, node in device.nodes.items():
            target = genapi._text(node, "pValue")
            if target in device.nodes:
                address = genapi._text(device.nodes[target], "Address")
                if address:
                    self.by_address[int(address, 0)] = name
        self.values = dict(MaxAIAvailable=1, MaxAISupportedFeatures=255, MaxAIEnabledFeatures=1,
                           MaxAIWorkerState=2, MaxAIErrorCode=0, MaxAIConfigGeneration=4,
                           MaxAIInferenceTimeUs=310000, Width=640, Height=480, PixelFormat=0x02180014,
                           FpgaProcessingEnable=1, ImageCompressionMode=0,
                           MaxAIMinimumScore=500000, MaxAISamplePeriodUs=500000, MaxAIFaceCosineThreshold=450000)
        self.latest = dict(sequence=9, capture_sequence=1234, timestamp_low=0x12345678, timestamp_high=0x12345678,
                           width=640, height=480, pixel_format=0x02180014, config_generation=4, age_us=500000,
                           count=2, objects=[object_record(text="unknown"), object_record(6, "QR: שלום", 80)])
        self.latched = None
        self.selector = 0
        self.writes = []
        self.failure = None
        self.mutate_on_select = False

    def read_regs(self, addresses):
        return [self.read(self.by_address.get(address, "")) for address in addresses]

    def read(self, name):
        if name == self.failure:
            raise genapi.ReadFailed("injected read failure")
        if name == "MaxAIResultValid":
            return int(self.latched is not None and self.values["MaxAIAvailable"])
        if name in ai.METADATA.values():
            key = next(key for key, value in ai.METADATA.items() if value == name)
            return self.latched[key] if self.latched else 0
        if name.startswith("MaxAIObjectTextWord"):
            text = self.latched["objects"][self.selector]["text"].encode()[:31].ljust(32, b"\0")
            index = int(name[-1])
            return int.from_bytes(text[index*4:index*4+4], "little")
        if name in ai.OBJECT.values():
            key = next(key for key, value in ai.OBJECT.items() if value == name)
            return self.latched["objects"][self.selector][key]
        return self.values.get(name, 0)

    def write_reg(self, address, value):
        name = self.by_address[address]
        self.writes.append((name, value))
        if name == "MaxAIResultLatch":
            self.latched = copy.deepcopy(self.latest)
        elif name == "MaxAIResultRelease":
            self.latched = None
        elif name == "MaxAIObjectSelector":
            self.selector = value
            if self.mutate_on_select:
                self.latest["sequence"] += 1
                self.latest["objects"][0]["text"] = "newer result"
        else:
            self.values[name] = value
            if name in policy.AI_SETTINGS:
                self.values["MaxAIConfigGeneration"] += 2


def fixture():
    b = bridge.Bridge.__new__(bridge.Bridge)
    b.device = genapi.Device(XML, None)
    wire = Wire(b.device)
    b.gvcp = wire
    b.port = bridge.CachedPort(wire)
    b.device.port = b.port
    b.schema = b._build_schema()
    b.state_lock = threading.RLock()
    b.owned = b.stream_on = True
    return b, wire


class AIContractTests(unittest.TestCase):
    def setUp(self):
        self.b, self.w = fixture()

    def test_published_xml_integrity(self):
        pin = json.loads((ROOT / "backend/data/upstream.json").read_text())
        self.assertEqual(hashlib.sha1(b"blob " + str(len(XML)).encode() + b"\0" + XML).hexdigest(), pin["xml_git_blob"])
        self.assertEqual(hashlib.sha256(XML).hexdigest(), pin["xml_sha256"])

    def test_named_snapshot_and_selector_cache_invalidation(self):
        result = self.b.dispatch("ai_results", {})
        self.assertEqual(result["state"], "fresh")
        self.assertEqual(result["result"]["objects"][1]["text"], "QR: שלום")
        self.assertEqual(result["result"]["objects"][0]["x"], 10)
        self.assertEqual(result["result"]["objects"][1]["x"], 80)
        self.assertEqual(self.w.writes[-1], ("MaxAIResultRelease", 1))
        self.assertIsNone(self.w.latched)

    def test_latch_stays_coherent_when_new_result_arrives(self):
        self.w.mutate_on_select = True
        result = self.b.dispatch("ai_results", {})
        self.assertEqual(result["result"]["sequence"], 9)
        self.assertEqual(result["result"]["objects"][0]["text"], "unknown")

    def test_empty_success_is_distinct_from_unavailable(self):
        self.w.latest.update(count=0, objects=[])
        result = self.b.dispatch("ai_results", {})
        self.assertEqual((result["state"], result["result"]["objects"]), ("fresh", []))

    def test_unavailable_and_disabled_never_latch(self):
        for field, value, state in [("MaxAIAvailable", 0, "unavailable"), ("MaxAIEnabledFeatures", 0, "disabled")]:
            with self.subTest(field=field):
                b, w = fixture()
                w.values[field] = value
                self.assertEqual(b.dispatch("ai_results", {})["state"], state)
                self.assertEqual(w.writes, [])

    def test_stopped_read_does_not_take_control(self):
        self.b.stream_on = self.b.owned = False
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "stopped")
        self.assertEqual(self.w.writes, [])

    def test_missing_contract(self):
        del self.b.device.nodes["MaxAIAvailable"]
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "unavailable")

    def test_unsupported_route_does_not_offer_old_results(self):
        for field, value in [("FpgaProcessingEnable", 0), ("PixelFormat", 0x01080001), ("ImageCompressionMode", 1)]:
            with self.subTest(field=field):
                b, w = fixture()
                w.values[field] = value
                self.assertEqual(b.dispatch("ai_results", {})["state"], "unavailable")
                self.assertEqual(w.writes, [])

    def test_stale_age_generation_and_source(self):
        for field, value in [("age_us", 15000001), ("config_generation", 2), ("width", 1280), ("pixel_format", 0x02180015)]:
            with self.subTest(field=field):
                b, w = fixture()
                w.latest[field] = value
                self.assertEqual(b.dispatch("ai_results", {})["state"], "stale")
                self.assertIsNone(w.latched)

    def test_all_worker_error_codes(self):
        for code in (1, 2, 3, 123):
            self.w.values["MaxAIErrorCode"] = code
            self.assertEqual(self.b.dispatch("ai_results", {})["state"], "error")
        self.assertEqual(self.w.writes, [])

    def test_read_failure_releases(self):
        self.w.failure = "MaxAIObjectX"
        result = self.b.dispatch("ai_results", {})
        self.assertEqual(result["state"], "error")
        self.assertIsNone(result["result"])
        self.assertIsNone(self.w.latched)
        self.assertEqual(self.w.writes[-1], ("MaxAIResultRelease", 1))

    def test_latch_failure_still_attempts_release(self):
        write = self.w.write_reg
        def fail(address, value):
            write(address, value)
            if self.w.by_address[address] == "MaxAIResultLatch":
                raise bridge.CameraOffline("lost latch ACK")
        self.w.write_reg = fail
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "error")
        self.assertIsNone(self.w.latched)

    def test_worker_loss_during_read_marks_snapshot_stale_and_releases(self):
        read = self.w.read
        def lose_worker(name):
            if name == "MaxAIObjectX":
                self.w.values["MaxAIAvailable"] = 0
            return read(name)
        self.w.read = lose_worker
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "stale")
        self.assertIsNone(self.w.latched)

    def test_no_published_result_is_waiting(self):
        write = self.w.write_reg
        def no_result(address, value):
            write(address, value)
            if self.w.by_address[address] == "MaxAIResultLatch":
                self.w.latched = None
        self.w.write_reg = no_result
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "waiting")
        self.assertEqual(self.w.writes[-1], ("MaxAIResultRelease", 1))

    def test_release_failure_does_not_report_fresh_success(self):
        write = self.w.write_reg
        def fail_release(address, value):
            write(address, value)
            if self.w.by_address[address] == "MaxAIResultRelease":
                raise bridge.CameraOffline("lost release ACK")
        self.w.write_reg = fail_release
        result = self.b.dispatch("ai_results", {})
        self.assertEqual(result["state"], "error")
        self.assertIsNone(result["result"])

    def test_bad_count_and_boxes_fail_closed_and_release(self):
        self.w.latest["count"] = 33
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "error")
        self.w.latest["count"] = 2
        self.w.latest["objects"][0]["width"] = 1000
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "error")
        self.assertIsNone(self.w.latched)

    def test_repeated_reads_are_independent_released_transactions(self):
        first = self.b.dispatch("ai_results", {})
        second = self.b.dispatch("ai_results", {})
        self.assertEqual(first["result"]["sequence"], second["result"]["sequence"])
        self.assertEqual(self.w.writes.count(("MaxAIResultRelease", 1)), 2)

    def test_repeated_setting_does_not_bump_configuration(self):
        self.b._write_one("MaxAIEnabledFeatures", 1)
        self.assertEqual(self.w.writes, [])
        self.b._write_one("MaxAIEnabledFeatures", 17)
        self.assertEqual(self.w.values["MaxAIConfigGeneration"], 6)

    def test_unsupported_tracking_only_and_stopped_writes_rejected(self):
        self.w.values["MaxAISupportedFeatures"] = 1
        with self.assertRaises(bridge.CommandError):
            self.b._write_one("MaxAIEnabledFeatures", 4)
        self.b.port.invalidate()
        self.w.values["MaxAISupportedFeatures"] = 255
        with self.assertRaises(bridge.CommandError):
            self.b._write_one("MaxAIEnabledFeatures", 128)
        self.b.stream_on = False
        with self.assertRaises(bridge.CommandError):
            self.b.apply([["MaxAIEnabledFeatures", 1]])
        self.assertEqual(self.w.writes, [])

    def test_internal_commands_and_network_features_stay_protected(self):
        for name in ["MaxAIResultLatch", "MaxAIResultRelease", "MaxAIObjectSelector", "GevSCDA", "GevPersistentIPAddress"]:
            with self.assertRaises(bridge.CommandError):
                self.b._coerce(name, 1)
        self.assertIn("MaxAIAvailable", self.b.schema["features"])
        self.assertNotIn("MaxAIObjectTextWord0", self.b.schema["features"])

    def test_unreadable_features_have_error_unavailable_state(self):
        self.w.failure = "MaxAIAvailable"
        state = self.b.device.state("MaxAIAvailable")
        self.assertFalse(state["available"])
        self.assertIn("injected", state["error"])

    def test_stop_is_idempotent_and_releases_after_clearing_transport(self):
        actions = []
        self.b.stream_error = None
        self.b.frame_lock = threading.Condition()
        self.b._join_rx = lambda: actions.append("join")
        self.b._stop_acquisition = lambda: actions.append("acquisition-stop")
        self.b._clear_stream_registers = lambda: actions.append("clear-transport")
        def release():
            actions.append("release")
            self.b.owned = False
            self.w.values["MaxAIEnabledFeatures"] = 0
        self.b._release = release
        self.b._notify = lambda event: actions.append("notify")
        self.b.stream_stop()
        self.b.stream_stop()
        self.assertEqual(actions, ["join", "acquisition-stop", "clear-transport", "release", "notify"])
        self.assertEqual(self.b.dispatch("ai_results", {})["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
