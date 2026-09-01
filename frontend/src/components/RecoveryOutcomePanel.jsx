import React from "react";
import { formatMoney, formatNumber, formatPercent } from "../lib/format.js";

/**
 * @param {{
 *   revenueRecovered: number,
 *   transactionsRecovered: number,
 *   transactionsAttempted: number,
 *   beforeAfter: ReturnType<typeof import('../lib/derive.js').baselineVsCurrent> | null
 * }} props
 */
export function RecoveryOutcomePanel({ revenueRecovered, transactionsRecovered, transactionsAttempted, beforeAfter }) {
  return (
    <div>
      <div style={styles.heroRow}>
        <div>
          <div className="text-tertiary" style={styles.blockLabel}>
            Amount recovered
          </div>
          <div className="mono" style={styles.heroValue}>
            {formatMoney(revenueRecovered, { precise: true })}
          </div>
        </div>
        <div>
          <div className="text-tertiary" style={styles.blockLabel}>
            Transactions recovered
          </div>
          <div className="mono" style={styles.heroValueSmall}>
            {formatNumber(transactionsRecovered)}{" "}
            <span className="text-tertiary" style={{ fontSize: 14, fontWeight: 500 }}>
              / {formatNumber(transactionsAttempted)} attempted
            </span>
          </div>
        </div>
      </div>

      {beforeAfter ? (
        <div style={styles.beforeAfterBlock}>
          <div className="text-tertiary" style={styles.blockLabel}>
            Before / after — failure rate for this segment
          </div>
          <div style={styles.compareRow}>
            <CompareCard label="Baseline (normal)" rate={beforeAfter.baselineFailureRate} count={beforeAfter.baselineCount} tone="neutral" />
            <div style={styles.arrow}>→</div>
            <CompareCard label="During incident" rate={beforeAfter.currentFailureRate} count={beforeAfter.currentCount} tone="negative" />
          </div>
        </div>
      ) : (
        <div className="empty-state" style={{ padding: "24px 0" }}>
          Before/after evidence not available for this incident.
        </div>
      )}
    </div>
  );
}

function CompareCard({ label, rate, count, tone }) {
  const color = tone === "negative" ? "var(--negative)" : "var(--text-primary)";
  return (
    <div style={styles.compareCard}>
      <div className="text-tertiary" style={styles.compareLabel}>
        {label}
      </div>
      <div className="mono" style={{ ...styles.compareRate, color }}>
        {formatPercent(rate)}
      </div>
      <div className="text-tertiary" style={styles.compareCount}>
        failure rate · {formatNumber(count)} txns
      </div>
    </div>
  );
}

const styles = {
  heroRow: {
    display: "flex",
    gap: 40,
    marginBottom: 22,
    paddingBottom: 22,
    borderBottom: "1px solid var(--border-soft)",
    flexWrap: "wrap",
  },
  blockLabel: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    marginBottom: 8,
  },
  heroValue: {
    fontSize: 30,
    fontWeight: 700,
    color: "var(--positive)",
  },
  heroValueSmall: {
    fontSize: 22,
    fontWeight: 700,
    color: "var(--text-primary)",
  },
  beforeAfterBlock: {},
  compareRow: {
    display: "flex",
    alignItems: "center",
    gap: 16,
  },
  compareCard: {
    flex: 1,
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-soft)",
    borderRadius: 12,
    padding: "14px 16px",
  },
  compareLabel: {
    fontSize: 11.5,
    marginBottom: 6,
  },
  compareRate: {
    fontSize: 22,
    fontWeight: 700,
  },
  compareCount: {
    fontSize: 11.5,
    marginTop: 4,
  },
  arrow: {
    color: "var(--text-tertiary)",
    fontSize: 18,
    flexShrink: 0,
  },
};
