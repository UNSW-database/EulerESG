"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useT } from "@/i18n/useT";

const ColumnPlot = dynamic(
  () => import("@ant-design/plots/es/components/column"),
  { ssr: false },
);

export type MetricChartRow = {
  report: string;
  value: number;
  unit?: string | null;
};

export type MetricChart = {
  key: string;
  metric: string;
  unitTitle?: string;
  rows: MetricChartRow[];
  // used for ordering
  maxVal?: number;
  // stable order for consistent coloring across charts
  reportOrder?: string[];
};

// Keep the same palette logic as the original grouped chart:
// first report -> green; the rest -> a stable palette.
const PALETTE = ["#3B82F6", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#06B6D4"];

function wrapAxisLabel(text: any, maxLen = 14): string {
  const s = String(text ?? "");
  if (s.length <= maxLen) return s;
  if (s.includes(" ")) {
    const words = s.split(/\s+/).filter(Boolean);
    const lines: string[] = [];
    let line = "";
    for (const w of words) {
      const next = line ? `${line} ${w}` : w;
      if (next.length > maxLen) {
        if (line) lines.push(line);
        line = w;
      } else {
        line = next;
      }
    }
    if (line) lines.push(line);
    return lines.join("\n");
  }
  const chars = Array.from(s);
  const lines: string[] = [];
  for (let i = 0; i < chars.length; i += maxLen) {
    lines.push(chars.slice(i, i + maxLen).join(""));
  }
  return lines.join("\n");
}

function ClientOnly({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return <>{children}</>;
}

export function MetricComparisonCard({ chart }: { chart: MetricChart }) {
  const { t } = useT();
  const UNIT_VARIES = t("analysis.unitVaries");
  const reportOrder = useMemo(() => (chart.reportOrder && chart.reportOrder.length ? chart.reportOrder : chart.rows.map((r) => r.report)), [chart]);

  const colorMap = useMemo(() => {
    const m: Record<string, string> = {};
    const ordered = reportOrder.filter(Boolean);
    ordered.forEach((rn, idx) => {
      if (idx === 0) {
        m[rn] = "#10B981"; // green
      } else {
        m[rn] = PALETTE[(idx - 1) % PALETTE.length];
      }
    });
    // Ensure every report in rows has a color
    chart.rows.forEach((r) => {
      if (!m[r.report]) m[r.report] = PALETTE[0];
    });
    return m;
  }, [reportOrder, chart.rows]);

  const uniqUnits = useMemo(() => {
    const s = new Set<string>();
    chart.rows.forEach((r) => {
      const u = r.unit ? String(r.unit).trim() : "";
      if (u) s.add(u);
    });
    return Array.from(s);
  }, [chart.rows]);

  const yUnitTitle = useMemo(() => {
    if (chart.unitTitle) return chart.unitTitle;
    if (uniqUnits.length === 1) return uniqUnits[0];
    if (uniqUnits.length > 1) return UNIT_VARIES;
    return "";
  }, [chart.unitTitle, uniqUnits]);

  const data = useMemo(() => {
    // Keep the report ordering stable and drop any invalid numbers.
    const m = new Map<string, MetricChartRow>();
    chart.rows.forEach((r) => {
      if (typeof r.value === "number" && Number.isFinite(r.value)) m.set(r.report, r);
    });
    const ordered = reportOrder.filter((rn) => m.has(rn)).map((rn) => m.get(rn) as MetricChartRow);
    // Fallback: any remaining reports not in order list
    const rest = Array.from(m.values()).filter((r) => !reportOrder.includes(r.report));
    return [...ordered, ...rest];
  }, [chart.rows, reportOrder]);

  const config = useMemo(() => {
    return {
      data,
      xField: "report",
      yField: "value",
      colorField: "report",
      // @ant-design/plots supports color as function in 2.x
      color: (datum: any) => colorMap[String(datum?.report ?? "")] || "#3B82F6",
      state: {
        active: {
          style: {
            lineWidth: 2,
            stroke: "#111827",
            shadowBlur: 14,
            shadowColor: "rgba(0,0,0,0.25)",
          },
        },
        inactive: { style: { opacity: 0.35 } },
      },
      axis: {
        x: {
          labelFill: "#64748B",
          labelFontSize: 12,
          labelFormatter: (t: any) => wrapAxisLabel(t, 16),
        },
        y: {
          ...(yUnitTitle ? { title: yUnitTitle } : {}),
          titleFill: "#64748B",
          titleFontSize: 12,
          labelFill: "#64748B",
          labelFontSize: 12,
          labelFormatter: (v: any) => {
            const num = Number(v);
            if (!Number.isFinite(num)) return String(v ?? "");
            if (Math.abs(num) >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
            if (Math.abs(num) >= 1000) return `${(num / 1000).toFixed(0)}K`;
            return String(num);
          },
        },
      },
      xAxis: {
        label: {
          rotate: 0,
          autoRotate: false,
          autoEllipsis: false,
          formatter: (t: any) => wrapAxisLabel(t, 16),
        },
        tickLine: false,
        line: { style: { stroke: "#E2E8F0" } },
      },
      yAxis: {
        title: yUnitTitle ? { text: yUnitTitle } : undefined,
        tickLine: false,
        line: { style: { stroke: "#E2E8F0" } },
        grid: { line: { style: { stroke: "#E2E8F0", lineDash: [3, 3] } } },
      },
      tooltip: {
        shared: false,
        showMarkers: false,
        customItems: (items: any[]) => (items && items.length ? [items[0]] : items),
        customContent: (title: string, items: any[]) => {
          if (!items || items.length === 0) return null;
          const it: any = items[0];
          const datum: any = it?.data?.data ?? it?.data ?? it?.datum ?? it?.mappingData?._origin ?? {};
          const report = String(datum?.report ?? title ?? "");
          const unit = datum?.unit ? String(datum.unit) : "";
          const value = typeof datum?.value === "number" && Number.isFinite(datum.value) ? datum.value : Number(it?.value);
          const valueText = Number.isFinite(value) ? value.toLocaleString() : "—";
          const escapeHtml = (text: string) =>
            String(text)
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/\"/g, "&quot;")
              .replace(/'/g, "&#039;");
          const unitSuffix = unit ? ` ${escapeHtml(unit)}` : (yUnitTitle && yUnitTitle !== UNIT_VARIES ? ` ${escapeHtml(yUnitTitle)}` : "");
          return `
            <div style="font-size:12px; color:#0F172A;">
              <div style="font-weight:600; margin-bottom:6px;">${escapeHtml(chart.metric)}</div>
              <div style="margin-bottom:4px;"><span style="color:#64748B;">${escapeHtml(t("crossAnalysis.table.report"))}:</span> ${escapeHtml(report)}</div>
              <div><span style="color:#64748B;">${escapeHtml(t("analysis.columns.value"))}:</span> ${escapeHtml(valueText)}${unitSuffix}</div>
            </div>
          `;
        },
        domStyles: {
          "g2-tooltip": {
            padding: "10px 12px",
            borderRadius: "10px",
          },
        },
      },
      legend: false,
      columnStyle: { radius: [8, 8, 0, 0] },
      height: 320,
      interactions: [{ type: "element-active" }, { type: "element-highlight" }],
    };
  }, [data, colorMap, yUnitTitle, chart.metric]);

  if (!data.length) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-4 text-center text-[#64748B]">
        {t("common.noDataAvailable")}
      </div>
    );
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm p-4 w-full">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <div className="text-[14px] font-semibold text-[#0F172A] truncate" title={chart.metric}>{chart.metric}</div>
          {yUnitTitle ? <div className="text-[12px] text-[#64748B] truncate" title={yUnitTitle}>{yUnitTitle}</div> : null}
        </div>
      </div>
      <ClientOnly>
        <ColumnPlot {...(config as any)} />
      </ClientOnly>
    </div>
  );
}
