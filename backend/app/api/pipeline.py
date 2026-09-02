"""
API-layer orchestration of the existing pipeline modules.

This is intentionally NOT a reimplementation of
app/evaluation/runner.run_batch_evaluation -- it calls the exact same
functions that module calls (investigate_incident, evaluate_policy,
execute_action, build_audit_record), in the same order, with the same
"transactions is the sole source of money" discipline. The one thing it
does differently is *why this file exists*: run_batch_evaluation builds
its own throwaway ActionLedger()/AuditStore() per call, appropriate for
a one-shot batch/eval script. The API needs those to be the single
long-lived AppState instances (see app/api/state.py), and needs to hold
onto the raw PolicyDecision object for any incident that comes out
escalated, so a later approve/reject call can resolve it without
re-running evaluate_policy. run_batch_evaluation's return shape doesn't
expose that PolicyDecision, so it can't be reused as-is here without
either changing its signature (touching tested, working code) or
duplicating its ~15 lines of loop body. This duplicates the loop body
only -- every call inside it is the same real function.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.agent.actions import ALL_ACTIONS
from app.agent.investigate import investigate_incident
from app.agent.schema import AgentInput
from app.audit.builder import build_audit_record
from app.data.loader import load_incidents_list, load_transactions
from app.detection.config import DEFAULT_CONFIG
from app.detection.detector import detect_incidents
from app.policies.engine import evaluate_policy
from app.policies.executor import EXECUTION_NOT_EXECUTED_ESCALATED, execute_action
from app.retrieval.structured import resolve_segment_mask
from app.retrieval.bundle import retrieve_evidence

from app.api.state import AppState, PendingDecision


def _resolve_window_transaction_ids(candidate: dict[str, Any], transactions_df: pd.DataFrame) -> list[str]:
    """Same logic as app.evaluation.runner._resolve_window_transaction_ids
    -- real detector candidates don't carry affected_transaction_ids, so
    resolve them via the same segment+window logic the retrieval layer
    already uses. Kept identical rather than imported because the
    original is a module-private helper (leading underscore); if it's
    made public later this should just import it instead of duplicating.
    """
    segment_mask = resolve_segment_mask(transactions_df, candidate.get("affected_segment") or {})
    window_start = pd.Timestamp(candidate["window_start"])
    window_end = pd.Timestamp(candidate["window_end"])
    ts = transactions_df["timestamp"]
    if ts.dt.tz is not None and window_start.tzinfo is None:
        window_start = window_start.tz_localize(ts.dt.tz)
        window_end = window_end.tz_localize(ts.dt.tz)
    window_mask = (ts >= window_start) & (ts <= window_end)
    return transactions_df.loc[segment_mask & window_mask, "transaction_id"].tolist()


def run_pipeline_for_dataset(
    state: AppState,
    transactions_df: pd.DataFrame,
    candidate_incidents: list[dict[str, Any]] | None = None,
    ground_truth_by_incident_id: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Run detection (if candidates not already supplied) -> retrieval ->
    agent -> policy -> executor for every candidate, against `state`'s
    shared ledger/store. Appends to state.audit_store and populates
    state.pending for any incident that comes out escalated.

    Does not clear existing audit_store/pending -- callers that want a
    clean slate (e.g. a fresh dataset upload replacing the active one)
    should build a fresh AppState, not reuse this to "re-run" the same
    incidents (see app/api/state.py's docstring on why a single
    long-lived ledger matters).
    """
    transactions_by_id = {
        row["transaction_id"]: row for row in transactions_df.to_dict(orient="records")
    }

    if candidate_incidents is None:
        candidate_incidents = detect_incidents(transactions_df, DEFAULT_CONFIG)

    ground_truth_by_incident_id = ground_truth_by_incident_id or {}

    for candidate in candidate_incidents:
        incident_id = candidate["incident_id"]

        # retrieve_evidence looks up the candidate incident itself (by id,
        # from the on-disk detection output) rather than taking our
        # `candidate` dict directly -- fine for today's seeded-dataset-only
        # scope, since detect_incidents() and this candidate list both
        # ultimately come from the same synthetic transactions.csv. This is
        # a real constraint to revisit when upload (item 2) lands: uploaded
        # data's candidates won't be on disk at CANDIDATE_INCIDENTS_PATH,
        # so retrieve_evidence (or its structured-evidence computation)
        # will need a variant that accepts the candidate dict in-memory.
        evidence = retrieve_evidence(incident_id=incident_id, transactions=transactions_df)
        structured = evidence.get("structured_evidence") or []
        unstructured = evidence.get("unstructured_evidence") or []

        agent_input = AgentInput(
            incident=candidate,
            structured_evidence=structured,
            unstructured_evidence=unstructured,
            allowed_actions=list(ALL_ACTIONS),
            merchant_policies={},
        )
        agent_result = investigate_incident(agent_input)

        window_transaction_ids = _resolve_window_transaction_ids(candidate, transactions_df)
        window_transactions = [
            transactions_by_id[tid] for tid in window_transaction_ids if tid in transactions_by_id
        ]

        policy_decision = evaluate_policy(
            recommended_action=agent_result.output.recommended_action,
            incident=candidate,
            transactions=window_transactions,
            confidence=agent_result.output.confidence,
            revenue_at_risk=agent_result.output.revenue_at_risk,
            ledger=state.ledger,
            merchant_policies={},
        )
        action_record = execute_action(
            requested_action=agent_result.output.recommended_action,
            decision=policy_decision,
            incident=candidate,
            transactions=window_transactions,
            ledger=state.ledger,
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
        state.audit_store.add(record)

        if action_record.execution_status == EXECUTION_NOT_EXECUTED_ESCALATED:
            state.add_pending(
                PendingDecision(
                    incident_id=incident_id,
                    requested_action=agent_result.output.recommended_action,
                    policy_decision=policy_decision,
                    incident=candidate,
                    transactions=window_transactions,
                    audit_record_id=record.record_id,
                )
            )


def seed_from_synthetic_dataset(state: AppState) -> None:
    """Run the pipeline once against the seeded synthetic dataset
    (app/data/synthetic/) -- this is the default state a fresh server
    should be in before any real-time approve/reject or upload happens,
    per today's scope ("fall back to seeded dataset if nothing
    uploaded").
    """
    transactions_df = load_transactions()
    ground_truth_list = load_incidents_list()
    ground_truth_by_id = {gt["incident_id"]: gt for gt in ground_truth_list}
    run_pipeline_for_dataset(
        state,
        transactions_df,
        ground_truth_by_incident_id=ground_truth_by_id,
    )
    state.active_dataset_label = "seeded synthetic dataset"
