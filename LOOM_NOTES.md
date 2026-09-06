# Loom video notes

Two videos are required, both **public**, both **with your webcam on**, and
recorded **in your own voice**. Free Loom accounts cap at 5 minutes; the
brief asks for max 3. Aim for 2:30.

Practise once before recording. Do not script it word for word — reviewers
are explicitly assessing communication, and read-aloud scripts sound read
aloud. Know your beats and talk.

---

## Video 1 — Project walkthrough (max 3 min)

**Have open beforehand:** a finished `calls/07_closed_day/transcript.txt`,
the mp3 ready to play, `ARCHITECTURE.md`, and a terminal in the project.

**0:00–0:20 — What it is.** Camera on, say it plainly: "I built a robot
patient that phones your AI receptionist, has a real conversation, and finds
where it breaks. 14 scenarios, from routine bookings to deliberately trying
to get it to leak another patient's data."

**0:20–0:50 — Play 20 seconds of actual audio.** Lead with this. The brief
says voice quality is graded before anything else, so prove it works before
you explain anything. Pick your most natural-sounding call.

**0:50–1:40 — How it works.** One pass over the diagram in
`ARCHITECTURE.md`: Twilio dials the number, streams the audio to a bridge on
my laptop, the bridge forwards it to OpenAI's Realtime API and pipes the
reply back. Both sides speak mu-law so nothing gets converted.

**1:40–2:20 — The two decisions you want them to hear.** These map directly
onto the grading criteria, so say them explicitly:
- *Why speech-to-speech rather than transcribe → think → speak:* latency.
  Chaining three services adds ~2 seconds of dead air per turn, which is
  exactly what "unnatural pacing" sounds like.
- *Why my own bridge rather than Vapi:* I started on Vapi — it's the first
  commit in the repo. I moved off it because this is a test harness. One
  scenario needs to interrupt aggressively and the others need to be patient,
  and owning the bridge makes that a one-line per-scenario override.

**2:20–2:50 — Your best bug.** Show the transcript, play the moment in the
audio, say why it matters in the real world.

**2:50–3:00 — Close.** "Repo's linked, README has one-command setup."

---

## Video 2 — Debugging with AI (max 3 min)

They want to see *how you iterate*, not a highlight reel. Show a real problem
being solved, including the part where the first attempt was wrong.

**The strongest story here is the shutdown deadlock**, because it's a genuine
bug that a test caught before it ever cost money:

**0:00–0:20 — The symptom.** "My self-test said no transcript was produced,
even though the conversation had clearly happened. Everything else passed."

**0:20–1:10 — Show the prompt you actually used.** Read it out. A good one
gives the AI the evidence, not just the complaint — the failing output, the
relevant function, and what you already ruled out.

**1:10–2:00 — The diagnosis, in your own words.** This is the part being
graded — show you understood the answer rather than pasting it. "Three jobs
run at once and I was waiting for all three to finish. When the far end hangs
up, the Twilio loop ends but the OpenAI socket is still open, so job two waits
forever for a message that's never coming. `gather` waits for all three, so
it waits forever too."

**2:00–2:40 — The fix and the proof.** Show the change — wait on the shared
finish flag, then cancel the rest — then re-run `python tools/selftest.py`
and show it go green on camera. Showing the test pass is the whole point.

**2:40–3:00 — What you took from it.** "With concurrent jobs, 'wait for
everything to finish' is usually the wrong instinct. Wait for the thing that
decides you're done, then cancel the rest."

---

## Before you submit

- [ ] Both Looms set to **public** — open each link in a private window to check
- [ ] Webcam visible in both
- [ ] GitHub repo set to **public**
- [ ] `.env` is **not** in the repo; `.env.example` **is**
- [ ] At least 10 calls in `calls/`, each with a transcript **and** an mp3
- [ ] `BUG_REPORT.md` filled in, example block deleted
- [ ] Submission form: your Twilio number in E.164 (`+1...`), the same one
      used for every call
- [ ] Receipts from OpenAI and Twilio attached for reimbursement
