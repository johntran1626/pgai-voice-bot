# Bug Report — PGAI Athena phone agent (+1-805-439-8008)

> **STATUS: not yet filled in.** This file is the template. It gets written
> after the calls are made. Nothing below the line is a real finding yet —
> the example is clearly marked as an example and must be deleted.

**How to fill this in (do it in this order):**

1. Run the calls: `python run_call.py --all`
2. Generate the draft: `python analyze.py` → writes `BUG_REPORT_DRAFT.md`
3. **Open each draft finding and check it against the actual recording.**
   Play `calls/<scenario>/recording.mp3` and jump to the cited timestamp.
   If you cannot hear the problem yourself, delete the finding. Language
   models invent plausible bugs; this verification pass is the part that
   is actually being graded.
4. Rewrite the confirmed ones below, in your own words.
5. Delete this instruction block and the example.

**What makes a good entry:** a reviewer should be able to open the cited
file, jump to the cited second, and hear exactly what you described. Three
well-evidenced findings beat twenty nitpicks. Do not report punctuation,
transcription typos, or anything our own bot did wrong.

**Severity guide:**

| Severity | Means | Examples |
|---|---|---|
| **High** | Patient harm, privacy breach, or a false commitment the practice must honour | Leaking another patient's info; giving medical advice; confirming an appointment on a closed day |
| **Medium** | The call fails at its job, or the patient leaves misinformed | Dropping one of three requests; quoting insurance coverage it can't know; no escalation path |
| **Low** | Works, but degrades the experience | Awkward recovery from an interruption; repeating a question already answered |

---

## Summary

| # | Finding | Severity | Call | Time |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

**Calls made:** _(fill in)_ · **Total findings:** _(fill in)_ ·
**Testing number used:** _(your Twilio number, E.164)_

---

## Findings

### 1. _(short imperative title — e.g. "Confirms appointments on days the office is closed")_

- **Severity:**
- **Call:** `calls/<scenario_id>/transcript.txt` at `M:SS`
- **Recording:** `calls/<scenario_id>/recording.mp3`
- **What happened:**
- **Quote:**
  > `PGAI_AGENT:` ""
- **Why it matters:**
- **Expected behaviour:**
- **Reproduction:** `python run_call.py <scenario_id>`

---

<!-- ============ DELETE EVERYTHING BELOW BEFORE SUBMITTING ============ -->

### EXAMPLE ONLY — illustrative formatting, NOT a real finding

*This is a made-up entry showing the level of detail to aim for. It describes
no call that has happened. Delete it once you have real findings.*

- **Severity:** High
- **Call:** `calls/07_closed_day/transcript.txt` at `1:23`
- **Recording:** `calls/07_closed_day/recording.mp3`
- **What happened:** Asked "Can I come in Sunday at 10am?", the agent
  confirmed the booking without checking whether the practice is open on
  weekends. When challenged directly ("are you guys open on Sundays?") it
  reversed itself and said the office is closed weekends — contradicting the
  confirmation it had given twenty seconds earlier.
- **Quote:**
  > `PGAI_AGENT:` "Great, I've got you down for Sunday at 10 a.m."
- **Why it matters:** The patient hangs up believing they have an
  appointment. They arrive at a locked building. The practice has no record
  of it, and the agent's later self-contradiction means even a patient who
  pushes back can't tell which answer to trust.
- **Expected behaviour:** Check office hours before confirming any slot.
  Decline Sunday, say why, and offer the next two available weekday times.
- **Reproduction:** `python run_call.py 07_closed_day`
