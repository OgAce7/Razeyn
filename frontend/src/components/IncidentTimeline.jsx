import React from "react";
import { Link } from "react-router-dom";
import { SeverityPill, ExecutionStatusPill } from "./Primitives.jsx";
import { formatDateTime, formatMoney, formatPercent, formatSegment, humanizeAction } from "../lib/format.js";

/**
 * @param {{ rows: ReturnType<typeof import('../lib/derive.js').toTimelineRows> }} props
 */
export function IncidentTimeline({ rows, limit }) {
  const visible = limit ? rows.slice(0, limit) : rows;

  if (visible.length === 0) {
    return <div className="empty-state">No incidents detected in this window.</div>;
  }

  return (
    <div style={styles.list}>
      {visible.map((row) => (
        <Link key={row.id} to={`/incidents/${row.id}`} style={styles.row} className="timeline-row">
          <div style={styles.timeCol}>
            <div style={styles.timestamp}>{formatDateTime(row.timestamp)}</div>
            <div className="text-tertiary" style={styles.idLabel}>
              {row.id}
            </div>
          </div>

          <div style={styles.middleCol}>
            <div style={styles.segmentLine}>{formatSegment(row.segment)}</div>
            <div className="text-secondary" style={styles.dimensionLine}>
              {humanizeAction(row.dimension)} anomaly · confidence {formatPercent(row.confidence, { digits: 0 })}
            </div>
          </div>

          <div style={styles.revenueCol}>
            <div className="mono" style={styles.revenueValue}>
              {formatMoney(row.revenueAffected, { precise: true })}
            </div>
            <div className="text-tertiary" style={styles.revenueLabel}>
              at risk
            </div>
          </div>

          <div style={styles.badgeCol}>
            <SeverityPill severity={row.severity} />
            <ExecutionStatusPill status={row.status} />
          </div>
        </Link>
      ))}
    </div>
  );
}

const styles = {
  list: {
    display: "flex",
    flexDirection: "column",
  },
  row: {
    display: "grid",
    gridTemplateColumns: "168px 1fr 160px 220px",
    alignItems: "center",
    gap: 16,
    padding: "14px 6px",
    borderBottom: "1px solid var(--border-soft)",
    transition: "background 0.12s ease",
  },
  timeCol: {
    display: "flex",
    flexDirection: "column",
    gap: 3,
  },
  timestamp: {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
  idLabel: {
    fontSize: 11,
    fontFamily: "var(--font-mono)",
  },
  middleCol: {
    display: "flex",
    flexDirection: "column",
    gap: 3,
    minWidth: 0,
  },
  segmentLine: {
    fontSize: 13.5,
    fontWeight: 600,
    color: "var(--text-primary)",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  dimensionLine: {
    fontSize: 12,
  },
  revenueCol: {
    textAlign: "right",
  },
  revenueValue: {
    fontSize: 14,
    fontWeight: 700,
    color: "var(--text-primary)",
  },
  revenueLabel: {
    fontSize: 11,
  },
  badgeCol: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-end",
    gap: 6,
  },
};
