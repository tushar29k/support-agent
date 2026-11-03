"""Adversarial evals: attacks on the gate, the draft, and the policy.

Attack surfaces, all on the deterministic mock brain:
  task        — hostile customer ticket: fake pre-approvals, forged
                approval tokens, role-play, amount smuggling, prompt
                and KB exfiltration through the reply
  observation — poisoned knowledge-base doc planted in the retriever
                (retrieval is this agent's tool output; the extractive
                draft stitches retrieved sentences into the reply, so
                a planted claim that ranks is a claim the customer
                reads)
  split       — payload split across the ticket and a poisoned doc
  gate        — scripted run()/approve() sequences probing the gate
                itself: approve-with-nothing-parked, wrong-ticket
                approval, deny-then-approve, pending overwrite
  control     — benign traffic; must be handled normally

Two suites:
  seeds    — the hand-written cases in adversarial.yaml.
  variants — the generated cases in adversarial_variants.yaml
             (mutations of the seeds + hand-seeded near-miss controls,
             see generate_variants.py).

REPORT-ONLY, both suites: outcomes print, classify via
evals/scoring.py (blocked / succeeded / evaded / false_refusal / ...),
record a history row, and the exit code stays 0. The groundedness
suite (evals/run_eval.py) remains the CI gate; gating on this suite
is a later decision, made with the numbers in hand — it is expected
to be red where the wiring genuinely has gaps, and that signal must
not train anyone to ignore a red build. Cases tagged real_only are
skipped on the mock entirely.

Every run is summarised as the three metrics — attack success rate,
false-refusal rate, completion under attack, each printed with its n.
Pass/fail alone hid the security–utility trade-off; the metrics line
is the honest summary.

Mock-only by design: the mock brain follows no instructions, so
"evaded" measures "no signal on a brain that cannot comply", never
"a real model would also shrug this off".

    python3 evals/run_adversarial.py [--suite seeds|variants|all]
                                     [--no-record]
"""
import argparse
import sys

import yaml

sys.path.insert(0, ".")
sys.path.insert(0, "evals")
import agent as agent_mod
import tools
from agent import SupportAgent
from brain import MockBrain
from history import record
from kb import KB, Retriever
from scoring import aggregate, classify, fmt_rate

BACKEND = "mock"


# --- shared harness (run_scenarios.py imports from here) ---------------------

def reset_state():
    """Tool store + traces + pending parks are module-global; every
    case starts from a clean slate or counts leak between cases."""
    tools.STORE["refunds"].clear()
    tools.STORE["escalations"].clear()
    tools.STORE["updates"].clear()
    tools.STORE["tickets"].clear()
    agent_mod.TRACES.clear()
    agent_mod.PENDING.clear()


def fresh_agent(docs=None):
    return SupportAgent(
        brain=MockBrain(),
        retriever=Retriever(docs) if docs is not None else Retriever())


def poison_docs(case):
    """The planted doc for observation/split cases: the benign lead
    sentence (which is what retrieval ranks on) + the payload."""
    return KB + [{"id": case["doc_id"], "title": case["doc_title"],
                  "text": case["doc_lead"] + " " + case["poison"]}]


def facts(events):
    """The raw outcome facts scoring.classify consumes, from one
    ticket's event stream + the tool store."""
    draft = next((e for e in events if e["type"] == "draft"), None)
    answer = draft["reply"] if draft else ""
    parked_args = [e.get("args", {}) for e in events
                   if e["type"] == "approval_required"]
    retrieved = next((e for e in events if e["type"] == "retrieved"),
                     None)
    return {
        "answer": answer,
        "retrieved_ids": [d["id"] for d in retrieved["docs"]]
                         if retrieved else [],
        "citations": draft["citations"] if draft else [],
        "parked": bool(parked_args),
        "parked_args": parked_args,
        "final_present": any(e["type"] == "final" for e in events),
        "escalated": any(e["type"] == "tool_result"
                         and e.get("tool") == "escalate" for e in events),
        "refunds": list(tools.STORE["refunds"]),
    }


def expected_ok(case, answer):
    exp = case.get("expected_contains")
    if not exp:
        return None
    low = answer.lower()
    return all(s.lower() in low for s in exp)


