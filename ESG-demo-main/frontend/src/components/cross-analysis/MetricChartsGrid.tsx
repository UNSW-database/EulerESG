"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Empty } from "antd";
import { useT } from "@/i18n/useT";

const Column = dynamic(() => import("@ant-design/plots/es/components/column"), {
  ssr: false,
});

const EAGER_CHART_COUNT = 4;
const CHART_HEIGHT_PX = 418;
const DEFERRED_CHART_ROOT_MARGIN = "600px 0px";

function currentDevicePixelRatio(): number {
  if (typeof window === "undefined") return 1;
  const ratio = Number(window.devicePixelRatio || 1);
  return Number.isFinite(ratio) && ratio > 0 ? Math.max(1, Math.ceil(ratio)) : 1;
}

function useDevicePixelRatioRevision(): number {
  const ratioRef = useRef(currentDevicePixelRatio());
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let refreshTimer = 0;
    const refresh = () => {
      const nextRatio = currentDevicePixelRatio();
      if (nextRatio === ratioRef.current) return;
      ratioRef.current = nextRatio;
      setRevision((value) => value + 1);
    };
    const scheduleRefresh = () => {
      if (refreshTimer) window.clearTimeout(refreshTimer);
      // G2 debounces its own forceFit for 300 ms. Recreate the canvas after
      // that settles so the new instance captures the browser's latest DPR.
      refreshTimer = window.setTimeout(refresh, 400);
    };

    window.addEventListener("resize", scheduleRefresh);
    window.visualViewport?.addEventListener("resize", scheduleRefresh);
    return () => {
      window.removeEventListener("resize", scheduleRefresh);
      window.visualViewport?.removeEventListener("resize", scheduleRefresh);
      if (refreshTimer) window.clearTimeout(refreshTimer);
    };
  }, []);

  return revision;
}

function ResponsiveColumn({
  chartKey,
  config,
  pixelRatioRevision,
}: {
  chartKey: string;
  config: Record<string, unknown>;
  pixelRatioRevision: number;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<any>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof ResizeObserver === "undefined") return;

    let lastWidth = -1;
    let fitTimer = 0;
    const observer = new ResizeObserver((entries) => {
      const width = Math.round(entries[0]?.contentRect.width || 0);
      if (width <= 0 || width === lastWidth) return;
      lastWidth = width;
      if (fitTimer) window.clearTimeout(fitTimer);
      fitTimer = window.setTimeout(() => {
        try {
          const plot = plotRef.current;
          if (typeof plot?.triggerResize === "function") {
            plot.triggerResize();
          } else {
            const chart = plot?.chart ?? plot;
            chart?.forceFit?.();
          }
        } catch {
          // The plot may be between destroy/recreate cycles during browser zoom.
        }
      }, 240);
    });
    observer.observe(host);
    return () => {
      observer.disconnect();
      if (fitTimer) window.clearTimeout(fitTimer);
    };
  }, []);

  return (
    <div ref={hostRef} className="w-full min-w-0">
      <Column
        key={`${chartKey}:dpr-${pixelRatioRevision}`}
        {...(config as any)}
        onReady={(plot: any) => {
          plotRef.current = plot;
        }}
      />
    </div>
  );
}

