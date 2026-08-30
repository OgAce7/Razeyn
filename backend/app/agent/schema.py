"""
Input/output schema for the investigation agent.

`AgentInput` is what the caller assembles (typically from the detection
engine's candidate incident + the retrieval layer's evidence bundle) and
passes in. `AgentOutput` is the strict structured JSON contract the agent
must always return — whether from a successful model call or from one of
the deterministic fallback paths in investigate.py (missing evidence, API
failure, malformed model output). Callers should be able to rely on
getting a valid `AgentOutput` every time, never a raw exception from a
model-side problem.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agent.actions import ALL_ACTIONS


class AgentInput(BaseModel):
    """Everything the agent is given to work with for one investigation."""

    incident: dict[str, Any]
    """The candidate incident from the detection engine (app/detection/) —
    e.g. incident_id, affected_dimension, affected_segment, severity,
    degradation_percentage, transaction_count, revenue_affected, window."""

    structured_evidence: list[dict[str, Any]] = Field(default_factory=list)
    """From the retrieval layer (app/retrieval/structured.py) — computed
    numbers, never LLM-generated. Each item has evidence_id/evidence_type/
    source/data/relevance_score/timestamp."""

    unstructured_evidence: list[dict[str, Any]] = Field(default_factory=list)
    """From the retrieval layer (app/retrieval/vector_store.py) — retrieved
    document passages. Each item has evidence_id/evidence_type/source/
    text/relevance_score/timestamp."""

    transaction_context: dict[str, Any] | None = None
    """Optional extra context the caller wants to surface directly (e.g. a
    handful of representative affected transactions/customers), separate
    from the general evidence bundle."""

    allowed_actions: list[str] = Field(default_factory=lambda: list(ALL_ACTIONS))
    """The finite set of actions usable for THIS call. Supplied by the
    caller (ultimately the not-yet-built policy engine) — this module
    does not decide eligibility itself, only enforces the model stays
    inside whatever list it's given."""

    merchant_policies: dict[str, Any] = Field(default_factory=dict)
    """Arbitrary merchant-specific constraints/preferences the caller wants
    respected (e.g. {"max_auto_retry_amount": 5000, "notify_only": true}).
    Passed through to the prompt as context; not interpreted or enforced
    by this module (that's the policy engine's job)."""

    @field_validator("allowed_actions")
    @classmethod
    def _non_empty_allowed_actions(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("allowed_actions must not be empty")
        return v


class AgentOutput(BaseModel):
    """Strict structured output contract. All fields are required — a
    model response (or fallback) missing any of these, or with a field of
    the wrong type, fails validation and is treated as malformed output."""

    diagnosis: str
    observations: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    revenue_at_risk: float = 0.0
    recommended_action: str
    reason: str
    confidence: float
    stop_condition: str
    escalation_required: bool

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        # Clamp rather than reject — an out-of-range-but-numeric confidence
        # (e.g. the model says 1.2) shouldn't blow up the whole response
        # when clamping preserves the intent perfectly well. A genuinely
        # wrong *type* (e.g. "high") still fails validation, which is what
        # we want to treat as malformed output.
        return max(0.0, min(1.0, float(v)))

    def model_dump_json_strict(self) -> str:
        return self.model_dump_json(indent=2)
