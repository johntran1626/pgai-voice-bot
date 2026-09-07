"""
config.py — one place that reads your .env file and hands out settings.

Why this file exists: every other file needs the API keys and settings.
If each file read the .env itself, a typo would be scattered everywhere.
Instead everything imports from here, and we validate once, loudly.
"""

import os
import sys
from dotenv import load_dotenv

# Reads the .env file sitting next to this project and loads it into
# os.environ, the same place real environment variables live.
load_dotenv()

# --- The one number we are allowed to dial ---------------------------------
# Hard-coded on purpose. The assessment says every call must go to this
# number. If someone edits .env to point somewhere else, we refuse to run.
ALLOWED_TARGET = "+18054398008"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime")
PATIENT_VOICE = os.environ.get("PATIENT_VOICE", "coral")
# --- The bug-analysis pass (text only; separate from the live call) --------
# You can run this on Anthropic OR OpenAI credits. "auto" prefers Anthropic
# when an Anthropic key is present, since the live call already requires
# OpenAI and this lets you spend a different balance on the analysis.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANALYSIS_PROVIDER = os.environ.get("ANALYSIS_PROVIDER", "auto").strip().lower()
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "gpt-5")
ANTHROPIC_ANALYSIS_MODEL = os.environ.get(
    "ANTHROPIC_ANALYSIS_MODEL", "claude-opus-5"
)


def analysis_provider() -> str:
    """Return 'anthropic' or 'openai' for the transcript-analysis pass."""
    if ANALYSIS_PROVIDER in ("anthropic", "openai"):
        return ANALYSIS_PROVIDER
    return "anthropic" if ANTHROPIC_API_KEY else "openai"

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")

TARGET_NUMBER = os.environ.get("TARGET_NUMBER", ALLOWED_TARGET)

PORT = int(os.environ.get("PORT", "5050"))
# Which tunnel service gives Twilio a public door into this laptop.
#   auto        = ngrok if NGROK_AUTHTOKEN is set, else cloudflared
#   ngrok       = force ngrok
#   cloudflared = force a Cloudflare quick tunnel (no account needed)
# Some home routers and ISPs block ngrok subdomains as "high risk";
# switching to cloudflared is the usual way around that.
TUNNEL_PROVIDER = os.environ.get("TUNNEL_PROVIDER", "auto").strip().lower()

NGROK_AUTHTOKEN = os.environ.get("NGROK_AUTHTOKEN", "")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "").strip()

MAX_CALL_SECONDS = int(os.environ.get("MAX_CALL_SECONDS", "240"))

# Where every call's transcript, recording and metadata gets written.
CALLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "calls")


def require(*names: str) -> None:
    """
    Stop the program with a friendly message if a required setting is blank.

    Called at the top of scripts so you find out about a missing key in one
    second, instead of after a confusing crash three minutes later.
    """
    missing = [n for n in names if not globals().get(n)]
    if missing:
        print("Missing required values in your .env file:")
        for n in missing:
            print(f"  - {n}")
        print("\nFix: `cp .env.example .env`, then open .env and fill those in.")
        sys.exit(1)

    if TARGET_NUMBER != ALLOWED_TARGET:
        print(
            f"Refusing to run.\n"
            f"  TARGET_NUMBER in .env is {TARGET_NUMBER!r}\n"
            f"  but this assessment only permits calling {ALLOWED_TARGET}."
        )
        sys.exit(1)
