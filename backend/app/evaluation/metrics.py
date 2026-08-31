"""
Deterministic metric calculations.

Every function here takes already-stored data (a list of `AuditRecord`s,
optionally ground-truth incidents and/or `BaselineOutcome`s) and returns
plain numbers/dicts. Nothing here calls the AI agent, the detector, the
policy engine, or the executor -- this module only ever reads what those
already produced and stored. That's what "reproducible from stored
transaction/action/outcome data" means in practice: run this against the
same `AuditRecord` list twice, get the same numbers twice, with no
network call and no randomness anywhere in the call path.

Metrics are grouped exactly as specified: detection, diagnosis, revenue,
actions, safety. `evaluate_batch` composes all of them into one report.
Every individual metric function is also exported and independently
testable/callable, since "detection latency" or "recovery rate" alone are
each meaningful outside the full report.

Money handling: every monetary figure this module returns is a sum or
ratio of numbers already present in the `AuditRecord`s -- `revenue_at_risk`
(guardrail-enforced deterministic, see app/agent/guardrails.py),
`revenue_exposed` (ground truth), `expected_revenue_recovery` (policy
engine), and `amount` summed from `ActionRecord.actual_result.per_transaction`
entries via the transactions the caller supplies for revenue_recovered.
This module never estimates, models, or asks a model for a dollar
figure; every function is pure arithmetic over stored data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.audit.schema import AuditRecord
from app.evaluation.baseline import BaselineOutcome

# ---------------------------------------------------------------------------
# Detection metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectionMetrics:
    incidents_detected: int
    """Count of AuditRecords -- one per candidate incident that reached
    the pipeline. Not a ground-truth-dependent number; this counts
    whatever the detector flagged, true or false."""

    true_positive_count: int
    """Detected candidates whose ground truth says `is_true_incident=True`.
    Only computed over records that HAVE ground truth -- see `evaluated_count`."""

    false_positive_count: int
    """Detected candidates whose ground truth says `is_true_incident=False`
    (i.e. the detector flagged the benign-fluctuation case)."""

    evaluated_count: int
    """How many records had ground truth available at all. Detection
    accuracy metrics below are only meaningful over this subset --
    records without ground truth (a live/production run) are excluded,
    not counted as either TP or FP."""

    precision: float | None
    """true_positive_count / incidents_with_ground_truth_that_were_flagged.
    None if evaluated_count == 0 (undefined, not zero)."""

    mean_detection_latency_seconds: float | None
    """Mean of (detection_timestamp - ground_truth.end_time) across
    records with ground truth -- how long after an incident actually
    ENDED the detector flagged it. (The detector runs after the fact
    over historical data in this project's batch setup, per
    docs/detection.md's "leave-window-out" method over the whole
    dataset, so latency is measured from window end, not window start;
    a streaming deployment would instead want latency from window
    start, which this function also returns for completeness.)
    None if no record has ground truth with parseable timestamps."""

    mean_detection_latency_seconds_from_window_start: float | None


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def compute_detection_metrics(records: list[AuditRecord]) -> DetectionMetrics:
    incidents_detected = len(records)
    with_gt = [r for r in records if r.ground_truth is not None]
    evaluated_count = len(with_gt)

    true_positive_count = sum(1 for r in with_gt if r.ground_truth.is_true_incident)
    false_positive_count = evaluated_count - true_positive_count

    precision = (true_positive_count / evaluated_count) if evaluated_count > 0 else None

    latencies_from_end: list[float] = []
    latencies_from_start: list[float] = []
    for r in with_gt:
        detected_at = _parse_iso(r.detection.detection_timestamp)
        window_end = _parse_iso(r.ground_truth.end_time)
        window_start = _parse_iso(r.ground_truth.start_time)
        if detected_at is not None and window_end is not None:
            latencies_from_end.append((detected_at - window_end).total_seconds())
        if detected_at is not None and window_start is not None:
            latencies_from_start.append((detected_at - window_start).total_seconds())

    mean_latency_end = (
        round(sum(latencies_from_end) / len(latencies_from_end), 3) if latencies_from_end else None
    )
    mean_latency_start = (
        round(sum(latencies_from_start) / len(latencies_from_start), 3) if latencies_from_start else None
    )

    return DetectionMetrics(
        incidents_detected=incidents_detected,
        true_positive_count=true_positive_count,
        false_positive_count=false_positive_count,
        evaluated_count=evaluated_count,
        precision=precision,
        mean_detection_latency_seconds=mean_latency_end,
        mean_detection_latency_seconds_from_window_start=mean_latency_start,
    )


# ---------------------------------------------------------------------------
# Diagnosis metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosisMetrics:
    evaluated_count: int
    """Records with ground truth AND a non-fallback ("ok") agent status --
    diagnosis accuracy is only meaningful when the model actually ran."""

    segment_match_count: int
    """Count where the detector's `affected_segment` (what the agent was
    told about, and what it diagnosed against) exactly matches the
    ground-truth incident's `affected_segment` dict. This measures the
    DETECTOR's segment identification (the agent only ever sees the
    detector's segment, never ground truth) -- see `note` field below."""

    segment_match_rate: float | None

    evidence_supported_count: int
    """Records where the agent's final (post-guardrail) `evidence_ids` is
    non-empty -- i.e. the diagnosis is backed by at least one concrete,
    verified-real evidence item, not asserted with zero citations. This
    is a floor, not a quality judgment: guardrails already strip any
    invented ids (see app/agent/guardrails.py), so a non-empty list here
    is guaranteed to be real evidence the agent was actually shown."""

    evidence_supported_rate: float | None

    note: str = (
        "segment_match compares the DETECTOR's affected_segment (what the agent "
        "was shown and diagnosed against) to the ground-truth injected segment -- "
        "the agent itself is never given ground truth to compare against (see "
        "docs/agent.md). This is a pipeline-convergence metric ('did detection "
        "correctly localize the incident'), not a judgment of the agent's own "
        "reasoning quality, which evidence_supported_rate addresses instead."
    )


