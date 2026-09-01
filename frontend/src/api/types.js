/**
 * Type definitions mirroring the backend's real data shapes exactly, as
 * produced by:
 *   - app/audit/schema.py        (AuditRecord and nested refs)
 *   - app/evaluation/metrics.py  (EvaluationReport and metric groups)
 *
 * These are JSDoc typedefs (not TypeScript) so the project stays plain
 * JS + Vite per the existing scaffold, while still giving editor
 * autocomplete/type-checking on the shapes the API client returns.
 *
 * IMPORTANT: nothing in this file invents fields. Every property here
 * corresponds 1:1 to a field already present in the backend's
 * dataclasses (see backend/app/audit/schema.py, backend/app/evaluation/
 * metrics.py) or its example JSON output
 * (backend/app/evaluation/example_output/*.json). If the backend adds a
 * field later, add it here to match -- don't guess ahead of it.
 */

/**
 * @typedef {Object} PolicyCheck
 * @property {string} name
 * @property {boolean} passed
 * @property {string} detail
 */

/**
 * @typedef {Object} DetectionRef
 * @property {string} candidate_incident_id
 * @property {string} detection_timestamp
 * @property {string} affected_dimension
 * @property {Object<string,string>} affected_segment
 * @property {string} window_start
 * @property {string} window_end
 * @property {string} severity  "LOW"|"MEDIUM"|"HIGH"|"CRITICAL"
 * @property {number} confidence_score
 * @property {number} transaction_count
 * @property {number} revenue_affected
 * @property {number|null} z_score
 */

/**
 * @typedef {Object} EvidenceRef
 * @property {string[]} structured_evidence_ids
 * @property {string[]} unstructured_evidence_ids
 */

/**
 * @typedef {Object} AgentDecisionRef
 * @property {string} diagnosis
 * @property {string[]} evidence_ids
 * @property {number} revenue_at_risk
 * @property {string} recommended_action
 * @property {number} confidence
 * @property {boolean} escalation_required
 * @property {string} status  "ok"|"no_evidence"|"api_error"|"malformed_output"
 * @property {string[]} guardrail_violations
 */

/**
 * @typedef {Object} PolicyDecisionRef
 * @property {boolean} approved
 * @property {boolean} escalation_required
 * @property {string} reason
 * @property {string[]} eligible_transaction_ids
 * @property {number} expected_revenue_recovery
 * @property {PolicyCheck[]} policy_checks
 */

/**
 * @typedef {Object} ActionOutcomeRef
 * @property {string} action_id
 * @property {string} requested_action
 * @property {string} execution_status  e.g. "SIMULATED"|"EXECUTED"|"NOT_EXECUTED_REJECTED"|"NOT_EXECUTED_ESCALATED"|"NOT_EXECUTED_STOPPED"|"NOT_EXECUTED_WAIT"
 * @property {string[]} transaction_ids
 * @property {number} attempted
 * @property {number} succeeded
 * @property {number} failed
 * @property {string} timestamp
 */

/**
 * @typedef {Object} GroundTruthRef
 * @property {string} incident_id
 * @property {boolean} is_true_incident
 * @property {number} revenue_exposed
 * @property {number} transaction_count
 * @property {string[]} affected_transaction_ids
 * @property {string} start_time
 * @property {string} end_time
 * @property {string} expected_severity
 * @property {Object<string,string>} affected_segment
 */

/**
 * @typedef {Object} AuditRecord
 * @property {string} record_id
 * @property {string} created_at
 * @property {DetectionRef} detection
 * @property {EvidenceRef} evidence
 * @property {AgentDecisionRef} agent_decision
 * @property {PolicyDecisionRef} policy_decision
 * @property {ActionOutcomeRef} action_outcome
 * @property {GroundTruthRef|null} ground_truth
 */

/**
 * @typedef {Object} StructuredEvidenceItem
 * @property {string} evidence_id
 * @property {string} evidence_type
 * @property {string} source
 * @property {Object|null} data
 * @property {string|null} text
 * @property {number} relevance_score
 * @property {string} timestamp
 */

/**
 * @typedef {Object} UnstructuredEvidenceItem
 * @property {string} evidence_id
 * @property {string} evidence_type
 * @property {string} source
 * @property {Object|null} data
 * @property {string} text
 * @property {number} relevance_score
 * @property {string} timestamp
 * @property {string} [title]
 */

/**
 * @typedef {Object} EvidenceBundle
 * @property {string} incident_id
 * @property {string} retrieved_at
 * @property {string} query_used
 * @property {StructuredEvidenceItem[]} structured_evidence
 * @property {UnstructuredEvidenceItem[]} unstructured_evidence
 */

/**
 * @typedef {Object} DetectionMetrics
 * @property {number} incidents_detected
 * @property {number} true_positive_count
 * @property {number} false_positive_count
 * @property {number} evaluated_count
 * @property {number|null} precision
 * @property {number|null} mean_detection_latency_seconds
 * @property {number|null} mean_detection_latency_seconds_from_window_start
 */

/**
 * @typedef {Object} DiagnosisMetrics
 * @property {number} evaluated_count
 * @property {number} segment_match_count
 * @property {number|null} segment_match_rate
 * @property {number} evidence_supported_count
 * @property {number|null} evidence_supported_rate
 * @property {string} note
 */

/**
 * @typedef {Object} RevenueMetrics
 * @property {number} total_revenue_exposed
 * @property {number} total_revenue_at_risk
 * @property {number} total_revenue_recovered
 * @property {number|null} recovery_rate
 * @property {number|null} baseline_revenue_recovered
 * @property {number|null} recovery_uplift_vs_baseline
 * @property {number|null} recovery_uplift_vs_baseline_pct
 */

/**
 * @typedef {Object} ActionMetrics
 * @property {number} actions_attempted
 * @property {number} actions_approved
 * @property {number} actions_rejected
 * @property {number} actions_successful
 * @property {number} actions_stopped
 * @property {number} actions_escalated
 * @property {number|null} success_rate_of_attempted
 */

/**
 * @typedef {Object} SafetyMetrics
 * @property {number} policy_violations_prevented
 * @property {number} guardrail_corrections
 * @property {number} unnecessary_interventions
 * @property {number} false_positive_cost
 * @property {number} evaluated_count
 */

/**
 * @typedef {Object} EvaluationReport
 * @property {string} generated_at
 * @property {number} record_count
 * @property {DetectionMetrics} detection
 * @property {DiagnosisMetrics} diagnosis
 * @property {RevenueMetrics} revenue
 * @property {ActionMetrics} actions
 * @property {SafetyMetrics} safety
 */

export {};
