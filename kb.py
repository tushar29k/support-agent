"""Seeded knowledge base + hybrid retrieval (BM25 + dense-style scorer).

The KB is the only source of truth the agent may cite. Every fact the
draft resolution states must come from one of these docs — that is what
the groundedness evals check.

Retrieval is hybrid on purpose: BM25 catches exact terms ("order id",
"30 days") and the dense-style scorer catches paraphrases ("money back"
vs "refund"). Neither alone is enough — see docs/adr/0001.
"""
import hashlib
import math
import re

# -- the seeded kb ------------------------------------------------------------
# facts carry concrete numbers on purpose: the groundedness eval verifies
# that every number in a draft appears in a cited doc.

KB = [
    {"id": "refund-policy", "title": "Refund policy",
     "text": "Customers can request a refund within 30 days of delivery. "
             "Refunds are issued to the original payment method within "
             "5 business days of approval. Digital downloads are not "
             "refundable once opened. Refunds above 200 dollars require "
             "supervisor approval."},
    {"id": "shipping-times", "title": "Shipping times",
     "text": "Standard shipping takes 3 to 5 business days. Express "
             "shipping takes 1 to 2 business days and costs 12 dollars. "
             "Orders placed before 2 pm ship the same day. We ship to the "
             "US, Canada, and the EU."},
    {"id": "damaged-items", "title": "Damaged or defective items",
     "text": "If an item arrives damaged, report it within 14 days with a "
             "photo. We send a free replacement or a full refund, and the "
             "damaged item does not need to be returned. A prepaid return "
             "label is provided for defective electronics."},
    {"id": "warranty", "title": "Warranty coverage",
     "text": "Electronics carry a 12 month warranty covering manufacturing "
             "defects. Accidental damage is not covered. Warranty claims "
             "need the order number and a short description of the fault."},
    {"id": "account-lockout", "title": "Account lockout and password reset",
     "text": "After 5 failed sign-in attempts the account locks for "
             "30 minutes. A password reset link is valid for 1 hour. "
             "Support can unlock an account immediately after verifying "
             "identity with the account email and last order number."},
    {"id": "billing-charges", "title": "Billing and duplicate charges",
     "text": "A duplicate charge is refunded automatically within "
             "3 business days once detected. Pending authorisation holds "
             "drop off within 7 days and are not charges. Invoices are "
             "available under account settings for 24 months."},
    {"id": "order-tracking", "title": "Order tracking and delivery issues",
     "text": "Tracking appears within 24 hours of dispatch. If tracking "
             "shows delivered but nothing arrived, wait 24 hours, then we "
             "reship or refund. Address changes are possible until the "
             "order is marked as shipped."},
    {"id": "returns-process", "title": "Returns process",
     "text": "Start a return from the orders page within 60 days of "
             "delivery. Items must be unused and in original packaging. "
             "Return shipping is free for orders over 50 dollars, "
             "otherwise a 6 dollar label fee applies."},
    {"id": "subscription-cancel", "title": "Subscription cancellation",
     "text": "Subscriptions can be cancelled anytime from account "
             "settings and stay active until the end of the billing "
             "period. We do not refund partial months. A cancelled "
             "subscription keeps its data for 90 days."},
    {"id": "escalation-policy", "title": "Escalation policy",
     "text": "Escalate to a human specialist when the customer asks for a "
             "manager, mentions legal action, or when the requested "
             "refund exceeds 200 dollars. Escalations are answered within "
             "4 business hours."},
]

_WORD = re.compile(r"[a-z0-9]+")

# retrieval ignores these: without a stoplist, "what can I do" outvotes
# the actual topic words in a 10-doc corpus
_STOP = {"a", "an", "the", "is", "it", "my", "i", "me", "can", "do",
         "does", "what", "how", "to", "for", "of", "in", "on", "and",
         "or", "was", "were", "am", "are", "be", "been", "have", "has",
         "had", "you", "your", "we", "our", "they", "them", "this",
         "that", "with", "but", "not", "no", "yes", "if", "so", "at",
         "by", "as", "from", "please"}


def tokens(text):
    out = []
    for t in _WORD.findall(text.lower()):
        if t in _STOP:
            continue
        # crude suffix stem: enough for charge/charged, ship/shipping
        for suf in ("ing", "ed", "es", "s"):
            if len(t) > len(suf) + 2 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


# -- bm25 ---------------------------------------------------------------------

class BM25:
    def __init__(self, docs):
        self.docs = docs
        self.doc_tokens = [tokens(d["title"] + " " + d["text"]) for d in docs]
        self.avg_len = sum(len(t) for t in self.doc_tokens) / max(len(docs), 1)
        self.df = {}
        for toks in self.doc_tokens:
            for t in set(toks):
                self.df[t] = self.df.get(t, 0) + 1
        self.n = len(docs)

    def score(self, query):
        q = tokens(query)
        out = []
        for i, toks in enumerate(self.doc_tokens):
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (self.n - self.df.get(t, 0) + 0.5)
                               / (self.df.get(t, 0) + 0.5))
                f = tf[t]
                s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(toks) / self.avg_len))
            out.append(s)
        return out


# -- dense-style mock scorer ---------------------------------------------------
# hashed bag-of-words vectors: deterministic, dependency-free stand-in for
# an embedding model. synonyms expand the query the way a real dense model
# "knows" paraphrases — crude, but it ranks paraphrases above BM25 does.

_SYNONYMS = {
    "money": ["refund"], "back": ["refund"], "broken": ["damaged", "defective"],
    "arrived": ["delivery", "delivered"], "late": ["shipping", "delivery"],
    "charged": ["billing", "charge"], "twice": ["duplicate"],
    "login": ["sign-in", "account"], "locked": ["lockout", "account"],
    "cancel": ["cancellation", "subscription"], "warranty": ["coverage"],
}

_DIM = 512


def _vec(text):
    v = [0.0] * _DIM
    for t in tokens(text):
        h = int(hashlib.md5(t.encode()).hexdigest(), 16)
        v[h % _DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class DenseMock:
    def __init__(self, docs):
        self.vecs = [_vec(d["title"] + " " + d["text"]) for d in docs]

    def score(self, query):
        expanded = query
        for t in tokens(query):
            expanded += " " + " ".join(_SYNONYMS.get(t, []))
        qv = _vec(expanded)
        return [sum(a * b for a, b in zip(qv, dv)) for dv in self.vecs]


# -- hybrid -------------------------------------------------------------------

class Retriever:
    def __init__(self, docs=KB):
        self.docs = docs
        self.bm25 = BM25(docs)
        self.dense = DenseMock(docs)

    def search(self, query, k=3):
        b = self.bm25.score(query)
        d = self.dense.score(query)
        # min-max normalise then average — simple fusion that behaves
        # (rrf needs rank lists; scores are easier to explain in the ui)
        def norm(xs):
            lo, hi = min(xs), max(xs)
            return [(x - lo) / (hi - lo) if hi > lo else 0.0 for x in xs]
        bn, dn = norm(b), norm(d)
        fused = sorted(range(len(self.docs)),
                       key=lambda i: -(0.5 * bn[i] + 0.5 * dn[i]))
        return [{"id": self.docs[i]["id"], "title": self.docs[i]["title"],
                 "text": self.docs[i]["text"],
                 "score": round(0.5 * bn[i] + 0.5 * dn[i], 3),
                 "bm25": round(bn[i], 3), "dense": round(dn[i], 3)}
                for i in fused[:k] if (bn[i] + dn[i]) > 0]
