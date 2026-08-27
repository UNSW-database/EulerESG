"use client";

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  useParams,
  usePathname,
  useRouter,
  useSearchParams,
} from "next/navigation";
import dynamic from "next/dynamic";
import { Button, Modal, Skeleton } from "antd";

import { apiService } from "@/lib/api";
import { warmAppRoute } from "@/lib/routeWarmup";
import type { CrossExtractedRecord, CrossReportSummary } from "@/features/crossAnalysis/types";
import { normalizeCrossRecords } from "@/features/crossAnalysis/recordAdapter";
import {
  NewSidebar,
  type DirectNavigationSelection,
} from "@/components/cross-analysis/NewSidebar";
import { useCrossAnalysisNavigationSlot } from "@/components/cross-analysis/CrossAnalysisNavigationPortal";
import { NewHeader } from "@/components/cross-analysis/NewHeader";
import type { MetricChartSpec } from "@/components/cross-analysis/MetricChartsGrid";
import { useT } from "@/i18n/useT";
import { useFileStore } from "@/store/useFileStore";

const loadMetricChartsGrid = () =>
  import("@/components/cross-analysis/MetricChartsGrid");
const loadNewDataTable = () =>
  import("@/components/cross-analysis/NewDataTable");
const loadDisclosureCompleteness = () =>
  import("@/components/cross-analysis/DisclosureCompletenessComparison");

const MetricChartsGrid = dynamic(
  () => loadMetricChartsGrid().then((mod) => mod.MetricChartsGrid),
  { loading: () => <Skeleton active paragraph={{ rows: 8 }} />, ssr: false },
);
const NewDataTable = dynamic(
  () => loadNewDataTable().then((mod) => mod.NewDataTable),
  { loading: () => <Skeleton active paragraph={{ rows: 10 }} />, ssr: false },
);
const DisclosureCompletenessComparison = dynamic(
  loadDisclosureCompleteness,
  { loading: () => <Skeleton active paragraph={{ rows: 10 }} />, ssr: false },
);
const EMPTY_REPORTS: CrossReportSummary[] = [];
const EMPTY_RECORDS: CrossExtractedRecord[] = [];
const ACTIVITY_METRICS_PRIMARY = "Activity Metrics";
const ACTIVITY_METRICS_SKIP_SECONDARY = new Set([
  "Quantitative",
  "Qualitative",
  "Discussion and Analysis",
  "General",
]);

function isActivityMetricsPrimary(primary: string) {
  return (
    primary === ACTIVITY_METRICS_PRIMARY ||
    safeTrim(primary).toLowerCase() === "activity metricss"
  );
}

function canonicalPrimary(primary: string) {
  return isActivityMetricsPrimary(primary)
    ? ACTIVITY_METRICS_PRIMARY
    : primary;
}

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

function stripFileExt(name: string): string {
  const s = safeTrim(name);
  if (!s) return "";
  // Remove the last extension only (e.g., ".pdf"), keep internal dots.
  return s.replace(/\.[^/.]+$/, "");
}


function isUuid(v: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(v || "").trim());
}

function normalizeReportKey(v: any): string {
  if (!v) return "";
  const s = String(v).trim().toLowerCase();
  const noExt = s.replace(/\.(pdf|json|txt)$/i, "");
  return noExt.replace(/[^a-z0-9]+/g, "");
}


function uniqPreserveOrder(arr: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const v0 of arr) {
    const v = safeTrim(v0);
    if (!v) continue;
    if (seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

function slugify(v: string): string {
  const s = safeTrim(v)
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_\-]/g, "")
    .slice(0, 64);
  return s || "nav";
}

function parseIds(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .filter((x, idx, arr) => arr.indexOf(x) === idx);
}

