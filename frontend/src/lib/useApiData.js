import { useEffect, useState, useCallback } from "react";

/**
 * Wraps an async API-client call with loading/error/data state, and
 * surfaces which data source answered (`"live"` vs `"mock"`) so the UI
 * can show a demo-data indicator without any component needing to know
 * about mocks directly -- that knowledge lives only in api/client.js.
 *
 * @param {() => Promise<{data: any, source?: "live"|"mock"}>} fetcher
 * @param {any[]} deps
 */
export function useApiData(fetcher, deps = []) {
  const [state, setState] = useState({ data: null, source: null, loading: true, error: null });

  const reload = useCallback(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcher()
      .then((result) => {
        if (cancelled) return;
        setState({ data: result.data, source: result.source ?? "live", loading: false, error: null });
      })
      .catch((err) => {
        if (cancelled) return;
        setState({ data: null, source: null, loading: false, error: err });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => reload(), [reload]);

  return { ...state, reload };
}
