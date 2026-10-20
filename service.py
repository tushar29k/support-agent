"""Thin HTTP wrapper around SupportAgent.

  POST /ticket    {ticket_id, message} -> NDJSON events
  POST /approve   {ticket_id, approved, reason?} -> resume after gate
  GET  /trace/<ticket_id> -> full stored trace
  GET  /store -> tool action store (verifiable side effects)
  GET  /health, /info (honest backend badge: real model or offline mock)
"""
import json
import os as _os

from fastapi import FastAPI
from fastapi.responses import FileResponse as _FileResponse
from fastapi.responses import StreamingResponse

from agent import PENDING, TRACES, SupportAgent
from brain import make_brain
import tools

agent = SupportAgent(brain=make_brain())
app = FastAPI(title="support-agent")


def _stream(events):
    for ev in events:
        yield json.dumps(ev) + "\n"


@app.post("/ticket")
def ticket(body: dict):
    return StreamingResponse(
        _stream(agent.run(body["ticket_id"], body["message"])),
        media_type="application/x-ndjson")


@app.post("/approve")
def approve(body: dict):
    return StreamingResponse(
        _stream(agent.approve(body["ticket_id"], body["approved"],
                              reason=body.get("reason", ""))),
        media_type="application/x-ndjson")


@app.get("/trace/{ticket_id}")
def trace(ticket_id: str):
    return {"ticket_id": ticket_id,
            "trace": TRACES.get(ticket_id, []),
            "pending": PENDING.get(ticket_id)}


@app.get("/store")
def store():
    return tools.STORE


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/info")
def info():
    real = type(agent.brain).__name__ == "FreeBrain"
    return {"real_llm": real,
            "provider": agent.brain.client.label if real else None,
            "model": agent.brain.client.model if real else None,
            "backend": type(agent.brain).__name__}

# -- demo ui -------------------------------------------------------------
_UI_INDEX = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                          "ui", "index.html")


@app.get("/", include_in_schema=False)
def _demo_ui():
    return _FileResponse(_UI_INDEX)
