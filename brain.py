"""The brain: turns a ticket + retrieved docs into a grounded draft.

Deterministic mock by default — same seam as the other services:
make_brain() returns a FreeBrain when LLM_API_KEY is set, else the mock.
The mock is extractive on purpose: it assembles the draft from sentences
of the retrieved docs, so a correct mock can never hallucinate. A real
LLM gets the same retrieved context plus a "cite or stay silent"
instruction — and the groundedness evals run against BOTH paths.

Action policy (what the agent wants to do, before the approval gate):
- refund mentioned + amount extractable -> propose issue_refund
- manager / legal / above 200 dollars   -> propose escalate
- always                                -> update_ticket with the outcome
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_AMOUNT = re.compile(r"\$?\b(\d{1,5})(?:\.\d{2})?\s*(?:dollars|usd|\$)", re.I)
_REFUND = re.compile(r"refund|money back|charged twice|duplicate charge", re.I)
_ESCALATE = re.compile(r"manager|lawyer|legal|sue|supervisor", re.I)


def _sentences(text):
    return [s.strip() for s in text.split(". ") if s.strip()]


class MockBrain:
    name = "mock"

    def draft(self, ticket_text, docs):
        """Return {reply, citations, actions} grounded in `docs` only."""
        if not docs:
            # nothing retrieved -> say so and escalate; never improvise
            return {"reply": "I could not find a policy that covers this. "
                             "I am escalating your ticket to a specialist "
                             "who will reply within 4 business hours.",
                    "citations": [],
                    "actions": [{"tool": "escalate",
                                 "args": {"reason": "no kb coverage"}}]}
        # extractive draft: top sentences from the top docs, stitched
        picked = []
        for d in docs[:2]:
            picked.extend(_sentences(d["text"])[:2])
        reply = ("Thanks for reaching out. Here is what our policy says: "
                 + ". ".join(picked) + ".")
        actions = []
        m = _AMOUNT.search(ticket_text)
        amount = int(m.group(1)) if m else None
        if _REFUND.search(ticket_text):
            # refund amount: the customer's figure, else the kb ceiling
            amt = amount if amount is not None else 50
            actions.append({"tool": "issue_refund",
                            "args": {"amount_dollars": amt,
                                     "reason": "customer refund request"}})
            if amt > 200:
                actions.append({"tool": "escalate",
                                "args": {"reason": "refund above 200 dollars"}})
        if _ESCALATE.search(ticket_text):
            actions.append({"tool": "escalate",
                            "args": {"reason": "customer requested escalation"}})
        return {"reply": reply,
                "citations": [d["id"] for d in docs[:2]],
                "actions": actions}


class FreeBrain:
    """Real model behind the same interface — used when a key exists."""
    name = "free-llm"

    def __init__(self, client):
        self.client = client

    def draft(self, ticket_text, docs):
        # the mock decides actions (policy), the model only words the reply
        base = MockBrain().draft(ticket_text, docs)
        if not docs:
            return base
        ctx = "\n".join(f"[{d['id']}] {d['text']}" for d in docs[:2])
        prompt = ("You are a support agent. Answer ONLY from the policy text "
                  "below. Do not add facts, numbers or promises that are not "
                  "in it.\n\nPOLICY:\n" + ctx + "\n\nCUSTOMER: " + ticket_text
                  + "\n\nREPLY:")
        try:
            text = self.client.generate(prompt, max_tokens=220)
            if text and text.strip():
                base["reply"] = text.strip()
        except Exception:  # noqa: BLE001 - fall back to the grounded mock
            pass
        return base


def make_brain():
    try:
        from llm_client import FreeLLMClient
        client = FreeLLMClient.from_env()
        if client is not None:
            return FreeBrain(client)
    except Exception:  # noqa: BLE001 - no key / no client -> mock
        pass
    return MockBrain()
