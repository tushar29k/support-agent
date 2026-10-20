# ADR 0003 — groundedness is checked mechanically, not by vibes

## Decision

The groundedness eval uses two cheap, deterministic checks instead of
an LLM judge:

1. every NUMBER in the draft must appear in a cited doc (or the
   customer's own message);
2. a "bait" suite of questions about policies that do not exist fails
   if the draft repeats the bait claim.

## Why

Numbers are where support agents hurt people — invented refund windows,
invented fees, invented timelines. A number check is exact, free, and
never argues, unlike a judge model. The bait suite targets the failure
mode directly: a customer confidently asking about a "lifetime warranty"
is exactly how a helpful-sounding hallucination gets born. The
extractive mock scores 0% baited by construction; the suite exists so a
real LLM swapped in behind the same interface has to earn that number.

## Cost

Semantic hallucinations without numbers (wrong tone, wrong policy
summary) slip past these checks. Accepted for v1 — the checks catch the
expensive class of error, and the citation requirement bounds the rest.
