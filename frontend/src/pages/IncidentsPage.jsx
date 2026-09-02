import React, { useMemo, useState } from "react";
import { getAuditTrail } from "../api/client.js";
import { useApiData } from "../lib/useApiData.js";
import { useDataset } from "../lib/DatasetContext.jsx";
import { DemoBadge, LoadingBlock, ErrorState } from "../components/Primitives.jsx";
import { IncidentTimeline } from "../components/IncidentTimeline.jsx";
import { toTimelineRows } from "../lib/derive.js";

const SEVERITY_FILTERS = ["ALL", "CRITICAL", "HIGH", "MEDIUM", "LOW"];

export default function IncidentsPage() {
  const { datasetVersion } = useDataset();
  const { data, loading, error, source, reload } = useApiData(getAuditTrail, [datasetVersion]);
  const [severityFilter, setSeverityFilter] = useState("ALL");

  const rows = useMemo(() => {
    const allRows = data ? toTimelineRows(data) : [];
    if (severityFilter === "ALL") return allRows;
    return allRows.filter((r) => r.severity === severityFilter);
  }, [data, severityFilter]);

  return (
    <div>
      <header style={styles.pageHeader}>
        <div>
          <h1 style={styles.title}>Incidents</h1>
          <p className="text-secondary" style={styles.subtitle}>
            Every incident the detector flagged, in the order it was detected.
          </p>
        </div>
        <DemoBadge source={source} />
      </header>

      {error && <ErrorState error={error} onRetry={reload} />}

      {!error && (
        <section className="card card-padded">
          <div className="section-heading-row">
            <h2 className="section-title" style={{ margin: 0 }}>
              Timeline ({rows.length})
            </h2>
            <div style={styles.filterRow}>
              {SEVERITY_FILTERS.map((sev) => (
                <button
                  key={sev}
                  className={`btn ${severityFilter === sev ? "" : "btn-ghost"}`}
                  onClick={() => setSeverityFilter(sev)}
                  style={styles.filterBtn}
                >
                  {sev === "ALL" ? "All" : sev.charAt(0) + sev.slice(1).toLowerCase()}
                </button>
              ))}
            </div>
          </div>
          {loading ? <LoadingBlock height={400} /> : <IncidentTimeline rows={rows} />}
        </section>
      )}
    </div>
  );
}

const styles = {
  pageHeader: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 16,
    marginBottom: 22,
    flexWrap: "wrap",
  },
  title: {
    fontSize: 22,
    fontWeight: 700,
    margin: 0,
    letterSpacing: "-0.01em",
  },
  subtitle: {
    fontSize: 13.5,
    margin: "6px 0 0",
  },
  filterRow: {
    display: "flex",
    gap: 6,
  },
  filterBtn: {
    padding: "6px 12px",
    fontSize: 12,
  },
};
