"""Reads .env and exposes each setting as a module-level constant."""

import os
import sys
from dotenv import load_dotenv

# Load .env from the project root into os.environ.
load_dotenv()

# --- The only number this dials --------------------------------------------
# Hard-coded on purpose: editing .env to point elsewhere aborts the run.
ALLOWED_TARGET = "+18054398008"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime")
PATIENT_VOICE = os.environ.get("PATIENT_VOICE", "coral")
# --- The bug-analysis pass (text only; separate from the live call) --------
# Runs on Anthropic or OpenAI credits. "auto" prefers Anthropic
# when an Anthropic key is present, since the live call already requires
# OpenAI, so the analysis can draw on a different balance.
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
# Which tunnel service gives Twilio a public route to this process.
#   auto        = ngrok if NGROK_AUTHTOKEN is set, else cloudflared
#   ngrok       = force ngrok
#   cloudflared = force a Cloudflare quick tunnel (no account needed)
# Some home routers and ISPs block ngrok subdomains as "high risk";
# switching to cloudflared is the usual way around that.
TUNNEL_PROVIDER = os.environ.get("TUNNEL_PROVIDER", "auto").strip().lower()

NGROK_AUTHTOKEN = os.environ.get("NGROK_AUTHTOKEN", "")
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "").strip()

MAX_CALL_SECONDS = int(os.environ.get("MAX_CALL_SECONDS", "300"))

# --- Turn detection: when the far side has finished talking ----------------
# This one setting trades response SPEED against INTERRUPTIONS. There is no
# value that wins both; tune it by listening to a recording.
#
#   VAD_MODE=semantic  asks a small model "did that sound like a finished
#                      thought?" Smarter about mid-sentence pauses, but adds
#                      latency before replying.
#     VAD_EAGERNESS    low | medium | high | auto  (the API rejects anything
#                      else). low = most patient, slowest to reply.
#
#   VAD_MODE=server    plain silence timer: reply once they have been quiet
#                      for VAD_SILENCE_MS. Snappier and predictable, but it
#                      cuts in on a mid-sentence pause.
VAD_MODE = os.environ.get("VAD_MODE", "semantic").strip().lower()
VAD_EAGERNESS = os.environ.get("VAD_EAGERNESS", "medium").strip().lower()
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "700"))

# Where every call's transcript, recording and metadata gets written.
CALLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "calls")


def require(*names: str) -> None:
    """
    Exit with a readable message if any required setting is blank.
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
            f"  but this program only dials {ALLOWED_TARGET}."
        )
        sys.exit(1)
