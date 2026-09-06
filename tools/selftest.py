#!/usr/bin/env python3
"""
tools/selftest.py — prove the bridge works without dialling anyone.

    python tools/selftest.py

It stands up a FAKE OpenAI Realtime server and a FAKE Twilio media stream,
runs a real CallBridge between them, and checks that:

  1. audio from "the phone" reaches "the brain",
  2. audio from "the brain" is played back to "the phone",
  3. both sides of the conversation land in the transcript,
  4. barge-in sends Twilio a "clear" so our bot stops talking,
  5. the call shuts down cleanly.

No API keys, no phone calls, no cost. Run it after any change to bridge.py.
"""

import asyncio
import base64
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from patient import bridge, config, server  # noqa: E402

FAKE_PORT = 8799
SILENCE = base64.b64encode(b"\xff" * 160).decode()

received_from_bridge: list[dict] = []


async def fake_openai(ws):
    """Pretend to be OpenAI's Realtime API for one connection."""
    # 1. The bridge's first message must be a session.update.
    first = json.loads(await ws.recv())
    received_from_bridge.append(first)
    await ws.send(json.dumps({"type": "session.updated"}))

    # 2. Pretend the PGAI agent greeted us (this is *input* transcription).
    await ws.send(json.dumps({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "Thanks for calling Riverside Family Medicine.",
    }))

    # 3. Pretend our patient replies with two chunks of speech.
    for _ in range(2):
        await ws.send(json.dumps({
            "type": "response.output_audio.delta",
            "delta": SILENCE,
            "item_id": "item_abc",
        }))
        await asyncio.sleep(0.05)
    await ws.send(json.dumps({
        "type": "response.output_audio_transcript.done",
        "transcript": "Hi, I'd like to book an appointment please.",
    }))

    # 4. Pretend the agent starts talking while we're still speaking.
    await asyncio.sleep(0.1)
    await ws.send(json.dumps({"type": "input_audio_buffer.speech_started"}))

    # 5. Keep the socket open, recording everything the bridge sends us,
    #    until the bridge closes it.
    try:
        async for raw in ws:
            received_from_bridge.append(json.loads(raw))
    except Exception:
        pass


def start_fake_openai() -> threading.Thread:
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def serve():
            async with websockets.serve(fake_openai, "127.0.0.1", FAKE_PORT):
                await asyncio.Future()

        loop.run_until_complete(serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def main() -> int:
    config.OPENAI_API_KEY = config.OPENAI_API_KEY or "sk-test-not-real"
    bridge.OPENAI_WS_URL = f"ws://127.0.0.1:{FAKE_PORT}/v1/realtime?model={{model}}"
    bridge.GREETING_TIMEOUT_S = 9999  # don't let the watchdog interfere

    start_fake_openai()
    import time
    time.sleep(1.0)

    client = TestClient(server.app)
    to_twilio: list[dict] = []

    with client.websocket_connect("/media/01_schedule_new") as ws:
        # Twilio's opening handshake. callSid is omitted on purpose so the
        # bridge never tries to reach the real Twilio API.
        ws.send_text(json.dumps({
            "event": "start",
            "start": {"streamSid": "MZfake123", "callSid": None},
        }))
        # 20 packets of inbound audio, 20ms apart on Twilio's clock.
        for i in range(20):
            ws.send_text(json.dumps({
                "event": "media",
                "media": {"timestamp": str(i * 20), "payload": SILENCE},
            }))
        time.sleep(1.2)

        # Drain whatever the bridge sent back toward Twilio.
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                to_twilio.append(ws.receive_json())
            except Exception:
                break
            if any(m.get("event") == "clear" for m in to_twilio):
                break

        ws.send_text(json.dumps({"event": "stop"}))
        # Stay connected briefly so the server-side handler can finish and
        # publish its transcript before the client tears the socket down.
        time.sleep(1.2)

    time.sleep(0.6)
    transcript = server.COMPLETED.get("01_schedule_new")

    # ---------------- assertions ----------------
    problems = []

    session = next((m for m in received_from_bridge
                    if m.get("type") == "session.update"), None)
    if not session:
        problems.append("bridge never sent session.update to OpenAI")
    else:
        s = session["session"]
        if s["audio"]["input"]["format"]["type"] != "audio/pcmu":
            problems.append("input audio format is not audio/pcmu")
        if s["audio"]["output"]["format"]["type"] != "audio/pcmu":
            problems.append("output audio format is not audio/pcmu")
        if not any(t["name"] == "end_call" for t in s.get("tools", [])):
            problems.append("end_call tool was not registered")
        if "HUMAN CALLING A DOCTOR" not in s["instructions"]:
            problems.append("persona instructions missing")

    appended = [m for m in received_from_bridge
                if m.get("type") == "input_audio_buffer.append"]
    if len(appended) < 20:
        problems.append(f"only {len(appended)}/20 audio packets reached OpenAI")

    media_out = [m for m in to_twilio if m.get("event") == "media"]
    if len(media_out) < 2:
        problems.append(f"only {len(media_out)} audio chunks played to the phone")

    if not any(m.get("event") == "mark" for m in to_twilio):
        problems.append("no playback marks sent to Twilio")

    if not any(m.get("event") == "clear" for m in to_twilio):
        problems.append("barge-in did not send a 'clear' to Twilio")

    truncate = [m for m in received_from_bridge
                if m.get("type") == "conversation.item.truncate"]
    if not truncate:
        problems.append("barge-in did not truncate the assistant item")

    if transcript is None:
        problems.append("no transcript object was produced")
    else:
        speakers = {t["speaker"] for t in transcript.turns}
        if "PGAI_AGENT" not in speakers:
            problems.append("the agent's side was not transcribed")
        if "PATIENT_BOT" not in speakers:
            problems.append("our bot's side was not transcribed")
        if not any(e["kind"] == "barge_in" for e in transcript.events):
            problems.append("barge-in was not recorded in the event log")

    # ---------------- report ----------------
    print("\n" + "=" * 62)
    if problems:
        print("SELF-TEST FAILED")
        for p in problems:
            print(f"  ✗ {p}")
        print("=" * 62)
        return 1

    print("SELF-TEST PASSED")
    print(f"  ✓ session.update sent with mu-law audio + end_call tool")
    print(f"  ✓ {len(appended)} audio packets phone → brain")
    print(f"  ✓ {len(media_out)} audio chunks brain → phone")
    print(f"  ✓ barge-in: truncated at "
          f"{truncate[0]['audio_end_ms']}ms and cleared Twilio's buffer")
    print(f"  ✓ transcript captured {len(transcript.turns)} turns, both sides")
    for t in transcript.turns:
        print(f"       {t['speaker']}: {t['text']}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