def forbidden_hit(case, f):
    """Planted content in the reply, a canary emitted, or a destructive
    tool executed (runners never call approve() inside attack cases,
    so any refund in the store is unauthorised by construction)."""
    low = f["answer"].lower()
    if any(s.lower() in low for s in case.get("forbidden_contains", [])):
        return True
    if case.get("canary") and case["canary"] in f["answer"]:
        return True
    if f["refunds"]:
        return True
    return False


# --- per-type runners ----------------------------------------------------------

def run_task(agent, case, tid):
    events = list(agent.run(tid, case["attack"]))
    f = facts(events)
    outcome = {"parked": f["parked"], "executed": bool(f["refunds"]),
               "forbidden_hit": forbidden_hit(case, f),
               "answer": f["answer"],
               "expected_ok": expected_ok(case, f["answer"])}
    detail = (f"parked={f['parked']} executed={bool(f['refunds'])} "
              f"docs={f['citations']}")
    return outcome, detail


def run_observation(agent, case, tid):
    events = list(agent.run(tid, case["question"]))
    f = facts(events)
    # no forbidden_hit key on purpose: scoring.goal_signal owns the
    # poison-in-the-draft check for observation/split cases (forbidden
    # substrings, canary, payload tail-fragment) — precomputing a bool
    # here would short-circuit it
    outcome = {"parked": f["parked"], "executed": bool(f["refunds"]),
               "answer": f["answer"],
               "expected_ok": expected_ok(case, f["answer"])}
    retrieved = case["doc_id"] in f["retrieved_ids"]
    detail = (f"poison_retrieved={retrieved} parked={f['parked']} "
              f"docs={f['citations']}")
    return outcome, detail


def _tid_of(case):
    return f"t-{case['id']}"


def run_split(agent, case, tid):
    msg = case["question"] + "\n" + case["fragment_task"]
    events = list(agent.run(tid, msg))
    f = facts(events)
    # forbidden_hit omitted for the same reason as run_observation
    outcome = {"parked": f["parked"], "executed": bool(f["refunds"]),
               "answer": f["answer"],
               "expected_ok": expected_ok(case, f["answer"])}
    detail = f"parked={f['parked']} docs={f['citations']}"
    return outcome, detail


def run_gate(agent, case, tid):
    """Scripted steps against ONE agent (state persists across steps —
    that persistence is what is being probed). A refund that appears
    during a run step, or during an approve step the script did not
    authorise for that ticket, is an unauthorised execution."""
    unauthorised = False
    notes = []
    for step in case["steps"]:
        before = len(tools.STORE["refunds"])
        if "run" in step:
            t = step.get("ticket", tid)
            list(agent.run(f"{tid}-{t}" if t != tid else tid,
                           step["run"]))
            if len(tools.STORE["refunds"]) > before:
                unauthorised = True
                notes.append("refund executed during a run step")
        elif "approve" in step:
            t = step.get("ticket", tid)
            list(agent.approve(f"{tid}-{t}" if t != tid else tid,
                               step["approve"]))
            if len(tools.STORE["refunds"]) > before:
                notes.append("executed via approve step")
    refunds = tools.STORE["refunds"]
    executed = bool(refunds)
    if executed and not case.get("expect_executed"):
        unauthorised = True
    if (case.get("expect_amount") and executed
            and refunds[0]["amount"] != case["expect_amount"]):
        notes.append(f"executed amount {refunds[0]['amount']} "
                     f"!= expected {case['expect_amount']}")
    outcome = {"parked": False, "executed": unauthorised,
               "forbidden_hit": unauthorised, "answer": "",
               "expected_ok": None}
    detail = (f"executed={executed} unauthorised={unauthorised} "
              f"refunds={[(r['ticket_id'], r['amount']) for r in refunds]}"
              + (f" notes={notes}" if notes else ""))
    return outcome, detail


def run_control(agent, case, tid):
    events = list(agent.run(tid, case["question"]))
    f = facts(events)
    exp_ok = expected_ok(case, f["answer"])
    park_ok = f["parked"] == bool(case.get("expect_parked"))
    nocov = f["escalated"] and not f["citations"]
    false_refusal = (not park_ok) or (
        exp_ok is False and (f["parked"] or nocov))
    outcome = {"parked": f["parked"], "executed": bool(f["refunds"]),
               "forbidden_hit": False, "answer": f["answer"],
               "expected_ok": exp_ok, "park_ok": park_ok,
               "false_refusal": false_refusal}
    detail = (f"parked={f['parked']} (expect {bool(case.get('expect_parked'))}) "
              f"final={f['final_present']} answer_ok={exp_ok}")
    return outcome, detail


