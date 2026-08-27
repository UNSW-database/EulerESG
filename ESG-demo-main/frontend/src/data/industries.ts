import sasbIndustryGroups from "./sasb_industry_groups.json";
import sasbManifest from "./sasb_metrics_manifest.json";

export type IndustryMap = {
  [key: string]: string[];
};

/** Shown when backend manifest lists a sub-industry not yet assigned to a SASB sector group. */
export const SASB_OTHER_INDUSTRY_KEY = "Other";

type ManifestShape = { semi_industry_to_file: Record<string, string> };

function mergeManifestOrphans(base: IndustryMap): IndustryMap {
  const flat = new Set(Object.values(base).flat());
  const supported = Object.keys((sasbManifest as ManifestShape).semi_industry_to_file);
  const orphans = supported.filter((k) => !flat.has(k));
  if (!orphans.length) {
    return base;
  }
  const out: IndustryMap = { ...base };
  const existing = out[SASB_OTHER_INDUSTRY_KEY] ?? [];
  out[SASB_OTHER_INDUSTRY_KEY] = [...new Set([...existing, ...orphans])].sort();
  return out;
}

/**
 * SASB: top-level industry (SASB sector) → sub-industry (SASB industry standard).
 * Aligned with `backend/data/sasb_metrics/manifest.json`; orphan manifest keys appear under {@link SASB_OTHER_INDUSTRY_KEY}.
 */
export const industries: IndustryMap = mergeManifestOrphans(sasbIndustryGroups as IndustryMap);

/** Sub-industries that have a metrics JSON on the backend (from bundled manifest). */
export const supportedSasbSemiIndustries: ReadonlySet<string> = new Set(
  Object.keys((sasbManifest as ManifestShape).semi_industry_to_file),
);
