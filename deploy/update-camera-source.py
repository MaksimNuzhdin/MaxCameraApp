#!/usr/bin/env python3
"""Verify or refresh the app's exact published MaxCamera XML reference.

No firmware/model download, git checkout mutation, or camera traffic. Updating to
a different release requires reviewing and explicitly editing upstream.json.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def verify(data, pin):
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob != pin["xml_git_blob"] or hashlib.sha256(data).hexdigest() != pin["xml_sha256"]:
        raise ValueError("XML does not match the reviewed upstream revision and checksums")
    ET.fromstring(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="download and verify the pinned XML before replacing the local copy")
    args = parser.parse_args()
    pin = json.loads((ROOT / "backend/data/upstream.json").read_text())
    target = ROOT / "backend/data/camera.xml"
    if args.fetch:
        url = f"https://raw.githubusercontent.com/MaksimNuzhdin/MaxCamera/{pin['revision']}/{pin['xml_path']}"
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(2_000_001)
        verify(data, pin)
        pending = target.with_suffix(".xml.tmp")
        pending.write_bytes(data)
        pending.replace(target)
    verify(target.read_bytes(), pin)
    print(f"Verified MaxCamera {pin['revision']} · XML blob {pin['xml_git_blob']}")


if __name__ == "__main__":
    main()
