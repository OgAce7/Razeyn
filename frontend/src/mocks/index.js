/**
 * MOCK / DEMO DATA -- clearly isolated so it can be deleted once the real
 * backend API endpoints exist, without touching any UI component.
 *
 * This is NOT invented data. Every value in these JSON files is the
 * actual, unmodified output of one real run of the backend pipeline
 * (backend/scripts/run_example_evaluation.py) against the project's real
 * synthetic dataset -- the same numbers documented in
 * backend/app/evaluation/example_output/. See EVALUATION_LAYER_README
 * (in the backend repo root) for how that run was produced and what its
 * numbers mean.
 *
 * The ONLY thing this module does beyond importing the JSON is expose it
 * through the same shape the typed API client (`../api/client.js`)
 * returns, so swapping a mock function for a real `fetch` call later is
 * a one-line change in client.js, not a rewrite of any component.
 */

import auditTrailRaw from "./audit_trail.json";
import evaluationReportRaw from "./evaluation_report.json";
import evidenceBundlesRaw from "./evidence_bundles.json";

/** @type {import('../api/types.js').AuditRecord[]} */
export const MOCK_AUDIT_TRAIL = auditTrailRaw;

/** @type {import('../api/types.js').EvaluationReport} */
export const MOCK_EVALUATION_REPORT = evaluationReportRaw;

/** @type {Object<string, import('../api/types.js').EvidenceBundle>} */
export const MOCK_EVIDENCE_BUNDLES = evidenceBundlesRaw;

/**
 * True whenever the app is serving mock data instead of live backend
 * responses. Read by the API client and surfaced in the UI (a small
 * "Demo data" badge) so nothing is ever silently presented as live.
 */
export const IS_MOCK_MODE = true;
