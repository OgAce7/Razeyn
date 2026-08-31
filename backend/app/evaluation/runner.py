"""
Batch runner for the evaluation layer.

This module does NOT implement the agent, the detector, or the policy
engine -- it only orchestrates calls to the functions those modules
already expose (`investigate_incident`, `evaluate_policy`,
`execute_action`), for every candidate incident in a batch, and records
one `AuditRecord` per incident plus a baseline comparison. This is the
piece that turns "we have five separate modules with clean I/O
contracts" into "here is one reproducible batch evaluation run."

`investigate_fn` / `retrieve_fn` are injectable specifically so this
module -- and the tests for it -- never *require* a live Anthropic API
call: pass a stub that returns a fixed `AgentResult` and the entire
pipeline (policy -> executor -> audit -> metrics) can be exercised
deterministically offline. The default values point at the real
functions for actual end-to-end runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.audit.builder import build_audit_record
from app.audit.schema import AuditRecord
from app.audit.store import AuditStore
from app.evaluation.baseline import BaselineOutcome, run_baseline
from app.evaluation.metrics import compute_exact_revenue_recovered
from app.policies.adapter import RecoveryActionAdapter
from app.policies.engine import evaluate_policy
from app.policies.executor import execute_action
from app.policies.ledger import ActionLedger


@dataclass
class BatchIncidentResult:
    """Everything produced for one candidate incident during a batch run --
    the audit record plus the raw revenue-recovered figure and baseline
    outcome, so callers don't need to re-derive them from the AuditRecord's
    compressed fields."""

    audit_record: AuditRecord
    revenue_recovered: float
    baseline_outcome: BaselineOutcome | None


def _default_investigate_fn():
    from app.agent.investigate import investigate_incident

    return investigate_incident


def _resolve_window_transaction_ids(candidate: dict[str, Any], transactions_df) -> list[str]:
    """Real detection-engine candidates (app/data/synthetic/candidate_incidents.json)
    do NOT carry an `affected_transaction_ids` list -- only the synthetic
    ground-truth incidents.json does (that field is a ground-truth-only
    convenience for the data generator, see app/data/schema.py). To find
    which real transactions a candidate's window+segment covers, this
    reuses the SAME segment-resolution logic the retrieval layer already
    uses (app/retrieval/structured.resolve_segment_mask) plus a timestamp
    filter on window_start/window_end -- no new segment-matching logic is
    invented here.
    """
    import pandas as pd

    from app.retrieval.structured import resolve_segment_mask

    segment_mask = resolve_segment_mask(transactions_df, candidate.get("affected_segment") or {})
    window_start = pd.Timestamp(candidate["window_start"])
    window_end = pd.Timestamp(candidate["window_end"])
    ts = transactions_df["timestamp"]
    if ts.dt.tz is not None and window_start.tzinfo is None:
        window_start = window_start.tz_localize(ts.dt.tz)
        window_end = window_end.tz_localize(ts.dt.tz)
    window_mask = (ts >= window_start) & (ts <= window_end)
    return transactions_df.loc[segment_mask & window_mask, "transaction_id"].tolist()


def run_batch_evaluation(
    candidate_incidents: list[dict[str, Any]],
    transactions_by_id: dict[str, dict[str, Any]],
    evidence_by_incident_id: dict[str, dict[str, Any]],
    ground_truth_by_incident_id: dict[str, dict[str, Any]] | None = None,
    allowed_actions: list[str] | None = None,
    merchant_policies: dict[str, Any] | None = None,
    adapter: RecoveryActionAdapter | None = None,
    investigate_fn: Callable[[Any], Any] | None = None,
    run_baseline_comparison: bool = True,
    transactions_df=None,
    now=None,
) -> tuple[AuditStore, list[BatchIncidentResult]]:
    """Run the full pipeline (agent -> policy -> executor) for every
    candidate incident, build one AuditRecord each, and optionally run the
    baseline strategy over the same transaction window for comparison.

    Parameters
    ----------
    candidate_incidents : detection engine output (app/detection/), one
        dict per candidate -- same shape as candidate_incidents.json's
        "candidates" list.
    transactions_by_id : the full transaction set keyed by transaction_id
        (e.g. from app.data.loader.load_transactions(), reshaped). This is
        the SOLE source of amounts for revenue-recovered and baseline
        computations -- never a value from the candidate incident dict or
        the agent's output.
    transactions_df : optional -- the same transaction set as a DataFrame
        (e.g. straight from app.data.loader.load_transactions()). Needed
        to resolve which transactions fall inside a candidate's
        window+segment when the candidate doesn't already carry an
        `affected_transaction_ids` list (real detector output never does
        -- only the synthetic ground-truth incidents.json does; see
        `_resolve_window_transaction_ids`). If omitted, this function
        falls back to `candidate.get("affected_transaction_ids", [])`,
        which will be empty for real detector candidates -- pass this
        whenever running against real (non-ground-truth-labeled) output.
    evidence_by_incident_id : retrieval-layer output (app/retrieval/),
        keyed by candidate_incident_id.
    ground_truth_by_incident_id : optional, keyed by ground-truth
        incident_id -- only needed if the caller can also map each
        candidate to the ground-truth incident it corresponds to (pass
        candidate["incident_id"] as the key if the detector already uses
        matching ids, otherwise resolve the mapping before calling this
        and pass ground truth dicts keyed however you've matched them,
        using the SAME key as candidate["incident_id"]).
    investigate_fn : defaults to the real `investigate_incident`
        (app/agent/investigate.py). Inject a stub for offline/deterministic
        tests -- this module places no requirements on what it returns
        beyond the `AgentResult` shape.
    run_baseline_comparison : if True (default), also runs the fixed
        baseline retry rule over the same incident's transactions.

    Returns
    -------
    (AuditStore populated with one record per incident, list of
    per-incident BatchIncidentResult for callers wanting the un-compressed
    revenue/baseline figures directly).
    """
    from app.agent.actions import ALL_ACTIONS
    from app.agent.schema import AgentInput

    investigate_fn = investigate_fn or _default_investigate_fn()
    ground_truth_by_incident_id = ground_truth_by_incident_id or {}
    store = AuditStore()
    ledger = ActionLedger()
    results: list[BatchIncidentResult] = []

    for candidate in candidate_incidents:
        incident_id = candidate["incident_id"]
        evidence = evidence_by_incident_id.get(incident_id, {})
        structured = evidence.get("structured_evidence") or []
        unstructured = evidence.get("unstructured_evidence") or []

        agent_input = AgentInput(
            incident=candidate,
            structured_evidence=structured,
            unstructured_evidence=unstructured,
            allowed_actions=allowed_actions or list(ALL_ACTIONS),
            merchant_policies=merchant_policies or {},
        )
        agent_result = investigate_fn(agent_input)

        if transactions_df is not None:
            window_transaction_ids = _resolve_window_transaction_ids(candidate, transactions_df)
        else:
            window_transaction_ids = candidate.get("affected_transaction_ids") or []
        window_transactions = [
            transactions_by_id[tid] for tid in window_transaction_ids if tid in transactions_by_id
        ]

        policy_decision = evaluate_policy(
            recommended_action=agent_result.output.recommended_action,
            incident=candidate,
            transactions=window_transactions,
            confidence=agent_result.output.confidence,
            revenue_at_risk=agent_result.output.revenue_at_risk,
            ledger=ledger,
            merchant_policies=merchant_policies,
            now=now,
        )
        action_record = execute_action(
            requested_action=agent_result.output.recommended_action,
            decision=policy_decision,
            incident=candidate,
            transactions=window_transactions,
            ledger=ledger,
            adapter=adapter,
            now=now,
        )

        ground_truth = ground_truth_by_incident_id.get(incident_id)
        record = build_audit_record(
            candidate_incident=candidate,
            evidence=evidence,
            agent_result=agent_result,
            policy_decision=policy_decision,
            action_record=action_record,
            ground_truth=ground_truth,
        )
        store.add(record)

        revenue_recovered = compute_exact_revenue_recovered(action_record)

        baseline_outcome = None
        if run_baseline_comparison:
            baseline_outcome = run_baseline(incident_id, window_transactions, adapter=adapter)

        results.append(
            BatchIncidentResult(
                audit_record=record,
                revenue_recovered=revenue_recovered,
                baseline_outcome=baseline_outcome,
            )
        )

    return store, results


def revenue_recovered_map(results: list[BatchIncidentResult]) -> dict[str, float]:
    """Convenience: `{audit_record.record_id: revenue_recovered}`, the
    exact shape `app.evaluation.metrics.evaluate_batch` expects."""
    return {r.audit_record.record_id: r.revenue_recovered for r in results}


def baseline_outcomes_list(results: list[BatchIncidentResult]) -> list[BaselineOutcome]:
    return [r.baseline_outcome for r in results if r.baseline_outcome is not None]
