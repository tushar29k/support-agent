# ADR 0001 — hybrid retrieval, not dense-only

## Decision

Retrieve with BM25 + a dense-style scorer, fused 50/50 after min-max
normalisation. (Offline, the "dense" scorer is a hashed bag-of-words
stand-in; the seam is `DenseMock` in `kb.py` — swap in a real embedding
model there and nothing else changes.)

## Why

Support language is two languages at once. Exact terms — "30 days",
"order", "duplicate charge" — are BM25's home turf, and paraphrases —
"money back", "charged twice" — are where dense retrieval wins. On the
eval set, either scorer alone misranked the expected policy for 3 of 10
grounded questions; the fused ranker gets 10/10. Stopword filtering
mattered more than the fusion weights: without it, "what can I do"
outvoted the actual topic words in a 10-doc corpus.

## Cost

Two scorers to maintain and one fusion constant to justify. Accepted —
retrieval quality is the groundedness ceiling for everything downstream.
