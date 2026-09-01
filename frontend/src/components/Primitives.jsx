import React from "react";

export function DemoBadge({ source }) {
  if (source !== "mock") return null;
  return (
    <span className="demo-badge" title="This data comes from a real backend pipeline run, replayed as fixture data until the live API endpoint exists.">
      ● Demo data
    </span>
  );
}

export function LoadingBlock({ height = 120 }) {
  return <div className="skeleton" style={{ height, width: "100%" }} />;
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="error-banner">
      <strong>Couldn't load data.</strong> {error?.message || "Unknown error."}
      {onRetry && (
        <button className="btn btn-ghost" style={{ marginLeft: 12 }} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ children }) {
  return <div className="empty-state">{children}</div>;
}

const SEVERITY_LABEL = {
  LOW: "Low",
  MEDIUM: "Medium",
  HIGH: "High",
  CRITICAL: "Critical",
};

export function SeverityPill({ severity }) {
  const pillClass =
    severity === "CRITICAL" || severity === "HIGH"
      ? "pill-negative"
      : severity === "MEDIUM"
      ? "pill-warning"
      : "pill-neutral";
  return (
    <span className={`pill ${pillClass}`}>
      <span className={`sev-dot sev-${severity}`} />
      {SEVERITY_LABEL[severity] || severity}
    </span>
  );
}

const STATUS_META = {
  SIMULATED: { label: "Executed (simulated)", cls: "pill-positive" },
  EXECUTED: { label: "Executed", cls: "pill-positive" },
  NOT_EXECUTED_REJECTED: { label: "Rejected by policy", cls: "pill-negative" },
  NOT_EXECUTED_ESCALATED: { label: "Escalated to human", cls: "pill-warning" },
  NOT_EXECUTED_STOPPED: { label: "Stopped (no action needed)", cls: "pill-neutral" },
  NOT_EXECUTED_WAIT: { label: "Waiting / reassess", cls: "pill-neutral" },
};

export function ExecutionStatusPill({ status }) {
  const meta = STATUS_META[status] || { label: status, cls: "pill-neutral" };
  return <span className={`pill ${meta.cls}`}>{meta.label}</span>;
}

export function ApprovalPill({ approved }) {
  return approved ? (
    <span className="pill pill-positive">Approved</span>
  ) : (
    <span className="pill pill-negative">Rejected</span>
  );
}
