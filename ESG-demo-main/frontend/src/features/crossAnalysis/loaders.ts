import type { AllRecord, AllRecordsRowRaw } from "@/features/crossAnalysis/types";
import { normalizeAllRecords } from "@/features/crossAnalysis/recordAdapter";

export type NavTree = {
  primaries: string[];
  secondaryByPrimary: Record<string, string[]>;
};

function uniqSort(vals: string[]): string[] {
  return Array.from(new Set(vals.filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

async function tryFetchJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export async function fetchAllRecords(apiBaseUrl: string): Promise<AllRecord[]> {
  const candidates = [
    // Preferred: backend persisted outputs under /uploads/...
    "/uploads/outputs/cross_analysis/output/all_records.json",
    "/uploads/outputs/cross_analysis/output/all_records.json?ts=" + Date.now(),
    "/uploads/outputs/cross_analysis/excel_output/all_records.json",
    "/uploads/outputs/cross_analysis/excel_output/all_records.json?ts=" + Date.now(),
    `${apiBaseUrl}/uploads/outputs/cross_analysis/output/all_records.json`,
    `${apiBaseUrl}/uploads/outputs/cross_analysis/output/all_records.json?ts=` + Date.now(),
    `${apiBaseUrl}/uploads/outputs/cross_analysis/excel_output/all_records.json`,
    `${apiBaseUrl}/uploads/outputs/cross_analysis/excel_output/all_records.json?ts=` + Date.now(),
    "/output/all_records.json",
    "/output/all_records.json?ts=" + Date.now(),
    `${apiBaseUrl}/output/all_records.json`,
    `${apiBaseUrl}/output/all_records.json?ts=` + Date.now(),
    "/all_records.json",
    `${apiBaseUrl}/all_records.json`,
  ];
  for (const u of candidates) {
    const raw = await tryFetchJson<AllRecordsRowRaw[] | { records: AllRecordsRowRaw[] }>(u);
    if (!raw) continue;
    const rows = Array.isArray(raw) ? raw : (raw as any).records;
    if (!Array.isArray(rows)) continue;
    const normalized = normalizeAllRecords(rows as AllRecordsRowRaw[]);
    if (normalized.length) return normalized;
  }
  throw new Error(
    "Cannot load all_records.json. Ensure it is served over HTTP, e.g. /uploads/outputs/cross_analysis/output/all_records.json (same origin, recommended) or /output/all_records.json." 
  );
}

/**
 * Try to load the navigation tree from ESGMetrics (backend catalog). If it is not available,
 * fall back to building from the all_records data.
 */
export async function fetchNavTree(apiBaseUrl: string, fallbackRecords?: AllRecord[]): Promise<NavTree> {
  const navCandidates = [
    `${apiBaseUrl}/api/catalog/esgmetrics`,
    `${apiBaseUrl}/api/catalog/esg_metrics`,
    `${apiBaseUrl}/api/catalog/ESGMetrics`,
    `${apiBaseUrl}/api/esgmetrics`,
    `${apiBaseUrl}/api/esg-metrics`,
  ];

  for (const u of navCandidates) {
    const raw = await tryFetchJson<any>(u);
    if (!raw) continue;
    const rows = Array.isArray(raw) ? raw : raw?.rows || raw?.data || raw?.metrics;
    if (!Array.isArray(rows)) continue;

    const primaries = uniqSort(
      rows.map((r: any) =>
        String(r.primary_navigation ?? r["Primary Navigation"] ?? r["PrimaryNavigation"] ?? "").trim()
      )
    );
    if (!primaries.length) continue;

    const secondaryByPrimary: Record<string, string[]> = {};
    for (const p of primaries) {
      const secs = uniqSort(
        rows
          .filter((r: any) =>
            String(r.primary_navigation ?? r["Primary Navigation"] ?? r["PrimaryNavigation"] ?? "").trim() === p
          )
          .map((r: any) => String(r.secondary_navigation ?? r["Secondary Navigation"] ?? r["SecondaryNavigation"] ?? "").trim())
      );
      secondaryByPrimary[p] = secs;
    }
    return { primaries, secondaryByPrimary };
  }

  // Fallback: derive from all_records
  const recs = fallbackRecords || [];
  const primaries = uniqSort(recs.map((r) => r.primaryNavigation));
  const secondaryByPrimary: Record<string, string[]> = {};
  for (const p of primaries) {
    secondaryByPrimary[p] = uniqSort(recs.filter((r) => r.primaryNavigation === p).map((r) => r.secondaryNavigation));
  }
  return { primaries, secondaryByPrimary };
}

export function slugifyNavKey(label: string): string {
  return String(label)
    .trim()
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
