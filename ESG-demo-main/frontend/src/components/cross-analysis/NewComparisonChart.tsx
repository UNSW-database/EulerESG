"use client";

import { useMemo, useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { useT } from "@/i18n/useT";

const ColumnPlot = dynamic(
  () => import("@ant-design/plots/es/components/column"),
  { ssr: false },
);

interface NewComparisonChartProps {
  data: any[];
}

// 颜色数组，用于不同报告（蓝色、橙色、红色、紫色、粉色、青色）
const COLORS = ["#3B82F6", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#06B6D4"];

function wrapAxisLabel(text: any, maxLen = 14): string {
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


function ClientOnly({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return <>{children}</>;
}

export function NewComparisonChart({ data }: NewComparisonChartProps) {
  const { t } = useT();
  const UNIT_VARIES_TOOLTIP = t("analysis.unitVariesTooltip");

  // 从数据中提取所有报告名称（排除 category），并排序以确保顺序一致
  const reportNames = useMemo(() => {
    if (!data || data.length === 0) return [];
    const names = new Set<string>();
    data.forEach((item) => {
      Object.keys(item).forEach((key) => {
        if (key !== "category" && key !== "unit") {
          names.add(key);
        }
      });
    });
    return Array.from(names).sort();
  }, [data]);

  // 转换为 Ant Design Plots 需要的格式
  // 确保报告顺序与 reportNames 排序后的顺序一致
  const plotData = useMemo(() => {
    if (!data || data.length === 0) return [];
    const result: any[] = [];
    const sortedNames = [...reportNames].sort();
    data.forEach((item) => {
      sortedNames.forEach((reportName) => {
        const rawValue = item[reportName];
        let numericValue: number;
        
        if (rawValue === null || rawValue === undefined || rawValue === 'null' || rawValue === '') {
          numericValue = 0;
        } else if (typeof rawValue === 'number') {
          numericValue = rawValue;
        } else if (typeof rawValue === 'string') {
          // 移除逗号等格式字符
          const cleaned = rawValue.replace(/,/g, '').trim();
          numericValue = parseFloat(cleaned) || 0;
        } else {
          numericValue = 0;
        }
        
        // 确保 value 是有效的数字
        if (isNaN(numericValue) || !isFinite(numericValue)) {
          numericValue = 0;
        }
        
        // 确保 value 不为 0 时才添加（或者即使为 0 也添加，但确保是数字）
        result.push({
          category: item.category,
          report: reportName,
          value: numericValue,
          unit: (item as any).unit ?? null,
          // 确保 value 字段是数字类型，不是 null
          ...(numericValue === 0 ? {} : {}), // 保持原样
        });
      });
    });
    
    return result;
  }, [data, reportNames]);

  const config = useMemo(() => {
    // 对报告名称进行排序以确保顺序一致
    const sortedReportNames = [...reportNames].sort();
    
    // 为每个报告分配颜色：第一个报告使用绿色，其他使用默认颜色
    // 创建颜色数组，顺序与排序后的报告名称一致
    const colorArray: string[] = [];
    sortedReportNames.forEach((reportName, index) => {
      if (index === 0) {
        // 第一个报告使用绿色
        colorArray.push("#10B981");
      } else {
        // 其他报告使用默认颜色数组（从蓝色开始）
        const colorIndex = (index - 1) % COLORS.length;
        colorArray.push(COLORS[colorIndex]);
      }
    });

    // 创建颜色映射对象，用于函数方式分配颜色
    const colorMap: Record<string, string> = {};
    sortedReportNames.forEach((reportName, index) => {
      colorMap[reportName] = colorArray[index];
    });

    // 获取 plotData 中 report 字段的唯一值顺序
    const uniqueReportsInData = Array.from(new Set(plotData.map((d: any) => d.report)));

    // 根据 plotData 中的顺序创建颜色数组
    const finalColorArray = uniqueReportsInData.map((reportName: string) => {
      return colorMap[reportName] || COLORS[0];
    });

    // y-axis unit (show a single unit if consistent; otherwise indicate mixed units)
    const uniqUnits = Array.from(new Set(plotData.map((d: any) => d.unit).filter((u: any) => u)));
    const yUnit = uniqUnits.length === 1 ? String(uniqUnits[0]) : (uniqUnits.length > 1 ? UNIT_VARIES_TOOLTIP : "");


    return {
      data: plotData,

      // ✅ v2.x axis configuration (xAxis/yAxis is ignored in some versions of @ant-design/plots 2.x)
      axis: {
        x: {
          labelFill: "#64748B",
          labelFontSize: 12,
          labelFormatter: (t: any) => wrapAxisLabel(t, 16),
          transform: [{ type: "rotate", optionalAngles: [0], recoverWhenFailed: true }],
        },
        y: {
          ...(yUnit ? { title: yUnit } : {}),
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
      // ✅ v2.x: force tooltip to be per-element instead of per-x "series" aggregation
      interaction: {
        tooltip: { series: false },
        elementHighlight: true,
      },
      xField: "category",
      yField: "value",
      seriesField: "report",
      colorField: "report",
      isGroup: true,
      color: finalColorArray,
      columnStyle: {
        radius: [8, 8, 0, 0],
      },
      xAxis: {
        label: {
          style: {
            fill: "#64748B",
            fontSize: 12,
          },
          // keep labels horizontal; wrap when too long
          rotate: 0,
          autoRotate: false,
          autoHide: false,
          autoEllipsis: false,
          formatter: (text: any) => wrapAxisLabel(text, 16),
        },
        line: {
          style: {
            stroke: "#E2E8F0",
          },
        },
        tickLine: false,
      },
      yAxis: {
        title: yUnit ? {
          text: yUnit,
          style: {
            fill: "#64748B",
            fontSize: 12,
          },
        } : undefined,
        label: {
          style: {
            fill: "#64748B",
            fontSize: 12,
          },
          formatter: (value: number) => {
            if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
            if (value >= 1000) return `${(value / 1000).toFixed(0)}K`;
            return String(value);
          },
        },
        line: {
          style: {
            stroke: "#E2E8F0",
          },
        },
        tickLine: false,
        grid: {
          line: {
            style: {
              stroke: "#E2E8F0",
              lineDash: [3, 3],
            },
          },
        },
      },
      tooltip: {
        // show tooltip for the hovered bar only (not the whole category)
        shared: false,
        showMarkers: false,
        customItems: (items: any[]) => (items && items.length ? [items[0]] : items),
        customContent: (title: string, items: any[]) => {
          if (!items || items.length === 0) return null;

          const it: any = items[0];

          // In different versions of @ant-design/plots / G2, the original datum may live in different places.
          const datum: any =
            it?.data?.data ??
            it?.data ??
            it?.datum ??
            it?.mappingData?._origin ??
            it?.mappingData?.originalData ??
            it?.originalData ??
            {};

          const category = String(datum?.category ?? title ?? "");
          const report = String(datum?.report ?? it?.name ?? it?.title ?? "");

          // Hard fallback: look up the exact bar from the plotData we feed into the chart.
          const fallbackRow: any =
            plotData.find((p: any) => String(p?.category) === category && String(p?.report) === report) || null;

          const unitRaw = (datum?.unit ?? fallbackRow?.unit ?? "") as any;
          const unit = unitRaw ? String(unitRaw) : "";

          const parseNumberOrNull = (v: any): number | null => {
            if (v === null || v === undefined) return null;
            if (typeof v === "number") return Number.isFinite(v) ? v : null;
            const s = String(v).trim();
            if (!s || s.toLowerCase() === "null" || s.toLowerCase() === "nan") return null;
            const m = s.replace(/,/g, "").match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/i);
            if (!m) return null;
            const n = Number(m[0]);
            return Number.isFinite(n) ? n : null;
          };

          const candidates = [datum?.value, it?.value, fallbackRow?.value];
          let numVal: number | null = null;
          for (const c of candidates) {
            const n = parseNumberOrNull(c);
            if (n !== null) {
              numVal = n;
              break;
            }
          }

          const valueText = (numVal !== null)
            ? numVal.toLocaleString()
            : "—";

          const escapeHtml = (text: string) =>
            String(text)
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#039;");

          const unitSuffix =
            unit && unit !== "null" && unit !== "Multiple units" && unit !== UNIT_VARIES_TOOLTIP
              ? ` ${escapeHtml(unit)}`
              : "";

          return `
            <div style="font-size:12px; color:#0F172A;">
              <div style="font-weight:600; margin-bottom:6px;">${escapeHtml(category)}</div>
              <div style="margin-bottom:4px;">
                <span style="color:#64748B;">${escapeHtml(t("crossAnalysis.table.report"))}:</span> ${escapeHtml(report)}
              </div>
              <div>
                <span style="color:#64748B;">${escapeHtml(t("analysis.columns.value"))}:</span> ${escapeHtml(valueText)}${unitSuffix}
              </div>
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
      interactions: [{ type: "active-region", enable: false }, { type: "element-active" }, { type: "element-highlight" }],

      legend: {
        position: "top" as const,
        itemName: {
          style: {
            fill: "#0F172A",
            fontSize: 12,
          },
        },
      },
      height: 520,
    };
  }, [plotData, reportNames]);

  if (!data || data.length === 0) {
    return (
      <div className="bg-white rounded-2xl shadow-sm p-6 text-center text-[#64748B]">
        {t("crossAnalysis.noComparableChartData")}
      </div>
    );
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm p-6 w-full">
      <ClientOnly>
        <ColumnPlot {...(config as any)} />
      </ClientOnly>
    </div>
  );
}
