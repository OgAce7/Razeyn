# Synthetic data & incident-generation layer

This document covers the data layer only (`backend/app/data/`). Detection,
retrieval, the AI agent, recovery execution, and the dashboard are separate,
later build steps and are not covered here.

## What's implemented

| File | Purpose |
|---|---|
| `app/data/schema.py` | Field names, enums, and value pools (single source of truth) |
| `app/data/generate.py` | Seeded generator — builds the baseline dataset and injects incidents |
| `app/data/loader.py` | Stable read interface (`load_transactions`, `load_incidents`, `load_incidents_list`) |
| `app/data/synthetic/transactions.csv` | Generated payment event dataset (gitignored, regenerate locally) |
| `app/data/synthetic/incidents.json` | Generated ground-truth incident definitions (gitignored, regenerate locally) |

## Regenerating the dataset

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # if not already set up
pip install -r requirements.txt
python -m app.data.generate
```

This is fully deterministic — the same `SEED = 42` (in `generate.py`) always
produces the same dataset. Change `SEED`, `NUM_DAYS`, or `BASE_TXNS_PER_HOUR`
at the top of `generate.py` and rerun to get a different-sized dataset.

Running it prints a summary: total row count, status breakdown, overall
failure rate, and a per-incident line showing how many transactions each
incident touched and how much revenue it exposed — useful as a quick sanity
check after any tuning.

## Dataset size (current default parameters)

- **~9,000 transactions** across a 14-day period (well above the 1,000+ target).
- **~4.6% overall failure rate** at baseline, consistent with a realistic
  payment success rate before any incident is layered in.
- 5 injected incidents, 4 of them true positives and 1 explicitly benign
  (see below).

Exact counts vary slightly by machine/library version due to floating point,
but will be very close to this given the fixed seed. Row counts, per-incident
affected-transaction counts, and revenue-exposed figures are printed every
time you regenerate.

## Transaction schema

Each row in `transactions.csv`:

| Field | Type | Notes |
|---|---|---|
| `transaction_id` | str | Unique, e.g. `txn_001234` |
| `timestamp` | ISO 8601 UTC | When the payment event occurred |
| `customer_id` | str | e.g. `cust_00231`, drawn from a pool of 350 |
| `amount` | float | INR, shaped by `checkout_context` (subscriptions cluster near a fixed price, cart checkouts have a heavier tail) |
| `currency` | str | Always `INR` |
| `payment_method` | str | `UPI`, `CARD`, `NETBANKING`, `WALLET` |
| `institution` | str | Issuing bank / PSP, e.g. `HDFC Bank`, `ICICI Bank` — traffic is weighted so a couple of banks dominate, like real routing |
| `geography` | str | City, weighted toward major metros |
| `status` | str | `SUCCESS`, `FAILED`, `PENDING` |
| `failure_reason` | str | One of 7 reasons (see below), empty if not failed |
| `processing_latency_ms` | int | End-to-end latency; inflated during latency-related incidents |
| `retry_count` | int | Mostly 0; boosted during degradation incidents |
| `checkout_context` | str | `one_time_checkout`, `cart_checkout`, `subscription_renewal` |
| `is_incident_injected` | bool | True if an incident touched this row — a ground-truth helper column, safe to ignore |
| `incident_id` | str | Which incident touched this row, if any |

Failure reasons: `INSUFFICIENT_FUNDS`, `BANK_TIMEOUT`, `INVALID_OTP`,
`NETWORK_ERROR`, `RISK_DECLINE`, `ISSUER_DECLINE`, `GATEWAY_ERROR`. At
baseline these are weighted toward customer-side causes (insufficient
funds, OTP issues); incidents skew heavily toward infra-side reasons
(bank timeout, network error, gateway error) to make the degradation
pattern distinguishable from ordinary noise.

Baseline traffic itself is not uniform random — it follows a diurnal curve
(low overnight, two daytime peaks) and is ~15% higher on weekends, so a
detector has a believable "normal" shape to compare incidents against.

## Ground-truth incident schema

Each entry in `incidents.json` → `incidents[]`:

| Field | Type | Notes |
|---|---|---|
| `incident_id` | str | e.g. `inc_001` |
| `type` | str | One of the 5 pattern types below |
| `start_time` / `end_time` | ISO 8601 UTC | Incident window |
| `affected_segment` | dict | The filter used to select affected rows, e.g. `{"payment_method": "UPI", "institution": "HDFC Bank"}`. Empty dict = no segment filter (all transactions in the window) |
| `expected_failure_pattern` | str | Human-readable description of the signature a detector should find |
| `expected_severity` | str | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` / `NONE` |
| `affected_transaction_ids` | list[str] | Every transaction in scope (window + segment), not just the ones that failed |
| `transaction_count` | int | `len(affected_transaction_ids)` |
| `revenue_exposed` | float | Sum of `amount` for transactions that were escalated to `FAILED` because of this incident |
| `is_true_incident` | bool | `False` only for the benign fluctuation — lets evaluation code check for false positives |

