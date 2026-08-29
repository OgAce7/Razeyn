"""
Pure statistics helpers used by the detection engine.

Deliberately dependency-light (no pandas) so these can be unit tested in
isolation from the dataset-shaping logic in detector.py.

Everything here answers one question: "given a `hit rate` (e.g. failure
rate) in a current window vs. a baseline period, is the difference large
enough — in both relative-size and statistical-significance terms — to be
worth surfacing as a candidate incident?" Nothing here assigns a cause.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RateComparison:
    baseline_total: int
    baseline_hits: int
    window_total: int
    window_hits: int

    @property
    def baseline_rate(self) -> float:
        return self.baseline_hits / self.baseline_total if self.baseline_total else 0.0

    @property
    def window_rate(self) -> float:
        return self.window_hits / self.window_total if self.window_total else 0.0

    @property
    def relative_change_pct(self) -> float:
        """Relative change in hit rate, window vs baseline, as a percentage.

        Guards against a near-zero baseline rate (which would otherwise
        blow up to an enormous or infinite relative change) by falling
        back to `None`-like behavior via the caller checking
        `baseline_rate` separately before trusting this value.
        """
        b = self.baseline_rate
        if b <= 0:
            return math.inf if self.window_rate > 0 else 0.0
        return ((self.window_rate - b) / b) * 100.0

    @property
    def z_score(self) -> float:
        """Two-proportion z-score (pooled variance) for window vs baseline hit rate.

        Standard test for "is the difference between two observed
        proportions larger than sampling noise would explain". Returns
        0.0 if either sample is empty or the pooled variance is zero
        (e.g. both rates are 0% or both are 100%).
        """
        n1, n2 = self.window_total, self.baseline_total
        if n1 == 0 or n2 == 0:
            return 0.0
        x1, x2 = self.window_hits, self.baseline_hits
        p_pool = (x1 + x2) / (n1 + n2)
        if p_pool <= 0 or p_pool >= 1:
            return 0.0
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
        if se == 0:
            return 0.0
        return (self.window_rate - self.baseline_rate) / se


def confidence_from_z(z_score: float) -> float:
    """Map a z-score to a bounded 0-1 confidence score for display purposes.

    Monotonic, saturating mapping (z=3 -> ~0.5, z=9 -> ~0.75, z=30 -> ~0.91).
    This is a convenience transform for UI/consumers, not a p-value —
    the raw z-score is always reported alongside it for anyone who wants
    the statistical detail.
    """
    if z_score <= 0:
        return 0.0
    return round(min(0.99, z_score / (z_score + 3.0)), 3)


def severity_from(window_rate: float, z_score: float) -> str:
    """Deterministic severity bucket from observed failure rate + z-score.

    Thresholds are intentionally simple and documented here rather than
    tuned by a model — see docs for rationale.
    """
    if window_rate >= 0.50 or z_score >= 12:
        return "CRITICAL"
    if window_rate >= 0.30 or z_score >= 8:
        return "HIGH"
    if window_rate >= 0.15 or z_score >= 5:
        return "MEDIUM"
    return "LOW"


def is_candidate(
    comparison: RateComparison,
    min_window_n: int,
    min_baseline_n: int,
    z_threshold: float,
    min_relative_change_pct: float,
    min_absolute_rate: float,
) -> bool:
    """The single gate a (segment, window) pair must clear to be reported.

    All of the following must hold — this is what keeps the detector from
    firing on tiny samples or ordinary noise:
      1. Enough transactions in both the window and the baseline to trust
         a rate estimate at all (`min_window_n`, `min_baseline_n`).
      2. The window's hit rate is statistically distinguishable from the
         baseline's, per a two-proportion z-test (`z_threshold`).
      3. The relative increase over baseline is large, not just
         statistically nonzero (`min_relative_change_pct`) — protects
         against a baseline of e.g. 0.5% moving to 0.8% registering as a
         "60% relative increase" while being practically meaningless.
      4. The window's absolute hit rate clears a floor
         (`min_absolute_rate`) — protects against extremely low absolute
         rates dominating the relative-change math.
    """
    if comparison.window_total < min_window_n:
        return False
    if comparison.baseline_total < min_baseline_n:
        return False
    if comparison.window_rate < min_absolute_rate:
        return False
    if comparison.window_rate <= comparison.baseline_rate:
        return False
    if comparison.z_score < z_threshold:
        return False
    rel_change = comparison.relative_change_pct
    if rel_change is math.inf:
        return True  # baseline was ~0%, window is meaningfully above the floor already
    if rel_change < min_relative_change_pct:
        return False
    return True
