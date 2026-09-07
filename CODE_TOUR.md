# Code tour — what every part does, from scratch

This is written for someone who can read a little Python but has never built
a phone system, used a WebSocket, or written async code. Read it start to
finish once, then open the files alongside it.

---

# Part 1 — Five ideas you need first

Skip any you already know.

### 1. A phone call is just a stream of numbers

When you speak into a phone, a chip measures the air pressure 8,000 times a
second and turns each measurement into a number. That stream of numbers is
your voice. Playing it back through a speaker recreates the sound.

The phone network compresses those numbers using a scheme from 1972 called
**G.711 mu-law** (written `mu-law`, or `pcmu`). It's 8,000 samples a second,
one byte each — so 8 kilobytes per second, which is tiny.

**Why this matters here:** Twilio sends us mu-law. OpenAI's Realtime API
*accepts* mu-law. So we never convert anything. We take the packet from one
socket and hand the identical bytes to the other. If the two sides had
disagreed on format, we'd need an audio-conversion library and a resampling
step, and this project would be twice the size and noticeably laggier.

### 2. A WebSocket is a phone call between two programs

A normal web request is a letter: you send one, you get one back, done. That
doesn't work for live audio — you'd be mailing 50 letters a second.

A **WebSocket** is a connection that stays open, and either side can send a
message whenever it wants, as many as it likes. That's exactly what streaming
audio needs.

This project has **two** WebSockets open at once during a call:
- one to **Twilio**, carrying the phone call's audio
- one to **OpenAI**, carrying audio to and from the AI brain

The whole job of `bridge.py` is to sit between them.

### 3. base64 is how you put audio inside text

WebSocket messages here are JSON — text. Audio is raw bytes, which aren't
valid text. **base64** is a standard way to rewrite arbitrary bytes as
ordinary letters and digits so they survive being put in a JSON string.

You'll see `"payload": "f39/f39..."` in the code. That's base64'd audio. We
never decode it — we just move the string across. That's the passthrough.

### 4. async lets one program wait for many things at once

Normally Python does one thing at a time. If you write "wait for a message
from Twilio", the program freezes there, and any message from OpenAI piles up
unread.

**async** solves this. Functions declared `async def` can be paused at any
`await`, letting other work run while they wait. `asyncio.create_task(...)`
starts a job in the background.

In `bridge.py` three jobs run simultaneously:
1. read from Twilio, forward to OpenAI
2. read from OpenAI, forward to Twilio
3. a watchdog that checks the clock every second

None of them blocks the others. This is why a single Python process can hold
both sides of a live phone call.

### 5. An API key is a password, and it must never reach GitHub

Every service (OpenAI, Twilio) gives you a secret string that proves requests
are yours — and gets billed to you. Anyone who finds it can spend your money.
Bots actively scan public GitHub repos for leaked keys, and they find them in
minutes.

So: real keys live in `.env`, which is listed in `.gitignore`, which means
git ignores it completely. What gets committed instead is `.env.example` —
the same variable names with the values blank. That tells other people what
to fill in without giving anything away.

---

# Part 2 — The files, in the order they matter

```
pgai-voice-bot/
├── run_call.py            ← START HERE. The command you type.
├── check_setup.py         ← Validates everything without spending money.
├── analyze.py             ← Turns transcripts into a draft bug report.
├── patient/
│   ├── config.py          ← Reads .env. One place for all settings.
│   ├── scenarios.py       ← The 14 test cases.
│   ├── prompts.py         ← How to behave like a human on a phone.
│   ├── telephony.py       ← Talks to Twilio: dial, hang up, get the mp3.
│   ├── server.py          ← The web server Twilio connects to.
│   ├── bridge.py          ← ★ The heart. Audio in both directions.
│   └── transcript.py      ← Records who said what, when.
└── tools/
    ├── selftest.py        ← Tests the bridge with fake everything.
    └── fetch_recordings.py← Re-downloads mp3s that weren't ready.
```

---

