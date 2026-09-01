/**
 * Pure derivation functions over already-fetched data (AuditRecord[] /
 * EvaluationReport). Nothing here invents a number -- every value
 * returned is copied or summed/ratio'd from fields already present on
 * the records passed in, the same rule the backend's own
 * app/evaluation/metrics.py follows.
 */

const SEVERITY_ORDER = { LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3 };

/** Sort audit records chronologically by detection timestamp (oldest first). */
export function sortByDetectionTime(records) {
  return [...records].sort(
    (a, b) => new Date(a.detection.detection_timestamp) - new Date(b.detection.detection_timestamp)
  );
}

/** Revenue recovered for one record: succeeded/attempted share of the
 * record's expected_revenue_recovery -- mirrors the backend's own
 * compute_revenue_recovered_for_record fallback formula
 * (app/evaluation/metrics.py) for when exact per-transaction amounts
 * aren't available client-side. */
export function revenueRecoveredForRecord(record) {
  const { attempted, succeeded } = record.action_outcome;
  if (!attempted) return 0;
  const fraction = succeeded / attempted;
  return round2(record.policy_decision.expected_revenue_recovery * fraction);
}

export function round2(value) {
  return Math.round(value * 100) / 100;
}

/** One row per incident for the timeline / list view. */
export function toTimelineRows(records) {
  return sortByDetectionTime(records).map((r) => ({
    id: r.detection.candidate_incident_id,
    recordId: r.record_id,
    timestamp: r.detection.detection_timestamp,
    dimension: r.detection.affected_dimension,
    segment: r.detection.affected_segment,
    severity: r.detection.severity,
    confidence: r.detection.confidence_score,
    revenueAffected: r.detection.revenue_affected,
    status: r.action_outcome.execution_status,
    approved: r.policy_decision.approved,
    recommendedAction: r.agent_decision.recommended_action,
    recovered: revenueRecoveredForRecord(r),
    isTrueIncident: r.ground_truth ? r.ground_truth.is_true_incident : null,
  }));
}

/** Severity distribution for a bar chart. */
export function severityBreakdown(records) {
  const counts = { LOW: 0, MEDIUM: 0, HIGH: 0, CRITICAL: 0 };
  for (const r of records) {
    if (counts[r.detection.severity] !== undefined) counts[r.detection.severity] += 1;
  }
  return Object.entries(counts)
    .filter(([, count]) => count > 0)
    .sort((a, b) => SEVERITY_ORDER[a[0]] - SEVERITY_ORDER[b[0]])
    .map(([severity, count]) => ({ severity, count }));
}

/** Revenue at-risk vs recovered, one bar-chart-friendly row per incident. */
export function revenueByIncident(records) {
  return sortByDetectionTime(records).map((r) => ({
    id: r.detection.candidate_incident_id,
    label: shortSegmentLabel(r.detection.affected_segment, r.detection.affected_dimension),
    atRisk: r.agent_decision.revenue_at_risk,
    recovered: revenueRecoveredForRecord(r),
  }));
}

/** Cumulative revenue recovered over time -- a line-chart-friendly series
 * built by running-sum over chronologically sorted records. */
export function cumulativeRecoveryOverTime(records) {
  const sorted = sortByDetectionTime(records);
  let running = 0;
  return sorted.map((r) => {
    running += revenueRecoveredForRecord(r);
    return {
      id: r.detection.candidate_incident_id,
      timestamp: r.detection.detection_timestamp,
      cumulativeRecovered: round2(running),
    };
  });
}

function shortSegmentLabel(segment, dimension) {
  if (!segment || Object.keys(segment).length === 0) return "All traffic";
  const values = Object.values(segment);
  return values.join(" · ") || dimension;
}

/** Find one record by its candidate_incident_id. */
export function findRecordByIncidentId(records, incidentId) {
  return records.find((r) => r.detection.candidate_incident_id === incidentId) ?? null;
}

/** before/after performance for a single incident's affected segment,
 * derived from the structured "transaction_statistics" evidence item if
 * present in the evidence bundle -- returns null if that evidence item
 * isn't in the bundle rather than fabricating baseline/current numbers. */
export function baselineVsCurrent(evidenceBundle) {
  if (!evidenceBundle) return null;
  const statsItem = evidenceBundle.structured_evidence?.find(
    (e) => e.evidence_type === "transaction_statistics"
  );
  if (!statsItem || !statsItem.data) return null;
  const d = statsItem.data;
  return {
    baselineFailureRate: d.baseline_failure_rate,
    currentFailureRate: d.window_failure_rate,
    baselineCount: d.baseline_transaction_count,
    currentCount: d.window_transaction_count,
    windowFailedCount: d.window_failed_count,
    windowSuccessCount: d.window_success_count,
  };
}
