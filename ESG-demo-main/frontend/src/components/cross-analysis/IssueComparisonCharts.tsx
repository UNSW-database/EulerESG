"use client";

import React, { useMemo, useEffect, useState } from "react";
import { Tabs, Select } from "antd";
import dynamic from "next/dynamic";
import type { CrossExtractedRecord } from "@/features/crossAnalysis/types";
import { useT } from "@/i18n/useT";

// NOTE:
// AntV/G2-based charts may render as an empty box in Next.js when the plot
// library is evaluated too early (SSR/hydration) or when the container size is
// temporarily zero on first paint. We therefore:
// 1) dynamically import the plot component with ssr:false, and
// 2) delay the first render until after mount.

const ColumnPlot = dynamic(
  () => import("@ant-design/plots/es/components/column"),
  { ssr: false },
);

function ClientOnly({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return <>{children}</>;
}

type ChartDatum = {
  x: string; // label (+ year)
  companyKey: string; // stable key (prefer report id)
  companyLabel: string; // display label used in legend/tooltip
  value: number;
  raw: string;
  topic: string;
  subTopic: string;
  year: string | null;
  unit: string | null;
};

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

function extractNumber(v?: any): number | null {
  // Keep 0 values; only treat null/undefined/empty-string as missing.
  if (v === null || v === undefined) return null;
  const s0 = String(v);
  if (s0.trim() === "") return null;

  // Robust numeric parsing for common report formats:
  // - thousands separators: 12,345.67
  // - scientific notation: 1.2e5
  // - leading +/-
  const m = s0.replace(/,/g, "").match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/i);
  if (!m) return null;
  let n = Number(m[0]);
  if (!Number.isFinite(n)) return null;

  // Accounting style: parentheses indicate negatives, e.g. "(1,234)".
  if (!m[0].startsWith("-") && s0.includes("(") && s0.includes(")")) {
    n = -Math.abs(n);
  }
  return n;
}

function buildXLabel(label: string, year: string | null): string {
  // Year as sub-category under the metric label.
  return year ? `${label}\n${year}` : label;
}

function uniq<T>(arr: T[]): T[] {
  const out: T[] = [];
  const s = new Set<T>();
  for (const x of arr) {
    if (s.has(x)) continue;
    s.add(x);
    out.push(x);
  }
  return out;
}

function normalizeUnit(u: string | null): string {
  return (u || "—").trim() || "—";
}

function formatNumber(n: number): string {
  const isInt = Number.isFinite(n) && Math.abs(n - Math.round(n)) < 1e-9;
  const fmt = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: isInt ? 0 : 6,
  });
  return fmt.format(n);
}


function wrapAxisLabel(text: any, maxLen = 18): string {
  const s = String(text ?? "");
  if (s.length <= maxLen) return s;

  // Prefer wrapping by whitespace when possible
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

  // Fallback: wrap by characters
  const chars = Array.from(s);
  const lines: string[] = [];
  for (let i = 0; i < chars.length; i += maxLen) {
    lines.push(chars.slice(i, i + maxLen).join(""));
  }
  return lines.join("\n");
}

export type UnitChart = {
  unitKey: string;
  unitRaw: string | null;
  categories: string[];
  data: ChartDatum[];
};

