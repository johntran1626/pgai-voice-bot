#!/usr/bin/env python3
"""
check_setup.py — run this BEFORE your first real call.

It checks every moving part and tells you exactly what's broken, without
dialling anybody or spending money on a call.

    python check_setup.py

Green ✓ all the way down means `python run_call.py 01_schedule_new` should work.
"""

import asyncio
import sys

OK, BAD, WARN = "✓", "✗", "!"
failures = 0


def report(status: str, label: str, detail: str = "") -> None:
    global failures
    if status == BAD:
        failures += 1
    line = f"  {status} {label}"
    if detail:
        line += f"\n      {detail}"
    print(line)


# ---------------------------------------------------------------------------

def check_python_and_deps() -> None:
    print("\nPython and packages")
    if sys.version_info < (3, 10):
        report(BAD, "Python 3.10+", f"you have {sys.version.split()[0]}")
    else:
        report(OK, f"Python {sys.version.split()[0]}")

    for mod, pkg in [
        ("fastapi", "fastapi"),
        ("anthropic", "anthropic"),
        ("uvicorn", "uvicorn"),
        ("websockets", "websockets"),
        ("twilio", "twilio"),
        ("openai", "openai"),
        ("dotenv", "python-dotenv"),
        ("pyngrok", "pyngrok"),
    ]:
        try:
            __import__(mod)
            report(OK, pkg)
        except ImportError:
            report(BAD, pkg, "fix: pip install -r requirements.txt")


def check_env() -> None:
    from patient import config

    print("\n.env values")
    for name in (
        "OPENAI_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER",
    ):
        value = getattr(config, name)
        if value:
            report(OK, name, f"set ({value[:6]}…)" if len(value) > 8 else "set")
        else:
            report(BAD, name, "missing — open .env and fill it in")

    if config.TARGET_NUMBER == config.ALLOWED_TARGET:
        report(OK, "TARGET_NUMBER", config.TARGET_NUMBER)
    else:
        report(BAD, "TARGET_NUMBER",
               f"is {config.TARGET_NUMBER}, must be {config.ALLOWED_TARGET}")

    provider = config.analysis_provider()
    if provider == "anthropic":
        if config.ANTHROPIC_API_KEY:
            report(OK, "bug analysis", f"Anthropic {config.ANTHROPIC_ANALYSIS_MODEL}")
        else:
            report(BAD, "bug analysis", "ANALYSIS_PROVIDER=anthropic but "
                                        "ANTHROPIC_API_KEY is blank")
    else:
        report(OK, "bug analysis", f"OpenAI {config.ANALYSIS_MODEL}")

    if config.NGROK_AUTHTOKEN or config.PUBLIC_HOST:
        report(OK, "public URL source",
               config.PUBLIC_HOST or "ngrok (auto)")
    else:
        report(BAD, "public URL source", "set NGROK_AUTHTOKEN or PUBLIC_HOST")


def check_twilio() -> None:
    from patient import config, telephony

    print("\nTwilio")
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN):
        report(WARN, "skipped", "credentials missing")
        return
    try:
        account = telephony.client().api.accounts(config.TWILIO_ACCOUNT_SID).fetch()
        report(OK, "credentials valid", f"account status: {account.status}")
    except Exception as exc:
        report(BAD, "credentials rejected", str(exc)[:160])
        return

    try:
        numbers = telephony.client().incoming_phone_numbers.list(limit=20)
        owned = [n.phone_number for n in numbers]
        if config.TWILIO_FROM_NUMBER in owned:
            report(OK, "TWILIO_FROM_NUMBER is a number you own")
        else:
            report(BAD, "TWILIO_FROM_NUMBER not found on this account",
                   f"you own: {', '.join(owned) or '(none — buy a number)'}")
    except Exception as exc:
        report(WARN, "could not list your numbers", str(exc)[:160])


async def check_openai_realtime() -> None:
    from patient import config

    print("\nOpenAI Realtime")
    if not config.OPENAI_API_KEY:
        report(WARN, "skipped", "OPENAI_API_KEY missing")
        return
    import websockets

    url = f"wss://api.openai.com/v1/realtime?model={config.REALTIME_MODEL}"
    try:
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        ):
            report(OK, f"connected to model '{config.REALTIME_MODEL}'")
    except Exception as exc:
        report(BAD, "could not open a Realtime session", str(exc)[:200])
        report(WARN, "hint", "403/404 usually means your account has no "
                             "Realtime access, or REALTIME_MODEL is misspelled")


