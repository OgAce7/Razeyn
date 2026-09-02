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

Also runs against uploaded datasets (see run_pipeline_for_dataset's
`transactions_df` / `candidate_incidents` parameters and
app/api/datasets.py), which is why evidence retrieval goes through
retrieve_evidence_for_incident (candidate dict in memory) rather than
retrieve_evidence (candidate looked up on disk by id) -- an uploaded
dataset's candidates are never written to
app/data/synthetic/candidate_incidents.json.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
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
from app.retrieval.bundle import retrieve_evidence_for_incident

from app.api.state import AppState, DatasetInfo, PendingDecision


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
    CURRENT ledger/store. Appends to state.audit_store and populates
    state.pending for any incident that comes out escalated.

    Callers (seed_from_synthetic_dataset, run_uploaded_dataset) are
    responsible for calling state.swap_dataset() BEFORE this, so it
    always runs against a freshly-cleared ledger/store for the dataset
    being processed -- this function itself does not clear anything, so
    calling it twice in a row without an intervening swap_dataset() would
    append to (not replace) the previous run's records.
    """
    transactions_by_id = {
        row["transaction_id"]: row for row in transactions_df.to_dict(orient="records")
    }

    if candidate_incidents is None:
        candidate_incidents = detect_incidents(transactions_df, DEFAULT_CONFIG)

    ground_truth_by_incident_id = ground_truth_by_incident_id or {}

    for candidate in candidate_incidents:
        incident_id = candidate["incident_id"]

        # retrieve_evidence_for_incident takes the candidate dict directly
        # (no on-disk lookup) -- this is what makes evidence retrieval
        # work for both the seeded dataset and, now, uploaded datasets
        # whose candidates never touch disk. See app/retrieval/bundle.py.
        evidence = retrieve_evidence_for_incident(candidate, transactions_df)
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

    candidate_incidents = detect_incidents(transactions_df, DEFAULT_CONFIG)

    info = DatasetInfo(
        dataset_id="seeded",
        label="Seeded synthetic dataset",
        kind="seeded",
        row_count=len(transactions_df),
        candidate_count=len(candidate_incidents),
    )
    state.swap_dataset(info)

    run_pipeline_for_dataset(
        state,
        transactions_df,
        candidate_incidents=candidate_incidents,
        ground_truth_by_incident_id=ground_truth_by_id,
    )


def run_uploaded_dataset(
    state: AppState,
    transactions_df: pd.DataFrame,
    original_filename: str | None = None,
) -> DatasetInfo:
    """Run detection + the full pipeline against an uploaded, validated
    transactions DataFrame, and make it the active dataset -- REPLACING
    whatever was active before (seeded dataset or a previous upload), not
    merging with it. See AppState.swap_dataset for why a full swap is the
    right semantics here (one live dataset in the dashboard at a time).

    No ground truth is available for uploaded data (there's no
    incidents.json for it), so AuditRecords from this run simply have
    ground_truth=None -- detection-accuracy metrics that require it are
    skipped rather than faked, same as any live (non-synthetic)
    deployment per app/audit/schema.py's own design note.

    Returns the DatasetInfo describing the run, so the API layer can
    report row/candidate counts back to the caller.
    """
    candidate_incidents = detect_incidents(transactions_df, DEFAULT_CONFIG)

    dataset_id = f"upload_{uuid.uuid4().hex[:10]}"
    info = DatasetInfo(
        dataset_id=dataset_id,
        label=original_filename or f"Uploaded dataset ({dataset_id})",
        kind="uploaded",
        row_count=len(transactions_df),
        candidate_count=len(candidate_incidents),
        uploaded_at=datetime.now(timezone.utc).isoformat(),
        original_filename=original_filename,
    )
    state.swap_dataset(info)

    run_pipeline_for_dataset(
        state,
        transactions_df,
        candidate_incidents=candidate_incidents,
        ground_truth_by_incident_id=None,
    )
    return info