export function getComparableUnitCharts(rows: CrossExtractedRecord[], selectedCompanies: string[]): UnitChart[] {
  const companyKeysFromRows = uniq(
    rows
      .map((r: any) => safeTrim(r?.id) || safeTrim(r?.name))
      .filter(Boolean)
  );
  const companies = uniq(selectedCompanies.map((x) => safeTrim(x)).filter(Boolean));
  const companyKeys = (companies.length ? companies : companyKeysFromRows);
  if (companyKeys.length < 2) return [];

  // key -> display label (legend/tooltip)
  const keyToLabel = new Map<string, string>();
  for (const r of rows as any[]) {
    const key = safeTrim(r?.id) || safeTrim(r?.name);
    if (!key) continue;
    const label = safeTrim(r?.name) || key;
    if (!keyToLabel.has(key)) keyToLabel.set(key, label);
  }

  // Group by unit
  const byUnit = new Map<string, CrossExtractedRecord[]>();
  for (const r of rows) {
    const uk = normalizeUnit(r.unit);
    const a = byUnit.get(uk) || [];
    a.push(r);
    byUnit.set(uk, a);
  }

  type Candidate = {
    value: number;
    raw: string;
    topic: string;
    subTopic: string;
    year: string | null;
    detail: string;
  };

  function pickCandidate(cands: Candidate[]): Candidate {
    // Deterministic selection so tooltips are stable.
    const sorted = [...cands].sort((a, b) => {
      const ae = a.detail ? 0 : 1;
      const be = b.detail ? 0 : 1;
      if (ae !== be) return ae - be;
      if (a.detail.length !== b.detail.length) return a.detail.length - b.detail.length;
      const dc = a.detail.localeCompare(b.detail);
      if (dc) return dc;
      return a.raw.localeCompare(b.raw);
    });
    return sorted[0];
  }

  const charts: UnitChart[] = [];

  for (const [unitKey, unitRows] of byUnit.entries()) {
    const perCompany = new Map<string, Map<string, Candidate[]>>();
    for (const c of companyKeys) perCompany.set(c, new Map());

    for (const r of unitRows) {
      const company = safeTrim((r as any).id) || safeTrim((r as any).name);
      if (!perCompany.has(company)) continue;
      const num = extractNumber(r.data);
      if (num === null) continue;

      const topic = safeTrim((r as any).topic) || "(unknown)";
      const subTopic = safeTrim((r as any).sub_topic);
      const year = safeTrim((r as any).year) || null;
      const display = subTopic ? `${topic} (${subTopic})` : topic;
      const x = buildXLabel(display, year);
      const detail = safeTrim((r as any).detail);

      const m = perCompany.get(company)!;
      const arr = m.get(x) || [];
      if (!arr.some((it) => it.detail === detail && it.value === num)) {
        arr.push({ value: num, raw: r.data || "", topic, subTopic, year, detail });
      }
      m.set(x, arr);
    }

    // Keep x only when EVERY company has at least one candidate (no missing side).
    const allX = new Set<string>();
    for (const c of companyKeys) {
      for (const x of perCompany.get(c)!.keys()) allX.add(x);
    }

    const comparableX: string[] = [];
    for (const x of allX) {
      let ok = true;
      for (const c of companyKeys) {
        const cands = perCompany.get(c)!.get(x);
        if (!cands || !cands.length) {
          ok = false;
          break;
        }
      }
      if (ok) comparableX.push(x);
    }

    // Stable ordering: label asc, year asc when numeric
    comparableX.sort((a, b) => {
      const [al, ay] = a.split("\n");
      const [bl, by] = b.split("\n");
      if (al !== bl) return al.localeCompare(bl);
      const ya = ay ? Number(ay) : NaN;
      const yb = by ? Number(by) : NaN;
      if (Number.isFinite(ya) && Number.isFinite(yb)) return ya - yb;
      return (ay || "").localeCompare(by || "");
    });

    if (!comparableX.length) continue;

    const data: ChartDatum[] = [];
    for (const x of comparableX) {
      for (const company of companyKeys) {
        const cands = perCompany.get(company)!.get(x) || [];
        const chosen = pickCandidate(cands);
        data.push({
          x,
          companyKey: company,
          companyLabel: keyToLabel.get(company) || company,
          value: chosen.value,
          raw: chosen.raw,
          topic: chosen.topic,
          subTopic: chosen.subTopic,
          year: chosen.year,
          unit: unitKey === "—" ? null : unitKey,
        });
      }
    }

    charts.push({
      unitKey,
      unitRaw: unitKey === "—" ? null : unitKey,
      categories: comparableX,
      data,
    });
  }

  charts.sort((a, b) => {
    if (a.unitKey === "—" && b.unitKey !== "—") return 1;
    if (a.unitKey !== "—" && b.unitKey === "—") return -1;
    return a.unitKey.localeCompare(b.unitKey);
  });

  return charts;
}