function updateCrossAnalysisHistory(url: string, mode: "push" | "replace"): void {
  if (typeof window === "undefined") return;
  const current = `${window.location.pathname}${window.location.search}`;
  if (current === url) return;
  if (mode === "push") window.history.pushState(null, "", url);
  else window.history.replaceState(null, "", url);
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// NOTE: Cross Analysis no longer reads cross_analysis/output/all_records.json nor triggers
// any re-extraction. It builds its dataset directly from per-report assessment outputs.

function CrossAnalysisDimensionPageContent() {
  const { t } = useT();
  const router = useRouter();
  const params = useParams();
  const pathname = usePathname() || "/cross-analysis";
  const searchParams = useSearchParams();
  const crossAnalysisSelection = useFileStore(
    (state) => state.crossAnalysisSelection,
  );
  const setCrossAnalysisSelection = useFileStore(
    (state) => state.setCrossAnalysisSelection,
  );

  const dimensionSlug = safeTrim((params as any)?.dimension || "");

  // IMPORTANT:
  // Next.js `useSearchParams()` may return a new object identity across renders.
  // If we depend on that object in useMemo/useCallback, we can accidentally retrigger
  // data loading on every render, which causes chart flicker.
  // Therefore we only depend on the *string values* we actually use.
  const idsParam = searchParams.get("ids") || "";

  // Cache commonly used query params as strings so hooks don't depend on the
  // `useSearchParams()` object identity.
  const searchParamsStr = searchParams.toString();
  const primaryQ = safeTrim(searchParams.get("primary"));
  const secondaryQ = safeTrim(searchParams.get("secondary"));
  const metricQ = safeTrim(searchParams.get("metric") || "");
  const isDisclosureQuery = safeTrim(searchParams.get("view")).toLowerCase() === "disclosure";
  const ids = useMemo(() => parseIds(idsParam), [idsParam]);
  const idsKey = ids.join("|");
  const [viewMode, setViewMode] = useState<"issue" | "disclosure">(
    isDisclosureQuery ? "disclosure" : "issue",
  );

  useEffect(() => {
    setViewMode(isDisclosureQuery ? "disclosure" : "issue");
  }, [isDisclosureQuery]);

  useEffect(() => {
    if (!crossAnalysisSelection || ids.length < 2) return;
    const committedIds = crossAnalysisSelection.reports.map(
      (report) => report.fileId,
    );
    if (!arraysEqual(ids, committedIds)) return;

    // Navigation inside an already confirmed comparison may update the saved
    // dimension/view URL, but it must never replace the committed report set.
    // This avoids an old page effect racing with a newly confirmed selection.
    const href = `${pathname}${searchParamsStr ? `?${searchParamsStr}` : ""}`;
    if (href === crossAnalysisSelection.href) return;
    setCrossAnalysisSelection({
      href,
      reports: crossAnalysisSelection.reports,
    });
  }, [
    crossAnalysisSelection,
    ids,
    pathname,
    searchParamsStr,
    setCrossAnalysisSelection,
  ]);

  useEffect(() => {
    if (idsParam || !crossAnalysisSelection) return;
    const committedIds = crossAnalysisSelection.reports.map(
      (report) => report.fileId,
    );
    if (parseIds(committedIds.join(",")).length < 2) return;
    try {
      const savedUrl = new URL(
        crossAnalysisSelection.href,
        "http://localhost",
      );
      if (!savedUrl.pathname.startsWith("/cross-analysis")) return;
      if (
        !arraysEqual(
          parseIds(savedUrl.searchParams.get("ids")),
          committedIds,
        )
      ) {
        return;
      }
      router.replace(`${savedUrl.pathname}${savedUrl.search}`);
    } catch {
      // Ignore malformed persisted state and keep the neutral selector view.
    }
  }, [crossAnalysisSelection, idsParam, router]);

  // Start downloading the active view's heavy UI at the same time as its data.
  // Without this, the chart/table chunks only begin after the API bootstrap has
  // finished, creating an avoidable second loading waterfall.
  useEffect(() => {
    if (viewMode === "disclosure") {
      void loadDisclosureCompleteness().catch(() => undefined);
      return;
    }
    void Promise.all([loadMetricChartsGrid(), loadNewDataTable()]).catch(
      () => undefined,
    );
  }, [viewMode]);

  const [reportsRequest, setReportsRequest] = useState<{
    key: string;
    loading: boolean;
    data: CrossReportSummary[];
    error: string | null;
  }>({ key: "", loading: false, data: [], error: null });
  const reports =
    reportsRequest.key === idsKey ? reportsRequest.data : EMPTY_REPORTS;
  const reportsLoading =
    ids.length >= 2 && (reportsRequest.key !== idsKey || reportsRequest.loading);
  const reportsError = reportsRequest.key === idsKey ? reportsRequest.error : null;
  const navigationSlot = useCrossAnalysisNavigationSlot();

  const currentFramework = useMemo(() => {
    if (!reports.length) return "";
    const frameworks = (reports as any[]).map((r) => safeTrim(r?.framework ?? "")).filter(Boolean);
    if (frameworks.length !== reports.length) return "";
    return frameworks.every((x) => x === frameworks[0]) ? frameworks[0] : "";
  }, [reports]);

  const isSasbFramework = currentFramework === "SASB";

  const [recordsRequest, setRecordsRequest] = useState<{
    key: string;
    loading: boolean;
    data: CrossExtractedRecord[];
    error: string | null;
  }>({ key: "", loading: false, data: [], error: null });
  const recordsRequestRef = useRef(recordsRequest);
  useEffect(() => {
    recordsRequestRef.current = recordsRequest;
  }, [recordsRequest]);
  const allRecords =
    recordsRequest.key === idsKey ? recordsRequest.data : EMPTY_RECORDS;
  const recordsLoading =
    viewMode === "issue" && ids.length >= 2 && (recordsRequest.key !== idsKey || recordsRequest.loading);
  const recordsError = recordsRequest.key === idsKey ? recordsRequest.error : null;
  const bootstrapLoading = reportsLoading || recordsLoading;
  const loadError =
    reportsError ||
    (recordsError === "empty" ? null : recordsError);

  // Data-driven navigation: Primary Navigation -> Secondary Navigation (canonical: "Activity Metricss" -> "Activity Metrics")
  const primaryOptions = useMemo(() => {
    return uniqPreserveOrder(
      (allRecords || []).map((r) => canonicalPrimary(safeTrim((r as any).primary_navigation))).filter(Boolean)
    );
  }, [allRecords]);

  const secondaryByPrimary = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of allRecords || []) {
      const pRaw = safeTrim((r as any).primary_navigation);
      if (!pRaw) continue;
      const p = canonicalPrimary(pRaw);
      if (isActivityMetricsPrimary(pRaw)) {
        const metricName = safeTrim((r as any).topic) || safeTrim((r as any).secondary_navigation);
        if (!metricName || ACTIVITY_METRICS_SKIP_SECONDARY.has(metricName)) continue;
        const a = m.get(p) || [];
        if (!a.includes(metricName)) a.push(metricName);
        m.set(p, a);
      } else {
        const s = isSasbFramework
          ? safeTrim((r as any).topic) || safeTrim((r as any).secondary_navigation)
          : safeTrim((r as any).secondary_navigation);
        if (!s) continue;
        const a = m.get(p) || [];
        if (!a.includes(s)) a.push(s);
        m.set(p, a);
      }
    }
    m.forEach((arr) => {
      arr.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    });
    return m;
  }, [allRecords, isSasbFramework]);

  // Tertiary = metric_name: Level 3 under each (primary, secondary). For "Activity Metrics" there is no tertiary (secondary = metric_name).
  const tertiaryByPrimaryAndSecondary = useMemo(() => {
    const outer = new Map<string, Map<string, string[]>>();
    if (isSasbFramework) return outer;
    for (const r of allRecords || []) {
      const pRaw = safeTrim((r as any).primary_navigation);
      if (!pRaw) continue;
      const p = canonicalPrimary(pRaw);
      if (isActivityMetricsPrimary(pRaw)) continue;
      const s = safeTrim((r as any).secondary_navigation);
      const metricName = safeTrim((r as any).topic);
      if (!p || !s || !metricName) continue;
      if (!outer.has(p)) outer.set(p, new Map());
      const inner = outer.get(p)!;
      if (!inner.has(s)) inner.set(s, []);
      const arr = inner.get(s)!;
      if (!arr.includes(metricName)) arr.push(metricName);
    }
    outer.forEach((inner) => {
      inner.forEach((arr) => arr.sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
    });
    return outer;
  }, [allRecords, isSasbFramework]);

  const [selectedPrimary, setSelectedPrimary] = useState<string>("");
  const [selectedSecondaries, setSelectedSecondaries] = useState<string[]>([]);
  const [selectedTertiary, setSelectedTertiary] = useState<string | null>(null);
  const [expandedPrimaries, setExpandedPrimaries] = useState<Record<string, boolean>>({});
  const [directNavigationSelection, setDirectNavigationSelection] =
    useState<DirectNavigationSelection | null>(null);

  useEffect(() => {
    setDirectNavigationSelection(null);
  }, [idsKey]);

  // Resolve selectedPrimary / Secondaries / Tertiary (metric) from URL after data arrives.
  useEffect(() => {
    if (!primaryOptions.length) return;

    const primaryFromQuery = primaryQ;
    let desiredPrimary = "";
    if (primaryFromQuery && primaryOptions.includes(primaryFromQuery)) {
      desiredPrimary = primaryFromQuery;
    } else if (dimensionSlug) {
      desiredPrimary = primaryOptions.find((p) => slugify(p) === dimensionSlug) || "";
    }
    if (!desiredPrimary) desiredPrimary = primaryOptions[0];

    const secondaryOptions = secondaryByPrimary.get(desiredPrimary) || [];
    const secondaryRaw = secondaryQ;
    let desiredSecondaries = secondaryRaw
      ? secondaryRaw
          .split(",")
          .map((x) => safeTrim(x))
          .filter(Boolean)
      : [];
    desiredSecondaries = desiredSecondaries.filter((s) => secondaryOptions.includes(s));

    const inner = tertiaryByPrimaryAndSecondary.get(desiredPrimary);
    const tertiaryOptions =
      desiredPrimary === ACTIVITY_METRICS_PRIMARY
        ? (secondaryByPrimary.get(desiredPrimary) || [])
        : isSasbFramework
          ? []
          : (inner && desiredSecondaries.length ? inner.get(desiredSecondaries[0]) : undefined) || [];
    const desiredTertiary = !isSasbFramework && metricQ && tertiaryOptions.includes(metricQ) ? metricQ : null;

    setExpandedPrimaries((prev) => (prev?.[desiredPrimary] ? prev : { ...prev, [desiredPrimary]: true }));

    setSelectedPrimary((prev) => (prev === desiredPrimary ? prev : desiredPrimary));
    setSelectedSecondaries((prev) => (arraysEqual(prev, desiredSecondaries) ? prev : desiredSecondaries));
    setSelectedTertiary((prev) => (prev === desiredTertiary ? prev : desiredTertiary));
  }, [primaryOptions, secondaryByPrimary, tertiaryByPrimaryAndSecondary, dimensionSlug, primaryQ, secondaryQ, metricQ, isSasbFramework]);

  const records = useMemo(() => {
    const p = selectedPrimary;
    const secondarySet = selectedSecondaries.length ? new Set(selectedSecondaries) : null;
    const metricName = selectedTertiary;
    const isActivityMetrics = p === ACTIVITY_METRICS_PRIMARY;
    return (allRecords || []).filter((r) => {
      if (p && canonicalPrimary(safeTrim((r as any).primary_navigation)) !== p) return false;
      if (isActivityMetrics) {
        if (metricName && safeTrim((r as any).topic) !== metricName) return false;
      } else {
        const secondaryValue = isSasbFramework
          ? safeTrim((r as any).topic) || safeTrim((r as any).secondary_navigation)
          : safeTrim((r as any).secondary_navigation);
        if (secondarySet && !secondarySet.has(secondaryValue)) return false;
        if (!isSasbFramework && metricName && safeTrim((r as any).topic) !== metricName) return false;
      }
      return true;
    });
  }, [allRecords, selectedPrimary, selectedSecondaries, selectedTertiary, isSasbFramework]);

  // Load report metadata immediately. The request is single-flight cached by
  // apiService, so StrictMode and click-time prefetch share the same promise.
  useEffect(() => {
    if (ids.length < 2) {
      setReportsRequest({ key: idsKey, loading: false, data: [], error: null });
      return;
    }
    let cancelled = false;
    setReportsRequest({ key: idsKey, loading: true, data: [], error: null });
    void apiService.getCrossAnalysisReports(ids).then(
      (resp) => {
        if (cancelled) return;
        setReportsRequest({
          key: idsKey,
          loading: false,
          data: (resp.reports || []) as CrossReportSummary[],
          error: null,
        });
      },
      (error: unknown) => {
        if (cancelled) return;
        setReportsRequest({
          key: idsKey,
          loading: false,
          data: [],
          error: error instanceof Error ? error.message : String(error),
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ids, idsKey]);

  // Only allow comparison when all selected reports share the same framework and scope:
  // - SASB/TCFD: same industry and sub-industry (semi_industry).
  // - GRI: same Sector and Topic (gri_sector, gri_topic); GRI has no sub-industry.
  const canCompare = useMemo(() => {
    if (!reports || reports.length < 2) return false;
    const arr = reports as any[];
    const fw = arr.map((r) => safeTrim(r?.framework ?? ""));
    if (fw.some((x) => !x)) return false;
    if (!fw.every((x) => x === fw[0])) return false;
    const isGRI = fw[0] === "GRI";
    if (isGRI) {
      const sectors = arr.map((r) => safeTrim(r?.gri_sector ?? ""));
      const topics = arr.map((r) => safeTrim(r?.gri_topic ?? ""));
      if (sectors.some((x) => !x) || topics.some((x) => !x)) return false;
      return sectors.every((x) => x === sectors[0]) && topics.every((x) => x === topics[0]);
    }
    const ind = arr.map((r) => safeTrim(r?.industry ?? ""));
    const semi = arr.map((r) => safeTrim(r?.semi_industry ?? ""));
    if (ind.some((x) => !x) || semi.some((x) => !x)) return false;
    return ind.every((x) => x === ind[0]) && semi.every((x) => x === semi[0]);
  }, [reports]);

  const scopeMismatch = ids.length >= 2 && reports.length >= 2 && !canCompare;

  // Prefer using the uploaded report's filename as the display label across Cross Analysis.
  const fileIdToReportLabel = useMemo(() => {
    const m = new Map<string, string>();
    (reports || []).forEach((r) => {
      const fid = safeTrim((r as any)?.file_id);
      if (!fid) return;
      const fileNameRaw = safeTrim((r as any)?.filename);
      const label =
        (fileNameRaw ? stripFileExt(fileNameRaw) : "") ||
        safeTrim((r as any)?.display_name) ||
        safeTrim((r as any)?.short_name) ||
        fid;
      m.set(fid, label);
    });
    return m;
  }, [reports]);

  // Map: (filename stem / display name / short name) -> true uuid file_id
  const reportKeyToFileId = useMemo(() => {
    const m = new Map<string, string>();
    (reports || []).forEach((r) => {
      const fid = safeTrim((r as any)?.file_id);
      if (!fid) return;
      const fname = stripFileExt(safeTrim((r as any)?.filename));
      const dname = stripFileExt(safeTrim((r as any)?.display_name));
      const sname = stripFileExt(safeTrim((r as any)?.short_name));
      [fid, fname, dname, sname].forEach((k) => {
        const nk = normalizeReportKey(k);
        if (nk) m.set(nk, fid);
      });
    });
    return m;
  }, [reports]);


  // Start the heavier records request at the same time as report metadata.
  // Entry points already validate compatibility; direct links are checked as
  // soon as the parallel metadata request resolves.
  useEffect(() => {
    if (viewMode === "disclosure") {
      return;
    }
    if (ids.length < 2) {
      setRecordsRequest({ key: idsKey, loading: false, data: [], error: null });
      return;
    }
    const previous = recordsRequestRef.current;
    if (
      previous.key === idsKey &&
      !previous.loading &&
      (previous.data.length > 0 || previous.error === "empty")
    ) {
      return;
    }
    let cancelled = false;
    setRecordsRequest({ key: idsKey, loading: true, data: [], error: null });
    void apiService.getCrossAnalysisDisclosedCache(ids).then(
      (resp) => {
        if (cancelled) return;
        const recordsFlat: unknown[] = Array.isArray(resp?.records) ? resp.records : [];
        const normalized = normalizeCrossRecords(recordsFlat as any[]);
        setRecordsRequest({
          key: idsKey,
          loading: false,
          data: normalized,
          error: normalized.length ? null : "empty",
        });
      },
      (error: unknown) => {
        if (cancelled) return;
        setRecordsRequest({
          key: idsKey,
          loading: false,
          data: [],
          error: error instanceof Error ? error.message : String(error),
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ids, idsKey, viewMode]);

const buildNavUrl = useCallback(
  (primary: string, secondaries: string[], metric?: string | null) => {
    const next = new URLSearchParams(searchParamsStr);
    // Preserve framework/industry/semiIndustry (and ids) when switching navigation
    if (primary) next.set("primary", primary);
    else next.delete("primary");
    if (secondaries && secondaries.length) next.set("secondary", secondaries.join(","));
    else next.delete("secondary");
    if (metric) next.set("metric", metric);
    else next.delete("metric");
    next.delete("view");

    const qs = next.toString();
    return qs ? `/cross-analysis?${qs}` : "/cross-analysis";
  },
  [searchParamsStr]
);

const handleTogglePrimary = useCallback(
  (primary: string) => {
    setViewMode("issue");
    setDirectNavigationSelection({ level: "primary", primary });
    setExpandedPrimaries((prev) => ({ ...prev, [primary]: safeTrim(selectedPrimary) === primary ? !prev?.[primary] : true }));

    if (safeTrim(selectedPrimary) !== primary) {
      const nextSecondaries: string[] = [];
      setSelectedPrimary(primary);
      setSelectedSecondaries(nextSecondaries);
      setSelectedTertiary(null);
      updateCrossAnalysisHistory(buildNavUrl(primary, nextSecondaries, null), "replace");
    }
  },
  [buildNavUrl, selectedPrimary]
);

const handleSelectSecondary = useCallback(
  (primary: string, secondary: string) => {
    setViewMode("issue");
    setExpandedPrimaries((prev) => ({ ...prev, [primary]: true }));

    const currentOrder = secondaryByPrimary.get(primary) || [];
    const existing = safeTrim(selectedPrimary) === primary ? [...selectedSecondaries] : [];
    const hasSecondary = existing.includes(secondary);
    const nextSecondaries = hasSecondary ? existing.filter((x) => x !== secondary) : [...existing, secondary];
    const order = new Map(currentOrder.map((v, idx) => [v, idx] as const));
    nextSecondaries.sort((a, b) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9));

    setSelectedPrimary(primary);
    setSelectedSecondaries(nextSecondaries);
    setSelectedTertiary(null);
    setDirectNavigationSelection(
      hasSecondary ? null : { level: "secondary", primary, secondary },
    );
    updateCrossAnalysisHistory(buildNavUrl(primary, nextSecondaries, null), "replace");
  },
  [buildNavUrl, selectedPrimary, selectedSecondaries, secondaryByPrimary]
);

const handleSelectTertiary = useCallback(
  (primary: string, secondary: string, metricName: string) => {
    setViewMode("issue");
    setExpandedPrimaries((prev) => ({ ...prev, [primary]: true }));

    const isSameMetric =
      safeTrim(selectedPrimary) === primary &&
      selectedSecondaries.length === 1 &&
      selectedSecondaries[0] === secondary &&
      selectedTertiary === metricName;

    const nextSecondaries = isSameMetric ? [] : [secondary];
    const nextMetric = isSameMetric ? null : metricName;

    setSelectedPrimary(primary);
    setSelectedSecondaries(nextSecondaries);
    setSelectedTertiary(nextMetric);
    setDirectNavigationSelection(
      isSameMetric
        ? null
        : primary === ACTIVITY_METRICS_PRIMARY
          ? { level: "secondary", primary, secondary }
          : { level: "tertiary", primary, secondary, metricName },
    );
    updateCrossAnalysisHistory(buildNavUrl(primary, nextSecondaries, nextMetric), "replace");
  },
  [buildNavUrl, selectedPrimary, selectedSecondaries, selectedTertiary]
);

  // 生成新样式需要的表格数据
  const newTableData = useMemo(() => {
    // 创建报告名称到 file_id 的映射
    const nameToFileIdMap = new Map<string, string>();
    reports.forEach((r) => {
      const fid = safeTrim((r as any).file_id);
      if (!fid) return;
      const fn = safeTrim((r as any).filename);
      const keys = [
        fn,
        fn ? stripFileExt(fn) : "",
        safeTrim((r as any).short_name),
        safeTrim((r as any).display_name),
      ].filter(Boolean);
      keys.forEach((k) => nameToFileIdMap.set(k, fid));
    });

    return records.map((record, index) => {
      const dataStr = safeTrim((record as any).data);
      const numericValue = parseFloat(dataStr?.replace(/,/g, "") || "0");
      const isNotDisclosed = !dataStr || (!Number.isFinite(numericValue) && (record as any).disclosure_status !== "fully_disclosed");
      const formattedValue = isNotDisclosed
        ? ""
        : isNaN(numericValue)
          ? dataStr || t("common.na")
          : numericValue.toLocaleString();
      // Extract file_id + page. Rows may come from old JSON where `id` is a company alias (e.g., Google2025).
      // Prefer true uuid file_id; otherwise resolve alias/name to uuid using report meta.
      const recordName = safeTrim((record as any).name) || "";
      const rawFileId = safeTrim((record as any).id) || safeTrim((record as any).file_id) || nameToFileIdMap.get(recordName) || "";
      const recordId = rawFileId
        ? (isUuid(rawFileId)
            ? rawFileId
            : reportKeyToFileId.get(normalizeReportKey(rawFileId)) || reportKeyToFileId.get(normalizeReportKey(recordName)) || rawFileId)
        : "";
      const recordPage = (record as any).page;
      const pageNumber = recordPage !== null && recordPage !== undefined 
        ? (typeof recordPage === 'number' ? recordPage : parseInt(String(recordPage))) 
        : null;

      const reportLabel = recordId
        ? fileIdToReportLabel.get(String(recordId)) || recordName || t("common.unknown")
        : recordName || t("common.unknown");

      return {
        id: index + 1,
        report: reportLabel,
        metric: safeTrim((record as any).topic) || t("common.na"),
        detail: [safeTrim((record as any).sub_topic), safeTrim((record as any).detail)]
          .filter(Boolean)
          .join(" — "),
        year: parseInt(safeTrim((record as any).year) || "0") || new Date().getFullYear(),
        value: formattedValue,
        isNotDisclosed,
        unit: safeTrim((record as any).unit) || "",
        fileId: recordId,
        page: pageNumber,
      };
    });
  }, [records, reports, fileIdToReportLabel, reportKeyToFileId, t]);

  // Report display names (may duplicate when same filename for multiple reports)
  const reportNames = useMemo(() => {
    if (reports.length > 0) {
      return reports
        .map((r) => {
          const fn = safeTrim((r as any)?.filename);
          return (fn ? stripFileExt(fn) : "") || safeTrim((r as any)?.short_name) || safeTrim((r as any)?.display_name) || safeTrim((r as any)?.file_id);
        })
        .filter(Boolean);
    }
    if (ids.length > 0) return ids;
    const uniqueNames = new Set<string>();
    allRecords.forEach((record) => {
      const name = safeTrim((record as any).name);
      if (name) uniqueNames.add(name);
    });
    return Array.from(uniqueNames);
  }, [reports, allRecords, ids]);

  // One slot per report with a unique chart label so the bar chart always shows one bar per report.
  // When two reports share the same name (e.g. "bmw esg 2024"), use "bmw esg 2024", "bmw esg 2024 (2)".
  const reportChartSlots = useMemo(() => {
    if (reports.length > 0) {
      const nameCount = new Map<string, number>();
      return reports.map((r) => {
        const fileId = safeTrim((r as any)?.file_id) || "";
        const fn = safeTrim((r as any)?.filename);
        const base = (fn ? stripFileExt(fn) : "") || safeTrim((r as any)?.short_name) || safeTrim((r as any)?.display_name) || fileId;
        const count = (nameCount.get(base) ?? 0) + 1;
        nameCount.set(base, count);
        const label = count === 1 ? base : `${base} (${count})`;
        return { fileId, label };
      });
    }
    const nameCount = new Map<string, number>();
    return (ids.length ? ids : reportNames.map((_, i) => String(i))).map((fileId, i) => {
      const base = reportNames[i] || fileId || String(i);
      const count = (nameCount.get(base) ?? 0) + 1;
      nameCount.set(base, count);
      const label = count === 1 ? base : `${base} (${count})`;
      return { fileId, label };
    });
  }, [reports, reportNames, ids]);

  // Reuse framework & sub-industry when all selected reports share the same (no re-prompt).
  const reportsFrameworkLabel = useMemo(() => {
    if (!reports.length) return null;
    const fw = (reports as any[]).map((r) => safeTrim(r.framework)).filter(Boolean);
    if (fw.length !== reports.length) return null;
    const first = fw[0];
    return fw.every((x) => x === first) ? first : null;
  }, [reports]);

  const reportsSemiIndustryLabel = useMemo(() => {
    if (!reports.length) return null;
    const semi = (reports as any[]).map((r) => safeTrim(r.semi_industry)).filter(Boolean);
    if (semi.length !== reports.length) return null;
    const first = semi[0];
    return semi.every((x) => x === first) ? first : null;
  }, [reports]);

  // Assign a stable color per report slot (one bar per report; used by all charts)
  const companyColors = useMemo(() => {
    const palette = [
      "#1677ff",
      "#52c41a",
      "#faad14",
      "#f5222d",
      "#722ed1",
      "#13c2c2",
      "#eb2f96",
      "#a0d911",
    ];
    const map: Record<string, string> = {};
    reportChartSlots.forEach((slot, idx) => {
      map[slot.fileId || slot.label] = palette[idx % palette.length];
    });
    return map;
  }, [reportChartSlots]);

  const companyLegend = useMemo(() => {
    return reportChartSlots.map((slot) => ({
      label: slot.label,
      color: companyColors[slot.fileId || slot.label] || "#1677ff",
    }));
  }, [reportChartSlots, companyColors]);

  // Build charts: one point per report (by file_id) so we always get one bar per report; value null = Not Disclosed.
  const metricCharts = useMemo<MetricChartSpec[]>(() => {
    if (!records.length && !reportChartSlots.length) return [];

    const yearNum = (y: string | null | undefined) => {
      const n = Number(safeTrim(y));
      return Number.isFinite(n) ? n : -Infinity;
    };

    const byKey = new Map<
      string,
      {
        topic: string;
        unit: string | null;
        category: string | null;
        perFileId: Map<string, { value: number | null; year: string | null }>;
      }
    >();

    records.forEach((record) => {
      const topic = safeTrim((record as any).topic) || t("crossAnalysis.table.metric");
      const unit = safeTrim((record as any).unit) || null;
      const category = safeTrim((record as any).category) || null;

      const fid = safeTrim((record as any).id || (record as any).file_id);

      const raw = safeTrim((record as any).data).replace(/,/g, "");
      const match = raw.match(/-?\d+(?:\.\d+)?/);
      const value = match && Number.isFinite(Number(match[0])) ? Number(match[0]) : null;
      const year = safeTrim((record as any).year) || null;

      const key = `${topic}||${unit || ""}`;
      if (!byKey.has(key)) {
        byKey.set(key, { topic, unit, category, perFileId: new Map() });
      }
      const bucket = byKey.get(key)!;
      if (category === "Quantitative") bucket.category = "Quantitative";
      const prev = bucket.perFileId.get(fid);
      if (!prev || (value != null && (prev.value == null || yearNum(year) > yearNum(prev.year)))) {
        bucket.perFileId.set(fid, { value, year });
      }
    });

    const charts: MetricChartSpec[] = [];
    byKey.forEach((bucket, key) => {
      const cat = bucket.category ? safeTrim(bucket.category) : "";
      if (cat && cat !== "Quantitative") return;

      const perFileId = bucket.perFileId;
      // One point per report slot so the chart always shows one bar per report (disclosed or Not Disclosed).
      const points: { company: string; colorKey: string; value: number | null; year: string | null }[] = reportChartSlots
        .map((slot) => {
          const v = perFileId.get(slot.fileId);
          return {
            company: slot.label,
            colorKey: slot.fileId || slot.label,
            value: v ? v.value : null,
            year: v ? v.year : null,
          };
        })
        .filter((p) => p.value !== null && p.value !== undefined && Number.isFinite(Number(p.value)));

      if (!points.length) return;

      const years = Array.from(new Set(points.map((p) => safeTrim(p.year)))).filter(Boolean) as string[];
      const yearInfo = years.length === 1 ? t("crossAnalysis.yearSingle", { year: years[0] }) : years.length > 1 ? t("crossAnalysis.yearsVary") : undefined;

      charts.push({
        key,
        topic: bucket.topic,
        unit: bucket.unit,
        yearInfo,
        points,
      });
    });

    charts.sort((a, b) => {
      const aWithData = a.points.filter((p) => p.value != null).length;
      const bWithData = b.points.filter((p) => p.value != null).length;
      return bWithData - aWithData || a.topic.localeCompare(b.topic);
    });
    return charts;
  }, [records, reportChartSlots, t]);

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#F8FAFC] to-[#FFFFFF] p-6 w-full">
      {ids.length < 2 ? (
        <div className="w-full flex flex-col items-center justify-center min-h-[50vh] text-slate-600">
          <p className="text-base mb-2">{t("crossAnalysis.title")}</p>
          <p className="text-sm">{t("files.selectAtLeastTwoReports")}</p>
        </div>
      ) : scopeMismatch ? (
        <>
          <Modal
            open={true}
            title={t("crossAnalysis.title")}
            closable={false}
            mask={{ closable: false }}
            footer={
              <Button
                type="primary"
                onPointerDown={() => warmAppRoute(router, "/dashboard")}
                onFocus={() => warmAppRoute(router, "/dashboard")}
                onMouseEnter={() => warmAppRoute(router, "/dashboard")}
                onClick={() => router.push("/dashboard")}
              >
                {t("common.back")}
              </Button>
            }
          >
            <p className="text-slate-700">{t("crossAnalysis.sameScopeRequired")}</p>
          </Modal>
        </>
      ) : (
      <div className="w-full relative">
        {viewMode === "issue" && navigationSlot
          ? createPortal(
              bootstrapLoading ? (
                <div className="px-2 py-2" aria-label={t("crossAnalysis.navigation")}>
                  <Skeleton active title={false} paragraph={{ rows: 4 }} />
                </div>
              ) : (
                <NewSidebar
                  embedded
                  primaryOptions={primaryOptions}
                  secondaryByPrimary={secondaryByPrimary}
                  tertiaryByPrimaryAndSecondary={tertiaryByPrimaryAndSecondary}
                  selectedPrimary={selectedPrimary}
                  directSelection={directNavigationSelection}
                  selectedSecondaries={selectedSecondaries}
                  selectedTertiary={selectedTertiary}
                  expandedPrimaries={expandedPrimaries}
                  primaryIsActivityMetrics={selectedPrimary === ACTIVITY_METRICS_PRIMARY}
                  forceSecondaryLeafMode={isSasbFramework && selectedPrimary !== ACTIVITY_METRICS_PRIMARY}
                  onTogglePrimary={handleTogglePrimary}
                  onSelectSecondary={handleSelectSecondary}
                  onSelectTertiary={handleSelectTertiary}
                />
              ),
              navigationSlot,
            )
          : null}

        <div className="flex-1 min-w-0 space-y-4">
          <NewHeader
            title={viewMode === "disclosure" ? t("crossAnalysis.disclosureCompleteness") : t("crossAnalysis.title")}
            dimension=""
            reports={reportNames}
            frameworkLabel={reportsFrameworkLabel}
            semiIndustryLabel={reportsSemiIndustryLabel}
            companyLegend={companyLegend}
          />

          {viewMode === "disclosure" ? (
              <DisclosureCompletenessComparison
                fileIds={ids}
                reports={reports}
              />
          ) : (
            <>
              {/* Comparison Chart Card */}
              {bootstrapLoading ? (
                <Skeleton active paragraph={{ rows: 8 }} />
              ) : (
                <MetricChartsGrid charts={metricCharts} companyColors={companyColors} />
              )}

              {/* Data Table Card */}
              {bootstrapLoading ? (
                <Skeleton active paragraph={{ rows: 10 }} />
              ) : loadError ? (
                <div className="bg-white rounded-2xl shadow-sm p-6 text-center text-red-500">
                  {loadError}
                </div>
              ) : newTableData.length > 0 ? (
                <NewDataTable
                  data={newTableData}
                  onViewEvidence={(row) => {
                    // 跳转到证据页面
                    if (row.fileId) {
                      const params = new URLSearchParams({
                        file_id: row.fileId,
                        name: row.report || t("crossAnalysis.evidence.defaultName"),
                      });
                      if (row.page !== null && row.page !== undefined) {
                        params.set("page", String(row.page));
                      }
                      // Open in a new tab (do not replace the current Cross Analysis view)
                      window.open(`/cross-analysis/evidence?${params.toString()}`, "_blank", "noopener,noreferrer");
                    } else {
                      console.warn("No file_id found for row:", row);
                    }
                  }}
                />
              ) : (
                <div className="bg-white rounded-2xl shadow-sm p-6 text-center text-[#64748B]">
                  {t("crossAnalysis.noRecordsFound")}
                </div>
              )}
            </>
          )}
        </div>
      </div>
      )}
    </div>
  );
}

export default function CrossAnalysisDimensionPage() {
  return (
    <Suspense
      fallback={<div aria-busy="true" className="min-h-screen w-full bg-[#F8FAFC]" />}
    >
      <CrossAnalysisDimensionPageContent />
    </Suspense>
  );
}
