# Setup — every step, in order

Written assuming you have never used a terminal much. Do these in order.
Total time: about 30 minutes, most of it waiting on account verification.

---

## Step 0 — Open a terminal and get to the project

On a Mac, press `Cmd + Space`, type `Terminal`, press Enter. A window opens
where you type commands.

Two commands are worth knowing:
- `cd <folder>` — "change directory", i.e. walk into a folder.
- `ls` — "list", i.e. show what's in the folder you're standing in.

Get into this project folder:

```bash
cd ~/"claude code"/pgai-voice-bot
```

Check you're in the right place — you should see `run_call.py` in the output:

```bash
ls
```

---

## Step 1 — Set up Python

A **virtual environment** ("venv") is a private box of Python packages that
belongs to just this project, so installing things here can't break anything
else on your Mac.

Create it (once, ever):

```bash
python3 -m venv venv
```

Activate it. **You must do this every time you open a new terminal window**
to work on this project:

```bash
source venv/bin/activate
```

You'll know it worked because your prompt now starts with `(venv)`.

Install the project's packages:

```bash
pip install -r requirements.txt
```

---

## Step 2 — OpenAI account (the bot's brain and voice)

> **You cannot skip this step, and Anthropic credit does not replace it.**
> The Realtime API is what actually holds the phone conversation, and
> Anthropic has no equivalent — so without this, there is no call. If you
> have Anthropic credit, it covers the *bug analysis* in Step 8; see the
> optional note at the end of this step.

1. Go to **https://platform.openai.com/signup** and create an account. This
   is the *developer* platform, not ChatGPT — a ChatGPT Plus subscription
   does **not** give you API access. They're separate.
2. Go to **https://platform.openai.com/settings/organization/billing** and
   add a payment method, then **add $10 of credit**. The Realtime API will
   not work on a zero-balance account, and this is the single most common
   reason people get a mysterious `403` later.
3. Go to **https://platform.openai.com/api-keys** → **Create new secret key**.
   Name it `pgai-voice-bot`.
4. **Copy it now.** It starts with `sk-` and OpenAI will never show it to you
   again. Paste it somewhere safe for a minute.

---

### Optional: use Anthropic credit for the bug analysis

If you already have Anthropic API credit, you can spend it on the analysis
step instead of OpenAI's.

The live call **cannot** run on Anthropic — the phone leg needs a realtime
speech-to-speech API, and Anthropic doesn't have one. But `analyze.py` just
reads text transcripts, so either provider works there.

Grab a key at **https://console.anthropic.com/settings/keys** and put it in
`.env` as `ANTHROPIC_API_KEY`. That's it — `ANALYSIS_PROVIDER=auto` picks
Claude automatically whenever that key is present.

> A Claude Pro or Max subscription is **not** API credit. They're separate
> balances; this needs credit on console.anthropic.com.

---

## Step 3 — Twilio account (the actual phone line)

Twilio is the company that owns real phone numbers and connects real calls.

1. Go to **https://www.twilio.com/try-twilio** and sign up. Verify your email
   and your personal mobile number when asked.
2. **Upgrade to a paid account.** In the console there's an **Upgrade**
   button; add a payment method and put ~$20 on it.

   > **This step is not optional.** A Twilio *trial* account can only call
   > phone numbers you have personally verified. +1-805-439-8008 is not
   > yours to verify, so on a trial account every call will fail with
   > error 21219. Upgrading is what makes this project work at all.

3. Buy a phone number: **Phone Numbers → Manage → Buy a number**. Choose
   **Local**, not Toll-free, and pick any US number with a checkmark in the
   **Voice** column. It costs about $1.15/month. Click **Buy**.

   > **Why local and not toll-free?** Real patients call a clinic from a
   > local mobile number; an 800 number calling *in* looks like a
   > telemarketer, and phone systems sometimes treat those differently. If
   > the agent then behaves oddly you can't tell a real bug from spam
   > handling. Local is also cheaper, both monthly and per minute, and
   > skips the extra verification toll-free numbers require.
   >
   > Optional nicety: the search box lets you filter by area code. Picking
   > **805** (the area the practice is in) makes your caller ID look local
   > to them. Any US area code works — this just costs nothing to do.

   After buying, Twilio shows a **"Finish setting up your number"** checklist
   — SHAKEN/STIR, Voice Integrity, Branded Calling, CNAM. **Skip all of it.**
   The only entry that matters is the compliance profile, which Twilio
   completes for you during purchase. The rest are paid add-ons for
   businesses running real outbound campaigns: they brand your caller ID and
   protect your number's spam reputation. You are making ~14 calls to a
   single bot that has no screen and does not read caller names. None of
   these affect whether a call connects.
4. Go to the console home page: **https://console.twilio.com**. In the
   **Account Info** panel you'll see:
   - **Account SID** — starts with `AC`
   - **Auth Token** — click the eye icon to reveal it
   Copy both.
5. Also copy the number you just bought, in the format `+14155550123`
   (a plus sign, then country code, then the number, no spaces or dashes).
   **This is the number you report on the submission form.** Use only this
   one number for every test call.