## `run_call.py` — the conductor

This is the file you actually run. It's a checklist executed in order.

**`start_server()`** launches the FastAPI web server *inside this same Python
process*, in the background. Most tutorials make you run the server in one
terminal and the caller script in another. Doing both here means one command,
one window — which matters a lot when you're new.

**`open_tunnel()`** solves the "how does Twilio find my laptop" problem. Your
laptop is behind a router with no public address; Twilio can't reach it.
ngrok creates a public URL like `https://a1b2c3.ngrok-free.app` that forwards
everything to port 5050 on your machine. `pyngrok` starts it from inside
Python, so you never touch a second terminal.

**`run_one(scenario, host)`** is one complete call:

```python
done = server.expect(sid)        # 1. tell the server a call is coming
call = telephony.place_call(...) # 2. dial the number
await asyncio.wait_for(done.wait(), ...)  # 3. sleep until the call ends
transcript.write(out_dir)        # 4. save the transcript
telephony.download_recording(...)# 5. save the mp3
```

Step 1 is the subtle one. We register the "I'm expecting this call" *before*
dialling. If we dialled first, the agent could answer and the audio start
arriving before we were ready to catch it — a **race condition**, a bug where
correctness depends on which of two things happens first. Registering first
removes the race entirely.

Step 3 uses `asyncio.wait_for` with a timeout, so a call that never connects
fails after a few minutes instead of hanging forever.

---

## `patient/config.py` — settings in one place

Reads `.env` and exposes each value as a normal Python variable.

The `require()` function is worth copying into your future projects. Called
at the top of every script, it checks the settings that script needs and
exits with a *readable* message if any are blank. Without it, a missing key
surfaces three minutes later as an incomprehensible crash from deep inside a
library.

It also enforces the one hard safety rule:

```python
if TARGET_NUMBER != ALLOWED_TARGET:
    print("Refusing to run..."); sys.exit(1)
```

The assessment says every call goes to +1-805-439-8008. That's hard-coded in
the source, not just the config, so no amount of editing `.env` can make this
program dial your ex.

---

## `patient/prompts.py` — teaching a model to act human

This is the highest-leverage file in the project, and it's all English.

`VOICE_DISCIPLINE` is a block of instructions applied to all 14 calls. Every
rule in it exists because the model did something wrong without it:

- *"You are NEVER an assistant"* — speech models are trained to be helpful
  assistants, so ours kept saying "How can I help you today?" while it was
  the one making the call.
- *"SPEAK IN SHORT TURNS, 5 to 25 words"* — without a length cap it delivers
  paragraphs. Nobody does that on the phone.
- *"Never read lists, never say 'firstly'"* — it writes when it should talk.
- *"Give ONE piece of information at a time"* — it would blurt out name, date
  of birth, insurance and phone number in one breath, which never happens and
  also skips past the exact identity-verification behaviour we're testing.
- *"You have a goal. Steer toward it."* — without this it's agreeable and
  passive, and the call ends with nothing tested.

`build_instructions()` glues that shared block to one scenario's `goal`.

`END_CALL_TOOL` is a **tool** (also called a function): a capability we
describe to the model so it can choose to invoke it. Here it means "hang up".
Without it, calls only end when the other side gives up or our timeout fires,
which wastes money and leaves 40 seconds of silence on every recording.

---

## `patient/scenarios.py` — the 14 test cases

A list of dictionaries. Each has:

- `id` — short slug, also the output folder name
- `name` — human-readable title
- `goal` — the character and mission, injected into the prompt
- `watch_for` — what a reviewer should check

`watch_for` is a design detail worth noticing. `analyze.py` passes it to the
bug-finding model, so the analysis is graded *against the actual intent of
that test* rather than against a generic "find problems". For scenario 12,
that's the difference between "the agent was a bit terse" and "the agent
confirmed another patient exists, which is a disclosure".

