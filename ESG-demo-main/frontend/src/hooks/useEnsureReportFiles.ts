"use client";

import { useEffect } from "react";
import { useFileStore } from "@/store/useFileStore";

const REPORT_LIST_FRESHNESS_MS = 15_000;

/**
 * Reuse an already loaded report directory during client-side navigation.
 * Empty/expired data is refreshed, while a populated directory remains usable
 * and is refreshed in the background without replacing it with a loading mask.
 */
export function useEnsureReportFiles(
  freshnessMs: number = REPORT_LIST_FRESHNESS_MS,
) {
  const fileCount = useFileStore((state) => state.files.length);
  const lastRefresh = useFileStore((state) => state.lastRefresh);
  const loadFilesFromBackend = useFileStore(
    (state) => state.loadFilesFromBackend,
  );

  useEffect(() => {
    const hasFreshSnapshot =
      lastRefresh > 0 && Date.now() - lastRefresh <= freshnessMs;
    if (hasFreshSnapshot) return;

    void loadFilesFromBackend({
      showLoading: fileCount === 0 && lastRefresh === 0,
    });
  }, [fileCount, freshnessMs, lastRefresh, loadFilesFromBackend]);
}
