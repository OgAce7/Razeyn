import math

import pytest

from app.detection.stats import RateComparison, confidence_from_z, is_candidate, severity_from


def test_rate_comparison_basic_rates():
    cmp = RateComparison(baseline_total=1000, baseline_hits=40, window_total=100, window_hits=40)
    assert cmp.baseline_rate == 0.04
    assert cmp.window_rate == 0.40
    assert cmp.relative_change_pct == pytest.approx(900.0, rel=0.01)


def test_relative_change_zero_baseline_hits_but_window_has_hits_is_inf():
    cmp = RateComparison(baseline_total=500, baseline_hits=0, window_total=50, window_hits=5)
    assert cmp.relative_change_pct == math.inf


def test_relative_change_zero_baseline_and_zero_window_is_zero():
    cmp = RateComparison(baseline_total=500, baseline_hits=0, window_total=50, window_hits=0)
    assert cmp.relative_change_pct == 0.0


def test_z_score_zero_when_rates_equal():
    cmp = RateComparison(baseline_total=1000, baseline_hits=40, window_total=200, window_hits=8)
    assert abs(cmp.z_score) < 1e-9


def test_z_score_grows_with_bigger_gap_and_sample_size():
    small_gap = RateComparison(baseline_total=1000, baseline_hits=40, window_total=100, window_hits=6)
    big_gap = RateComparison(baseline_total=1000, baseline_hits=40, window_total=100, window_hits=40)
    assert big_gap.z_score > small_gap.z_score > 0


def test_confidence_from_z_monotonic_and_bounded():
    assert confidence_from_z(0) == 0.0
    assert confidence_from_z(-5) == 0.0
    low = confidence_from_z(3)
    high = confidence_from_z(20)
    assert 0 < low < high < 1
    assert confidence_from_z(1000) <= 0.99


def test_severity_thresholds():
    assert severity_from(window_rate=0.6, z_score=1) == "CRITICAL"
    assert severity_from(window_rate=0.35, z_score=1) == "HIGH"
    assert severity_from(window_rate=0.20, z_score=1) == "MEDIUM"
    assert severity_from(window_rate=0.05, z_score=1) == "LOW"
    assert severity_from(window_rate=0.01, z_score=9) == "HIGH"  # z alone can drive severity up


def test_is_candidate_rejects_small_window_sample():
    cmp = RateComparison(baseline_total=1000, baseline_hits=40, window_total=10, window_hits=8)
    assert not is_candidate(
        cmp, min_window_n=30, min_baseline_n=120, z_threshold=4.5,
        min_relative_change_pct=60, min_absolute_rate=0.12,
    )


def test_is_candidate_rejects_small_baseline_sample():
    cmp = RateComparison(baseline_total=20, baseline_hits=1, window_total=40, window_hits=16)
    assert not is_candidate(
        cmp, min_window_n=30, min_baseline_n=120, z_threshold=4.5,
        min_relative_change_pct=60, min_absolute_rate=0.12,
    )


def test_is_candidate_accepts_clear_degradation():
    cmp = RateComparison(baseline_total=1000, baseline_hits=40, window_total=60, window_hits=24)
    assert is_candidate(
        cmp, min_window_n=30, min_baseline_n=120, z_threshold=4.5,
        min_relative_change_pct=60, min_absolute_rate=0.12,
    )


def test_is_candidate_rejects_when_window_rate_not_above_baseline():
    cmp = RateComparison(baseline_total=1000, baseline_hits=40, window_total=60, window_hits=1)
    assert not is_candidate(
        cmp, min_window_n=30, min_baseline_n=120, z_threshold=4.5,
        min_relative_change_pct=60, min_absolute_rate=0.12,
    )
