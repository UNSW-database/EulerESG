"use client";

import type { FrameworkSelectionValues } from "@/components/cross-analysis/FrameworkSelectModal";
import { isActiveFramework } from "@/data/frameworkOptions";

export const CROSS_FRAMEWORK_LS_KEY = "cross_analysis_framework_selection";

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

export function readCachedCrossFrameworkSelection(): Partial<FrameworkSelectionValues> {
  try {
    const raw = typeof window !== "undefined" ? localStorage.getItem(CROSS_FRAMEWORK_LS_KEY) : null;
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const framework = safeTrim((parsed as any).framework);
    if (!isActiveFramework(framework)) {
      localStorage.removeItem(CROSS_FRAMEWORK_LS_KEY);
      return {};
    }
    return {
      framework: framework.toUpperCase(),
      industry: safeTrim((parsed as any).industry) || undefined,
      semiIndustry: safeTrim((parsed as any).semiIndustry) || undefined,
    };
  } catch {
    return {};
  }
}

export function writeCachedCrossFrameworkSelection(values: FrameworkSelectionValues): void {
  try {
    if (!isActiveFramework(values.framework)) {
      localStorage.removeItem(CROSS_FRAMEWORK_LS_KEY);
      return;
    }
    localStorage.setItem(CROSS_FRAMEWORK_LS_KEY, JSON.stringify(values));
  } catch {
    // ignore
  }
}

export function applyFrameworkToSearchParams(
  qs: URLSearchParams,
  values: FrameworkSelectionValues,
): URLSearchParams {
  const out = new URLSearchParams(qs.toString());
  const framework = safeTrim(values.framework).toUpperCase();
  if (!isActiveFramework(framework)) {
    out.delete("framework");
    out.delete("industry");
    out.delete("semiIndustry");
    return out;
  }
  out.set("framework", framework);

  if (framework === "SASB") {
    const ind = safeTrim(values.industry);
    const semi = safeTrim(values.semiIndustry);
    if (ind) out.set("industry", ind);
    else out.delete("industry");
    if (semi) out.set("semiIndustry", semi);
    else out.delete("semiIndustry");
  } else if (framework === "CDP") {
    out.set("industry", "CDP");
    const semi = safeTrim(values.semiIndustry);
    if (semi) out.set("semiIndustry", semi);
    else out.delete("semiIndustry");
  } else {
    out.delete("industry");
    out.delete("semiIndustry");
  }

  return out;
}

export function safeDecodeURIComponent(v: string): string {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}
