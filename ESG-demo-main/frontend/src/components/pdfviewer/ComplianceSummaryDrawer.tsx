"use client";

import React, { useMemo } from "react";
import { Drawer, Tag } from "antd";
import { Download, FileText, TrendingUp, TriangleAlert } from "lucide-react";

import { useT } from "@/i18n/useT";
import {
  buildComplianceSummary,
  createComplianceSummaryMarkdown,
  disclosurePercentage,
  type ComplianceSummaryMetric,
} from "@/lib/complianceSummary";

interface ComplianceSummaryDrawerProps {
  metrics: ComplianceSummaryMetric[];
  open: boolean;
  onClose: () => void;
  reportName?: string;
}

const EMPTY_VALUES = new Set(["", "-", "n/a", "na", "null", "none"]);

const metricCode = (metric: ComplianceSummaryMetric) =>
  metric.metric_code || metric.metric_id;

const metricValue = (metric: ComplianceSummaryMetric): string => {
  if (metric.value === null || metric.value === undefined) return "";
  const value = String(metric.value).trim();
  if (EMPTY_VALUES.has(value.toLowerCase())) return "";
  return `${value}${metric.unit ? ` ${metric.unit}` : ""}`;
};

const safeDownloadName = (reportName?: string) => {
  const base = (reportName || "compliance-report")
    .replace(/\.[^.]+$/, "")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .trim();
  return `${base || "compliance-report"}-disclosure-summary.md`;
};

const ComplianceSummaryDrawer: React.FC<ComplianceSummaryDrawerProps> = ({
  metrics,
  open,
  onClose,
  reportName,
}) => {
  const { t, lang } = useT();
  const summary = useMemo(() => buildComplianceSummary(metrics), [metrics]);

  const downloadSummary = () => {
    const content = createComplianceSummaryMarkdown(summary, {
      reportName,
      lang: lang === "zh" ? "zh" : "en",
    });
    const url = URL.createObjectURL(
      new Blob([content], { type: "text/markdown;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = safeDownloadName(reportName);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const renderMetric = (metric: ComplianceSummaryMetric, tone: "good" | "gap") => {
    const value = metricValue(metric);
    const hasPage =
      metric.page !== null &&
      metric.page !== undefined &&
      String(metric.page).trim().length > 0;
    const isPartial = metric.disclosure_status === "partially_disclosed";

    return (
      <article
        key={metric.metric_id}
        className={`rounded-xl border p-4 transition-colors ${
          tone === "good"
            ? "border-emerald-100 bg-emerald-50/50"
            : "border-amber-100 bg-amber-50/45"
        }`}
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {metricCode(metric)}
            </div>
            <h4 className="mt-1 text-sm font-semibold leading-5 text-slate-900">
              {metric.metric_name}
            </h4>
          </div>
          <Tag color={tone === "good" ? "success" : isPartial ? "gold" : "error"}>
            {tone === "good"
              ? t("analysis.status.fully")
              : isPartial
                ? t("analysis.status.partial")
                : t("analysis.status.not")}
          </Tag>
        </div>

        {(value || hasPage) && (
          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-600">
            {value && (
              <span>
                <span className="font-semibold">{t("analysis.summaryValueLabel")}:</span>{" "}
                {value}
              </span>
            )}
            {hasPage && (
              <span>
                <span className="font-semibold">{t("analysis.summaryPageLabel")}:</span>{" "}
                {metric.page}
              </span>
            )}
          </div>
        )}

        <p className="mt-3 text-sm leading-6 text-slate-700">
          {metric.reasoning?.trim() || t("analysis.noAnalysisText")}
        </p>
      </article>
    );
  };

  const overview = [
    {
      label: t("analysis.metricsAssessed"),
      count: summary.total,
      percent: summary.total > 0 ? "100.0" : "0.0",
      color: "text-slate-800",
    },
    {
      label: t("analysis.wellDisclosed"),
      count: summary.wellDisclosed.length,
      percent: disclosurePercentage(summary.wellDisclosed.length, summary.total),
      color: "text-emerald-600",
    },
    {
      label: t("analysis.summary.partial"),
      count: summary.partiallyDisclosed.length,
      percent: disclosurePercentage(summary.partiallyDisclosed.length, summary.total),
      color: "text-amber-600",
    },
    {
      label: t("analysis.summary.not"),
      count: summary.notDisclosed.length,
      percent: disclosurePercentage(summary.notDisclosed.length, summary.total),
      color: "text-red-600",
    },
  ];

  return (
    <Drawer
      title={
        <div className="flex items-center gap-2">
          <FileText className="h-5 w-5 text-[#2274BC]" />
          <span>{t("analysis.disclosureSummaryReport")}</span>
        </div>
      }
      placement="right"
      size="min(760px, 96vw)"
      open={open}
      onClose={onClose}
      destroyOnHidden={false}
      extra={
        <button
          type="button"
          onClick={downloadSummary}
          disabled={summary.total === 0}
          className="inline-flex h-8 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition-colors hover:border-[#2274BC] hover:text-[#2274BC] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Download className="h-3.5 w-3.5" />
          {t("analysis.downloadSummary")}
        </button>
      }
      styles={{
        body: {
          padding: 20,
          background: "#f8fafc",
          overflowY: "auto",
          // The summary is a modal surface: wheel/touch gestures stay inside it
          // instead of moving the obscured Compliance page behind the mask.
          overscrollBehaviorY: "contain",
          WebkitOverflowScrolling: "touch",
        },
      }}
    >
      <div className="space-y-6">
        <div className="rounded-xl border border-blue-100 bg-blue-50/70 p-4">
          {reportName && (
            <div className="mb-1 truncate text-sm font-semibold text-slate-900">
              {reportName}
            </div>
          )}
          <p className="text-sm leading-6 text-slate-600">
            {t("analysis.disclosureSummaryDescription")}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {overview.map((item) => (
            <div key={item.label} className="rounded-xl border border-slate-200 bg-white p-3 text-center">
              <div className={`text-2xl font-bold ${item.color}`}>{item.count}</div>
              <div className="mt-0.5 text-xs font-medium text-slate-600">{item.label}</div>
              <div className="mt-1 text-[11px] text-slate-400">{item.percent}%</div>
            </div>
          ))}
        </div>

        <section>
          <div className="mb-3 flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-emerald-600" />
            <h3 className="text-base font-semibold text-slate-900">
              {t("analysis.wellDisclosedMetrics")} ({summary.wellDisclosed.length})
            </h3>
          </div>
          <div className="space-y-3">
            {summary.wellDisclosed.length > 0 ? (
              summary.wellDisclosed.map((metric) => renderMetric(metric, "good"))
            ) : (
              <div className="rounded-xl border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-500">
                {t("analysis.noWellDisclosedMetrics")}
              </div>
            )}
          </div>
        </section>

        <section>
          <div className="mb-3 flex items-center gap-2">
            <TriangleAlert className="h-5 w-5 text-amber-600" />
            <h3 className="text-base font-semibold text-slate-900">
              {t("analysis.metricsNeedingImprovement")} ({summary.needsImprovement.length})
            </h3>
          </div>
          <div className="space-y-3">
            {summary.needsImprovement.length > 0 ? (
              summary.needsImprovement.map((metric) => renderMetric(metric, "gap"))
            ) : (
              <div className="rounded-xl border border-dashed border-emerald-200 bg-emerald-50/50 p-5 text-sm text-emerald-700">
                {t("analysis.noMetricsNeedingImprovement")}
              </div>
            )}
          </div>
        </section>
      </div>
    </Drawer>
  );
};

export default React.memo(ComplianceSummaryDrawer);
