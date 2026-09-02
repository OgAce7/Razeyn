import React, { useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { getAuditTrail, getEvidence } from "../api/client.js";
import { useApiData } from "../lib/useApiData.js";
import { DemoBadge, LoadingBlock, ErrorState } from "../components/Primitives.jsx";
import { IncidentSummaryCard } from "../components/IncidentSummaryCard.jsx";
import { EvidencePanel } from "../components/EvidencePanel.jsx";
import { AiDecisionPanel } from "../components/AiDecisionPanel.jsx";
import { RecoveryExecutionPanel } from "../components/RecoveryExecutionPanel.jsx";
import { RecoveryOutcomePanel } from "../components/RecoveryOutcomePanel.jsx";
import { ApproveRejectPanel } from "../components/ApproveRejectPanel.jsx";
import { findRecordByIncidentId, revenueRecoveredForRecord, baselineVsCurrent } from "../lib/derive.js";

export default function IncidentDetailPage() {
  const { incidentId } = useParams();
  const auditState = useApiData(getAuditTrail, []);
  const evidenceState = useApiData(() => getEvidence(incidentId), [incidentId]);

  // Holds the freshly-resolved record returned directly by
  // POST /decision, so the UI updates the instant the call succeeds
  // rather than waiting on a second round-trip to re-fetch the audit
  // trail. Cleared whenever the route/incidentId changes.
  const [resolvedOverride, setResolvedOverride] = useState(null);
  const [justResolvedDecision, setJustResolvedDecision] = useState(null);

  const record = useMemo(() => {
    if (resolvedOverride && resolvedOverride.detection.candidate_incident_id === incidentId) {
      return resolvedOverride;
    }
    return auditState.data ? findRecordByIncidentId(auditState.data, incidentId) : null;
  }, [auditState.data, incidentId, resolvedOverride]);

  const beforeAfter = useMemo(() => baselineVsCurrent(evidenceState.data), [evidenceState.data]);

  const loading = auditState.loading || evidenceState.loading;
  const error = auditState.error || evidenceState.error;

  function handleResolved(newRecord) {
    setResolvedOverride(newRecord);
    setJustResolvedDecision(newRecord.policy_decision.approved ? "approve" : "reject");
    // Also refresh the underlying audit trail in the background so list
    // views (IncidentsPage) and any future navigation away-and-back see
    // the same resolved state, not just this page's local override.
    auditState.reload();
  }

  if (!loading && !error && auditState.data && !record) {
    return (
      <div>
        <BackLink />
        <div className="empty-state">Incident "{incidentId}" was not found in the audit trail.</div>
      </div>
    );
  }

  return (
    <div>
      <BackLink />

      <header style={styles.pageHeader}>
        <div>
          <h1 style={styles.title}>{incidentId}</h1>
          <p className="text-secondary" style={styles.subtitle}>
            Full chain: detection → evidence → AI diagnosis → recovery decision → action → outcome.
          </p>
        </div>
        <DemoBadge source={auditState.source} />
      </header>

      {error && <ErrorState error={error} onRetry={() => { auditState.reload(); evidenceState.reload(); }} />}

      {!error && (loading ? (
        <LoadingBlock height={520} />
      ) : record ? (
        <IncidentDetailBody
          incidentId={incidentId}
          record={record}
          evidenceBundle={evidenceState.data}
          beforeAfter={beforeAfter}
          onResolved={handleResolved}
          justResolvedDecision={justResolvedDecision}
        />
      ) : null)}
    </div>
  );
}

function IncidentDetailBody({ incidentId, record, evidenceBundle, beforeAfter, onResolved, justResolvedDecision }) {
  const recovered = revenueRecoveredForRecord(record);
  const isPendingDecision = record.action_outcome.execution_status === "NOT_EXECUTED_ESCALATED";

  return (
    <>
      <section className="card card-padded" style={{ marginBottom: 20 }}>
        <div className="section-heading-row">
          <h2 className="section-title" style={{ margin: 0 }}>
            3 · Incident detail
          </h2>
        </div>
        <IncidentSummaryCard detection={record.detection} beforeAfter={beforeAfter} />
      </section>

      <div style={styles.twoCol}>
        <section className="card card-padded">
          <h2 className="section-title" style={{ margin: 0 }}>
            4 · Evidence
          </h2>
          <p className="text-secondary" style={styles.panelIntro}>
            The exact evidence the AI diagnosis below was built from, with source attribution.
          </p>
          <EvidencePanel bundle={evidenceBundle} />
        </section>

        <section className="card card-padded">
          <h2 className="section-title" style={{ margin: 0 }}>
            5 · AI diagnosis
          </h2>
          <p className="text-secondary" style={styles.panelIntro}>
            What the agent concluded, and how confident it was.
          </p>
          <AiDecisionPanel agentDecision={record.agent_decision} />
        </section>
      </div>

      <section className="card card-padded" style={{ marginTop: 20 }}>
        <h2 className="section-title" style={{ margin: 0 }}>
          6 · Recovery decision & execution
        </h2>
        <p className="text-secondary" style={styles.panelIntro}>
          Every deterministic policy check the recommended action had to pass before it could run.
        </p>

        {!isPendingDecision && justResolvedDecision && (
          <div className="decision-toast">
            {justResolvedDecision === "approve" ? "✓ Approved and executed" : "✓ Rejected"}
          </div>
        )}

        {isPendingDecision && (
          <ApproveRejectPanel incidentId={incidentId} onResolved={onResolved} />
        )}

        <RecoveryExecutionPanel policyDecision={record.policy_decision} actionOutcome={record.action_outcome} />
      </section>

      <section className="card card-padded" style={{ marginTop: 20 }}>
        <h2 className="section-title" style={{ margin: 0 }}>
          7 · Recovery outcome
        </h2>
        <p className="text-secondary" style={styles.panelIntro}>
          Money recovered — the end of the story that started with revenue at risk.
        </p>
        <RecoveryOutcomePanel
          revenueRecovered={recovered}
          transactionsRecovered={record.action_outcome.succeeded}
          transactionsAttempted={record.action_outcome.attempted}
          beforeAfter={beforeAfter}
        />
      </section>
    </>
  );
}

function BackLink() {
  return (
    <Link to="/incidents" className="text-secondary" style={styles.backLink}>
      ← Back to incidents
    </Link>
  );
}

const styles = {
  backLink: {
    display: "inline-block",
    fontSize: 13,
    marginBottom: 16,
  },
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
    fontFamily: "var(--font-mono)",
  },
  subtitle: {
    fontSize: 13.5,
    margin: "6px 0 0",
  },
  twoCol: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 20,
  },
  panelIntro: {
    fontSize: 12.5,
    margin: "4px 0 18px",
  },
};