Scenario 08 also carries a `turn_detection` override. That call is *supposed*
to interrupt, so it swaps the polite default for an aggressive setting. This
is the concrete payoff of building our own bridge instead of using a managed
platform: a per-call behaviour change is one dictionary key.

---

## `patient/telephony.py` — talking to the phone company

**`build_twiml()`** builds TwiML, Twilio's little XML instruction language.
Ours is one instruction:

```xml
<Response><Connect><Stream url="wss://host/media/07_closed_day" /></Connect></Response>
```

`<Connect><Stream>` means "open a two-way WebSocket to this address and pipe
the call's audio through it". The two-way part is essential — `<Start><Stream>`
is the one-way version, which would let us listen but never speak.

Notice the scenario id is in the URL. That's how the server knows which
patient to play the instant the socket opens, before any audio arrives.

**`place_call()`** does the dialling, with `record=True` and
`recording_channels="dual"`. Dual means each speaker gets their own stereo
channel — invaluable when you're trying to hear who talked over whom.

**`download_recording()`** retries in a loop, because Twilio needs a few
seconds after hang-up to finish encoding the audio. Asking immediately
usually returns nothing.

---

## `patient/server.py` — the doorbell

Deliberately tiny. It accepts the WebSocket, looks up the scenario from the
URL, hands everything to a `CallBridge`, and stores the result.

```python
COMPLETED: dict[str, Transcript] = {}
DONE: dict[str, asyncio.Event] = {}
```

An `asyncio.Event` is a flag one part of the program can wait on and another
can raise. `run_call.py` waits on it; the bridge raises it when the call ends.
That's how two independent parts of the same process coordinate without
either one polling in a loop.

---

## `patient/bridge.py` — ★ the heart

Read this one slowly. Everything else is setup.

### `session_config()` — the one message that configures the call

Sent once, immediately after connecting to OpenAI. It sets the persona, the
voice, the audio format on both sides, and the turn-taking rules.

The turn-taking part is the most important setting in the project:

```python
{"type": "semantic_vad", "eagerness": "low", "interrupt_response": True}
```

**VAD** is voice activity detection — deciding when the other person has
stopped talking. The naive version is "they've been quiet for 500ms, go".
That fails constantly, because people pause mid-sentence to think.

**Semantic** VAD asks a small model *"does that sound like a finished
thought?"* — so a pause after "I'd like to book an appointment for..." is
correctly read as not-finished.

`eagerness: low` makes it wait longer still. That's specifically because both
sides here are AIs. A setting tuned to wait out a human's pause will happily
trample another model's measured delivery, and you get two robots barking
over each other. This one word is the difference between a call that sounds
natural and one that's unusable.

### The two pumps

`pump_phone_to_brain()` reads Twilio's messages:

| Twilio event | What we do |
|---|---|
| `start` | Learn the stream and call ids; start the clock |
| `media` | Forward the audio to OpenAI, note Twilio's timestamp |
| `mark` | A chunk we sent finished playing; pop the queue |
| `stop` | The far end hung up; shut down |

`pump_brain_to_phone()` reads OpenAI's events:

| OpenAI event | What we do |
|---|---|
| `response.output_audio.delta` | Play that audio down the phone line |
| `response.output_audio_transcript.done` | Log what **our bot** said |
| `conversation.item.input_audio_transcription.completed` | Log what **they** said |
| `input_audio_buffer.speech_started` | They started talking — handle barge-in |
| `response.function_call_arguments.done` | Our bot chose to hang up |
| `error` | Log it; retry with a simpler config if needed |

Those last two transcript rows are how we get **both sides** of the
conversation. Our own words come free from the model. Theirs comes from
asking OpenAI to transcribe the inbound audio as a side job.

Note that the code accepts two spellings of each event name:

```python
AUDIO_DELTA_EVENTS = {"response.output_audio.delta", "response.audio.delta"}
```

OpenAI renamed these when the Realtime API left beta. Accepting both means
the project keeps working either way — a cheap hedge against a moving API.

### `handle_barge_in()` — the trickiest 20 lines

