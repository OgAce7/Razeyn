"""
Integration tests for app/retrieval/bundle.retrieve_evidence — the public
interface the future AI agent will call. Runs against the actual
generated dataset + detection output (skipped automatically if those
haven't been generated, so this suite doesn't hard-fail on a fresh clone
before anyone has run app.data.generate / app.detection.run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.retrieval.bundle import retrieve_evidence
from app.retrieval.structured import CANDIDATE_INCIDENTS_PATH, IncidentNotFoundError

pytestmark = pytest.mark.skipif(
    not Path(CANDIDATE_INCIDENTS_PATH).exists(),
    reason="candidate_incidents.json not generated — run app.data.generate then app.detection.run",
)

REQUIRED_EVIDENCE_FIELDS = {
    "evidence_id",
    "evidence_type",
    "source",
    "data",
    "text",
    "relevance_score",
    "timestamp",
}


def _first_candidate_id() -> str:
    with open(CANDIDATE_INCIDENTS_PATH) as f:
        payload = json.load(f)
    assert payload["candidates"], "expected at least one candidate incident in the fixture data"
    return payload["candidates"][0]["incident_id"]


def test_retrieve_evidence_returns_expected_top_level_shape():
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id)

    assert result["incident_id"] == incident_id
    assert "retrieved_at" in result
    assert "query_used" in result
    assert isinstance(result["structured_evidence"], list) and result["structured_evidence"]
    assert isinstance(result["unstructured_evidence"], list)


def test_every_evidence_item_has_required_fields_and_attribution():
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id)

    for item in result["structured_evidence"] + result["unstructured_evidence"]:
        assert REQUIRED_EVIDENCE_FIELDS.issubset(item.keys())
        assert item["source"], f"{item['evidence_id']} missing source attribution"
        assert 0.0 <= item["relevance_score"] <= 1.0


def test_structured_evidence_data_is_never_null():
    """Structured items must carry real computed data, not text a model wrote."""
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id)
    for item in result["structured_evidence"]:
        assert item["data"] is not None
        assert item["text"] is None


def test_unstructured_evidence_has_text_not_data():
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id)
    for item in result["unstructured_evidence"]:
        assert item["text"]
        assert item["data"] is None


def test_unstructured_results_sorted_by_relevance_descending():
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id, top_k_unstructured=10)
    scores = [item["relevance_score"] for item in result["unstructured_evidence"]]
    assert scores == sorted(scores, reverse=True)


def test_top_k_unstructured_is_respected():
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id, top_k_unstructured=2)
    assert len(result["unstructured_evidence"]) == 2


def test_explicit_query_overrides_implicit_one():
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id, query="recovery outcome retry campaign")
    assert result["query_used"] == "recovery outcome retry campaign"


def test_unknown_incident_id_raises():
    with pytest.raises(IncidentNotFoundError):
        retrieve_evidence("cand_does_not_exist")


def test_affected_transaction_ids_evidence_matches_candidate_transaction_count():
    """Cross-check: the affected_transaction_ids structured evidence item's
    failed-transaction count should be internally consistent with the
    transaction_statistics item computed in the same call."""
    incident_id = _first_candidate_id()
    result = retrieve_evidence(incident_id)
    stats = next(
        i["data"] for i in result["structured_evidence"] if i["evidence_type"] == "transaction_statistics"
    )
    ids_evidence = next(
        i["data"] for i in result["structured_evidence"] if i["evidence_type"] == "affected_transaction_ids"
    )
    assert ids_evidence["total_affected_count"] == stats["window_failed_count"]


def test_bank_specific_incident_surfaces_relevant_evidence_in_top_results():
    """For the UPI+HDFC Bank candidate specifically, at least one of the
    top unstructured results should be about UPI/HDFC — a concrete check
    that retrieval isn't just returning arbitrary documents."""
    with open(CANDIDATE_INCIDENTS_PATH) as f:
        candidates = json.load(f)["candidates"]
    pair_candidates = [
        c
        for c in candidates
        if c["affected_dimension"] == "payment_method+institution"
        and c["affected_segment"].get("institution") == "HDFC Bank"
        and c["affected_segment"].get("payment_method") == "UPI"
    ]
    if not pair_candidates:
        pytest.skip("no UPI+HDFC Bank candidate present in this run of the detector")

    result = retrieve_evidence(pair_candidates[0]["incident_id"], top_k_unstructured=5)
    titles = " ".join(item["title"].lower() for item in result["unstructured_evidence"])
    assert "upi" in titles or "hdfc" in titles
