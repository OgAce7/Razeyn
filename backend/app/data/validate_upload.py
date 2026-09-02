"""
Validation for uploaded transaction CSVs.

Goal: never let a malformed upload reach the detection/retrieval/agent
pipeline and crash it with a confusing pandas/numpy traceback. Every
failure mode here is caught and turned into a structured, human-readable
error the API can hand back as a 400/422 response -- see
app/api/datasets.py.

Validation runs in cheapest-first layers, matching the plan discussed
before implementation:
  1. Structural -- required columns present at all.
  2. Type/parse -- timestamp parses as a real datetime, amount/latency/
     retry_count are numeric, status/payment_method/etc. are strings.
  3. Domain/enum -- values fall within the sets app/data/schema.py
     defines (PAYMENT_METHODS, STATUSES, ...). Rows failing this are
     reported, not silently dropped.
  4. Size limits -- row/byte ceilings appropriate for a live demo (the
     detection/retrieval layers were built and tuned against a dataset
     in the tens-of-thousands-of-rows range, not arbitrary scale).
  5. Empty-after-filtering -- refuse to hand an empty frame to detection,
     which would either produce a meaningless zero-candidate result or
     throw deep inside pandas on empty-series operations.

Only the columns detection.py / retrieval/structured.py actually read
(see their source) are REQUIRED: timestamp, status, failure_reason,
processing_latency_ms, amount, payment_method, institution, geography,
plus transaction_id/customer_id (needed downstream by the policy engine
and executor -- every eligible transaction needs a stable id, and
customer-facing actions need a customer_id) and currency/checkout_context
(present in every real evidence/schema reference even though no pipeline
code currently branches on their value). is_incident_injected and
incident_id are intentionally NOT required -- those are synthetic-
dataset-only ground-truth helper columns (see app/data/schema.py); a
real uploaded CSV from a user would never have them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import pandas as pd

from app.data.schema import (
    CHECKOUT_CONTEXTS,
    FAILURE_REASONS,
    GEOGRAPHIES,
    INSTITUTIONS,
    PAYMENT_METHODS,
    STATUSES,
)

REQUIRED_COLUMNS = [
    "transaction_id",
    "timestamp",
    "customer_id",
    "amount",
    "currency",
    "payment_method",
    "institution",
    "geography",
    "status",
    "failure_reason",
    "processing_latency_ms",
    "retry_count",
    "checkout_context",
]

# Columns detection/retrieval/policy code actually branches on the VALUE
# of -- these get full enum validation. transaction_id/customer_id are
# free-form ids (validated for presence/uniqueness, not against a fixed
# set); currency is checked separately (single allowed value, this
# dataset's whole design assumes INR throughout the pipeline's monetary
# math). checkout_context is accepted from any of CHECKOUT_CONTEXTS but
# nothing downstream currently depends on it being exactly right, so a
# mismatch there is reported as a warning-level issue, not fatal.
ENUM_COLUMNS = {
    "payment_method": set(PAYMENT_METHODS),
    "institution": set(INSTITUTIONS),
    "geography": set(GEOGRAPHIES),
    "status": set(STATUSES),
}

MAX_ROWS = 50_000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
MAX_REPORTED_ROW_ERRORS = 20


@dataclass
class ValidationIssue:
    """One reportable problem. `row_numbers` uses 1-based CSV data-row
    numbering (excluding the header), truncated to MAX_REPORTED_ROW_ERRORS
    so one badly malformed file doesn't return thousands of lines of
    errors -- `total_affected_rows` still reports the true count."""

    code: str
    message: str
    row_numbers: list[int] = field(default_factory=list)
    total_affected_rows: int = 0

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.row_numbers:
            d["sample_row_numbers"] = self.row_numbers
            d["total_affected_rows"] = self.total_affected_rows
        return d


class DatasetValidationError(Exception):
    """Raised for validation failures severe enough to refuse the
    upload outright (structural problems, or zero valid rows remain).
    Carries the full list of ValidationIssues that led to rejection."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(i.message for i in issues))

    def to_dict(self) -> dict:
        return {"errors": [i.to_dict() for i in self.issues]}


@dataclass
class ValidationResult:
    """A successfully validated (and possibly row-filtered) dataset,
    plus any non-fatal warnings about rows that were dropped along the
    way (e.g. individual rows with an out-of-range enum value)."""

    transactions_df: pd.DataFrame
    warnings: list[ValidationIssue]
    rows_read: int
    rows_valid: int
    rows_dropped: int

    def to_summary_dict(self) -> dict:
        return {
            "rows_read": self.rows_read,
            "rows_valid": self.rows_valid,
            "rows_dropped": self.rows_dropped,
            "warnings": [w.to_dict() for w in self.warnings],
        }


