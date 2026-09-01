import React from "react";
import { ApprovalPill, ExecutionStatusPill } from "./Primitives.jsx";
import { formatMoney, formatNumber, humanizeAction } from "../lib/format.js";

/**
 * @param {{ policyDecision: import('../api/types.js').PolicyDecisionRef, actionOutcome: import('../api/types.js').ActionOutcomeRef }} props
 */
export function RecoveryExecutionPanel({ policyDecision, actionOutcome }) {
  if (!policyDecision || !actionOutcome) {
    return <div className="empty-state">No recovery decision recorded.</div>;
  }

  const { approved, reason, policy_checks, eligible_transaction_ids, expected_revenue_recovery } =
    policyDecision;
  const { requested_action, execution_status, attempted, succeeded, failed } = actionOutcome;

  const failedChecks = policy_checks.filter((c) => !c.passed);
  const passedChecks = policy_checks.filter((c) => c.passed);

  return (
    <div>
      <div style={styles.topRow}>
        <div>
          <div className="text-tertiary" style={styles.blockLabel}>
            Action selected
          </div>
          <span className="pill pill-accent" style={{ fontSize: 13, padding: "5px 12px" }}>
            {humanizeAction(requested_action)}
          </span>
        </div>
        <div style={styles.pillGroup}>
          <ApprovalPill approved={approved} />
          <ExecutionStatusPill status={execution_status} />
        </div>
      </div>

      <p style={styles.reasonText}>{reason}</p>

      <div style={styles.statGrid}>
        <Stat label="Eligible transactions" value={formatNumber(eligible_transaction_ids.length)} />
        <Stat label="Expected recovery" value={formatMoney(expected_revenue_recovery, { precise: true })} />
        <Stat label="Attempted" value={formatNumber(attempted)} />
        <Stat
          label="Succeeded / failed"
          value={`${formatNumber(succeeded)} / ${formatNumber(failed)}`}
          tone={succeeded > 0 ? "positive" : "neutral"}
        />
      </div>

      <div style={styles.checksBlock}>
        <div className="text-tertiary" style={styles.blockLabel}>
          Policy checks ({passedChecks.length} passed
          {failedChecks.length > 0 ? `, ${failedChecks.length} caught issues` : ""})
        </div>
        <div style={styles.checkList}>
          {policy_checks.map((check, i) => (
            <div key={i} style={styles.checkRow}>
              <span style={{ ...styles.checkIcon, color: check.passed ? "var(--positive)" : "var(--negative)" }}>
                {check.passed ? "✓" : "✕"}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={styles.checkName}>{humanizeAction(check.name)}</div>
                <div className="text-secondary" style={styles.checkDetail}>
                  {check.detail}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone = "neutral" }) {
  const color = tone === "positive" ? "var(--positive)" : "var(--text-primary)";
  return (
    <div>
      <div className="text-tertiary" style={styles.statLabel}>
        {label}
      </div>
      <div className="mono" style={{ ...styles.statValue, color }}>
        {value}
      </div>
    </div>
  );
}

const styles = {
  topRow: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 12,
    marginBottom: 14,
    flexWrap: "wrap",
  },
  pillGroup: {
    display: "flex",
    gap: 8,
    paddingTop: 2,
  },
  blockLabel: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    marginBottom: 6,
  },
  reasonText: {
    fontSize: 13,
    lineHeight: 1.55,
    color: "var(--text-secondary)",
    margin: "0 0 18px",
  },
  statGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(4, 1fr)",
    gap: 16,
    marginBottom: 20,
    paddingBottom: 20,
    borderBottom: "1px solid var(--border-soft)",
  },
  statLabel: {
    fontSize: 11,
    marginBottom: 4,
  },
  statValue: {
    fontSize: 16,
    fontWeight: 700,
  },
  checksBlock: {},
  checkList: {
    display: "flex",
    flexDirection: "column",
    gap: 10,
    maxHeight: 280,
    overflowY: "auto",
    paddingRight: 4,
  },
  checkRow: {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
  },
  checkIcon: {
    fontSize: 13,
    fontWeight: 700,
    marginTop: 1,
    flexShrink: 0,
    width: 14,
  },
  checkName: {
    fontSize: 12.5,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  checkDetail: {
    fontSize: 12,
    lineHeight: 1.45,
  },
};
