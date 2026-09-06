# PGAI Voice Bot — an automated "patient" that stress-tests a medical AI receptionist

This project builds a robot patient. It picks up a phone line, dials
**+1-805-439-8008**, has a real spoken conversation with the AI receptionist
on the other end, and writes down everything both sides said — then flags
where the receptionist got things wrong.

It runs **14 scenarios**: routine bookings, reschedules, cancellations,
refill requests, insurance questions, and deliberately nasty edge cases
(asking for a Sunday appointment, interrupting mid-sentence, asking for a
diagnosis, and probing whether it will leak another patient's information).

- **How it works and why:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Line-by-line explanation of the code:** [CODE_TOUR.md](CODE_TOUR.md)
- **Step-by-step account setup:** [SETUP.md](SETUP.md)
- **What we found:** [BUG_REPORT.md](BUG_REPORT.md)
- **Outline for the two Loom videos:** [LOOM_NOTES.md](LOOM_NOTES.md)

---

## Quick start

If you already have your accounts set up, this is the whole thing:

```bash
git clone https://github.com/YOUR_USERNAME/pgai-voice-bot.git
```

```bash
cd pgai-voice-bot && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Open `.env`, paste in your keys (see [SETUP.md](SETUP.md) for where to get
each one), then:

```bash
python check_setup.py
```

That checks every credential and proves Twilio can reach your laptop —
**without placing a call or spending money.** When it's all green:

```bash
python run_call.py 01_schedule_new
```

That places one real call and saves the results. Once you're happy:

```bash
python run_call.py --all
```

Then draft the bug report from the transcripts:

```bash
python analyze.py
```

---

## All the commands

| Command | What it does |
|---|---|
| `python check_setup.py` | Verifies keys, phone number, OpenAI access and the public tunnel. Places no calls. |
| `python tools/selftest.py` | Runs the audio bridge against a fake phone and fake AI. No keys, no cost. Run after changing `bridge.py`. |
| `python run_call.py --list` | Lists the 14 scenarios. |
| `python run_call.py 07_closed_day` | Runs one scenario. |
| `python run_call.py --all` | Runs all 14, back to back. |
| `python analyze.py` | Reads the transcripts, drafts `BUG_REPORT_DRAFT.md`. |
| `python tools/fetch_recordings.py` | Re-downloads any `.mp3` Twilio hadn't finished encoding. |

---

## What you get after a call

Each call writes a folder under `calls/`:

```
calls/07_closed_day/
├── transcript.txt     ← readable, timestamped, both sides
├── transcript.json    ← same thing, machine-readable, for analyze.py
├── recording.mp3      ← stereo: agent on one channel, our bot on the other
├── meta.json          ← call SID, numbers, duration, status
└── analysis.json      ← written later by analyze.py
```

The transcript looks like this:

```
[0:03] PGAI_AGENT: Thanks for calling. How can I help you today?
[0:06] PATIENT_BOT: Hi — can I come in this Sunday at ten?
[0:11] PGAI_AGENT: Sure, I've got you down for Sunday at 10 a.m.
[0:15] PATIENT_BOT: Wait, are you guys actually open on Sundays?
```

---

## Requirements

- **Python 3.10 or newer** (`python3 --version`)
- Three free accounts: OpenAI, Twilio, ngrok — see [SETUP.md](SETUP.md)
- No `ffmpeg`, no database, no deployment. Twilio hands us `.mp3` directly.

## Cost

Roughly **$0.35–0.55 per 2-minute call**, so all 14 calls land around
**$5–8**. Twilio charges about $0.014/min for the call plus ~$1.15/month for
the number; the OpenAI Realtime audio is the rest. `MAX_CALL_SECONDS` in
`.env` is a hard stop so a stuck call can never run up a bill.

## Security

`.env` holds your real keys and is listed in `.gitignore` — it must never be
committed. `.env.example` documents the variable names with no values, which
is the file that *is* committed. The code refuses to dial any number other
than +1-805-439-8008.
