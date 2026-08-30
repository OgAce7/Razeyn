"""
Tests for app/retrieval/structured.py — verifies every computed number
against a manually-built, small, fully-known transaction DataFrame, so
each assertion has an obvious hand-checkable expected value.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.retrieval.structured import (
    IncidentNotFoundError,
    compute_structured_evidence,
    load_candidate_incident,
    resolve_segment_mask,
)


def make_rows():
    """8 transactions: 4 inside the window for payment_method=UPI (2 failed,
    2 success), 4 outside the window / other methods, so every count below
    can be checked by hand."""
    rows = [
        # inside window, UPI, institution X -> part of the segment+window
        {"transaction_id": "t1", "timestamp": "2026-01-01T10:00:00Z", "customer_id": "c1",
         "amount": 100.0, "payment_method": "UPI", "institution": "X", "geography": "CityA",
         "status": "FAILED", "failure_reason": "BANK_TIMEOUT", "processing_latency_ms": 900,
         "retry_count": 1},
        {"transaction_id": "t2", "timestamp": "2026-01-01T10:30:00Z", "customer_id": "c2",
         "amount": 200.0, "payment_method": "UPI", "institution": "X", "geography": "CityB",
         "status": "FAILED", "failure_reason": "NETWORK_ERROR", "processing_latency_ms": 950,
         "retry_count": 1},
        {"transaction_id": "t3", "timestamp": "2026-01-01T11:00:00Z", "customer_id": "c3",
         "amount": 50.0, "payment_method": "UPI", "institution": "X", "geography": "CityA",
         "status": "SUCCESS", "failure_reason": "", "processing_latency_ms": 400,
         "retry_count": 0},
        {"transaction_id": "t4", "timestamp": "2026-01-01T11:30:00Z", "customer_id": "c4",
         "amount": 75.0, "payment_method": "UPI", "institution": "X", "geography": "CityB",
         "status": "SUCCESS", "failure_reason": "", "processing_latency_ms": 420,
         "retry_count": 0},
        # outside window, same segment (UPI, institution X) -> baseline
        {"transaction_id": "t5", "timestamp": "2026-01-02T10:00:00Z", "customer_id": "c5",
         "amount": 60.0, "payment_method": "UPI", "institution": "X", "geography": "CityA",
         "status": "SUCCESS", "failure_reason": "", "processing_latency_ms": 410,
         "retry_count": 0},
        {"transaction_id": "t6", "timestamp": "2026-01-02T10:30:00Z", "customer_id": "c6",
         "amount": 80.0, "payment_method": "UPI", "institution": "X", "geography": "CityA",
         "status": "FAILED", "failure_reason": "INSUFFICIENT_FUNDS", "processing_latency_ms": 500,
         "retry_count": 0},
        # different segment entirely (CARD) -> shouldn't affect UPI/X numbers
        {"transaction_id": "t7", "timestamp": "2026-01-01T10:15:00Z", "customer_id": "c7",
         "amount": 500.0, "payment_method": "CARD", "institution": "Y", "geography": "CityA",
         "status": "FAILED", "failure_reason": "RISK_DECLINE", "processing_latency_ms": 600,
         "retry_count": 0},
        {"transaction_id": "t8", "timestamp": "2026-01-01T10:45:00Z", "customer_id": "c8",
         "amount": 500.0, "payment_method": "CARD", "institution": "Y", "geography": "CityA",
         "status": "SUCCESS", "failure_reason": "", "processing_latency_ms": 300,
         "retry_count": 0},
    ]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


FAKE_INCIDENT = {
    "incident_id": "cand_test01",
    "affected_dimension": "payment_method+institution",
    "affected_segment": {"payment_method": "UPI", "institution": "X"},
    "window_start": "2026-01-01T09:00:00+00:00",
    "window_end": "2026-01-01T12:00:00+00:00",
    "supporting_statistics": {"z_score": 5.0},
}


def test_resolve_segment_mask_matches_expected_rows():
    df = make_rows()
    mask = resolve_segment_mask(df, {"payment_method": "UPI", "institution": "X"})
    matched_ids = set(df.loc[mask, "transaction_id"])
    assert matched_ids == {"t1", "t2", "t3", "t4", "t5", "t6"}


def test_resolve_segment_mask_empty_segment_matches_everything():
    df = make_rows()
    mask = resolve_segment_mask(df, {})
    assert mask.all()


def test_transaction_statistics_counts_are_exact():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df)
    stats_item = next(i for i in items if i["evidence_type"] == "transaction_statistics")
    data = stats_item["data"]

    assert data["window_transaction_count"] == 4  # t1-t4
    assert data["window_failed_count"] == 2  # t1, t2
    assert data["window_success_count"] == 2  # t3, t4
    assert data["window_failure_rate"] == 0.5
    assert data["baseline_transaction_count"] == 2  # t5, t6
    assert data["baseline_failed_count"] == 1  # t6
    assert data["baseline_failure_rate"] == 0.5


def test_revenue_impact_is_exact_sum():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df)
    revenue_item = next(i for i in items if i["evidence_type"] == "revenue_impact")
    data = revenue_item["data"]

    assert data["revenue_affected"] == 300.0  # t1 (100) + t2 (200)
    assert data["total_window_revenue"] == 425.0  # 100+200+50+75
    assert data["revenue_at_risk_share"] == pytest.approx(300 / 425, abs=1e-4)


def test_affected_transaction_ids_are_exact_and_only_failed():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df)
    ids_item = next(i for i in items if i["evidence_type"] == "affected_transaction_ids")
    data = ids_item["data"]

    assert data["total_affected_count"] == 2
    assert set(data["transaction_ids"]) == {"t1", "t2"}
    assert data["truncated"] is False


def test_affected_transaction_ids_truncates_when_over_limit():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df, max_transaction_ids=1)
    ids_item = next(i for i in items if i["evidence_type"] == "affected_transaction_ids")
    data = ids_item["data"]

    assert data["total_affected_count"] == 2
    assert len(data["transaction_ids"]) == 1
    assert data["truncated"] is True


def test_geography_breakdown_sums_to_window_total():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df)
    geo_item = next(i for i in items if i["evidence_type"] == "geography_breakdown")
    breakdown = geo_item["data"]["breakdown"]

    total = sum(row["total"] for row in breakdown)
    failed = sum(row["failed"] for row in breakdown)
    assert total == 4  # matches window_transaction_count
    assert failed == 2  # matches window_failed_count

    by_geo = {row["geography"]: row for row in breakdown}
    assert by_geo["CityA"]["total"] == 2  # t1, t3
    assert by_geo["CityA"]["failed"] == 1  # t1
    assert by_geo["CityB"]["total"] == 2  # t2, t4
    assert by_geo["CityB"]["failed"] == 1  # t2


def test_breakdown_skips_dimension_already_in_segment():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df)
    types = {i["evidence_type"] for i in items}
    # FAKE_INCIDENT's segment already covers payment_method + institution,
    # so those breakdowns would be redundant and should not be produced.
    assert "payment_method_breakdown" not in types
    assert "institution_breakdown" not in types
    assert "geography_breakdown" in types


def test_failure_reason_breakdown_matches_window_failures():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df)
    reason_item = next(i for i in items if i["evidence_type"] == "failure_reason_breakdown")
    counts = reason_item["data"]["failure_reason_counts"]
    assert counts == {"BANK_TIMEOUT": 1, "NETWORK_ERROR": 1}


def test_source_attribution_present_on_every_item():
    df = make_rows()
    items = compute_structured_evidence(FAKE_INCIDENT, df)
    for item in items:
        assert item["source"], f"{item['evidence_type']} is missing source attribution"
        assert item["evidence_id"].startswith(FAKE_INCIDENT["incident_id"])


def test_load_candidate_incident_raises_for_unknown_id(tmp_path):
    path = tmp_path / "candidate_incidents.json"
    path.write_text('{"candidates": [{"incident_id": "cand_00001"}]}')
    with pytest.raises(IncidentNotFoundError):
        load_candidate_incident("cand_99999", path=path)


def test_load_candidate_incident_finds_matching_id(tmp_path):
    path = tmp_path / "candidate_incidents.json"
    path.write_text('{"candidates": [{"incident_id": "cand_00001", "severity": "HIGH"}]}')
    result = load_candidate_incident("cand_00001", path=path)
    assert result["severity"] == "HIGH"
