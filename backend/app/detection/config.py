"""
Detection engine configuration.

All tunable thresholds live here so they can be inspected/adjusted without
digging through detector.py. Defaults are chosen to be conservative
(fewer, more confident candidates) rather than maximally sensitive —
appropriate for a first pass that a human or downstream agent will review.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionConfig:
    # Sliding window used to evaluate "current" behavior, in hours. Wide
    # enough that lower-traffic segments (e.g. a less common payment
    # method) can still accumulate enough transactions per window to
    # clear `min_window_n`, even though each individual hour's z-score
    # may already be well above threshold on its own.
    window_hours: int = 6

    # Step between successive window evaluations, in hours. 1 = evaluate
    # every hour (windows overlap); coarser steps run faster but can miss
    # or mis-time short incidents.
    step_hours: int = 1

    # Minimum transactions required inside the window for its rate
    # estimate to be trusted at all. At a ~4% baseline failure rate, tiny
    # windows are dominated by sampling noise (a single extra failure in
    # a 15-transaction window already looks like a "125% increase") —
    # this floor keeps the rate estimate itself meaningful, while still
    # being low enough that lower-traffic segments (a single city, a
    # single less-common payment method) can clear it during a real
    # incident window.
    min_window_n: int = 30

    # Minimum transactions required in the baseline (all remaining history
    # for that segment, outside the window) for the same reason.
    min_baseline_n: int = 120

    # Two-proportion z-test threshold. Deliberately conservative — with
    # many overlapping windows and dimensions being scanned each run
    # (a multiple-comparisons setting), a lower threshold produces
    # occasional false positives purely from ordinary noise, even though
    # a single test at that threshold would be very unlikely to fire by
    # chance. 5.0 keeps the false-positive rate low across a full sweep
    # while still leaving genuine incidents (which score 8-12+ in this
    # project's synthetic dataset) with a comfortable margin.
    z_threshold: float = 5.0

    # Minimum relative increase in hit rate over baseline, in percent, to
    # be worth reporting even if statistically significant.
    min_relative_change_pct: float = 60.0

    # Minimum absolute hit (failure) rate in the window. Keeps very low
    # absolute rates (e.g. 0.3% -> 0.6%) from being reported purely
    # because their *relative* change looks large.
    min_absolute_rate: float = 0.12

    # Finer-grained dimensions (a payment_method+institution pairing,
    # individual failure reasons) naturally split traffic into smaller
    # populations than a single column does. Using the same sample floor
    # as single-column dimensions would make these dimensions unable to
    # ever fire even on a genuine, narrow incident. These thresholds are
    # lower but still gated by the same z-score/relative-change/absolute-
    # rate requirements, so noise resistance doesn't disappear — it comes
    # from the statistical test, not just the sample floor.
    fine_min_window_n: int = 15
    fine_min_baseline_n: int = 60

    # Quantile buckets used for the transaction-value dimension.
    amount_buckets: int = 3
    amount_bucket_labels: tuple = ("LOW_VALUE", "MID_VALUE", "HIGH_VALUE")

    # Minimum number of distinct values a categorical column needs before
    # it's worth slicing on at all (skips degenerate/near-constant columns).
    min_segment_values: int = 2


DEFAULT_CONFIG = DetectionConfig()
