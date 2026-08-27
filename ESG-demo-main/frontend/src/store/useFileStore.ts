// src/store/useFileStore.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { apiService } from "@/lib/api";
import { errorSummary } from "@/lib/logger";

export interface File {
  key: string;
  name: string;
  size: string;
  dateUploaded: string;
  type: string;
  /** Backend storage category; homepage interpretation catalogues only show reports. */
  file_type?: string;
  status: "pending" | "ready" | "failed" | "partial";
  url?: string;
  industry?: string;
  semiIndustry?: string;
  framework?: string;
  /** GRI sector slug (for cross-analysis compatibility: same sector+topic only). */
  gri_sector?: string;
  /** GRI topic slug (for cross-analysis compatibility: same sector+topic only). */
  gri_topic?: string;
  file_id?: string;
  backend_status?: string;
  /** Multi-scope compliance progress (from GET /api/files enrichment). */
  scope_analysis_completed?: number;
  scope_analysis_total?: number;
  scope_analysis_partial?: boolean;
  scope_analysis_all_done?: boolean;
  scope_analysis_unknown_total?: boolean;
  /** When set, this row is one scope of a multi-scope upload; used for assessment + chat URL. */
  analysis_scope_key?: string;
  // Keep as string for AntD Table rendering, but accept backend numeric variants during mapping.
  pages?: string;
  /** Epoch ms from upload_time (or client clock when queued); newest-first sorting. */
  uploadedAtMs?: number;
  company_id?: string;
  company_name?: string;
  report_year?: number;
  batch_id?: string;
  upload_mode?: "single" | "multi" | string;
  company_analysis_version?: number;
}

export interface AnalysisReportSelection {
  fileId: string;
  scopeKey?: string;
}

export interface CrossAnalysisSelection {
  href: string;
  reports: AnalysisReportSelection[];
}

export function buildComplianceAnalysisHref(
  fileId: string,
  scopeKey?: string | null,
): string {
  const query = new URLSearchParams({ file_id: String(fileId || "").trim() });
  const normalizedScopeKey = String(scopeKey || "").trim();
  if (normalizedScopeKey) query.set("scope", normalizedScopeKey);
  return `/dashboard/chat?${query.toString()}`;
}

function normalizeCrossAnalysisSelection(
  selection: unknown,
): CrossAnalysisSelection | null {
  if (!selection || typeof selection !== "object") return null;
  const rawSelection = selection as Partial<CrossAnalysisSelection>;
  const href = String(rawSelection.href || "").trim();
  const rawReports = Array.isArray(rawSelection.reports)
    ? rawSelection.reports
    : [];
  const reports = rawReports
    .map((report) => ({
      fileId: String(report?.fileId || "").trim(),
      scopeKey: String(report?.scopeKey || "").trim() || undefined,
    }))
    .filter((report) => report.fileId)
    .filter(
      (report, index, values) =>
        values.findIndex(
          (candidate) => candidate.fileId === report.fileId,
        ) === index,
    );
  if (reports.length < 2) return null;

  try {
    const url = new URL(href, "http://localhost");
    if (
      url.pathname !== "/cross-analysis"
      && !url.pathname.startsWith("/cross-analysis/")
    ) {
      return null;
    }
    const hrefIds = [
      ...new Set(
        String(url.searchParams.get("ids") || "")
          .split(",")
          .map((fileId) => fileId.trim())
          .filter(Boolean),
      ),
    ];
    if (
      hrefIds.length !== reports.length
      || hrefIds.some((fileId, index) => fileId !== reports[index].fileId)
    ) {
      return null;
    }
  } catch {
    return null;
  }

  return { href, reports };
}

export type ReportCatalogMode = "single" | "multi";

/**
 * Select the homepage catalogue for a report.
 *
 * The original single-report endpoint did not persist `upload_mode`, so an
 * absent or unknown value must remain visible in the single-report catalogue.
 * Only an explicit `multi` value belongs to the company/multi-report catalogue.
 */
