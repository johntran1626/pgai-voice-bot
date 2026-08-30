# PGAI Voice Bot Challenge

An automated "patient" voice bot that calls PGAI's Athena test line,
runs through a set of realistic scheduling/refill/edge-case scenarios,
and saves the recording + transcript of each call for bug analysis.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how it works and why.
See [bug_report.md](bug_report.md) for findings.

## Setup

1. **Get accounts / keys** (all have free tiers to start):
   - [Vapi.ai](https://vapi.ai) — sign up, buy/connect a phone number
     (Vapi walks you through connecting a Twilio number), grab your API
     key from the dashboard.
   - [Anthropic](https://console.anthropic.com) — grab an API key.

2. **Clone this repo and install dependencies:**
   ```bash
   git clone <your-repo-url>
   cd pgai-voice-bot
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure your environment:**
   ```bash
   cp .env.example .env
   ```
   Then open `.env` and fill in `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`,
   and `ANTHROPIC_API_KEY`. Leave `TARGET_PHONE_NUMBER` as-is
   (`+18054398008`) — the script refuses to run against any other number.

## Run

Run every scenario (places one call per scenario, waits for each to
finish, saves recording + transcript):

```bash
python run_calls.py
```

Run just one scenario while iterating/debugging (much faster than a full
run):

```bash
python run_calls.py 07_weekend_edge_case
```

After calls finish, get an AI-assisted first pass at bugs (review this
by hand before trusting it — see `bug_report.md`):

```bash
python analyze_transcripts.py
```

## Output

- `recordings/*.mp3` — audio of each call
- `transcripts/*.txt` — text transcript of each call
- `transcripts/*_raw.json` — full raw API response (useful for debugging
  if Vapi's response format ever changes)
- `bug_report_draft.md` — AI-drafted issues, unverified
- `bug_report.md` — final, human-reviewed findings

## Scenarios

See [scenarios.py](scenarios.py) for the full list and personas —
covers new appointments, rescheduling, cancellation, refills, office
hours/insurance questions, and edge cases (weekend booking, interrupting
the agent, vague requests, out-of-scope medical questions, name/date
misunderstandings).
