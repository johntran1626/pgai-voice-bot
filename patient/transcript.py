"""Collects both sides of the conversation and writes them to calls/<id>/."""

import json
import os
import time


def mmss(seconds: float) -> str:
    """Turn 83.4 seconds into the string '1:23'."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Transcript:
    """Accumulates conversation turns in memory, then writes files at the end."""

    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.started_at = time.monotonic()
        self.turns: list[dict] = []
        # Filled in later by the bridge / call runner.
        self.call_sid: str | None = None
        self.stream_sid: str | None = None
        self.outcome: dict | None = None
        self.events: list[dict] = []  # non-speech notes, e.g. "we interrupted"

    # -- recording things that happen ---------------------------------------

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def add(self, speaker: str, text: str) -> None:
        """Record one spoken turn. `speaker` is PGAI_AGENT or PATIENT_BOT."""
        text = (text or "").strip()
        if not text:
            return
        self.turns.append(
            {"t": round(self.elapsed(), 2), "speaker": speaker, "text": text}
        )
        print(f"  [{mmss(self.elapsed())}] {speaker}: {text}")

    def note(self, kind: str, detail: str = "") -> None:
        """Record a non-speech event (barge-in, timeout, tool call, error)."""
        self.events.append(
            {"t": round(self.elapsed(), 2), "kind": kind, "detail": detail}
        )

    # -- output ---------------------------------------------------------

    def as_text(self) -> str:
        """The human-readable transcript that goes in the repo."""
        head = [
            f"Scenario : {self.scenario['id']} — {self.scenario['name']}",
            f"Call SID : {self.call_sid or 'unknown'}",
            f"Duration : {mmss(self.elapsed())}",
            "",
            "PATIENT_BOT = our simulated patient",
            "PGAI_AGENT  = the AI agent under test at +1-805-439-8008",
            "=" * 68,
            "",
        ]
        body = [f"[{mmss(t['t'])}] {t['speaker']}: {t['text']}" for t in self.turns]
        tail = []
        if self.events:
            tail = ["", "-" * 68, "EVENT LOG (non-speech)", ""]
            tail += [f"[{mmss(e['t'])}] {e['kind']} {e['detail']}".rstrip()
                     for e in self.events]
        if self.outcome:
            tail += [
                "",
                "-" * 68,
                f"Bot-reported outcome: {self.outcome.get('outcome')}",
                f"Bot-reported summary: {self.outcome.get('summary')}",
            ]
        return "\n".join(head + body + tail) + "\n"

    def write(self, out_dir: str) -> None:
        os.makedirs(out_dir, exist_ok=True)

        with open(os.path.join(out_dir, "transcript.txt"), "w") as f:
            f.write(self.as_text())

        # The JSON version keeps exact timings and is what analyze.py reads.
        with open(os.path.join(out_dir, "transcript.json"), "w") as f:
            json.dump(
                {
                    "scenario_id": self.scenario["id"],
                    "scenario_name": self.scenario["name"],
                    "watch_for": self.scenario["watch_for"],
                    "call_sid": self.call_sid,
                    "duration_seconds": round(self.elapsed(), 2),
                    "turns": self.turns,
                    "events": self.events,
                    "outcome": self.outcome,
                },
                f,
                indent=2,
            )
