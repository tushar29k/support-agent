# ADR 0002 — the approval gate lives in the agent, not the tools

## Decision

Tools declare `destructive: True/False` in the registry (`tools.py`).
The agent loop — never the tool, never the UI — pauses before any
destructive call, parks the pending action, and only resumes on an
explicit human decision via `/approve`.

## Why

If the gate lived in the tool, a second caller (a script, a future UI)
could skip it. If it lived in the UI, the API would be unguarded. In the
loop, there is exactly one path from "agent wants to" to "money moved",
and it goes through a human. Denials are first-class: the draft is kept,
the ticket is marked resolved-without-action, and the denial is in the
trace — an audit trail, not a silent drop.

## Cost

Runs are no longer fully streaming-to-completion; the caller must handle
the `approval_required` event and call back. That's the price of moving
money with an agent in the loop, and it's cheap.
