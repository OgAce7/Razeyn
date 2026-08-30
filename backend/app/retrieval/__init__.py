"""
Evidence Retrieval.

Given a detected incident (from app/detection/), returns the structured
and unstructured evidence an AI investigation agent needs to cite in its
reasoning — with source attribution preserved on every item.

Implemented:
- structured.py     Structured evidence (transaction stats, failure rates,
                     breakdowns, affected transaction IDs, revenue impact,
                     historical trend) — computed directly from the
                     transaction dataset via pandas. No LLM.
- embeddings.py      Text embedding abstraction: optional Mistral API,
                     deterministic local hashing-vectorizer fallback.
- vector_store.py    Lightweight local vector store (JSON-backed cache)
                     over a synthetic unstructured evidence corpus.
- corpus/            Synthetic incident reports, merchant notes, recovery
                     outcomes, and provider observations.
- bundle.py          The public entrypoint: retrieve_evidence(incident_id, query).

See docs/retrieval.md for the full write-up.
"""
