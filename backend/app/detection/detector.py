"""
Deterministic payment-degradation detection engine.

No LLM involved anywhere in this module. The approach is a rolling-window
vs. leave-window-out-baseline comparison, evaluated independently across
several dimensions (payment method, institution, geography, a
method+institution pairing, failure reason, and a transaction-value
bucket), plus one dimension-less "ALL" pass to catch broad, non-segment-
specific degradation (e.g. a latency spike).

For each (dimension, segment value) the engine:
  1. Buckets that segment's transactions into hourly bins.
  2. Slides a `window_hours`-wide window across time, and at each step
     compares the window's hit rate (failure rate, or a specific
     failure-reason's share) against a baseline built from ALL of that
     segment's other transactions (leave-window-out).
  3. Flags hours where app.detection.stats.is_candidate(...) says the
     difference is both statistically significant and practically large.
  4. Merges consecutive flagged hours into a single "run" (one candidate
     incident) rather than emitting near-duplicate hourly detections for
     the same ongoing event, and recomputes final stats directly from the
     raw data over the run's exact time span for accuracy.

Output is a list of structured candidate-incident dicts (see
`build_candidate` for the exact schema) — observational only, no causal
claims. See docs/detection.md for the full write-up.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
import dataclasses
from datetime import timedelta

import pandas as pd

from app.detection.config import DEFAULT_CONFIG, DetectionConfig
from app.detection.stats import RateComparison, confidence_from_z, is_candidate, severity_from

STATUS_FAILED = "FAILED"

_incident_counter = itertools.count(1)


def _next_candidate_id() -> str:
    return f"cand_{next(_incident_counter):05d}"


def reset_id_counter() -> None:
    """Reset the candidate-id counter. Mainly useful for deterministic tests."""
    global _incident_counter
    _incident_counter = itertools.count(1)


@dataclass
class _Run:
    start_hour_idx: int
    end_hour_idx: int  # inclusive
    peak_z: float


def _hourly_totals(seg_df: pd.DataFrame, hit_mask: pd.Series, full_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Bucket a segment's transactions into hourly total/hit counts, reindexed
    to the full dataset's hourly index (missing hours filled with 0)."""
    ts_floor = seg_df["timestamp"].dt.floor("h")
    total = ts_floor.value_counts().reindex(full_index, fill_value=0).sort_index()
    hit = ts_floor[hit_mask].value_counts().reindex(full_index, fill_value=0).sort_index()
    return pd.DataFrame({"total": total, "hit": hit})


def _find_runs(flagged: pd.Series, z_series: pd.Series) -> list[_Run]:
    """Collapse a boolean per-hour flagged series into contiguous runs."""
    runs: list[_Run] = []
    in_run = False
    start = None
    peak_z = -1.0
    prev_idx = None

    for pos in range(len(flagged)):
        val = flagged.iloc[pos]
        if val:
            if not in_run:
                in_run = True
                start = pos
                peak_z = z_series.iloc[pos]
            else:
                peak_z = max(peak_z, z_series.iloc[pos])
            prev_idx = pos
        else:
            if in_run:
                runs.append(_Run(start, prev_idx, peak_z))
                in_run = False
    if in_run:
        runs.append(_Run(start, prev_idx, peak_z))
    return runs


