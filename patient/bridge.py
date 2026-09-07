"""
bridge.py — the heart of the project.

A phone call is just a stream of audio. This file sits in the middle of two
streams and shovels audio between them:

    Twilio (the phone line)  <---->  BRIDGE  <---->  OpenAI Realtime (the brain)

Left side : Twilio opens a WebSocket to us and sends ~50 tiny audio packets
            per second of whatever the PGAI agent is saying. It also plays
            back any audio we send it.
Right side: OpenAI's Realtime API takes audio in, thinks, and streams audio
            back out in our patient's voice.

Both sides speak the same audio format — G.711 mu-law at 8kHz, the ancient
codec the phone network uses — so we never have to convert anything. We just
pass the base64 blobs straight through. That is the single biggest reason
this file is short.

Everything else in here is the hard part: knowing WHEN to talk.
"""

import asyncio
import base64
import json
import time

import websockets

from . import config
from .prompts import END_CALL_TOOL, build_instructions
from .transcript import Transcript

OPENAI_WS_URL = "wss://api.openai.com/v1/realtime?model={model}"

# OpenAI renamed several event types when the Realtime API went GA.
# We accept BOTH spellings so this keeps working either way.
AUDIO_DELTA_EVENTS = {"response.output_audio.delta", "response.audio.delta"}
BOT_TRANSCRIPT_DONE = {
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
}
AGENT_TRANSCRIPT_DONE = {
    "conversation.item.input_audio_transcription.completed",
}

# How long to wait, in silence, before nudging our bot to say "hello?"
GREETING_TIMEOUT_S = 12

# --- Letting the office finish its greeting --------------------------------
# Real receptionists open with a recorded disclaimer and/or "Dr. Smith's
# office, how can I help you?" A human waits through all of that. Our bot
# could not: with turn detection on, the API CREATES A REPLY every time it
# thinks a turn ended, so the model was forced to speak over the recording
# and then repeat itself once a person actually greeted us.
#
# So we start the call with create_response switched off — our patient
# listens but cannot reply — and only open the gate once the office has
# actually said something AND gone quiet, which is the real cue to speak.
OPENING_SILENCE_S = 1.5   # quiet this long after they speak = our turn
OPENING_MAX_HOLD_S = 25   # never stay muted longer than this, whatever happens

# --- Hanging up politely ---------------------------------------------------
# After our patient signs off, wait for the receptionist to say their piece
# ("alright, see you Wednesday") before dropping the line.
HANGUP_QUIET_S = 1.2      # they've been quiet this long = conversation over
HANGUP_GRACE_MAX_S = 12   # but never hold the line open longer than this