def compute_diagnosis_metrics(records: list[AuditRecord]) -> DiagnosisMetrics:
    with_gt = [r for r in records if r.ground_truth is not None]
    evaluated = [r for r in with_gt if r.agent_decision.status == "ok"]
    evaluated_count = len(evaluated)

    segment_match_count = sum(
        1
        for r in with_gt
        if r.detection.affected_segment == r.ground_truth.affected_segment
    )
    segment_match_rate = (segment_match_count / len(with_gt)) if with_gt else None

    evidence_supported_count = sum(1 for r in evaluated if len(r.agent_decision.evidence_ids) > 0)
    evidence_supported_rate = (
        (evidence_supported_count / evaluated_count) if evaluated_count > 0 else None
    )

    return DiagnosisMetrics(
        evaluated_count=evaluated_count,
        segment_match_count=segment_match_count,
        segment_match_rate=segment_match_rate,
        evidence_supported_count=evidence_supported_count,
        evidence_supported_rate=evidence_supported_rate,
    )


# ---------------------------------------------------------------------------
# Revenue metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RevenueMetrics:
    total_revenue_exposed: float
    """Sum of ground_truth.revenue_exposed across records with ground
    truth. 0.0 (not None) if no record has ground truth, since "no
    exposure measured" and "zero exposure" are both legitimately zero
    here -- unlike detection latency, there's no divide-by-zero risk."""

    total_revenue_at_risk: float
    """Sum of agent_decision.revenue_at_risk across ALL records (this
    number exists whether or not ground truth is available -- it's the
    guardrail-enforced deterministic figure from app/agent/guardrails.py,
    not a ground-truth comparison)."""

    total_revenue_recovered: float
    """Sum of (amount for each transaction in action_outcome with a
    SUCCESS outcome), computed by the caller-supplied `transactions_by_id`
    lookup and passed in as `revenue_recovered_by_record` -- see
    `compute_revenue_recovered_for_record` below. This module does not
    invent this number; it is summed from the SAME `per_transaction`
    outcomes the executor already recorded (app/policies/executor.py)."""

    recovery_rate: float | None
    """total_revenue_recovered / total_revenue_at_risk. None if
    total_revenue_at_risk is 0 (undefined, not zero -- avoids a
    misleading 0% or 100% when there was nothing to recover)."""

    baseline_revenue_recovered: float | None
    """Sum of BaselineOutcome.revenue_recovered across the supplied
    baseline outcomes, if any were provided. None if no baseline was run."""

    recovery_uplift_vs_baseline: float | None
    """total_revenue_recovered - baseline_revenue_recovered. None if no
    baseline was provided. Can be negative -- this module does not clamp
    it, since a negative uplift (baseline outperforming the agent) is a
    real and reportable outcome, not an error."""

    recovery_uplift_vs_baseline_pct: float | None
    """recovery_uplift_vs_baseline / baseline_revenue_recovered, as a
    percentage. None if no baseline was provided OR baseline recovered
    exactly 0.0 (undefined percentage change from a zero base)."""


