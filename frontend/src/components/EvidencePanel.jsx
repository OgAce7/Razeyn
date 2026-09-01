import React, { useState } from "react";
import { formatDateTime, formatPercent, humanizeAction } from "../lib/format.js";

/**
 * @param {{ bundle: import('../api/types.js').EvidenceBundle | null }} props
 */
export function EvidencePanel({ bundle }) {
  const [tab, setTab] = useState("structured");

  if (!bundle) {
    return <div className="empty-state">No evidence bundle available for this incident.</div>;
  }

  const structured = bundle.structured_evidence || [];
  const unstructured = bundle.unstructured_evidence || [];

  return (
    <div>
      <div style={styles.tabRow}>
        <TabButton active={tab === "structured"} onClick={() => setTab("structured")}>
          Structured data ({structured.length})
        </TabButton>
        <TabButton active={tab === "unstructured"} onClick={() => setTab("unstructured")}>
          Reports & documents ({unstructured.length})
        </TabButton>
      </div>

      {tab === "structured" ? (
        <div style={styles.evidenceList}>
          {structured.length === 0 ? (
            <div className="empty-state">No structured evidence.</div>
          ) : (
            structured.map((item) => <StructuredEvidenceCard key={item.evidence_id} item={item} />)
          )}
        </div>
      ) : (
        <div style={styles.evidenceList}>
          {unstructured.length === 0 ? (
            <div className="empty-state">No supporting documents.</div>
          ) : (
            unstructured.map((item) => <UnstructuredEvidenceCard key={item.evidence_id} item={item} />)
          )}
        </div>
      )}
    </div>
  );
}

function TabButton({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`btn ${active ? "" : "btn-ghost"}`}
      style={{ fontSize: 12.5 }}
    >
      {children}
    </button>
  );
}

function StructuredEvidenceCard({ item }) {
  return (
    <div style={styles.evidenceCard}>
      <div style={styles.evidenceHeadRow}>
        <span className="pill pill-accent">{humanizeAction(item.evidence_type)}</span>
        <span className="text-tertiary" style={styles.relevance}>
          relevance {formatPercent(item.relevance_score, { digits: 0 })}
        </span>
      </div>
      <DataPreview data={item.data} />
      <EvidenceSource source={item.source} timestamp={item.timestamp} evidenceId={item.evidence_id} />
    </div>
  );
}

function UnstructuredEvidenceCard({ item }) {
  return (
    <div style={styles.evidenceCard}>
      <div style={styles.evidenceHeadRow}>
        <span className="pill pill-neutral">{humanizeAction(item.evidence_type)}</span>
        <span className="text-tertiary" style={styles.relevance}>
          relevance {formatPercent(item.relevance_score, { digits: 0 })}
        </span>
      </div>
      {item.title && <div style={styles.docTitle}>{item.title}</div>}
      <p style={styles.docText}>{item.text}</p>
      <EvidenceSource source={item.source} timestamp={item.timestamp} evidenceId={item.evidence_id} />
    </div>
  );
}

/** Renders a flat key/value preview of a structured-evidence data
 * object. For array-valued "breakdown" fields, shows the first few rows
 * as a mini table rather than dumping raw JSON. */
function DataPreview({ data }) {
  if (!data) return null;
  const entries = Object.entries(data);
  return (
    <div style={styles.dataGrid}>
      {entries.map(([key, value]) => {
        if (Array.isArray(value)) {
          return (
            <div key={key} style={styles.dataGridFullRow}>
              <div className="text-tertiary" style={styles.dataKey}>
                {humanizeAction(key)}
              </div>
              <BreakdownTable rows={value} />
            </div>
          );
        }
        if (value !== null && typeof value === "object") {
          return (
            <div key={key} style={styles.dataGridFullRow}>
              <div className="text-tertiary" style={styles.dataKey}>
                {humanizeAction(key)}
              </div>
              <div style={styles.dataValue}>{JSON.stringify(value)}</div>
            </div>
          );
        }
        return (
          <div key={key} style={styles.dataPair}>
            <div className="text-tertiary" style={styles.dataKey}>
              {humanizeAction(key)}
            </div>
            <div className="mono" style={styles.dataValue}>
              {String(value)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function BreakdownTable({ rows }) {
  if (!rows || rows.length === 0) return null;
  const cols = Object.keys(rows[0]);
  return (
    <table style={styles.table}>
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c} style={styles.th}>
              {humanizeAction(c)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 6).map((row, i) => (
          <tr key={i}>
            {cols.map((c) => (
              <td key={c} className="mono" style={styles.td}>
                {typeof row[c] === "number" && c.toLowerCase().includes("rate")
                  ? formatPercent(row[c])
                  : String(row[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EvidenceSource({ source, timestamp, evidenceId }) {
  return (
    <div style={styles.sourceRow}>
      <span style={styles.sourceIcon}>▤</span>
      <span className="text-secondary" style={styles.sourceText}>
        {source}
      </span>
      <span className="text-tertiary mono" style={styles.sourceMeta}>
        {evidenceId} · {formatDateTime(timestamp)}
      </span>
    </div>
  );
}

const styles = {
  tabRow: {
    display: "flex",
    gap: 8,
    marginBottom: 16,
  },
  evidenceList: {
    display: "flex",
    flexDirection: "column",
    gap: 12,
    maxHeight: 520,
    overflowY: "auto",
    paddingRight: 4,
  },
  evidenceCard: {
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-soft)",
    borderRadius: 12,
    padding: "14px 16px",
  },
  evidenceHeadRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 10,
  },
  relevance: {
    fontSize: 11.5,
  },
  docTitle: {
    fontSize: 13.5,
    fontWeight: 700,
    color: "var(--text-primary)",
    marginBottom: 6,
  },
  docText: {
    fontSize: 13,
    lineHeight: 1.55,
    color: "var(--text-secondary)",
    margin: "0 0 12px",
  },
  dataGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "6px 16px",
    marginBottom: 12,
  },
  dataGridFullRow: {
    gridColumn: "1 / -1",
  },
  dataPair: {
    display: "flex",
    flexDirection: "column",
    gap: 1,
  },
  dataKey: {
    fontSize: 10.5,
    textTransform: "uppercase",
    letterSpacing: "0.03em",
  },
  dataValue: {
    fontSize: 12.5,
    color: "var(--text-primary)",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    marginTop: 4,
    fontSize: 11.5,
  },
  th: {
    textAlign: "left",
    color: "var(--text-tertiary)",
    fontWeight: 600,
    padding: "4px 8px 4px 0",
    borderBottom: "1px solid var(--border-soft)",
    whiteSpace: "nowrap",
  },
  td: {
    padding: "4px 8px 4px 0",
    color: "var(--text-primary)",
    borderBottom: "1px solid var(--border-soft)",
    whiteSpace: "nowrap",
  },
  sourceRow: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    paddingTop: 10,
    borderTop: "1px solid var(--border-soft)",
    fontSize: 11.5,
  },
  sourceIcon: {
    color: "var(--text-tertiary)",
    fontSize: 11,
  },
  sourceText: {
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  sourceMeta: {
    fontSize: 10.5,
    flexShrink: 0,
  },
};