When the other party starts talking while our bot is mid-sentence, two things
must happen, and people routinely do only the first:

**1. Stop the sound.** Twilio has buffered several seconds of our bot's
speech that hasn't played yet. `{"event": "clear"}` throws it away so our bot
goes quiet immediately.

**2. Fix the model's memory.** OpenAI thinks it said the *entire* sentence.
It didn't — the listener only heard the first second. If we don't correct
this, the model reasons from a version of events that never occurred, and the
rest of the call quietly drifts. `conversation.item.truncate` trims its memory
to just the part actually heard.

How do we know how much was heard? Every Twilio audio packet carries a
millisecond timestamp. We remember the timestamp when our sentence *started*
playing, subtract it from the current one, and that difference is how much of
our bot's speech reached the other side:

```python
heard_ms = self.latest_media_ts - self.response_start_ts
```

We use Twilio's clock rather than our laptop's on purpose: it reflects what
the phone line actually did, not what our code hoped it did.

**The mark queue** answers "is our bot talking *right now*?" Each time we send
audio we also send a `mark`, and Twilio echoes it back once that chunk has
finished playing. A non-empty queue means audio is still in flight. If it's
empty, there's nothing to interrupt and we do nothing — which stops us from
"interrupting" during silence.

### `watchdog()` — the safety net

Runs once a second, doing two jobs:

- If the line is silent 12 seconds in, nudge our bot to say "Hello?" so a
  missed greeting doesn't waste the call.
- If the call exceeds `MAX_CALL_SECONDS`, hang up. A stuck call that nobody
  is watching is a bill that grows on its own.

### The shutdown fix

The first version of `run()` ended with `asyncio.gather(...)` on all three
jobs — wait for all of them to finish. That **deadlocked**, and
`tools/selftest.py` caught it before any real call was placed.

Here's the bug. When the far end hangs up, the Twilio loop ends. But the
OpenAI socket is still perfectly healthy and open, so the second loop waits
forever for a message that will never come. `gather` waits for all three, so
it waits forever too.

The fix waits on the *shared finish flag* instead, then cancels whatever is
still running:

```python
await self.finished.wait()
for task in tasks:
    task.cancel()
await asyncio.gather(*tasks, return_exceptions=True)
```

This is a good lesson to keep: with concurrent jobs, "wait for everything to
finish" is usually wrong. What you want is "wait for the thing that decides
we're done, then stop the rest."

---

## `patient/transcript.py` — the record

Collects turns in memory, writes two files at the end. `transcript.txt` is for
humans; `transcript.json` keeps exact decimal timings and is what `analyze.py`
reads.

Every entry is stamped with seconds since the call started, formatted `1:23`.
That's not decoration — it's what lets a bug report say *"transcript at 1:23"*
so a reviewer can jump straight to that moment in the mp3.

The separate `events` list records things nobody said: barge-ins, timeouts,
errors, the bot hanging up. Turn-taking bugs live in that list, not in the
words.

---

## `check_setup.py` — fail cheaply

Checks Python version, installed packages, every `.env` value, that your
Twilio credentials work, that you actually own the number you claim, that
OpenAI will open a Realtime session, and that the internet can reach your
laptop through the tunnel — **without dialling anyone**.

The OpenAI check is neat: it opens a real Realtime WebSocket and closes it
immediately. That proves your key works *and* that your account has Realtime
access, and costs nothing because no audio is exchanged.

The principle: when a system has six things that can be misconfigured,
build the tool that tells you which one, before the expensive step.

---

## `analyze.py` — a first draft, not an answer

Sends each transcript to a text model with the scenario's `watch_for`, and
asks for structured JSON findings.

**This is the one part that can run on either Anthropic or OpenAI.** Worth
understanding why: the live phone call needs a model that takes audio in and
gives audio back over a live connection, and Anthropic doesn't offer that.
Reading a transcript is ordinary text work, so both providers can do it.
`make_analyzer()` picks one and returns a function; the rest of the file
doesn't know or care which ran. That's a useful shape to copy — isolate the
provider-specific bit behind one function, and the surrounding code stays
provider-agnostic.

