"""
Deterministic guardrails applied to every agent output, model-generated
or fallback, before it's returned to the caller.

This is the actual enforcement mechanism for "the model must never invent
evidence / invent amounts / bypass policy" -- the prompt (prompt.py) asks
the model nicely; this module does not trust that it complied. Every
function here is plain Python operating on data the caller already gave
us (AgentInput) plus whatever the model returned -- no LLM calls.

Order of operations in `enforce_guardrails`:
  1. Evidence IDs the model cited are filtered down to only those that
     were actually shown to it (invented/hallucinated IDs are dropped,
     not trusted).
  2. revenue_at_risk is FORCE-OVERWRITTEN with a value computed directly
     from the supplied evidence -- never the model's own number, even if
     it happened to match. See `_deterministic_revenue_at_risk`.
  3. recommended_action is checked against the caller-supplied
     allowed_actions list; if it's not in that list, it's forced to
     ESCALATE regardless of anything else in the response.
  4. confidence is checked against two thresholds; below the lower one,
     the action is forced to ESCALATE; below the higher one,
     escalation_required is forced True even if the action is left alone.
  5. escalation_required is forced True whenever recommended_action ends
     up being ESCALATE, for internal consistency.

Every violation found is recorded in the returned GuardrailResult so
callers/tests can inspect exactly what was corrected and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.actions import ESCALATE
from app.agent.schema import AgentInput, AgentOutput

# Below this, the recommended action itself is overridden to ESCALATE --
# the model was not confident enough to trust its own action choice.
HARD_ESCALATE_CONFIDENCE = 0.2

# Below this (but at/above the hard floor), the action is left as-is but
# escalation_required is forced True -- a human should sanity-check it
# before it's acted on, even though the agent isn't so unsure that its
# recommendation should be discarded outright.
SOFT_REVIEW_CONFIDENCE = 0.4


@dataclass
class GuardrailResult:
    output: AgentOutput
    violations: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.violations


def _all_evidence_ids(agent_input: AgentInput) -> set[str]:
    ids = set()
    for item in agent_input.structured_evidence:
        if item.get("evidence_id"):
            ids.add(item["evidence_id"])
    for item in agent_input.unstructured_evidence:
        if item.get("evidence_id"):
            ids.add(item["evidence_id"])
    return ids


def _deterministic_revenue_at_risk(agent_input: AgentInput) -> tuple[float, bool]:
    """The ONLY source of truth for revenue_at_risk. Returns (value, found).

    Preference order:
      1. A structured evidence item of type "revenue_impact" -> data.revenue_affected
         (this is the retrieval layer's own computed figure -- see
         app/retrieval/structured.py -- itself computed directly from
         transaction data, never from a model).
      2. The incident dict's own "revenue_affected" field, if present
         (the detection engine already computes this -- see
         app/detection/detector.py).
      3. Not found -> 0.0, found=False -- callers should treat this as
         "revenue impact could not be determined from supplied evidence"
         rather than silently reporting zero as if it were a real figure.
    """
    for item in agent_input.structured_evidence:
        if item.get("evidence_type") == "revenue_impact":
            data = item.get("data") or {}
            if "revenue_affected" in data:
                return float(data["revenue_affected"]), True

    if "revenue_affected" in agent_input.incident:
        return float(agent_input.incident["revenue_affected"]), True

    return 0.0, False


def enforce_guardrails(output: AgentOutput, agent_input: AgentInput) -> GuardrailResult:
    violations: list[str] = []
    data = output.model_dump()

    # 1. Evidence ID filtering
    valid_ids = _all_evidence_ids(agent_input)
    cited = data["evidence_ids"]
    filtered = [eid for eid in cited if eid in valid_ids]
    if len(filtered) != len(cited):
        dropped = sorted(set(cited) - set(filtered))
        violations.append(
            f"Model cited evidence_id(s) not present in supplied evidence, dropped: {dropped}"
        )
    data["evidence_ids"] = filtered

    # 2. Revenue figure -- always overwritten from a deterministic source
    revenue_value, revenue_found = _deterministic_revenue_at_risk(agent_input)
    if revenue_found and abs(data["revenue_at_risk"] - revenue_value) > 0.01:
        violations.append(
            f"Model's revenue_at_risk ({data['revenue_at_risk']}) did not match the "
            f"deterministic figure from evidence ({revenue_value}); overwritten."
        )
    elif not revenue_found and data["revenue_at_risk"] != 0.0:
        violations.append(
            f"Model reported revenue_at_risk={data['revenue_at_risk']} but no revenue "
            "figure was present in supplied evidence; this looks invented and was zeroed out."
        )
    data["revenue_at_risk"] = revenue_value if revenue_found else 0.0

    # 3. Action must be in the allowed set for this call
    if data["recommended_action"] not in agent_input.allowed_actions:
        violations.append(
            f"Model recommended {data['recommended_action']!r}, which is not in the "
            f"allowed_actions for this call ({agent_input.allowed_actions}); overridden to ESCALATE."
        )
        data["recommended_action"] = ESCALATE

    # 4. Confidence thresholds
    if data["confidence"] < HARD_ESCALATE_CONFIDENCE and data["recommended_action"] != ESCALATE:
        violations.append(
            f"Confidence {data['confidence']} is below the hard escalation floor "
            f"({HARD_ESCALATE_CONFIDENCE}); action overridden to ESCALATE."
        )
        data["recommended_action"] = ESCALATE
    elif data["confidence"] < SOFT_REVIEW_CONFIDENCE and not data["escalation_required"]:
        violations.append(
            f"Confidence {data['confidence']} is below the review threshold "
            f"({SOFT_REVIEW_CONFIDENCE}); escalation_required forced True."
        )
        data["escalation_required"] = True

    # 5. Internal consistency
    if data["recommended_action"] == ESCALATE and not data["escalation_required"]:
        data["escalation_required"] = True

    return GuardrailResult(output=AgentOutput(**data), violations=violations)
