"""
Produces a real, reproducible example evaluation report over the
project's actual synthetic dataset (transactions.csv, incidents.json,
candidate_incidents.json) and the real retrieval/policy/executor code.

The AI agent itself is NOT built by this task (per the brief), and this
sandbox has no network access to the Anthropic API, so this script uses
a small deterministic, evidence-driven stub in place of the real LLM
call -- it inspects the SAME structured evidence the real agent would
receive and picks an action using simple fixed rules (not a model call,
not randomness). This is clearly a stand-in for demonstration purposes
only: the point of this script is to prove the evaluation/metrics/audit
layer works end-to-end against real data, not to claim this stub is a
good incident-response policy.

Run with:  python -m scripts.run_example_evaluation
(from the backend/ directory, with the synthetic dataset already
generated -- see app/data/generate.py)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.agent.schema import AgentOutput  # noqa: E402
from app.data.loader import load_transactions  # noqa: E402
from app.evaluation.metrics import evaluate_batch  # noqa: E402
from app.evaluation.report import render_markdown_report  # noqa: E402
from app.evaluation.runner import (  # noqa: E402
    baseline_outcomes_list,
    revenue_recovered_map,
    run_batch_evaluation,
)
from app.retrieval.bundle import retrieve_evidence  # noqa: E402

CANDIDATE_INCIDENTS_PATH = BACKEND_DIR / "app/data/synthetic/candidate_incidents.json"
INCIDENTS_PATH = BACKEND_DIR / "app/data/synthetic/incidents.json"
OUT_DIR = BACKEND_DIR / "app/evaluation/example_output"


@dataclass
class _StubAgentResult:
    output: Any
    status: str = "ok"
    guardrail_violations: list = field(default_factory=list)
    error_detail: str | None = None


def _stub_investigate(agent_input) -> _StubAgentResult:
    """Deterministic stand-in for the real LLM call (app/agent/investigate.py
    is not built/modified by this task). Looks only at the structured
    evidence it was given -- same input the real agent would see -- and
    picks an action via fixed rules:
      - if the incident's transient-failure-reason share (from evidence)
        is high and confidence is high -> RETRY_ELIGIBLE_PAYMENTS
      - if confidence is low -> ESCALATE
      - otherwise -> WAIT_AND_REASSESS
    Revenue at risk is taken directly from the incident's own
    revenue_affected (same number the real guardrails would also
    ultimately re-derive/enforce -- see app/agent/guardrails.py).
    """
    incident = agent_input.incident
    evidence_ids = [e["evidence_id"] for e in agent_input.structured_evidence]
    confidence = float(incident.get("confidence_score", 0.5))
    revenue_at_risk = float(incident.get("revenue_affected", 0.0))

    transient_share = 0.0
    for item in agent_input.structured_evidence:
        if item.get("evidence_type") != "failure_reason_breakdown":
            continue
        breakdown = (item.get("data") or {}).get("failure_reason_counts") or {}
        total = sum(breakdown.values()) or 1
        transient = sum(
            v for k, v in breakdown.items() if k in ("BANK_TIMEOUT", "NETWORK_ERROR", "GATEWAY_ERROR")
        )
        transient_share = transient / total
        break

    if confidence < 0.5:
        action, reason, escalate = "ESCALATE", "Low detector confidence; needs human review.", True
    elif transient_share >= 0.4:
        action = "RETRY_ELIGIBLE_PAYMENTS"
        reason = f"Transient failure reasons account for {transient_share:.0%} of failures in-window."
        escalate = False
    else:
        action = "WAIT_AND_REASSESS"
        reason = "Failure pattern not dominated by transient reasons; reassess before acting."
        escalate = False

    output = AgentOutput(
        diagnosis=f"{incident.get('affected_dimension', 'segment')} degradation: {incident.get('observation', '')[:200]}",
        observations=[incident.get("observation", "")],
        inferences=[reason],
        evidence_ids=evidence_ids,
        revenue_at_risk=revenue_at_risk,
        recommended_action=action,
        reason=reason,
        confidence=confidence,
        stop_condition="",
        escalation_required=escalate,
    )
    return _StubAgentResult(output=output)


def _load_candidates() -> list[dict]:
    return json.loads(CANDIDATE_INCIDENTS_PATH.read_text())["candidates"]


def _load_ground_truth() -> list[dict]:
    return json.loads(INCIDENTS_PATH.read_text())["incidents"]


def _match_candidate_to_ground_truth(candidate: dict, ground_truths: list[dict]) -> dict | None:
    """Match by affected_segment subset/equality -- the detector's
    segment dict and ground truth's injected segment dict use the same
    key/value conventions (see app/data/schema.py), so exact-or-subset
    match is a reliable pairing without needing shared ids."""
    cand_segment = candidate.get("affected_segment") or {}
    best = None
    for gt in ground_truths:
        gt_segment = gt.get("affected_segment") or {}
        if cand_segment == gt_segment:
            return gt
        if cand_segment and gt_segment and cand_segment.items() <= gt_segment.items():
            best = best or gt
    return best


def main() -> None:
    candidates = _load_candidates()
    ground_truths = _load_ground_truth()
    df = load_transactions()
    transactions_by_id = df.set_index("transaction_id", drop=False).to_dict("index")
    for tid, row in transactions_by_id.items():
        row["transaction_id"] = tid

    evidence_by_incident_id = {}
    ground_truth_by_incident_id = {}
    for candidate in candidates:
        cid = candidate["incident_id"]
        evidence_by_incident_id[cid] = retrieve_evidence(cid, transactions=df)
        matched_gt = _match_candidate_to_ground_truth(candidate, ground_truths)
        if matched_gt is not None:
            ground_truth_by_incident_id[cid] = matched_gt

    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence_by_incident_id,
        ground_truth_by_incident_id=ground_truth_by_incident_id,
        investigate_fn=_stub_investigate,
        run_baseline_comparison=True,
        transactions_df=df,
    )

    report = evaluate_batch(
        store.all(),
        revenue_recovered_map(results),
        baseline_outcomes_list(results),
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store.save_json(OUT_DIR / "audit_trail.json")
    (OUT_DIR / "evaluation_report.json").write_text(json.dumps(report.to_dict(), indent=2, default=str))
    (OUT_DIR / "evaluation_report.md").write_text(render_markdown_report(report))

    print(render_markdown_report(report))
    print(f"\nWrote: {OUT_DIR / 'audit_trail.json'}")
    print(f"Wrote: {OUT_DIR / 'evaluation_report.json'}")
    print(f"Wrote: {OUT_DIR / 'evaluation_report.md'}")


if __name__ == "__main__":
    main()
