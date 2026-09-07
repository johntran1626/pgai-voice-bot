# Architecture

## The short version

A Python process on my laptop does three things at once. It runs a small
FastAPI server, exposes that server to the internet through an ngrok tunnel,
and then asks Twilio to dial +1-805-439-8008. The TwiML we hand Twilio says
`<Connect><Stream>`, which means "open a two-way WebSocket back to this URL
and pipe the call's audio through it". So the moment the PGAI agent answers,
Twilio starts pushing ~50 audio packets per second at our server. Our
`CallBridge` opens a second WebSocket — this one to OpenAI's Realtime API —
and sits between the two, forwarding audio in both directions. Twilio and
OpenAI both speak G.711 mu-law at 8kHz, the phone network's native codec, so
the bridge passes the base64 audio straight through without ever decoding,
resampling or re-encoding it. That single format choice is why the whole
bridge is one file and why latency stays low enough to sound human.

The interesting engineering is not the plumbing, it's knowing *when to talk*.
OpenAI's semantic voice-activity detection decides when the other party has
finished a thought, and I run it at `eagerness: low` because two AIs talking
to each other will otherwise stampede over one another — a model that would
politely wait out a human's pause will happily trample another model's
measured delivery. When the agent does start talking over us, the bridge
handles barge-in in two parts: it tells Twilio to `clear` its playback buffer
so our bot goes quiet instantly, and it sends OpenAI a
`conversation.item.truncate` so the model's memory is trimmed to *only the
audio the other side actually heard*. Skipping that second half is the
classic bug — the bot believes it delivered a whole sentence nobody heard,
and the rest of the conversation quietly desynchronises. Everything the call
produces is written to `calls/<scenario>/`: a timestamped transcript of both
sides, a stereo `.mp3` from Twilio, and an event log recording every
interruption and timeout, which is what lets a bug report cite an exact
moment.

---

## Data flow

```
 run_call.py  ─┬─→ starts FastAPI server (in-process)      [patient/server.py]
               ├─→ opens ngrok tunnel        → https://xxxx.ngrok-free.app
               └─→ Twilio REST: "dial +18054398008"        [patient/telephony.py]
                                    │
                                    ▼
                    ☎ PGAI agent answers at +1-805-439-8008
                                    │
        Twilio executes our TwiML: <Connect><Stream url="wss://xxxx/media/07_closed_day">
                                    │
                                    ▼
        ┌───────────────────────────────────────────────────────┐
        │                  CallBridge  [patient/bridge.py]      │
        │                                                       │
        │  Twilio WS  ──── mu-law audio ────→  OpenAI WS        │
        │      ↑                                    │           │
        │      └──────── mu-law audio ──────────────┘           │
        │                                                       │
        │  also captures: their transcript (speech-to-text)     │
        │                 our transcript (free, from the model) │
        │                 barge-in events, errors, timeouts     │
        └───────────────────────────────────────────────────────┘
                                    │
                                    ▼
                   calls/07_closed_day/{transcript.txt,
                                        transcript.json,
                                        recording.mp3,
                                        meta.json}
                                    │
                                    ▼
                    analyze.py  →  BUG_REPORT_DRAFT.md
                                    │
                        (human verification pass)
                                    ▼
                              BUG_REPORT.md
```

---

## Key decisions and the alternatives I rejected

### Speech-to-speech (Realtime API) instead of an STT → LLM → TTS pipeline

The obvious alternative is to chain three services: Deepgram to transcribe,
a text model to think, ElevenLabs to speak. I've built that shape before and
rejected it here for two reasons.

The first is latency. Each hop adds its own delay and, worse, each hop waits
for the previous one to *finish*. Realistically that lands around 1.5–2.5
seconds of dead air per turn. The brief explicitly grades "realistic pacing"
and "minimal awkward pauses", and two seconds of silence after every sentence
is exactly what a reviewer would flag. Speech-to-speech keeps it near 500ms
because the model starts emitting audio before it has finished deciding what
the whole sentence will be.

The second reason matters more for a *testing* tool: a transcription pipeline
throws away everything that isn't words. Tone, hesitation, and the exact
moment someone started talking are all destroyed by the STT step — and those
are precisely the signals I need to judge turn-taking quality and to make our
patient sound like a person rather than a script being read aloud.

The trade-off I accepted: less introspection. In a pipeline I could log the
exact text the model saw before it replied. Here the audio goes into a black
box. I compensate by asking OpenAI to transcribe the inbound audio *anyway*,
purely for the record, which gives me both sides of the transcript without
putting transcription on the critical path.

### Twilio + my own bridge instead of Vapi, Retell or Bland

A managed voice-agent platform would have collapsed this project into one
HTTP POST. I started there — the first commit in this repo is a Vapi
scaffold — and moved off it deliberately.

The deciding factor is that this is a *test harness*, not a product. Scenario
08 exists to interrupt the agent on purpose, and needs turn detection tuned
to be aggressive. Every other scenario needs it tuned to be patient. On a
managed platform that's a dashboard setting I'd be fighting; here it's a
per-scenario dictionary key (`turn_detection` in `scenarios.py`) that
overrides the default for exactly one call. Likewise, `bridge.py` can log the
millisecond at which each barge-in happened, because the bridge is the thing
doing the interrupting. That event log is the evidence behind the
turn-taking findings in the bug report.

