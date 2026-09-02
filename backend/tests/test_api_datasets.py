"""
Tests for app/api/datasets.py -- upload, list, activate.

These exercise the endpoints through FastAPI's TestClient with a real
AppState (no LLM required, same as test_api_incidents.py -- without an
ANTHROPIC_API_KEY the agent falls back to its tested ESCALATE-on-error
path, which is fine here since these tests care about the upload/dataset
plumbing, not agent behavior).

Uses a small real slice of the seeded synthetic dataset as "the uploaded
file" for the happy-path tests, so detection actually has realistic
data to find candidates in -- a handful of hand-written rows is too
small to clear the detector's min_window_n/min_baseline_n thresholds
(see app/detection/config.py) and would produce zero candidates, which
wouldn't actually exercise the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import datasets as datasets_module
from app.api import incidents as incidents_module
from app.api.pipeline import seed_from_synthetic_dataset
from app.api.state import AppState

SYNTHETIC_CSV = Path("app/data/synthetic/transactions.csv")
pytestmark = pytest.mark.skipif(
    not SYNTHETIC_CSV.exists(),
    reason="synthetic dataset not generated -- run app.data.generate then app.detection.run",
)


def _make_app(state: AppState) -> FastAPI:
    app = FastAPI()
    app.state.app_state = state
    app.include_router(datasets_module.router)
    app.include_router(incidents_module.router)
    return app


def _seeded_state() -> AppState:
    state = AppState()
    seed_from_synthetic_dataset(state)
    return state


def _valid_csv_bytes(n_rows: int = 2000) -> bytes:
    df = pd.read_csv(SYNTHETIC_CSV)
    sample = df.sample(n=min(n_rows, len(df)), random_state=7)
    return sample.to_csv(index=False).encode("utf-8")


# ---------------------------------------------------------------------------
# GET /api/datasets
# ---------------------------------------------------------------------------

def test_list_datasets_shows_seeded_as_active_initially():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    resp = client.get("/api/datasets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_dataset_id"] == "seeded"
    assert len(body["datasets"]) == 1
    assert body["datasets"][0]["kind"] == "seeded"
    assert body["datasets"][0]["row_count"] > 0


# ---------------------------------------------------------------------------
# POST /api/datasets/upload -- happy path
# ---------------------------------------------------------------------------

def test_upload_valid_csv_runs_pipeline_and_becomes_active():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    resp = client.post(
        "/api/datasets/upload",
        files={"file": ("my_transactions.csv", _valid_csv_bytes(), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "my_transactions.csv"
    assert body["row_count"] > 0
    assert body["validation_summary"]["rows_dropped"] == 0

    # The uploaded dataset is now active -- audit trail reflects it, not
    # the seeded dataset.
    trail = client.get("/api/evaluation/audit-trail").json()
    assert len(trail) == body["candidate_count"]

    datasets = client.get("/api/datasets").json()
    assert datasets["active_dataset_id"] == body["dataset_id"]
    assert len(datasets["datasets"]) == 2  # seeded + this upload


def test_upload_replaces_not_merges_previous_dataset():
    """A second upload should REPLACE the first upload's audit trail,
    not add to it -- only one dataset is live at a time."""
    state = _seeded_state()
    client = TestClient(_make_app(state))

    first = client.post(
        "/api/datasets/upload",
        files={"file": ("first.csv", _valid_csv_bytes(n_rows=1500), "text/csv")},
    ).json()
    first_trail_len = len(client.get("/api/evaluation/audit-trail").json())
    assert first_trail_len == first["candidate_count"]

    second = client.post(
        "/api/datasets/upload",
        files={"file": ("second.csv", _valid_csv_bytes(n_rows=3000), "text/csv")},
    ).json()
    second_trail = client.get("/api/evaluation/audit-trail").json()
    assert len(second_trail) == second["candidate_count"]
    # None of the second run's records should be left over from the first
    # if candidate ids happened to collide is out of scope here; the
    # meaningful invariant is that the trail length matches the LATEST
    # run's candidate_count, not first+second combined.
    assert len(second_trail) != first_trail_len + first["candidate_count"] \
        or second["candidate_count"] == first["candidate_count"]


# ---------------------------------------------------------------------------
# POST /api/datasets/upload -- validation / crash-proofing
# ---------------------------------------------------------------------------

def test_upload_empty_file_returns_422_not_500():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    resp = client.post("/api/datasets/upload", files={"file": ("empty.csv", b"", "text/csv")})
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"][0]["code"] == "empty_file"

    # Server is still alive and serving the seeded dataset -- a bad
    # upload must never take down or corrupt existing state.
    still_ok = client.get("/api/evaluation/audit-trail")
    assert still_ok.status_code == 200
    assert client.get("/api/datasets").json()["active_dataset_id"] == "seeded"


def test_upload_missing_columns_returns_422_not_500():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    resp = client.post(
        "/api/datasets/upload", files={"file": ("bad.csv", b"foo,bar\n1,2\n", "text/csv")}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"][0]["code"] == "missing_columns"


def test_upload_binary_garbage_returns_422_not_500():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    garbage = bytes([0xFF, 0xFE, 0x00, 0x01] * 100)
    resp = client.post(
        "/api/datasets/upload", files={"file": ("garbage.csv", garbage, "application/octet-stream")}
    )
    assert resp.status_code == 422


def test_upload_all_rows_invalid_returns_422_not_500():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    from app.data.validate_upload import REQUIRED_COLUMNS

    header = ",".join(REQUIRED_COLUMNS)
    bad_row = ",".join(["" for _ in REQUIRED_COLUMNS])
    resp = client.post(
        "/api/datasets/upload",
        files={"file": ("all_bad.csv", f"{header}\n{bad_row}\n".encode(), "text/csv")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"][0]["code"] == "no_valid_rows"


def test_upload_wrong_extension_returns_400():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    resp = client.post(
        "/api/datasets/upload", files={"file": ("data.txt", b"whatever", "text/plain")}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["errors"][0]["code"] == "wrong_file_type"


def test_partial_bad_rows_still_succeeds_with_warnings_reported():
    """A file that's mostly valid but has a few bad rows should still
    upload successfully (dropping only the bad rows), with the drop
    reported back to the caller."""
    state = _seeded_state()
    client = TestClient(_make_app(state))

    df = pd.read_csv(SYNTHETIC_CSV).sample(n=2000, random_state=3).reset_index(drop=True)
    df.loc[0, "payment_method"] = "BITCOIN"  # inject one bad row
    df.loc[1, "amount"] = -5.0
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    resp = client.post(
        "/api/datasets/upload", files={"file": ("mostly_valid.csv", csv_bytes, "text/csv")}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["validation_summary"]["rows_dropped"] == 2
    codes = {w["code"] for w in body["validation_summary"]["warnings"]}
    assert "invalid_payment_method" in codes
    assert "bad_amount" in codes


# ---------------------------------------------------------------------------
# POST /api/datasets/activate/{id}
# ---------------------------------------------------------------------------

def test_activate_seeded_after_upload_switches_back():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    client.post(
        "/api/datasets/upload", files={"file": ("up.csv", _valid_csv_bytes(), "text/csv")}
    )
    assert client.get("/api/datasets").json()["active_dataset_id"] != "seeded"

    resp = client.post("/api/datasets/activate/seeded")
    assert resp.status_code == 200
    assert resp.json() == {"status": "activated", "dataset_id": "seeded"}
    assert client.get("/api/datasets").json()["active_dataset_id"] == "seeded"


def test_activate_already_active_is_a_noop():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    resp = client.post("/api/datasets/activate/seeded")
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_active"


def test_activate_unknown_upload_id_returns_409():
    state = _seeded_state()
    client = TestClient(_make_app(state))

    resp = client.post("/api/datasets/activate/upload_doesnotexist")
    assert resp.status_code == 409
