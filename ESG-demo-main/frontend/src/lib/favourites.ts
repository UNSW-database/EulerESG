export const FAVOURITE_REPORTS_STORAGE_KEY = "euleresg-favourite-reports";

export type FavouriteReportIdentity = {
  key: string;
  file_id?: string;
  analysis_scope_key?: string;
};

export function favouriteReportKey(report: FavouriteReportIdentity): string {
  return `${report.file_id || report.key}::${report.analysis_scope_key || ""}`;
}

export function parseFavouriteReportKeys(value: string | null): Set<string> {
  if (!value) return new Set();
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(
      parsed
        .filter((item): item is string => typeof item === "string")
        .map((item) => item.trim())
        .filter(Boolean),
    );
  } catch {
    return new Set();
  }
}

export function readFavouriteReportKeys(): Set<string> {
  if (typeof window === "undefined") return new Set();
  return parseFavouriteReportKeys(
    window.localStorage.getItem(FAVOURITE_REPORTS_STORAGE_KEY),
  );
}

export function writeFavouriteReportKeys(keys: Iterable<string>): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    FAVOURITE_REPORTS_STORAGE_KEY,
    JSON.stringify([...new Set(keys)]),
  );
}

export function removeFavouriteKeysForFile(
  keys: Iterable<string>,
  fileId: string,
): Set<string> {
  const prefix = `${fileId}::`;
  return new Set([...keys].filter((key) => key !== fileId && !key.startsWith(prefix)));
}
