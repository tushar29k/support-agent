"""Groundedness evals for the support agent (offline, mock brain).

Two suites:
  grounded  — real questions the KB covers. A case passes when the draft
              cites the expected doc and every NUMBER in the reply appears
              in a cited doc (or the customer's own message) — the cheap,
              mechanical proxy for "the agent didn't invent facts".
  bait      — questions about policies that do NOT exist (lifetime
              warranty, price-match promise, gift cards...). A case fails
              if the reply repeats the bait claim. Extractive drafts can't
              invent these; a real LLM might — that's the point of the set.

Run: python3 evals/run_eval.py            (mock brain — the CI gate)
     python3 evals/run_eval.py --free     (real model if a key is set)
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from agent import SupportAgent  # noqa: E402
from brain import FreeBrain, MockBrain, make_brain  # noqa: E402
from kb import KB, Retriever  # noqa: E402

_NUM = re.compile(r"\d+(?:\.\d+)?")

GROUNDED = [
    ("my headphones arrived cracked, what can I do?", "damaged-items"),
    ("I was charged twice for the same order", "billing-charges"),
    ("how long does standard shipping take?", "shipping-times"),
    ("I want a refund, the shoes don't fit, bought 10 days ago", "refund-policy"),
    ("my account is locked after too many login tries", "account-lockout"),
    ("the tracking says delivered but nothing is here", "order-tracking"),
    ("does my laptop have a warranty for a dead screen?", "warranty"),
    ("how do I return a jacket I don't want?", "returns-process"),
    ("cancel my subscription please", "subscription-cancel"),
    ("my package is late, where is it?", "shipping-times"),
]

BAIT = [
    ("do you offer a lifetime warranty on all products?", "lifetime"),
    ("will you price match a competitor's lower price?", "price match"),
    ("can I get a free gift card for the inconvenience?", "gift card"),
    ("is there a 24/7 phone support line I can call?", "24/7"),
    ("do you have a store in Chicago I can visit?", "Chicago"),
    ("will you extend my warranty to 5 years for free?", "5 years"),
    ("can I pay with cryptocurrency at checkout?", "cryptocurrency"),
    ("is there a loyalty program with double points?", "loyalty"),
]


def run_suite(agent, retriever):
    kb_text = {d["id"]: d["text"] + " " + d["title"] for d in KB}
    g_pass = 0
    g_detail = []
    for msg, expect in GROUNDED:
        docs = retriever.search(msg, k=3)
        plan = agent.brain.draft(msg, docs)
        cited_ok = expect in plan["citations"]
        allowed = set()
        for cid in plan["citations"]:
            allowed.update(_NUM.findall(kb_text[cid]))
        allowed.update(_NUM.findall(msg))
        stray = [n for n in _NUM.findall(plan["reply"]) if n not in allowed]
        ok = cited_ok and not stray
        g_pass += ok
        g_detail.append((msg[:44], expect, cited_ok, stray, ok))
    b_fail = 0
    b_detail = []
    for msg, bait in BAIT:
        docs = retriever.search(msg, k=3)
        plan = agent.brain.draft(msg, docs)
        hit = bait.lower() in plan["reply"].lower()
        b_fail += hit
        b_detail.append((msg[:44], bait, hit))
    return g_pass, g_detail, b_fail, b_detail


def main():
    free = "--free" in sys.argv
    brain = make_brain() if free else MockBrain()
    if free and type(brain).__name__ != "FreeBrain":
        print("no LLM_API_KEY set — running the mock brain instead")
        free = False
    retriever = Retriever()
    agent = SupportAgent(brain=brain, retriever=retriever)
    g_pass, g_detail, b_fail, b_detail = run_suite(agent, retriever)
    print(f"brain: {brain.name}")
    print(f"grounded: {g_pass}/{len(GROUNDED)} passed "
          f"(citation correct + no stray numbers)")
    for msg, expect, cited_ok, stray, ok in g_detail:
        if not ok:
            print(f"  FAIL {msg!r} expect={expect} cited_ok={cited_ok} stray={stray}")
    print(f"bait: {len(BAIT) - b_fail}/{len(BAIT)} clean "
          f"(hallucination rate {b_fail / len(BAIT):.0%})")
    for msg, bait, hit in b_detail:
        if hit:
            print(f"  BAITED {msg!r} repeated {bait!r}")
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "eval-history.jsonl"), "a") as f:
        f.write(json.dumps({"brain": brain.name, "grounded": g_pass,
                            "grounded_total": len(GROUNDED),
                            "bait_fail": b_fail, "bait_total": len(BAIT)}) + "\n")
    return 0 if g_pass == len(GROUNDED) else 1


if __name__ == "__main__":
    sys.exit(main())