export default function IssueComparisonCharts({
  rows,
  selectedCompanies,
  issueLabel,
  charts,
  height = 560,
  minCategoryWidth = 84,
}: {
  rows: CrossExtractedRecord[];
  selectedCompanies: string[];
  issueLabel: string;
  charts?: UnitChart[];
  height?: number;
  minCategoryWidth?: number;
}) {
  const { t } = useT();
  const computedCharts = useMemo(() => getComparableUnitCharts(rows, selectedCompanies), [rows, selectedCompanies]);
  const unitCharts = charts ?? computedCharts;

  // Independent quick switches (NOT linked to the table)
  const metricOptions = useMemo(() => {
    const s = new Set<string>();
    for (const c of unitCharts) {
      for (const d of c.data) if (d.topic) s.add(d.topic);
    }
    return Array.from(s).sort((a, b) => a.localeCompare(b));
  }, [unitCharts]);

  const yearOptions = useMemo(() => {
    const s = new Set<string>();
    for (const c of unitCharts) {
      for (const d of c.data) if (d.year) s.add(d.year);
    }
    // show recent first when year-like
    return Array.from(s).sort((a, b) => b.localeCompare(a));
  }, [unitCharts]);

  const [metricFilter, setMetricFilter] = useState<string | undefined>(undefined);
  const [yearFilter, setYearFilter] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (metricFilter && !metricOptions.includes(metricFilter)) setMetricFilter(undefined);
  }, [metricFilter, metricOptions]);
  useEffect(() => {
    if (yearFilter && !yearOptions.includes(yearFilter)) setYearFilter(undefined);
  }, [yearFilter, yearOptions]);

  if (!unitCharts.length) return null;

  const tabs = unitCharts
    .map((c) => {
      const filteredData = c.data.filter((d) => {
        if (metricFilter && d.topic !== metricFilter) return false;
        if (yearFilter && d.year !== yearFilter) return false;
        return true;
      });

      if (!filteredData.length) return null;

      const xSet = new Set(filteredData.map((d) => d.x));
      const categories = c.categories.filter((x) => xSet.has(x));
      if (!categories.length) return null;

      const enableSlider = categories.length * minCategoryWidth > 520;
      const slider = enableSlider
        ? {
            start: 0,
            end: Math.min(1, 10 / Math.max(10, categories.length)),
          }
        : undefined;

      const palette = [
        "#1677ff",
        "#52c41a",
        "#faad14",
        "#f5222d",
        "#722ed1",
        "#13c2c2",
        "#eb2f96",
        "#2f54eb",
        "#a0d911",
        "#fa541c",
      ];

      const companyOrder = uniq(
        (selectedCompanies.length ? selectedCompanies : filteredData.map((d) => d.companyKey))
          .map((x) => safeTrim(x))
          .filter(Boolean)
      );

      const labelByKey = new Map<string, string>();
      for (const d of filteredData) {
        if (!labelByKey.has(d.companyKey)) labelByKey.set(d.companyKey, d.companyLabel || d.companyKey);
      }

      const colorMap = new Map<string, string>();
      companyOrder.forEach((key, idx) => colorMap.set(key, palette[idx % palette.length]));

      const config: any = {
        data: filteredData,
        autoFit: true,
        height,
        xField: "x",
        yField: "value",
        seriesField: "companyKey",
        colorField: "companyKey",
        isGroup: true,
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
        appendPadding: [8, 8, 0, 8],
        legend: {
          position: "top",
          layout: "horizontal",
          maxRow: 2,
          flipPage: false,
          marker: { symbol: "square" },
          itemName: {
            style: { fontSize: 12 },
            formatter: (text: string) => labelByKey.get(text) || text,
          },
        },
        color: (datum: any) => colorMap.get(datum.companyKey) || palette[0],
        tooltip: {
          // Use per-bar tooltip to avoid null/empty values from shared aggregation.
          shared: false,
          showMarkers: false,
          customItems: (items: any[]) => (items && items.length ? [items[0]] : items),
          domStyles: {
            "g2-tooltip": {
              padding: "8px 10px",
              borderRadius: "12px",
              boxShadow: "0 10px 28px rgba(15,23,42,0.14)",
            },
            "g2-tooltip-title": { fontWeight: 600, marginBottom: "6px" },
          },
          formatter: (datum: any) => {
            const key = safeTrim(datum?.companyKey);
            const name = labelByKey.get(key) || safeTrim(datum?.companyLabel) || key || "—";
            const unitText = c.unitRaw ? ` ${c.unitRaw}` : "";
            const v = (typeof datum?.value === "number") ? datum.value : extractNumber(datum?.value);
            const val = (v === null || !Number.isFinite(v)) ? "—" : formatNumber(v);
            return { name, value: `${val}${unitText}`.trim() };
          },
        },
        xAxis: {
          label: {
            // Keep labels horizontal; wrap when too long
            rotate: 0,
            autoRotate: false,
            autoHide: false,
            autoEllipsis: false,
            style: { fontSize: 11 },
            formatter: (t: any) => wrapAxisLabel(t, 18),
          },
        },
        yAxis: {
          title: c.unitRaw
            ? {
                text: c.unitRaw,
                spacing: 0,
                style: {
                  fontSize: 12,
                  fontWeight: 600,
                  fill: "#334155",
                },
              }
            : undefined,
        },

        axis: {
          x: {
            labelFontSize: 11,
            labelFill: "#64748B",
            labelFormatter: (t: any) => wrapAxisLabel(t, 18),
            // lock horizontal; still allow newline wrapping from formatter
            transform: [{ type: "rotate", optionalAngles: [0], recoverWhenFailed: true }],
          },
          y: {
            ...(c.unitRaw ? { title: c.unitRaw } : {}),
            titleFontSize: 12,
            titleFontWeight: 600,
            titleFill: "#334155",
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
        // ✅ v2.x: ensure tooltip is element-based (not "series"/axis shared)
        interaction: {
          tooltip: { series: false },
        },
        slider,
        columnStyle: { radius: [6, 6, 0, 0] },
        // Force per-element hover/tooltip; avoid axis/region aggregation
        interactions: [{ type: "active-region", enable: false }, { type: "element-active" }, { type: "element-highlight" }],
        meta: {
          x: { alias: t("crossAnalysis.topic") },
          value: { alias: c.unitRaw ? `${t("crossAnalysis.table.value")} (${c.unitRaw})` : t("crossAnalysis.table.value") },
        },
      };

      const chartKey = `${issueLabel}__${c.unitKey}__${companyOrder.join("|")}__${categories.length}__${metricFilter || "ALL"}__${yearFilter || "ALL"}`;

      return {
        key: c.unitKey,
        label: c.unitRaw ? c.unitRaw : t("crossAnalysis.noUnit"),
        children: (
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <ClientOnly>
              <ColumnPlot
                key={chartKey}
                {...config}
                onReady={(plot: any) => {
                  setTimeout(() => {
                    try {
                      plot?.render?.();
                      plot?.forceFit?.();
                    } catch {
                      // no-op
                    }
                  }, 0);
                }}
              />
            </ClientOnly>
          </div>
        ),
      };
    })
    .filter(Boolean) as any[];

  if (!tabs.length) return null;

  return (
    <div className="w-full">
      {(metricOptions.length > 1 || yearOptions.length > 1) ? (
        <div className="mb-3 flex flex-wrap items-center justify-end gap-2">
          <Select
            size="small"
            value={metricFilter}
            allowClear
            style={{ width: 260 }}
            placeholder={t("crossAnalysis.topic")}
            options={metricOptions.map((v) => ({ label: v, value: v }))}
            onChange={(v) => setMetricFilter(v)}
          />
          <Select
            size="small"
            value={yearFilter}
            allowClear
            style={{ width: 140 }}
            placeholder={t("crossAnalysis.year")}
            options={yearOptions.map((v) => ({ label: v, value: v }))}
            onChange={(v) => setYearFilter(v)}
          />
        </div>
      ) : null}

      {tabs.length === 1 ? (
        tabs[0].children
      ) : (
        <Tabs
          size="small"
          items={tabs as any}
          tabBarGutter={8}
          className="[&_.ant-tabs-nav]:mb-2"
        />
      )}
    </div>
  );
}
