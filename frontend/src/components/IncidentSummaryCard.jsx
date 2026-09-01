import React from "react";
import { SeverityPill } from "./Primitives.jsx";
import { formatDateTime, formatMoney, formatNumber, formatPercent, formatSegment, humanizeAction } from "../lib/format.js";

/**
 * @param {{ detection: import('../api/types.js').DetectionRef, beforeAfter: ReturnType<typeof import('../lib/derive.js').baselineVsCurrent> | null }} props
 */
export function IncidentSummaryCard({ detection, beforeAfter }) {
  const {
    affected_dimension,
    affected_segment,
    severity,
    confidence_score,
    revenue_affected,
    transaction_count,
    window_start,
    window_end,
    detection_timestamp,
    z_score,
  } = detection;

  return (
    <div>
      <div style={styles.headRow}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="text-tertiary" style={styles.blockLabel}>
            What changed
          </div>
          <p style={styles.whatChanged}>
            A statistically significant rise in failed transactions was detected in{" "}
            <strong>{formatSegment(affected_segment)}</strong>, flagged along the{" "}
            <strong>{humanizeAction(affected_dimension)}</strong> dimension
            {z_score != null ? (
              <>
                {" "}
                (z-score <span className="mono">{z_score.toFixed(2)}</span>)
              </>
            ) : null}
            , during the window {formatDateTime(window_start)} → {formatDateTime(window_end)}.
          </p>
        </div>
        <div style={styles.badges}>
          <SeverityPill severity={severity} />
        </div>
      </div>

      <div style={styles.statGrid}>
        <Stat label="Affected segment" value={formatSegment(affected_segment)} mono={false} />
        <Stat label="Revenue at risk" value={formatMoney(revenue_affected, { precise: true })} />
        <Stat label="Transactions affected" value={formatNumber(transaction_count)} />
        <Stat label="Detector confidence" value={formatPercent(confidence_score)} />
      </div>

      {beforeAfter && (
        <div style={styles.perfRow}>
          <PerfBlock label="Baseline failure rate" value={formatPercent(beforeAfter.baselineFailureRate)} tone="neutral" />
          <PerfBlock label="Current (window) failure rate" value={formatPercent(beforeAfter.currentFailureRate)} tone="negative" />
          <PerfBlock
            label="Relative change"
            value={
              beforeAfter.baselineFailureRate > 0
                ? `${(((beforeAfter.currentFailureRate - beforeAfter.baselineFailureRate) / beforeAfter.baselineFailureRate) * 100).toFixed(0)}%`
                : "—"
            }
            tone="negative"
          />
        </div>
      )}

      <div className="text-tertiary" style={styles.detectedAt}>
        Detected {formatDateTime(detection_timestamp)}
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="text-tertiary" style={styles.statLabel}>
        {label}
      </div>
      <div className="mono" style={styles.statValue}>
        {value}
      </div>
    </div>
  );
}

function PerfBlock({ label, value, tone }) {
  const color = tone === "negative" ? "var(--negative)" : "var(--text-primary)";
  return (
    <div style={styles.perfBlock}>
      <div className="text-tertiary" style={styles.perfLabel}>
        {label}
      </div>
      <div className="mono" style={{ ...styles.perfValue, color }}>
        {value}
      </div>
    </div>
  );
}

const styles = {
  headRow: {
    display: "flex",
    justifyContent: "space-between",
    gap: 16,
    marginBottom: 20,
  },
  blockLabel: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    marginBottom: 6,
  },
  whatChanged: {
    fontSize: 14,
    lineHeight: 1.65,
    color: "var(--text-primary)",
    margin: 0,
    maxWidth: 720,
  },
  badges: {
    flexShrink: 0,
    paddingTop: 2,
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
    fontSize: 15,
    fontWeight: 700,
    color: "var(--text-primary)",
  },
  perfRow: {
    display: "grid",
    gridTemplateColumns: "repeat(3, 1fr)",
    gap: 14,
    marginBottom: 14,
  },
  perfBlock: {
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-soft)",
    borderRadius: 10,
    padding: "12px 14px",
  },
  perfLabel: {
    fontSize: 11,
    marginBottom: 5,
  },
  perfValue: {
    fontSize: 17,
    fontWeight: 700,
  },
  detectedAt: {
    fontSize: 11.5,
  },
};
