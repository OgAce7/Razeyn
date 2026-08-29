"""
Detector tests built on small, purpose-built synthetic datasets (not the
large generated dataset in app/data/synthetic/) so each scenario is fast,
self-contained, and its expected outcome is obvious from the test itself.

Covers the five required scenarios:
  1. Genuine degradation is detected.
  2. No degradation -> no candidates.
  3. Insufficient sample size -> no candidates, even with a real-looking
     failure-rate jump.
  4. Temporary noise (a brief, small random blip) -> no candidates.
  5. Multiple simultaneous, independent segments are each detected
     separately, and the unaffected segment is not.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.detection.config import DetectionConfig
from app.detection.detector import detect_incidents, reset_id_counter

FAILURE_REASONS_POOL = ["INSUFFICIENT_FUNDS", "INVALID_OTP", "RISK_DECLINE"]
INCIDENT_REASON = "BANK_TIMEOUT"


def make_dataset(
    start: datetime,
    hours: int,
    methods: list[str],
    rate_per_hour: float,
    baseline_failure_rate: float,
    seed: int = 7,
    degradations: list[dict] | None = None,
    institutions: list[str] | None = None,
    geographies: list[str] | None = None,
) -> pd.DataFrame:
    """Build a small, controlled synthetic transaction dataset.

    `degradations` is a list of dicts like:
        {"method": "A", "start_offset_h": 40, "duration_h": 8,
         "failure_rate": 0.45, "reason": "BANK_TIMEOUT"}
    Each describes a window where a specific payment_method's failure
    rate is overridden.

    Institution/geography default to a single constant value each so
    those dimensions are skipped by the detector (min_segment_values
    gate), keeping test assertions focused on payment_method behavior.
    """
    rng = random.Random(seed)
    institutions = institutions or ["Test Bank"]
    geographies = geographies or ["Test City"]
    degradations = degradations or []

    rows = []
    txn_id = 1
    for h in range(hours):
        hour_ts = start + timedelta(hours=h)
        for method in methods:
            count = _poisson(rng, rate_per_hour)
            active_degradation = next(
                (
                    d
                    for d in degradations
                    if d["method"] == method
                    and d["start_offset_h"] <= h < d["start_offset_h"] + d["duration_h"]
                ),
                None,
            )
            failure_rate = active_degradation["failure_rate"] if active_degradation else baseline_failure_rate
            reason_for_failure = active_degradation["reason"] if active_degradation else None

            for _ in range(count):
                ts = hour_ts + timedelta(seconds=rng.uniform(0, 3599))
                is_failed = rng.random() < failure_rate
                status = "FAILED" if is_failed else "SUCCESS"
                if is_failed:
                    reason = reason_for_failure or rng.choice(FAILURE_REASONS_POOL)
                else:
                    reason = ""

                rows.append(
                    {
                        "transaction_id": f"txn_{txn_id:06d}",
                        "timestamp": ts.isoformat(),
                        "customer_id": f"cust_{rng.randint(1, 200):04d}",
                        "amount": round(rng.uniform(50, 2000), 2),
                        "payment_method": method,
                        "institution": rng.choice(institutions),
                        "geography": rng.choice(geographies),
                        "status": status,
                        "failure_reason": reason,
                        "processing_latency_ms": rng.randint(300, 1200),
                        "retry_count": 0,
                    }
                )
                txn_id += 1

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _poisson(rng: random.Random, lam: float) -> int:
    """Minimal Poisson sampler using only the stdlib random module (Knuth's algorithm)."""
    import math

    l = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= l:
            return k - 1


TEST_CONFIG = DetectionConfig(
    window_hours=6,
    step_hours=1,
    min_window_n=30,
    min_baseline_n=120,
    z_threshold=5.0,
    min_relative_change_pct=60.0,
    min_absolute_rate=0.12,
    fine_min_window_n=15,
    fine_min_baseline_n=60,
    min_segment_values=2,
)


def by_dimension(candidates: list[dict], dimension: str) -> list[dict]:
    return [c for c in candidates if c["affected_dimension"] == dimension]


def segment_values(candidates: list[dict], key: str) -> set:
    return {c["affected_segment"].get(key) for c in candidates}


# --------------------------------------------------------------------------
# 1. Genuine degradation
# --------------------------------------------------------------------------

def test_genuine_degradation_is_detected():
    reset_id_counter()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = make_dataset(
        start=start,
        hours=20 * 24,
        methods=["A", "B"],
        rate_per_hour=6.0,
        baseline_failure_rate=0.04,
        degradations=[
            {"method": "A", "start_offset_h": 240, "duration_h": 8, "failure_rate": 0.45, "reason": INCIDENT_REASON}
        ],
        seed=1,
    )

    candidates = detect_incidents(df, TEST_CONFIG)
    method_candidates = by_dimension(candidates, "payment_method")

    assert len(method_candidates) >= 1
    assert "A" in segment_values(method_candidates, "payment_method")
    assert "B" not in segment_values(method_candidates, "payment_method")

    flagged = next(c for c in method_candidates if c["affected_segment"]["payment_method"] == "A")
    assert flagged["current_success_rate"] < flagged["baseline_success_rate"]
    assert flagged["transaction_count"] >= TEST_CONFIG.min_window_n
    assert flagged["revenue_affected"] > 0
    assert flagged["degradation_percentage"] > 0
    # observational language only — no causal claim
    assert "outage" not in flagged["observation"].lower()
    assert "caused" not in flagged["observation"].lower()


# --------------------------------------------------------------------------
# 2. No degradation
# --------------------------------------------------------------------------

def test_no_degradation_produces_no_candidates():
    reset_id_counter()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Run across several seeds: pure noise should essentially never clear
    # the combined sample-size + z-score + relative-change gates, but a
    # single seed occasionally will by chance (this is a real property of
    # scanning many overlapping windows/segments, not a test artifact) —
    # so we require the *large majority* of seeds to be clean rather than
    # asserting zero false positives from exactly one trial.
    false_positive_seeds = 0
    num_seeds = 8
    for seed in range(num_seeds):
        df = make_dataset(
            start=start,
            hours=20 * 24,
            methods=["A", "B"],
            rate_per_hour=6.0,
            baseline_failure_rate=0.04,
            degradations=[],
            seed=100 + seed,
        )
        candidates = detect_incidents(df, TEST_CONFIG)
        if by_dimension(candidates, "payment_method"):
            false_positive_seeds += 1

    # Allow at most one false positive across 8 independent noise-only
    # datasets (~12.5%) — documents the residual false-positive rate
    # rather than pretending a fixed-threshold test is perfectly immune
    # to multiple-comparisons noise.
    assert false_positive_seeds <= 1


# --------------------------------------------------------------------------
# 3. Insufficient sample size
# --------------------------------------------------------------------------

def test_insufficient_sample_size_suppresses_detection():
    reset_id_counter()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Very short history + low volume: even a real-looking failure spike
    # shouldn't clear min_window_n / min_baseline_n.
    df = make_dataset(
        start=start,
        hours=18,  # well under a day of history
        methods=["A", "B"],
        rate_per_hour=1.0,  # low volume
        baseline_failure_rate=0.04,
        degradations=[
            {"method": "A", "start_offset_h": 8, "duration_h": 6, "failure_rate": 0.6, "reason": INCIDENT_REASON}
        ],
        seed=3,
    )

    candidates = detect_incidents(df, TEST_CONFIG)
    assert by_dimension(candidates, "payment_method") == []


# --------------------------------------------------------------------------
# 4. Temporary noise
# --------------------------------------------------------------------------

def test_temporary_noise_is_not_flagged():
    reset_id_counter()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # A brief 1-hour blip with a handful of unlucky failures — not a
    # sustained pattern, and too small relative to the noise-resistance
    # gates (sample size / z-score / relative-change floors) to register.
    df = make_dataset(
        start=start,
        hours=20 * 24,
        methods=["A", "B"],
        rate_per_hour=6.0,
        baseline_failure_rate=0.04,
        degradations=[
            {"method": "A", "start_offset_h": 300, "duration_h": 1, "failure_rate": 0.20, "reason": INCIDENT_REASON}
        ],
        seed=4,
    )

    candidates = detect_incidents(df, TEST_CONFIG)
    assert by_dimension(candidates, "payment_method") == []


# --------------------------------------------------------------------------
# 5. Multiple simultaneous, independent segments
# --------------------------------------------------------------------------

def test_multiple_simultaneous_segments_detected_independently():
    reset_id_counter()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = make_dataset(
        start=start,
        hours=20 * 24,
        methods=["A", "B", "C"],
        rate_per_hour=6.0,
        baseline_failure_rate=0.04,
        degradations=[
            {"method": "A", "start_offset_h": 200, "duration_h": 8, "failure_rate": 0.40, "reason": "BANK_TIMEOUT"},
            {"method": "C", "start_offset_h": 200, "duration_h": 8, "failure_rate": 0.50, "reason": "GATEWAY_ERROR"},
        ],
        seed=5,
    )

    candidates = detect_incidents(df, TEST_CONFIG)
    method_candidates = by_dimension(candidates, "payment_method")
    flagged_methods = segment_values(method_candidates, "payment_method")

    assert "A" in flagged_methods
    assert "C" in flagged_methods
    assert "B" not in flagged_methods

    a_candidate = next(c for c in method_candidates if c["affected_segment"]["payment_method"] == "A")
    c_candidate = next(c for c in method_candidates if c["affected_segment"]["payment_method"] == "C")
    assert a_candidate["incident_id"] != c_candidate["incident_id"]
    assert a_candidate["supporting_statistics"]["failure_reason_breakdown"]
    assert c_candidate["supporting_statistics"]["failure_reason_breakdown"]


# --------------------------------------------------------------------------
# Schema sanity check (applies to any produced candidate)
# --------------------------------------------------------------------------

def test_candidate_schema_has_required_fields():
    reset_id_counter()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = make_dataset(
        start=start,
        hours=20 * 24,
        methods=["A", "B"],
        rate_per_hour=6.0,
        baseline_failure_rate=0.04,
        degradations=[
            {"method": "A", "start_offset_h": 240, "duration_h": 8, "failure_rate": 0.45, "reason": INCIDENT_REASON}
        ],
        seed=1,
    )
    candidates = detect_incidents(df, TEST_CONFIG)
    assert candidates, "expected at least one candidate for this fixture"

    required_fields = {
        "incident_id",
        "detection_timestamp",
        "affected_dimension",
        "affected_segment",
        "baseline_success_rate",
        "current_success_rate",
        "degradation_percentage",
        "transaction_count",
        "revenue_affected",
        "severity",
        "confidence_score",
        "observation",
        "supporting_statistics",
    }
    for c in candidates:
        assert required_fields.issubset(c.keys())
        assert c["severity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert 0.0 <= c["confidence_score"] <= 1.0
