"""
Tests for app/data/validate_upload.py -- the layer that stands between
an arbitrary uploaded file and the detection/retrieval/agent pipeline.
Every case here is chosen to confirm the pipeline NEVER sees malformed
input: either validation raises a clean DatasetValidationError, or it
returns a DataFrame that's actually safe to run detect_incidents() on.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data.validate_upload import (
    DatasetValidationError,
    MAX_ROWS,
    REQUIRED_COLUMNS,
    validate_dataframe,
    validate_upload_bytes,
)

HEADER = ",".join(REQUIRED_COLUMNS)


def _row(**overrides) -> str:
    base = {
        "transaction_id": "txn_1",
        "timestamp": "2026-08-10T00:01:54+00:00",
        "customer_id": "cust_1",
        "amount": "100.0",
        "currency": "INR",
        "payment_method": "UPI",
        "institution": "HDFC Bank",
        "geography": "Mumbai",
        "status": "SUCCESS",
        "failure_reason": "",
        "processing_latency_ms": "500",
        "retry_count": "0",
        "checkout_context": "cart_checkout",
    }
    base.update(overrides)
    return ",".join(str(base[c]) for c in REQUIRED_COLUMNS)


def _csv(*rows: str) -> bytes:
    return (HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Structural / empty / unreadable
# ---------------------------------------------------------------------------

def test_empty_bytes_raises_clean_error():
    with pytest.raises(DatasetValidationError) as exc:
        validate_upload_bytes(b"")
    assert exc.value.to_dict()["errors"][0]["code"] == "empty_file"


def test_header_only_no_rows_raises():
    with pytest.raises(DatasetValidationError) as exc:
        validate_upload_bytes((HEADER + "\n").encode())
    assert exc.value.to_dict()["errors"][0]["code"] == "no_rows"


def test_missing_columns_raises_with_all_missing_named():
    with pytest.raises(DatasetValidationError) as exc:
        validate_upload_bytes(b"foo,bar\n1,2\n")
    err = exc.value.to_dict()["errors"][0]
    assert err["code"] == "missing_columns"
    assert "transaction_id" in err["message"]


def test_binary_garbage_does_not_crash_raises_clean_error():
    garbage = bytes([0xFF, 0xFE, 0x00, 0x01, 0x02] * 50)
    with pytest.raises(DatasetValidationError):
        validate_upload_bytes(garbage)


def test_non_csv_text_that_still_parses_is_handled_via_missing_columns():
    with pytest.raises(DatasetValidationError) as exc:
        validate_upload_bytes(b"just some\nplain text\nnot a csv at all\n")
    # pandas parses this as a 1-column CSV -- should fail on missing
    # required columns, not crash.
    assert exc.value.to_dict()["errors"][0]["code"] == "missing_columns"


# ---------------------------------------------------------------------------
# Valid file passes through cleanly
# ---------------------------------------------------------------------------

def test_fully_valid_csv_passes_with_no_warnings():
    csv = _csv(_row(transaction_id="txn_1"), _row(transaction_id="txn_2"))
    result = validate_upload_bytes(csv)
    assert result.rows_read == 2
    assert result.rows_valid == 2
    assert result.rows_dropped == 0
    assert result.warnings == []
    assert pd.api.types.is_datetime64_any_dtype(result.transactions_df["timestamp"])


def test_real_synthetic_dataset_validates_cleanly():
    """The actual seeded dataset must validate with zero warnings --
    otherwise the validator disagrees with the data it's meant to accept."""
    with open("app/data/synthetic/transactions.csv", "rb") as f:
        raw = f.read()
    result = validate_upload_bytes(raw)
    assert result.rows_dropped == 0
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Row-level type/parse errors -- dropped with a warning, not fatal
# ---------------------------------------------------------------------------

def test_bad_timestamp_row_is_dropped_with_warning():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id="txn_bad", timestamp="not-a-date"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert result.transactions_df.iloc[0]["transaction_id"] == "txn_ok"
    codes = {w.code for w in result.warnings}
    assert "bad_timestamp" in codes


def test_negative_amount_row_is_dropped():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id="txn_neg", amount="-5.0"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert {w.code for w in result.warnings} >= {"bad_amount"}


def test_non_numeric_amount_row_is_dropped():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id="txn_bad", amount="not-a-number"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1


def test_missing_transaction_id_row_is_dropped():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id=""))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert {w.code for w in result.warnings} >= {"missing_transaction_id"}


