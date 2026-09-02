import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import { getAuditTrail, getEvaluationReport } from "../api/client.js";
import { useApiData } from "../lib/useApiData.js";
import { useDataset } from "../lib/DatasetContext.jsx";
import { DemoBadge, LoadingBlock, ErrorState } from "../components/Primitives.jsx";
import { KpiCard } from "../components/KpiCard.jsx";
import { IncidentTimeline } from "../components/IncidentTimeline.jsx";
import { RevenueByIncidentChart } from "../components/RevenueByIncidentChart.jsx";
import { CumulativeRecoveryChart } from "../components/CumulativeRecoveryChart.jsx";
import { SeverityBreakdownChart } from "../components/SeverityBreakdownChart.jsx";
import {
  toTimelineRows,
  severityBreakdown,
  revenueByIncident,
  cumulativeRecoveryOverTime,
} from "../lib/derive.js";
import { formatMoney, formatPercent, formatNumber } from "../lib/format.js";

export default function OverviewPage() {
  const { datasetVersion, activeDataset } = useDataset();
  const auditState = useApiData(getAuditTrail, [datasetVersion]);
  const reportState = useApiData(getEvaluationReport, [datasetVersion]);

  const timelineRows = useMemo(
    () => (auditState.data ? toTimelineRows(auditState.data) : []),
    [auditState.data]
  );
  const severityData = useMemo(
    () => (auditState.data ? severityBreakdown(auditState.data) : []),
    [auditState.data]
  );
  const revenueData = useMemo(
    () => (auditState.data ? revenueByIncident(auditState.data) : []),
    [auditState.data]
  );
  const cumulativeData = useMemo(
    () => (auditState.data ? cumulativeRecoveryOverTime(auditState.data) : []),
    [auditState.data]
  );

  const activeIncidents = useMemo(() => {
    if (!auditState.data) return 0;
    return auditState.data.filter((r) =>
      ["SIMULATED", "EXECUTED", "NOT_EXECUTED_ESCALATED"].includes(r.action_outcome.execution_status)
    ).length;
  }, [auditState.data]);

  const successfulInterventions = useMemo(() => {
    if (!auditState.data) return 0;
    return auditState.data.reduce((sum, r) => sum + r.action_outcome.succeeded, 0);
  }, [auditState.data]);

  const loading = auditState.loading || reportState.loading;
  const error = auditState.error || reportState.error;
  const report = reportState.data;

  return (
    <div>
      <header style={styles.pageHeader}>
        <div>
          <h1 style={styles.title}>Executive overview</h1>
          <p className="text-secondary" style={styles.subtitle}>
            Revenue at risk, through detection, diagnosis, and recovery — in one view.
          </p>
        </div>
        <div style={styles.headerBadges}>
          <DemoBadge source={auditState.source} />
        </div>
      </header>

      {error && <ErrorState error={error} onRetry={() => { auditState.reload(); reportState.reload(); }} />}

      {!error && (
        <>
          <section style={styles.kpiGrid}>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => <LoadingBlock key={i} height={108} />)
            ) : (
              <>
                <KpiCard
                  label="Revenue at risk"
                  value={formatMoney(report?.revenue?.total_revenue_at_risk, { precise: true })}
                  sublabel={`across ${formatNumber(report?.record_count)} incidents`}
                  tone="warning"
                />
                <KpiCard
                  label="Revenue recovered"
                  value={formatMoney(report?.revenue?.total_revenue_recovered, { precise: true })}
                  sublabel="via automated recovery actions"
                  tone="positive"
                />
                <KpiCard
                  label="Recovery rate"
                  value={formatPercent(report?.revenue?.recovery_rate)}
                  sublabel={
                    report?.revenue?.recovery_uplift_vs_baseline_pct != null
                      ? `${report.revenue.recovery_uplift_vs_baseline_pct > 0 ? "+" : ""}${report.revenue.recovery_uplift_vs_baseline_pct.toFixed(1)}% vs fixed-rule baseline`
                      : undefined
                  }
                  tone={
                    report?.revenue?.recovery_uplift_vs_baseline_pct >= 0 ? "positive" : "negative"
                  }
                />
                <KpiCard
                  label="Active incidents"
                  value={formatNumber(activeIncidents)}
                  sublabel="executed, simulated, or escalated"
                  tone="neutral"
                />
                <KpiCard
                  label="Successful interventions"
                  value={formatNumber(successfulInterventions)}
                  sublabel={`${formatPercent(report?.actions?.success_rate_of_attempted)} of attempted retries`}
                  tone="positive"
                />
              </>
            )}
          </section>

          <section className="card card-padded" style={{ marginTop: 20 }}>
            <div className="section-heading-row">
              <h2 className="section-title" style={{ margin: 0 }}>
                Revenue at risk vs. recovered, by incident
              </h2>
            </div>
            {loading ? <LoadingBlock height={280} /> : <RevenueByIncidentChart data={revenueData} />}
          </section>

          <div style={styles.twoCol}>
            <section className="card card-padded">
              <h2 className="section-title" style={{ margin: 0, marginBottom: 4 }}>
                Cumulative revenue recovered
              </h2>
              {loading ? <LoadingBlock height={220} /> : <CumulativeRecoveryChart data={cumulativeData} />}
            </section>

            <section className="card card-padded">
              <h2 className="section-title" style={{ margin: 0, marginBottom: 4 }}>
                Incidents by severity
              </h2>
              {loading ? <LoadingBlock height={160} /> : <SeverityBreakdownChart data={severityData} />}
            </section>
          </div>

          <section className="card card-padded" style={{ marginTop: 20 }}>
            <div className="section-heading-row">
              <h2 className="section-title" style={{ margin: 0 }}>
                Recent incidents
              </h2>
              <Link to="/incidents" className="btn btn-ghost" style={{ display: "inline-block" }}>
                View all incidents →
              </Link>
            </div>
            {loading ? <LoadingBlock height={220} /> : <IncidentTimeline rows={timelineRows} limit={5} />}
          </section>
        </>
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
  headerBadges: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    paddingTop: 4,
  },
  kpiGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
    gap: 16,
  },
  twoCol: {
    display: "grid",
    gridTemplateColumns: "1.4fr 1fr",
    gap: 20,
    marginTop: 20,
  },
};
