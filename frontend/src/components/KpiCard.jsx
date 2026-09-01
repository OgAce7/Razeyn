import React from "react";

/**
 * @param {Object} props
 * @param {string} props.label
 * @param {string} props.value
 * @param {string} [props.sublabel]
 * @param {"positive"|"negative"|"neutral"|"warning"} [props.tone]
 * @param {string} [props.delta] pre-formatted delta string, e.g. "-5.3% vs baseline"
 */
export function KpiCard({ label, value, sublabel, tone = "neutral", delta }) {
  return (
    <div className="card card-padded" style={styles.card}>
      <div style={styles.label}>{label}</div>
      <div className="mono" style={styles.value}>
        {value}
      </div>
      {(sublabel || delta) && (
        <div style={styles.footRow}>
          {sublabel && <span style={styles.sublabel}>{sublabel}</span>}
          {delta && <span style={{ ...styles.delta, color: TONE_COLOR[tone] }}>{delta}</span>}
        </div>
      )}
    </div>
  );
}

const TONE_COLOR = {
  positive: "var(--positive)",
  negative: "var(--negative)",
  warning: "var(--warning)",
  neutral: "var(--text-tertiary)",
};

const styles = {
  card: {
    minHeight: 108,
    display: "flex",
    flexDirection: "column",
    justifyContent: "space-between",
    gap: 10,
  },
  label: {
    fontSize: 12.5,
    fontWeight: 600,
    color: "var(--text-tertiary)",
    letterSpacing: "0.02em",
  },
  value: {
    fontSize: 28,
    fontWeight: 700,
    color: "var(--text-primary)",
    letterSpacing: "-0.01em",
  },
  footRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    fontSize: 12.5,
  },
  sublabel: {
    color: "var(--text-secondary)",
  },
  delta: {
    fontWeight: 600,
  },
};
