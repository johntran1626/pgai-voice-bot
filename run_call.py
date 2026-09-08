#!/usr/bin/env python3
"""
Entry point: places calls and saves the results.

    python run_call.py --list              list the scenarios
    python run_call.py 01_schedule_new     one call
    python run_call.py --all               all 14, back to back

Per call: validate .env, start the bridge server, open a public tunnel, dial,
wait for the conversation to end, then write the transcript and download the
recording into calls/<id>/. Server, tunnel and call share one process.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
from typing import Callable
import subprocess
import re
from datetime import datetime, timezone

import uvicorn

from patient import config, server, telephony
from patient.scenarios import BY_ID, SCENARIOS

# How long we'll wait for a call to connect and finish before giving up.
CALL_WAIT_TIMEOUT = config.MAX_CALL_SECONDS + 90
# Breather between back-to-back calls.
GAP_BETWEEN_CALLS = 12


# ---------------------------------------------------------------------------
# Infrastructure: the local server, and the public tunnel to it
# ---------------------------------------------------------------------------

async def start_server() -> uvicorn.Server:
    """Run the FastAPI app in the background of this same process."""
    cfg = uvicorn.Config(
        server.app, host="0.0.0.0", port=config.PORT, log_level="warning"
    )
    srv = uvicorn.Server(cfg)
    asyncio.create_task(srv.serve())
    # Wait for uvicorn to report it's actually listening.
    for _ in range(50):
        if srv.started:
            return srv
        await asyncio.sleep(0.1)
    raise RuntimeError(f"Server did not start on port {config.PORT}")


async def open_tunnel() -> tuple[str, "Callable[[], None]"]:
    """
    Open a public address routing to this process.

    Returns (hostname, close); close() is a no-op when nothing was started.
    Source is PUBLIC_HOST, ngrok, or a cloudflared quick tunnel — the last
    because some networks block ngrok subdomains outright.
    """
    if config.PUBLIC_HOST:
        host = config.PUBLIC_HOST.replace("https://", "").replace("http://", "")
        return host.rstrip("/"), lambda: None

    provider = config.TUNNEL_PROVIDER
    if provider == "auto":
        provider = "ngrok" if config.NGROK_AUTHTOKEN else "cloudflared"

    if provider == "cloudflared":
        return await open_cloudflared_tunnel()
    return await open_ngrok_tunnel()


async def open_ngrok_tunnel() -> tuple[str, "Callable[[], None]"]:
    if not config.NGROK_AUTHTOKEN:
        print(
            "No NGROK_AUTHTOKEN and no PUBLIC_HOST in .env.\n"
            "Twilio needs a public URL to send call audio to. Either get a\n"
            "free token at https://dashboard.ngrok.com/get-started/your-authtoken\n"
            "or set TUNNEL_PROVIDER=cloudflared (no account required)."
        )
        sys.exit(1)

    from pyngrok import conf, ngrok

    conf.get_default().auth_token = config.NGROK_AUTHTOKEN
    tunnel = await asyncio.to_thread(ngrok.connect, config.PORT, "http")
    host = tunnel.public_url.replace("https://", "").replace("http://", "")
    return host, ngrok.kill


async def open_cloudflared_tunnel() -> tuple[str, "Callable[[], None]"]:
    """
    Start a cloudflared quick tunnel and read the hostname it prints.

    No account or token; the URL is random and dies with the process.
    """
    if shutil.which("cloudflared") is None:
        print(
            "TUNNEL_PROVIDER is cloudflared but the `cloudflared` command\n"
            "isn't installed. On a Mac:  brew install cloudflared"
        )
        sys.exit(1)

    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{config.PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def close() -> None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    # cloudflared announces the URL on stderr a second or two after start.
    def read_host() -> str | None:
        for _ in range(400):
            line = proc.stdout.readline()
            if not line:
                return None
            match = re.search(r"https://([a-z0-9-]+\.trycloudflare\.com)", line)
            if match:
                return match.group(1)
        return None

    try:
        host = await asyncio.wait_for(asyncio.to_thread(read_host), timeout=60)
    except asyncio.TimeoutError:
        host = None

    if not host:
        close()
        print("cloudflared started but never printed a tunnel URL.")
        sys.exit(1)

    # The edge needs a few seconds before it will route to us. Without this
    # the very first request 404s and looks like a broken tunnel.
    await asyncio.sleep(8)
    return host, close


# ---------------------------------------------------------------------------
# One call, start to finish
# ---------------------------------------------------------------------------

async def run_one(scenario: dict, public_host: str) -> bool:
    sid = scenario["id"]
    print(f"\n{'=' * 70}\n▶  {sid} — {scenario['name']}\n{'=' * 70}")

    out_dir = os.path.join(config.CALLS_DIR, sid)
    if os.path.exists(os.path.join(out_dir, "transcript.txt")):
        print("   (a previous result exists here and will be replaced)")
        shutil.rmtree(out_dir)

    # Register before dialling: the agent can answer faster than we arm.
    done = server.expect(sid)

    try:
        call = await asyncio.to_thread(telephony.place_call, public_host, sid)
    except Exception as exc:
        print(f"   ✗ Twilio refused the call: {exc}")
        return False

    print(f"   dialing {config.TARGET_NUMBER} … call SID {call.sid}")

    # Wait for the bridge to say the conversation is over.
    try:
        await asyncio.wait_for(done.wait(), timeout=CALL_WAIT_TIMEOUT)
    except asyncio.TimeoutError:
        print("   ✗ timed out waiting for the call to finish")
        await asyncio.to_thread(telephony.hangup_call, call.sid)

    transcript = server.COMPLETED.get(sid)
    if transcript is None:
        print("   ✗ no transcript captured — the media stream never connected.")
        print("     Check that your ngrok tunnel is reachable (python check_setup.py).")
        return False

    transcript.call_sid = transcript.call_sid or call.sid
    transcript.write(out_dir)
    print(f"   ✓ transcript → {out_dir}/transcript.txt "
          f"({len(transcript.turns)} turns)")

    # Make sure Twilio agrees the call is over before asking for the audio.
    status = await asyncio.to_thread(telephony.wait_for_completion, call.sid)
    print(f"   call status: {status}; fetching recording …")

    mp3 = await asyncio.to_thread(telephony.download_recording, call.sid, out_dir)
    if mp3:
        size_kb = os.path.getsize(mp3) // 1024
        print(f"   ✓ recording  → {mp3} ({size_kb} KB)")
    else:
        print("   ! recording not available yet — re-run tools/fetch_recordings.py later")

    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(
            {
                "scenario_id": sid,
                "scenario_name": scenario["name"],
                "call_sid": call.sid,
                "from_number": config.TWILIO_FROM_NUMBER,
                "to_number": config.TARGET_NUMBER,
                "placed_at_utc": datetime.now(timezone.utc).isoformat(),
                "twilio_status": status,
                "duration_seconds": round(transcript.elapsed(), 2),
                "turn_count": len(transcript.turns),
                "outcome": transcript.outcome,
                "recording": os.path.basename(mp3) if mp3 else None,
            },
            f,
            indent=2,
        )

    # A call with barely any turns is a failed test, not a passed one.
    if len(transcript.turns) < 4:
        print("   ⚠ very few turns — listen to this one before counting it.")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main_async(scenarios: list[dict]) -> None:
    config.require(
        "OPENAI_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER",
    )

    srv = await start_server()
    host, close_tunnel = await open_tunnel()
    print(f"Bridge listening on port {config.PORT}, public at https://{host}")
    print(f"Calling {config.TARGET_NUMBER} from {config.TWILIO_FROM_NUMBER}")

    succeeded = 0
    try:
        for i, scenario in enumerate(scenarios):
            if await run_one(scenario, host):
                succeeded += 1
            if i < len(scenarios) - 1:
                print(f"\n   … waiting {GAP_BETWEEN_CALLS}s before the next call")
                await asyncio.sleep(GAP_BETWEEN_CALLS)
    finally:
        print(f"\n{'=' * 70}\n{succeeded}/{len(scenarios)} call(s) captured. "
              f"Results are in calls/\n{'=' * 70}")
        srv.should_exit = True
        # Let uvicorn shut down before leaving the loop, or its lifespan
        # task is cancelled mid-await and prints a spurious traceback.
        await asyncio.sleep(0.5)
        close_tunnel()


def main() -> None:
    parser = argparse.ArgumentParser(description="Call the PGAI test line.")
    parser.add_argument("scenario", nargs="*", help="scenario id(s) to run")
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = parser.parse_args()

    if args.list:
        for s in SCENARIOS:
            print(f"  {s['id']:<26} {s['name']}")
        return

    if args.all:
        chosen = SCENARIOS
    elif args.scenario:
        unknown = [s for s in args.scenario if s not in BY_ID]
        if unknown:
            print(f"Unknown scenario(s): {', '.join(unknown)}")
            print("Run `python run_call.py --list` to see valid ids.")
            sys.exit(1)
        chosen = [BY_ID[s] for s in args.scenario]
    else:
        parser.print_help()
        print("\nTip: start with `python run_call.py 01_schedule_new`")
        return

    asyncio.run(main_async(chosen))


if __name__ == "__main__":
    main()
