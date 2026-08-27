"use client";

import React, { useMemo } from "react";
import dynamic from "next/dynamic";
import { Empty } from "antd";
import { useT } from "@/i18n/useT";

const Column = dynamic(() => import("@ant-design/plots/es/components/column"), {
  ssr: false,
});

export type MetricChartPoint = {
  company: string;
  colorKey?: string;
  value: number | null;
  year?: string | null;
};

export type MetricChartSpec = {
  key: string;
  topic: string;
  unit?: string | null;
  yearInfo?: string;
  points: MetricChartPoint[];
};

function wrapAxisLabel(text: any, maxLen = 22): string {
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

  return s;
}

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function getRowSpan(rowLength: number): string {
  if (rowLength <= 1) return "md:col-span-12";
  if (rowLength === 2) return "md:col-span-6";
  if (rowLength === 3) return "md:col-span-4";
  return "md:col-span-3";
}

function MetricChartsGridInner({
  charts,
  companyColors,
}: {
  charts: MetricChartSpec[];
  companyColors: Record<string, string>;
}) {
  const { t } = useT();

  const normalizedCharts = useMemo(() => {
    return (charts || [])
      .map((chart) => {
        const numericPoints = (chart.points || []).filter(
          (p) => p.value !== null && p.value !== undefined && Number.isFinite(Number(p.value))
        );

        return {
          ...chart,
          points: numericPoints.map((p) => ({
            ...p,
            colorKey: p.colorKey || p.company,
            value: Number(p.value),
          })),
        };
      })
      .filter((chart) => chart.points.length > 0);
  }, [charts]);

  if (!normalizedCharts.length) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-6">
        <Empty description={t("crossAnalysis.noComparableMetrics")} />
      </div>
    );
  }

  const colorDomain = Object.keys(companyColors);
  const colorRange = colorDomain.map((key) => companyColors[key]);
  const rows = chunk(normalizedCharts, 4);

  return (
    <div className="space-y-2.5">
      {rows.map((row, rowIndex) => (
        <div key={`chart-row-${rowIndex}`} className="grid grid-cols-1 md:grid-cols-12 gap-2.5">
          {row.map((chart) => {
            const data = chart.points.map((point) => ({
              company: point.company,
              colorKey: point.colorKey || point.company,
              value: Number(point.value),
              year: point.year ?? null,
              unit: chart.unit ? String(chart.unit) : "",
            }));

            const rowSpanClass = getRowSpan(row.length);
            const shouldSpanTwo =
              row.length >= 3 &&
              (chart.topic.length > 34 || data.length >= 4 || data.some((d) => String(d.company).length > 18));
            const spanClass = shouldSpanTwo ? "md:col-span-6" : rowSpanClass;
            const labelWrapLen = row.length < 4 ? 28 : 20;
            const chartHeight = shouldSpanTwo || row.length < 4 ? 430 : 390;

            const config: any = {
              data,
              xField: "company",
              yField: "value",
              colorField: "colorKey",
              scale: {
                color: {
                  domain: colorDomain,
                  range: colorRange,
                },
              },
              color: ({ colorKey }: any) => companyColors[String(colorKey)] || "#1677ff",
              legend: false,
              animation: false,
              autoFit: true,
              padding: [11, 11, 11, 11],
              appendPadding: [0, 0, 0, 0],
              margin: 0,
              columnWidthRatio: data.length >= 4 ? 0.58 : data.length === 1 ? 0.44 : 0.54,
              columnStyle: { radius: [8, 8, 0, 0] },
              axis: {
                x: {
                  title: false,
                  labelAutoHide: false,
                  labelAutoRotate: false,
                  labelAutoWrap: false,
                  labelFill: "#64748B",
                  labelFontSize: 14,
                  labelFormatter: (value: any) => wrapAxisLabel(value, labelWrapLen),
                },
                y: {
                  title: false,
                  labelAutoHide: false,
                  labelFill: "#64748B",
                  labelFontSize: 14,
                  labelFormatter: (v: any) => {
                    const num = Number(v);
                    if (!Number.isFinite(num)) return String(v ?? "");
                    if (Math.abs(num) >= 1_000_000_000) return `${((num / 1_000_000_000).toFixed(1))}B`;
                    if (Math.abs(num) >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
                    if (Math.abs(num) >= 1000) return `${(num / 1000).toFixed(1)}K`;
                    return num.toLocaleString();
                  },
                },
              },
              tooltip: {
                title: false,
                marker: false,
                shared: false,
                items: [
                  (datum: any) => ({ name: "Name", value: datum.company }),
                  (datum: any) => ({ name: "Year", value: datum.year || "—" }),
                  (datum: any) => ({
                    name: "Value",
                    value: Number.isFinite(Number(datum.value)) ? Number(datum.value).toLocaleString() : "—",
                  }),
                  (datum: any) => ({ name: "Unit", value: datum.unit || "—" }),
                ],
              },
              interaction: { elementHighlight: true },
              interactions: [{ type: "element-active" }, { type: "tooltip" }],
              height: chartHeight,
            };

            return (
              <div key={chart.key} className={`${spanClass} bg-white rounded-2xl shadow-sm px-2 py-2 min-w-0`}>
                <div className="mb-1 min-w-0 px-1">
                  <div className="font-semibold text-slate-900 text-[15px] leading-snug break-words">{chart.topic}</div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    {chart.unit ? `Unit: ${chart.unit}` : "Unit: —"}
                    {chart.yearInfo ? ` · ${chart.yearInfo}` : ""}
                  </div>
                </div>

                <div className="w-full min-h-[288px] pt-2">
                  <Column {...config} />
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export const MetricChartsGrid = React.memo(
  MetricChartsGridInner,
  (prev, next) => prev.charts === next.charts && prev.companyColors === next.companyColors
);
