"""
Loaders for the generated synthetic dataset.

Downstream code (detection, retrieval, agent, dashboard — built in later
steps) should import from here rather than reading the CSV/JSON directly,
so the on-disk format can evolve without breaking callers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.data.generate import INCIDENTS_JSON, TRANSACTIONS_CSV


def load_transactions(path: Path | str = TRANSACTIONS_CSV) -> pd.DataFrame:
    """Load the transaction dataset as a DataFrame.

    Raises FileNotFoundError with a helpful message if the dataset hasn't
    been generated yet.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Generate it first with: "
            "python -m app.data.generate"
        )
    df = pd.read_csv(path, parse_dates=["timestamp"])
    # CSV round-trips empty strings as NaN; normalize back to "" so downstream
    # code can do simple string comparisons/filters without NaN-handling.
    for col in ("failure_reason", "incident_id"):
        if col in df.columns:
            df[col] = df[col].fillna("")
    return df


def load_incidents(path: Path | str = INCIDENTS_JSON) -> dict:
    """Load the ground-truth incidents payload (includes metadata + incident list)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Generate it first with: "
            "python -m app.data.generate"
        )
    with open(path) as f:
        return json.load(f)


def load_incidents_list(path: Path | str = INCIDENTS_JSON) -> list[dict]:
    """Convenience accessor: just the list of incident ground-truth dicts."""
    return load_incidents(path)["incidents"]
