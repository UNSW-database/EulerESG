"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Card, Empty, Select, Tabs } from "antd";
import { useT } from "@/i18n/useT";
import Column from "@ant-design/plots/es/components/column";
import type { AllRecord } from "@/features/crossAnalysis/types";

function extractNumber(v: string): number | null {
  if (!v) return null;
  const m = String(v).replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  if (!m) return null;
  const n = Number(m[0]);
  return Number.isFinite(n) ? n : null;
}

type ReportMeta = { id: string; name: string };

function stableColorForKey(key: string): string {
  // Deterministic HSL palette -> hex-ish string supported by G2Plot.
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 360;
  return `hsl(${h}, 70%, 45%)`;
}

function wrapAxisLabel(text: any, maxLen = 24): string {
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


export default function RecordsComparisonChart({
  records,
  selectedReportIds,
}: {
  records: AllRecord[];
  selectedReportIds: string[];
}) {
  const { t } = useT();
  const reportMeta = useMemo<ReportMeta[]>(() => {
    const seen = new Map<string, string>();
    for (const r of records) {
      if (!selectedReportIds.includes(r.id)) continue;
      if (!seen.has(r.id)) seen.set(r.id, r.name || r.id);
    }
    return Array.from(seen.entries()).map(([id, name]) => ({ id, name }));
  }, [records, selectedReportIds]);

  const years = useMemo(() => {
    const s = new Set<string>();
    for (const r of records) {
      if (!selectedReportIds.includes(r.id)) continue;
      if (r.year) s.add(String(r.year));
    }
    return Array.from(s).sort((a, b) => b.localeCompare(a));
  }, [records, selectedReportIds]);

  const [year, setYear] = useState<string | undefined>(() => years[0]);

  useEffect(() => {
    if (!years.length) return;
    if (!year || !years.includes(year)) setYear(years[0]);
  }, [years.join("||"), year]);

  // metricKey groups comparable items: Topic + Sub-topic + Unit
  const metricGroups = useMemo(() => {
    const y = year || years[0] || null;
    const m = new Map<string, { topic: string; sub: string; unit: string | null; byReport: Map<string, number> }>();

    for (const r of records) {
      if (!selectedReportIds.includes(r.id)) continue;
      if (y && String(r.year || "") !== String(y)) continue;
      const num = extractNumber(String(r.data || ""));
      if (num === null) continue;
      const key = `${r.topic}||${r.subTopic}||${r.unit || ""}`;
      if (!m.has(key)) {
        m.set(key, { topic: r.topic, sub: r.subTopic, unit: r.unit || null, byReport: new Map() });
      }
      const g = m.get(key)!;
      // If multiple rows per report for the same metricKey, keep the first non-null number deterministically.
      if (!g.byReport.has(r.id)) g.byReport.set(r.id, num);
    }

    // Only keep metricKey where every selected report has a value.
    const out: { topic: string; sub: string; unit: string | null; byReport: Map<string, number> }[] = [];
    for (const g of m.values()) {
      let ok = true;
      for (const rid of selectedReportIds) {
        if (!g.byReport.has(rid)) {
          ok = false;
          break;
        }
      }
      if (ok) out.push(g);
    }

    // Stable sort by Topic, Sub-topic
    out.sort((a, b) => {
      const ka = `${a.topic} ${a.sub}`.trim();
      const kb = `${b.topic} ${b.sub}`.trim();
      return ka.localeCompare(kb);
    });
    return out;
  }, [records, selectedReportIds, year, years]);

  const units = useMemo(() => {
    const s = new Set<string>();
    for (const g of metricGroups) {
      s.add(g.unit || "—");
    }
    return Array.from(s).sort((a, b) => a.localeCompare(b));
  }, [metricGroups]);

  const [activeUnit, setActiveUnit] = useState<string>(() => units[0] || "—");

  useEffect(() => {
    if (!units.length) return;
    if (!units.includes(activeUnit)) setActiveUnit(units[0]);
  }, [units.join("||"), activeUnit]);

  const unitTabs = useMemo(() => {
    return units.map((u) => ({ key: u, label: u === "—" ? t("crossAnalysis.noUnit") : u }));
  }, [units, t]);

  const chartData = useMemo(() => {
    const uKey = activeUnit || units[0] || "—";
    const rows: { metric: string; report: string; reportId: string; value: number }[] = [];
    const nameById = new Map(reportMeta.map((x) => [x.id, x.name]));

    for (const g of metricGroups) {
      const unitKey = g.unit || "—";
      if (unitKey !== uKey) continue;
      const metricLabel = g.sub ? `${g.topic} · ${g.sub}` : g.topic;
      for (const rid of selectedReportIds) {
        const v = g.byReport.get(rid);
        if (v === undefined) continue;
        rows.push({ metric: metricLabel, report: nameById.get(rid) || rid, reportId: rid, value: v });
      }
    }
    return rows;
  }, [metricGroups, selectedReportIds, reportMeta, activeUnit, units]);

  const unitForAxis = activeUnit && activeUnit !== "—" ? activeUnit : null;

  if (selectedReportIds.length < 2) {
    return (
      <Card className="rounded-2xl" styles={{ body: { padding: 14 } }}>
        <Empty description={t("files.selectAtLeastTwoReports")} />
      </Card>
    );
  }

  return (
    <Card className="rounded-2xl" styles={{ body: { padding: 14 } }}>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="text-sm font-semibold text-slate-900">{t("crossAnalysis.comparisonChartTitle")}</div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-xs text-slate-500">{t("crossAnalysis.table.year")}</div>
          <Select
            size="small"
            value={year}
            onChange={(v) => setYear(v)}
            style={{ minWidth: 120 }}
            options={years.map((y) => ({ value: y, label: y }))}
            placeholder={t("crossAnalysis.table.year")}
          />
        </div>
      </div>

      {!metricGroups.length ? (
        <div className="mt-4">
          <Empty description={t("crossAnalysis.noComparableNumericForYear")} />
        </div>
      ) : (
        <div className="mt-3">
          <Tabs
            size="small"
            items={unitTabs}
            activeKey={activeUnit}
            onChange={(k) => setActiveUnit(k)}
          />
          {!chartData.length ? (
            <Empty description={t("crossAnalysis.noComparableForUnit")} />
          ) : (
            <Column
              data={chartData}
              xField="metric"
              yField="value"
              seriesField="report"
              isGroup
              legend={{ position: "top" } as any}
              xAxis={{
                label: {
                  rotate: 0,
                  autoRotate: false,
                  autoHide: false,
                  autoEllipsis: false,
                  formatter: (txt: any) => wrapAxisLabel(txt, 28),
                },
              } as any}
              height={420}
              color={(datum: any) => stableColorForKey(String(datum?.reportId || datum?.report || ""))}
              yAxis={{
                title: unitForAxis
                  ? { text: unitForAxis, style: { fontSize: 12 } }
                  : undefined,
              } as any}
              interactions={[{ type: "active-region", enable: false }, { type: "element-active" }, { type: "element-highlight" }] as any}
              state={{
                active: {
                  style: {
                    lineWidth: 2,
                    stroke: "#111827",
                    shadowBlur: 14,
                    shadowColor: "rgba(0,0,0,0.25)",
                  },
                },
                inactive: { style: { opacity: 0.35 } },
              } as any}
              columnStyle={{ radius: [6, 6, 0, 0] } as any}
              tooltip={{
                shared: false,
                showMarkers: false,
                customItems: (items: any[]) => (items && items.length ? [items[0]] : items),
                formatter: (d: any) => ({
                  name: d.report,
                  value: `${d.value}${activeUnit && activeUnit !== "—" ? " " + activeUnit : ""}`,
                }),
              } as any}
            />
          )}
        </div>
      )}
    </Card>
  );
}
