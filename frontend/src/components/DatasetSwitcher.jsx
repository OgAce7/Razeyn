import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useDataset } from "../lib/DatasetContext.jsx";
import { activateDataset } from "../api/client.js";

/**
 * Small "using dataset X" indicator + switcher, always visible in the
 * sidebar so it's never ambiguous which dataset the rest of the
 * dashboard is currently showing. Only the seeded dataset can be
 * re-activated on demand once you've navigated away from it (see
 * backend/app/api/datasets.py) -- past uploads are listed for
 * visibility but require re-uploading the file to view again, which is
 * called out inline rather than offered as a broken action.
 */
export function DatasetSwitcher() {
  const { datasets, activeDataset, loading, error, notifyDatasetChanged } = useDataset();
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState(null);
  const [open, setOpen] = useState(false);

  async function handleSwitchToSeeded() {
    setSwitchError(null);
    setSwitching(true);
    try {
      await activateDataset("seeded");
      await notifyDatasetChanged();
      setOpen(false);
    } catch (err) {
      setSwitchError(err);
    } finally {
      setSwitching(false);
    }
  }

  if (loading) {
    return <div style={styles.wrap}><div style={styles.skeleton} /></div>;
  }

  if (error) {
    return (
      <div style={styles.wrap}>
        <div style={styles.label}>Dataset</div>
        <div style={styles.errorText}>Couldn't load dataset info.</div>
      </div>
    );
  }

  const isSeeded = activeDataset?.kind === "seeded";
  const uploadedDatasets = datasets.filter((d) => d.kind === "uploaded");

  return (
    <div style={styles.wrap}>
      <button style={styles.trigger} onClick={() => setOpen((o) => !o)}>
        <div>
          <div style={styles.label}>Dataset</div>
          <div style={styles.activeLabel} title={activeDataset?.label}>
            {activeDataset?.label ?? "—"}
          </div>
          <div style={styles.activeMeta}>
            {activeDataset ? `${activeDataset.row_count.toLocaleString()} txns · ${activeDataset.candidate_count} incident(s)` : ""}
          </div>
        </div>
        <span style={styles.chevron}>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div style={styles.menu}>
          {!isSeeded && (
            <button style={styles.menuItem} disabled={switching} onClick={handleSwitchToSeeded}>
              {switching ? "Switching…" : "↺ Use seeded synthetic dataset"}
            </button>
          )}
          {isSeeded && <div style={styles.menuNote}>Currently using the seeded synthetic dataset.</div>}

          {uploadedDatasets.length > 0 && (
            <div style={styles.uploadHistory}>
              <div style={styles.uploadHistoryLabel}>Previous uploads</div>
              {uploadedDatasets.map((d) => (
                <div key={d.dataset_id} style={styles.uploadHistoryItem}>
                  <span title={d.label} style={styles.uploadHistoryItemName}>
                    {d.dataset_id === activeDataset?.dataset_id ? "● " : ""}
                    {d.label}
                  </span>
                  {d.dataset_id !== activeDataset?.dataset_id && (
                    <span style={styles.uploadHistoryHint}>re-upload to view</span>
                  )}
                </div>
              ))}
            </div>
          )}

          <Link to="/upload" className="btn btn-ghost" style={styles.uploadLink} onClick={() => setOpen(false)}>
            ⇪ Upload a new dataset
          </Link>

          {switchError && <div style={styles.errorText}>{switchError.message}</div>}
        </div>
      )}
    </div>
  );
}

const styles = {
  wrap: {
    marginBottom: 20,
    position: "relative",
  },
  skeleton: {
    height: 52,
    borderRadius: 10,
    background: "var(--bg-card)",
    border: "1px solid var(--border-soft)",
  },
  trigger: {
    width: "100%",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    background: "var(--bg-card)",
    border: "1px solid var(--border-soft)",
    borderRadius: 10,
    padding: "9px 11px",
    cursor: "pointer",
    textAlign: "left",
  },
  label: {
    fontSize: 10,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    color: "var(--text-tertiary)",
    marginBottom: 2,
  },
  activeLabel: {
    fontSize: 12.5,
    fontWeight: 600,
    color: "var(--text-primary)",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    maxWidth: 150,
  },
  activeMeta: {
    fontSize: 10.5,
    color: "var(--text-tertiary)",
    marginTop: 1,
  },
  chevron: {
    fontSize: 9,
    color: "var(--text-tertiary)",
    flexShrink: 0,
  },
  menu: {
    marginTop: 6,
    background: "var(--bg-card)",
    border: "1px solid var(--border-soft)",
    borderRadius: 10,
    padding: 10,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  menuItem: {
    background: "var(--accent-soft)",
    color: "var(--accent-strong)",
    border: "1px solid rgba(78, 168, 255, 0.25)",
    borderRadius: 8,
    padding: "7px 10px",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
    textAlign: "left",
  },
  menuNote: {
    fontSize: 11.5,
    color: "var(--text-tertiary)",
  },
  uploadHistory: {
    borderTop: "1px solid var(--border-soft)",
    paddingTop: 8,
  },
  uploadHistoryLabel: {
    fontSize: 10,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    color: "var(--text-tertiary)",
    marginBottom: 6,
  },
  uploadHistoryItem: {
    display: "flex",
    justifyContent: "space-between",
    gap: 6,
    fontSize: 11.5,
    color: "var(--text-secondary)",
    padding: "3px 0",
  },
  uploadHistoryItemName: {
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  uploadHistoryHint: {
    fontSize: 10,
    color: "var(--text-tertiary)",
    flexShrink: 0,
  },
  uploadLink: {
    textAlign: "center",
    fontSize: 12,
  },
  errorText: {
    fontSize: 11,
    color: "var(--negative)",
  },
};