def check_anthropic() -> None:
    """Only runs if you've chosen Anthropic for the analysis pass."""
    from patient import config

    if config.analysis_provider() != "anthropic":
        return
    print("\nAnthropic (bug analysis only)")
    if not config.ANTHROPIC_API_KEY:
        report(WARN, "skipped", "ANTHROPIC_API_KEY missing")
        return
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        # One-token request: proves the key works and has credit, costs ~nothing.
        client.messages.create(
            model=config.ANTHROPIC_ANALYSIS_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        report(OK, f"key valid for '{config.ANTHROPIC_ANALYSIS_MODEL}'")
    except Exception as exc:
        report(BAD, "Anthropic call failed", str(exc)[:200])
        report(WARN, "hint", "a Claude Pro/Max subscription is not API credit — "
                             "add credit at console.anthropic.com")


def diagnose_tunnel_failure(host: str) -> None:
    """
    Work out *why* the tunnel is unreachable, so the user gets a sentence
    instead of a stack trace.

    The common cause is not a bug in this project: home routers and ISPs
    increasingly classify ngrok tunnels as "high risk" and block them. Over
    plain HTTP the filter is chatty — it serves its own block page — so we
    ask over HTTP and read what comes back. Over HTTPS it can't inject a
    page, so it garbles the connection instead, which is what surfaces as an
    SSL error.
    """
    import requests

    if not host:
        return

    blocked_words = ("blocked", "threat", "not allowed", "restricted",
                     "security", "content filter", "denied")
    try:
        body = requests.get(f"http://{host}/health", timeout=15).text.lower()
    except Exception:
        report(WARN, "could not reach the tunnel over plain HTTP either",
               "looks like a general network problem, not a filter")
        return

    if any(word in body for word in blocked_words) and "{" not in body[:200]:
        report(WARN, "your network is blocking ngrok",
               "a filter answered instead of your laptop — see the note below")
        print("""
      Something between this Mac and the internet (usually the router, the
      ISP, or a VPN / "Advanced Security" style feature) treats ngrok
      tunnels as risky and blocks them. Nothing is wrong with your keys or
      your code — every other check above passed.

      Fastest test: turn on your phone's hotspot, connect this Mac to it,
      and run this script again. If it goes green, the block is your home
      network.

      Real fixes, cheapest first:
        1. Turn off the security/web-filter feature in your ISP's app or
           your router's admin page, then reconnect.
        2. Run the calls over the phone hotspot instead.

      Worth knowing: Twilio reaches your tunnel from Twilio's servers, not
      from this laptop, so calls may actually work even while this check
      fails. But don't guess — clear the block so this check is honest.
""")
    else:
        report(WARN, "the tunnel edge answered, but not with our /health",
               f"got: {body[:100]!r}")


async def check_tunnel() -> None:
    """Start the real server + tunnel and prove the internet can reach it."""
    import requests

    from patient import config
    from run_call import open_tunnel, start_server

    print("\nPublic tunnel (this is what Twilio uses)")
    try:
        srv = await start_server()
    except Exception as exc:
        report(BAD, "local server failed to start", str(exc)[:160])
        return

    close_tunnel = lambda: None
    host = ""
    try:
        host, close_tunnel = await open_tunnel()
        report(OK, "tunnel open", f"https://{host}")
        resp = await asyncio.to_thread(
            requests.get, f"https://{host}/health", timeout=20
        )
        if resp.ok and resp.json().get("ok"):
            report(OK, "the internet can reach your bridge")
        else:
            report(BAD, "tunnel reachable but /health failed", resp.text[:120])
    except SystemExit:
        report(BAD, "tunnel not configured")
    except Exception as exc:
        report(BAD, "tunnel check failed", str(exc)[:160])
        diagnose_tunnel_failure(host)
    finally:
        srv.should_exit = True
        # Let uvicorn finish shutting down before we leave the event loop.
        # Without this pause its lifespan task gets cancelled mid-await and
        # prints an alarming (but harmless) traceback under the results.
        await asyncio.sleep(0.5)
        close_tunnel()


async def main() -> int:
    print("=" * 66)
    print("PGAI voice bot — setup check (no calls are placed)")
    print("=" * 66)
    check_python_and_deps()
    if failures:
        print("\nInstall the packages first, then run this again.")
        sys.exit(1)
    check_env()
    check_twilio()
    await check_openai_realtime()
    check_anthropic()
    await check_tunnel()

    print("\n" + "=" * 66)
    if failures:
        print(f"{failures} problem(s) above. Fix those before calling.")
    else:
        print("All good. Try:  python run_call.py 01_schedule_new")
    print("=" * 66)
    # Return the exit code rather than calling sys.exit() here: raising
    # SystemExit inside a running event loop tears down the still-live
    # server task and buries the results under a traceback.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
