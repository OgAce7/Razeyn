import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { listDatasets } from "../api/client.js";

/**
 * Tracks which dataset is currently active (seeded synthetic, or an
 * upload) and exposes a `datasetVersion` counter that bumps every time
 * the active dataset changes. Pages that show dataset-derived data
 * (OverviewPage, IncidentsPage) include `datasetVersion` in their
 * `useApiData` deps so switching datasets automatically triggers a
 * refetch, without every page needing to know about datasets directly.
 */
const DatasetContext = createContext(null);

export function DatasetProvider({ children }) {
  const [datasets, setDatasets] = useState([]);
  const [activeDatasetId, setActiveDatasetId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [datasetVersion, setDatasetVersion] = useState(0);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listDatasets();
      setDatasets(result.datasets);
      setActiveDatasetId(result.active_dataset_id);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Call this after any action that changes which dataset is active
  // (upload succeeded, activate succeeded) -- refetches the dataset
  // list AND bumps datasetVersion so every other page refetches its
  // own dataset-derived data too.
  const notifyDatasetChanged = useCallback(async () => {
    await refresh();
    setDatasetVersion((v) => v + 1);
  }, [refresh]);

  const activeDataset = datasets.find((d) => d.dataset_id === activeDatasetId) ?? null;

  return (
    <DatasetContext.Provider
      value={{ datasets, activeDataset, activeDatasetId, loading, error, datasetVersion, notifyDatasetChanged }}
    >
      {children}
    </DatasetContext.Provider>
  );
}

export function useDataset() {
  const ctx = useContext(DatasetContext);
  if (!ctx) {
    throw new Error("useDataset must be used within a DatasetProvider");
  }
  return ctx;
}
