# Payment-degradation detection engine

This document covers the detection layer only (`backend/app/detection/`).
It consumes the synthetic dataset (`docs/data_layer.md`) and produces
candidate incidents with evidence. No LLM is used anywhere in this module
— it's deterministic Python/pandas, independently testable, and the
output is deliberately observational rather than causal.

## Files

| File | Purpose |
|---|---|
| `app/detection/config.py` | All tunable thresholds, in one place, with rationale in comments |
| `app/detection/stats.py` | Pure statistics helpers (two-proportion z-test, severity/confidence mapping, the candidate gate) — no pandas, independently unit-testable |
| `app/detection/detector.py` | The engine: hourly binning, rolling-window vs. baseline comparison, run-clustering, per-dimension drivers |
| `app/detection/run.py` | CLI — runs the detector against the generated dataset, writes `app/data/synthetic/candidate_incidents.json` |
| `backend/tests/test_stats.py` | Unit tests for the pure statistics functions |
| `backend/tests/test_detector.py` | Scenario tests for the full engine (see below) |

## Running it

```bash
cd backend
source .venv/bin/activate   # after the usual pip install -r requirements.txt
python -m app.data.generate   # if you haven't already generated the dataset
python -m app.detection.run
```

This prints a one-line summary per candidate and writes the full structured
output to `app/data/synthetic/candidate_incidents.json`.

Run the tests with:

```bash
cd backend
python -m pytest tests/ -v
```

## Methodology

**Approach: leave-window-out baseline comparison + two-proportion z-test,
evaluated per segment across several dimensions.** This is the simplest
technique in the brief's list that still supports statistical
significance testing, and it's fully explainable — every number in the
output can be traced back to a count of transactions.

For each dimension/segment (e.g. `payment_method = WALLET`, or
`institution = HDFC Bank`, or the unsegmented "all" pass):

1. **Bucket into hourly bins.** Transaction counts and failure counts per
   hour, for that segment only.
2. **Slide a window across time** (`window_hours`, default 6, stepped
   hourly). At each position, the window's failure rate is compared
   against a baseline built from *all other transactions in that
   segment's history* (i.e. total history minus the window) — not a
   separate fixed reference period. This keeps the method simple (no
   separate "baseline period" configuration needed) while still adapting
   to each segment's actual historical rate.
3. **Test significance** with a two-proportion z-test (pooled variance) —
   the standard test for "are these two observed proportions different
   beyond what sampling noise would explain."
4. **Gate on four conditions simultaneously** (see `stats.is_candidate`):
   - Minimum transaction count in both the window and the baseline
     (`min_window_n`, `min_baseline_n`) — protects against unstable rate
     estimates from tiny samples.
   - z-score above a threshold (`z_threshold`) — statistical significance.
   - Relative increase over baseline above a floor
     (`min_relative_change_pct`) — protects against a statistically
     "real" but practically trivial move (e.g. 0.5% → 0.8%).
   - Absolute failure rate above a floor (`min_absolute_rate`) — protects
     against relative-change math blowing up when the baseline itself is
     very close to zero.
5. **Cluster consecutive flagged hours into one run** per segment, so an
   ongoing incident produces one candidate instead of a new one every
   hour it stays anomalous. The run's final stats are recomputed directly
   from the raw transactions over its exact time span (not the rolling
   approximation), and re-checked against the same gate before being kept.

### Dimensions evaluated

| Dimension | What's compared | Why |
|---|---|---|
| `payment_method` | Each method's failure rate vs. its own history | Catches method-wide degradation |
| `institution` | Each bank's failure rate vs. its own history | Catches bank-wide degradation |
| `geography` | Each city's failure rate vs. its own history | Catches regional degradation |
| `payment_method+institution` | Each method×bank pairing | Catches narrower incidents (e.g. UPI specifically through one bank) that a single-column view would dilute |
| `failure_reason` | Each reason's share of *all* transactions vs. its own history | Catches a shift toward a specific failure mode even if the overall failure rate move is modest |
| `transaction_value_bucket` | Failure rate within LOW/MID/HIGH tertiles of transaction amount | Surfaces value-correlated degradation |
| `all` (no segment) | Failure rate across every transaction | Catches broad, non-segment-specific issues (e.g. a latency spike hitting every method/bank at once) |

