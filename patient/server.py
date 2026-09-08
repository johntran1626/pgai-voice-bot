"""
The WebSocket endpoint Twilio connects to. Resolves the scenario from the URL
path, hands the socket to a CallBridge, and publishes the finished transcript.
"""

import asyncio

from fastapi import FastAPI, WebSocket

from .bridge import CallBridge
from .scenarios import BY_ID
from .transcript import Transcript

app = FastAPI()

# run_call.py registers an Event here before placing a call, then waits on it.
# The bridge fills in COMPLETED and fires the Event when the call is over.
COMPLETED: dict[str, Transcript] = {}
DONE: dict[str, asyncio.Event] = {}


def expect(scenario_id: str) -> asyncio.Event:
    """Called before dialling: 'a call for this scenario is about to arrive'."""
    COMPLETED.pop(scenario_id, None)
    DONE[scenario_id] = asyncio.Event()
    return DONE[scenario_id]


@app.get("/health")
async def health():
    """Used by check_setup.py to prove the tunnel reaches this process."""
    return {"ok": True}


@app.websocket("/media/{scenario_id}")
async def media(websocket: WebSocket, scenario_id: str):
    await websocket.accept()

    scenario = BY_ID.get(scenario_id)
    if scenario is None:
        await websocket.close()
        return

    bridge = CallBridge(websocket, scenario)
    try:
        transcript = await bridge.run()
    except Exception as exc:  # never let one bad call kill the server
        bridge.transcript.note("bridge_crashed", str(exc))
        transcript = bridge.transcript

    COMPLETED[scenario_id] = transcript
    if scenario_id in DONE:
        DONE[scenario_id].set()
