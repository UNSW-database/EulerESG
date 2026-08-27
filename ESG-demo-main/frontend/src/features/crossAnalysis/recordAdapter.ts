// src/features/crossAnalysis/recordAdapter.ts

import type { AllRecord, AllRecordsRowRaw, CrossExtractedRecord } from "./types";

/**
 * Normalize backend records into the requested schema:
 * id, name, primary_navigation, secondary_navigation, topic, sub_topic,
 * page, data, year, unit, detail
 *
 * The backend may still return legacy fields:
 * - topic (primary nav), type (secondary nav), label (metric), detail (remark), context (interpretation)
 */

function asStr(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

function asNumOrNull(v: any): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function getAny(raw: any, keys: string[]): any {
  for (const k of keys) {
    if (raw && Object.prototype.hasOwnProperty.call(raw, k)) return (raw as any)[k];
  }
  return undefined;
}

function isNewSchema(raw: any): boolean {
  return !!(
    getAny(raw, ["primary_navigation", "Primary Navigation", "primaryNavigation"]) ||
    getAny(raw, ["secondary_navigation", "Secondary Navigation", "secondaryNavigation"]) ||
    getAny(raw, ["sub_topic", "Sub-topic", "subTopic"]) ||
    // If it contains explicit Topic field alongside navs
    (getAny(raw, ["Topic"]) && getAny(raw, ["Primary Navigation", "Secondary Navigation"]))
  );
}

export function normalizeCrossRecord(raw: any): CrossExtractedRecord {
  const newSchema = isNewSchema(raw);

  const id = asStr(getAny(raw, ["id", "file_id", "report_id"])) || asStr(raw?.id);
  const name = asStr(getAny(raw, ["name", "display_name", "short_name"])) || asStr(raw?.name);

  if (newSchema) {
    const primary_navigation = asStr(getAny(raw, ["primary_navigation", "Primary Navigation", "primaryNavigation"])) || "";
    const secondary_navigation = asStr(getAny(raw, ["secondary_navigation", "Secondary Navigation", "secondaryNavigation"])) || "";
    const topic = asStr(getAny(raw, ["topic", "Topic"])) || "";
    const sub_topic = asStr(getAny(raw, ["sub_topic", "Sub-topic", "subTopic"])) || "";
    const pageRaw = getAny(raw, ["page", "Page"]);
    const page = pageRaw === null || pageRaw === undefined ? null : (asNumOrNull(pageRaw) ?? null);
    // Cross Analysis cached records should use `value` (not legacy `data`).
    // Keep the internal field name as `data` for UI compatibility.
    const data = asStr(getAny(raw, ["value", "Value"])) || "";
    const year = asStr(getAny(raw, ["year", "Year"])) || null;
    const unit = asStr(getAny(raw, ["unit", "Unit"])) || null;
    const detail = asStr(getAny(raw, ["detail", "Detail"])) || "";
    const category = asStr(getAny(raw, ["category", "Category"])) || null;

    return {
      id,
      name,
      primary_navigation,
      secondary_navigation,
      topic,
      sub_topic,
      page,
      data,
      year,
      unit,
      detail,
      category: category || undefined,
    };
  }

  // Legacy mapping
  const primary_navigation = asStr(getAny(raw, ["topic"])) || "";
  const secondary_navigation = asStr(getAny(raw, ["type"])) || "";
  const topic = asStr(getAny(raw, ["label"])) || "";
  const sub_topic = asStr(getAny(raw, ["detail"])) || "";
  const pageRaw = getAny(raw, ["page"]);
  const page = pageRaw === null || pageRaw === undefined ? null : (asNumOrNull(pageRaw) ?? null);
  const data = asStr(getAny(raw, ["data"])) || "";
  const year = asStr(getAny(raw, ["year"])) || null;
  const unit = asStr(getAny(raw, ["unit"])) || null;
  const detail = asStr(getAny(raw, ["detail_interpretation"])) || asStr(getAny(raw, ["context"])) || "";
  const category = asStr(getAny(raw, ["category", "Category"])) || null;

  return {
    id,
    name,
    primary_navigation,
    secondary_navigation,
    topic,
    sub_topic,
    page,
    data,
    year,
    unit,
    detail,
    category: category || undefined,
    // Keep legacy fields so the rest of the app can still reference them if needed.
    type: asStr(getAny(raw, ["type"])),
    label: asStr(getAny(raw, ["label"])) || null,
    context: asStr(getAny(raw, ["context"])) || null,
  };
}

export function normalizeCrossRecords(rawRecords: any[]): CrossExtractedRecord[] {
  return (rawRecords || []).map(normalizeCrossRecord);
}


export function normalizeAllRecords(rawRows: AllRecordsRowRaw[]): AllRecord[] {
  return (rawRows || []).map((raw) => {
    const id = asStr(getAny(raw, ["id", "file_id", "report_id"]));
    const name = asStr(getAny(raw, ["name", "display_name", "short_name"]));
    const primaryNavigation = asStr(getAny(raw, ["primaryNavigation", "primary_navigation", "Primary Navigation"]));
    const secondaryNavigation = asStr(getAny(raw, ["secondaryNavigation", "secondary_navigation", "Secondary Navigation"]));
    const topic = asStr(getAny(raw, ["topic", "Topic", "label"]));
    const subTopic = asStr(getAny(raw, ["subTopic", "sub_topic", "Sub-topic", "detail"]));
    const pageRaw = getAny(raw, ["page", "Page"]);
    const page = pageRaw === null || pageRaw === undefined ? null : (asNumOrNull(pageRaw) ?? null);
    const data = asStr(getAny(raw, ["data", "Data", "value", "Value"]));
    const year = asStr(getAny(raw, ["year", "Year"])) || null;
    const unit = asStr(getAny(raw, ["unit", "Unit"])) || null;
    const detail = asStr(getAny(raw, ["detail", "Detail", "context", "detail_interpretation"]));

    return {
      id,
      name,
      primaryNavigation,
      secondaryNavigation,
      topic,
      subTopic,
      page,
      data,
      year,
      unit,
      detail,
    };
  });
}
