"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Popover, Select, Spin, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/navigation";
import { useT } from "@/i18n/useT";

import { apiService } from "@/lib/api";
import { normalizeDisclosureStatus, type DisclosureStatus } from "@/lib/complianceSummary";
import type { CrossReportSummary } from "@/features/crossAnalysis/types";
import {
  buildComplianceAnalysisHref,
  useFileStore,
} from "@/store/useFileStore";

export type AnalysisDataItem = {
  metric_id: string;
  metric_name: string;
  disclosure_status: DisclosureStatus;
  reasoning: string;
  unit?: string;
  category?: string;
  topic?: string;
  type?: string;
  value?: string | number | null;
  page?: string | number | null;
  context?: string | null;
};

type PerReport = {
  fileId: string;
  label: string;
  framework?: string | null;
  loading: boolean;
  error: string | null;
  metrics: AnalysisDataItem[];
};

type SortMode = "default" | "report_asc" | "report_desc" | "disclosed_desc" | "not_disclosed_desc";

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

function stripFileExt(name: string): string {
  const s = safeTrim(name);
  if (!s) return "";
  return s.replace(/\.[^/.]+$/, "");
}

function normalizeStatus(raw: any): DisclosureStatus {
  return normalizeDisclosureStatus(raw);
}

function pick(...vals: any[]) {
  for (const v of vals) {
    if (v === null || v === undefined) continue;
    if (typeof v === "string" && v.trim() === "") continue;
    return v;
  }
  return null;
}

const EMPTY_VALUE_TOKENS = new Set(["", "-", "—", "n/a", "na", "null", "none", "not specified", "not available"]);

function isEmptyValue(value: unknown) {
  if (value === null || value === undefined) return true;
  if (typeof value === "number") return !Number.isFinite(value);
  const s = String(value).trim().toLowerCase();
  return EMPTY_VALUE_TOKENS.has(s);
}

function normalizeCategoryLabel(raw: unknown): string {
  const s = String(raw ?? "").trim();
  if (!s) return "";
  const lower = s.toLowerCase();
  if (lower === "quantitative") return "Quantitative";
  if (lower === "qualitative") return "Discussion and Analysis";
  if (lower === "discussion and analysis" || lower === "discussion") return "Discussion and Analysis";
  return s;
}

function formatDisplayValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    return Number.isFinite(value) ? toDisplayValue(value) : null;
  }
  const s = String(value).trim();
  return isEmptyValue(s) ? null : s;
}

function extractContextText(raw: any): string {
  if (raw === null || raw === undefined) return "";
  if (typeof raw === "string") return raw.trim();

  const pickTextFromObj = (o: any): string => {
    if (!o || typeof o !== "object") return "";
    const cand =
      o.context ??
      o.Context ??
      o.specific_data_found ??
      o.specificDataFound ??
      o.text ??
      o.Text ??
      o.excerpt ??
      o.Excerpt ??
      o.evidence_text ??
      o.evidenceText ??
      o.evidence ??
      o.snippet ??
      o.Snippet;

    if (typeof cand === "string") return cand.trim();
    if (cand && typeof cand === "object") {
      const nested = pickTextFromObj(cand);
      if (nested) return nested;
    }

    try {
      const shallow = {
        context: o.context ?? o.Context,
        text: o.text ?? o.Text,
        excerpt: o.excerpt ?? o.Excerpt,
        page: o.page ?? o.page_number ?? o.pageNumber,
      };
      const s = JSON.stringify(shallow);
      return s === "{}" ? "" : s;
    } catch {
      return "";
    }
  };

  if (Array.isArray(raw)) {
    const parts = raw
      .map((seg) => {
        if (typeof seg === "string") return seg.trim();
        return pickTextFromObj(seg);
      })
      .filter((s) => !!s);
    return parts.join("\n\n");
  }

  if (typeof raw === "object") {
    return pickTextFromObj(raw);
  }

  return String(raw).trim();
}

function normalizePage(page: string | number | null | undefined): number | null {
  if (page === null || page === undefined) return null;
  if (typeof page === "number") return Number.isFinite(page) ? page : null;
  const s = String(page).trim();
  if (!s) return null;
  const firstToken = s.split(",")[0].trim();
  const rangeFirst = firstToken.split("-")[0].trim();
  const m = rangeFirst.match(/\d+/);
  if (!m) return null;
  const n = parseInt(m[0], 10);
  return Number.isFinite(n) ? n : null;
}