The two backends differ in how they guarantee valid JSON. Claude is given a
JSON schema via `output_config`, which the server enforces. OpenAI is asked
for a JSON object. Either way `parse_json()` tolerates a stray code fence,
because a malformed reply shouldn't lose you 14 calls' worth of analysis. `response_format={"type": "json_object"}`
forces valid JSON out, so we can parse it instead of scraping prose.

The system prompt spends as many words on what **not** to report as what to
report — no punctuation nitpicks, no criticising our own bot, and an explicit
"an honest empty result is more useful than filler". Without that, these
models pad the list to look thorough.

**This output is a draft.** Language models confabulate plausible bugs.
Everything in `BUG_REPORT.md` was confirmed against the audio by hand. The
draft file stays in the repo so the difference is visible.

---

## `tools/selftest.py` — testing without a phone

Stands up a fake OpenAI server and a fake Twilio client, runs a real
`CallBridge` between them, and asserts that audio flows both ways, both
transcript sides are captured, and barge-in fires correctly.

This is a **mock**: a stand-in that behaves enough like the real thing to
test your code against. It costs nothing, needs no keys, and runs in three
seconds — so you can run it after every edit.

It earned its keep immediately by catching the shutdown deadlock described
above, which would otherwise have shown up as calls mysteriously hanging
after the agent said goodbye.

---

# Part 3 — Read the code in this order

1. `patient/scenarios.py` — plain English, no machinery. See what's tested.
2. `patient/prompts.py` — also plain English. See how behaviour is shaped.
3. `patient/config.py` — short, and every other file uses it.
4. `run_call.py`, `main()` then `run_one()` — the sequence of events.
5. `patient/telephony.py` — how a phone call actually gets placed.
6. `patient/server.py` — 40 lines, connects steps 4 and 7.
7. `patient/bridge.py` — the real thing. Read `session_config()`, then the
   two pumps, then `handle_barge_in()` last.
8. `tools/selftest.py` — shows what "correct" means for the bridge.

## Things to try, to learn by breaking it

- Change `PATIENT_VOICE` in `.env` and hear the difference.
- Set `eagerness` to `"high"` in `bridge.py` and listen to the bots trample
  each other. Set it back.
- Comment out the `clear` line in `handle_barge_in()` and hear our bot keep
  talking after being interrupted.
- Write a 15th scenario. Copy one, change the `goal`, run it.
- Delete a required line from `VOICE_DISCIPLINE` and hear the persona drift.

---

# Glossary

| Term | Meaning |
|---|---|
| **API key** | A password that proves a request is yours, and gets billed to you. |
| **async / await** | Python syntax letting one program wait on many things at once. |
| **base64** | Encoding that rewrites raw bytes as plain text, so audio fits in JSON. |
| **barge-in** | Talking over someone who is already speaking. |
| **codec** | A scheme for compressing audio. Here: G.711 mu-law. |
| **deadlock** | Two parts of a program each waiting on the other; nothing moves. |
| **E.164** | The international phone format: `+14155550123`. |
| **endpoint** | One address a server answers on, e.g. `/media/07_closed_day`. |
| **mock** | A fake stand-in used to test code without the real service. |
| **mu-law / pcmu** | The phone network's audio codec. 8,000 samples/sec, 1 byte each. |
| **ngrok** | A service giving your laptop a temporary public web address. |
| **race condition** | A bug where correctness depends on which of two things happens first. |
| **SID** | Twilio's id for a thing. Calls start `CA`, recordings `RE`, accounts `AC`. |
| **tool / function** | A capability described to a model so it can choose to use it. |
| **TwiML** | Twilio's XML language for telling it what to do on a call. |
| **VAD** | Voice activity detection — deciding when someone stopped talking. |
| **venv** | A private per-project box of Python packages. |
| **WebSocket** | A connection that stays open so both sides can send anytime. |