export function getReportCatalogMode(
  file: Pick<File, "upload_mode">,
): ReportCatalogMode {
  return String(file.upload_mode || "").trim().toLowerCase() === "multi"
    ? "multi"
    : "single";
}

/**
 * Whether the given files can be used together for cross-analysis:
 * - Same framework only.
 * - For GRI, same Sector + same Topic only.
 * - For CDP / TCFD, same topic slug (semiIndustry) only.
 */
export function canCrossAnalyzeFiles(files: File[]): boolean {
  if (files.length < 2) return false;
  const frameworks = files.map((f) => (f.framework || "").trim());
  if (new Set(frameworks).size > 1) return false;
  if (frameworks[0] === "GRI") {
    const sectors = files.map((f) => (f.gri_sector ?? f.industry ?? "").toString().trim());
    const topics = files.map((f) => (f.gri_topic ?? f.semiIndustry ?? "").toString().trim());
    if (new Set(sectors).size > 1 || new Set(topics).size > 1) return false;
  }
  if (frameworks[0] === "CDP" || frameworks[0] === "TCFD") {
    const topics = files.map((f) => (f.semiIndustry ?? "").toString().trim());
    if (topics.some((x) => !x)) return false;
    if (new Set(topics).size > 1) return false;
  }
  return true;
}

/**
 * Backends are not always consistent in naming page count fields.
 * This helper normalizes common variants into a displayable string.
 */
/** Convert backend gri_sector/gri_topic slug to display label (e.g. oil_and_gas_sector -> Oil And Gas Sector) */
function slugToLabel(slug: string | undefined | null): string {
  if (slug == null || slug === "") return "";
  return slug
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

function formatFileSize(bytes: unknown): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "-";

  const megabytes = bytes / (1024 * 1024);
  if (megabytes < 1024) return `${megabytes.toFixed(2)} MB`;

  return `${(megabytes / 1024).toFixed(2)} GB`;
}

function normalizeTotalPages(file: any): string {
  const candidates = [
    file?.total_pages,
    file?.totalPages,
    file?.page_count,
    file?.pageCount,
    file?.pages,
    file?.pages_count,
    file?.num_pages,
    file?.n_pages,
    file?.metadata?.total_pages,
    file?.meta?.total_pages,
  ];

  const val = candidates.find((v) => v !== undefined && v !== null && v !== "");
  if (val === undefined || val === null || val === "") return "-";

  // Accept number-like strings.
  const n = typeof val === "string" ? Number(val) : val;
  if (typeof n === "number" && Number.isFinite(n)) return String(Math.trunc(n));
  return String(val);
}

function parseUploadTimeMs(raw: string | undefined | null): number {
  if (raw == null || raw === "") return 0;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : 0;
}

function fileRowsEqual(left: File, right: File): boolean {
  const leftKeys = Object.keys(left) as (keyof File)[];
  const rightKeys = Object.keys(right) as (keyof File)[];
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key) => Object.prototype.hasOwnProperty.call(right, key) && left[key] === right[key],
    )
  );
}

function fileListsEqual(current: File[], incoming: File[]): boolean {
  return (
    current.length === incoming.length &&
    current.every((file, index) => fileRowsEqual(file, incoming[index]))
  );
}

function mapBackendReportStatus(file: any): "pending" | "ready" | "failed" | "partial" {
  const raw = file?.status;
  const partial = file?.scope_analysis_partial === true;
  const allDone = file?.scope_analysis_all_done === true;

  if (raw === "failed") return "failed";
  // Scope-derived progress is more precise than the coarse backend file status.
  // A report may already be in the processed directory while only some scope outputs exist.
  if (partial) return "partial";
  if (allDone) return "ready";
  if (raw === "processed") return "ready";
  return "pending";
}

