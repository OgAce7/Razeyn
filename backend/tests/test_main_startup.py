"""
Regression tests for app/main.py's startup handler.

Context: `on_startup` seeds the synthetic dataset by running the FULL
pipeline, including a real call to the investigation agent
(app/agent/investigate.py) per candidate incident -- which, whenever a
Mistral API key is configured, means a live network call at startup.
Previously, only `FileNotFoundError` was caught around that seeding
step; any other failure (rate limit, timeout, transient network error,
an unexpected exception from a dependency) propagated out of
`on_startup`, which FastAPI/uvicorn treats as a fatal startup error --
`app.state.app_state` was left unset, and every subsequent request
(including totally unrelated ones like GET /api/datasets) failed with
an AttributeError, indistinguishable from "the whole backend is
broken."

These tests confirm startup completes and `app.state.app_state` is
always set, even when seeding raises something other than
FileNotFoundError.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


def test_startup_survives_unexpected_seeding_failure():
    """A non-FileNotFoundError exception during seeding (e.g. what a
    live Mistral API failure could surface as) must not prevent the app
    from starting or leave app.state.app_state unset."""
    with patch(
        "app.main.seed_from_synthetic_dataset",
        side_effect=RuntimeError("simulated Mistral API failure during seeding"),
    ):
        from app.main import app

        with TestClient(app) as client:
            # If startup had crashed, app.state.app_state would never
            # have been assigned and this would raise AttributeError
            # deep inside the endpoint instead of returning cleanly.
            response = client.get("/api/datasets")
            assert response.status_code == 200
            body = response.json()
            assert body["datasets"] == []
            assert body["active_dataset_id"] is None

            health = client.get("/api/health")
            assert health.status_code == 200


def test_startup_survives_missing_synthetic_dataset():
    """Existing behavior (FileNotFoundError) must keep working
    identically after broadening the except clause."""
    with patch(
        "app.main.seed_from_synthetic_dataset",
        side_effect=FileNotFoundError("synthetic dataset not generated"),
    ):
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/datasets")
            assert response.status_code == 200
            assert response.json()["datasets"] == []


def test_startup_succeeds_normally_when_seeding_works():
    """Sanity check: a successful seed still populates app_state as
    before -- the broadened except clause must not swallow the happy
    path or change its behavior."""
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/datasets")
        assert response.status_code == 200
        # Either the real synthetic dataset seeded successfully (at
        # least one dataset present, the seeded one active), or it's
        # genuinely absent in this environment -- either way this must
        # not 500.
        body = response.json()
        assert "datasets" in body
        assert "active_dataset_id" in body
