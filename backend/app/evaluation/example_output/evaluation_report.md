# Revenue Incident Responder -- Evaluation Report

Generated: 2026-08-31T19:29:17.632176+00:00
Incidents evaluated: 7

## Detection
- Incidents detected: 7
- Evaluated against ground truth: 6
- True positives: 6
- False positives: 0
- Precision: 100.0%
- Mean detection latency (from incident end): 1,330,097.86s
- Mean detection latency (from incident start): 1,348,697.86s

## Diagnosis
- Evaluated (agent ran successfully + ground truth available): 6
- Affected-segment match rate: 66.7% (4 matched)
- Evidence-supported diagnosis rate: 100.0% (6 / 6)

## Revenue
- Total revenue exposed (ground truth): ₹54,033.71
- Total revenue at risk (agent, guardrail-enforced): ₹72,184.25
- Total revenue recovered (agent pipeline): ₹27,145.33
- Recovery rate (recovered / at risk): 37.6%
- Baseline (fixed retry rule) revenue recovered: ₹28,653.14
- Recovery uplift vs baseline: ₹-1,507.81
- Recovery uplift vs baseline (%): -5.26%

## Actions
- Actions attempted (executed/simulated): 6
- Actions approved: 6
- Actions rejected: 1
- Successful transaction-level actions: 42
- Stopped (no-op, benign): 0
- Escalated to human: 0
- Success rate of attempted transactions: 53.8%

## Safety
- Policy violations prevented (failed checks caught): 13
- Guardrail corrections to agent output: 0
- Unnecessary interventions (acted on a false positive): 0
- False-positive cost (revenue at risk on false positives): ₹0.00
- Evaluated against ground truth: 6
