# AI investigation and recovery-decision agent

This document covers the agent layer only (`backend/app/agent/`). It
consumes a candidate incident (`docs/detection.md`) and an evidence
bundle (`docs/retrieval.md`), and produces one structured investigation +
recovery recommendation. It does not detect incidents, does not decide
which actions are policy-eligible (that's a separate, not-yet-built
policy engine), and does not execute anything (a separate, not-yet-built
action executor).

## Files

| File | Purpose |
|---|---|
| `app/agent/actions.py` | The finite, explicit universe of recovery actions |
| `app/agent/schema.py` | `AgentInput` / `AgentOutput` — strict Pydantic models |
| `app/agent/prompt.py` | System/user prompt construction + the forced tool-use schema |
| `app/agent/client.py` | Claude API wrapper — the only network call in this module |
| `app/agent/guardrails.py` | Deterministic (non-LLM) post-processing enforcement |
| `app/agent/investigate.py` | Orchestration + fallback handling for every failure mode |
| `app/agent/errors.py` | Exception types for each failure mode |
| `backend/tests/test_agent_*.py` | 44 tests across schema, guardrails, client, and orchestration |

## Running it

```bash
cd backend
source .venv/bin/activate
# needs ANTHROPIC_API_KEY set in backend/.env for a real call;
# tests mock the API and need no key/network at all.
python -m pytest tests/test_agent_*.py -v
```

```python
from app.agent.schema import AgentInput
from app.agent.investigate import investigate_incident
from app.retrieval.bundle import retrieve_evidence
from app.retrieval.structured import load_candidate_incident

evidence = retrieve_evidence("cand_00005")
incident = load_candidate_incident("cand_00005")

agent_input = AgentInput(
    incident=incident,
    structured_evidence=evidence["structured_evidence"],
    unstructured_evidence=evidence["unstructured_evidence"],
    allowed_actions=["RETRY_ELIGIBLE_PAYMENTS", "NOTIFY_MERCHANT", "WAIT_AND_REASSESS", "ESCALATE"],
    merchant_policies={"max_auto_retry_amount_inr": 10000, "auto_retry_enabled": True},
)

result = investigate_incident(agent_input)
print(result.status)                        # "ok" | "no_evidence" | "api_error" | "malformed_output"
print(result.output.model_dump_json_strict())
```

## Architecture

```
AgentInput (incident + evidence + allowed_actions + merchant_policies)
        │
        ▼
  missing evidence? ──yes──► fallback AgentOutput (ESCALATE, no model call)
        │ no
        ▼
  build system/user prompt (prompt.py)
        │
        ▼
  call Claude, forced tool-use (client.py) ──API error──► fallback AgentOutput (ESCALATE)
        │ success                                  │
        ▼                                            └─(no tool call)──► fallback AgentOutput (ESCALATE)
  parse into AgentOutput (schema.py) ──validation error──► fallback AgentOutput (ESCALATE)
        │ valid
        ▼
  enforce_guardrails (guardrails.py) — ALWAYS runs, even on fallback paths
    - strip invented evidence_ids
    - overwrite revenue_at_risk from deterministic evidence
    - force ESCALATE if action not in allowed_actions
    - force ESCALATE if confidence < 0.2, force escalation_required if < 0.4
    - force escalation_required=True whenever action is ESCALATE
        │
        ▼
  AgentResult { output: AgentOutput, status, guardrail_violations, error_detail }
```

Every path — success or any of the four failure modes — ends at the same
`enforce_guardrails` step and returns the same `AgentResult` shape. A
caller never needs different handling code for "the model worked" vs.
"the model failed": `result.output` is always a valid `AgentOutput`,
`result.status` tells you which path was taken, and
`result.guardrail_violations` tells you what (if anything) had to be
corrected.

## The ten required behaviors, and where each is enforced

