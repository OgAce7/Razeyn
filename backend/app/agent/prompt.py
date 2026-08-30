"""
Prompt construction for the investigation agent.

Design notes (see docs/agent.md for the full write-up):

1. **Structured output via forced tool use, not text parsing.** The model
   is given a single tool (`submit_investigation`) whose input schema
   mirrors AgentOutput exactly, and `tool_choice` forces it to call that
   tool. This is far more reliable than asking for JSON in prose and
   parsing it — no markdown fences to strip, no "here's the JSON:"
   preamble to work around, and the API enforces the argument shape at
   the schema level before we even see it.

2. **Hard rules are stated explicitly and repeated in both the system
   prompt and inline in the user prompt's evidence section**, because the
   things being guarded against (inventing evidence, inventing amounts,
   inventing external causes, bypassing policy, executing actions) are
   exactly the failure modes a capable model can slip into under
   pressure to be "helpful" or fill gaps in incomplete evidence.

3. **Evidence is presented with its exact evidence_id** so the model has
   no excuse to invent one, and is told explicitly that any evidence_id
   in its output must be copied verbatim from what's shown.

4. **The prompt never asks the model to compute revenue** — it's told to
   find and copy a revenue figure from the supplied evidence if one
   exists, and to say so plainly if none does. The actual value used in
   the final response is still overwritten deterministically by
   app/agent/guardrails.py regardless of what the model outputs; the
   prompt instruction is a first line of defense, not the enforcement
   mechanism.
"""

from __future__ import annotations

import json

from app.agent.schema import AgentInput

SYSTEM_PROMPT = """You are a payment incident investigation agent for Revenue Incident Responder, a payments platform's automated incident response system.

Your job is to investigate ONE detected payment-degradation incident using ONLY the evidence you are given, and recommend ONE bounded recovery action.

You MUST follow these rules without exception:

1. Use ONLY the evidence provided to you. Never invent, assume, or infer the existence of evidence you were not given -- including transaction amounts, customer details, bank/provider outages, or root causes not stated in the evidence.
2. Clearly separate OBSERVATIONS (facts directly stated in the supplied evidence) from INFERENCES (your reasoning connecting those facts to a likely cause). An inference is allowed; a fact you made up is not.
3. Every entry in "evidence_ids" must be an evidence_id copied EXACTLY from the evidence shown to you. Do not reference evidence that was not shown.
4. Any revenue figure you mention must come directly from a supplied evidence item's data (never estimate, extrapolate, or round to a "nicer" number). If no revenue figure is present in the evidence, say so explicitly rather than guessing.
5. You may recommend ONLY ONE action, and it MUST be one of the "allowed_actions" you are given for this call. Never recommend an action outside that list, even if you think a different action would be better -- if none of the allowed actions seem appropriate, recommend ESCALATE.
6. You do not execute anything. You only recommend. Never phrase your output as though an action has already been taken.
7. Respect any merchant_policies you are given as hard constraints on your recommendation, not suggestions.
8. If the evidence is ambiguous, contradictory, or insufficient to support a confident diagnosis, say so directly, lower your confidence score accordingly, and prefer WAIT_AND_REASSESS or ESCALATE over guessing.
9. State a clear stop_condition: the specific circumstance under which this response should not be acted on further without human review (e.g. "if failure rate does not drop within 2 hours of retry" or "if evidence is later found to be incomplete").

You MUST respond by calling the submit_investigation tool exactly once, with all fields filled in. Do not respond with plain text."""


TOOL_SCHEMA = {
    "name": "submit_investigation",
    "description": (
        "Submit the structured investigation and recovery-decision result for this incident. "
        "This is the only way to respond -- you must call this tool exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "diagnosis": {
                "type": "string",
                "description": "1-3 sentence summary of what is happening and the affected segment.",
            },
            "observations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Facts stated directly in the supplied evidence. No interpretation.",
            },
            "inferences": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Your reasoning connecting the observations to a likely cause. Clearly reasoning, not stated fact.",
            },
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "evidence_id values copied EXACTLY from the evidence shown to you that support this diagnosis.",
            },
            "revenue_at_risk": {
                "type": "number",
                "description": "A revenue figure copied directly from a supplied evidence item's data. 0 if no such figure was supplied.",
            },
            "recommended_action": {
                "type": "string",
                "description": "Exactly one action, copied verbatim from the allowed_actions list given to you.",
            },
            "reason": {
                "type": "string",
                "description": "Why this specific action was selected over the alternatives, referencing the evidence/policies.",
            },
            "confidence": {
                "type": "number",
                "description": "Your confidence in this diagnosis and recommendation, from 0.0 (no confidence) to 1.0 (certain).",
            },
            "stop_condition": {
                "type": "string",
                "description": "The specific circumstance under which this recommendation should not be acted on further without human review.",
            },
            "escalation_required": {
                "type": "boolean",
                "description": "True if a human should review this before any action is taken.",
            },
        },
        "required": [
            "diagnosis",
            "observations",
            "inferences",
            "evidence_ids",
            "revenue_at_risk",
            "recommended_action",
            "reason",
            "confidence",
            "stop_condition",
            "escalation_required",
        ],
    },
}


def _format_evidence(evidence: list[dict], kind: str) -> str:
    if not evidence:
        return f"(no {kind} evidence supplied)"
    lines = []
    for item in evidence:
        lines.append(json.dumps(item, indent=2, default=str))
    return "\n\n".join(lines)


def build_user_prompt(agent_input: AgentInput) -> str:
    incident_block = json.dumps(agent_input.incident, indent=2, default=str)
    structured_block = _format_evidence(agent_input.structured_evidence, "structured")
    unstructured_block = _format_evidence(agent_input.unstructured_evidence, "unstructured")
    context_block = (
        json.dumps(agent_input.transaction_context, indent=2, default=str)
        if agent_input.transaction_context
        else "(no additional transaction/customer context supplied)"
    )
    policies_block = (
        json.dumps(agent_input.merchant_policies, indent=2, default=str)
        if agent_input.merchant_policies
        else "(no merchant-specific policies supplied for this call)"
    )
    allowed_actions_block = "\n".join(f"- {a}" for a in agent_input.allowed_actions)

    all_evidence_ids = [e.get("evidence_id") for e in agent_input.structured_evidence] + [
        e.get("evidence_id") for e in agent_input.unstructured_evidence
    ]

    return f"""## Detected Incident

{incident_block}

## Structured Evidence
(computed directly from transaction data -- treat every number here as ground truth)

{structured_block}

## Unstructured Evidence
(retrieved document passages -- treat as reported observations from their source, not verified fact)

{unstructured_block}

## Additional Transaction/Customer Context

{context_block}

## Allowed Recovery Actions for this call
(you may recommend ONLY one of these -- no exceptions)

{allowed_actions_block}

## Merchant Recovery Policies for this call
(treat as hard constraints)

{policies_block}

## Valid evidence_id values for this call
(your evidence_ids output must be a subset of exactly these -- copy verbatim)

{json.dumps(all_evidence_ids)}

Investigate this incident now and call submit_investigation with your result."""


def build_prompts(agent_input: AgentInput) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    return SYSTEM_PROMPT, build_user_prompt(agent_input)