The pairing and failure-reason dimensions use **lower sample-size floors**
(`fine_min_window_n` / `fine_min_baseline_n`) than the single-column
dimensions, because they necessarily split traffic into smaller
populations — a genuine narrow incident (e.g. one method through one
bank) will never accumulate as many transactions per window as a broad
one. Noise resistance still comes from the z-score/relative-change/
absolute-rate gates, not just the sample floor, so this doesn't reopen
the false-positive problem a low floor caused elsewhere (see "Tuning
notes" below).

### What counts as "meaningful," concretely

With the default thresholds (`window_hours=6`, `min_window_n=30`,
`min_baseline_n=120`, `z_threshold=5.0`, `min_relative_change_pct=60%`,
`min_absolute_rate=12%`), a segment must show **at least a 60% relative
increase in failure rate, reaching at least 12% absolute, across at least
30 transactions, with a z-score of 5+** (roughly equivalent to
p < 10⁻⁶ for a single test) before it's reported. This is intentionally
conservative for a first pass.

### Output schema

Each candidate is a dict with:

```
incident_id                str    e.g. "cand_00001"
detection_timestamp        str    ISO 8601, when the detector ran
affected_dimension         str    e.g. "payment_method", "institution",
                                   "payment_method+institution", "geography",
                                   "failure_reason", "transaction_value_bucket", "all"
affected_segment           dict   e.g. {"payment_method": "WALLET"}, {} for "all"
window_start / window_end  str    ISO 8601, the flagged period
baseline_success_rate      float  1 - baseline failure rate
current_success_rate       float  1 - window failure rate
degradation_percentage     float  relative increase in failure rate, % (null if baseline was ~0%)
transaction_count          int    transactions in the window
revenue_affected            float  sum of `amount` for FAILED transactions in the window
severity                   str    LOW / MEDIUM / HIGH / CRITICAL
confidence_score           float  0-1, monotonic transform of the z-score
observation                str    plain-language, non-causal description
supporting_statistics      dict   z_score, baseline/window counts, median
                                   latency baseline vs. window, failure
                                   reason breakdown within the window
```

`revenue_affected` is the actual observed FAILED-transaction revenue
inside the flagged window — not the same figure as the synthetic
dataset's ground-truth `revenue_exposed` (which only counts the failures
the generator specifically caused). They should be close but aren't
required to match exactly, since the detector doesn't have access to
which failures were "caused" by the incident vs. ordinary baseline noise
that happened to fall in the same window.

### Language: observation, not diagnosis

Every `observation` string is built from a fixed template — it states the
segment, the rate change, the transaction count, and the z-score, and
nothing else. It never names a cause (no "outage," "bug," "issue with
Bank X's servers"). Example actual output from this engine:

> "Transactions where payment_method=WALLET showed a failure rate of
> 37.3% between 2026-08-15T08:00:00+00:00 and 2026-08-15T17:00:00+00:00,
> versus a baseline of 4.1% (+634.1% relative change), across 73
> transactions (z=10.95)."

Diagnosing *why* is explicitly out of scope for this module (that's the
AI Investigation Agent's job, in a later build step, working from this
module's evidence).

## Validation against the synthetic dataset's ground truth

Running `python -m app.detection.run` against the generated dataset
(`docs/data_layer.md`) currently produces **7 candidates that collectively
cover all 4 true injected incidents**, and **zero candidates overlapping
the benign traffic-fluctuation window** (the one that should *not* be
flagged):

| Candidate | Dimension | Segment | Overlaps ground truth |
|---|---|---|---|
| cand_00001 | payment_method | UPI | inc_001 (bank-specific UPI degradation) |
| cand_00003 | institution | HDFC Bank | inc_001 |
| cand_00005 | payment_method+institution | UPI, HDFC Bank | inc_001 |
| cand_00002 | payment_method | WALLET | inc_002 (payment-method degradation) |
| cand_00006 | payment_method+institution | WALLET, HDFC Bank | inc_002 |
| cand_00007 | all | — | inc_003 (latency spike) |
| cand_00004 | geography | Chennai | inc_004 (geographic concentration) |

Several true incidents are (correctly) picked up from more than one
angle — e.g. inc_001 shows up as `payment_method=UPI`,
`institution=HDFC Bank`, *and* the narrower `UPI+HDFC Bank` pairing. This
is expected: a single real event legitimately has an observable signature
at multiple levels of granularity, and this module doesn't attempt to
merge or deduplicate across dimensions (that kind of correlation is left
to later stages, since it starts to shade into "diagnosis").

## Tests

`backend/tests/test_detector.py` builds small, self-contained synthetic
datasets (independent of the large generated dataset) and covers:

1. **Genuine degradation** — a clear, sustained failure-rate jump in one
   segment is detected; the unaffected segment is not; the candidate's
   fields (rates, revenue, degradation %) are consistent with the
   injected pattern.
2. **No degradation** — pure baseline noise, no injected pattern. Checked
   across 8 independent random seeds (documents the method's small
   residual false-positive rate from scanning many overlapping windows,
   rather than asserting an unrealistic zero).
3. **Insufficient sample size** — a real-looking failure-rate jump in a
   dataset too small/short to clear the sample-size floors is correctly
   suppressed.
4. **Temporary noise** — a brief (1-hour) blip that isn't sustained or
   large enough to clear the gates is not flagged.
5. **Multiple simultaneous segments** — two different payment methods
   degrade at the same time for different reasons; both are detected
   independently with distinct incident IDs and reason breakdowns, and
   the third, unaffected method is not flagged.

`backend/tests/test_stats.py` unit-tests the pure statistics helpers
(rate/relative-change/z-score math, severity and confidence mapping, and
the combined candidate gate) in isolation.

Run everything with `cd backend && python -m pytest tests/ -v` — 17 tests,
all passing.

## Tuning notes (why the thresholds are what they are)

The first version of this engine used much looser thresholds
(`min_window_n=15`, `z_threshold=3.0`, `min_relative_change_pct=20%`) and
produced **35 candidates** against the same dataset — mostly noise, because
at a ~4% baseline failure rate, small windows are dominated by sampling
variance (one extra failure in a 15-transaction window already looks like
a "125% increase"). Tightening the sample-size floors, raising the
z-threshold to account for scanning many overlapping windows/dimensions
per run (a multiple-comparisons setting), and requiring a large relative
*and* absolute change together brought this down to a small, precise set
that maps cleanly onto the ground truth. This trade-off — and the
resulting thresholds — are recorded here so they can be revisited if the
downstream investigation agent needs a different sensitivity/precision
balance.