The honest counter-argument: a managed platform would have been faster to
build and has better-tuned endpointing out of the box. I judged that the
control was worth the extra day, and that "we couldn't examine the thing we
were supposed to be measuring" would have been the worse outcome.

### Running locally through ngrok instead of deploying

Twilio needs a public URL, and the reflex is to deploy to Render or Fly. I
didn't, because the deployment adds a build step, a secrets store, and a log
console between me and the bug — and this is a tool that gets run maybe
twenty times, by one person, interactively, while they listen to the call
happen. `pyngrok` starts the tunnel from inside `run_call.py`, so the whole
thing is one command in one terminal with the transcript printing live. If
this ever needed to run on a schedule, `PUBLIC_HOST` in `.env` bypasses ngrok
entirely and points at a deployed instance — that's the one hook I left for it.

### Recording via Twilio, not by saving the audio myself

The bridge has both audio streams in hand and could write its own `.wav`.
Twilio's `record=True` with `recording_channels="dual"` is better: it records
at the carrier, so it captures what *actually went down the wire* including
anything my code dropped, and it puts each speaker on a separate stereo
channel. When reviewing a suspected talk-over, being able to hear the two
sides separated is the difference between "I think they overlapped" and
"they overlapped for 800ms". It also arrives as `.mp3`, which is a required
submission format, with no `ffmpeg` dependency.

### Two providers, split by what each can actually do

The live call runs on OpenAI because that leg needs realtime speech-to-speech
and Anthropic has no such API — its surface is text, vision and documents, and
Claude's own voice mode is a turn-based pipeline over an external TTS provider.
The analysis pass has no such constraint: it reads text, so it runs on either,
and defaults to Claude when an Anthropic key is present. The seam is one
function (`make_analyzer()`), so nothing downstream knows which ran.

### Two-layer prompts

`prompts.py` holds `VOICE_DISCIPLINE`, which is identical for all 14 calls,
and `scenarios.py` holds each patient's goal. This split exists because of
what went wrong early: speech-to-speech models default hard to *assistant*
behaviour. Ours would drift into "How can I help you today?" — which is
absurd, since it's the one making the call — and into reading numbered lists
aloud. Those failures were identical across every scenario, so the fix
belongs in one shared block, not copy-pasted fourteen times. Now tuning
call-realism is a single edit that improves all 14 calls at once.

---

## Tuning, if the calls don't sound right

Listen to the first recording before running the rest. Symptoms and fixes:

| Symptom | Cause | Fix |
|---|---|---|
| The bots talk over each other | Our VAD jumps in too fast | `.env`: `VAD_EAGERNESS=low` (the most patient value the API accepts — `low`/`medium`/`high`/`auto`; there is no `very_low`) |
| Our patient is slow to reply | Semantic VAD is deliberating | `.env`: `VAD_EAGERNESS=high`, or `VAD_MODE=server` + `VAD_SILENCE_MS=600` for a plain, snappy silence timer |
| Our patient talks into the silence after asking a question | Prompt, not VAD | The "STOP AND WAIT" rule in `prompts.py`; the watchdog only nudges when *nothing* has been said all call |
| Our patient answers the recorded disclaimer, then repeats itself | Turn detection *forces* a reply on every detected turn end, so no prompt can stop it | The opening gate in `bridge.py`: the call starts with `create_response: false`, and `open_opening_gate()` restores it once the office has spoken and gone quiet for `OPENING_SILENCE_S` |
| Our patient hangs up on the receptionist's goodbye | `end_call` fired immediately | `handle_tool_call()` waits for `HANGUP_QUIET_S` of silence (capped at `HANGUP_GRACE_MAX_S`) before dropping the line |
| Long dead air after they finish | Our VAD waits too long | `eagerness` `low` → `"medium"` |
| Our bot monologues | Persona drift | Tighten the turn-length rule in `VOICE_DISCIPLINE` |
| Our bot sounds like an assistant | Same | Strengthen the "you are the caller" rules |
| Calls never end | `end_call` not being used | Check the event log in `transcript.txt`; `MAX_CALL_SECONDS` is the backstop |
| Their side missing from transcript | Inbound transcription failed | Look for `openai_error` in the event log |

---

## Known limitations

- **One call at a time.** `run_call.py` is sequential. Concurrency would need
  a scenario-keyed registry rather than the single `DONE` dict in `server.py`,
  and 14 sequential calls take 35 minutes, which is fine.
- **ngrok URLs are ephemeral.** Each run gets a new hostname. Harmless here
  because we place the call ourselves and hand Twilio the current URL, but it
  would break anything inbound.
- **`analyze.py` output is a draft, not a finding.** Language models
  confabulate plausible bugs. Every item in `BUG_REPORT.md` was checked
  against the audio by hand; the draft file is kept in the repo so the
  difference between the two is visible.
- **The self-test mocks OpenAI.** `tools/selftest.py` proves the bridge's
  wiring, framing and barge-in logic, not that OpenAI's live behaviour is
  unchanged. It caught a real shutdown deadlock during development, which is
  what it's for.
