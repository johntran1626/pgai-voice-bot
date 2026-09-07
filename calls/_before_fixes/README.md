# Kept on purpose: call 01, before tuning

This is the **first** real call the simulator ever made, saved before the
fixes it prompted. Re-running a scenario overwrites its folder, so this
copy exists to show what changed and why.

What this call exposed, and what it changed:

| Seen here | Fix |
|---|---|
| Our patient talked over the office's recorded disclaimer, got cut off, and restated its request four times in 25s (`0:06`–`0:25`) | Prompt now says to sit silent through recordings, say the request once, and resume rather than restart after an interruption |
| `Audio content of 3100ms is already shorter than 39200ms`, three times | `response_start_ts` never restarted per sentence, so barge-in measured from the start of the *call*. Now restarts whenever `item_id` changes |
| Hard cutoff at 4:00, mid-booking | `MAX_CALL_SECONDS` 240 → 300 |

See `transcript.txt` and the event log at the bottom of it.

---

## v2 — after the pacing fixes, before the opening gate

`01_schedule_new_v2/` is the 2:12 call that reached `goal_achieved` cleanly:
no OpenAI errors, no hard timeout, `end_call` fired properly.

Two things were still wrong, both audible:

- Our patient answered the **recorded disclaimer** ("this call may be
  recorded…") and then repeated its whole request once a person actually
  greeted us. Not fixable by prompting — with turn detection on, the API
  creates a reply on every detected turn end, so the model had no option
  to stay silent. Fixed with the opening gate in `bridge.py`.
- It hung up the instant it said "that's all I needed", clipping the
  receptionist's sign-off. Fixed with the hang-up grace period.

v3 (the current `calls/01_schedule_new/`) is the first call with both.
