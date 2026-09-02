import React, { useState } from "react";
import { decideIncident } from "../api/client.js";

/**
 * Approve/Reject controls for an incident whose action_outcome is
 * NOT_EXECUTED_ESCALATED -- i.e. genuinely waiting on a human decision.
 * Not rendered at all for any other execution_status (see
 * IncidentDetailPage.jsx), so there's no ambiguity about when a decision
 * is possible.
 *
 * `onResolved(newRecord)` is called with the freshly-resolved AuditRecord
 * returned by the backend once a decision succeeds, so the parent page
 * can update in place without waiting on a second round-trip.
 *
 * @param {{ incidentId: string, onResolved: (record: object) => void }} props
 */
export function ApproveRejectPanel({ incidentId, onResolved }) {
  const [pendingAction, setPendingAction] = useState(null); // "approve" | "reject" | null
  const [error, setError] = useState(null);

  async function handleDecision(decision) {
    setError(null);
    setPendingAction(decision);
    try {
      const record = await decideIncident(incidentId, decision);
      onResolved(record);
    } catch (err) {
      setError(err);
    } finally {
      setPendingAction(null);
    }
  }

  const disabled = pendingAction !== null;

  return (
    <div className="pending-decision-panel">
      <div style={styles.textBlock}>
        <div style={styles.label}>Awaiting human decision</div>
        <p className="text-secondary" style={styles.copy}>
          This incident was escalated by policy and hasn't been executed yet. Approving will run the
          recommended action against the real policy/executor pipeline; rejecting records it as
          declined with no action taken.
        </p>
      </div>

      <div style={styles.buttonRow}>
        <button
          className="btn btn-positive"
          disabled={disabled}
          onClick={() => handleDecision("approve")}
        >
          {pendingAction === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          className="btn btn-negative btn-ghost"
          disabled={disabled}
          onClick={() => handleDecision("reject")}
        >
          {pendingAction === "reject" ? "Rejecting…" : "Reject"}
        </button>
      </div>

      {error && (
        <div className="error-banner" style={styles.errorBanner}>
          <strong>Couldn't record decision.</strong> {error.message}
          {error.status === 409 && (
            <> This incident may already have been resolved — refresh to see its current state.</>
          )}
        </div>
      )}
    </div>
  );
}

const styles = {
  textBlock: {
    marginBottom: 14,
  },
  label: {
    fontSize: 11,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    color: "var(--warning)",
    marginBottom: 6,
  },
  copy: {
    fontSize: 12.5,
    lineHeight: 1.5,
    margin: 0,
  },
  buttonRow: {
    display: "flex",
    gap: 10,
  },
  errorBanner: {
    marginTop: 12,
  },
};
