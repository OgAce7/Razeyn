"""
Evaluation, metrics, and batch-comparison layer.

Deliberately built to import and orchestrate the existing detection,
retrieval, agent, and policy/executor modules -- not to reimplement or
modify any of them. See docs/evaluation.md for the full write-up.

Files:
- baseline.py   Fixed, deterministic retry-rule strategy for comparison
                against the AI agent (no evidence, no diagnosis, no policy)
- runner.py     Orchestrates agent -> policy -> executor -> audit for a
                batch of candidate incidents, plus the baseline comparison
- metrics.py    Pure functions computing detection/diagnosis/revenue/
                action/safety metrics from stored AuditRecords
- report.py     Renders an EvaluationReport as Markdown text (no dashboard)
"""

from app.evaluation.baseline import BaselineOutcome, run_baseline
from app.evaluation.metrics import EvaluationReport, evaluate_batch
from app.evaluation.report import render_markdown_report
from app.evaluation.runner import run_batch_evaluation

__all__ = [
    "BaselineOutcome",
    "run_baseline",
    "EvaluationReport",
    "evaluate_batch",
    "render_markdown_report",
    "run_batch_evaluation",
]
