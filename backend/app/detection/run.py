"""
Run the detection engine against the generated synthetic dataset and
write the candidate incidents to app/data/synthetic/candidate_incidents.json.

Usage:
    python -m app.detection.run
"""

from __future__ import annotations

import json
from pathlib import Path

from app.data.loader import load_transactions
from app.detection.config import DEFAULT_CONFIG
from app.detection.detector import detect_incidents

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "synthetic" / "candidate_incidents.json"


def main() -> None:
    df = load_transactions()
    candidates = detect_incidents(df, DEFAULT_CONFIG)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump({"candidate_count": len(candidates), "candidates": candidates}, f, indent=2)

    print(f"Analyzed {len(df)} transactions.")
    print(f"Found {len(candidates)} candidate incident(s) -> {OUTPUT_PATH}\n")
    for c in candidates:
        print(
            f"  [{c['incident_id']}] {c['severity']:8s} "
            f"{c['affected_dimension']:24s} {c['affected_segment']} "
            f"n={c['transaction_count']:4d} "
            f"degradation={c['degradation_percentage']}% "
            f"revenue_affected={c['revenue_affected']}"
        )


if __name__ == "__main__":
    main()