/** Backend sends one record per PDF; expand to one table row per scope when scope_rows.length > 1. */
function expandMultiScopeBackendRows(file: any, mapped: File): File[] {
  if (file?.status === "failed") return [mapped];
  const sr = file?.scope_rows;
  if (!Array.isArray(sr) || sr.length <= 1) return [mapped];

  return sr.map((r: any) => {
    const sk = String(r?.scope_key ?? "").trim();
    const label = typeof r?.label === "string" && r.label.trim() ? r.label.trim() : slugToLabel(sk);
    const ready = r?.ready === true;
    const fw = (mapped.framework || "").trim();
    return {
      ...mapped,
      key: `${file.file_id}::${sk}`,
      semiIndustry: label,
      gri_topic: fw === "GRI" && sk ? sk : mapped.gri_topic,
      analysis_scope_key: sk || undefined,
      status: ready ? ("ready" as const) : ("pending" as const),
      scope_analysis_completed: undefined,
      scope_analysis_total: undefined,
      scope_analysis_partial: false,
      scope_analysis_all_done: ready,
      scope_analysis_unknown_total: false,
    };
  });
}

interface FileStore {
  files: File[];
  selectedFileId: string | null;
  selectedFileScopeKey: string | null;
  crossAnalysisSelection: CrossAnalysisSelection | null;
  loading: boolean;
  lastRefresh: number;
  addFile: (file: File) => void;
  removeFileByKey: (key: string) => void;
  deleteFile: (fileId: string, scopeKey?: string) => Promise<void>;
  updateFileStatus: (
    fileIdOrKey: string,
    status: "pending" | "ready" | "failed" | "partial",
  ) => void;
  updateFilePages: (fileIdOrKey: string, pages: number) => void;
  setSelectedFileId: (fileId: string | null) => void;
  setComplianceSelection: (
    fileId: string | null,
    scopeKey?: string | null,
  ) => void;
  setCrossAnalysisSelection: (
    selection: CrossAnalysisSelection | null,
  ) => void;
  loadFilesFromBackend: (options?: {
    showLoading?: boolean;
    /** Queue one trailing request when a mutation must observe the newest backend state. */
    forceFresh?: boolean;
  }) => Promise<void>;
  setLoading: (loading: boolean) => void;
  clearFiles: () => void;
}

