export type DisclosureStatus =
  | "fully_disclosed"
  | "partially_disclosed"
  | "not_disclosed";

export interface ComplianceSummaryMetric {
  metric_id: string;
  metric_code?: string;
  metric_name: string;
  disclosure_status: DisclosureStatus;
  reasoning?: string;
  value?: string | number | null;
  unit?: string;
  page?: string | number | null;
}

export interface ComplianceSummary {
  total: number;
  fullyDisclosed: ComplianceSummaryMetric[];
  partiallyDisclosed: ComplianceSummaryMetric[];
  notDisclosed: ComplianceSummaryMetric[];
  wellDisclosed: ComplianceSummaryMetric[];
  needsImprovement: ComplianceSummaryMetric[];
}

export const normalizeDisclosureStatus = (value: unknown): DisclosureStatus => {
  const status = String(value ?? "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (status === "fully_disclosed") return "fully_disclosed";
  if (status === "partially_disclosed") return "partially_disclosed";
  if (status === "not_disclosed") return "not_disclosed";
  if (status.includes("not_clear") || status.includes("unclear")) {
    return "partially_disclosed";
  }
  if (status.includes("not")) return "not_disclosed";
  if (status.includes("partial")) return "partially_disclosed";
  if (status.includes("fully") || status === "disclosed" || status === "complete") {
    return "fully_disclosed";
  }
  return "not_disclosed";
};

export const buildComplianceSummary = (
  metrics: readonly ComplianceSummaryMetric[],
): ComplianceSummary => {
  const fullyDisclosed = metrics.filter(
    (metric) => metric.disclosure_status === "fully_disclosed",
  );
  const partiallyDisclosed = metrics.filter(
    (metric) => metric.disclosure_status === "partially_disclosed",
  );
  const notDisclosed = metrics.filter(
    (metric) => metric.disclosure_status === "not_disclosed",
  );

  return {
    total: metrics.length,
    fullyDisclosed,
    partiallyDisclosed,
    notDisclosed,
    wellDisclosed: fullyDisclosed,
    // Missing disclosures are shown before partial disclosures so the highest
    // priority gaps are visible first. Do not deduplicate by metric code:
    // frameworks can legitimately define multiple components under one code.
    needsImprovement: [...notDisclosed, ...partiallyDisclosed],
  };
};

export const disclosurePercentage = (count: number, total: number): string => {
  if (total <= 0) return "0.0";
  return ((count / total) * 100).toFixed(1);
};

const displayValue = (metric: ComplianceSummaryMetric): string => {
  if (metric.value === null || metric.value === undefined) return "";
  const value = String(metric.value).trim();
  if (!value || ["n/a", "na", "null", "none", "-"].includes(value.toLowerCase())) return "";
  return `${value}${metric.unit ? ` ${metric.unit}` : ""}`;
};

const metricLabel = (metric: ComplianceSummaryMetric) =>
  `${metric.metric_code || metric.metric_id} — ${metric.metric_name}`;

export const createComplianceSummaryMarkdown = (
  summary: ComplianceSummary,
  options: { reportName?: string; lang?: "en" | "zh" } = {},
): string => {
  const zh = options.lang === "zh";
  const lines = [
    `# ${zh ? "披露摘要报告" : "Disclosure Summary Report"}`,
    "",
  ];

  if (options.reportName) {
    lines.push(`**${zh ? "报告" : "Report"}:** ${options.reportName}`, "");
  }

  lines.push(
    `## ${zh ? "概览" : "Overview"}`,
    "",
    `- ${zh ? "已评估指标" : "Metrics assessed"}: ${summary.total}`,
    `- ${zh ? "已披露" : "Disclosed"}: ${summary.wellDisclosed.length} (${disclosurePercentage(summary.wellDisclosed.length, summary.total)}%)`,
    `- ${zh ? "部分披露" : "Partially Disclosed"}: ${summary.partiallyDisclosed.length} (${disclosurePercentage(summary.partiallyDisclosed.length, summary.total)}%)`,
    `- ${zh ? "未披露" : "Not disclosed"}: ${summary.notDisclosed.length} (${disclosurePercentage(summary.notDisclosed.length, summary.total)}%)`,
    "",
  );

  const appendMetrics = (
    title: string,
    metrics: ComplianceSummaryMetric[],
  ) => {
    lines.push(`## ${title}`, "");
    if (!metrics.length) {
      lines.push(zh ? "无。" : "None.", "");
      return;
    }

    metrics.forEach((metric, index) => {
      lines.push(`${index + 1}. **${metricLabel(metric)}**`);
      const value = displayValue(metric);
      if (value) lines.push(`   - ${zh ? "数值" : "Value"}: ${value}`);
      if (metric.page !== null && metric.page !== undefined && String(metric.page).trim()) {
        lines.push(`   - ${zh ? "页码" : "Page"}: ${metric.page}`);
      }
      if (metric.reasoning?.trim()) {
        lines.push(`   - ${zh ? "分析说明" : "Assessment"}: ${metric.reasoning.trim()}`);
      }
      lines.push("");
    });
  };

  appendMetrics(
    zh ? "已披露指标" : "Disclosed metrics",
    summary.wellDisclosed,
  );
  appendMetrics(
    zh ? "披露不足的指标" : "Metrics requiring improvement",
    summary.needsImprovement,
  );

  return `${lines.join("\n").trimEnd()}\n`;
};
