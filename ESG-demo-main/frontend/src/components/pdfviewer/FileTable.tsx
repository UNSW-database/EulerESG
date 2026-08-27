import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { App, Table, Button, Dropdown, Modal, Space, Tag, Tooltip } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import {
  BarChartOutlined,
  DeleteOutlined,
  EllipsisOutlined,
  StarFilled,
  StarOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  canCrossAnalyzeFiles,
  getReportCatalogMode,
  useFileStore,
} from "@/store/useFileStore";
import type { File, ReportCatalogMode } from "@/store/useFileStore";
import { useT } from "@/i18n/useT";
import { errorSummary } from "@/lib/logger";
import { apiService } from "@/lib/api";
import { warmAppRoute } from "@/lib/routeWarmup";
import {
  FAVOURITE_REPORTS_STORAGE_KEY,
  favouriteReportKey,
  readFavouriteReportKeys,
  removeFavouriteKeysForFile,
  writeFavouriteReportKeys,
} from "@/lib/favourites";

interface FileTableProps {
  onChatClick: (file: File) => void;
  selectedRows: File[];
  onSelectionChange: (rows: File[]) => void;
  reportCatalogMode: ReportCatalogMode;
  favouritesOnly?: boolean;
  title?: React.ReactNode;
  emptyText?: React.ReactNode;
}

type FileTableRow = File & {
  isCompany?: boolean;
  children?: FileTableRow[];
  reportCount?: number;
};

function uploadSortKey(f: File): number {
  if (typeof f.uploadedAtMs === "number" && f.uploadedAtMs > 0) return f.uploadedAtMs;
  const p = Date.parse(f.dateUploaded);
  return Number.isFinite(p) ? p : 0;
}

function sortFilesForDisplay(files: File[]): File[] {
  return [...files].sort((a, b) => {
    const tb = uploadSortKey(b);
    const ta = uploadSortKey(a);
    if (tb !== ta) return tb - ta;
    const nc = (a.name || "").localeCompare(b.name || "");
    if (nc !== 0) return nc;
    return (a.key || "").localeCompare(b.key || "");
  });
}

function statusColor(status: string | undefined) {
  if (status === "ready") return "success";
  if (status === "failed") return "error";
  if (status === "partial") return "processing";
  return "warning";
}

function frameworkTagColor(framework: string | undefined) {
  const value = (framework || "").trim();
  if (value === "SASB") return "blue";
  if (value === "GRI") return "green";
  if (value === "TCFD") return "purple";
  if (value === "CDP") return "gold";
  return "default";
}