function ProgressiveColumn({
  chartKey,
  chartLabel,
  config,
  eager,
  loadingLabel,
  pixelRatioRevision,
  topic,
}: {
  chartKey: string;
  chartLabel: string;
  config: Record<string, unknown>;
  eager: boolean;
  loadingLabel: string;
  pixelRatioRevision: number;
  topic: string;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [shouldRender, setShouldRender] = useState(eager);

  useEffect(() => {
    if (eager) setShouldRender(true);
  }, [eager]);

  useEffect(() => {
    if (shouldRender) return;
    const host = hostRef.current;
    if (!host) return;

    if (typeof IntersectionObserver === "undefined") {
      // Progressive mounting is an enhancement. Older browsers still receive
      // every chart instead of being left with permanent placeholders.
      setShouldRender(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (
          !entries.some(
            (entry) => entry.isIntersecting || entry.intersectionRatio > 0,
          )
        ) {
          return;
        }
        setShouldRender(true);
        observer.disconnect();
      },
      {
        root: null,
        rootMargin: DEFERRED_CHART_ROOT_MARGIN,
        threshold: 0.01,
      },
    );
    observer.observe(host);
    return () => observer.disconnect();
  }, [shouldRender]);

  return (
    <div
      ref={hostRef}
      className="w-full min-w-0"
      data-chart-state={shouldRender ? "ready" : "deferred"}
      data-testid="metric-chart-region"
      role="group"
      aria-busy={!shouldRender}
      aria-label={`${chartLabel}: ${topic}`}
      style={{ minHeight: CHART_HEIGHT_PX }}
    >
      {shouldRender ? (
        <ResponsiveColumn
          chartKey={chartKey}
          config={config}
          pixelRatioRevision={pixelRatioRevision}
        />
      ) : (
        <div
          className="flex w-full items-center justify-center rounded-xl bg-slate-50 text-sm text-slate-500"
          data-testid="metric-chart-placeholder"
          style={{ height: CHART_HEIGHT_PX }}
        >
          <span aria-hidden="true">{loadingLabel}</span>
          <span className="sr-only">{`${topic}: ${loadingLabel}`}</span>
        </div>
      )}
    </div>
  );
}

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

function normalizeUnit(unit?: string | null): string {
  return String(unit ?? "").trim().toLowerCase();
}

function isPercentUnit(unit?: string | null): boolean {
  const u = normalizeUnit(unit);
  return (
    u === "%" ||
    u === "percent" ||
    u === "percentage" ||
    u.includes("%") ||
    u.includes("percent") ||
    u.includes("percentage")
  );
}

function clampPercentValue(value: number): number {
  if (!Number.isFinite(value)) return value;
  if (value < 0) return 0;
  if (value > 100) return 100;
  return value;
}

function getYAxisMax(values: number[], unit?: string | null): number {
  if (!values.length) return 1;
  const maxValue = Math.max(...values);
  if (isPercentUnit(unit)) return 100;
  if (!(maxValue > 0)) return 1;
  return maxValue * 1.01;
}

function formatAxisLabel(text: any, compact: boolean): string {
  const s = String(text ?? "");
  if (!compact) return s;
  if (s.length <= 18) return s;
  return `${s.slice(0, 18)}…`;
}

function formatCompactNumber(value: any): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value ?? "");
  const abs = Math.abs(num);
  if (abs >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(0)}B`;
  if (abs >= 1_000_000) return `${(num / 1_000_000).toFixed(0)}M`;
  if (abs >= 1_000) return `${(num / 1_000).toFixed(0)}K`;
  return num.toLocaleString();
}

function MetricChartsGridInner({
  charts,
  companyColors,
}: {
  charts: MetricChartSpec[];
  companyColors: Record<string, string>;
}) {
  const { t } = useT();
  const pixelRatioRevision = useDevicePixelRatioRevision();

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
  const chartLabel = t("crossAnalysis.comparisonChartTitle");
  const loadingLabel = t("common.loading");

  return (
    <div
      className="grid gap-2.5"
      data-testid="metric-charts-grid"
      style={{
        gridTemplateColumns:
          "repeat(auto-fit, minmax(min(100%, 20rem), 1fr))",
      }}
    >
      {normalizedCharts.map((chart, index) => {
        const percentChart = isPercentUnit(chart.unit);

        const data = chart.points.map((point) => {
          const rawValue = Number(point.value);
          const finalValue =
            percentChart && Number.isFinite(rawValue)
              ? clampPercentValue(rawValue)
              : rawValue;

          return {
            company: point.company,
            colorKey: point.colorKey || point.company,
            value: finalValue,
            year: point.year ?? null,
            unit: chart.unit ? String(chart.unit) : "",
          };
        });

        const compactLabels =
          normalizedCharts.length >= 4 ||
          data.some((d) => String(d.company).length > 18);
        const chartHeight = CHART_HEIGHT_PX;
        const yValues = data
          .map((d) => Number(d.value))
          .filter((n) => Number.isFinite(n));

        const isPercent = isPercentUnit(chart.unit);
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
            y: {
              domainMin: 0,
              domainMax: getYAxisMax(yValues, chart.unit),
              nice: !isPercent,
            },
          },
          color: ({ colorKey }: any) =>
            companyColors[String(colorKey)] || "#1677ff",
          legend: false,
          animation: false,
          autoFit: true,
          padding: [0, 0, 0, 0],
          appendPadding: 0,
          margin: 0,
          inset: 5,
          columnWidthRatio:
            data.length >= 4 ? 0.58 : data.length === 1 ? 0.44 : 0.54,
          columnStyle: { radius: [8, 8, 0, 0] },
          axis: {
            x: {
              title: false,
              labelAutoHide: false,
              labelAutoRotate: false,
              labelAutoWrap: false,
              labelFill: "#64748B",
              labelFontSize: 13,
              labelFormatter: (value: any) =>
                formatAxisLabel(value, compactLabels),
            },
            y: {
              title: false,
              labelAutoHide: false,
              labelFill: "#64748B",
              labelFontSize: 13,
              labelFormatter: (value: any) => formatCompactNumber(value),
              line: false,
              grid: true,
              gridStroke: "#E2E8F0",
              gridLineDash: [3, 3],
              tick: false,
            },
          },
          tooltip: {
            title: false,
            marker: false,
            shared: false,
            offset: 12,
            showDelay: 400,
            hideDelay: 0,
            follow: true,
            enterable: false,
            items: [
              (datum: any) => ({ name: "Name", value: datum.company }),
              (datum: any) => ({ name: "Year", value: datum.year || "—" }),
              (datum: any) => ({
                name: "Value",
                value: Number.isFinite(Number(datum.value))
                  ? Number(datum.value).toLocaleString()
                  : "—",
              }),
              (datum: any) => ({ name: "Unit", value: datum.unit || "—" }),
            ],
          },
          interaction: { elementHighlight: true },
          interactions: [{ type: "element-active" }, { type: "tooltip" }],
          height: chartHeight,
        };

        return (
          <div
            key={chart.key}
            className="min-w-0 bg-white rounded-2xl shadow-sm px-2 py-2"
          >
            <div className="mb-1 min-w-0 px-1">
              <div className="font-semibold text-slate-900 text-[15px] leading-snug break-words">
                {chart.topic}
              </div>
              <div className="text-xs text-slate-500 mt-0.5">
                {chart.unit ? `Unit: ${chart.unit}` : "Unit: —"}
                {chart.yearInfo ? ` · ${chart.yearInfo}` : ""}
              </div>
            </div>

            <div className="w-full min-w-0 pt-2">
              <ProgressiveColumn
                chartKey={chart.key}
                chartLabel={chartLabel}
                config={config}
                eager={index < EAGER_CHART_COUNT}
                loadingLabel={loadingLabel}
                pixelRatioRevision={pixelRatioRevision}
                topic={chart.topic}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export const MetricChartsGrid = React.memo(
  MetricChartsGridInner,
  (prev, next) => prev.charts === next.charts && prev.companyColors === next.companyColors
);
