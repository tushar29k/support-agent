"""MCP-style tools: the actions the agent can take on a ticket.

Each tool is a plain function behind a registry with a name, a schema-ish
description, and a `destructive` flag. Destructive tools never run without
a human approval — the gate lives in agent.py, the flag lives here, so a
new tool can't silently become destructive.

The store is in-memory: refunds, escalations and ticket updates land in
dicts the API can read back, which is what makes the demo verifiable.
"""
import itertools

STORE = {"refunds": [], "escalations": [], "updates": [], "tickets": {}}
_seq = itertools.count(1)


def issue_refund(ticket_id, amount_dollars, reason):
    """Issue a refund to the customer. Moves money — destructive."""
    rid = f"rf-{next(_seq):04d}"
    STORE["refunds"].append({"id": rid, "ticket_id": ticket_id,
                              "amount": amount_dollars, "reason": reason})
    return {"refund_id": rid, "status": "issued", "amount": amount_dollars}


def escalate(ticket_id, reason):
    """Escalate the ticket to a human specialist."""
    eid = f"esc-{next(_seq):04d}"
    STORE["escalations"].append({"id": eid, "ticket_id": ticket_id,
                                  "reason": reason})
    return {"escalation_id": eid, "status": "escalated"}


def update_ticket(ticket_id, status, note):
    """Set ticket status and append an internal note. Not destructive."""
    STORE["updates"].append({"ticket_id": ticket_id, "status": status,
                              "note": note})
    t = STORE["tickets"].setdefault(ticket_id, {})
    t["status"] = status
    return {"ticket_id": ticket_id, "status": status}


TOOLS = {
    "issue_refund": {"fn": issue_refund, "destructive": True,
                     "desc": "issue a refund (moves money)"},
    "escalate": {"fn": escalate, "destructive": False,
                 "desc": "hand the ticket to a human specialist"},
    "update_ticket": {"fn": update_ticket, "destructive": False,
                      "desc": "set status / add an internal note"},
}


def call(name, **kwargs):
    # a tool can never crash the agent: errors come back as data
    try:
        return {"ok": True, "result": TOOLS[name]["fn"](**kwargs)}
    except Exception as e:  # noqa: BLE001 - deliberate guardrail
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