const FileTable: React.FC<FileTableProps> = ({
  onChatClick,
  selectedRows,
  onSelectionChange,
  reportCatalogMode,
  favouritesOnly = false,
  title,
  emptyText,
}) => {
  const { t, lang } = useT();
  const { message, modal } = App.useApp();
  const router = useRouter();

  const files = useFileStore((state) => state.files);
  const loading = useFileStore((state) => state.loading);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [favouriteReportKeys, setFavouriteReportKeys] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<File | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [reanalyzingIds, setReanalyzingIds] = useState<Set<string>>(new Set());
  const warmedRoutes = useRef(new Set<string>());

  const warmRoute = useCallback((href: string) => {
    if (warmedRoutes.current.has(href)) return;
    warmedRoutes.current.add(href);
    warmAppRoute(router, href);
  }, [router]);

  useEffect(() => {
    const syncFavourites = () => setFavouriteReportKeys(readFavouriteReportKeys());
    const handleStorage = (event: StorageEvent) => {
      if (event.key === FAVOURITE_REPORTS_STORAGE_KEY || event.key === null) {
        syncFavourites();
      }
    };
    syncFavourites();
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, []);

  const toggleFavourite = useCallback((file: File) => {
    const favouriteKey = favouriteReportKey(file);
    setFavouriteReportKeys((current) => {
      const next = new Set(current);
      if (next.has(favouriteKey)) next.delete(favouriteKey);
      else next.add(favouriteKey);
      writeFavouriteReportKeys(next);
      return next;
    });
  }, []);

  const isFavourite = useCallback(
    (file: File) => favouriteReportKeys.has(favouriteReportKey(file)),
    [favouriteReportKeys],
  );

  useEffect(() => {
    const timer = setInterval(() => {
      const state = useFileStore.getState();
      const hasUnfinished = state.files.some((f) => f.status === "pending" || f.status === "partial");
      if (hasUnfinished && !state.loading) {
        void state.loadFilesFromBackend({ showLoading: false });
      }
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  const crossAnalysisAllowed = canCrossAnalyzeFiles(selectedRows);
  const selectedFrameworks = [...new Set(selectedRows.map((f) => (f.framework || "").trim()).filter(Boolean))];
  const crossAnalysisDisabledReason =
    selectedRows.length >= 2 && !crossAnalysisAllowed
      ? selectedFrameworks.length > 1
        ? t("files.crossAnalysisDifferentFrameworkDisabled")
        : t("files.crossAnalysisSameFramework")
      : undefined;

  const handleCrossAnalyze = () => {
    if (selectedRows.length < 2) {
      void message.info(t("files.selectAtLeastTwoReports"));
      return;
    }
    if (!crossAnalysisAllowed) {
      void message.warning(t("files.crossAnalysisSameFramework"));
      return;
    }
    const ids = [...new Set(selectedRows.map((file) => file.file_id).filter(Boolean) as string[])];
    if (ids.length < 2) {
      void message.info(t("files.selectAtLeastTwoReports"));
      return;
    }
    const href = `/cross-analysis?ids=${encodeURIComponent(ids.join(","))}`;
    useFileStore.getState().setCrossAnalysisSelection({
      href,
      reports: ids.map((fileId) => {
        const report = selectedRows.find((file) => file.file_id === fileId);
        return {
          fileId,
          scopeKey: report?.analysis_scope_key,
        };
      }),
    });
    warmRoute("/cross-analysis");
    apiService.prefetchCrossAnalysis(ids);
    router.push(href);
  };

  const handleDeleteConfirm = async () => {
    const target = deleteTarget;
    if (!target) return;
    if (!target.file_id) {
      void message.error(lang === "zh" ? "删除失败：报告 ID 缺失" : "Delete failed: missing report ID");
      return;
    }

    setDeleting(true);
    try {
      // This action means deleting the report itself. Never pass a scope key
      // here, otherwise a multi-scope row would only remove one assessment and
      // the PDF would reappear after the list refresh.
      await useFileStore.getState().deleteFile(target.file_id);
      onSelectionChange(selectedRows.filter((row) => row.file_id !== target.file_id));
      setFavouriteReportKeys((current) => {
        const next = removeFavouriteKeysForFile(current, target.file_id!);
        writeFavouriteReportKeys(next);
        return next;
      });
      setDeleteTarget(null);
      void message.success(lang === "zh" ? "文件已删除" : "File deleted");
    } catch (error) {
      const detail = errorSummary(error);
      void message.error(lang === "zh" ? `删除失败：${detail}` : `Delete failed: ${detail}`);
    } finally {
      setDeleting(false);
    }
  };

  const setReanalyzing = useCallback((fileId: string, active: boolean) => {
    setReanalyzingIds((current) => {
      const next = new Set(current);
      if (active) next.add(fileId);
      else next.delete(fileId);
      return next;
    });
  }, []);

  const handleReanalyze = useCallback((file: File) => {
    const fileId = file.file_id;
    if (!fileId || reanalyzingIds.has(fileId)) return;

    modal.confirm({
      title: lang === "zh" ? "重新分析报告" : "Re-analyze report",
      content:
        lang === "zh"
          ? "仅复用已有 OCR、分段和 embedding，不会重新解析 PDF。非确定性指标仍可能调用当前配置的 LLM 并产生费用。"
          : "This reuses the existing OCR, segments, and embeddings without parsing the PDF again. Non-deterministic metrics may still call the configured LLM and incur usage charges.",
      okText: lang === "zh" ? "开始重新分析" : "Start re-analysis",
      cancelText: lang === "zh" ? "取消" : "Cancel",
      onOk: async () => {
        const messageKey = `reanalyze-${fileId}`;
        setReanalyzing(fileId, true);
        try {
          const accepted = await apiService.reanalyzeReport(fileId);
          const jobId = accepted.job_id;
          if (!jobId) throw new Error("Backend did not return a re-analysis job ID");
          void message.loading({
            key: messageKey,
            content: lang === "zh" ? "正在重新分析报告…" : "Re-analyzing report…",
            duration: 0,
          });
          apiService.subscribeReportJob(jobId, {
            onDone: () => {
              apiService.invalidateAssessmentByFileCache(fileId);
              apiService.invalidateCrossAnalysisCache();
              void useFileStore.getState().loadFilesFromBackend({
                showLoading: false,
                forceFresh: true,
              });
              setReanalyzing(fileId, false);
              void message.success({
                key: messageKey,
                content: lang === "zh" ? "报告分析已更新" : "Report analysis updated",
              });
            },
            onError: (error) => {
              setReanalyzing(fileId, false);
              void message.error({
                key: messageKey,
                content:
                  lang === "zh"
                    ? `重新分析失败：${errorSummary(error)}`
                    : `Re-analysis failed: ${errorSummary(error)}`,
              });
            },
          });
        } catch (error) {
          setReanalyzing(fileId, false);
          void message.error({
            key: messageKey,
            content:
              lang === "zh"
                ? `无法开始重新分析：${errorSummary(error)}`
                : `Unable to start re-analysis: ${errorSummary(error)}`,
          });
          throw error;
        }
      },
    });
  }, [lang, message, modal, reanalyzingIds, setReanalyzing]);

  const dataSource = useMemo<FileTableRow[]>(() => {
    const reportFiles = files.filter(
      (file) => !file.file_type || file.file_type === "report",
    );
    const companyReportIds = new Map<string, Set<string>>();
    const explicitlyMultiCompanyIds = new Set<string>();
    for (const file of reportFiles) {
      if (!file.company_id) continue;
      const reportIds = companyReportIds.get(file.company_id) || new Set<string>();
      reportIds.add(file.file_id || file.key);
      companyReportIds.set(file.company_id, reportIds);
      if (getReportCatalogMode(file) === "multi") {
        explicitlyMultiCompanyIds.add(file.company_id);
      }
    }
    const multiReportCompanyIds = new Set(
      [...companyReportIds.entries()]
        .filter(
          ([companyId, reportIds]) =>
            explicitlyMultiCompanyIds.has(companyId) || reportIds.size >= 2,
        )
        .map(([companyId]) => companyId),
    );
    const visibleFiles = favouritesOnly
      ? reportFiles.filter((file) => favouriteReportKeys.has(favouriteReportKey(file)))
      : reportFiles.filter((file) => {
          if (reportCatalogMode === "single") {
            return getReportCatalogMode(file) === "single";
          }
          return (
            getReportCatalogMode(file) === "multi" ||
            Boolean(file.company_id && multiReportCompanyIds.has(file.company_id))
          );
        });
    const sorted = sortFilesForDisplay(visibleFiles);
    const grouped = new Map<string, File[]>();
    const ungrouped: FileTableRow[] = [];
    for (const file of sorted) {
      // Single-report interpretations are always flat, including historical
      // records that happen to carry a company_id. Multi-report interpretations
      // are grouped into their company directory.
      if (favouritesOnly || reportCatalogMode === "single" || !file.company_id) {
        ungrouped.push(file);
        continue;
      }
      const values = grouped.get(file.company_id) || [];
      values.push(file);
      grouped.set(file.company_id, values);
    }
    const companyRows: FileTableRow[] = [];
    for (const [companyId, reports] of grouped.entries()) {
      const children = sortFilesForDisplay(reports);
      const reportCount = new Set(
        children.map((item) => item.file_id || item.key),
      ).size;
      const failed = children.some((item) => item.status === "failed");
      const pending = children.some((item) => item.status === "pending");
      const partial = children.some((item) => item.status === "partial");
      const first = children[0];
      companyRows.push({
        ...first,
        key: `company::${companyId}`,
        file_id: undefined,
        name: first.company_name || companyId,
        size: `${reportCount} ${reportCount === 1 ? "report" : "reports"}`,
        type: "Company",
        pages: "-",
        status: failed ? "failed" : pending ? "pending" : partial ? "partial" : "ready",
        isCompany: true,
        reportCount,
        children,
      });
    }
    companyRows.sort((a, b) => uploadSortKey(b) - uploadSortKey(a));
    return [...companyRows, ...ungrouped];
  }, [favouriteReportKeys, favouritesOnly, files, reportCatalogMode]);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(dataSource.length / pageSize));
    setCurrentPage((prev) => Math.min(prev, totalPages));
  }, [dataSource.length, pageSize]);

  useEffect(() => {
    setCurrentPage(1);
  }, [reportCatalogMode]);

  const statusText = useCallback((file: File) => {
    const s = file.status;
    if (s === "ready") return t("files.status.ready");
    if (s === "failed") return t("files.status.failed");
    if (s === "partial") {
      const unk = file.scope_analysis_unknown_total === true;
      const done = Number(file.scope_analysis_completed ?? 0);
      const total = Number(file.scope_analysis_total ?? 0);
      if (unk || !total) {
        return t("files.status.partialUnknown", { n: String(done) });
      }
      return t("files.status.partial", { done: String(done), total: String(total) });
    }
    return t("files.status.pending");
  }, [t]);

  const renderUnknown = useCallback(
    (value: any) =>
      value && value !== "Unknown" && value !== "未知"
        ? value
        : t("common.unknown"),
    [t],
  );

  const optionFilterValue = useCallback((file: File) => {
    const framework = (file.framework || "").trim();
    const value = framework === "CDP" || framework === "TCFD" ? file.semiIndustry : file.industry;
    return String(value || t("common.unknown"));
  }, [t]);

  const subOptionFilterValue = useCallback((file: File) => {
    const framework = (file.framework || "").trim();
    return framework === "CDP" || framework === "TCFD"
      ? t("common.na")
      : String(file.semiIndustry || t("common.unknown"));
  }, [t]);

  const tableFilters = useMemo(() => {
    const filterRows = dataSource.flatMap((row) => [
      row,
      ...(row.children || []),
    ]);
    const makeFilterOptions = (values: string[]) =>
      [...new Set(values.map((value) => value.trim()).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b))
        .map((value) => ({ text: value, value }));

    return {
      name: makeFilterOptions(
        filterRows.map((file) => String(file.name || t("common.unknown"))),
      ),
      date: makeFilterOptions(
        filterRows.map((file) =>
          String(file.dateUploaded || t("common.unknown")),
        ),
      ),
      framework: makeFilterOptions(
        filterRows.map((file) =>
          String(file.framework || t("common.unknown")),
        ),
      ),
      option: makeFilterOptions(filterRows.map(optionFilterValue)),
      subOption: makeFilterOptions(filterRows.map(subOptionFilterValue)),
      status: [
        ...new Map(
          filterRows.map((file) => [
            file.status,
            { text: statusText(file), value: file.status },
          ]),
        ).values(),
      ],
    };
  }, [dataSource, optionFilterValue, statusText, subOptionFilterValue, t]);

  const columns = useMemo<ColumnsType<FileTableRow>>(() => [
    {
      title: t("files.columns.name"),
      dataIndex: "name",
      key: "name",
      filters: tableFilters.name,
      filterSearch: true,
      onFilter: (value, record) => String(record.name || t("common.unknown")) === String(value),
      render: (value: string, record) => (
        <span className={`inline-flex items-center gap-2 ${record.isCompany ? "font-semibold text-slate-900" : ""}`}>
          {!record.isCompany && isFavourite(record) ? (
            <StarFilled
              className="shrink-0 text-amber-400"
              aria-label={lang === "zh" ? "已收藏" : "Favourite"}
            />
          ) : null}
          <span>{value}</span>
        </span>
      ),
    },
    { title: t("files.columns.size"), dataIndex: "size", key: "size" },
    {
      title: t("files.columns.dateUploaded"),
      dataIndex: "dateUploaded",
      key: "dateUploaded",
      filters: tableFilters.date,
      filterSearch: true,
      onFilter: (value, record) => String(record.dateUploaded || t("common.unknown")) === String(value),
      render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")),
    },
    {
      title: t("files.columns.type"),
      dataIndex: "type",
      key: "type",
      render: (v: any) => (v && v !== "Unknown" && v !== "未知" ? v : t("common.unknown")),
    },
    {
      title: t("files.columns.framework"),
      dataIndex: "framework",
      key: "framework",
      filters: tableFilters.framework,
      filterSearch: true,
      onFilter: (value, record) => String(record.framework || t("common.unknown")) === String(value),
      render: (v: string | undefined) => {
        const fw = (v || "").trim() || t("common.unknown");
        return <Tag color={frameworkTagColor(v)}>{fw}</Tag>;
      },
    },
    {
      title: t("files.columns.industry"),
      dataIndex: "industry",
      key: "industry",
      filters: tableFilters.option,
      filterSearch: true,
      onFilter: (value, record) => optionFilterValue(record) === String(value),
      render: (v: any, record: File) => {
        const fw = (record.framework || "").trim();
        if (fw === "CDP" || fw === "TCFD") {
          return renderUnknown(record.semiIndustry);
        }
        return renderUnknown(v);
      },
    },
    {
      title: t("files.columns.subOption"),
      dataIndex: "semiIndustry",
      key: "semiIndustry",
      filters: tableFilters.subOption,
      filterSearch: true,
      onFilter: (value, record) => subOptionFilterValue(record) === String(value),
      render: (v: any, record: File) => {
        const fw = (record.framework || "").trim();
        return fw === "CDP" || fw === "TCFD" ? t("common.na") : renderUnknown(v);
      },
    },
    {
      title: t("files.columns.status"),
      key: "status",
      filters: tableFilters.status,
      filterSearch: true,
      onFilter: (value, record) => record.status === String(value),
      render: (_: any, file: File) => <Tag color={statusColor(file.status)}>{statusText(file)}</Tag>,
    },
    {
      title: t("files.columns.actions"),
      key: "actions",
      render: (_: any, file: FileTableRow) => (
        file.isCompany ? (
          <Button
            type="primary"
            size="small"
            icon={<ShieldCheck aria-hidden="true" className="h-4 w-4" data-testid="compliance-action-icon" />}
            disabled={file.status !== "ready"}
            onPointerDown={() => warmRoute(`/dashboard/company/${encodeURIComponent(file.company_id!)}`)}
            onFocus={() => warmRoute(`/dashboard/company/${encodeURIComponent(file.company_id!)}`)}
            onMouseEnter={() => warmRoute(`/dashboard/company/${encodeURIComponent(file.company_id!)}`)}
            onClick={(event) => {
              event.stopPropagation();
              router.push(`/dashboard/company/${encodeURIComponent(file.company_id!)}`);
            }}
          >
            {t("files.actions.analysis")}
          </Button>
        ) : <Space size={4}>
          <Button
            type="primary"
            size="small"
            icon={<ShieldCheck aria-hidden="true" className="h-4 w-4" data-testid="compliance-action-icon" />}
            onClick={(event) => {
              event.stopPropagation();
              onChatClick(file);
            }}
            onPointerDown={() => warmRoute("/dashboard/chat")}
            onFocus={() => warmRoute("/dashboard/chat")}
            onMouseEnter={() => warmRoute("/dashboard/chat")}
            disabled={file.status !== "ready"}
          >
            {t("files.actions.analysis")}
          </Button>
          <Dropdown
            trigger={["click"]}
            menu={{
              items: [
                {
                  key: "favourite",
                  icon: isFavourite(file) ? <StarFilled className="text-amber-400" /> : <StarOutlined />,
                  label: isFavourite(file)
                    ? (lang === "zh" ? "取消收藏" : "Remove from favourites")
                    : (lang === "zh" ? "收藏" : "Add to favourites"),
                },
                {
                  key: "reanalyze",
                  icon: (
                    <SyncOutlined
                      spin={Boolean(file.file_id && reanalyzingIds.has(file.file_id))}
                    />
                  ),
                  disabled:
                    file.status !== "ready" ||
                    !file.file_id ||
                    reanalyzingIds.has(file.file_id),
                  label:
                    lang === "zh"
                      ? "重新分析（不重新解析）"
                      : "Re-analyze (reuse parsing)",
                },
                {
                  key: "delete",
                  danger: true,
                  icon: <DeleteOutlined />,
                  label: t("files.actions.delete"),
                },
              ],
              onClick: ({ key, domEvent }) => {
                domEvent.stopPropagation();
                if (key === "favourite") {
                  toggleFavourite(file);
                  return;
                }
                if (key === "reanalyze") {
                  handleReanalyze(file);
                  return;
                }
                if (key !== "delete") return;
                setDeleteTarget(file);
              },
            }}
          >
            <Button
              type="default"
              size="small"
              icon={<EllipsisOutlined style={{ fontSize: 13 }} />}
              aria-label={lang === "zh" ? "更多操作" : "More actions"}
              title={lang === "zh" ? "更多操作" : "More actions"}
              onClick={(event) => event.stopPropagation()}
            />
          </Dropdown>
        </Space>
      ),
    },
  ], [
    handleReanalyze,
    isFavourite,
    lang,
    onChatClick,
    optionFilterValue,
    reanalyzingIds,
    renderUnknown,
    router,
    statusText,
    subOptionFilterValue,
    tableFilters,
    t,
    toggleFavourite,
    warmRoute,
  ]);

  const pagination = useMemo<TablePaginationConfig>(() => ({
    current: currentPage,
    pageSize,
    defaultPageSize: 10,
    showSizeChanger: true,
    pageSizeOptions: ["10", "20", "50", "100"],
    showLessItems: true,
    responsive: false,
    showTotal: (total) => `${total}`,
    onChange: (page, nextPageSize) => {
      const resolvedPageSize = nextPageSize || pageSize;
      if (resolvedPageSize !== pageSize) {
        setPageSize(resolvedPageSize);
        const firstIndex = (page - 1) * resolvedPageSize;
        const nextCurrent = Math.floor(firstIndex / resolvedPageSize) + 1;
        setCurrentPage(nextCurrent);
      } else {
        setCurrentPage(page);
      }
    },
  }), [currentPage, pageSize]);

  const selectedRowKeys = useMemo(
    () => selectedRows.map((row) => row.key),
    [selectedRows],
  );
  const rowSelection = useMemo(
    () => ({
      selectedRowKeys,
      onChange: (_keys: React.Key[], rows: FileTableRow[]) =>
        onSelectionChange(rows.filter((row) => !row.isCompany)),
      getCheckboxProps: (record: FileTableRow) => ({
        disabled: Boolean(record.isCompany),
      }),
    }),
    [onSelectionChange, selectedRowKeys],
  );
  const tableRow = useCallback(
    (record: FileTableRow) => {
      const companyHref = record.company_id
        ? `/dashboard/company/${encodeURIComponent(record.company_id)}`
        : "";
      return {
        onClick: () => {
          if (record.isCompany && record.company_id && record.status === "ready") {
            router.push(companyHref);
          }
        },
        onMouseEnter: () => {
          if (record.isCompany && companyHref) warmRoute(companyHref);
        },
        onPointerDown: () => {
          if (record.isCompany && companyHref) warmRoute(companyHref);
        },
        style: record.isCompany
          ? { cursor: record.status === "ready" ? "pointer" : "default" }
          : undefined,
      };
    },
    [router, warmRoute],
  );

  return (
    <>
      <div className="mt-4 bg-white rounded-lg shadow-sm">
      <div className="p-3 border-b border-gray-200 flex justify-between items-center">
        <h3 className="text-lg font-semibold text-gray-700">
          {title ?? t("files.title")}
        </h3>
        <div className="flex items-center space-x-2">
          <Tooltip title={crossAnalysisDisabledReason} placement="top">
            <span>
              <Button
                type="primary"
                size="small"
                icon={<BarChartOutlined />}
                disabled={selectedRows.length < 2 || !crossAnalysisAllowed}
                onPointerDown={() => warmRoute("/cross-analysis")}
                onFocus={() => warmRoute("/cross-analysis")}
                onMouseEnter={() => warmRoute("/cross-analysis")}
                onClick={handleCrossAnalyze}
              >
                {t("files.crossAnalysisBeta")}
              </Button>
            </span>
          </Tooltip>
        </div>
      </div>
      <div className="overflow-x-auto px-1 pb-2">
        {dataSource.length === 0 && !loading ? (
          <div className="p-6 text-center text-gray-500">
            {emptyText ?? t("common.noDataAvailable")}
          </div>
        ) : (
          <Table
            columns={columns}
            dataSource={dataSource}
            pagination={pagination}
            scroll={{ x: "max-content", scrollToFirstRowOnChange: true }}
            size="small"
            className="w-full dashboard-file-table"
            rowKey={(record) => record.key}
            loading={loading}
            rowSelection={rowSelection}
            onRow={tableRow}
          />
        )}
      </div>
      </div>
      <Modal
        open={Boolean(deleteTarget)}
        title={t("files.deleteTitle")}
        okText={t("common.yes")}
        cancelText={t("common.no")}
        okButtonProps={{ danger: true }}
        confirmLoading={deleting}
        closable={!deleting}
        mask={{ closable: !deleting }}
        keyboard={!deleting}
        onOk={() => void handleDeleteConfirm()}
        onCancel={() => {
          if (!deleting) setDeleteTarget(null);
        }}
      >
        <p>{t("files.deleteDesc")}</p>
        {deleteTarget?.name ? (
          <p className="mt-2 break-all text-sm font-medium text-slate-700">{deleteTarget.name}</p>
        ) : null}
      </Modal>
    </>
  );
};

export default FileTable;