| # | Requirement | Where |
|---|---|---|
| 1 | Summarize what is happening | `AgentOutput.diagnosis` — model-generated, prompted for 1-3 sentences |
| 2 | Identify the affected segment | Part of `diagnosis`; the incident's `affected_segment` is also passed through verbatim in the prompt |
| 3 | Determine likely cause from evidence only | `AgentOutput.inferences` — model-generated, but every `evidence_id` it cites is checked against what was actually supplied (guardrails.py) |
| 4 | Distinguish observation from inference | Separate `observations` / `inferences` fields, both in the schema and explicitly instructed in the system prompt (rule 2) |
| 5 | Revenue impact from deterministic values only | `AgentOutput.revenue_at_risk` is **always overwritten** by `guardrails._deterministic_revenue_at_risk`, regardless of what the model said |
| 6 | Select a bounded recovery action | `AgentOutput.recommended_action` — checked against `AgentInput.allowed_actions`; forced to `ESCALATE` if outside that list |
| 7 | Explain why | `AgentOutput.reason` — model-generated |
| 8 | State confidence | `AgentOutput.confidence` — model-generated, clamped to [0,1], drives the confidence-threshold guardrails |
| 9 | Identify supporting evidence | `AgentOutput.evidence_ids` — filtered to only IDs that were actually supplied |
| 10 | When to stop/escalate | `AgentOutput.stop_condition` (model-generated) + `AgentOutput.escalation_required` (model-generated, but forced `True` by guardrails under several conditions regardless of what the model said) |

## Prompt / system design

**Structured output via forced tool use, not text parsing.** The model is
given exactly one tool, `submit_investigation`, whose `input_schema`
mirrors `AgentOutput` field-for-field, and the API call sets
`tool_choice={"type": "tool", "name": "submit_investigation"}` — the
model cannot respond with plain text or a different tool. This avoids the
usual "ask for JSON, strip markdown fences, hope it's well-formed"
approach entirely. `client._extract_tool_input` still has to handle "the
model didn't call the tool" (raises `MalformedOutputError`) because
`tool_choice` forcing isn't an absolute guarantee across all conditions —
robust handling for that case is required either way.

**Hard rules are explicit and repeated.** The system prompt states nine
numbered rules (no invented evidence, observation/inference separation,
evidence_id must be copied verbatim, revenue must come from evidence,
action must be from the allowed list, never phrase output as already
executed, respect merchant policies as hard constraints, prefer
`WAIT_AND_REASSESS`/`ESCALATE` over guessing under ambiguous evidence,
state a concrete stop condition). The user prompt then restates the
allowed-actions list and the exact valid `evidence_id` values inline,
right where the model will use them — reducing the chance it drifts from
the system prompt's instructions by the time it's deep into the specific
evidence.

