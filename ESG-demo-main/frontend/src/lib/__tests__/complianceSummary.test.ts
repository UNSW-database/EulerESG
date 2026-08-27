import { describe, expect, it } from "vitest";

import { DICT } from "@/i18n/dict";

import {
  buildComplianceSummary,
  createComplianceSummaryMarkdown,
  disclosurePercentage,
  normalizeDisclosureStatus,
  type ComplianceSummaryMetric,
} from "../complianceSummary";

const metric = (
  id: string,
  status: ComplianceSummaryMetric["disclosure_status"],
  overrides: Partial<ComplianceSummaryMetric> = {},
): ComplianceSummaryMetric => ({
  metric_id: id,
  metric_code: id,
  metric_name: `Metric ${id}`,
  disclosure_status: status,
  ...overrides,
});

describe("compliance summary", () => {
  it("groups strong disclosures and orders missing metrics before partial metrics", () => {
    const full = metric("A", "fully_disclosed");
    const partial = metric("B", "partially_disclosed");
    const missing = metric("C", "not_disclosed");

    const summary = buildComplianceSummary([full, partial, missing]);

    expect(summary.total).toBe(3);
    expect(summary.wellDisclosed).toEqual([full]);
    expect(summary.needsImprovement).toEqual([missing, partial]);
  });

  it("keeps distinct metric components that share a framework code", () => {
    const first = metric("component-1", "fully_disclosed", { metric_code: "TC-SI-1" });
    const second = metric("component-2", "not_disclosed", { metric_code: "TC-SI-1" });

    const summary = buildComplianceSummary([first, second]);

    expect(summary.total).toBe(2);
    expect(summary.wellDisclosed).toHaveLength(1);
    expect(summary.needsImprovement).toHaveLength(1);
  });

  it("creates a downloadable report without inventing a missing page", () => {
    const summary = buildComplianceSummary([
      metric("TC-SI-330a.2", "fully_disclosed", {
        metric_name: "Employee engagement as a percentage",
        value: 87,
        unit: "%",
        page: 108,
        reasoning: "The report explicitly discloses employee engagement at 87%.",
      }),
      metric("TC-SI-550a.1", "not_disclosed", { page: null }),
    ]);

    const markdown = createComplianceSummaryMarkdown(summary, {
      reportName: "Example ESG Report",
      lang: "en",
    });

    expect(markdown).toContain("Employee engagement as a percentage");
    expect(markdown).toContain("Value: 87 %");
    expect(markdown).toContain("Page: 108");
    expect(markdown).not.toContain("Page: null");
    expect(markdown).toContain("- Disclosed: 1 (50.0%)");
    expect(markdown).toContain("- Partially Disclosed: 0 (0.0%)");
    expect(markdown).toContain("## Disclosed metrics");
    expect(markdown).not.toContain("Well disclosed");
    expect(markdown).toContain("Metrics requiring improvement");
  });

  it("handles an empty assessment and percentage safely", () => {
    expect(buildComplianceSummary([]).total).toBe(0);
    expect(disclosurePercentage(0, 0)).toBe("0.0");
  });

  it("does not classify legacy 'not fully disclosed' text as fully disclosed", () => {
    expect(normalizeDisclosureStatus("not fully disclosed")).toBe("not_disclosed");
    expect(normalizeDisclosureStatus("Disclosed But Not Clear")).toBe("partially_disclosed");
    expect(normalizeDisclosureStatus("partially disclosed")).toBe("partially_disclosed");
    expect(normalizeDisclosureStatus("fully disclosed")).toBe("fully_disclosed");
  });

  it("uses unified display labels without changing canonical statuses", () => {
    expect(DICT.en.analysis.status.fully).toBe("Disclosed");
    expect(DICT.en.analysis.status.partial).toBe("Partially Disclosed");
    expect(DICT.en.analysis.summary.partial).toBe("Partially Disclosed");
    expect(DICT.zh.analysis.status.fully).toBe("已披露");
    expect(DICT.zh.analysis.status.partial).toBe("部分披露");
  });
});
