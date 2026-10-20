"""SupportAgent: ticket -> retrieve -> draft -> gate -> act -> trace.

Events stream out as dicts (the API serialises them as NDJSON):
  retrieved / draft / approval_required / tool_result / final

The approval gate mirrors agent-service: a destructive tool never runs
inside run(). run() pauses, stores the pending action, and only approve()
executes it. Every step lands in the per-ticket trace, which the UI and
the evals both read.
"""
import time

import tools
from brain import make_brain
from kb import Retriever

TRACES = {}   # ticket_id -> [events]
PENDING = {}  # ticket_id -> pending destructive action


class SupportAgent:
    def __init__(self, brain=None, retriever=None):
        self.brain = brain or make_brain()
        self.retriever = retriever or Retriever()

    def _emit(self, ticket_id, event):
        event = {"t": round(time.time(), 3), **event}
        TRACES.setdefault(ticket_id, []).append(event)
        return event

    def run(self, ticket_id, message):
        docs = self.retriever.search(message, k=3)
        yield self._emit(ticket_id, {
            "type": "retrieved", "ticket_id": ticket_id,
            "docs": [{"id": d["id"], "title": d["title"],
                      "score": d["score"], "bm25": d["bm25"],
                      "dense": d["dense"]} for d in docs]})
        plan = self.brain.draft(message, docs)
        yield self._emit(ticket_id, {
            "type": "draft", "reply": plan["reply"],
            "citations": plan["citations"], "brain": self.brain.name})
        for action in plan["actions"]:
            spec = tools.TOOLS[action["tool"]]
            args = {"ticket_id": ticket_id, **action["args"]}
            if spec["destructive"]:
                # gate: park it, let a human decide
                PENDING[ticket_id] = {"tool": action["tool"], "args": args}
                yield self._emit(ticket_id, {
                    "type": "approval_required", "tool": action["tool"],
                    "args": args, "desc": spec["desc"]})
                return  # run ends here; approve() resumes it
            res = tools.call(action["tool"], **args)
            yield self._emit(ticket_id, {"type": "tool_result",
                                         "tool": action["tool"], **res})
        yield self._finish(ticket_id, plan)

    def approve(self, ticket_id, approved, reason=""):
        pending = PENDING.pop(ticket_id, None)
        if pending is None:
            yield self._emit(ticket_id, {"type": "error",
                                         "error": "no pending action"})
            return
        if not approved:
            yield self._emit(ticket_id, {
                "type": "tool_result", "tool": pending["tool"],
                "ok": False, "error": f"denied by human: {reason or 'no reason given'}"})
            yield self._emit(ticket_id, {
                "type": "final", "status": "resolved_without_action",
                "note": "draft kept, destructive action denied"})
            return
        res = tools.call(pending["tool"], **pending["args"])
        yield self._emit(ticket_id, {"type": "tool_result",
                                     "tool": pending["tool"], **res})
        yield self._emit(ticket_id, {"type": "final", "status": "resolved",
                                     "note": "approved action executed"})

    def _finish(self, ticket_id, plan):
        tools.call("update_ticket", ticket_id=ticket_id, status="answered",
                   note=f"citations: {', '.join(plan['citations']) or 'none'}")
        return self._emit(ticket_id, {"type": "final", "status": "answered",
                                      "reply": plan["reply"]})
