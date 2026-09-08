#!/usr/bin/env python3
"""
Re-downloads recordings Twilio had not finished encoding when the call ended.

    python tools/fetch_recordings.py
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patient import config, telephony  # noqa: E402


def main() -> None:
    config.require("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    metas = sorted(glob.glob(os.path.join(config.CALLS_DIR, "*", "meta.json")))
    if not metas:
        print("No calls found.")
        return

    for meta_path in metas:
        out_dir = os.path.dirname(meta_path)
        name = os.path.basename(out_dir)
        if os.path.exists(os.path.join(out_dir, "recording.mp3")):
            print(f"  ✓ {name} already has a recording")
            continue
        meta = json.load(open(meta_path))
        call_sid = meta.get("call_sid")
        if not call_sid:
            print(f"  ✗ {name}: no call_sid in meta.json")
            continue
        print(f"  … fetching {name}")
        path = telephony.download_recording(call_sid, out_dir, tries=4)
        if path:
            meta["recording"] = "recording.mp3"
            json.dump(meta, open(meta_path, "w"), indent=2)
            print(f"  ✓ {name} → {path}")
        else:
            print(f"  ✗ {name}: Twilio has no recording for {call_sid}")


if __name__ == "__main__":
    main()
