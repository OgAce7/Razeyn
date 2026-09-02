import React, { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadDataset } from "../api/client.js";
import { useDataset } from "../lib/DatasetContext.jsx";

const REQUIRED_COLUMNS = [
  "transaction_id",
  "timestamp",
  "customer_id",
  "amount",
  "currency",
  "payment_method",
  "institution",
  "geography",
  "status",
  "failure_reason",
  "processing_latency_ms",
  "retry_count",
  "checkout_context",
];

export default function UploadPage() {
  const { notifyDatasetChanged } = useDataset();
  const navigate = useNavigate();
  const fileInputRef = useRef(null);

  const [selectedFile, setSelectedFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  function handleFileChange(e) {
    setResult(null);
    setError(null);
    setSelectedFile(e.target.files?.[0] ?? null);
  }

  async function handleUpload() {
    if (!selectedFile) return;
    setUploading(true);
    setError(null);
    setResult(null);
    try {
      const uploadResult = await uploadDataset(selectedFile);
      setResult(uploadResult);
      await notifyDatasetChanged();
    } catch (err) {
      setError(err);
    } finally {
      setUploading(false);
    }
  }

  function handlePickAnother() {
    setSelectedFile(null);
    setResult(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div>
      <header style={styles.pageHeader}>
        <div>
          <h1 style={styles.title}>Upload a dataset</h1>
          <p className="text-secondary" style={styles.subtitle}>
            Upload a transactions CSV matching the expected schema. It runs through the real
            detection → evidence retrieval → AI investigation → policy → recovery pipeline, and
            becomes the active dataset for the whole dashboard.
          </p>
        </div>
      </header>

      <section className="card card-padded" style={{ marginBottom: 20 }}>
        <h2 className="section-title" style={{ margin: 0, marginBottom: 10 }}>
          Required columns
        </h2>
        <div style={styles.columnPills}>
          {REQUIRED_COLUMNS.map((col) => (
            <code key={col} style={styles.columnPill}>
              {col}
            </code>
          ))}
        </div>
        <p className="text-secondary" style={styles.columnNote}>
          <code>status</code> must be one of SUCCESS / FAILED / PENDING; <code>failure_reason</code>{" "}
          is required for FAILED rows and blank otherwise; <code>currency</code> must be INR.
          Rows that don't validate are dropped individually with a reason shown after upload — a
          few bad rows won't block the rest of the file.
        </p>
      </section>

      <section className="card card-padded">
        <h2 className="section-title" style={{ margin: 0, marginBottom: 14 }}>
          Choose a file
        </h2>

        <div style={styles.uploadRow}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,text/csv"
            onChange={handleFileChange}
            disabled={uploading}
            style={styles.fileInput}
          />
          <button
            className="btn"
            disabled={!selectedFile || uploading}
            onClick={handleUpload}
          >
            {uploading ? "Uploading & running pipeline…" : "Upload and run"}
          </button>
        </div>

        {uploading && (
          <p className="text-secondary" style={styles.processingNote}>
            Running detection, evidence retrieval, AI investigation, and policy evaluation against
            every candidate incident found — this can take a moment for larger files.
          </p>
        )}

        {error && <UploadErrorPanel error={error} />}

        {result && (
          <div style={styles.successPanel}>
            <div style={styles.successHeader}>✓ Dataset uploaded and pipeline complete</div>
            <dl style={styles.successStats}>
              <div style={styles.statItem}>
                <dt style={styles.statLabel}>Rows read</dt>
                <dd style={styles.statValue}>{result.validation_summary.rows_read.toLocaleString()}</dd>
              </div>
              <div style={styles.statItem}>
                <dt style={styles.statLabel}>Rows used</dt>
                <dd style={styles.statValue}>{result.validation_summary.rows_valid.toLocaleString()}</dd>
              </div>
              <div style={styles.statItem}>
                <dt style={styles.statLabel}>Rows dropped</dt>
                <dd style={styles.statValue}>{result.validation_summary.rows_dropped.toLocaleString()}</dd>
              </div>
              <div style={styles.statItem}>
                <dt style={styles.statLabel}>Candidate incidents found</dt>
                <dd style={styles.statValue}>{result.candidate_count}</dd>
              </div>
            </dl>

            {result.validation_summary.warnings.length > 0 && (
              <div style={styles.warningsBlock}>
                <div style={styles.warningsLabel}>
                  {result.validation_summary.rows_dropped} row(s) were dropped:
                </div>
                <ul style={styles.warningsList}>
                  {result.validation_summary.warnings.map((w, i) => (
                    <li key={i} style={styles.warningItem}>
                      {w.message}
                      {w.total_affected_rows > 0 && (
                        <span style={styles.warningRows}>
                          {" "}
                          ({w.total_affected_rows} row{w.total_affected_rows === 1 ? "" : "s"}
                          {w.sample_row_numbers ? `, e.g. row ${w.sample_row_numbers.slice(0, 5).join(", ")}` : ""})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div style={styles.successActions}>
              <button className="btn" onClick={() => navigate("/incidents")}>
                View incidents from this dataset →
              </button>
              <button className="btn btn-ghost" onClick={handlePickAnother}>
                Upload another file
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function UploadErrorPanel({ error }) {
  const validationErrors = error.detail?.errors;

  return (
    <div className="error-banner" style={styles.errorPanel}>
      <div style={styles.errorHeader}>Upload rejected</div>
      {Array.isArray(validationErrors) && validationErrors.length > 0 ? (
        <ul style={styles.errorList}>
          {validationErrors.map((e, i) => (
            <li key={i} style={styles.errorItem}>
              {e.message}
              {e.total_affected_rows > 0 && (
                <span style={styles.warningRows}>
                  {" "}
                  ({e.total_affected_rows} row{e.total_affected_rows === 1 ? "" : "s"}
                  {e.sample_row_numbers ? `, e.g. row ${e.sample_row_numbers.slice(0, 5).join(", ")}` : ""})
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p style={styles.errorItem}>{error.message}</p>
      )}
    </div>
  );
}

const styles = {
  pageHeader: { marginBottom: 20 },
  title: { fontSize: 22, fontWeight: 700, margin: 0, letterSpacing: "-0.01em" },
  subtitle: { fontSize: 13.5, margin: "6px 0 0", maxWidth: 640, lineHeight: 1.5 },
  columnPills: { display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 },
  columnPill: {
    fontSize: 11.5,
    background: "var(--bg-card)",
    border: "1px solid var(--border-soft)",
    borderRadius: 6,
    padding: "3px 8px",
    color: "var(--text-secondary)",
  },
  columnNote: { fontSize: 12.5, lineHeight: 1.5, margin: 0 },
  uploadRow: { display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" },
  fileInput: { fontSize: 13, color: "var(--text-secondary)" },
  processingNote: { fontSize: 12.5, marginTop: 12, marginBottom: 0 },
  errorPanel: { marginTop: 16 },
  errorHeader: { fontWeight: 700, marginBottom: 6, fontSize: 13 },
  errorList: { margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 },
  errorItem: { fontSize: 12.5, lineHeight: 1.5, margin: 0 },
  warningRows: { color: "var(--text-tertiary)" },
  successPanel: {
    marginTop: 18,
    border: "1px solid rgba(51, 214, 159, 0.3)",
    background: "var(--positive-soft)",
    borderRadius: 10,
    padding: "16px 18px",
  },
  successHeader: { fontWeight: 700, color: "var(--positive)", marginBottom: 12, fontSize: 13.5 },
  successStats: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
    gap: 12,
    margin: "0 0 8px",
  },
  statItem: { margin: 0 },
  statLabel: { fontSize: 10.5, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.04em", margin: 0 },
  statValue: { fontSize: 16, fontWeight: 700, color: "var(--text-primary)", margin: "2px 0 0" },
  warningsBlock: { marginTop: 12, borderTop: "1px solid rgba(51, 214, 159, 0.25)", paddingTop: 12 },
  warningsLabel: { fontSize: 12, fontWeight: 600, marginBottom: 6, color: "var(--text-secondary)" },
  warningsList: { margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 },
  warningItem: { fontSize: 12, lineHeight: 1.5, color: "var(--text-secondary)" },
  successActions: { display: "flex", gap: 10, marginTop: 16 },
};