def validate_upload_bytes(raw_bytes: bytes, filename: str | None = None) -> ValidationResult:
    """Entry point: validate raw uploaded file bytes as a transactions
    CSV. Raises DatasetValidationError for anything that isn't a usable
    dataset at all; returns a ValidationResult (with `warnings` for any
    rows that had to be dropped) otherwise. Never raises a raw
    pandas/numpy exception -- every parse failure is caught and
    translated into a DatasetValidationError.
    """
    if not raw_bytes:
        raise DatasetValidationError(
            [ValidationIssue("empty_file", "The uploaded file is empty.")]
        )

    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise DatasetValidationError(
            [
                ValidationIssue(
                    "file_too_large",
                    f"File is {len(raw_bytes) / (1024*1024):.1f}MB, which exceeds the "
                    f"{MAX_UPLOAD_BYTES // (1024*1024)}MB limit for this demo.",
                )
            ]
        )

    try:
        df = pd.read_csv(BytesIO(raw_bytes))
    except pd.errors.EmptyDataError:
        raise DatasetValidationError(
            [ValidationIssue("empty_file", "The uploaded file has no columns/data to parse.")]
        )
    except pd.errors.ParserError as e:
        raise DatasetValidationError(
            [ValidationIssue("unparseable_csv", f"Could not parse this file as CSV: {e}")]
        )
    except UnicodeDecodeError:
        raise DatasetValidationError(
            [
                ValidationIssue(
                    "bad_encoding",
                    "Could not decode this file as text -- is it actually a CSV "
                    "(not an .xlsx or binary file renamed to .csv)?",
                )
            ]
        )
    except Exception as e:  # pragma: no cover - defensive catch-all, see module docstring
        raise DatasetValidationError(
            [ValidationIssue("unreadable_file", f"Could not read this file: {e}")]
        )

    return validate_dataframe(df)