def compute_revenue_recovered_for_record(
    record: AuditRecord, transactions_by_id: dict[str, dict[str, Any]]
) -> float:
    """The revenue actually recovered for one AuditRecord: sum of `amount`
    (from the real transaction record -- never from the AI's output, same
    rule the executor itself follows) for every transaction_id in this
    record's action_outcome whose per-transaction result was a SUCCESS.

    This module doesn't have access to the raw `per_transaction` outcome
    list (AuditRecord only stores aggregate attempted/succeeded/failed
    counts -- see app/audit/schema.py ActionOutcomeRef), so it estimates
    per-transaction recovered revenue as:
        (succeeded / attempted) * sum(amount for transaction_ids)
    when attempted > 0, which is exact when transaction amounts are
    homogeneous and an unbiased estimate otherwise. Callers needing an
    EXACT per-transaction figure should instead sum `amount` directly
    from the `ActionRecord.actual_result["per_transaction"]` list before
    it's compressed into an AuditRecord -- see
    `compute_exact_revenue_recovered` for that path.
    """
    outcome = record.action_outcome
    if outcome.attempted == 0 or not outcome.transaction_ids:
        return 0.0
    total_amount = sum(
        float(transactions_by_id[tid]["amount"])
        for tid in outcome.transaction_ids
        if tid in transactions_by_id
    )
    success_fraction = outcome.succeeded / outcome.attempted
    return round(total_amount * success_fraction, 2)


def compute_exact_revenue_recovered(action_record: Any) -> float:
    """Exact recovered-revenue figure computed directly from an
    `ActionRecord`'s own `per_transaction` results (app/policies/ledger.py
    / executor.py) -- use this at the point the ActionRecord is still
    available (e.g. inside the same batch-run loop that builds the
    AuditRecord), before compression. Sums `amount` only for entries
    whose `outcome == "SUCCESS"`; entries without an `amount` key (e.g. a
    NOTIFY_MERCHANT record) contribute 0.0."""
    result = action_record.actual_result or {}
    per_txn = result.get("per_transaction") or []
    return round(
        sum(
            float(item["amount"])
            for item in per_txn
            if item.get("outcome") == "SUCCESS" and "amount" in item
        ),
        2,
    )


