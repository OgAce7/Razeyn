"""
Schema definitions for the synthetic payment dataset.

This module is the single source of truth for field names, enums, and
value pools used by the generator (app/data/generate.py) and consumed by
the loader (app/data/loader.py). Keeping these here means downstream
code (detection, retrieval, agent — built in later steps) can import
constants instead of hardcoding strings.
"""

from __future__ import annotations

# --- Transaction schema (columns in transactions.csv / .parquet) -----------
# transaction_id        str   unique id, e.g. "txn_00001234"
# timestamp             str   ISO 8601 UTC timestamp
# customer_id           str   e.g. "cust_0231"
# amount                float INR amount, 2dp
# currency              str   always "INR" for this dataset
# payment_method        str   one of PAYMENT_METHODS
# institution           str   issuing bank / PSP institution, one of INSTITUTIONS
# geography              str   city, one of GEOGRAPHIES
# status                str   one of STATUSES
# failure_reason        str   one of FAILURE_REASONS, or "" if status == SUCCESS
# processing_latency_ms int   end-to-end processing latency in milliseconds
# retry_count           int   number of retries attempted for this transaction
# checkout_context      str   one of CHECKOUT_CONTEXTS
# is_incident_injected  bool  True if this transaction's outcome was altered
#                              by an injected incident (ground-truth helper
#                              column; safe for detection code to ignore)
# incident_id           str   which incident (if any) touched this row, else ""

PAYMENT_METHODS = ["UPI", "CARD", "NETBANKING", "WALLET"]

# Institutions relevant to UPI/netbanking routing and card issuing banks.
INSTITUTIONS = [
    "HDFC Bank",
    "ICICI Bank",
    "State Bank of India",
    "Axis Bank",
    "Kotak Mahindra Bank",
    "Yes Bank",
    "Paytm Payments Bank",
    "IDFC First Bank",
]

GEOGRAPHIES = [
    "Mumbai",
    "Delhi",
    "Bangalore",
    "Chennai",
    "Hyderabad",
    "Pune",
    "Kolkata",
    "Ahmedabad",
    "Jaipur",
    "Lucknow",
]

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_PENDING = "PENDING"
STATUSES = [STATUS_SUCCESS, STATUS_FAILED, STATUS_PENDING]

FAILURE_REASONS = [
    "INSUFFICIENT_FUNDS",
    "BANK_TIMEOUT",
    "INVALID_OTP",
    "NETWORK_ERROR",
    "RISK_DECLINE",
    "ISSUER_DECLINE",
    "GATEWAY_ERROR",
]

CHECKOUT_CONTEXTS = [
    "one_time_checkout",
    "cart_checkout",
    "subscription_renewal",
]

# --- Incident ground-truth schema (incidents.json) --------------------------
# incident_id            str    e.g. "inc_001"
# type                   str    one of INCIDENT_TYPES
# start_time             str    ISO 8601 UTC
# end_time               str    ISO 8601 UTC
# affected_segment       dict   the filter used to select affected transactions,
#                                e.g. {"payment_method": "UPI", "institution": "HDFC Bank"}
# expected_failure_pattern str  human-readable description of the expected signature
# expected_severity      str    one of SEVERITIES
# affected_transaction_ids list[str]
# transaction_count      int    len(affected_transaction_ids)
# revenue_exposed        float  sum of `amount` for affected transactions
# is_true_incident       bool   False only for the benign-fluctuation case,
#                                so evaluation code can check for false positives

INCIDENT_TYPES = [
    "bank_specific_upi_degradation",
    "payment_method_degradation",
    "latency_spike",
    "geographic_concentration",
    "benign_traffic_fluctuation",
]

SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL", "NONE"]
