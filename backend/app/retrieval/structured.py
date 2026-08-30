"""
Structured evidence.

Every number here is computed directly from the transaction dataset with
pandas — no LLM is involved anywhere in this file. This is deliberate:
the AI investigation agent (a later build step) must be able to trust
these figures as ground truth it can cite, not something a model
inferred or approximated.

Evidence is built around a *candidate incident* (the output of the
detection engine — app/detection/) — specifically its `affected_segment`
and `window_start`/`window_end`. Given those, this module recomputes the
supporting statistics fresh from the raw transactions rather than only
trusting the cached values already sitting in candidate_incidents.json,
so this module still works if it's ever pointed at incidents produced by
a different or updated detection run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.detection.config import DEFAULT_CONFIG

CANDIDATE_INCIDENTS_PATH = (
    Path(__file__).parent.parent / "data" / "synthetic" / "candidate_incidents.json"
)

STATUS_FAILED = "FAILED"


class IncidentNotFoundError(Exception):
    pass


def load_candidate_incident(incident_id: str, path: Path | str = CANDIDATE_INCIDENTS_PATH) -> dict:
    """Look up a single candidate incident by id from the detection engine's output."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run the detector first: python -m app.detection.run"
        )
    with open(path) as f:
        payload = json.load(f)
    for candidate in payload.get("candidates", []):
        if candidate["incident_id"] == incident_id:
            return candidate
    raise IncidentNotFoundError(f"No candidate incident with id {incident_id!r}")


def resolve_segment_mask(df: pd.DataFrame, affected_segment: dict) -> pd.Series:
    """Build a boolean mask selecting the transactions an incident's
    `affected_segment` refers to. Handles the segment shapes the detection
    engine can produce: plain column=value pairs (payment_method,
    institution, geography, and their pairing), the failure_reason
    dimension (a "hit" column rather than a partition), and the
    transaction_value_bucket dimension (recomputed with the same
    quantile config the detector used, so bucket membership matches).
    """
    if not affected_segment:
        return pd.Series(True, index=df.index)

    mask = pd.Series(True, index=df.index)
    for key, value in affected_segment.items():
        if key == "failure_reason":
            mask &= df["failure_reason"] == value
        elif key == "_amount_bucket":
            labels = list(DEFAULT_CONFIG.amount_bucket_labels[: DEFAULT_CONFIG.amount_buckets])
            buckets = pd.qcut(df["amount"], q=DEFAULT_CONFIG.amount_buckets, labels=labels)
            mask &= buckets == value
        elif key in df.columns:
            mask &= df[key] == value
        else:
            raise ValueError(f"Unknown segment key {key!r}; cannot resolve for structured evidence")
    return mask


