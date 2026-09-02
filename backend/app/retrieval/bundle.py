"""
Evidence retrieval interface.

The single entrypoint downstream code (the AI investigation agent, in a
later build step) should use:

    from app.retrieval.bundle import retrieve_evidence
    evidence = retrieve_evidence(incident_id="cand_00002", query="wallet failures")

Combines:
  - Structured evidence (app/retrieval/structured.py) — computed directly
    from the transaction dataset via pandas. No LLM involved.
  - Unstructured evidence (app/retrieval/vector_store.py) — retrieved via
    vector similarity search over a synthetic document corpus. No LLM
    involved in retrieval itself (embeddings are either a local hashing
    vectorizer or, optionally, Mistral's embedding API — never a
    generative model, and never used to invent evidence content).

Every returned evidence item carries a `source` (or document metadata)
field so any conclusion the future agent draws from it can be traced back
to exactly where the number or passage came from.
"""

from __future__ import annotations

import pandas as pd

from app.data.loader import load_transactions
from app.retrieval.structured import compute_structured_evidence, load_candidate_incident
from app.retrieval.vector_store import UnstructuredEvidenceStore

_store: UnstructuredEvidenceStore | None = None


def _get_store() -> UnstructuredEvidenceStore:
    global _store
    if _store is None:
        _store = UnstructuredEvidenceStore()
    return _store


def _implicit_query(incident: dict) -> str:
    """Build a search query from the incident itself when the caller
    doesn't supply one. Deliberately built from clean, meaningful fields
    (dimension, segment, failure reasons) rather than the full
    `observation` sentence — that sentence embeds ISO timestamps and
    numeric stats (e.g. repeated "00" from "T18:00:00+00:00") that would
    otherwise flood a lexical query with filler tokens and drown out the
    handful of words that actually distinguish one incident from another.

    Also folds in a "latency" keyword when the incident's own supporting
    statistics show meaningfully elevated latency in the window vs.
    baseline — a real observed signal already computed by the detection
    engine, not an inferred cause, but a useful search term for surfacing
    latency-related evidence for broad/unsegmented incidents where the
    dimension and segment alone ("all", no segment) aren't distinctive."""
    segment_desc = " ".join(f"{v}" for v in incident["affected_segment"].values())
    dimension = incident.get("affected_dimension", "").replace("_", " ").replace("+", " ")
    stats = incident.get("supporting_statistics", {})
    reasons = list(stats.get("failure_reason_breakdown", {}).keys())

    latency_terms = ""
    if not incident["affected_segment"]:  # only the broad "all" dimension needs this nudge —
        # segmented incidents already have a distinctive bank/method/city term
        window_latency = stats.get("window_median_latency_ms")
        baseline_latency = stats.get("baseline_median_latency_ms")
        if window_latency and baseline_latency and window_latency > baseline_latency * 1.15:
            latency_terms = "latency processing delay"

    parts = [dimension, segment_desc, "failure rate degradation", " ".join(reasons), latency_terms]
    return " ".join(p for p in parts if p).strip()


def _unstructured_evidence_items(
    incident_id: str, query: str, top_k: int, store: UnstructuredEvidenceStore
) -> list[dict]:
    results = store.search(query, top_k=top_k)
    items = []
    for doc, score in results:
        items.append(
            {
                "evidence_id": f"{incident_id}_ev_{doc['doc_id']}",
                "evidence_type": doc["type"],
                "source": doc["source"],
                "data": None,
                "text": doc["text"],
                "relevance_score": round(score, 4),
                "timestamp": doc.get("timestamp"),
                "title": doc.get("title"),
            }
        )
    return items


def retrieve_evidence_for_incident(
    incident: dict,
    transactions: pd.DataFrame,
    query: str | None = None,
    top_k_unstructured: int = 5,
) -> dict:
    """Same evidence-assembly logic as `retrieve_evidence`, but takes the
    candidate incident dict directly instead of looking it up on disk via
    `load_candidate_incident`. This is what makes evidence retrieval work
    for candidates that only exist in memory -- e.g. detected from an
    uploaded dataset that was never written to
    app/data/synthetic/candidate_incidents.json (see app/api/pipeline.py,
    which uses this for both the seeded and uploaded-dataset runs).

    `retrieve_evidence` below is now a thin wrapper around this: it does
    the on-disk lookup, then delegates here, so there is exactly one
    place that assembles a structured+unstructured evidence bundle.
    """
    structured_items = compute_structured_evidence(incident, transactions)

    effective_query = query if query else _implicit_query(incident)
    store = _get_store()
    unstructured_items = _unstructured_evidence_items(
        incident["incident_id"], effective_query, top_k_unstructured, store
    )

    return {
        "incident_id": incident["incident_id"],
        "retrieved_at": pd.Timestamp.utcnow().isoformat(),
        "query_used": effective_query,
        "structured_evidence": structured_items,
        "unstructured_evidence": unstructured_items,
    }


def retrieve_evidence(
    incident_id: str,
    query: str | None = None,
    top_k_unstructured: int = 5,
    transactions: pd.DataFrame | None = None,
) -> dict:
    """Retrieve all supporting evidence for a detected incident.

    Parameters
    ----------
    incident_id : the candidate incident id from the detection engine
        (e.g. "cand_00002" — see app/data/synthetic/candidate_incidents.json).
    query : optional free-text context to focus the unstructured search
        (e.g. "recovery outcomes", "similar past incidents"). If omitted,
        a query is derived from the incident's own segment + observation.
    top_k_unstructured : how many unstructured documents to return.
    transactions : optionally pass a pre-loaded DataFrame (mainly for
        tests); otherwise loaded via app.data.loader.load_transactions().

    Returns
    -------
    dict with:
        incident_id, retrieved_at,
        structured_evidence: list[evidence dict],
        unstructured_evidence: list[evidence dict]
    Each evidence dict has: evidence_id, evidence_type, source, data,
    text, relevance_score, timestamp.
    """
    incident = load_candidate_incident(incident_id)
    df = transactions if transactions is not None else load_transactions()
    return retrieve_evidence_for_incident(
        incident, df, query=query, top_k_unstructured=top_k_unstructured
    )
