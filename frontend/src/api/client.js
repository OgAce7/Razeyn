/**
 * Typed API client for the Revenue Incident Responder dashboard.
 *
 * Design intent: every function here has a signature and return shape
 * matching a REAL backend endpoint this project's evaluation layer
 * already supports data-wise (AuditRecord / EvaluationReport / evidence
 * bundle -- see backend/app/audit/schema.py, backend/app/evaluation/
 * metrics.py, backend/app/retrieval/bundle.py). Only `/health` actually
 * exists on the backend today (backend/app/api/health.py); the others
 * are NOT yet implemented as HTTP routes.
 *
 * Every non-health function therefore:
 *   1. Attempts the real endpoint via `fetch`.
 *   2. On a network error OR a 404 (endpoint not implemented yet),
 *      catches it and returns the equivalent MOCK data instead, tagging
 *      the result with `_source: "mock"` so callers/UI can show a
 *      "Demo data" indicator.
 *   3. On any OTHER failure (500, malformed JSON, etc.) it throws --
 *      mock fallback is only for "not built yet," never for masking a
 *      real backend error.
 *
 * When the real endpoints ship, delete the try/catch fallback in each
 * function (or just let them keep working -- a 200 response short-
 * circuits before the catch ever runs) -- no component using this
 * client needs to change, since they all consume the same return shape
 * either way.
 */

import {
  MOCK_AUDIT_TRAIL,
  MOCK_EVALUATION_REPORT,
  MOCK_EVIDENCE_BUNDLES,
} from "../mocks/index.js";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

/** Endpoints this client calls before falling back to mock data. None of
 * these exist on the backend yet except `/health` -- see module docstring. */
export const ENDPOINTS = {
  health: `${API_BASE}/health`,
  auditTrail: `${API_BASE}/evaluation/audit-trail`,
  evaluationReport: `${API_BASE}/evaluation/report`,
  evidence: (incidentId) => `${API_BASE}/evidence/${incidentId}`,
};

async function fetchJson(url) {
  const res = await fetch(url);
  if (res.status === 404) {
    const err = new Error(`Not implemented: ${url}`);
    err.notImplemented = true;
    throw err;
  }
  if (!res.ok) {
    throw new Error(`Request to ${url} failed with status ${res.status}`);
  }
  return res.json();
}

/** @returns {Promise<{status: string, service: string, env: string}>} */
export async function getHealth() {
  return fetchJson(ENDPOINTS.health);
}

/**
 * Full audit trail -- one AuditRecord per incident, chaining detection
 * -> evidence -> AI decision -> policy decision -> action -> outcome.
 * @returns {Promise<{data: import('./types.js').AuditRecord[], source: "live"|"mock"}>}
 */
export async function getAuditTrail() {
  try {
    const data = await fetchJson(ENDPOINTS.auditTrail);
    return { data, source: "live" };
  } catch (err) {
    if (!err.notImplemented) throw err;
    return { data: MOCK_AUDIT_TRAIL, source: "mock" };
  }
}

/**
 * Composite evaluation report (detection/diagnosis/revenue/actions/safety).
 * @returns {Promise<{data: import('./types.js').EvaluationReport, source: "live"|"mock"}>}
 */
export async function getEvaluationReport() {
  try {
    const data = await fetchJson(ENDPOINTS.evaluationReport);
    return { data, source: "live" };
  } catch (err) {
    if (!err.notImplemented) throw err;
    return { data: MOCK_EVALUATION_REPORT, source: "mock" };
  }
}

/**
 * Evidence bundle (structured + unstructured, with source attribution)
 * for one candidate incident.
 * @param {string} incidentId
 * @returns {Promise<{data: import('./types.js').EvidenceBundle | null, source: "live"|"mock"}>}
 */
export async function getEvidence(incidentId) {
  try {
    const data = await fetchJson(ENDPOINTS.evidence(incidentId));
    return { data, source: "live" };
  } catch (err) {
    if (!err.notImplemented) throw err;
    return { data: MOCK_EVIDENCE_BUNDLES[incidentId] ?? null, source: "mock" };
  }
}
