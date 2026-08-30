# Evidence retrieval layer

This document covers the retrieval layer only (`backend/app/retrieval/`).
It consumes candidate incidents from the detection engine
(`docs/detection.md`) and the transaction dataset (`docs/data_layer.md`),
and gives a future AI investigation agent a single function to call for
evidence: `retrieve_evidence(incident_id, query)`. No reasoning/diagnosis
happens here — this layer only finds and returns evidence with source
attribution.

## Files

| File | Purpose |
|---|---|
| `app/retrieval/structured.py` | Structured evidence — computed directly from `transactions.csv` via pandas. No LLM. |
| `app/retrieval/embeddings.py` | Text embedding abstraction: optional Mistral API call, deterministic local fallback, IDF weighting helper |
| `app/retrieval/vector_store.py` | Lightweight local vector store (JSON-backed cache) over the unstructured corpus |
| `app/retrieval/corpus/unstructured_evidence.json` | 24 synthetic documents: incident reports, merchant notes, recovery outcomes, provider observations |
| `app/retrieval/bundle.py` | The public entrypoint: `retrieve_evidence(incident_id, query=None, top_k_unstructured=5)` |
| `backend/tests/test_structured.py` | Correctness tests for structured evidence, against hand-checkable fixtures |
| `backend/tests/test_retrieval_vector.py` | Tests for embeddings + vector store ranking |
| `backend/tests/test_bundle.py` | Integration tests for the full `retrieve_evidence` interface |

## Running it

```bash
cd backend
source .venv/bin/activate
python -m app.data.generate      # if not already generated
python -m app.detection.run      # if not already generated
python -m pytest tests/ -v
```

```python
from app.retrieval.bundle import retrieve_evidence

evidence = retrieve_evidence("cand_00005")                     # implicit query from the incident itself
evidence = retrieve_evidence("cand_00005", query="recovery outcomes")  # explicit query
```

## Architecture

```
                    ┌─────────────────────────┐
incident_id, query→ │   retrieve_evidence()    │ →  {structured_evidence, unstructured_evidence}
                    │      (bundle.py)         │
                    └────────────┬─────────────┘
                                 │
                 ┌───────────────┴────────────────┐
                 ▼                                 ▼
     ┌───────────────────────┐         ┌───────────────────────────┐
     │   structured.py        │         │    vector_store.py         │
     │  pandas over            │         │  cosine search over        │
     │  transactions.csv,       │         │  corpus/unstructured_      │
     │  scoped to the incident's │         │  evidence.json, using      │
     │  affected_segment +       │         │  embeddings.py (Mistral    │
     │  window                    │         │  or local fallback)        │
     └───────────────────────┘         └───────────────────────────┘
```

`retrieve_evidence` does three things: (1) look up the candidate incident
by id, (2) compute structured evidence fresh from the transaction data
for that incident's exact segment + time window, (3) run a vector search
over the unstructured corpus using either the caller's query or one
derived from the incident itself, and combine both into one response.

### Structured evidence — deterministic, no LLM

Given an incident's `affected_segment` (e.g. `{"payment_method": "UPI",
"institution": "HDFC Bank"}`) and `window_start`/`window_end`,
`structured.py` recomputes — directly from `transactions.csv`, not from
any cached/trusted-blindly value — eight evidence items:

1. **`transaction_statistics`** — window/baseline counts and failure rates, z-score
2. **`revenue_impact`** — revenue affected (failed transactions' amounts), share of window revenue at risk
3. **`affected_transaction_ids`** — the exact failed transaction IDs (capped, with a `truncated` flag)
4. **`payment_method_breakdown`** — counts/failure rate by method *(skipped if that's already the segment)*
5. **`institution_breakdown`** — same, by bank *(skipped if already the segment)*
6. **`geography_breakdown`** — same, by city *(skipped if already the segment)*
7. **`failure_reason_breakdown`** — counts of each failure reason within the window
8. **`historical_daily_trend`** — the segment's daily failure rate for the 5 days preceding the window, so the agent can see whether this was already trending

Every item's `data` field is a plain computed number/list/dict — never
generated text — and every item's `source` field states exactly which
rows of `transactions.csv`, filtered how, produced it (e.g.
`"transactions.csv, filtered to payment_method=UPI, institution=HDFC
Bank, window [2026-08-12T09:00:00+00:00 .. 2026-08-12T20:00:00+00:00)"`).
This is what "structured financial values must come directly from the
database/calculation layer" means in practice here: there's no path from
an LLM into any of these numbers.

### Unstructured evidence — where Mistral is used

The unstructured corpus (`corpus/unstructured_evidence.json`) holds 24
synthetic documents across four types matching the brief: incident
reports, merchant operational notes, recovery outcomes, and
payment-provider observations. Several are written to closely match the
project's actual injected incidents (HDFC/UPI, WALLET, the latency spike,
Chennai); several are unrelated distractors (a KYC backlog note, historic
incidents from other months/banks, a routine fraud-rule tuning note) so
retrieval quality is actually being tested, not just returning "the only
relevant doc in a tiny corpus."