function statusTag(t: (key: string, vars?: Record<string, any>) => string, status: DisclosureStatus) {
  if (status === "fully_disclosed") return <Tag color="green">{t("analysis.summary.disclosed")}</Tag>;
  if (status === "partially_disclosed") return <Tag color="gold">{t("analysis.status.partial")}</Tag>;
  return <Tag color="red">{t("analysis.summary.not")}</Tag>;
}

function toDisplayValue(v: any): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") {
    try {
      return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(v);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

function computeSummary(items: AnalysisDataItem[]) {
  const red = items.filter((x) => x.disclosure_status === "not_disclosed").length;
  const yellow = items.filter((x) => x.disclosure_status === "partially_disclosed").length;
  const green = items.filter((x) => x.disclosure_status === "fully_disclosed").length;
  return { red, yellow, green, total: red + yellow + green };
}

function reportLabelFromSummaries(fileId: string, reports?: CrossReportSummary[]) {
  const r = (reports || []).find((x) => x.file_id === fileId);
  const fn = safeTrim(r?.filename);
  return (fn ? stripFileExt(fn) : "") || safeTrim(r?.display_name) || safeTrim(r?.short_name) || fileId;
}

function openEvidence(fileId: string, page: number | null, title: string) {
  const qs = new URLSearchParams({
    file_id: fileId,
    name: title,
  });
  if (page && page > 0) qs.set("page", String(page));
  window.open(`/cross-analysis/evidence?${qs.toString()}`, "_blank", "noopener,noreferrer");
}

function StatusDonut({ red, yellow, total }: { red: number; yellow: number; green: number; total: number }) {
  const redPct = total ? (red / total) * 100 : 0;
  const yellowPct = total ? (yellow / total) * 100 : 0;
  const donutStyle: React.CSSProperties = {
    background: `conic-gradient(#ef4444 0% ${redPct}%, #f59e0b ${redPct}% ${redPct + yellowPct}%, #22c55e ${redPct + yellowPct}% 100%)`,
  };

  return (
    <div className="relative h-14 w-14 md:h-[62px] md:w-[62px] shrink-0 rounded-full" style={donutStyle} aria-hidden>
      <div className="absolute inset-[9px] md:inset-[10px] rounded-full bg-white" />
    </div>
  );
}

export default function DisclosureCompletenessComparison(props: {
  fileIds: string[];
  reports?: CrossReportSummary[];
}) {
  const { t } = useT();
  const router = useRouter();
  const files = useFileStore((state) => state.files);
  const crossAnalysisSelection = useFileStore(
    (state) => state.crossAnalysisSelection,
  );
  const setComplianceSelection = useFileStore(
    (state) => state.setComplianceSelection,
  );
  const { fileIds, reports } = props;
  const fileIdsKey = (fileIds || []).join("\u0000");
  const scopedAssessmentSelections = useMemo(() => {
    const selectedFileIds = fileIdsKey
      ? fileIdsKey.split("\u0000").filter(Boolean)
      : [];
    const committedReports = crossAnalysisSelection?.reports || [];
    const committedIds = committedReports.map((report) => report.fileId);
    const exactCommittedSelection =
      committedIds.length === selectedFileIds.length
      && committedIds.every(
        (fileId, index) => fileId === selectedFileIds[index],
      );
    return selectedFileIds.map((fileId) => ({
      fileId,
      scopeKey: exactCommittedSelection
        ? committedReports.find((report) => report.fileId === fileId)?.scopeKey
        : undefined,
    }));
  }, [crossAnalysisSelection, fileIdsKey]);
  const scopedAssessmentSelectionKey = scopedAssessmentSelections
    .map(({ fileId, scopeKey }) => `${fileId}\u0001${scopeKey || ""}`)
    .join("\u0000");
  const reportsRef = useRef(reports);

  const [resultPage, setResultPage] = useState(1);
  const [resultPageSize, setResultPageSize] = useState(20);
  const [sortMode, setSortMode] = useState<SortMode>("default");

  const [per, setPer] = useState<PerReport[]>(() =>
    (fileIds || []).map((id) => ({
      fileId: id,
      label: reportLabelFromSummaries(id, reports),
      framework: null,
      loading: true,
      error: null,
      metrics: [],
    }))
  );

  useEffect(() => {
    reportsRef.current = reports;
    setPer((prev) => prev.map((p) => ({ ...p, label: reportLabelFromSummaries(p.fileId, reports) })));
  }, [reports]);

  useEffect(() => {
    let cancelled = false;
    const requestedSelections = scopedAssessmentSelectionKey
      ? scopedAssessmentSelectionKey
          .split("\u0000")
          .filter(Boolean)
          .map((entry) => {
            const [fileId, scopeKey] = entry.split("\u0001", 2);
            return { fileId, scopeKey: scopeKey || undefined };
          })
      : [];
    if (requestedSelections.length < 2) return;
    const requestedFileIds = requestedSelections.map(({ fileId }) => fileId);

    setPer((current) => {
      const currentLabels = new Map(
        current.map((item) => [item.fileId, item.label]),
      );
      return requestedFileIds.map((id) => ({
        fileId: id,
        label:
          currentLabels.get(id) ||
          reportLabelFromSummaries(id, reportsRef.current),
        framework: null,
        loading: true,
        error: null,
        metrics: [],
      }));
    });

    (async () => {
      const results = await Promise.all(
        requestedSelections.map(async ({ fileId: id, scopeKey }) => {
          try {
            const assessment = await apiService.getAssessmentByFile(
              id,
              scopeKey,
              false,
              true,
            );

            const actualFramework = safeTrim(
              (assessment as any)?.framework ??
                (assessment as any)?.assessment?.framework ??
                (assessment as any)?.current_framework
            );

            const converted: AnalysisDataItem[] = (assessment?.metric_analyses || [])
              .filter((item: any) => {
                const mid = item?.metric_id ?? item?.metric_code ?? item?.metricId ?? item?.Code ?? item?.code;
                const name = item?.metric_name ?? item?.Metric ?? item?.metric;
                return Boolean(mid && name);
              })
              .map((item: any) => {
                const metric_id = String(
                  item?.metric_id ?? item?.metric_code ?? item?.metricId ?? item?.Code ?? item?.code
                );
                const metric_name = String(item?.metric_name ?? item?.Metric ?? item?.metric);

                const disclosure_status = normalizeStatus(
                  item?.disclosure_status ??
                    item?.disclosureStatus ??
                    item?.status ??
                    item?.["Disclosure Status"] ??
                    item?.["Model Disclosure Status"]
                );

                const value = pick(
                  item?.value,
                  item?.Value,
                  item?.data,
                  item?.Data
                );

                const page = pick(
                  item?.page,
                  item?.Page,
                  item?.page_number,
                  item?.pageNumber,
                  item?.evidence?.page,
                  item?.evidence_segments?.[0]?.page_number,
                  item?.evidence_segments?.[0]?.page
                );

                const unit = pick(item?.unit, item?.Unit) ?? "";
                const category = normalizeCategoryLabel(pick(item?.category, item?.Category) ?? "");
                const topic = pick(item?.topic, item?.Topic) ?? "";
                const type = pick(item?.type, item?.Type) ?? "";
                const reasoning = String(
                  pick(item?.reasoning, item?.["LLM Analysis"], item?.Reasoning, item?.analysis, item?.Analysis) ?? ""
                ).trim();
                const contextRaw = pick(
                  item?.context,
                  item?.Context,
                  item?.specific_data_found,
                  item?.specificDataFound,
                  item?.evidence,
                  item?.evidence_text,
                  item?.evidenceText
                );

                return {
                  metric_id,
                  metric_name,
                  disclosure_status,
                  reasoning,
                  value,
                  page,
                  unit,
                  category,
                  topic,
                  type,
                  context: extractContextText(contextRaw) || null,
                };
              });

            return {
              fileId: id,
              label: id,
              framework: actualFramework || null,
              loading: false,
              error: null,
              metrics: converted,
            } as PerReport;
          } catch (e: any) {
            return {
              fileId: id,
              label: reportLabelFromSummaries(id, reportsRef.current),
              framework: null,
              loading: false,
              error: e?.message || "Error",
              metrics: [],
            } as PerReport;
          }
        })
      );

      if (!cancelled) {
        setPer((current) => {
          const labels = new Map(current.map((item) => [item.fileId, item.label]));
          return results.map((item) => ({
            ...item,
            label: labels.get(item.fileId) || item.label,
          }));
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [scopedAssessmentSelectionKey]);

  const openReport = useCallback((fileId: string) => {
    const scopedSelection = scopedAssessmentSelections.find(
      (report) => report.fileId === fileId,
    );
    const reportCandidates = files.filter((file) => file.file_id === fileId);
    const scopeKey = scopedSelection?.scopeKey
      || (reportCandidates.length === 1
        ? reportCandidates[0].analysis_scope_key
        : undefined);
    // Warm the exact compact assessment variant consumed by the destination
    // page, then navigate immediately so it can share the in-flight promise.
    apiService.prefetchAssessmentByFile(fileId, scopeKey, false, true);
    setComplianceSelection(fileId, scopeKey);
    router.push(buildComplianceAnalysisHref(fileId, scopeKey));
  }, [files, router, scopedAssessmentSelections, setComplianceSelection]);

  const anyLoading = per.some((p) => p.loading);

  const summaryCards = useMemo(() => {
    return per.map((p) => ({ ...p, summary: computeSummary(p.metrics) }));
  }, [per]);

  const orderedSummaryCards = useMemo(() => {
    const xs = [...summaryCards];
    if (sortMode === "report_asc") xs.sort((a, b) => a.label.localeCompare(b.label));
    if (sortMode === "report_desc") xs.sort((a, b) => b.label.localeCompare(a.label));
    if (sortMode === "disclosed_desc") {
      xs.sort((a, b) => {
        const ar = a.summary.total ? a.summary.green / a.summary.total : -1;
        const br = b.summary.total ? b.summary.green / b.summary.total : -1;
        return br - ar || a.label.localeCompare(b.label);
      });
    }
    if (sortMode === "not_disclosed_desc") {
      xs.sort((a, b) => {
        const ar = a.summary.total ? a.summary.red / a.summary.total : -1;
        const br = b.summary.total ? b.summary.red / b.summary.total : -1;
        return br - ar || a.label.localeCompare(b.label);
      });
    }
    return xs;
  }, [summaryCards, sortMode]);

  const orderedPer = useMemo(() => {
    const order = new Map(orderedSummaryCards.map((item, index) => [item.fileId, index] as const));
    return [...per].sort((a, b) => (order.get(a.fileId) ?? 1e9) - (order.get(b.fileId) ?? 1e9));
  }, [per, orderedSummaryCards]);

  const metricUnion = useMemo(() => {
    const map = new Map<string, { metric_id: string; metric_name: string }>();
    for (const p of per) {
      for (const m of p.metrics) {
        if (!map.has(m.metric_id)) map.set(m.metric_id, { metric_id: m.metric_id, metric_name: m.metric_name });
      }
    }
    return Array.from(map.values());
  }, [per]);

  type Row = {
    key: string;
    metric_id: string;
    metric_name: string;
    byReport: Record<string, AnalysisDataItem | null>;
  };

  const tableData: Row[] = useMemo(() => {
    const metricsByReport = new Map<string, Map<string, AnalysisDataItem>>();
    for (const report of per) {
      const byMetric = new Map<string, AnalysisDataItem>();
      for (const metric of report.metrics) {
        // Preserve the previous Array.find behaviour when malformed payloads
        // contain a duplicate metric id: the first occurrence wins.
        if (!byMetric.has(metric.metric_id)) {
          byMetric.set(metric.metric_id, metric);
        }
      }
      metricsByReport.set(report.fileId, byMetric);
    }

    return metricUnion.map((m) => {
      const byReport: Record<string, AnalysisDataItem | null> = {};
      for (const p of per) {
        byReport[p.fileId] =
          metricsByReport.get(p.fileId)?.get(m.metric_id) || null;
      }
      return {
        key: m.metric_id,
        metric_id: m.metric_id,
        metric_name: m.metric_name,
        byReport,
      };
    });
  }, [metricUnion, per]);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(tableData.length / resultPageSize));
    setResultPage((prev) => Math.min(prev, totalPages));
  }, [tableData.length, resultPageSize]);

  const columns: ColumnsType<Row> = useMemo(() => {
    const base: ColumnsType<Row> = [
      {
        title: t("crossAnalysis.table.metric"),
        dataIndex: "metric_name",
        key: "metric_name",
        width: 300,
        fixed: "left",
        render: (_: any, row: Row) => <div className="font-medium text-slate-900">{row.metric_name}</div>,
      },
    ];

    const perCols: ColumnsType<Row> = orderedPer.map((p) => ({
      title: (
        <button
          type="button"
          className="font-semibold text-slate-800 hover:text-blue-600 text-left"
          onClick={() => openReport(p.fileId)}
        >
          {p.label}
        </button>
      ),
      key: p.fileId,
      width: 280,
      render: (_: any, row: Row) => {
        const item = row.byReport[p.fileId];
        if (p.loading) return <span className="text-slate-400">{t("common.loading")}</span>;
        if (p.error) return <span className="text-red-500">{p.error}</span>;
        if (!item) return <span className="text-slate-400">—</span>;

        const status = item.disclosure_status;
        const category = normalizeCategoryLabel(item.category);
        const page = normalizePage(item.page);
        const unit = safeTrim(item.unit);
        const ctx = safeTrim(item.context);
        const evidenceTitle = `${p.label} · ${row.metric_name}`;
        const formattedValue = formatDisplayValue(item.value);
        const reasoningText = safeTrim(item.reasoning);
        const isDiscussionAndAnalysis = category === "Discussion and Analysis";

        let displayValue = "";
        if (status === "fully_disclosed") {
          displayValue = isDiscussionAndAnalysis
            ? reasoningText || t("analysis.noAnalysisText")
            : formattedValue || t("analysis.summary.notSpecified");
        } else {
          displayValue = reasoningText || t("analysis.noAnalysisText");
        }

        const contextContent = (
          <div className="max-w-[520px] p-2">
            <div className="text-xs font-semibold text-gray-700">{t("analysis.columns.context")}</div>
            {ctx ? (
              <div className="mt-1 whitespace-pre-wrap text-sm">{ctx}</div>
            ) : (
              <div className="mt-1 text-xs text-gray-500">{t("analysis.noEvidenceExcerpt")}</div>
            )}
          </div>
        );

        const pageNode = page ? (
          <button
            className="text-blue-500 hover:underline text-xs"
            onClick={(e) => {
              e.stopPropagation();
              openEvidence(p.fileId, page, evidenceTitle);
            }}
            title={t("crossAnalysis.disclosure.openEvidence")}
          >
            {t("crossAnalysis.disclosure.evidencePage", { page })}
          </button>
        ) : null;

        return (
          <div className="space-y-2">
            <div className="flex items-center gap-2">{statusTag(t, status)}</div>
            <div className="text-sm text-slate-900 flex items-start gap-2 flex-wrap">
              <span className="min-w-0 whitespace-pre-wrap break-words">{displayValue}</span>
              <Popover
                content={contextContent}
                title={null}
                trigger="hover"
                mouseEnterDelay={0.2}
                getPopupContainer={(trigger) => trigger.parentElement || document.body}
              >
                <span
                  className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-300 text-gray-700 text-[11px] font-semibold leading-none cursor-pointer select-none shrink-0"
                  aria-label={t("analysis.columns.context")}
                  onClick={(e) => e.stopPropagation()}
                >
                  !
                </span>
              </Popover>
              {status === "fully_disclosed" && !isDiscussionAndAnalysis && unit ? (
                <span className="text-xs text-slate-500 shrink-0">{unit}</span>
              ) : null}
              {pageNode}
            </div>
          </div>
        );
      },
    }));

    return [...base, ...perCols];
  }, [openReport, orderedPer, t]);

  if (!fileIds || fileIds.length < 2) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-6">
        <div className="text-slate-900 font-semibold">{t("crossAnalysis.disclosureCompleteness")}</div>
        <div className="text-slate-600 mt-1">{t("files.selectAtLeastTwoReports")}</div>
      </div>
    );
  }

  if (anyLoading && per.every((p) => p.metrics.length === 0 && !p.error)) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-10 text-center">
        <Spin size="large" />
        <div className="mt-4 text-slate-600">{t("crossAnalysis.disclosure.loading")}</div>
      </div>
    );
  }

  const anyError = per.some((p) => p.error);

  return (
    <div className="space-y-4">
      {anyError ? (
        <Alert
          title={t("crossAnalysis.disclosure.someReportsFailedTitle")}
          description={t("crossAnalysis.disclosure.someReportsFailedDesc")}
          type="warning"
          showIcon
        />
      ) : null}

      <div className="bg-white rounded-2xl shadow-sm px-2.5 py-4 md:px-4 md:py-4">
        <div className="space-y-8">
          <div className="hidden md:grid grid-cols-[minmax(260px,0.61fr)_minmax(260px,0.76fr)_minmax(400px,0.84fr)_minmax(200px,0.76fr)_150px] gap-8 items-center border-slate-200 px-2.5 mb-2">
            <div className="text-xl font-semibold text-slate-800">{t("crossAnalysis.table.report")}</div>
            <div className="text-xl font-semibold text-slate-800 text-center">{t("analysis.summary.not")}</div>
            <div className="text-xl font-semibold text-slate-800 text-center">{t("analysis.status.partial")}</div>
            <div className="text-xl font-semibold text-slate-800 text-center">{t("analysis.summary.disclosed")}</div>
            <div className="flex justify-end">
              <Select
                value={sortMode}
                onChange={(value) => setSortMode(value)}
                size="medium"
                variant="borderless"
                suffixIcon={null}
                className="w-full text-center [&_.ant-select-selection-item]:text-center"
                style={{ width: "100%", fontSize: 14 }}
                options={[
                  { value: "default", label: "Default" },
                  { value: "report_asc", label: "Report A-Z" },
                  { value: "report_desc", label: "Report Z-A" },
                  { value: "disclosed_desc", label: "Disclosed first" },
                  { value: "not_disclosed_desc", label: "Not disclosed first" },
                ]}
              />
            </div>
          </div>

          {orderedSummaryCards.map((p) => {
            const s = p.summary;
            const total = s.total || 0;
            const redPct = total ? `${((s.red / total) * 100).toFixed(1)}%` : "0.0%";
            const yellowPct = total ? `${((s.yellow / total) * 100).toFixed(1)}%` : "0.0%";
            const greenPct = total ? `${((s.green / total) * 100).toFixed(1)}%` : "0.0%";

            return (
              <div
                key={p.fileId}
                className="grid grid-cols-1 md:grid-cols-[minmax(260px,0.61fr)_minmax(260px,0.76fr)_minmax(400px,0.84fr)_minmax(200px,0.76fr)_150px] gap-8 items-center rounded-xl border border-slate-200 px-2.5 mb-4 py-4.5"
              >
                <button
                  type="button"
                  onClick={() => openReport(p.fileId)}
                  className="min-w-0 text-left text-xl font-semibold text-slate-900 truncate pr-1 hover:text-blue-600"
                  title={p.label}
                >
                  {p.label}
                </button>

                {p.loading ? (
                  <div className="md:col-span-4 text-slate-500 text-sm">{t("common.loading")}</div>
                ) : p.error ? (
                  <div className="md:col-span-4 text-red-500 text-sm">{p.error}</div>
                ) : total === 0 ? (
                  <div className="md:col-span-4 text-slate-500 text-sm">{t("crossAnalysis.disclosure.noMetricsFound")}</div>
                ) : (
                  <>
                    <div className="text-center">
                      <div className="text-[40px] font-semibold text-red-500 leading-none">{redPct}</div>
                      <div className="mt-1 text-xl text-slate-500">{s.red}/{total}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-[40px] font-semibold text-amber-500 leading-none">{yellowPct}</div>
                      <div className="mt-1 text-xl text-slate-500">{s.yellow}/{total}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-[40px] font-semibold text-green-500 leading-none">{greenPct}</div>
                      <div className="mt-1 text-xl text-slate-500">{s.green}/{total}</div>
                    </div>
                    <div className="flex items-center justify-center">
                      <StatusDonut red={s.red} yellow={s.yellow} green={s.green} total={total} />
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-2xl shadow-sm p-5">
        <div className="flex items-center justify-between gap-3 mb-4">
          <h3 className="text-lg font-semibold text-gray-800">{t("analysis.resultsTitle")}</h3>
        </div>
        <div className="overflow-x-auto">
          <Table
            className="ca-table-wrap"
            columns={columns}
            dataSource={tableData}
            rowKey="key"
            pagination={{
              current: resultPage,
              pageSize: resultPageSize,
              showSizeChanger: true,
              pageSizeOptions: ["10", "20"],
              onChange: (page, pageSize) => {
                setResultPage(page);
                if (pageSize && pageSize !== resultPageSize) setResultPageSize(pageSize);
              },
            }}
            scroll={{ x: 420 + orderedPer.length * 280 }}
            tableLayout="fixed"
          />
        </div>
      </div>
    </div>
  );
}
