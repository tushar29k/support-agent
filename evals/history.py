"""Eval history log: one JSONL record per suite run, committed to git.

A score trend is project history, so the adversarial runners append
their summary here (next to the groundedness rows evals/run_eval.py
already writes) instead of only printing it. Rows from the adversarial
suites carry a "suite" key; the older groundedness rows predate it.

    python3 -c "import sys; sys.path.insert(0, 'evals'); from history import load; print(len(load()))"
"""
import json
import os
from datetime import date

HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "eval-history.jsonl")


def record(suite, backend, passed, total, detail="", source="live"):
    # one line per suite run — date-stamped, so the dashboard can plot it
    row = {"date": date.today().isoformat(), "suite": suite,
           "backend": backend, "passed": passed, "total": total,
           "detail": detail, "source": source}
    with open(HISTORY, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def load():
    rows = []
    if not os.path.exists(HISTORY):
        return rows
    for line in open(HISTORY):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a torn write shouldn't kill the whole report
    return rows