---

## Step 4 — ngrok account (a doorbell for your laptop)

Here's the problem ngrok solves. Twilio needs to *send audio to your laptop*
while the call is happening. But your laptop sits behind your home router
and has no public address on the internet — there's no way for Twilio to
find it. ngrok fixes that by giving you a temporary public web address that
forwards straight to a port on your machine.

1. Go to **https://dashboard.ngrok.com/signup** — free, no card needed.
2. Go to **https://dashboard.ngrok.com/get-started/your-authtoken** and copy
   the token shown.

You don't need to download anything — the project starts ngrok for you.

---

## Step 5 — Put the keys in `.env`

`.env` is a plain text file holding your secrets. It is listed in
`.gitignore`, which means **it never gets uploaded to GitHub**. That's the
whole point: your keys stay on your machine.

Make your own copy of the template:

```bash
cp .env.example .env
```

Open it in a text editor:

```bash
open -e .env
```

Fill in the five values you collected, so it looks like this (with your real
values, obviously):

```
OPENAI_API_KEY=sk-proj-abc123...
TWILIO_ACCOUNT_SID=ACabc123...
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_FROM_NUMBER=+14155550123
NGROK_AUTHTOKEN=2abc123...
```

Leave everything else as it is. Save the file and close the editor.

---

## Step 6 — Check everything before spending money

```bash
python check_setup.py
```

This validates every key, confirms you own the Twilio number, opens a real
tunnel and proves the internet can reach your laptop — **without placing a
call.** Read the output top to bottom. Every line should be a `✓`.

Common failures and what they mean:

| What you see | What's wrong | Fix |
|---|---|---|
| `could not open a Realtime session` (403) | No credit on the OpenAI account | Add $10 in billing (Step 2) |
| `TWILIO_FROM_NUMBER not found on this account` | Typo, or you didn't buy a number | Re-copy it in `+1...` format |
| `credentials rejected` | Auth Token copied wrong | Re-copy from console.twilio.com |
| `tunnel check failed` + `your network is blocking ngrok` | Your router/ISP filters ngrok subdomains as "high risk" — **not** an ngrok or token problem | `brew install cloudflared`, then set `TUNNEL_PROVIDER=cloudflared` in `.env` and re-run. Cloudflare tunnels usually pass the same filters |
| `cloudflared ... isn't installed` | `TUNNEL_PROVIDER=cloudflared` but the tool is missing | `brew install cloudflared` |
| `tunnel not configured` | No `NGROK_AUTHTOKEN` and no `PUBLIC_HOST` | Paste your token from the ngrok dashboard |

You can also test the audio engine itself with no keys and no cost:

```bash
python tools/selftest.py
```

---

## Step 7 — Make your first real call

```bash
python run_call.py 01_schedule_new
```

You'll see the conversation print out live, line by line, as it happens.
It takes about two minutes. Then look at what it saved:

```bash
open calls/01_schedule_new/recording.mp3
```

**Listen to it.** Do not skip this. If the two bots talk over each other or
there are long silences, tune it before burning 13 more calls — see the
"Tuning" section in [ARCHITECTURE.md](ARCHITECTURE.md).

When one call sounds good, run the rest:

```bash
python run_call.py --all
```

That's about 35 minutes and roughly $6. Leave it running.

---

## Step 8 — Draft the bug report

```bash
python analyze.py
```

This writes `BUG_REPORT_DRAFT.md`. It is a **draft**. Open each finding,
listen to that moment in the recording, and confirm it actually happened —
AI analysis invents plausible bugs that aren't there. Move the real ones
into `BUG_REPORT.md` in your own words. That human verification pass is
exactly what the reviewers are grading.

---

## Step 9 — Put it on GitHub

First, prove your secrets aren't about to be uploaded. This command should
print **nothing at all**:

```bash
git status --porcelain | grep -w ".env$"
```

If it prints something, stop and tell someone — `.gitignore` isn't working.

Now stage and commit everything:

```bash
git add -A && git commit -m "Voice bot: Twilio + OpenAI Realtime patient simulator with 14 scenarios"
```

Create the repository on GitHub. If you have the GitHub CLI installed:

```bash
gh repo create pgai-voice-bot --public --source=. --push
```

If you don't, do it by hand instead:
1. Go to **https://github.com/new**
2. Name it `pgai-voice-bot`, set it to **Public**, and do **not** tick
   "Add a README" (you already have one).
3. Click **Create repository**, then run the two commands GitHub shows you
   under "…or push an existing repository", which look like:

```bash
git remote add origin https://github.com/YOUR_USERNAME/pgai-voice-bot.git && git branch -M main && git push -u origin main
```

Finally, open your repo in a browser and click through the files. Confirm
that `.env` is **not** there and `.env.example` **is**. Confirm the `.mp3`
files play in the browser.

---

## Step 10 — Record the two Loom videos

See [LOOM_NOTES.md](LOOM_NOTES.md) for a shot-by-shot outline of both.