class CallBridge:
    """Handles exactly one phone call, from answer to hang-up."""

    def __init__(self, twilio_ws, scenario: dict):
        self.twilio_ws = twilio_ws
        self.scenario = scenario
        self.transcript = Transcript(scenario)
        self.openai_ws = None

        # Twilio identifiers, learned from its "start" message.
        self.stream_sid: str | None = None
        self.call_sid: str | None = None

        # --- state used for interruption ("barge-in") handling -------------
        # Twilio stamps every inbound audio packet with a millisecond
        # timestamp. We use those numbers as our clock, because they reflect
        # what the phone line actually did, not what our laptop thinks.
        self.latest_media_ts = 0
        # When our bot's current sentence STARTED playing, on that same clock.
        self.response_start_ts: int | None = None
        # The id of the audio chunk our bot is currently speaking.
        self.last_assistant_item: str | None = None
        # Twilio "marks" are receipts: we send one after each audio chunk and
        # Twilio echoes it back once that chunk has actually been played.
        # A non-empty queue means "our bot is still talking right now".
        self.mark_queue: list[str] = []

        self.finished = asyncio.Event()
        self.nudged = False
        self.session_retry_done = False

        # --- opening gate (see OPENING_SILENCE_S above) --------------------
        # False until we've let the office finish greeting us.
        self.opening_done = False
        self.agent_speaking = False
        self.agent_has_spoken = False
        self.agent_quiet_since: float | None = None
        # The real turn-detection settings, held back until the gate opens.
        self.turn_detection: dict = {}

    # =====================================================================
    # Session setup
    # =====================================================================

    def session_config(self, minimal: bool = False) -> dict:
        """
        The one message that configures the whole call: who our patient is,
        what voice it uses, what audio format, and how it decides when the
        other person has stopped talking.

        `minimal=True` strips the optional extras — used as an automatic
        fallback if OpenAI rejects a newer field.
        """
        # How we decide the other person has stopped talking. A scenario can
        # override this outright (08_barge_in does); otherwise it comes from
        # .env, because it is the setting most worth tuning by ear.
        #
        # The trade-off is real and has no free lunch: waiting longer means
        # never talking over them but replying slowly, and replying fast
        # means occasionally cutting in on a pause. See config.py.
        if config.VAD_MODE == "server":
            default_vad = {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": config.VAD_SILENCE_MS,
                "interrupt_response": True,
                "create_response": True,
            }
        else:
            default_vad = {
                "type": "semantic_vad",
                "eagerness": config.VAD_EAGERNESS,
                "interrupt_response": True,
            }
        turn_detection = dict(self.scenario.get("turn_detection", default_vad))
        # Remember the real settings, then send a muted version: the model
        # listens to the greeting but is not allowed to answer it yet.
        # open_opening_gate() restores these once the office stops talking.
        self.turn_detection = dict(turn_detection)
        self.turn_detection["create_response"] = True
        if not self.opening_done:
            turn_detection["create_response"] = False

        session: dict = {
            "type": "realtime",
            "model": config.REALTIME_MODEL,
            "output_modalities": ["audio"],
            "instructions": build_instructions(self.scenario),
            "audio": {
                "input": {
                    # audio/pcmu == G.711 mu-law == exactly what Twilio sends.
                    "format": {"type": "audio/pcmu"},
                    "turn_detection": turn_detection,
                    # Transcribe the OTHER side so we capture their half of
                    # the conversation for the transcript deliverable.
                    "transcription": {"model": "gpt-4o-transcribe"},
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": config.PATIENT_VOICE,
                },
            },
            "tools": [END_CALL_TOOL],
        }

        if not minimal:
            # Phone lines are noisy; this cleans up the inbound audio a little.
            session["audio"]["input"]["noise_reduction"] = {"type": "near_field"}

        return {"type": "session.update", "session": session}

    # =====================================================================
    # Main loop
    # =====================================================================

    async def run(self) -> Transcript:
        url = OPENAI_WS_URL.format(model=config.REALTIME_MODEL)
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            max_size=None,
        ) as openai_ws:
            self.openai_ws = openai_ws
            await self.send_openai(self.session_config())

            # Run three jobs at the same time. asyncio lets a single thread
            # interleave them, so none of them blocks the others.
            tasks = [
                asyncio.create_task(self.pump_phone_to_brain()),
                asyncio.create_task(self.pump_brain_to_phone()),
                asyncio.create_task(self.watchdog()),
            ]

            # Wait for whichever job decides the call is over, then stop the
            # others. We can NOT just gather() all three: when the far end
            # hangs up, the phone-side loop ends but the OpenAI socket stays
            # open, so gather() would wait forever on a call that's finished.
            await self.finished.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return self.transcript

    # ---------------------------------------------------------------- helpers

    async def send_openai(self, payload: dict) -> None:
        try:
            await self.openai_ws.send(json.dumps(payload))
        except Exception:
            pass  # socket already closing; nothing useful to do

    async def send_twilio(self, payload: dict) -> None:
        try:
            await self.twilio_ws.send_json(payload)
        except Exception:
            pass

    # =====================================================================
    # Direction 1: phone line  --->  brain
    # =====================================================================

    async def pump_phone_to_brain(self) -> None:
        """Read Twilio's messages and forward the caller's audio to OpenAI."""
        try:
            async for raw in self.twilio_ws.iter_text():
                msg = json.loads(raw)
                event = msg.get("event")

                if event == "start":
                    start = msg["start"]
                    self.stream_sid = start["streamSid"]
                    self.call_sid = start.get("callSid")
                    self.transcript.call_sid = self.call_sid
                    self.transcript.stream_sid = self.stream_sid
                    # Reset the clock: the call really begins here, not when
                    # the Python object was created.
                    self.transcript.started_at = time.monotonic()
                    print(f"  ▸ media stream open (call {self.call_sid})")

                elif event == "media":
                    # Twilio's own clock for this packet, in milliseconds.
                    self.latest_media_ts = int(msg["media"]["timestamp"])
                    # Hand the audio straight to OpenAI. No decoding needed —
                    # it's already base64'd mu-law, which is what OpenAI wants.
                    await self.send_openai(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": msg["media"]["payload"],
                        }
                    )

                elif event == "mark":
                    # A chunk we sent has finished playing out loud.
                    if self.mark_queue:
                        self.mark_queue.pop(0)

                elif event == "stop":
                    self.transcript.note("call_ended_by_far_side")
                    break

        except Exception as exc:
            self.transcript.note("phone_stream_error", str(exc))
        finally:
            self.finished.set()

    # =====================================================================
    # Direction 2: brain  --->  phone line
    # =====================================================================

    async def pump_brain_to_phone(self) -> None:
        """Read OpenAI's events: play its audio, and log both transcripts."""
        try:
            async for raw in self.openai_ws:
                evt = json.loads(raw)
                etype = evt.get("type")

                # ---- our patient's voice, streamed out to the phone --------
                if etype in AUDIO_DELTA_EVENTS and evt.get("delta"):
                    await self.play_audio(evt)

                # ---- what our patient said (text) --------------------------
                elif etype in BOT_TRANSCRIPT_DONE:
                    self.transcript.add("PATIENT_BOT", evt.get("transcript", ""))

                # ---- what the PGAI agent said (text) -----------------------
                elif etype in AGENT_TRANSCRIPT_DONE:
                    self.transcript.add("PGAI_AGENT", evt.get("transcript", ""))

                # ---- they started speaking: stop talking over them ---------
                elif etype == "input_audio_buffer.speech_started":
                    self.agent_speaking = True
                    self.agent_has_spoken = True
                    self.agent_quiet_since = None
                    await self.handle_barge_in()

                # ---- they stopped: start the clock on our opening gate -----
                elif etype == "input_audio_buffer.speech_stopped":
                    self.agent_speaking = False
                    self.agent_quiet_since = time.monotonic()

                # ---- our patient decided to hang up ------------------------
                elif etype == "response.function_call_arguments.done":
                    await self.handle_tool_call(evt)

                # ---- something went wrong ----------------------------------
                elif etype == "error":
                    await self.handle_error(evt)

        except Exception as exc:
            self.transcript.note("brain_stream_error", str(exc))
        finally:
            self.finished.set()

    async def play_audio(self, evt: dict) -> None:
        """Forward one chunk of generated speech to the phone line."""
        await self.send_twilio(
            {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {"payload": evt["delta"]},
            }
        )

        # Note when this sentence began, so that if we get interrupted we can
        # work out how much of it was actually heard.
        #
        # The clock has to restart on every NEW sentence. Each response gets a
        # fresh item_id, so a changed id means a new sentence. Without this
        # check response_start_ts stays pinned to the first reply of the whole
        # call: a barge-in twenty seconds later then computes "they heard
        # 39200ms" of a three-second sentence, OpenAI rejects the truncate
        # ("Audio content of 3100ms is already shorter than 39200ms"), and our
        # bot's memory quietly desynchronises from what was actually heard.
        item_id = evt.get("item_id")
        if item_id and item_id != self.last_assistant_item:
            self.last_assistant_item = item_id
            self.response_start_ts = self.latest_media_ts
        elif self.response_start_ts is None:
            self.response_start_ts = self.latest_media_ts

        # Ask Twilio to tell us when this chunk finishes playing.
        await self.send_twilio(
            {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": "chunk"},
            }
        )
        self.mark_queue.append("chunk")

    async def handle_barge_in(self) -> None:
        """
        The other side started talking while our bot was still speaking.

        Two things must happen, and BOTH matter:
          1. Tell Twilio to throw away audio it has buffered but not yet
             played, so our bot goes quiet immediately instead of finishing
             a sentence nobody is listening to.
          2. Tell OpenAI to truncate its memory of that sentence to the part
             that was actually heard. Without this, our bot believes it said
             a whole sentence the other party never heard, and the rest of
             the conversation quietly desynchronises.
        """
        if not self.mark_queue or self.response_start_ts is None:
            return  # our bot wasn't talking; nothing to interrupt

        heard_ms = self.latest_media_ts - self.response_start_ts

        if self.last_assistant_item:
            await self.send_openai(
                {
                    "type": "conversation.item.truncate",
                    "item_id": self.last_assistant_item,
                    "content_index": 0,
                    "audio_end_ms": max(0, heard_ms),
                }
            )

        await self.send_twilio({"event": "clear", "streamSid": self.stream_sid})

        self.transcript.note("barge_in", f"cut our bot off after {heard_ms}ms")
        self.mark_queue.clear()
        self.last_assistant_item = None
        self.response_start_ts = None

    async def handle_tool_call(self, evt: dict) -> None:
        if evt.get("name") != "end_call":
            return
        try:
            args = json.loads(evt.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        self.transcript.outcome = args
        self.transcript.note("bot_hung_up", args.get("outcome", ""))
        print(f"  ▸ patient ended the call: {args.get('outcome')}")

        # Let the receptionist finish. Hanging up the instant our patient
        # says "that's all I needed" chops their "alright, see you Wednesday"
        # off the recording and sounds abrupt — a real caller waits for the
        # other person to close the conversation too.
        deadline = time.monotonic() + HANGUP_GRACE_MAX_S
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if self.agent_speaking:
                continue
            quiet_since = self.agent_quiet_since
            if quiet_since is None or time.monotonic() - quiet_since >= HANGUP_QUIET_S:
                break

        # And a beat for our own goodbye audio to drain out of Twilio.
        await asyncio.sleep(1.0)
        await self.hang_up()

    async def handle_error(self, evt: dict) -> None:
        err = evt.get("error", {})
        message = err.get("message", "")
        self.transcript.note("openai_error", message)
        print(f"  ! OpenAI error: {message}")

        # If our session config used a field this account/model doesn't know
        # about, retry once with the stripped-down version rather than losing
        # the whole call.
        if not self.session_retry_done and (
            "session" in message.lower() or "unknown" in message.lower()
        ):
            self.session_retry_done = True
            print("  ↻ retrying with a minimal session config")
            await self.send_openai(self.session_config(minimal=True))

    # =====================================================================
    # Safety net
    # =====================================================================

    async def open_opening_gate(self, why: str) -> None:
        """
        Let our patient start talking.

        Called once, when the office has finished its greeting (or when we
        have waited long enough that something is clearly odd). It restores
        the real turn-detection settings and asks for one reply, which is
        our patient's opening line.
        """
        if self.opening_done:
            return
        self.opening_done = True
        self.transcript.note("opening_gate", why)

        await self.send_openai(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "audio": {"input": {"turn_detection": self.turn_detection}},
                },
            }
        )
        await self.send_openai({"type": "response.create"})

    async def watchdog(self) -> None:
        """
        Runs once a second. Two jobs:
          - Nudge our bot to speak if the line has been dead silent.
          - Hard-stop the call if it runs too long, so a stuck call can never
            quietly bill you for twenty minutes.
        """
        while not self.finished.is_set():
            await asyncio.sleep(1)
            elapsed = self.transcript.elapsed()

            # Has the office finished greeting us? They must have said
            # something, and then gone quiet for a beat. If they never do,
            # give up waiting rather than sitting mute for the whole call.
            if not self.opening_done and self.stream_sid:
                quiet_for = (
                    time.monotonic() - self.agent_quiet_since
                    if self.agent_quiet_since is not None
                    else 0.0
                )
                if (
                    self.agent_has_spoken
                    and not self.agent_speaking
                    and quiet_for >= OPENING_SILENCE_S
                ):
                    await self.open_opening_gate("office finished greeting")
                elif elapsed > OPENING_MAX_HOLD_S:
                    await self.open_opening_gate("timed out waiting for a greeting")

            if (
                not self.nudged
                and not self.transcript.turns
                and elapsed > GREETING_TIMEOUT_S
                and self.stream_sid
            ):
                self.nudged = True
                self.transcript.note("silence_nudge", "no speech detected")
                # Dead line: unmute ourselves too, or the nudge is pointless.
                await self.open_opening_gate("silence nudge")
                await self.send_openai(
                    {
                        "type": "response.create",
                        "response": {
                            "instructions": (
                                "The line has been silent. Say a short, natural "
                                "'Hello? Hi, can you hear me?' and nothing else."
                            )
                        },
                    }
                )

            if elapsed > config.MAX_CALL_SECONDS:
                self.transcript.note("max_duration_reached")
                print("  ▸ hard timeout reached, hanging up")
                await self.hang_up()
                return

    async def hang_up(self) -> None:
        """Ask Twilio to end the call. Safe to call more than once."""
        self.finished.set()
        if not self.call_sid:
            return
        from .telephony import hangup_call

        # The Twilio library is synchronous, so run it on a worker thread to
        # avoid freezing the audio loop.
        await asyncio.to_thread(hangup_call, self.call_sid)
