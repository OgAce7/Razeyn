"""
Renders an `EvaluationReport` (app/evaluation/metrics.py) as plain
Markdown text -- NOT a dashboard, NOT a UI, just a deterministic string
built from the report's own numbers via simple f-string formatting. This
satisfies "provide an example evaluation report" without touching the
"do not build the dashboard" boundary: nothing here renders HTML, serves
a page, or depends on any frontend.
"""

from __future__ import annotations

from app.evaluation.metrics import EvaluationReport


def _fmt_money(value: float) -> str:
    return f"₹{value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _fmt_num(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:,.2f}{suffix}"


def render_markdown_report(report: EvaluationReport) -> str:
    d, dg, rev, act, saf = (
        report.detection,
        report.diagnosis,
        report.revenue,
        report.actions,
        report.safety,
    )

    lines: list[str] = []
    lines.append("# Revenue Incident Responder -- Evaluation Report")
    lines.append("")
    lines.append(f"Generated: {report.generated_at}")
    lines.append(f"Incidents evaluated: {report.record_count}")
    lines.append("")

    lines.append("## Detection")
    lines.append(f"- Incidents detected: {d.incidents_detected}")
    lines.append(f"- Evaluated against ground truth: {d.evaluated_count}")
    lines.append(f"- True positives: {d.true_positive_count}")
    lines.append(f"- False positives: {d.false_positive_count}")
    lines.append(f"- Precision: {_fmt_pct(d.precision)}")
    lines.append(
        f"- Mean detection latency (from incident end): "
        f"{_fmt_num(d.mean_detection_latency_seconds, 's')}"
    )
    lines.append(
        f"- Mean detection latency (from incident start): "
        f"{_fmt_num(d.mean_detection_latency_seconds_from_window_start, 's')}"
    )
    lines.append("")

    lines.append("## Diagnosis")
    lines.append(f"- Evaluated (agent ran successfully + ground truth available): {dg.evaluated_count}")
    lines.append(
        f"- Affected-segment match rate: {_fmt_pct(dg.segment_match_rate)} "
        f"({dg.segment_match_count} matched)"
    )
    lines.append(
        f"- Evidence-supported diagnosis rate: {_fmt_pct(dg.evidence_supported_rate)} "
        f"({dg.evidence_supported_count} / {dg.evaluated_count})"
    )
    lines.append("")

    lines.append("## Revenue")
    lines.append(f"- Total revenue exposed (ground truth): {_fmt_money(rev.total_revenue_exposed)}")
    lines.append(f"- Total revenue at risk (agent, guardrail-enforced): {_fmt_money(rev.total_revenue_at_risk)}")
    lines.append(f"- Total revenue recovered (agent pipeline): {_fmt_money(rev.total_revenue_recovered)}")
    lines.append(f"- Recovery rate (recovered / at risk): {_fmt_pct(rev.recovery_rate)}")
    if rev.baseline_revenue_recovered is not None:
        lines.append(f"- Baseline (fixed retry rule) revenue recovered: {_fmt_money(rev.baseline_revenue_recovered)}")
        lines.append(f"- Recovery uplift vs baseline: {_fmt_money(rev.recovery_uplift_vs_baseline)}")
        lines.append(f"- Recovery uplift vs baseline (%): {_fmt_num(rev.recovery_uplift_vs_baseline_pct, '%')}")
    else:
        lines.append("- Baseline comparison: not run")
    lines.append("")

    lines.append("## Actions")
    lines.append(f"- Actions attempted (executed/simulated): {act.actions_attempted}")
    lines.append(f"- Actions approved: {act.actions_approved}")
    lines.append(f"- Actions rejected: {act.actions_rejected}")
    lines.append(f"- Successful transaction-level actions: {act.actions_successful}")
    lines.append(f"- Stopped (no-op, benign): {act.actions_stopped}")
    lines.append(f"- Escalated to human: {act.actions_escalated}")
    lines.append(f"- Success rate of attempted transactions: {_fmt_pct(act.success_rate_of_attempted)}")
    lines.append("")

    lines.append("## Safety")
    lines.append(f"- Policy violations prevented (failed checks caught): {saf.policy_violations_prevented}")
    lines.append(f"- Guardrail corrections to agent output: {saf.guardrail_corrections}")
    lines.append(f"- Unnecessary interventions (acted on a false positive): {saf.unnecessary_interventions}")
    lines.append(f"- False-positive cost (revenue at risk on false positives): {_fmt_money(saf.false_positive_cost)}")
    lines.append(f"- Evaluated against ground truth: {saf.evaluated_count}")
    lines.append("")

    return "\n".join(lines)