def _window_and_baseline(df: pd.DataFrame, incident: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    ts = pd.to_datetime(df["timestamp"], utc=True)
    start = pd.Timestamp(incident["window_start"])
    end = pd.Timestamp(incident["window_end"])
    segment_mask = resolve_segment_mask(df, incident["affected_segment"])
    seg_df = df[segment_mask]
    seg_ts = ts[segment_mask]
    window_df = seg_df[(seg_ts >= start) & (seg_ts < end)]
    baseline_df = seg_df.drop(index=window_df.index)
    return window_df, baseline_df


def _segment_label(affected_segment: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in affected_segment.items()) or "all transactions"


def _evidence_item(
    evidence_id: str,
    evidence_type: str,
    source: str,
    data: Any,
    relevance_score: float,
    timestamp: str | None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "source": source,
        "data": data,
        "text": None,
        "relevance_score": round(relevance_score, 4),
        "timestamp": timestamp,
    }


def compute_structured_evidence(
    incident: dict, df: pd.DataFrame, max_transaction_ids: int = 200
) -> list[dict]:
    """Build the full set of structured evidence items for one incident.

    Returns a list of evidence dicts (see `_evidence_item` for the exact
    shape). All `data` fields are plain computed numbers/lists — nothing
    here is generated text.
    """
    incident_id = incident["incident_id"]
    segment = incident["affected_segment"]
    segment_label = _segment_label(segment)
    window_start, window_end = incident["window_start"], incident["window_end"]
    source_base = (
        f"transactions.csv, filtered to {segment_label}, window "
        f"[{window_start} .. {window_end})"
    )

    window_df, baseline_df = _window_and_baseline(df, incident)
    window_failed = window_df[window_df["status"] == STATUS_FAILED]
    baseline_failed = baseline_df[baseline_df["status"] == STATUS_FAILED]

    items = []

    # 1. Transaction statistics / failure rates -----------------------------
    window_total, baseline_total = len(window_df), len(baseline_df)
    window_fail_n, baseline_fail_n = len(window_failed), len(baseline_failed)
    window_rate = window_fail_n / window_total if window_total else 0.0
    baseline_rate = baseline_fail_n / baseline_total if baseline_total else 0.0
    items.append(
        _evidence_item(
            evidence_id=f"{incident_id}_ev_stats",
            evidence_type="transaction_statistics",
            source=source_base,
            data={
                "window_transaction_count": window_total,
                "window_failed_count": window_fail_n,
                "window_success_count": int((window_df["status"] == "SUCCESS").sum()),
                "window_pending_count": int((window_df["status"] == "PENDING").sum()),
                "window_failure_rate": round(window_rate, 4),
                "baseline_transaction_count": baseline_total,
                "baseline_failed_count": baseline_fail_n,
                "baseline_failure_rate": round(baseline_rate, 4),
                "z_score": incident.get("supporting_statistics", {}).get("z_score"),
            },
            relevance_score=1.0,
            timestamp=window_end,
        )
    )

    # 2. Revenue impact -------------------------------------------------------
    window_revenue_lost = float(window_failed["amount"].sum())
    window_revenue_total = float(window_df["amount"].sum())
    baseline_revenue_lost = float(baseline_failed["amount"].sum())
    items.append(
        _evidence_item(
            evidence_id=f"{incident_id}_ev_revenue",
            evidence_type="revenue_impact",
            source=source_base,
            data={
                "revenue_affected": round(window_revenue_lost, 2),
                "total_window_revenue": round(window_revenue_total, 2),
                "revenue_at_risk_share": round(
                    window_revenue_lost / window_revenue_total, 4
                )
                if window_revenue_total
                else 0.0,
                "baseline_revenue_lost_to_failures": round(baseline_revenue_lost, 2),
                "currency": "INR",
            },
            relevance_score=1.0,
            timestamp=window_end,
        )
    )

    # 3. Affected transaction IDs ---------------------------------------------
    all_ids = window_failed["transaction_id"].tolist()
    items.append(
        _evidence_item(
            evidence_id=f"{incident_id}_ev_txn_ids",
            evidence_type="affected_transaction_ids",
            source=source_base,
            data={
                "total_affected_count": len(all_ids),
                "transaction_ids": all_ids[:max_transaction_ids],
                "truncated": len(all_ids) > max_transaction_ids,
            },
            relevance_score=0.9,
            timestamp=window_end,
        )
    )

    # 4. Breakdown by payment method (skip if that's already the segment key) --
    if "payment_method" not in segment:
        items.append(_breakdown_item(incident_id, window_df, "payment_method", source_base, window_end))

    # 5. Breakdown by institution ----------------------------------------------
    if "institution" not in segment:
        items.append(_breakdown_item(incident_id, window_df, "institution", source_base, window_end))

    # 6. Breakdown by geography -------------------------------------------------
    if "geography" not in segment:
        items.append(_breakdown_item(incident_id, window_df, "geography", source_base, window_end))

    # 7. Failure reason breakdown -----------------------------------------------
    reason_counts = window_failed["failure_reason"].value_counts().to_dict()
    items.append(
        _evidence_item(
            evidence_id=f"{incident_id}_ev_reasons",
            evidence_type="failure_reason_breakdown",
            source=source_base,
            data={"failure_reason_counts": reason_counts},
            relevance_score=0.95,
            timestamp=window_end,
        )
    )

    # 8. Historical daily trend (segment's failure rate for the 5 days before
    #    the window, so the agent can see whether this was already trending) --
    items.append(_historical_trend_item(incident_id, df, segment, window_start, source_base))

    return items


def _breakdown_item(
    incident_id: str, window_df: pd.DataFrame, column: str, source_base: str, window_end: str
) -> dict:
    grouped = (
        window_df.groupby(column)["status"]
        .agg(total="count", failed=lambda s: (s == STATUS_FAILED).sum())
        .reset_index()
    )
    grouped["failure_rate"] = (grouped["failed"] / grouped["total"]).round(4)
    grouped = grouped.sort_values("failed", ascending=False)
    data = grouped.to_dict(orient="records")
    return _evidence_item(
        evidence_id=f"{incident_id}_ev_breakdown_{column}",
        evidence_type=f"{column}_breakdown",
        source=source_base,
        data={"breakdown": data},
        relevance_score=0.85,
        timestamp=window_end,
    )


def _historical_trend_item(
    incident_id: str,
    df: pd.DataFrame,
    segment: dict,
    window_start: str,
    source_base: str,
    lookback_days: int = 5,
) -> dict:
    ts = pd.to_datetime(df["timestamp"], utc=True)
    segment_mask = resolve_segment_mask(df, segment)
    seg_df = df[segment_mask].copy()
    seg_ts = ts[segment_mask]

    start = pd.Timestamp(window_start)
    lookback_start = start - pd.Timedelta(f"{int(lookback_days)}D")
    prior_df = seg_df[(seg_ts >= lookback_start) & (seg_ts < start)]
    prior_ts = pd.to_datetime(prior_df["timestamp"], utc=True)

    daily = []
    if len(prior_df):
        day_bins = prior_ts.dt.floor("D")
        for day, group in prior_df.groupby(day_bins):
            total = len(group)
            failed = int((group["status"] == STATUS_FAILED).sum())
            daily.append(
                {
                    "date": day.date().isoformat(),
                    "transaction_count": total,
                    "failed_count": failed,
                    "failure_rate": round(failed / total, 4) if total else 0.0,
                }
            )

    return _evidence_item(
        evidence_id=f"{incident_id}_ev_trend",
        evidence_type="historical_daily_trend",
        source=f"{source_base.split(', window')[0]}, {lookback_days} days preceding window start",
        data={"lookback_days": lookback_days, "daily_failure_rates": daily},
        relevance_score=0.7,
        timestamp=window_start,
    )
