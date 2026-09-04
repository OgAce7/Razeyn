import React from "react";
import { formatMoney, formatPercent, humanizeAction } from "../lib/format.js";

/**
 * @param {{ agentDecision: import('../api/types.js').AgentDecisionRef }} props
 */
export function AiDecisionPanel({ agentDecision }) {
  if (!agentDecision) return <div className="empty-state">No AI decision recorded.</div>;

  const {
    diagnosis,
    recommended_action,
    confidence,
    revenue_at_risk,
    escalation_required,
    status,
    guardrail_violations,
    evidence_ids,
    error_detail,
  } = agentDecision;

  return (
    <div>
      {status !== "ok" && (
        <div style={{ marginBottom: 14 }}>
          <div className="pill pill-warning">Fallback path: {humanizeAction(status)}</div>
          {error_detail && (
            <p className="text-tertiary mono" style={{ fontSize: 12, marginTop: 6 }}>
              {error_detail}
            </p>
          )}
        </div>
      )}

      <div style={styles.block}>
        <div className="text-tertiary" style={styles.blockLabel}>
          Diagnosis
        </div>
        <p style={styles.diagnosisText}>{diagnosis}</p>
      </div>

      <div style={styles.block}>
        <div className="text-tertiary" style={styles.blockLabel}>
          Inferred cause & recommended action
        </div>
        <div style={styles.actionRow}>
          <span className="pill pill-accent" style={{ fontSize: 13, padding: "5px 12px" }}>
            {humanizeAction(recommended_action)}
          </span>
          {escalation_required && <span className="pill pill-warning">Escalation required</span>}
        </div>
      </div>

      <div style={styles.metricGrid}>
        <div>
          <div className="text-tertiary" style={styles.blockLabel}>
            Confidence
          </div>
          <ConfidenceBar value={confidence} />
        </div>
        <div>
          <div className="text-tertiary" style={styles.blockLabel}>
            Revenue at risk <span style={{ opacity: 0.7, fontWeight: 400 }}>· guardrail-verified</span>
          </div>
          <div className="mono" style={styles.revenueValue}>
            {formatMoney(revenue_at_risk, { precise: true })}
          </div>
        </div>
      </div>

      <div style={styles.block}>
        <div className="text-tertiary" style={styles.blockLabel}>
          Stopping condition
        </div>
        <p style={styles.stopText}>
          {escalation_required
            ? "Escalates to a human reviewer rather than acting automatically — confidence or severity crossed the auto-escalate threshold."
            : "None triggered — the agent's recommendation proceeded to the policy engine for automated evaluation."}
        </p>
      </div>

      {guardrail_violations && guardrail_violations.length > 0 && (
        <div style={styles.block}>
          <div className="text-tertiary" style={styles.blockLabel}>
            Guardrail corrections applied
          </div>
          <ul style={styles.violationList}>
            {guardrail_violations.map((v, i) => (
              <li key={i} style={styles.violationItem}>
                {v}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={styles.block}>
        <div className="text-tertiary" style={styles.blockLabel}>
          Cited evidence ({evidence_ids.length})
        </div>
        <div style={styles.evidenceChips}>
          {evidence_ids.map((id) => (
            <span key={id} className="mono" style={styles.evidenceChip}>
              {id}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function ConfidenceBar({ value }) {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? "var(--positive)" : pct >= 45 ? "var(--warning)" : "var(--negative)";
  return (
    <div>
      <div style={styles.confidenceTrack}>
        <div style={{ ...styles.confidenceFill, width: `${pct}%`, background: color }} />
      </div>
      <div className="mono" style={{ fontSize: 13, fontWeight: 700, marginTop: 6, color }}>
        {formatPercent(value, { digits: 0 })}
      </div>
    </div>
  );
}

const styles = {
  block: {
    marginBottom: 18,
  },
  blockLabel: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    marginBottom: 6,
  },
  diagnosisText: {
    fontSize: 14,
    lineHeight: 1.6,
    color: "var(--text-primary)",
    margin: 0,
  },
  actionRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  metricGrid: {
    display: "flex",
    flexDirection: "column",
    gap: 16,
    marginBottom: 18,
  },
  revenueValue: {
    fontSize: 18,
    fontWeight: 700,
    color: "var(--text-primary)",
  },
  confidenceTrack: {
    height: 8,
    borderRadius: 999,
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-soft)",
    overflow: "hidden",
  },
  confidenceFill: {
    height: "100%",
    borderRadius: 999,
  },
  stopText: {
    fontSize: 12.5,
    lineHeight: 1.55,
    color: "var(--text-secondary)",
    margin: 0,
  },
  violationList: {
    margin: 0,
    paddingLeft: 18,
    fontSize: 12.5,
    color: "var(--warning)",
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  violationItem: {
    lineHeight: 1.5,
  },
  evidenceChips: {
    display: "flex",
    flexWrap: "wrap",
    gap: 6,
  },
  evidenceChip: {
    fontSize: 10.5,
    padding: "3px 8px",
    borderRadius: 6,
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-soft)",
    color: "var(--text-secondary)",
  },
};
