# Architecture

This bot calls PGAI's Athena test line (+1-805-439-8008) using
[Vapi.ai](https://vapi.ai) to handle real-time telephony and voice
(speech-to-text, text-to-speech, and turn-taking), with an Anthropic
Claude model acting as the "brain" that plays each patient persona.
`run_calls.py` (Python) drives the whole process: for every scenario in
`scenarios.py` it sends Vapi a call request built from that scenario's
system prompt and opening line, polls until the call ends, then
downloads the recording and transcript locally. `analyze_transcripts.py`
runs a second pass over the saved transcripts, asking Claude to flag
possible issues as a first draft, which is then manually verified and
written up in `bug_report.md`.

The main design decision was **not** building a custom telephony +
speech pipeline from scratch (e.g. Twilio Media Streams wired directly
to an STT/LLM/TTS loop). That approach gives more low-level control, but
getting natural turn-taking, barge-in handling, and low latency right by
hand is a significant engineering effort on its own -- and the grading
criteria make voice-conversation quality the #1 gate before anything
else is even reviewed. Vapi already solves that problem well, so using
it let the actual engineering time go into scenario design, prompt
quality for realistic personas, and bug analysis instead. The trade-off
is less control over the exact voice pipeline internals and a dependency
on a third-party platform's API, which is why `run_calls.py` saves the
raw JSON response for every call (`transcripts/*_raw.json`) -- if Vapi's
response shape changes, that's easy to debug without re-running calls.
