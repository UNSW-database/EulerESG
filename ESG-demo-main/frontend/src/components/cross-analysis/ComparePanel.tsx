"use client";

import React, { useMemo, useState } from "react";
import { Empty, Skeleton } from "antd";
import Bar from "@ant-design/plots/es/components/bar";
import type { CrossCompareResponse, CrossMetricValue } from "@/lib/api";
import { ChevronDown } from "lucide-react";
import { useT } from "@/i18n/useT";


function stripFileExt(name: string | null | undefined): string {
  const s = (name ?? "").toString().trim();
  if (!s) return "";
  return s.replace(/\.[^/.]+$/, "");
}

function extractNumber(v?: string | null): number | null {
  if (!v) return null;
  const m = String(v).replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  if (!m) return null;
  const n = Number(m[0]);
  return Number.isFinite(n) ? n : null;
}

function pickBestMetric(results: { meta: any; metrics: CrossMetricValue[] }[]) {
  const counts: Record<string, number> = {};
  for (const r of results) {
    for (const m of r.metrics) {
      const num = extractNumber(m.value as any);
      if (num === null) continue;
      counts[m.name] = (counts[m.name] || 0) + 1;
    }
  }
  let best = "";
  let bestCount = 0;
  for (const [k, c] of Object.entries(counts)) {
    if (c > bestCount) {
      best = k;
      bestCount = c;
    }
  }
  return bestCount >= 2 ? best : null;
}

export default function ComparePanel({
  loading,
  compare,
}: {
  loading: boolean;
  compare: CrossCompareResponse | null;
}) {
  const { t } = useT();
  const [showEvidence, setShowEvidence] = useState<Record<string, boolean>>({});

  const bestMetricName = useMemo(() => {
    if (!compare?.results?.length) return null;
    return pickBestMetric(compare.results as any);
  }, [compare]);

  const chartData = useMemo(() => {
    if (!bestMetricName || !compare?.results) return [];
    const rows: { report: string; value: number }[] = [];
    for (const r of compare.results) {
      const metric = r.metrics.find((m) => m.name === bestMetricName);
      const num = extractNumber(metric?.value ?? null);
      if (num === null) continue;
      rows.push({ report: stripFileExt(r.meta.filename || r.meta.file_id), value: num });
    }
    return rows;
  }, [bestMetricName, compare]);

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton active paragraph={{ rows: 4 }} />
        <Skeleton active paragraph={{ rows: 6 }} />
      </div>
    );
  }

  if (!compare || !compare.results?.length) {
    return <Empty description={t("crossAnalysis.noComparableItems")} />;
  }

  return (
    <div className="space-y-6">
      {bestMetricName && chartData.length >= 2 ? (
        <div className="rounded-2xl border border-slate-200 bg-white/60 p-4 shadow-sm backdrop-blur">
          <div className="flex items-baseline justify-between gap-3">
            <div className="text-sm font-semibold text-slate-900">{t("crossAnalysis.compare.quantAlignmentTitle")}</div>
            <div className="text-xs text-slate-500">
              {t("crossAnalysis.compare.metricLabel")}: <span className="font-medium">{bestMetricName}</span>
            </div>
          </div>
          <div className="mt-3">
            <Bar
              data={chartData}
              xField="value"
              yField="report"
              legend={false as any}
              height={Math.max(220, chartData.length * 36)}
              label={{ position: "middle" as any }}
              tooltip={{ shared: false, showMarkers: false, customItems: (items: any[]) => (items && items.length ? [items[0]] : items) } as any}
              style={{ radius: [8, 8, 8, 8] } as any}
            />
          </div>
          <div className="mt-2 text-xs text-slate-500">
            {t("crossAnalysis.compare.valuesParsedHint")}
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {compare.results.map((r) => {
          const key = r.meta.file_id;
          const opened = !!showEvidence[key];
          return (
            <div key={key} className="rounded-2xl border border-slate-200 bg-white/60 p-4 shadow-sm backdrop-blur">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-slate-900">
                    {r.meta.filename || r.meta.file_id}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {r.meta.framework ? `${r.meta.framework}` : ""}
                    {r.meta.industry ? ` · ${r.meta.industry}` : ""}
                  </div>
                </div>
              </div>

              <div className="mt-3 text-sm text-slate-700">
                <div className="text-xs font-medium text-slate-500">{t("crossAnalysis.compare.minimalSummary")}</div>
                <div className="mt-1 leading-relaxed">{r.summary}</div>
              </div>

              <div className="mt-4">
                <div className="text-xs font-medium text-slate-500">{t("crossAnalysis.compare.extractedItems")}</div>
                <ul className="mt-2 space-y-2">
                  {r.metrics.slice(0, 8).map((m, idx) => (
                    <li key={`${m.name}-${idx}`} className="flex items-start justify-between gap-3 text-xs">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium text-slate-700">{m.name}</div>
                        {m.page ? <div className="text-slate-400">{t("crossAnalysis.compare.pageShort", { page: m.page })}</div> : null}
                      </div>
                      <div className="shrink-0 text-right text-slate-700">
                        <div className="font-medium">{m.value ?? "—"}</div>
                        {m.unit ? <div className="text-slate-400">{m.unit}</div> : null}
                      </div>
                    </li>
                  ))}
                </ul>
                {r.metrics.length > 8 ? (
                  <div className="mt-2 text-xs text-slate-400">{t("crossAnalysis.compare.showingTopItems", { shown: 8, total: r.metrics.length })}</div>
                ) : null}
              </div>

              {r.evidence?.length ? (
                <button
                  onClick={() => setShowEvidence((prev) => ({ ...prev, [key]: !prev[key] }))}
                  className="mt-4 flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white/50 px-3 py-2 text-xs text-slate-700 hover:bg-white"
                >
                  <span className="font-medium">{t("crossAnalysis.compare.evidenceSnippets")}</span>
                  <ChevronDown size={16} className={opened ? "rotate-180 transition" : "transition"} />
                </button>
              ) : null}

              {opened && r.evidence?.length ? (
                <div className="mt-3 space-y-2">
                  {r.evidence.map((e, i) => (
                    <div key={i} className="rounded-xl border border-slate-200 bg-white/60 p-3 text-xs text-slate-700">
                      <div className="mb-1 text-slate-400">
                        {e.segment_id ? e.segment_id : t("crossAnalysis.compare.snippetFallback")}
                        {e.page_number ? ` · ${t("crossAnalysis.compare.pageShort", { page: e.page_number })}` : ""}
                      </div>
                      <div className="leading-relaxed">{e.content}</div>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