def _evaluate_segment(
    seg_df: pd.DataFrame,
    hit_mask: pd.Series,
    dimension: str,
    affected_segment: dict,
    full_index: pd.DatetimeIndex,
    config: DetectionConfig,
) -> list[dict]:
    """Run the sliding-window scan for a single (dimension, segment) pair."""
    if len(seg_df) == 0:
        return []

    hourly = _hourly_totals(seg_df, hit_mask, full_index)
    window_total = hourly["total"].rolling(config.window_hours, min_periods=config.window_hours).sum()
    window_hit = hourly["hit"].rolling(config.window_hours, min_periods=config.window_hours).sum()

    global_total = int(hourly["total"].sum())
    global_hit = int(hourly["hit"].sum())

    z_scores = []
    flags = []
    for wt, wh in zip(window_total, window_hit):
        if pd.isna(wt) or pd.isna(wh):
            z_scores.append(0.0)
            flags.append(False)
            continue
        wt, wh = int(wt), int(wh)
        bt, bh = global_total - wt, global_hit - wh
        cmp = RateComparison(baseline_total=bt, baseline_hits=bh, window_total=wt, window_hits=wh)
        z_scores.append(cmp.z_score)
        flags.append(
            is_candidate(
                cmp,
                min_window_n=config.min_window_n,
                min_baseline_n=config.min_baseline_n,
                z_threshold=config.z_threshold,
                min_relative_change_pct=config.min_relative_change_pct,
                min_absolute_rate=config.min_absolute_rate,
            )
        )

    flagged_series = pd.Series(flags, index=hourly.index)
    z_series = pd.Series(z_scores, index=hourly.index)

    if config.step_hours > 1:
        keep = pd.Series(False, index=hourly.index)
        keep.iloc[:: config.step_hours] = True
        flagged_series = flagged_series & keep

    runs = _find_runs(flagged_series, z_series)
    candidates = []
    for run in runs:
        run_end_hour = hourly.index[run.end_hour_idx]
        run_start_hour = hourly.index[run.start_hour_idx] - timedelta(hours=config.window_hours - 1)
        window_end = run_end_hour + timedelta(hours=1)

        window_df = seg_df[(seg_df["timestamp"] >= run_start_hour) & (seg_df["timestamp"] < window_end)]
        window_hit_mask = hit_mask.loc[window_df.index]
        baseline_df = seg_df.drop(index=window_df.index)
        baseline_hit_mask = hit_mask.loc[baseline_df.index]

        cmp = RateComparison(
            baseline_total=len(baseline_df),
            baseline_hits=int(baseline_hit_mask.sum()),
            window_total=len(window_df),
            window_hits=int(window_hit_mask.sum()),
        )
        if not is_candidate(
            cmp,
            min_window_n=config.min_window_n,
            min_baseline_n=config.min_baseline_n,
            z_threshold=config.z_threshold,
            min_relative_change_pct=config.min_relative_change_pct,
            min_absolute_rate=config.min_absolute_rate,
        ):
            continue  # final recheck on exact-window stats didn't hold up

        failed_in_window = window_df[window_df["status"] == STATUS_FAILED]
        revenue_affected = round(float(failed_in_window["amount"].sum()), 2)

        reason_breakdown = (
            window_df.loc[window_df["status"] == STATUS_FAILED, "failure_reason"]
            .value_counts()
            .to_dict()
        )

        candidates.append(
            build_candidate(
                dimension=dimension,
                affected_segment=affected_segment,
                comparison=cmp,
                window_start=run_start_hour,
                window_end=window_end,
                revenue_affected=revenue_affected,
                supporting_reason_breakdown=reason_breakdown,
                window_df=window_df,
                baseline_df=baseline_df,
            )
        )
    return candidates


def build_candidate(
    dimension: str,
    affected_segment: dict,
    comparison: RateComparison,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    revenue_affected: float,
    supporting_reason_breakdown: dict,
    window_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
) -> dict:
    """Assemble the structured candidate-incident record. Pure formatting —
    no thresholding decisions happen here (those already happened upstream)."""
    z = comparison.z_score
    degradation_pct = comparison.relative_change_pct
    degradation_display = None if degradation_pct == float("inf") else round(degradation_pct, 1)

    baseline_median_latency = (
        float(baseline_df["processing_latency_ms"].median()) if len(baseline_df) else None
    )
    window_median_latency = (
        float(window_df["processing_latency_ms"].median()) if len(window_df) else None
    )

    segment_desc = ", ".join(f"{k}={v}" for k, v in affected_segment.items()) or "all segments"
    if degradation_display is None:
        change_phrase = "baseline rate was ~0%, so relative change is undefined"
    else:
        change_phrase = f"{degradation_display:+.1f}% relative change"
    observation = (
        f"Transactions where {segment_desc} showed a failure rate of "
        f"{comparison.window_rate:.1%} between {window_start.isoformat()} and "
        f"{window_end.isoformat()}, versus a baseline of {comparison.baseline_rate:.1%} "
        f"({change_phrase}), across {comparison.window_total} transactions "
        f"(z={z:.2f})."
    )

    return {
        "incident_id": _next_candidate_id(),
        "detection_timestamp": pd.Timestamp.utcnow().isoformat(),
        "affected_dimension": dimension,
        "affected_segment": affected_segment,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "baseline_success_rate": round(1 - comparison.baseline_rate, 4),
        "current_success_rate": round(1 - comparison.window_rate, 4),
        "degradation_percentage": degradation_display,
        "transaction_count": comparison.window_total,
        "revenue_affected": revenue_affected,
        "severity": severity_from(comparison.window_rate, z),
        "confidence_score": confidence_from_z(z),
        "observation": observation,
        "supporting_statistics": {
            "z_score": round(z, 3),
            "baseline_transaction_count": comparison.baseline_total,
            "baseline_failure_count": comparison.baseline_hits,
            "window_failure_count": comparison.window_hits,
            "baseline_median_latency_ms": baseline_median_latency,
            "window_median_latency_ms": window_median_latency,
            "failure_reason_breakdown": supporting_reason_breakdown,
        },
    }


# --------------------------------------------------------------------------
# Dimension drivers
# --------------------------------------------------------------------------