**Embedding backend (`embeddings.py`):**
- If `MISTRAL_API_KEY` is set in `backend/.env`, `embed_text()` calls
  Mistral's `/v1/embeddings` endpoint (`mistral-embed`) over HTTPS. This
  is the "genuinely unstructured, semantic" use case the brief describes
  — real semantic embeddings for free-text evidence, not for anything
  financial.
- **In this project's actual dev/test environment, that path isn't
  exercised** — the sandboxed network here doesn't have
  `api.mistral.ai` allowlisted, so any Mistral call would fail. The code
  handles this gracefully: `_mistral_embedding()` catches any failure
  (missing key, no network, bad response) and returns `None`, and
  `embed_text()` falls straight through to the local backend. Nothing
  crashes; the system just runs in local mode.
- **Local fallback (the default, and what all tests run against):** a
  dependency-free hashing bag-of-words vectorizer (`_local_embedding`),
  IDF-weighted using a small corpus-wide term-frequency table
  (`compute_idf`) so common words ("payment", "failure") don't drown out
  distinctive ones ("HDFC", "Chennai", "wallet"). This is exactly the
  "if Mistral introduces unnecessary complexity, use SQL/Python" fallback
  the brief asks for — and it's good enough for a same-topic, ~25-document
  corpus at hackathon scale.

Whichever backend built the corpus is recorded in the on-disk cache
(`store/embeddings_cache.json`) and reused for query embedding, so corpus
and query vectors are always comparable even if `MISTRAL_API_KEY` gets
added or removed between runs (the cache is invalidated and rebuilt with
the newly-active backend on the next call).

**Vector store (`vector_store.py`):** brute-force cosine similarity over
the corpus's cached vectors — no external vector database. At ~25
documents this is both correct and effectively instant; introducing a
real vector DB here would be the "unnecessary complexity" the brief warns
against.

**Query construction:** if the caller doesn't supply a `query`,
`bundle._implicit_query()` builds one from the incident's own dimension,
segment values, and observed failure reasons (e.g. `"payment method
institution UPI HDFC Bank failure rate degradation BANK_TIMEOUT
NETWORK_ERROR"`) — not from the incident's full `observation` sentence,
which embeds ISO timestamps (`"...T18:00:00+00:00..."`) that would flood
a lexical query with repeated filler tokens (`00`, `2026`, `08`...) and
drown out the words that actually distinguish one incident from another.
For the unsegmented "all" dimension (broad, non-bank/method-specific
incidents), the query is also augmented with `"latency processing
delay"` when the incident's own supporting statistics show meaningfully
elevated latency in the window — a real observed number already computed
by the detection engine, not an inferred cause.

### Source attribution

Every evidence item — structured or unstructured — carries a `source`
field. For structured evidence this is the exact filter description
applied to `transactions.csv`; for unstructured evidence it's the
originating document's file path (e.g.
`"internal_ops_notes/merchant_support_log.csv"`) plus its own `doc_id`
folded into the evidence item's id (e.g.
`"cand_00005_ev_doc_0001"`). A future agent citing either can point back
to precisely where the number or passage came from.

## Retrieval interface schema

```python
retrieve_evidence(incident_id: str, query: str | None = None, top_k_unstructured: int = 5) -> dict
```

Returns:

```python
{
  "incident_id": "cand_00005",
  "retrieved_at": "2026-08-29T15:48:04+00:00",
  "query_used": "payment method institution UPI HDFC Bank failure rate degradation BANK_TIMEOUT NETWORK_ERROR",
  "structured_evidence": [ {...}, ... ],   # 6-8 items, see above
  "unstructured_evidence": [ {...}, ... ]  # up to top_k_unstructured items
}
```

Each evidence item (both kinds) has this shape:

```python
{
  "evidence_id": str,        # e.g. "cand_00005_ev_stats" or "cand_00005_ev_doc_0001"
  "evidence_type": str,      # e.g. "transaction_statistics", "incident_report"
  "source": str,             # exact provenance — see above
  "data": dict | None,       # structured evidence: computed numbers. None for unstructured.
  "text": str | None,        # unstructured evidence: the document passage. None for structured.
  "relevance_score": float,  # structured: fixed weights (1.0 for core stats/revenue,
                              #   0.85-0.95 for breakdowns, 0.7 for trend);
                              # unstructured: cosine similarity score
  "timestamp": str | None,   # window end (structured) or the document's own date (unstructured)
}
```

## Example retrieved evidence

Run against the project's actual generated dataset, for the UPI+HDFC Bank
candidate incident (`cand_00005`):

