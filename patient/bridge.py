"""
Audio bridge between a Twilio call and OpenAI's Realtime API.

    Twilio (phone line)  <---->  CallBridge  <---->  Realtime API

Both sides speak G.711 mu-law at 8kHz, so audio passes through base64-encoded
and is never transcoded. Handles turn-taking, barge-in and call teardown.
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

# The Realtime API renamed these at GA; both spellings are accepted.
AUDIO_DELTA_EVENTS = {"response.output_audio.delta", "response.audio.delta"}
BOT_TRANSCRIPT_DONE = {
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
}
AGENT_TRANSCRIPT_DONE = {
    "conversation.item.input_audio_transcription.completed",
}

# Silence before prompting an opening line.
GREETING_TIMEOUT_S = 12

# --- Letting the office finish its greeting --------------------------------
# Offices answer with a recorded disclaimer before a person speaks. With
# turn detection enabled the API creates a response every time it decides a
# turn ended, so no prompt can make the model wait — it is not given the
# option. The call therefore starts with create_response off and is un-muted
# only once the far side has spoken and then gone quiet.
OPENING_SILENCE_S = 1.5   # quiet this long after they speak = our turn
OPENING_MAX_HOLD_S = 25   # never stay muted longer than this, whatever happens

# --- Hanging up politely ---------------------------------------------------
# After signing off, wait for the far side to close the conversation.
HANGUP_QUIET_S = 1.2      # they've been quiet this long = conversation over
HANGUP_GRACE_MAX_S = 12   # but never hold the line open longer than this

# --- Dead air mid-call -----------------------------------------------------
# GREETING_TIMEOUT_S only fires when nothing has been said all call, so a
# conversation that dies partway through went unnoticed until the hard cap.
MID_CALL_SILENCE_S = 22   # nobody has spoken this long = something is wrong
MID_CALL_GIVE_UP_S = 45   # still nothing after this = hang up


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
        # what the phone line actually did, not what this process thinks.
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

        # --- dead-air detection -------------------------------------------
        # Updated whenever EITHER side makes a sound.
        self.last_voice_at = time.monotonic()
        self.mid_call_prodded = False
        # How much audio we have actually streamed for the current sentence,
        # so a barge-in can't claim they heard more than we sent.
        self.audio_sent_ms = 0.0

    # =====================================================================
    # Session setup
    # =====================================================================

    def session_config(self, minimal: bool = False) -> dict:
        """
        Persona, voice, audio format and turn detection, in one message.

        `minimal=True` drops the optional fields, as a retry if the API
        rejects a newer one.
        """
        # A scenario may override turn detection outright (08_barge_in does);
        # otherwise it comes from .env. Trade-offs documented in config.py.
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
        # Send a muted version first: the model listens to the greeting but
        # cannot answer it. open_opening_gate() restores these settings.
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
                    # Transcribe the far side too, for the transcript.
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

            tasks = [
                asyncio.create_task(self.pump_phone_to_brain()),
                asyncio.create_task(self.pump_brain_to_phone()),
                asyncio.create_task(self.watchdog()),
            ]

            # Wait on whichever task decides the call is over, then cancel
            # the rest. gather() on all three deadlocks: when the far end
            # hangs up the phone loop ends, but the OpenAI socket stays open.
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
                    # Timestamps run from the stream opening, not from
                    # whenever this object was constructed.
                    self.transcript.started_at = time.monotonic()
                    print(f"  ▸ media stream open (call {self.call_sid})")

                elif event == "media":
                    self.latest_media_ts = int(msg["media"]["timestamp"])
                    # Already base64 mu-law, which is what the API expects.
                    await self.send_openai(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": msg["media"]["payload"],
                        }
                    )

                elif event == "mark":
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
                    self.last_voice_at = time.monotonic()
                    self.agent_speaking = True
                    self.agent_has_spoken = True
                    self.agent_quiet_since = None
                    await self.handle_barge_in()

                # ---- they stopped: start the clock on our opening gate -----
                elif etype == "input_audio_buffer.speech_stopped":
                    self.agent_speaking = False
                    self.agent_quiet_since = time.monotonic()
                    self.last_voice_at = time.monotonic()

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

        # Mark when this sentence began, so a barge-in can measure how much
        # was heard. A changed item_id marks a new sentence: left pinned to
        # the first reply of the call, a later barge-in
        # reports a duration longer than the sentence and the truncate is
        # rejected, silently desyncing the model's memory from what was heard.
        item_id = evt.get("item_id")
        if item_id and item_id != self.last_assistant_item:
            self.last_assistant_item = item_id
            self.response_start_ts = self.latest_media_ts
            self.audio_sent_ms = 0.0
        elif self.response_start_ts is None:
            self.response_start_ts = self.latest_media_ts

        # mu-law at 8kHz is 8 bytes per millisecond.
        self.audio_sent_ms += len(base64.b64decode(evt["delta"])) / 8.0
        self.last_voice_at = time.monotonic()

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
        Handle the far side talking over us.

        Both halves matter: clear Twilio's playback buffer, and truncate the
        model's memory to the audio actually heard. Without the second, the
        model believes it said sentences nobody received.
        """
        if not self.mark_queue or self.response_start_ts is None:
            return  # our bot wasn't talking; nothing to interrupt

        heard_ms = self.latest_media_ts - self.response_start_ts
        # Twilio's inbound clock runs ahead of our outbound audio, so bound
        # this by what was actually streamed.
        heard_ms = int(min(heard_ms, self.audio_sent_ms))

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

        # Let the far side finish. Hanging up on our own last word clips
        # their sign-off from the recording.
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
        Un-mute the patient once the far side has finished its greeting.

        Restores the real turn-detection settings and requests one reply.
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
        Once a second: open the opening gate, nudge on silence, recover from
        mid-call dead air, and enforce MAX_CALL_SECONDS.
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

            # Dead air mid-call: prod once, then stop paying for silence.
            if self.opening_done and not self.finished.is_set():
                quiet = time.monotonic() - self.last_voice_at
                if quiet > MID_CALL_GIVE_UP_S:
                    self.transcript.note(
                        "dead_air", f"no speech for {int(quiet)}s — hanging up")
                    print(f"  ▸ line went dead for {int(quiet)}s, hanging up")
                    await self.hang_up()
                    continue
                if quiet > MID_CALL_SILENCE_S and not self.mid_call_prodded:
                    self.mid_call_prodded = True
                    self.transcript.note(
                        "dead_air", f"no speech for {int(quiet)}s — prodding")
                    await self.send_openai({
                        "type": "response.create",
                        "response": {"instructions": (
                            "The line has gone quiet. Say a short, natural "
                            "'Sorry, are you still there?' and nothing else."
                        )},
                    })

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