def compute_revenue_metrics(
    records: list[AuditRecord],
    revenue_recovered_by_record: dict[str, float],
    baseline_outcomes: list[BaselineOutcome] | None = None,
) -> RevenueMetrics:
    with_gt = [r for r in records if r.ground_truth is not None]
    total_revenue_exposed = round(sum(r.ground_truth.revenue_exposed for r in with_gt), 2)
    total_revenue_at_risk = round(sum(r.agent_decision.revenue_at_risk for r in records), 2)
    total_revenue_recovered = round(
        sum(revenue_recovered_by_record.get(r.record_id, 0.0) for r in records), 2
    )

    recovery_rate = (
        round(total_revenue_recovered / total_revenue_at_risk, 4) if total_revenue_at_risk > 0 else None
    )

    baseline_revenue_recovered = None
    recovery_uplift_vs_baseline = None
    recovery_uplift_vs_baseline_pct = None
    if baseline_outcomes is not None:
        baseline_revenue_recovered = round(sum(b.revenue_recovered for b in baseline_outcomes), 2)
        recovery_uplift_vs_baseline = round(total_revenue_recovered - baseline_revenue_recovered, 2)
        if baseline_revenue_recovered != 0:
            recovery_uplift_vs_baseline_pct = round(
                (recovery_uplift_vs_baseline / baseline_revenue_recovered) * 100, 2
            )

    return RevenueMetrics(
        total_revenue_exposed=total_revenue_exposed,
        total_revenue_at_risk=total_revenue_at_risk,
        total_revenue_recovered=total_revenue_recovered,
        recovery_rate=recovery_rate,
        baseline_revenue_recovered=baseline_revenue_recovered,
        recovery_uplift_vs_baseline=recovery_uplift_vs_baseline,
        recovery_uplift_vs_baseline_pct=recovery_uplift_vs_baseline_pct,
    )


# ---------------------------------------------------------------------------
# Action metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionMetrics:
    actions_attempted: int
    """Count of AuditRecords whose action_outcome.execution_status is
    SIMULATED or EXECUTED (i.e. something actually ran) -- pass-through
    no-ops (STOP/WAIT/NOT_EXECUTED_*) are not "attempted" in this sense."""

    actions_approved: int
    """policy_decision.approved == True, regardless of whether it then
    executed, escalated, or was a no-op pass-through."""

    actions_rejected: int
    """policy_decision.approved == False."""

    actions_successful: int
    """Count of individual transaction-level successes, summed across all
    records' action_outcome.succeeded -- NOT count of records; one
    record can represent many successful transaction-level retries."""

    actions_stopped: int
    """execution_status == NOT_EXECUTED_STOPPED."""

    actions_escalated: int
    """execution_status == NOT_EXECUTED_ESCALATED, OR policy_decision.escalation_required
    True with execution_status NOT_EXECUTED_ESCALATED (kept as one count;
    see `escalated_record_ids` for exactly which)."""

    success_rate_of_attempted: float | None
    """actions_successful / total transaction-level attempts summed
    across attempted records. None if no actions were attempted."""


def compute_action_metrics(records: list[AuditRecord]) -> ActionMetrics:
    attempted_records = [
        r for r in records if r.action_outcome.execution_status in ("SIMULATED", "EXECUTED")
    ]
    actions_attempted = len(attempted_records)
    actions_approved = sum(1 for r in records if r.policy_decision.approved)
    actions_rejected = sum(1 for r in records if not r.policy_decision.approved)
    actions_successful = sum(r.action_outcome.succeeded for r in records)
    actions_stopped = sum(
        1 for r in records if r.action_outcome.execution_status == "NOT_EXECUTED_STOPPED"
    )
    actions_escalated = sum(
        1 for r in records if r.action_outcome.execution_status == "NOT_EXECUTED_ESCALATED"
    )

    total_attempted_transactions = sum(r.action_outcome.attempted for r in attempted_records)
    success_rate_of_attempted = (
        round(actions_successful / total_attempted_transactions, 4)
        if total_attempted_transactions > 0
        else None
    )

    return ActionMetrics(
        actions_attempted=actions_attempted,
        actions_approved=actions_approved,
        actions_rejected=actions_rejected,
        actions_successful=actions_successful,
        actions_stopped=actions_stopped,
        actions_escalated=actions_escalated,
        success_rate_of_attempted=success_rate_of_attempted,
    )