def test_missing_customer_id_row_is_dropped():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id="txn_bad", customer_id=""))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1


# ---------------------------------------------------------------------------
# Domain/enum validation
# ---------------------------------------------------------------------------

def test_invalid_payment_method_row_is_dropped():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id="txn_bad", payment_method="BITCOIN"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert {w.code for w in result.warnings} >= {"invalid_payment_method"}


def test_invalid_status_row_is_dropped():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id="txn_bad", status="WEIRD_STATUS"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1


def test_failed_row_without_failure_reason_is_dropped():
    csv = _csv(
        _row(transaction_id="txn_ok"),
        _row(transaction_id="txn_bad_failed", status="FAILED", failure_reason=""),
    )
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert {w.code for w in result.warnings} >= {"invalid_failure_reason"}


def test_failed_row_with_valid_failure_reason_is_kept():
    csv = _csv(_row(transaction_id="txn_failed", status="FAILED", failure_reason="BANK_TIMEOUT"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1


def test_success_row_with_blank_failure_reason_is_fine():
    csv = _csv(_row(transaction_id="txn_ok", status="SUCCESS", failure_reason=""))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert result.transactions_df.iloc[0]["failure_reason"] == ""


def test_non_inr_currency_row_is_dropped():
    csv = _csv(_row(transaction_id="txn_ok"), _row(transaction_id="txn_usd", currency="USD"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert {w.code for w in result.warnings} >= {"unexpected_currency"}


def test_unrecognized_checkout_context_is_kept_but_flagged():
    """checkout_context isn't read by any downstream pipeline code, so a
    bad value is a warning, not a dropped row."""
    csv = _csv(_row(transaction_id="txn_1", checkout_context="something_new"))
    result = validate_upload_bytes(csv)
    assert result.rows_valid == 1
    assert {w.code for w in result.warnings} >= {"unrecognized_checkout_context"}


# ---------------------------------------------------------------------------
# All-rows-invalid -> fatal
# ---------------------------------------------------------------------------

def test_all_rows_invalid_raises_no_valid_rows():
    csv = _csv(_row(transaction_id="", timestamp="garbage", amount="-1"))
    with pytest.raises(DatasetValidationError) as exc:
        validate_upload_bytes(csv)
    codes = {e["code"] for e in exc.value.to_dict()["errors"]}
    assert "no_valid_rows" in codes


# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------

def test_oversized_byte_count_rejected_before_parsing():
    from app.data.validate_upload import MAX_UPLOAD_BYTES

    oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(DatasetValidationError) as exc:
        validate_upload_bytes(oversized)
    assert exc.value.to_dict()["errors"][0]["code"] == "file_too_large"


def test_too_many_valid_rows_rejected():
    df = pd.DataFrame(
        [
            {
                "transaction_id": f"txn_{i}",
                "timestamp": "2026-08-10T00:01:54+00:00",
                "customer_id": f"cust_{i}",
                "amount": 100.0,
                "currency": "INR",
                "payment_method": "UPI",
                "institution": "HDFC Bank",
                "geography": "Mumbai",
                "status": "SUCCESS",
                "failure_reason": "",
                "processing_latency_ms": 500,
                "retry_count": 0,
                "checkout_context": "cart_checkout",
            }
            for i in range(MAX_ROWS + 1)
        ]
    )
    with pytest.raises(DatasetValidationError) as exc:
        validate_dataframe(df)
    assert exc.value.to_dict()["errors"][0]["code"] == "too_many_rows"


# ---------------------------------------------------------------------------
# Row-error reporting is capped, but totals are accurate
# ---------------------------------------------------------------------------

def test_many_bad_rows_caps_reported_samples_but_reports_true_total():
    from app.data.validate_upload import MAX_REPORTED_ROW_ERRORS

    rows = [_row(transaction_id=f"txn_ok_{0}")]
    n_bad = MAX_REPORTED_ROW_ERRORS + 10
    rows += [_row(transaction_id=f"txn_bad_{i}", amount="-1") for i in range(n_bad)]
    csv = _csv(*rows)
    result = validate_upload_bytes(csv)
    bad_amount_warning = next(w for w in result.warnings if w.code == "bad_amount")
    assert len(bad_amount_warning.row_numbers) == MAX_REPORTED_ROW_ERRORS
    assert bad_amount_warning.total_affected_rows == n_bad