def validate_dataframe(df: pd.DataFrame) -> ValidationResult:
    """Validate an already-parsed DataFrame. Split out from
    validate_upload_bytes so tests (and any future non-CSV upload path)
    can exercise validation without needing raw CSV bytes.
    """
    rows_read = len(df)

    # -- 1. Structural: required columns present -----------------------------
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DatasetValidationError(
            [
                ValidationIssue(
                    "missing_columns",
                    "Missing required column(s): " + ", ".join(missing) + ". "
                    f"Expected columns: {', '.join(REQUIRED_COLUMNS)}.",
                )
            ]
        )

    if rows_read == 0:
        raise DatasetValidationError(
            [ValidationIssue("no_rows", "The file has a header row but no data rows.")]
        )

    df = df.copy()
    warnings: list[ValidationIssue] = []
    valid_mask = pd.Series(True, index=df.index)

    # -- 2. Type/parse errors -------------------------------------------------
    parsed_timestamp = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    bad_timestamp = parsed_timestamp.isna()
    if bad_timestamp.any():
        warnings.append(_row_issue("bad_timestamp", "Row(s) with an unparseable timestamp were dropped.", df, bad_timestamp))
        valid_mask &= ~bad_timestamp

    numeric_amount = pd.to_numeric(df["amount"], errors="coerce")
    bad_amount = numeric_amount.isna() | (numeric_amount < 0)
    if bad_amount.any():
        warnings.append(_row_issue("bad_amount", "Row(s) with a missing, non-numeric, or negative amount were dropped.", df, bad_amount))
        valid_mask &= ~bad_amount

    numeric_latency = pd.to_numeric(df["processing_latency_ms"], errors="coerce")
    bad_latency = numeric_latency.isna() | (numeric_latency < 0)
    if bad_latency.any():
        warnings.append(_row_issue("bad_latency", "Row(s) with a missing/non-numeric processing_latency_ms were dropped.", df, bad_latency))
        valid_mask &= ~bad_latency

    numeric_retry = pd.to_numeric(df["retry_count"], errors="coerce")
    bad_retry = numeric_retry.isna() | (numeric_retry < 0)
    if bad_retry.any():
        warnings.append(_row_issue("bad_retry_count", "Row(s) with a missing/non-numeric retry_count were dropped.", df, bad_retry))
        valid_mask &= ~bad_retry

    missing_id = df["transaction_id"].isna() | (df["transaction_id"].astype(str).str.strip() == "")
    if missing_id.any():
        warnings.append(_row_issue("missing_transaction_id", "Row(s) missing transaction_id were dropped.", df, missing_id))
        valid_mask &= ~missing_id

    missing_customer = df["customer_id"].isna() | (df["customer_id"].astype(str).str.strip() == "")
    if missing_customer.any():
        warnings.append(_row_issue("missing_customer_id", "Row(s) missing customer_id were dropped.", df, missing_customer))
        valid_mask &= ~missing_customer

    # -- 3. Domain/enum validation ---------------------------------------------
    for column, allowed in ENUM_COLUMNS.items():
        col_values = df[column].astype(str)
        bad_values = ~col_values.isin(allowed)
        if bad_values.any():
            warnings.append(
                _row_issue(
                    f"invalid_{column}",
                    f"Row(s) with an unrecognized {column} value were dropped "
                    f"(expected one of: {', '.join(sorted(allowed))}).",
                    df,
                    bad_values,
                )
            )
            valid_mask &= ~bad_values

    # failure_reason has a different rule: it's REQUIRED to be a known
    # value for FAILED rows, but is expected to be blank/NaN for
    # SUCCESS/PENDING rows (see app/data/schema.py) -- so only check it
    # where status == FAILED, on the currently-still-valid subset.
    status_str = df["status"].astype(str)
    is_failed = status_str == "FAILED"
    failure_reason_str = df["failure_reason"].astype(str)
    bad_failure_reason = is_failed & ~failure_reason_str.isin(set(FAILURE_REASONS))
    if bad_failure_reason.any():
        warnings.append(
            _row_issue(
                "invalid_failure_reason",
                "FAILED row(s) with a missing/unrecognized failure_reason were dropped "
                f"(expected one of: {', '.join(sorted(FAILURE_REASONS))}).",
                df,
                bad_failure_reason,
            )
        )
        valid_mask &= ~bad_failure_reason

    unexpected_currency = ~df["currency"].astype(str).isin({"INR"})
    if unexpected_currency.any():
        warnings.append(
            _row_issue(
                "unexpected_currency",
                "Row(s) with a currency other than INR were dropped -- this dataset's "
                "detection/policy math assumes a single currency.",
                df,
                unexpected_currency,
            )
        )
        valid_mask &= ~unexpected_currency

    checkout_context_str = df["checkout_context"].astype(str)
    bad_checkout_context = ~checkout_context_str.isin(set(CHECKOUT_CONTEXTS))
    if bad_checkout_context.any():
        # Non-fatal in a stricter sense: nothing downstream currently
        # branches on this value, so rows are kept but flagged.
        warnings.append(
            ValidationIssue(
                "unrecognized_checkout_context",
                "Row(s) had a checkout_context outside the recognized set "
                f"({', '.join(CHECKOUT_CONTEXTS)}) -- kept, but this field is unused "
                "downstream so it doesn't affect detection.",
                row_numbers=(df.index[bad_checkout_context][:MAX_REPORTED_ROW_ERRORS] + 1).tolist(),
                total_affected_rows=int(bad_checkout_context.sum()),
            )
        )

    valid_df = df.loc[valid_mask].copy()
    rows_valid = len(valid_df)
    rows_dropped = rows_read - rows_valid

    # -- 5. Empty-after-filtering ------------------------------------------------
    if rows_valid == 0:
        raise DatasetValidationError(
            [
                ValidationIssue(
                    "no_valid_rows",
                    "No rows passed validation -- 0 of "
                    f"{rows_read} row(s) had a valid, in-schema value for every required field.",
                )
            ]
            + warnings
        )

    # -- 4. Size limit (checked after filtering, since that's what actually
    #       gets run through detection) ------------------------------------
    if rows_valid > MAX_ROWS:
        raise DatasetValidationError(
            [
                ValidationIssue(
                    "too_many_rows",
                    f"{rows_valid} valid row(s) exceeds the {MAX_ROWS}-row limit for this demo.",
                )
            ]
        )

    # Finalize types the rest of the pipeline expects (see
    # app/data/loader.load_transactions: timestamp parsed, failure_reason/
    # incident_id NaN normalized to "").
    valid_df["timestamp"] = pd.to_datetime(valid_df["timestamp"], utc=True)
    valid_df["amount"] = pd.to_numeric(valid_df["amount"])
    valid_df["processing_latency_ms"] = pd.to_numeric(valid_df["processing_latency_ms"]).astype(int)
    valid_df["retry_count"] = pd.to_numeric(valid_df["retry_count"]).astype(int)
    valid_df["failure_reason"] = valid_df["failure_reason"].where(valid_df["status"] == "FAILED", "")
    valid_df["failure_reason"] = valid_df["failure_reason"].fillna("")
    if "incident_id" not in valid_df.columns:
        valid_df["incident_id"] = ""
    else:
        valid_df["incident_id"] = valid_df["incident_id"].fillna("")
    valid_df = valid_df.reset_index(drop=True)

    return ValidationResult(
        transactions_df=valid_df,
        warnings=warnings,
        rows_read=rows_read,
        rows_valid=rows_valid,
        rows_dropped=rows_dropped,
    )


def _row_issue(code: str, message: str, df: pd.DataFrame, bad_mask: pd.Series) -> ValidationIssue:
    bad_rows = (df.index[bad_mask] + 1).tolist()  # 1-based data-row numbers
    return ValidationIssue(
        code=code,
        message=message,
        row_numbers=bad_rows[:MAX_REPORTED_ROW_ERRORS],
        total_affected_rows=int(bad_mask.sum()),
    )