def _run_categorical_dimension(
    df: pd.DataFrame,
    column: str,
    dimension_name: str,
    hit_mask_full: pd.Series,
    full_index: pd.DatetimeIndex,
    config: DetectionConfig,
) -> list[dict]:
    candidates = []
    values = [v for v in df[column].dropna().unique() if v != ""]
    if len(values) < config.min_segment_values:
        return candidates
    for value in values:
        seg_df = df[df[column] == value]
        seg_hit_mask = hit_mask_full.loc[seg_df.index]
        candidates.extend(
            _evaluate_segment(
                seg_df, seg_hit_mask, dimension_name, {column: value}, full_index, config
            )
        )
    return candidates


def _run_pair_dimension(
    df: pd.DataFrame,
    col_a: str,
    col_b: str,
    hit_mask_full: pd.Series,
    full_index: pd.DatetimeIndex,
    config: DetectionConfig,
) -> list[dict]:
    candidates = []
    fine_config = dataclasses.replace(
        config, min_window_n=config.fine_min_window_n, min_baseline_n=config.fine_min_baseline_n
    )
    pairs = df[[col_a, col_b]].drop_duplicates()
    for _, row in pairs.iterrows():
        val_a, val_b = row[col_a], row[col_b]
        seg_df = df[(df[col_a] == val_a) & (df[col_b] == val_b)]
        if len(seg_df) < fine_config.min_window_n:  # cheap pre-filter, real gate is is_candidate
            continue
        seg_hit_mask = hit_mask_full.loc[seg_df.index]
        candidates.extend(
            _evaluate_segment(
                seg_df,
                seg_hit_mask,
                f"{col_a}+{col_b}",
                {col_a: val_a, col_b: val_b},
                full_index,
                fine_config,
            )
        )
    return candidates


def _run_failure_reason_dimension(
    df: pd.DataFrame, full_index: pd.DatetimeIndex, config: DetectionConfig
) -> list[dict]:
    candidates = []
    fine_config = dataclasses.replace(
        config, min_window_n=config.fine_min_window_n, min_baseline_n=config.fine_min_baseline_n
    )
    reasons = [r for r in df["failure_reason"].dropna().unique() if r != ""]
    for reason in reasons:
        hit_mask = df["failure_reason"] == reason
        candidates.extend(
            _evaluate_segment(
                df, hit_mask, "failure_reason", {"failure_reason": reason}, full_index, fine_config
            )
        )
    return candidates


def _run_all_dimension(
    df: pd.DataFrame, hit_mask_full: pd.Series, full_index: pd.DatetimeIndex, config: DetectionConfig
) -> list[dict]:
    return _evaluate_segment(df, hit_mask_full, "all", {}, full_index, config)


# --------------------------------------------------------------------------
# Public entrypoint
# --------------------------------------------------------------------------

def detect_incidents(df: pd.DataFrame, config: DetectionConfig = DEFAULT_CONFIG) -> list[dict]:
    """Run the full detection sweep across all supported dimensions.

    Parameters
    ----------
    df : DataFrame with at least the columns produced by
        app.data.loader.load_transactions(): timestamp, status, amount,
        payment_method, institution, geography, failure_reason,
        processing_latency_ms.
    config : DetectionConfig, thresholds for what counts as a candidate.

    Returns
    -------
    List of candidate-incident dicts (schema: see build_candidate), sorted
    by severity then by transaction count descending.
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    full_index = pd.date_range(
        df["timestamp"].min().floor("h"), df["timestamp"].max().floor("h"), freq="1h"
    )

    hit_mask_full = df["status"] == STATUS_FAILED

    amount_labels = list(config.amount_bucket_labels[: config.amount_buckets])
    try:
        df["_amount_bucket"] = pd.qcut(df["amount"], q=config.amount_buckets, labels=amount_labels)
    except ValueError:
        df["_amount_bucket"] = None



    candidates: list[dict] = []
    candidates += _run_categorical_dimension(
        df, "payment_method", "payment_method", hit_mask_full, full_index, config
    )
    candidates += _run_categorical_dimension(
        df, "institution", "institution", hit_mask_full, full_index, config
    )
    candidates += _run_categorical_dimension(
        df, "geography", "geography", hit_mask_full, full_index, config
    )
    candidates += _run_categorical_dimension(
        df, "_amount_bucket", "transaction_value_bucket", hit_mask_full, full_index, config
    )
    candidates += _run_pair_dimension(
        df, "payment_method", "institution", hit_mask_full, full_index, config
    )
    candidates += _run_failure_reason_dimension(df, full_index, config)
    candidates += _run_all_dimension(df, hit_mask_full, full_index, config)

    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    candidates.sort(
        key=lambda c: (severity_rank.get(c["severity"], 9), -c["transaction_count"])
    )
    return candidates
