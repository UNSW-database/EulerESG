"use client";

import React, { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Card, Empty } from "antd";
import { useT } from "@/i18n/useT";

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

function stableColorForKey(key: string): string {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 360;
  return `hsl(${h}, 70%, 45%)`;
}

function wrapAxisLabel(text: any, maxLen = 16): string {
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
  for (let i = 0; i < chars.length; i += maxLen) lines.push(chars.slice(i, i + maxLen).join(""));
  return lines.join("\n");
}

export type TopicChartPoint = {
  reportId: string;
  reportLabel: string;
  value: number;
};

export type TopicChartSpec = {
  topic: string;        // use JSON Topic as the indicator name
  unit: string | null;
  points: TopicChartPoint[];
};

export default function TopicComparisonCharts({
  charts,
  height = 320,
}: {
  charts: TopicChartSpec[];
  height?: number;
}) {
  const { t } = useT();
  const rows = useMemo(() => {
    const out: TopicChartSpec[][] = [];
    for (let i = 0; i < charts.length; i += 4) out.push(charts.slice(i, i + 4));
    return out;
  }, [charts]);

  if (!charts.length) {
    return (
      <Card className="rounded-2xl" styles={{ body: { padding: 16 } }}>
        <Empty description={t("crossAnalysis.noComparableChartData")} />
      </Card>
    );
  }

  return (
    <div className="space-y-4 w-full">
      {rows.map((row, idx) => (
        <div
          key={`row_${idx}`}
          className="grid gap-4 w-full"
          style={{ gridTemplateColumns: `repeat(${row.length}, minmax(0, 1fr))` }}
        >
          {row.map((c) => {
            const data = c.points.map((p) => ({
              companyKey: p.reportId,
              company: p.reportLabel,
              value: p.value,
            }));

            const config: any = {
              data,
              xField: "company",
              yField: "value",
              colorField: "companyKey",
              color: (d: any) => stableColorForKey(String(d.companyKey || "")),
              legend: false,
              height,
              autoFit: true,
              axis: {
                x: {
                  labelFontSize: 11,
                  labelFill: "#64748B",
                  labelFormatter: (t: any) => wrapAxisLabel(t, 14),
                  transform: [{ type: "rotate", optionalAngles: [0], recoverWhenFailed: true }],
                },
                y: {
                  ...(c.unit ? { title: c.unit } : {}),
                  titleFill: "#64748B",
                  titleFontSize: 11,
                  labelFontSize: 11,
                  labelFill: "#64748B",
                  labelFormatter: (v: any) => {
                    const n = Number(v);
                    if (!Number.isFinite(n)) return String(v);
                    return n.toLocaleString();
                  },
                },
              },
              tooltip: {
                title: c.topic,
                formatter: (d: any) => {
                  const n = Number(d?.value);
                  const val = Number.isFinite(n) ? n.toLocaleString() : String(d?.value ?? "");
                  return {
                    name: String(d?.company ?? t("crossAnalysis.company")),
                    value: c.unit ? `${val} ${c.unit}` : val,
                  };
                },
              },
              // Keep things stable in narrow columns (avoid forced internal scrolling)
              interactions: [{ type: "element-highlight" }],
            };

            return (
              <Card
                key={`${c.topic}__${c.unit || ""}`}
                className="rounded-2xl min-w-0"
                styles={{ body: { padding: 14 } }}
              >
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-slate-900 break-words">
                    {c.topic}
                  </div>
                  {c.unit ? (
                    <div className="text-xs text-slate-500 mt-0.5 break-words">{c.unit}</div>
                  ) : null}
                </div>

                <div className="mt-3 w-full">
                  <ClientOnly>
                    <ColumnPlot {...config} />
                  </ClientOnly>
                </div>
              </Card>
            );
          })}
        </div>
      ))}
    </div>
  );
}
