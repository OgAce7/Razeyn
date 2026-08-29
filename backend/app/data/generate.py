"""
Synthetic payment data + ground-truth incident generator.

Produces two artifacts under app/data/synthetic/:
  - transactions.csv   the payment event dataset
  - incidents.json     ground-truth incident definitions for evaluation

Run directly to (re)generate both from scratch:
    python -m app.data.generate
or:
    python app/data/generate.py

Design notes
------------
1. A realistic baseline is generated first: transaction volume follows a
   diurnal + weekday pattern, amounts/latency are drawn from distributions
   appropriate to their context, and a small, believable baseline failure
   rate (~4%) is spread across failure reasons.
2. Each incident is then injected by selecting the transactions that fall
   within its time window AND match its affected segment (e.g. UPI +
   a specific bank), and escalating a large fraction of them to FAILED
   with a reason consistent with the incident's story. This means the
   dataset always contains a plausible mix of pre-existing baseline
   failures plus a clearly elevated failure cluster during the incident.
3. The "benign traffic fluctuation" pattern is different by design: it
   ADDS extra normal-behaving transactions (a volume spike, e.g. a flash
   sale) without touching the failure rate. It is included specifically
   so a detector that flags on volume alone produces a false positive —
   this is the ground truth's "should NOT be classified as an incident"
   case, marked `is_true_incident: false`.
4. Everything is driven off a single seeded RNG so the dataset is fully
   reproducible.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.data.schema import (
    CHECKOUT_CONTEXTS,
    GEOGRAPHIES,
    INSTITUTIONS,
    PAYMENT_METHODS,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCESS,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

SEED = 42
NUM_CUSTOMERS = 350
START_DATE = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
NUM_DAYS = 14
BASE_TXNS_PER_HOUR = 30.0  # mean baseline volume/hour before diurnal/weekday shaping

# Institution share of traffic — a couple of large banks dominate, like real
# payment routing, so a bank-specific incident has enough affected volume
# to be statistically visible rather than a handful of stray transactions.
INSTITUTION_WEIGHTS = [0.28, 0.19, 0.15, 0.12, 0.09, 0.07, 0.06, 0.04]

BASELINE_FAILURE_RATE = 0.04
BASELINE_FAILURE_REASON_WEIGHTS = {
    "INSUFFICIENT_FUNDS": 0.30,
    "INVALID_OTP": 0.20,
    "RISK_DECLINE": 0.15,
    "ISSUER_DECLINE": 0.15,
    "BANK_TIMEOUT": 0.10,
    "NETWORK_ERROR": 0.05,
    "GATEWAY_ERROR": 0.05,
}

OUTPUT_DIR = Path(__file__).parent / "synthetic"
TRANSACTIONS_CSV = OUTPUT_DIR / "transactions.csv"
INCIDENTS_JSON = OUTPUT_DIR / "incidents.json"


# --------------------------------------------------------------------------
# Helpers: baseline shaping
# --------------------------------------------------------------------------

def diurnal_multiplier(hour: int) -> float:
    """Traffic shape across a 24h day: low overnight, peaks late morning/evening."""
    peaks = [11, 20]
    base = 0.25
    boost = sum(np.exp(-((hour - p) ** 2) / (2 * 3.0 ** 2)) for p in peaks)
    return base + boost


def weekday_multiplier(weekday: int) -> float:
    """0=Mon ... 6=Sun. Slightly higher on weekends (more discretionary checkout)."""
    return 1.15 if weekday >= 5 else 1.0


def sample_amount(context: str, rng: np.random.Generator) -> float:
    if context == "subscription_renewal":
        base = rng.normal(499, 120)
    elif context == "cart_checkout":
        base = rng.lognormal(mean=6.8, sigma=0.6)  # heavier tail, bigger baskets
    else:  # one_time_checkout
        base = rng.lognormal(mean=6.2, sigma=0.5)
    return round(float(max(29.0, base)), 2)


def sample_latency_ms(rng: np.random.Generator, mean: float = 850.0, sd: float = 220.0) -> int:
    return int(max(120, rng.normal(mean, sd)))


def pick_failure_reason(rng: np.random.Generator, weights: dict[str, float] | None = None) -> str:
    weights = weights or BASELINE_FAILURE_REASON_WEIGHTS
    reasons = list(weights.keys())
    probs = np.array(list(weights.values()))
    probs = probs / probs.sum()
    return str(rng.choice(reasons, p=probs))


# --------------------------------------------------------------------------
# Baseline generation
# --------------------------------------------------------------------------

def generate_customers(n: int, rng: random.Random) -> list[str]:
    return [f"cust_{i:05d}" for i in range(1, n + 1)]


def generate_baseline(rng_np: np.random.Generator, rng_py: random.Random) -> pd.DataFrame:
    customers = generate_customers(NUM_CUSTOMERS, rng_py)
    rows = []
    txn_counter = 1

    total_hours = NUM_DAYS * 24
    for h in range(total_hours):
        ts_hour = START_DATE + timedelta(hours=h)
        mult = diurnal_multiplier(ts_hour.hour) * weekday_multiplier(ts_hour.weekday())
        expected = BASE_TXNS_PER_HOUR * mult
        n_txns = rng_np.poisson(lam=expected)

        for _ in range(n_txns):
            minute_offset = rng_py.uniform(0, 3599)
            ts = ts_hour + timedelta(seconds=minute_offset)

            method = rng_py.choices(PAYMENT_METHODS, weights=[0.40, 0.30, 0.15, 0.15])[0]
            institution = rng_py.choices(INSTITUTIONS, weights=INSTITUTION_WEIGHTS)[0]
            geography = rng_py.choices(
                GEOGRAPHIES,
                weights=[0.17, 0.15, 0.14, 0.15, 0.10, 0.09, 0.08, 0.06, 0.04, 0.02],
            )[0]
            context = rng_py.choices(CHECKOUT_CONTEXTS, weights=[0.45, 0.35, 0.20])[0]
            customer = rng_py.choice(customers)
            amount = sample_amount(context, rng_np)
            latency = sample_latency_ms(rng_np)
            retry_count = 0 if rng_py.random() > 0.06 else rng_py.choice([1, 1, 2])

            is_failure = rng_py.random() < BASELINE_FAILURE_RATE
            if is_failure:
                status = STATUS_FAILED
                reason = pick_failure_reason(rng_np)
            elif rng_py.random() < 0.01:
                status = STATUS_PENDING
                reason = ""
            else:
                status = STATUS_SUCCESS
                reason = ""

            rows.append(
                {
                    "transaction_id": f"txn_{txn_counter:06d}",
                    "timestamp": ts.isoformat(),
                    "customer_id": customer,
                    "amount": amount,
                    "currency": "INR",
                    "payment_method": method,
                    "institution": institution,
                    "geography": geography,
                    "status": status,
                    "failure_reason": reason,
                    "processing_latency_ms": latency,
                    "retry_count": retry_count,
                    "checkout_context": context,
                    "is_incident_injected": False,
                    "incident_id": "",
                }
            )
            txn_counter += 1

    df = pd.DataFrame(rows)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# --------------------------------------------------------------------------
# Incident injection
# --------------------------------------------------------------------------

@dataclass
class IncidentDef:
    incident_id: str
    type: str
    start_time: datetime
    end_time: datetime
    segment_filter: dict = field(default_factory=dict)  # column -> value
    target_failure_rate: float = 0.4
    failure_reason_weights: dict = field(default_factory=dict)
    latency_multiplier: float = 1.0
    retry_boost: bool = True
    expected_failure_pattern: str = ""
    expected_severity: str = "HIGH"


def _in_window(df: pd.DataFrame, start: datetime, end: datetime) -> pd.Series:
    ts = pd.to_datetime(df["timestamp"], utc=True)
    return (ts >= start) & (ts < end)


def _matches_segment(df: pd.DataFrame, segment_filter: dict) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for col, val in segment_filter.items():
        mask &= df[col] == val
    return mask


def apply_degradation_incident(
    df: pd.DataFrame, inc: IncidentDef, rng_py: random.Random, rng_np: np.random.Generator
) -> dict:
    """Escalate a fraction of matching transactions to FAILED. Returns ground truth."""
    window_mask = _in_window(df, inc.start_time, inc.end_time)
    segment_mask = _matches_segment(df, inc.segment_filter)
    eligible = df.index[window_mask & segment_mask].tolist()

    affected_ids = df.loc[eligible, "transaction_id"].tolist()
    revenue_exposed = 0.0

    for idx in eligible:
        df.at[idx, "is_incident_injected"] = True
        df.at[idx, "incident_id"] = inc.incident_id

        if rng_py.random() < inc.target_failure_rate:
            df.at[idx, "status"] = STATUS_FAILED
            df.at[idx, "failure_reason"] = pick_failure_reason(
                rng_np, inc.failure_reason_weights or None
            )
            revenue_exposed += float(df.at[idx, "amount"])
            if inc.retry_boost:
                df.at[idx, "retry_count"] = rng_py.choice([1, 2, 2, 3])

        base_latency = df.at[idx, "processing_latency_ms"]
        df.at[idx, "processing_latency_ms"] = int(base_latency * inc.latency_multiplier)

    ground_truth = {
        "incident_id": inc.incident_id,
        "type": inc.type,
        "start_time": inc.start_time.isoformat(),
        "end_time": inc.end_time.isoformat(),
        "affected_segment": inc.segment_filter,
        "expected_failure_pattern": inc.expected_failure_pattern,
        "expected_severity": inc.expected_severity,
        "affected_transaction_ids": affected_ids,
        "transaction_count": len(affected_ids),
        "revenue_exposed": round(revenue_exposed, 2),
        "is_true_incident": True,
    }
    return ground_truth


def apply_benign_fluctuation(
    df: pd.DataFrame,
    inc: IncidentDef,
    customers: list[str],
    rng_py: random.Random,
    rng_np: np.random.Generator,
    txn_counter_start: int,
    volume_multiplier: float = 3.0,
) -> tuple[pd.DataFrame, dict, int]:
    """Add extra, normally-behaving transactions during a window (e.g. a flash sale).

    This should NOT look like an incident: failure rate stays at baseline,
    only volume rises. Returns (updated_df, ground_truth, next_txn_counter).
    """
    hours = int((inc.end_time - inc.start_time).total_seconds() // 3600) or 1
    new_rows = []
    counter = txn_counter_start

    for h in range(hours):
        ts_hour = inc.start_time + timedelta(hours=h)
        mult = diurnal_multiplier(ts_hour.hour) * weekday_multiplier(ts_hour.weekday())
        expected_extra = BASE_TXNS_PER_HOUR * mult * (volume_multiplier - 1.0)
        n_extra = rng_np.poisson(lam=max(expected_extra, 1.0))

        for _ in range(n_extra):
            ts = ts_hour + timedelta(seconds=rng_py.uniform(0, 3599))
            method = rng_py.choices(PAYMENT_METHODS, weights=[0.40, 0.30, 0.15, 0.15])[0]
            institution = rng_py.choices(INSTITUTIONS, weights=INSTITUTION_WEIGHTS)[0]
            geography = rng_py.choices(
                GEOGRAPHIES,
                weights=[0.17, 0.15, 0.14, 0.15, 0.10, 0.09, 0.08, 0.06, 0.04, 0.02],
            )[0]
            context = "cart_checkout"  # flash-sale style traffic
            customer = rng_py.choice(customers)
            amount = sample_amount(context, rng_np)
            latency = sample_latency_ms(rng_np)

            is_failure = rng_py.random() < BASELINE_FAILURE_RATE  # unchanged rate
            if is_failure:
                status = STATUS_FAILED
                reason = pick_failure_reason(rng_np)
            else:
                status = STATUS_SUCCESS
                reason = ""

            new_rows.append(
                {
                    "transaction_id": f"txn_{counter:06d}",
                    "timestamp": ts.isoformat(),
                    "customer_id": customer,
                    "amount": amount,
                    "currency": "INR",
                    "payment_method": method,
                    "institution": institution,
                    "geography": geography,
                    "status": status,
                    "failure_reason": reason,
                    "processing_latency_ms": latency,
                    "retry_count": 0,
                    "checkout_context": context,
                    "is_incident_injected": True,
                    "incident_id": inc.incident_id,
                }
            )
            counter += 1

    new_df = pd.DataFrame(new_rows)
    combined = pd.concat([df, new_df], ignore_index=True)

    ground_truth = {
        "incident_id": inc.incident_id,
        "type": inc.type,
        "start_time": inc.start_time.isoformat(),
        "end_time": inc.end_time.isoformat(),
        "affected_segment": inc.segment_filter,
        "expected_failure_pattern": inc.expected_failure_pattern,
        "expected_severity": inc.expected_severity,
        "affected_transaction_ids": new_df["transaction_id"].tolist(),
        "transaction_count": len(new_df),
        "revenue_exposed": 0.0,
        "is_true_incident": False,
    }
    return combined, ground_truth, counter


# --------------------------------------------------------------------------
# Incident scenario definitions
# --------------------------------------------------------------------------

def build_incident_defs() -> list[IncidentDef]:
    d0 = START_DATE
    return [
        IncidentDef(
            incident_id="inc_001",
            type="bank_specific_upi_degradation",
            start_time=d0 + timedelta(days=2, hours=13),
            end_time=d0 + timedelta(days=2, hours=18),
            segment_filter={"payment_method": "UPI", "institution": "HDFC Bank"},
            target_failure_rate=0.55,
            failure_reason_weights={"BANK_TIMEOUT": 0.7, "NETWORK_ERROR": 0.3},
            latency_multiplier=2.4,
            expected_failure_pattern=(
                "Sharp rise in BANK_TIMEOUT/NETWORK_ERROR for UPI transactions "
                "routed through HDFC Bank only; other banks/methods unaffected."
            ),
            expected_severity="HIGH",
        ),
        IncidentDef(
            incident_id="inc_002",
            type="payment_method_degradation",
            start_time=d0 + timedelta(days=5, hours=9),
            end_time=d0 + timedelta(days=5, hours=17),
            segment_filter={"payment_method": "WALLET"},
            target_failure_rate=0.38,
            failure_reason_weights={"GATEWAY_ERROR": 0.65, "RISK_DECLINE": 0.35},
            latency_multiplier=1.6,
            expected_failure_pattern=(
                "Elevated GATEWAY_ERROR/RISK_DECLINE for all WALLET transactions "
                "across every bank and geography for a full business day."
            ),
            expected_severity="MEDIUM",
        ),
        IncidentDef(
            incident_id="inc_003",
            type="latency_spike",
            start_time=d0 + timedelta(days=7, hours=19),
            end_time=d0 + timedelta(days=7, hours=21),
            segment_filter={},
            target_failure_rate=0.22,
            failure_reason_weights={"BANK_TIMEOUT": 0.85, "NETWORK_ERROR": 0.15},
            latency_multiplier=3.2,
            expected_failure_pattern=(
                "Processing latency roughly triples across ALL payment methods "
                "and banks for a 2-hour window, driving a moderate timeout-based "
                "failure bump with no single segment standing out."
            ),
            expected_severity="MEDIUM",
        ),
        IncidentDef(
            incident_id="inc_004",
            type="geographic_concentration",
            start_time=d0 + timedelta(days=10, hours=11),
            end_time=d0 + timedelta(days=10, hours=17),
            segment_filter={"geography": "Chennai"},
            target_failure_rate=0.32,
            failure_reason_weights={"NETWORK_ERROR": 0.6, "ISSUER_DECLINE": 0.4},
            latency_multiplier=1.5,
            expected_failure_pattern=(
                "Failures concentrate in Chennai across multiple payment methods "
                "and banks (regional network/carrier issue), other cities normal."
            ),
            expected_severity="HIGH",
        ),
        IncidentDef(
            incident_id="inc_005",
            type="benign_traffic_fluctuation",
            start_time=d0 + timedelta(days=12, hours=18),
            end_time=d0 + timedelta(days=12, hours=22),
            segment_filter={},
            expected_failure_pattern=(
                "Transaction volume roughly triples (flash-sale-like demand spike) "
                "but failure rate stays at baseline (~4%) — a volume-only detector "
                "would false-positive here; a good detector should NOT flag this."
            ),
            expected_severity="NONE",
        ),
    ]


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def generate_dataset() -> tuple[pd.DataFrame, list[dict]]:
    random.seed(SEED)
    rng_py = random.Random(SEED)
    rng_np = np.random.default_rng(SEED)

    customers = generate_customers(NUM_CUSTOMERS, rng_py)
    df = generate_baseline(rng_np, rng_py)

    incident_defs = build_incident_defs()
    ground_truths = []
    next_counter = int(df["transaction_id"].str.replace("txn_", "").astype(int).max()) + 1

    for inc in incident_defs:
        if inc.type == "benign_traffic_fluctuation":
            df, gt, next_counter = apply_benign_fluctuation(
                df, inc, customers, rng_py, rng_np, next_counter
            )
        else:
            gt = apply_degradation_incident(df, inc, rng_py, rng_np)
        ground_truths.append(gt)

    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df, ground_truths


def save_dataset(df: pd.DataFrame, ground_truths: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(TRANSACTIONS_CSV, index=False)

    payload = {
        "seed": SEED,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_start": START_DATE.isoformat(),
        "dataset_days": NUM_DAYS,
        "total_transactions": len(df),
        "incidents": ground_truths,
    }
    with open(INCIDENTS_JSON, "w") as f:
        json.dump(payload, f, indent=2)


def main() -> None:
    df, ground_truths = generate_dataset()
    save_dataset(df, ground_truths)

    print(f"Generated {len(df)} transactions -> {TRANSACTIONS_CSV}")
    print(f"Generated {len(ground_truths)} incidents -> {INCIDENTS_JSON}")
    print("\nSummary:")
    print(df["status"].value_counts().to_string())
    print(f"\nOverall failure rate: {(df['status'] == STATUS_FAILED).mean():.2%}")
    for gt in ground_truths:
        print(
            f"  [{gt['incident_id']}] {gt['type']}: "
            f"{gt['transaction_count']} txns, "
            f"revenue_exposed={gt['revenue_exposed']}, "
            f"true_incident={gt['is_true_incident']}"
        )


if __name__ == "__main__":
    main()
