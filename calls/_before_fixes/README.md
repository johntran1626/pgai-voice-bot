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