**The prompt is not the enforcement mechanism — guardrails.py is.**
Nothing above is trusted. Every rule with a real consequence (evidence
authenticity, revenue accuracy, action legality) is checked and corrected
in `guardrails.py` after the model responds, using only data the caller
supplied. This split — prompt for guidance and quality, deterministic
code for anything that actually matters — is the same principle the
whole project follows (see `README.md`'s "LLM never controls money").

**Why revenue is always overwritten, never just validated.** An earlier
design only *flagged* a mismatched revenue figure as a violation while
still using the model's number if no deterministic source existed. The
final design instead always computes revenue from evidence-or-incident
data and uses that value — full stop. If neither source has a number,
`revenue_at_risk` is `0.0` and a violation is recorded rather than
`0.0` being reported as if it were a confirmed zero-impact reading.

## Guardrail thresholds

| Guardrail | Threshold | Effect |
|---|---|---|
| Evidence ID authenticity | must be in the supplied evidence's IDs | invented IDs silently dropped, violation recorded |
| Revenue figure | n/a — always overwritten | model's number replaced by the deterministic figure |
| Action legality | must be in `AgentInput.allowed_actions` | forced to `ESCALATE`, violation recorded |
| Confidence — hard floor | `< 0.2` | `recommended_action` forced to `ESCALATE` |
| Confidence — soft floor | `< 0.4` (and `>= 0.2`) | `escalation_required` forced `True`, action left as-is |
| Internal consistency | `recommended_action == ESCALATE` | `escalation_required` forced `True` |

These thresholds live in `guardrails.py` as named constants
(`HARD_ESCALATE_CONFIDENCE`, `SOFT_REVIEW_CONFIDENCE`) so they're easy to
find and tune independently of the detection engine's own thresholds
(which are unrelated — see `docs/detection.md`).

## Error handling

| Failure mode | Detected how | Result |
|---|---|---|
| **Missing evidence** | `AgentInput` has both `structured_evidence` and `unstructured_evidence` empty | No API call is made at all. Fallback `AgentOutput` with `recommended_action=ESCALATE`, `confidence=0.0`. `AgentResult.status == "no_evidence"`. |
| **API failure** | `client.call_agent_model` raises `AgentAPIError` (network error, auth error, rate limit, or 5xx after `MAX_RETRIES=2` retries with backoff; 4xx client errors fail fast without retrying) | Fallback `AgentOutput`, same shape as above. `status == "api_error"`, `error_detail` carries the original error message. Revenue is still populated deterministically from evidence if available — a failed model call doesn't erase an already-known number. |
| **Malformed model output** | Either no tool call at all (`MalformedOutputError` from `client.py`), or the tool call's arguments fail `AgentOutput` Pydantic validation (missing required field, wrong type) | Same fallback shape. `status == "malformed_output"`. |
| **Low-confidence diagnosis** | Not a failure to catch — a successful, well-formed response with `confidence` below the guardrail thresholds | `status == "ok"` (the model call and parsing succeeded), but `guardrail_violations` is non-empty and the action/escalation flag are corrected per the thresholds table above. |

`investigate_incident` never raises for any of these four cases — see
`test_agent_investigate.py::test_api_failure_does_not_raise` and
`::test_malformed_output_does_not_raise`. Only genuinely unexpected bugs
(not one of the four modeled failure types) would propagate as an
exception, deliberately, rather than being silently absorbed.

## Example input

An abbreviated `AgentInput`, built from this project's own detection +
retrieval output for the UPI+HDFC Bank incident (`cand_00005`):

```json
{
  "incident": {
    "incident_id": "cand_00005",
    "affected_dimension": "payment_method+institution",
    "affected_segment": { "payment_method": "UPI", "institution": "HDFC Bank" },
    "window_start": "2026-08-12T09:00:00+00:00",
    "window_end": "2026-08-12T20:00:00+00:00",
    "current_success_rate": 0.6216,
    "degradation_percentage": 620.4,
    "transaction_count": 37,
    "revenue_affected": 7402.39,
    "severity": "HIGH",
    "supporting_statistics": {
      "z_score": 7.905,
      "failure_reason_breakdown": { "BANK_TIMEOUT": 11, "NETWORK_ERROR": 3 }
    }
  },
  "structured_evidence_count": 6,
  "structured_evidence_types": [
    "transaction_statistics", "revenue_impact", "affected_transaction_ids",
    "geography_breakdown", "failure_reason_breakdown", "historical_daily_trend"
  ],
  "unstructured_evidence_count": 5,
  "allowed_actions": [
    "RETRY_ELIGIBLE_PAYMENTS", "OFFER_ALTERNATE_METHOD",
    "NOTIFY_MERCHANT", "WAIT_AND_REASSESS", "ESCALATE"
  ],
  "merchant_policies": {
    "max_auto_retry_amount_inr": 10000,
    "requires_human_approval_above_severity": "CRITICAL",
    "auto_retry_enabled": true
  }
}
```

## Example structured output

The resulting `AgentOutput` (a mocked-but-realistic model response run
through the real guardrails — zero violations, since this response
happens to be clean):

```json
{
  "diagnosis": "UPI transactions routed through HDFC Bank are failing at a substantially higher rate than baseline for this segment, with failures concentrated on bank-side timeouts.",
  "observations": [
    "Structured evidence (cand_00005_ev_stats) shows a window failure rate of 37.8% for UPI+HDFC Bank vs a baseline of 5.3% for the same segment.",
    "Structured evidence (cand_00005_ev_reasons) shows the window's failures are dominated by BANK_TIMEOUT and NETWORK_ERROR reasons.",
    "Unstructured evidence (cand_00005_ev_doc_0001) reports UPI transactions through HDFC Bank showing a sharp rise in failed authorizations timing out at the bank's end, with other issuing banks unaffected over the same window."
  ],
  "inferences": [
    "The failure pattern is isolated to one bank and one payment method, and is dominated by timeout-type reasons rather than customer-side declines, which is consistent with a bank-side authorization bottleneck rather than a platform-wide or customer-behavior issue."
  ],
  "evidence_ids": ["cand_00005_ev_stats", "cand_00005_ev_reasons", "cand_00005_ev_doc_0001"],
  "revenue_at_risk": 7402.39,
  "recommended_action": "RETRY_ELIGIBLE_PAYMENTS",
  "reason": "Failures are timeout-dominant and isolated to a single bank rather than spread across the platform, which is the pattern most likely to resolve on retry. Merchant policy permits auto-retry up to INR 10,000 and this window's revenue at risk (7402.39) is within that bound.",
  "confidence": 0.78,
  "stop_condition": "If the retry success rate for this segment stays below 30% within 2 hours of the retry attempt, stop retrying and escalate instead of continuing to retry.",
  "escalation_required": false
}
```

Notice `observations` state only what the evidence directly says, while
`inferences` is where the diagnostic reasoning ("consistent with a
bank-side authorization bottleneck") lives — the model is never asked to
assert the cause as fact.

## Tests

44 tests across four files, all passing (`python -m pytest tests/test_agent_*.py -v`):

- **`test_agent_schema.py`** (9 tests) — `AgentInput`/`AgentOutput`
  validation: required fields, defaults, empty-allowed-actions rejection,
  confidence clamping vs. wrong-type rejection, JSON round-tripping.
- **`test_agent_guardrails.py`** (19 tests) — the deterministic
  enforcement layer in isolation: deterministic revenue extraction
  (structured evidence → incident field → not-found), evidence ID
  filtering (valid pass-through, invented IDs dropped, all-invented →
  empty list), revenue overwrite (matching → no violation, mismatched →
  overwritten + flagged, invented-with-no-source → zeroed + flagged),
  action allow-list enforcement, both confidence thresholds, and
  internal-consistency (`ESCALATE` always implies `escalation_required`).
- **`test_agent_client.py`** (9 tests) — the API wrapper directly, with
  `anthropic.Anthropic` mocked: successful tool-call extraction, missing
  API key, retry-then-succeed on a transient `RateLimitError`,
  give-up-after-`MAX_RETRIES` on a persistent `APIConnectionError`,
  fail-fast (no retry) on a 4xx `AuthenticationError`, and
  `MalformedOutputError` when the model responds with plain text instead
  of a tool call.
- **`test_agent_investigate.py`** (13 tests) — the full orchestration
  layer with `call_agent_model` mocked, explicitly covering:
  - **API failure**: `AgentAPIError` → safe `ESCALATE` fallback, doesn't
    raise, still reports revenue from evidence.
  - **Malformed output**: missing required field, wrong-typed field,
    `evidence_ids` as a string instead of a list, no-tool-call
    (`MalformedOutputError`), and — the flip side — extra unrequested
    fields in an otherwise-valid response do *not* count as malformed.
  - **Missing evidence**: short-circuits without ever calling the model
    (`mock_call.assert_not_called()`), still surfaces revenue from the
    incident dict if available.
  - **Low-confidence diagnosis**: below the hard floor → action forced to
    `ESCALATE`; between the two thresholds → action kept but
    `escalation_required` forced `True`.
  - End-to-end guardrail exercises: disallowed action overridden,
    invented evidence ID stripped, invented revenue corrected — all
    verified through the full `investigate_incident` call, not just the
    guardrails unit tests.

## What this module deliberately does not do

- **Does not decide which actions are eligible.** `allowed_actions` and
  `merchant_policies` are inputs the caller provides; this module only
  enforces the model stays inside whatever it's given. The actual
  eligibility logic (e.g. "WALLET failures under $X can auto-retry") is
  the not-yet-built policy engine's job.
- **Does not execute anything.** No code path here sends a retry, a
  recovery link, or a notification. `AgentOutput.recommended_action` is a
  recommendation string, nothing more.
- **Does not detect incidents or retrieve evidence.** Both are separate,
  already-built upstream modules (`app/detection/`, `app/retrieval/`)
  this module only consumes.