```json
{
  "incident_id": "cand_00005",
  "query_used": "payment method institution UPI HDFC Bank failure rate degradation BANK_TIMEOUT NETWORK_ERROR",
  "structured_evidence": [
    {
      "evidence_id": "cand_00005_ev_stats",
      "evidence_type": "transaction_statistics",
      "source": "transactions.csv, filtered to payment_method=UPI, institution=HDFC Bank, window [2026-08-12T09:00:00+00:00 .. 2026-08-12T20:00:00+00:00)",
      "data": {
        "window_transaction_count": 37,
        "window_failed_count": 14,
        "window_failure_rate": 0.3784,
        "baseline_transaction_count": 952,
        "baseline_failed_count": 50,
        "baseline_failure_rate": 0.0525,
        "z_score": 7.905
      },
      "relevance_score": 1.0
    },
    {
      "evidence_id": "cand_00005_ev_revenue",
      "evidence_type": "revenue_impact",
      "data": {
        "revenue_affected": 7402.39,
        "total_window_revenue": 21152.26,
        "revenue_at_risk_share": 0.35,
        "currency": "INR"
      },
      "relevance_score": 1.0
    },
    {
      "evidence_id": "cand_00005_ev_txn_ids",
      "evidence_type": "affected_transaction_ids",
      "data": {
        "total_affected_count": 14,
        "transaction_ids": ["txn_001523", "txn_001517", "txn_001510", "..."],
        "truncated": false
      },
      "relevance_score": 0.9
    }
  ],
  "unstructured_evidence": [
    {
      "evidence_id": "cand_00005_ev_doc_0001",
      "evidence_type": "incident_report",
      "source": "incident_archive/2026-08-12-hdfc-upi.md",
      "text": "Between 13:00 and 18:00 UTC on Aug 12, UPI transactions routed through HDFC Bank showed a sharp rise in failed authorizations, predominantly timing out at the bank's end. Transactions through other issuing banks over the same window were unaffected...",
      "relevance_score": 0.238,
      "timestamp": "2026-08-12T18:30:00+00:00"
    },
    {
      "evidence_id": "cand_00005_ev_doc_0002",
      "evidence_type": "merchant_note",
      "source": "internal_ops_notes/merchant_support_log.csv",
      "text": "Support ticket volume for 'payment failed' complaints roughly tripled in the early afternoon of Aug 12. Nearly all tickets we sampled mentioned UPI as the payment method...",
      "relevance_score": 0.183
    }
  ]
}
```

Sweeping all 7 detected candidates from the current dataset, the top
unstructured result for each is topically on-point: `WALLET` → "Elevated
wallet payment failures across all issuing banks"; `UPI+HDFC Bank` →
"UPI failures concentrated on HDFC Bank routing"; `Chennai` → "Failure
concentration in Chennai across multiple banks/methods"; the unsegmented
`all` (latency spike) candidate → "Broad processing latency increase, all
payment methods". Weaker single-dimension queries (e.g. `institution=HDFC
Bank` alone, without the UPI qualifier) sometimes rank a related-but-not-
best document first — a known, documented limitation of a lexical
hashing approach rather than true semantic search (see "Limitations"
below); the correct document is still reliably present in the top 5.

## Tests

49 tests total, all passing, split across:

- **`test_structured.py`** (13 tests) — builds a small, fully-known
  8-row transaction fixture and checks every computed number (counts,
  rates, revenue sums, transaction ID lists, breakdowns, truncation
  behavior, unknown-incident-id error handling) against hand-calculated
  expected values.
- **`test_retrieval_vector.py`** (12 tests) — embedding determinism and
  normalization, cosine similarity sanity checks, and vector-store search
  correctness against a small 3-document fixture corpus with an obvious
  expected ranking (a query about "HDFC UPI bank timeout" should rank the
  HDFC/UPI document first, not the unrelated KYC one) — plus cache
  read/write/reuse behavior.
- **`test_bundle.py`** (10 tests) — integration tests against the real
  generated dataset: response shape, required-field presence, source
  attribution on every item, structured items never carrying null `data`
  / unstructured items never carrying null `text`, relevance-sorted
  ordering, `top_k` enforcement, explicit-query override, unknown-id
  error handling, internal consistency between two structured items
  computed from the same call, and a concrete relevance check that the
  UPI+HDFC Bank candidate's top unstructured results actually mention UPI
  or HDFC.
- **`test_detector.py` / `test_stats.py`** (17 tests, pre-existing from
  the detection layer) — unaffected by this change, still passing.

Run with `cd backend && python -m pytest tests/ -v`.

## Limitations (worth being upfront about)

- **The local embedding backend is lexical, not semantic.** It's a
  hashing bag-of-words vectorizer with IDF weighting — it finds documents
  that share distinctive words with the query, not documents that mean
  the same thing using different words. For this project's small,
  topically-clustered synthetic corpus, that's enough to reliably surface
  the right document in the top few results, but it isn't a substitute
  for a real embedding model. Setting `MISTRAL_API_KEY` and deploying
  somewhere with network access to `api.mistral.ai` switches to true
  semantic embeddings with no other code changes.
- **The corpus is synthetic and small (24 documents).** It's built to
  closely mirror this project's own injected incidents so retrieval
  quality is demonstrable, not to be a realistic production-scale
  knowledge base.
- **Structured evidence recomputes from the raw dataset every call.**
  This is a deliberate correctness choice (never trust a cached number
  blindly) but means `retrieve_evidence` does real pandas work each time
  rather than a pure lookup — fine at this dataset size (~9,000 rows),
  would need memoization or a real query layer at much larger scale.
