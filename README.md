# support-agent

## Release notes — v1.0 (2026-10-20)

Measured numbers, offline, mock brain — every figure below comes from
`python3 evals/run_eval.py`, re-run today.

- **Grounded: 10/10** — every grounded-suite ticket cites the right policy
  doc and the draft contains zero numbers not present in a cited doc.
  (Early run: 7/10 — stopword filtering in the retriever fixed the other
  three. The fix and the reasoning are in `docs/adr/0001`.)
- **Bait: 8/8 clean, 0% hallucination rate** — on planted questions about
  policies that do not exist (lifetime warranty, price matching, gift
  cards, crypto payments), the agent never repeated the bait claim. The
  extractive draft can't invent; no-KB-coverage tickets escalate to a
  human instead of improvising.
- **Approval gate: 100% of destructive calls gated** — `issue_refund`
  (moves money) never executes without a human yes/no via `/approve`;
  denials resolve the ticket as answered-without-action and stay in the
  trace.

Trends and history: `evals/eval-history.jsonl` (appended on every run).
Docs: `docs/adr/` (why hybrid retrieval, why the gate lives in the
agent, why groundedness is checked mechanically).

A support-ticket agent you can run, break, and approve/deny yourself —
no LLM API key needed.

## Live demo

**[https://tushar29k-support-agent.onrender.com](https://tushar29k-support-agent.onrender.com)** — Pick a sample ticket (or write your own), watch hybrid retrieval, the grounded draft with citations, the approval gate, and the tool actions stream in. Deny a refund and watch the trace record it.
> Hosted on Render's free tier — the first visit after a while can take ~30s while the instance wakes up.

## The idea

Support agents are the most deployed — and most dangerous — agent
pattern: they act on customers' money. This project is that loop built
the way you'd build it in production, with the guardrails up front
instead of as an afterthought:

1. **Retrieve** — hybrid BM25 + dense-style search over a seeded policy
   KB (`kb.py`). Both scores are returned per doc, so the UI can show
   *why* a doc won.
2. **Draft, grounded** — the reply is assembled only from retrieved
   policy text, with citations (`brain.py`). No matching doc → the
   agent says so and escalates. It never improvises policy.
3. **Gate** — destructive tools (`issue_refund`) pause the run and wait
   for a human. Same approval-gate design as agent-service
   (`docs/adr/0002`).
4. **Act + trace** — approved actions run through MCP-style tools and
   land in an inspectable store (`GET /store`). Every step lands in the
   per-ticket trace.

The brain is a deterministic mock by default (extractive drafts: a
correct mock *cannot* hallucinate). Set `LLM_API_KEY` and the same
interface is driven by a real model — the mock keeps deciding *actions*
from policy, the model only words the reply, and the eval suite still
runs the same checks.

## Architecture

```
customer message
      │
      ▼
 ┌─────────────┐
 │  Retriever   │  BM25 (exact terms) + DenseMock (paraphrases),
 │  (kb.py)     │  fused 50/50 after min-max normalisation
 └─────┬───────┘
       │ top-3 docs with scores
       ▼
 ┌─────────────┐
 │  Brain       │  MockBrain (extractive, can't hallucinate) or
 │  (brain.py)  │  FreeBrain (real LLM words the reply; mock still
 └─────┬───────┘  decides actions from policy)
       │ draft + citations + proposed actions
       ▼
 ┌─────────────┐      destructive?      ┌──────────────────┐
 │  SupportAgent│ ─────────────────────▶│  HUMAN (gate)     │
 │  (agent.py)  │  parks pending action │  approve / deny   │
 └─────┬───────┘ ◀───────────────────── └──────────────────┘
       │ safe tools run directly; approved tools execute
       ▼
 ┌─────────────┐
 │  tools.py    │  issue_refund / escalate / update_ticket
 └─────────────┘  → STORE (in-memory, readable via /store)
```

`service.py` is a thin FastAPI wrapper: `POST /ticket` streams NDJSON
events, `POST /approve` resumes after the gate, `GET /trace/<id>` shows
the full trace, `GET /info` honestly reports real-LLM vs mock.

## API

```
POST /ticket   {"ticket_id": "t1", "message": "..."}   -> NDJSON events
POST /approve  {"ticket_id": "t1", "approved": true}    -> resume
GET  /trace/<ticket_id>   full stored trace + pending action
GET  /store               refunds / escalations / updates issued
GET  /info                real model or offline mock (for the UI badge)
```

Try it:

```
uvicorn service:app --reload
curl -s -N -X POST localhost:8000/ticket \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id":"t1","message":"I was charged twice, please refund me"}'
```

## Evals

```
python3 evals/run_eval.py          # mock brain — this is the CI gate
python3 evals/run_eval.py --free   # real model, if LLM_API_KEY is set
```

Two suites (details in `docs/adr/0003`):
- **grounded** (10 cases): cites the right doc, no invented numbers.
- **bait** (8 cases): questions about nonexistent policies — fails if the
  reply repeats the bait.
