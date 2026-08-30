"""
Tests for app/retrieval/embeddings.py and app/retrieval/vector_store.py.

Deliberately exercises only the local backend (no MISTRAL_API_KEY set in
the test environment, and no network access to api.mistral.ai in this
sandbox) — this is also exactly the path a fresh clone runs by default,
so it's the most important one to have covered.
"""

from __future__ import annotations

import json

import pytest

from app.retrieval.embeddings import cosine_similarity, embed_text
from app.retrieval.vector_store import UnstructuredEvidenceStore


def test_local_embedding_is_deterministic():
    vec1, backend1 = embed_text("HDFC Bank UPI failures")
    vec2, backend2 = embed_text("HDFC Bank UPI failures")
    assert backend1 == backend2 == "local"
    assert vec1 == vec2


def test_local_embedding_is_normalized():
    vec, _ = embed_text("some reasonably long piece of evidence text about failures")
    norm_sq = sum(v * v for v in vec)
    assert norm_sq == pytest.approx(1.0, abs=1e-6)


def test_cosine_similarity_identical_text_is_near_one():
    vec, _ = embed_text("wallet gateway errors spiked")
    assert cosine_similarity(vec, vec) == pytest.approx(1.0, abs=1e-6)


def test_cosine_similarity_unrelated_text_is_lower():
    vec_a, _ = embed_text("wallet gateway errors spiked across every bank")
    vec_b, _ = embed_text("quarterly office lease renewal paperwork submitted")
    vec_c, _ = embed_text("wallet payment gateway failures rose sharply today")
    # vec_c (topically similar to vec_a) should score higher than vec_b (unrelated)
    assert cosine_similarity(vec_a, vec_c) > cosine_similarity(vec_a, vec_b)


# --------------------------------------------------------------------------
# Vector store — built on a small, fully-controlled fixture corpus so
# expected rankings are obvious, rather than the large production corpus.
# --------------------------------------------------------------------------

FIXTURE_DOCS = [
    {
        "doc_id": "d1",
        "type": "incident_report",
        "source": "test/d1.md",
        "timestamp": "2026-01-01T00:00:00Z",
        "title": "HDFC Bank UPI timeout cluster",
        "text": "UPI payments through HDFC Bank failed with bank timeouts for several hours.",
        "tags": ["UPI", "HDFC"],
    },
    {
        "doc_id": "d2",
        "type": "incident_report",
        "source": "test/d2.md",
        "timestamp": "2026-01-02T00:00:00Z",
        "title": "Wallet gateway errors",
        "text": "Wallet checkout transactions failed due to gateway errors across all banks.",
        "tags": ["WALLET"],
    },
    {
        "doc_id": "d3",
        "type": "merchant_note",
        "source": "test/d3.md",
        "timestamp": "2026-01-03T00:00:00Z",
        "title": "Routine KYC backlog note",
        "text": "The onboarding team is clearing a backlog of KYC document reviews this week.",
        "tags": ["kyc", "unrelated"],
    },
]


@pytest.fixture()
def fixture_store(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({"documents": FIXTURE_DOCS}))
    cache_path = tmp_path / "cache.json"
    return UnstructuredEvidenceStore(corpus_path=corpus_path, cache_path=cache_path)


def test_search_ranks_relevant_document_first(fixture_store):
    results = fixture_store.search("HDFC UPI bank timeout", top_k=3)
    assert results[0][0]["doc_id"] == "d1"
    assert results[0][1] > results[-1][1]  # top score strictly higher than the weakest


def test_search_finds_wallet_document_for_wallet_query(fixture_store):
    results = fixture_store.search("wallet gateway failure", top_k=3)
    assert results[0][0]["doc_id"] == "d2"


def test_search_unrelated_query_ranks_kyc_doc_highest(fixture_store):
    results = fixture_store.search("KYC onboarding document backlog", top_k=3)
    assert results[0][0]["doc_id"] == "d3"


def test_search_top_k_limits_result_count(fixture_store):
    results = fixture_store.search("payments", top_k=1)
    assert len(results) == 1


def test_search_filter_fn_restricts_candidates(fixture_store):
    results = fixture_store.search(
        "failures", top_k=5, filter_fn=lambda d: d["type"] == "merchant_note"
    )
    assert all(doc["type"] == "merchant_note" for doc, _ in results)
    assert len(results) == 1
    assert results[0][0]["doc_id"] == "d3"


def test_cache_is_written_and_reused(fixture_store):
    assert fixture_store.cache_path.exists()
    with open(fixture_store.cache_path) as f:
        cache = json.load(f)
    assert set(cache["doc_ids"]) == {"d1", "d2", "d3"}
    assert cache["backend"] == "local"

    # A second store pointed at the same cache should reuse it rather than
    # recompute (same vectors -> identical search results).
    store2 = UnstructuredEvidenceStore(
        corpus_path=fixture_store.corpus_path, cache_path=fixture_store.cache_path
    )
    r1 = fixture_store.search("HDFC UPI timeout", top_k=1)
    r2 = store2.search("HDFC UPI timeout", top_k=1)
    assert r1[0][0]["doc_id"] == r2[0][0]["doc_id"]
    assert r1[0][1] == pytest.approx(r2[0][1])