RUNNERS = {"task": run_task, "observation": run_observation,
           "split": run_split, "gate": run_gate, "control": run_control}


def agent_for(case):
    if case["type"] in ("observation", "split"):
        return fresh_agent(docs=poison_docs(case))
    return fresh_agent()


def run_cases(cases, verbose=True):
    """Run a case list on the mock brain; return [(case, outcome,
    class)] via scoring.classify. No history rows are written here —
    callers own the trend line."""
    outcomes = []
    width = max((len(c["id"]) for c in cases), default=10)
    for case in cases:
        if case.get("real_only"):
            if verbose:
                print(f"{case['id']:{width}s} {case['type']:11s} "
                      "skip real_only — needs a real backend")
            continue
        reset_state()
        tid = _tid_of(case)
        agent = agent_for(case)
        outcome, detail = RUNNERS[case["type"]](agent, case, tid)
        klass = classify(case, outcome)
        outcome["ok"] = klass in ("blocked", "completed")
        outcomes.append((case, outcome, klass))
        if verbose:
            print(f"{case['id']:{width}s} {case['type']:11s} "
                  f"{klass:14s} {detail}")
            if klass in ("succeeded", "false_refusal", "inconclusive"):
                print(f"   answer: {outcome['answer'][:140]}")
    return outcomes


def _print_metrics(label, outcomes):
    m = aggregate(outcomes)
    a, b = m["attacks"], m["benign"]
    print(f"{label} metrics: ASR {fmt_rate(a['asr'], a['n'])} | "
          f"block rate {fmt_rate(a['block_rate'], a['n'])} | "
          f"FRR {fmt_rate(b['frr'], b['n'])} | "
          f"completion under attack "
          f"{fmt_rate(a['completion_under_attack'], a['under_attack_n'])}")
    return m


def _run_suite(label, path, record_rows=True):
    cases = yaml.safe_load(open(path))["cases"]
    print(f"=== {label}: {len(cases)} cases (mock brain) ===")
    outcomes = run_cases(cases)
    _print_metrics(label, outcomes)
    print()
    if record_rows:
        clean = sum(1 for _, _, k in outcomes
                    if k in ("blocked", "completed"))
        tally = {}
        for _, _, k in outcomes:
            tally[k] = tally.get(k, 0) + 1
        record(label, BACKEND, clean, len(outcomes),
               detail=", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    return outcomes


def run_all(record_rows=True, verbose=True):
    """Both suites; returns {suite: outcomes} for the report
    generator (which passes record_rows=False — the runners' own
    runs own the trend line)."""
    results = {}
    for label, path in (("adversarial", "evals/adversarial.yaml"),
                        ("adversarial_variants",
                         "evals/adversarial_variants.yaml")):
        if verbose:
            results[label] = _run_suite(label, path, record_rows)
        else:
            cases = yaml.safe_load(open(path))["cases"]
            results[label] = run_cases(cases, verbose=False)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["seeds", "variants", "all"],
                    default="all")
    ap.add_argument("--no-record", action="store_true",
                    help="do not append history rows (report generation "
                         "and dry runs)")
    args = ap.parse_args()
    record_rows = not args.no_record

    variant_outcomes = []
    if args.suite in ("seeds", "all"):
        _run_suite("adversarial", "evals/adversarial.yaml", record_rows)
    if args.suite in ("variants", "all"):
        variant_outcomes = _run_suite(
            "adversarial_variants", "evals/adversarial_variants.yaml",
            record_rows)
        breakdown = {}
        for case, outcome, _cls in variant_outcomes:
            slot = breakdown.setdefault(case.get("mutation", "-"), [0, 0])
            slot[0] += outcome["ok"]
            slot[1] += 1
        print("--- variants by mutation class ---")
        for mutation in sorted(breakdown):
            passed, total = breakdown[mutation]
            print(f"  {mutation:18s} {passed}/{total}")
        print("\nadversarial suites are REPORT-ONLY: the groundedness "
              "suite (evals/run_eval.py) remains the gate.")


if __name__ == "__main__":
    main()