`incidents.json` also carries top-level metadata: `seed`, `generated_at`,
`dataset_start`, `dataset_days`, `total_transactions`.

## Incident patterns

1. **`bank_specific_upi_degradation`** (`inc_001`) — UPI transactions routed
   through HDFC Bank spike to ~55% failure (mostly `BANK_TIMEOUT` /
   `NETWORK_ERROR`), latency ~2.4x, over a 5-hour window. All other
   banks/methods are untouched — the signature is narrow and specific.
2. **`payment_method_degradation`** (`inc_002`) — All WALLET transactions,
   regardless of bank or city, degrade to ~38% failure (mostly
   `GATEWAY_ERROR` / `RISK_DECLINE`) across a full business day.
3. **`latency_spike`** (`inc_003`) — No segment filter: processing latency
   roughly triples across every method/bank for a 2-hour window, producing
   a moderate, broad-based failure bump (`BANK_TIMEOUT`-dominant) without
   any single segment standing out.
4. **`geographic_concentration`** (`inc_004`) — Chennai transactions
   (across multiple methods/banks) degrade to ~32% failure
   (`NETWORK_ERROR` / `ISSUER_DECLINE`), simulating a regional
   network/carrier issue, over a 6-hour window.
5. **`benign_traffic_fluctuation`** (`inc_005`) — Deliberately **not** a
   real incident. Adds ~3x the normal transaction volume for a 4-hour
   window (a flash-sale-style demand spike) but keeps the failure rate at
   baseline. `is_true_incident` is `False` and `revenue_exposed` is `0.0`.
   This exists specifically to test whether a detector wrongly flags pure
   volume increases as degradation.

## Design choices worth knowing about

- **Injection over generation-from-scratch for degradation incidents.**
  Incidents 1–4 don't add new rows; they select existing baseline rows
  that fall in the time window + segment and escalate a fraction of them
  to `FAILED`. This keeps a realistic mix of pre-existing baseline noise
  and a genuinely elevated cluster, rather than an artificially clean
  signal.
- **The benign case is structurally different on purpose** — it adds new
  rows instead of altering existing ones, because the thing being tested
  is a volume spike, not a failure-rate change.
- **Institution/geography/payment-method traffic shares are weighted, not
  uniform.** A uniform distribution across 8 banks × 4 methods makes any
  single-bank-and-method incident too sparse to be statistically visible.
  Weighting mirrors real-world traffic concentration (a couple of large
  banks and UPI dominate) and keeps affected-transaction counts
  (currently ~20–65 per incident) large enough for a detector to work
  with, while still leaving the affected segment a minority of total
  traffic.
- **Validated signal strength:** for `inc_001`, transactions in-window for
  the UPI+HDFC segment fail at **~70%**, versus **~5%** for the same
  segment outside the window — a clear, detectable spike. The benign
  window's failure rate stays at baseline (~4.8%) despite a 3x volume
  increase.

## Using the data from other code

```python
from app.data.loader import load_transactions, load_incidents_list

df = load_transactions()          # pandas DataFrame, ready to filter/aggregate
incidents = load_incidents_list()  # list[dict], ground truth for evaluation
```

Both raise a clear `FileNotFoundError` telling you to run
`python -m app.data.generate` if the dataset hasn't been built yet. No
other part of the app should read the CSV/JSON directly — go through the
loader so the on-disk format can change without breaking callers.