export const useFileStore = create<FileStore>()(
  persist(
    (set, get) => {
      let filesLoadRequest: Promise<void> | null = null;
      let filesLoadShowsLoading = false;
      let filesLoadTrailingRequest: Promise<void> | null = null;
      let trailingLoadShowsLoading = false;
      let filesLoadGeneration = 0;

      return {
      files: [],
      selectedFileId: null,
      selectedFileScopeKey: null,
      crossAnalysisSelection: null,
      loading: false,
      lastRefresh: 0,
      setLoading: (loading) => set({ loading }),
      clearFiles: () => {
        filesLoadGeneration += 1;
        filesLoadRequest = null;
        filesLoadShowsLoading = false;
        filesLoadTrailingRequest = null;
        trailingLoadShowsLoading = false;
        apiService.invalidateAssessmentByFileCache();
        apiService.invalidateVisualAssetCache();
        set(() => ({
          files: [],
          selectedFileId: null,
          selectedFileScopeKey: null,
          crossAnalysisSelection: null,
          loading: false,
          lastRefresh: 0,
        }));
      },
      addFile: (file) =>
        set((state) => ({
          files: [...state.files, { ...file, status: file.status ?? "pending" }],
        })),
      removeFileByKey: (key) =>
        set((state) => ({
          files: state.files.filter((file) => file.key !== key),
        })),
      updateFileStatus: (fileIdOrKey, status) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.file_id === fileIdOrKey || file.key === fileIdOrKey ? { ...file, status } : file
          ),
        })),
      updateFilePages: (fileIdOrKey, pages) =>
        set((state) => ({
          files: state.files.map((file) =>
            file.file_id === fileIdOrKey || file.key === fileIdOrKey
              ? { ...file, pages: pages.toString() }
              : file
          ),
        })),
      deleteFile: async (fileId, scopeKey) => {
        try {
          await apiService.deleteFile(fileId, scopeKey);

          const sk = scopeKey && String(scopeKey).trim() ? String(scopeKey).trim() : "";
          set((state) => {
            const deletingSelectedCompliance =
              state.selectedFileId === fileId
              && (
                !sk
                || !state.selectedFileScopeKey
                || state.selectedFileScopeKey === sk
              );
            const deletingSelectedCrossReport =
              state.crossAnalysisSelection?.reports.some(
                (report) =>
                  report.fileId === fileId
                  && (!sk || !report.scopeKey || report.scopeKey === sk),
              ) === true;
            return {
              files: state.files.filter((file) => {
                if (file.file_id !== fileId) return true;
                if (sk) return file.analysis_scope_key !== sk;
                return false;
              }),
              selectedFileId: deletingSelectedCompliance
                ? null
                : state.selectedFileId,
              selectedFileScopeKey: deletingSelectedCompliance
                ? null
                : state.selectedFileScopeKey,
              crossAnalysisSelection: deletingSelectedCrossReport
                ? null
                : state.crossAnalysisSelection,
            };
          });
        } catch (error) {
          console.error(`Failed to delete file from backend: ${errorSummary(error)}`);
          throw error;
        }

        // Deletion already succeeded. Keep a temporary list-refresh failure
        // from being reported to the user as a failed deletion.
        await get().loadFilesFromBackend({ showLoading: false, forceFresh: true });
      },
      setSelectedFileId: (fileId) =>
        set((state) => ({
          selectedFileId: fileId,
          selectedFileScopeKey:
            fileId && fileId === state.selectedFileId
              ? state.selectedFileScopeKey
              : null,
        })),
      setComplianceSelection: (fileId, scopeKey) =>
        set((state) => {
          const normalizedFileId = String(fileId || "").trim() || null;
          const normalizedScopeKey = normalizedFileId
            ? String(scopeKey || "").trim() || null
            : null;
          if (
            state.selectedFileId === normalizedFileId
            && state.selectedFileScopeKey === normalizedScopeKey
          ) {
            return state;
          }
          return {
            selectedFileId: normalizedFileId,
            selectedFileScopeKey: normalizedScopeKey,
          };
        }),
      setCrossAnalysisSelection: (selection) =>
        set((state) => {
          if (selection === null) {
            return state.crossAnalysisSelection === null
              ? state
              : { crossAnalysisSelection: null };
          }
          const nextSelection = normalizeCrossAnalysisSelection(selection);
          if (!nextSelection) return state;
          if (
            state.crossAnalysisSelection?.href === nextSelection.href
            && JSON.stringify(state.crossAnalysisSelection.reports)
              === JSON.stringify(nextSelection.reports)
          ) {
            return state;
          }
          return { crossAnalysisSelection: nextSelection };
        }),
      loadFilesFromBackend: (options) => {
        const showLoading = options?.showLoading === true;
        const forceFresh = options?.forceFresh === true;
        if (filesLoadRequest) {
          if (showLoading && !filesLoadShowsLoading) {
            filesLoadShowsLoading = true;
            set({ loading: true });
          }
          if (!forceFresh) return filesLoadRequest;

          trailingLoadShowsLoading = trailingLoadShowsLoading || showLoading;
          if (!filesLoadTrailingRequest) {
            const activeRequest = filesLoadRequest;
            const trailingGeneration = filesLoadGeneration;
            const startTrailingRequest = () => {
              if (filesLoadTrailingRequest !== trailingRequest) return;
              const trailingShowLoading = trailingLoadShowsLoading;
              trailingLoadShowsLoading = false;
              filesLoadTrailingRequest = null;
              if (trailingGeneration !== filesLoadGeneration) return;
              return get().loadFilesFromBackend({ showLoading: trailingShowLoading });
            };
            const trailingRequest = activeRequest.then(
              startTrailingRequest,
              startTrailingRequest,
            );
            filesLoadTrailingRequest = trailingRequest;
          }
          return filesLoadTrailingRequest;
        }

        filesLoadShowsLoading = showLoading;
        if (showLoading) set({ loading: true });

        const requestGeneration = filesLoadGeneration;
        const request = (async () => {
          try {
            const response = await apiService.getFiles();
            if (
              requestGeneration === filesLoadGeneration &&
              response.status === 'success'
            ) {
              const backendFiles: File[] = [];
              for (const file of response.files as any[]) {
                const mapped: File = {
                key: file.file_id,
                name: file.original_name,
                size: formatFileSize(file.file_size),
                dateUploaded: file.upload_time?.split("T")?.[0] || "",
                uploadedAtMs: parseUploadTimeMs(file.upload_time),
                type: file.original_name?.split('.')?.pop()?.toUpperCase() || '',
                file_type: file.file_type || undefined,
                status: mapBackendReportStatus(file),
                file_id: file.file_id,
                backend_status: file.status,
                industry:
                  file.framework === "GRI" && (file.gri_sector || file.gri_topic)
                    ? slugToLabel(file.gri_sector) || "GRI"
                    : file.framework === "CDP"
                      ? "CDP"
                      : file.framework === "TCFD"
                        ? "TCFD"
                        : (file.industry || ""),
                semiIndustry:
                  file.framework === "GRI" && (file.gri_sector || file.gri_topic)
                    ? slugToLabel(file.gri_topic)
                    : (file.semi_industry || ""),
                pages: normalizeTotalPages(file),
                framework: file.framework || "SASB",
                gri_sector: file.gri_sector ?? undefined,
                gri_topic: file.gri_topic ?? undefined,
                scope_analysis_completed:
                  typeof file.scope_analysis_completed === "number"
                    ? file.scope_analysis_completed
                    : undefined,
                scope_analysis_total:
                  typeof file.scope_analysis_total === "number"
                    ? file.scope_analysis_total
                    : undefined,
                scope_analysis_partial: file.scope_analysis_partial === true,
                scope_analysis_all_done: file.scope_analysis_all_done === true,
                scope_analysis_unknown_total: file.scope_analysis_unknown_total === true,
                company_id: file.company_id || undefined,
                company_name: file.company_name || undefined,
                report_year:
                  typeof file.report_year === "number" ? file.report_year : undefined,
                batch_id: file.batch_id || undefined,
                upload_mode: file.upload_mode || undefined,
                company_analysis_version:
                  typeof file.company_analysis_version === "number"
                    ? file.company_analysis_version
                    : undefined,
              };
                backendFiles.push(...expandMultiScopeBackendRows(file, mapped));
              }

              set((state) => ({
                files: fileListsEqual(state.files, backendFiles)
                  ? state.files
                  : backendFiles,
                lastRefresh: Date.now(),
              }));
            }
          } catch (error) {
            console.error(`Failed to load files from backend: ${errorSummary(error)}`);
          }
        })().finally(() => {
          if (filesLoadRequest !== request) return;
          const shouldStopLoading = filesLoadShowsLoading;
          filesLoadRequest = null;
          filesLoadShowsLoading = false;
          if (shouldStopLoading) set({ loading: false });
        });

        filesLoadRequest = request;
        return request;
      }
      };
    },
    {
      name: "file-storage",
      partialize: (state) => ({
        selectedFileId: state.selectedFileId,
        selectedFileScopeKey: state.selectedFileScopeKey,
        crossAnalysisSelection: state.crossAnalysisSelection,
      }),
      merge: (persistedState, currentState) => {
        const persisted = (persistedState || {}) as Partial<FileStore>;
        const selectedFileId = String(
          persisted.selectedFileId || "",
        ).trim() || null;
        return {
          ...currentState,
          selectedFileId,
          selectedFileScopeKey: selectedFileId
            ? String(persisted.selectedFileScopeKey || "").trim() || null
            : null,
          crossAnalysisSelection: normalizeCrossAnalysisSelection(
            persisted.crossAnalysisSelection,
          ),
        };
      },
    }
  )
);
