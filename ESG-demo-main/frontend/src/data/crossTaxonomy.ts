export type CrossIssue = {
  /** backend issue_key */
  key: string;
  label_en: string;
  label_zh: string;
  /** short hint shown in UI */
  hint: string;
};

export type CrossDimension = {
  /** backend topic_key */
  key: string;
  label_en: string;
  label_zh: string;
  /** short subtitle shown in UI */
  intro: string;
  issues: CrossIssue[];
};

/**
 * Cross Analysis (beta) navigation taxonomy.
 * IMPORTANT:
 * - Keys must match backend catalog.
 * - Labels must match the in-code taxonomy wording (bilingual) to keep UI consistent.
 */

import { CROSS_TAXONOMY as FULL_TAXONOMY } from "@/features/crossAnalysis/taxonomy";

export const CROSS_TAXONOMY: CrossDimension[] = FULL_TAXONOMY.map((d) => {
  const issues: CrossIssue[] = (d.issues || []).map((i) => {
    const metricHints = (i.metrics || [])
      .map((m) => m.labelEn)
      .filter(Boolean)
      .slice(0, 4);
    return {
      key: i.key,
      label_en: i.labelEn,
      label_zh: i.labelZh,
      hint: metricHints.length ? metricHints.join(", ") : "",
    };
  });

  return {
    key: d.key,
    label_en: d.labelEn,
    label_zh: d.labelZh,
    intro: "",
    issues,
  };
});

export function getDimension(key: string): CrossDimension | undefined {
  return CROSS_TAXONOMY.find((d) => d.key === key);
}

export function getDefaultDimensionKey(): string {
  return CROSS_TAXONOMY[0].key;
}
