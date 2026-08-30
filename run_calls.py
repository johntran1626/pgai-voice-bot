"""
run_calls.py

The main script for the PGAI voice bot challenge.

What it does, step by step:
  1. Loads your secret keys from .env
  2. For each scenario in scenarios.py:
       a. Asks Vapi's API to place an outbound call to the target number,
          using an AI persona built from that scenario's system prompt.
       b. Waits (polling) for the call to finish.
       c. Downloads the recording (.mp3) into recordings/
       d. Saves the transcript (.txt) into transcripts/
  3. Prints a summary at the end.

Run it with:
    python run_calls.py

Run just one scenario (handy while testing/debugging):
    python run_calls.py 07_weekend_edge_case
"""

import os
import sys
import time
import json
import requests
from dotenv import load_dotenv

from scenarios import SCENARIOS

load_dotenv()

VAPI_API_KEY = os.environ.get("VAPI_API_KEY")
VAPI_PHONE_NUMBER_ID = os.environ.get("VAPI_PHONE_NUMBER_ID")
TARGET_PHONE_NUMBER = os.environ.get("TARGET_PHONE_NUMBER", "+18054398008")
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "anthropic")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-sonnet-5")
VOICE_PROVIDER = os.environ.get("VOICE_PROVIDER", "11labs")
VOICE_ID = os.environ.get("VOICE_ID", "paula")

VAPI_BASE = "https://api.vapi.ai"
RECORDINGS_DIR = "recordings"
TRANSCRIPTS_DIR = "transcripts"

# Safety check: this challenge only allows calling ONE specific number.
ALLOWED_NUMBER = "+18054398008"


def require_env():
    missing = [
        name
        for name in ("VAPI_API_KEY", "VAPI_PHONE_NUMBER_ID")
        if not os.environ.get(name)
    ]
    if missing:
        print(f"Missing required .env values: {', '.join(missing)}")
        print("Copy .env.example to .env and fill it in first.")
        sys.exit(1)

    if TARGET_PHONE_NUMBER != ALLOWED_NUMBER:
        print(
            f"Refusing to run: TARGET_PHONE_NUMBER in .env is "
            f"'{TARGET_PHONE_NUMBER}', but this challenge only allows "
            f"calling {ALLOWED_NUMBER}."
        )
        sys.exit(1)


def build_assistant(scenario):
    """Build the inline Vapi 'assistant' object for one scenario."""
    return {
        "name": f"pgai-test-{scenario['id']}",
        "firstMessage": scenario["first_message"],
        "model": {
            "provider": MODEL_PROVIDER,
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "system",
                    "content": scenario["system_prompt"],
                }
            ],
        },
        "voice": {
            "provider": VOICE_PROVIDER,
            "voiceId": VOICE_ID,
        },
        # Let Vapi record the call so we can download audio afterward.
        "recordingEnabled": True,
    }


def start_call(scenario):
    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "assistant": build_assistant(scenario),
        "customer": {"number": TARGET_PHONE_NUMBER},
    }
    resp = requests.post(f"{VAPI_BASE}/call", headers=headers, json=body, timeout=30)
    if resp.status_code >= 300:
        print(f"  Vapi rejected the call request: {resp.status_code}")
        print(f"  {resp.text}")
        resp.raise_for_status()
    return resp.json()


def poll_until_ended(call_id, timeout_seconds=360, interval_seconds=8):
    """Keep checking the call's status until Vapi says it has ended."""
    headers = {"Authorization": f"Bearer {VAPI_API_KEY}"}
    waited = 0
    while waited < timeout_seconds:
        resp = requests.get(f"{VAPI_BASE}/call/{call_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        print(f"  ...status: {status} ({waited}s elapsed)")
        if status in ("ended", "failed"):
            return data
        time.sleep(interval_seconds)
        waited += interval_seconds
    print("  Timed out waiting for call to end -- check the Vapi dashboard.")
    return None


def extract_recording_url(call_data):
    # Vapi has used a couple of different shapes for this over time;
    # check the common ones defensively.
    if call_data.get("recordingUrl"):
        return call_data["recordingUrl"]
    artifact = call_data.get("artifact") or {}
    if artifact.get("recordingUrl"):
        return artifact["recordingUrl"]
    recording = artifact.get("recording") or {}
    if isinstance(recording, dict):
        return recording.get("stereoUrl") or recording.get("mono", {}).get("combinedUrl")
    return None


def extract_transcript(call_data):
    if call_data.get("transcript"):
        return call_data["transcript"]
    artifact = call_data.get("artifact") or {}
    if artifact.get("transcript"):
        return artifact["transcript"]
    messages = call_data.get("messages") or artifact.get("messages")
    if messages:
        lines = []
        for m in messages:
            role = m.get("role", "?")
            content = m.get("message") or m.get("content") or ""
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)
    return None


def save_call_outputs(scenario_id, call_data):
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

    # Always dump the raw call JSON too -- useful for debugging field names
    # if Vapi's API shape has shifted since this script was written.
    with open(f"{TRANSCRIPTS_DIR}/{scenario_id}_raw.json", "w") as f:
        json.dump(call_data, f, indent=2)

    transcript = extract_transcript(call_data)
    if transcript:
        with open(f"{TRANSCRIPTS_DIR}/{scenario_id}.txt", "w") as f:
            f.write(transcript)
        print(f"  Saved transcript -> {TRANSCRIPTS_DIR}/{scenario_id}.txt")
    else:
        print("  No transcript found yet in the API response (check the _raw.json file).")

    recording_url = extract_recording_url(call_data)
    if recording_url:
        audio = requests.get(recording_url, timeout=60)
        ext = "mp3" if ".mp3" in recording_url else "wav"
        path = f"{RECORDINGS_DIR}/{scenario_id}.{ext}"
        with open(path, "wb") as f:
            f.write(audio.content)
        print(f"  Saved recording -> {path}")
    else:
        print("  No recording URL found yet (check the _raw.json file).")


def run_scenario(scenario):
    print(f"\n=== {scenario['id']}: {scenario['name']} ===")
    call = start_call(scenario)
    call_id = call.get("id")
    print(f"  Call started, id={call_id}")
    final = poll_until_ended(call_id)
    if final:
        save_call_outputs(scenario["id"], final)
    else:
        print("  Skipping save -- call never reached 'ended' within the timeout.")


def main():
    require_env()

    target_ids = sys.argv[1:]
    scenarios_to_run = (
        [s for s in SCENARIOS if s["id"] in target_ids] if target_ids else SCENARIOS
    )
    if target_ids and not scenarios_to_run:
        print(f"No scenario matched: {target_ids}")
        sys.exit(1)

    print(f"Running {len(scenarios_to_run)} scenario(s) against {TARGET_PHONE_NUMBER}\n")
    for scenario in scenarios_to_run:
        run_scenario(scenario)

    print("\nDone. Check recordings/ and transcripts/.")


if __name__ == "__main__":
    main()
