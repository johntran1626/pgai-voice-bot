"""
telephony.py — everything that talks to Twilio (the phone company).

Three jobs:
  1. Place the outbound call, telling Twilio to pipe the audio to our bridge.
  2. Hang up on demand.
  3. After the call, download the recording as an .mp3.
"""

import os
import time

import requests
from twilio.rest import Client

from . import config

_client: Client | None = None


def client() -> Client:
    """Create the Twilio client once and reuse it."""
    global _client
    if _client is None:
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def build_twiml(public_host: str, scenario_id: str) -> str:
    """
    TwiML is the little XML script that tells Twilio what to do once the
    other end picks up.

    <Connect><Stream> means: "open a two-way WebSocket to this URL and pipe
    the call's audio through it". <Connect> (as opposed to <Start>) is the
    bidirectional version — audio flows both ways, which is what lets our bot
    actually speak.

    The scenario id rides along in the URL path so the bridge knows which
    patient to play the instant the socket opens.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Connect><Stream url="wss://{public_host}/media/{scenario_id}" /></Connect>'
        "</Response>"
    )


def place_call(public_host: str, scenario_id: str):
    """Dial the assessment line and return Twilio's call object."""
    return client().calls.create(
        to=config.TARGET_NUMBER,
        from_=config.TWILIO_FROM_NUMBER,
        twiml=build_twiml(public_host, scenario_id),
        # Record both sides. "dual" puts each speaker on their own stereo
        # channel, which makes it much easier to hear who talked over whom.
        record=True,
        recording_channels="dual",
        # Twilio's own safety cap, independent of our watchdog.
        time_limit=config.MAX_CALL_SECONDS + 30,
    )


def hangup_call(call_sid: str) -> None:
    """End a call that is still in progress. Ignores 'already ended' errors."""
    try:
        client().calls(call_sid).update(status="completed")
    except Exception:
        pass


def wait_for_completion(call_sid: str, timeout: int = 400) -> str:
    """Poll Twilio until the call is over. Returns the final status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client().calls(call_sid).fetch().status
        if status in ("completed", "failed", "busy", "no-answer", "canceled"):
            return status
        time.sleep(3)
    return "timeout"


def download_recording(call_sid: str, out_dir: str, tries: int = 12) -> str | None:
    """
    Fetch the call's recording as an .mp3.

    Twilio takes a few seconds after hang-up to finish processing audio, so
    we retry for a bit rather than giving up immediately.
    """
    os.makedirs(out_dir, exist_ok=True)
    for _ in range(tries):
        recordings = client().recordings.list(call_sid=call_sid, limit=1)
        if recordings:
            rec = recordings[0]
            # rec.uri looks like /2010-04-01/Accounts/AC.../Recordings/RE....json
            mp3_url = "https://api.twilio.com" + rec.uri.replace(".json", ".mp3")
            resp = requests.get(
                mp3_url,
                auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
                timeout=90,
            )
            if resp.ok and resp.content:
                path = os.path.join(out_dir, "recording.mp3")
                with open(path, "wb") as f:
                    f.write(resp.content)
                return path
        time.sleep(5)
    return None
