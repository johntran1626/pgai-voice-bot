# Automated patient — stress-testing a medical AI receptionist over the phone

A simulated patient that places real phone calls to an AI receptionist, holds
a natural spoken conversation, steers it toward a goal, and records both sides
so the agent's failures can be evidenced rather than described.

**14 scenarios, 14 calls, 37 minutes of recorded conversation.** Every call has
a stereo recording and a timestamped transcript in [`calls/`](calls/).

## What it found

Thirteen verified findings are written up in **[BUG_REPORT.md](BUG_REPORT.md)**.
The most serious:

- **A caller was given a third party's appointment times and clinic address**
  with no authorization check, after creating a profile under a different name.
- **Appointment data is not isolated between callers** — four differently-named
  callers were shown the same two appointments in one session, and one had a
  legitimate booking blocked because of it.
- **A date of birth was invented on every profile created** and the agent
  declined to correct it when told.
- **A booking was confirmed that was never made** — fifteen seconds after
  saying it was still searching for availability.
- **Clinical advice after disclaiming it** — a differential and self-triage
  criteria, which escalated rather than stopped when pressed.

The report also records what the agent got right, and flags where a finding
could be an artefact of the demo environment rather than inflating it.

## Where to look

| | |
|---|---|
| **[BUG_REPORT.md](BUG_REPORT.md)** | The findings, with call, timestamp and verbatim quote for each |
| **[calls/](calls/)** | 14 recordings + transcripts. `calls/_before_fixes/` keeps earlier runs that drove specific fixes |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | How it works, and the alternatives rejected |
| **[patient/bridge.py](patient/bridge.py)** | The interesting part — turn-taking, barge-in, the opening gate |

## How it works

Twilio dials the number and streams the call audio over a WebSocket to a local
FastAPI server. `CallBridge` sits between that socket and a second one to
OpenAI's Realtime API, forwarding audio both ways. Twilio and OpenAI both speak
G.711 mu-law at 8kHz, so audio passes through base64-encoded and is never
decoded, resampled or re-encoded — which is why latency stays low enough to
sound human.

The hard part is knowing *when to talk*. Semantic voice-activity detection
decides when the other party has finished a thought; barge-in both clears
Twilio's playback buffer and truncates the model's memory to the audio actually
heard; and the call opens muted so the patient waits through the recorded
greeting instead of talking over it. [ARCHITECTURE.md](ARCHITECTURE.md) covers
the reasoning, including why speech-to-speech over an STT→LLM→TTS pipeline,
why a custom bridge over a managed voice platform, and how to tune turn-taking.

## Running it

Requires Python 3.10+, an OpenAI account with Realtime access, and a **paid**
Twilio account with a voice-capable number. No ffmpeg, no database, no
deployment.

```bash
git clone https://github.com/johntran1626/pgai-voice-bot.git && cd pgai-voice-bot
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env      # then fill it in — see the table below
python check_setup.py     # verifies everything; places no calls, spends nothing
python run_call.py --all
python analyze.py
```

The audio engine can be exercised with **no keys and no cost** — it runs the
real bridge against a fake phone line and a fake model:

```bash
python tools/selftest.py
```

### Environment variables

Copy `.env.example` to `.env` and fill in the first four. Everything else has a
working default.

| Variable | Required | Notes |
|---|---|---|
| `OPENAI_API_KEY` | **yes** | Needs credit; the Realtime API is what holds the conversation |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | **yes** | A *trial* account cannot dial unverified numbers — every call fails with error 21219 |
| `TWILIO_FROM_NUMBER` | **yes** | E.164, e.g. `+14155550123`. Local, not toll-free |
| `NGROK_AUTHTOKEN` | one of | Free token. Or set `TUNNEL_PROVIDER=cloudflared` and install `cloudflared` — no account needed |
| `ANTHROPIC_API_KEY` | no | `analyze.py` is plain text and runs on either provider; `ANALYSIS_PROVIDER=auto` picks Claude when this is set. The **call** always needs OpenAI |
| `VAD_MODE` / `VAD_EAGERNESS` / `VAD_SILENCE_MS` | no | Turn-taking. Trades reply speed against interruptions; tuned by ear |
| `TARGET_NUMBER` | no | The number under test. Hardcoded as well — the code refuses to dial anything else |
| `MAX_CALL_SECONDS` | no | Hard stop, so a stuck call can't run up a bill |
| `PORT` / `PUBLIC_HOST` / `REALTIME_MODEL` / `PATIENT_VOICE` | no | Defaults are fine |

> If `check_setup.py` reports **"your network is blocking ngrok"**, that is a
> router or ISP filter, not a misconfiguration — many classify ngrok tunnels as
> high risk. `brew install cloudflared` and set `TUNNEL_PROVIDER=cloudflared`.

### Commands

| Command | What it does |
|---|---|
| `python check_setup.py` | Verifies keys, number ownership, Realtime access and the tunnel. Places no calls. |
| `python tools/selftest.py` | Full audio bridge against fakes. No keys, no cost. |
| `python run_call.py --list` | Lists the 14 scenarios. |
| `python run_call.py 07_closed_day` | Runs one scenario. |
| `python run_call.py --all` | Runs all 14 back to back. |
| `python analyze.py` | Drafts candidate findings from the transcripts. |

## What a call produces

```
calls/07_closed_day/
├── transcript.txt     readable, timestamped, both sides, with an event log
├── transcript.json    machine-readable, consumed by analyze.py
├── recording.mp3      stereo — agent on one channel, patient on the other
├── meta.json          call SID, duration, outcome
└── analysis.json      candidate findings
```

```
[0:03] PGAI_AGENT: Thanks for calling. How can I help you today?
[0:06] PATIENT_BOT: Hi — can I come in this Sunday at ten?
[0:11] PGAI_AGENT: Sure, I've got you down for Sunday at 10 a.m.
[0:15] PATIENT_BOT: Wait, are you guys actually open on Sundays?
```

The event log at the foot of each transcript records barge-ins, silences and
errors with millisecond timings — that is the evidence behind the turn-taking
findings.

## Cost and safety

About **$0.35–0.55 per two-minute call**; all 14 land around **$5–8**. The
OpenAI Realtime audio dominates; Twilio is pennies per call plus ~$1.15/month
for the number.

`.env` holds real keys and is gitignored — `.env.example` is the committed
version and carries names only. The dialled number is hardcoded in
`patient/config.py`, so no edit to `.env` can point this at anyone else.