# ---------------------------------------------------------------------------
# Safety metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SafetyMetrics:
    policy_violations_prevented: int
    """Count of individual FAILED policy_checks entries across all
    records (i.e. every time a check caught something and the decision
    trail recorded `passed: False`) -- each one represents a rule that
    would otherwise have let a disallowed action, amount, retry count, or
    contact count through. Counts checks, not records: one record can
    carry multiple failed checks in its trail (see docs/policy_engine.md
    -- "every step appends a PolicyCheckResult... the full trail is
    always returned, not just the first failure" -- so a rejected record's
    single fatal check plus any earlier informational failures are all
    counted)."""

    guardrail_corrections: int
    """Count of individual guardrail_violations strings across all
    records (app/agent/guardrails.py) -- each one is a case where the
    model's raw output was corrected (invented evidence dropped, revenue
    overwritten, disallowed action forced to ESCALATE, confidence-driven
    escalation) before it could reach the policy engine."""

    unnecessary_interventions: int
    """Records with ground truth `is_true_incident=False` (the benign-
    fluctuation case) where the policy decision was nonetheless approved
    for an actionable (money/customer-touching) action -- i.e. the system
    acted on something that, per ground truth, didn't need acting on.
    Only computable over records with ground truth."""

    false_positive_cost: float
    """Sum of revenue_at_risk across those same unnecessary-intervention
    records -- the deterministic dollar figure the system was prepared to
    spend effort/customer-contact on on a false positive. This is a cost
    of ATTENTION, not cash lost (nothing here implies money was actually
    given away; see docs/policy_engine.md -- executed actions are retries/
    notifications, not refunds), but it is what the brief's "false-positive
    cost" metric is: the deterministic revenue figure attached to incidents
    that shouldn't have triggered a response."""

    evaluated_count: int
    """How many records had ground truth available -- unnecessary_interventions
    and false_positive_cost are only meaningful over this subset."""


_ACTIONABLE_EXECUTION_STATUSES = frozenset({"SIMULATED", "EXECUTED", "NOT_EXECUTED_ESCALATED"})
_NON_ACTIONABLE_RECOMMENDED_ACTIONS = frozenset({"STOP", "WAIT_AND_REASSESS"})


def compute_safety_metrics(records: list[AuditRecord]) -> SafetyMetrics:
    policy_violations_prevented = sum(
        1 for r in records for check in r.policy_decision.policy_checks if not check.get("passed", True)
    )
    guardrail_corrections = sum(len(r.agent_decision.guardrail_violations) for r in records)

    with_gt = [r for r in records if r.ground_truth is not None]
    unnecessary = [
        r
        for r in with_gt
        if not r.ground_truth.is_true_incident
        and r.policy_decision.approved
        and r.agent_decision.recommended_action not in _NON_ACTIONABLE_RECOMMENDED_ACTIONS
        and r.agent_decision.recommended_action != "ESCALATE"
    ]
    unnecessary_interventions = len(unnecessary)
    false_positive_cost = round(sum(r.agent_decision.revenue_at_risk for r in unnecessary), 2)

    return SafetyMetrics(
        policy_violations_prevented=policy_violations_prevented,
        guardrail_corrections=guardrail_corrections,
        unnecessary_interventions=unnecessary_interventions,
        false_positive_cost=false_positive_cost,
        evaluated_count=len(with_gt),
    )


# ---------------------------------------------------------------------------
# Composite report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationReport:
    generated_at: str
    record_count: int
    detection: DetectionMetrics
    diagnosis: DiagnosisMetrics
    revenue: RevenueMetrics
    actions: ActionMetrics
    safety: SafetyMetrics

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


def evaluate_batch(
    records: list[AuditRecord],
    revenue_recovered_by_record: dict[str, float],
    baseline_outcomes: list[BaselineOutcome] | None = None,
    generated_at: str | None = None,
) -> EvaluationReport:
    """Compose all five metric groups into one report. Pure function of
    its inputs -- same `records`/`revenue_recovered_by_record`/
    `baseline_outcomes` always produces the same report."""
    from app.audit.schema import now_iso

    return EvaluationReport(
        generated_at=generated_at or now_iso(),
        record_count=len(records),
        detection=compute_detection_metrics(records),
        diagnosis=compute_diagnosis_metrics(records),
        revenue=compute_revenue_metrics(records, revenue_recovered_by_record, baseline_outcomes),
        actions=compute_action_metrics(records),
        safety=compute_safety_metrics(records),
    )
