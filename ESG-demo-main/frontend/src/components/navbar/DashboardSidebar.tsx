"use client";

import Image from "next/image";
import dynamic from "next/dynamic";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { RefCallback } from "react";
import { BarChart3, Check, HelpCircle, House, Languages, LibraryBig, ListChecks, LogOut, Network, PanelLeftClose, PanelLeftOpen, Repeat2, Settings, ShieldCheck, Star } from "lucide-react";
import { App as AntdApp } from "antd";
import EulerLogo from "@/assets/Euler-Img.svg";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { clearAuth, getStoredAuth } from "@/lib/auth";
import { apiService } from "@/lib/api";
import { warmAppRoute } from "@/lib/routeWarmup";
import { useAppLang } from "@/i18n/useAppLang";
import { useT } from "@/i18n/useT";
import {
  buildComplianceAnalysisHref,
  canCrossAnalyzeFiles,
  useFileStore,
} from "@/store/useFileStore";
import type { File } from "@/store/useFileStore";
import type { DashboardReportSelectorMode } from "./DashboardReportSelector";

function ReportSelectorLoading() {
  return (
    <div
      className="fixed inset-0 z-[1000] grid place-items-center bg-slate-950/20 backdrop-blur-[1px]"
      role="status"
      aria-live="polite"
    >
      <div className="rounded-2xl border border-slate-200 bg-white px-5 py-4 text-sm font-medium text-slate-700 shadow-xl">
        Loading reports…
      </div>
    </div>
  );
}

const DashboardReportSelector = dynamic(
  () => import("./DashboardReportSelector"),
  { ssr: false, loading: ReportSelectorLoading },
);

const preloadDashboardReportSelector = () =>
  import("./DashboardReportSelector");
const preloadDashboardWorkspace = () =>
  import("@/components/pdfviewer/PDFViewer");
const preloadComplianceWorkspace = () =>
  import("@/components/pdfviewer/ChatView");
const preloadFavouriteWorkspace = () =>
  import("@/components/pdfviewer/FileTable");
const preloadStandardsWorkspace = () =>
  import("@/components/maincontent/FrameworkReferencePanel");

const preloadWorkspace = (loader: () => Promise<unknown>) => {
  if (process.env.NODE_ENV === "test") return;
  void loader().catch(() => undefined);
};

const SIDEBAR_STORAGE_KEY = "dashboard-sidebar-collapsed";
type SidebarNavigationKey =
  | "homepage"
  | "compliance"
  | "cross-analysis"
  | "disclosure-completeness"
  | "favourite"
  | "standards-library"
  | "graph-exploration";

function reportKey(file: Pick<File, "file_id" | "analysis_scope_key">) {
  return `${file.file_id}::${file.analysis_scope_key || ""}`;
}

type DashboardSidebarProps = {
  crossAnalysisNavigationSlotRef?: RefCallback<HTMLDivElement>;
};

export default function DashboardSidebar({
  crossAnalysisNavigationSlotRef,
}: DashboardSidebarProps = {}) {
  const { message } = AntdApp.useApp();
  const router = useRouter();
  const pathname = usePathname() || "";
  const searchParams = useSearchParams();
  const crossAnalysisIdsParam = searchParams.get("ids") || "";
  const crossAnalysisSelectedReportIds = useMemo(
    () => [
      ...new Set(
        crossAnalysisIdsParam
          .split(",")
          .map((fileId) => fileId.trim())
          .filter(Boolean),
      ),
    ],
    [crossAnalysisIdsParam],
  );
  const clearFiles = useFileStore((state) => state.clearFiles);
  const files = useFileStore((state) => state.files);
  const selectedFileId = useFileStore((state) => state.selectedFileId);
  const selectedFileScopeKey = useFileStore(
    (state) => state.selectedFileScopeKey,
  );
  const crossAnalysisSelection = useFileStore(
    (state) => state.crossAnalysisSelection,
  );
  const setComplianceSelection = useFileStore(
    (state) => state.setComplianceSelection,
  );
  const setCrossAnalysisSelection = useFileStore(
    (state) => state.setCrossAnalysisSelection,
  );
  const { lang, setLang } = useAppLang();
  const { t } = useT();
  const [collapsed, setCollapsed] = useState(false);
  const [clickedNavigationKey, setClickedNavigationKey] = useState<SidebarNavigationKey | null>(null);
  const [displayName, setDisplayName] = useState("User");
  const [selectorMode, setSelectorMode] = useState<DashboardReportSelectorMode | null>(null);
  const [selectedReportKeys, setSelectedReportKeys] = useState<string[]>([]);
  const standardsPrefetchStarted = useRef(false);
  const prefetchedRoutes = useRef(new Set<string>());

  const readyReports = useMemo(
    () => files.filter((file) => file.status === "ready" && Boolean(file.file_id)),
    [files]
  );
  const selectedReportKeySet = useMemo(
    () => new Set(selectedReportKeys),
    [selectedReportKeys],
  );
  const selectedReports = useMemo(
    () => readyReports.filter((file) => selectedReportKeySet.has(reportKey(file))),
    [readyReports, selectedReportKeySet],
  );
  useEffect(() => {
    const stored = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    setCollapsed(stored === null ? window.innerWidth < 768 : stored === "true");

    const auth = getStoredAuth();
    if (auth?.name || auth?.email) setDisplayName(auth.name || auth.email || "User");
  }, []);

  const initials = useMemo(() => {
    const firstCharacter = displayName.trim().slice(0, 1);
    return (firstCharacter || "U").toUpperCase();
  }, [displayName]);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
      return next;
    });
  };

  const markNavigationSelection = (key: SidebarNavigationKey) => {
    setClickedNavigationKey(key);
  };

  const handleLogout = () => {
    clearAuth();
    clearFiles();
    router.push("/login");
  };

  const handleSwitchAccount = () => {
    clearAuth();
    clearFiles();
    router.push("/login?switch_account=1");
  };

  const prefetchStandardsLibrary = () => {
    if (standardsPrefetchStarted.current) return;
    standardsPrefetchStarted.current = true;
    warmAppRoute(router, "/dashboard/standards-library");
    preloadWorkspace(preloadStandardsWorkspace);
    void apiService.getStandardsCatalog().catch(() => {
      standardsPrefetchStarted.current = false;
    });
  };

  const showUnavailableMessage = (feature: "settings" | "help") => {
    const label = feature === "settings"
      ? (lang === "zh" ? "设置" : "Settings")
      : (lang === "zh" ? "帮助" : "Help");
    void message.info(lang === "zh" ? `${label}功能即将开放` : `${label} is coming soon`);
  };

  const prefetchRoute = (route: string) => {
    if (prefetchedRoutes.current.has(route)) return;
    prefetchedRoutes.current.add(route);
    warmAppRoute(router, route);
  };

  const prefetchReportFlow = (mode: DashboardReportSelectorMode) => {
    void preloadDashboardReportSelector().catch(() => undefined);
    if (mode === "compliance") {
      preloadWorkspace(preloadComplianceWorkspace);
    }
    prefetchRoute(mode === "compliance" ? "/dashboard/chat" : "/cross-analysis");
  };

  const prefetchHomepage = () => {
    prefetchRoute("/dashboard");
    preloadWorkspace(preloadDashboardWorkspace);
  };

  const prefetchFavourite = () => {
    prefetchRoute("/dashboard/favourite");
    preloadWorkspace(preloadFavouriteWorkspace);
  };

  const openReportSelector = (mode: DashboardReportSelectorMode) => {
    prefetchReportFlow(mode);
    setSelectorMode(mode);
    setSelectedReportKeys([]);
    if (files.length === 0) {
      void useFileStore.getState().loadFilesFromBackend({ showLoading: false });
    }
  };

  const handleReportClick = (file: File) => {
    const key = reportKey(file);
    const isMultiReportSelector = selectorMode === "cross" || selectorMode === "disclosure";
    if (!isMultiReportSelector) {
      apiService.prefetchAssessmentByFile(
        file.file_id,
        file.analysis_scope_key,
        false,
        true,
      );
      setSelectedReportKeys([key]);
      return;
    }
    setSelectedReportKeys((current) =>
      current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key],
    );
  };

  const confirmReportSelection = () => {
    if (selectorMode === "compliance") {
      const report = selectedReports[0];
      if (!report?.file_id) {
        void message.info(lang === "zh" ? "请选择一份已处理完成的报告" : "Select one processed report");
        return;
      }
      apiService.prefetchAssessmentByFile(
        report.file_id,
        report.analysis_scope_key,
        false,
        true,
      );
      setComplianceSelection(report.file_id, report.analysis_scope_key);
      setSelectorMode(null);
      router.push(
        buildComplianceAnalysisHref(
          report.file_id,
          report.analysis_scope_key,
        ),
      );
      return;
    }

    const uniqueFileIds = [...new Set(selectedReports.map((file) => file.file_id).filter(Boolean) as string[])];
    if (uniqueFileIds.length < 2) {
      void message.info(lang === "zh" ? "请至少选择两份不同的已处理报告" : "Select at least two different processed reports");
      return;
    }
    if (!canCrossAnalyzeFiles(selectedReports)) {
      void message.warning(lang === "zh" ? "所选报告的框架或分析范围不兼容" : "The selected reports are not compatible for cross analysis");
      return;
    }
    if (selectorMode === "disclosure") {
      void apiService.getCrossAnalysisReports(uniqueFileIds).catch(() => undefined);
      uniqueFileIds.forEach((fileId) => {
        const report = selectedReports.find(
          (candidate) => candidate.file_id === fileId,
        );
        apiService.prefetchAssessmentByFile(
          fileId,
          report?.analysis_scope_key,
          false,
          true,
        );
      });
    } else {
      apiService.prefetchCrossAnalysis(uniqueFileIds);
    }
    setSelectorMode(null);
    const viewParam = selectorMode === "disclosure" ? "&view=disclosure" : "";
    const href = `/cross-analysis?ids=${encodeURIComponent(uniqueFileIds.join(","))}${viewParam}`;
    setCrossAnalysisSelection({
      href,
      reports: uniqueFileIds.map((fileId) => {
        const report = selectedReports.find((file) => file.file_id === fileId);
        return { fileId, scopeKey: report?.analysis_scope_key };
      }),
    });
    router.push(href);
  };

  const isHomepage = pathname === "/dashboard";
  const isCompliance = pathname.startsWith("/dashboard/chat") || pathname.startsWith("/dashboard/company");
  const isCrossAnalysisRoute = pathname.startsWith("/cross-analysis");
  const isDisclosureCompleteness =
    isCrossAnalysisRoute && (searchParams.get("view") || "").trim().toLowerCase() === "disclosure";
  const isCrossAnalysis = isCrossAnalysisRoute && !isDisclosureCompleteness;
  const restoreComplianceAnalysis = () => {
    if (!selectedFileId) return false;
    const candidates = readyReports.filter(
      (report) => report.file_id === selectedFileId,
    );
    const matchingReport = selectedFileScopeKey
      ? candidates.find(
          (report) => report.analysis_scope_key === selectedFileScopeKey,
        )
      : candidates.length === 1
        ? candidates[0]
        : (() => {
            const unscoped = candidates.filter(
              (report) => !report.analysis_scope_key,
            );
            return unscoped.length === 1 ? unscoped[0] : undefined;
          })();
    if (files.length > 0 && !matchingReport) return false;

    const scopeKey = matchingReport?.analysis_scope_key
      || selectedFileScopeKey
      || undefined;
    if (matchingReport && selectedFileScopeKey !== (scopeKey || null)) {
      setComplianceSelection(selectedFileId, scopeKey);
    }

    apiService.prefetchAssessmentByFile(
      selectedFileId,
      scopeKey,
      false,
      true,
    );
    router.push(
      buildComplianceAnalysisHref(selectedFileId, scopeKey),
    );
    return true;
  };
  const restoreCrossAnalysis = (view: "issue" | "disclosure") => {
    const saved = crossAnalysisSelection;
    if (!saved || saved.reports.length < 2) return false;
    const reportIds = [
      ...new Set(saved.reports.map((report) => report.fileId).filter(Boolean)),
    ];
    if (reportIds.length < 2) return false;
    if (files.length > 0) {
      const allReportsAvailable = saved.reports.every((savedReport) =>
        readyReports.some(
          (report) =>
            report.file_id === savedReport.fileId
            && (!savedReport.scopeKey
              || report.analysis_scope_key === savedReport.scopeKey),
        ),
      );
      if (!allReportsAvailable) return false;
    }

    const savedUrl = new URL(saved.href, "http://localhost");
    savedUrl.searchParams.set("ids", reportIds.join(","));
    if (view === "disclosure") {
      savedUrl.searchParams.set("view", "disclosure");
    } else {
      savedUrl.searchParams.delete("view");
    }
    const href = `${savedUrl.pathname}${savedUrl.search}`;
    if (view === "disclosure") {
      void apiService.getCrossAnalysisReports(reportIds).catch(() => undefined);
      reportIds.forEach((fileId) => {
        const selectedReport = saved.reports.find(
          (report) => report.fileId === fileId,
        );
        apiService.prefetchAssessmentByFile(
          fileId,
          selectedReport?.scopeKey,
          false,
          true,
        );
      });
    } else {
      apiService.prefetchCrossAnalysis(reportIds);
    }
    setCrossAnalysisSelection({ ...saved, href });
    router.push(href);
    return true;
  };
  const handleDisclosureCompletenessClick = () => {
    if (!isCrossAnalysisRoute || crossAnalysisSelectedReportIds.length < 2) {
      if (restoreCrossAnalysis("disclosure")) return;
      openReportSelector("disclosure");
      return;
    }

    // The active Cross Analysis URL is the source of truth for its selected
    // reports. Reuse it directly instead of asking the user to select the same
    // reports again when switching to Disclosure Completeness.
    void apiService.getCrossAnalysisReports(crossAnalysisSelectedReportIds).catch(() => undefined);
    const savedReports = crossAnalysisSelection?.reports || [];
    crossAnalysisSelectedReportIds.forEach((fileId) => {
      const selectedReport = savedReports.find(
        (report) => report.fileId === fileId,
      );
      apiService.prefetchAssessmentByFile(
        fileId,
        selectedReport?.scopeKey,
        false,
        true,
      );
    });
    setSelectorMode(null);
    const next = new URLSearchParams(searchParams.toString());
    next.set("ids", crossAnalysisSelectedReportIds.join(","));
    next.set("view", "disclosure");
    const href = `${pathname}?${next.toString()}`;
    const savedReportIds = savedReports.map((report) => report.fileId);
    const isCommittedSelection =
      savedReportIds.length === crossAnalysisSelectedReportIds.length
      && savedReportIds.every(
        (fileId, index) => fileId === crossAnalysisSelectedReportIds[index],
      );
    if (isCommittedSelection) {
      setCrossAnalysisSelection({ href, reports: savedReports });
    }
    router.push(href);
  };
  const isFavourite = pathname.startsWith("/dashboard/favourite");
  const isStandardsLibrary = pathname.startsWith("/dashboard/standards-library");
  const isGraphExploration = pathname.startsWith("/dashboard/graph");
  const blueIconClass = (selected: boolean) =>
    selected ? "text-[#2274BC]" : "text-slate-600";
  const homepageClicked = clickedNavigationKey === "homepage" && isHomepage;
  const complianceClicked =
    clickedNavigationKey === "compliance" &&
    (isCompliance || selectorMode === "compliance");
  const crossAnalysisClicked =
    clickedNavigationKey === "cross-analysis" &&
    (isCrossAnalysis || selectorMode === "cross");
  const disclosureCompletenessClicked =
    clickedNavigationKey === "disclosure-completeness" &&
    (isDisclosureCompleteness || selectorMode === "disclosure");
  const favouriteClicked = clickedNavigationKey === "favourite" && isFavourite;
  const standardsLibraryClicked =
    clickedNavigationKey === "standards-library" && isStandardsLibrary;
  const graphExplorationClicked =
    clickedNavigationKey === "graph-exploration" && isGraphExploration;
  const navigationClass = (active: boolean) =>
    `flex h-10 w-full shrink-0 items-center rounded-xl transition-colors ${
      active
        ? "bg-[#ececec] text-slate-900 visited:text-slate-900 hover:bg-[#e5e5e5]"
        : "text-slate-700 visited:text-slate-700 hover:bg-[#ececec] hover:text-slate-950"
    } ${collapsed ? "justify-center px-2" : "gap-3 px-2.5"}`;

  return (
    <>
    <div
      aria-hidden="true"
      className={`h-screen shrink-0 transition-[width] duration-200 ease-[var(--motion-fluid)] ${collapsed ? "w-[60px]" : "w-[260px]"}`}
    />
    <aside
      className={`fixed bottom-0 left-0 top-0 z-40 flex h-dvh shrink-0 flex-col overflow-hidden bg-[#f9f9f9] transition-[width] duration-200 ease-[var(--motion-fluid)] ${
        collapsed ? "w-[60px]" : "w-[260px]"
      }`}
      data-collapsed={collapsed}
    >
      <div className={`flex h-14 shrink-0 items-center ${collapsed ? "justify-center px-2" : "justify-between px-2.5"}`}>
        {collapsed ? (
          <button
            type="button"
            onClick={toggleCollapsed}
            className="group relative flex h-10 w-10 items-center justify-center rounded-xl text-slate-600 transition-colors hover:bg-[#ececec] hover:text-slate-950"
            aria-label="Expand sidebar"
            title="Expand sidebar"
            aria-expanded={!collapsed}
          >
            <Image
              src={EulerLogo}
              alt="Euler ESG"
              className="h-6 w-6 transition-[transform,opacity] duration-150 ease-[var(--motion-fluid)] group-hover:scale-75 group-hover:opacity-0 group-focus-visible:scale-75 group-focus-visible:opacity-0"
            />
            <PanelLeftOpen className="absolute h-5 w-5 scale-75 opacity-0 transition-[transform,opacity] duration-150 ease-[var(--motion-fluid)] group-hover:scale-100 group-hover:opacity-100 group-focus-visible:scale-100 group-focus-visible:opacity-100" />
          </button>
        ) : (
          <>
            <Link
              href="/dashboard"
              prefetch
              onPointerDown={prefetchHomepage}
              onFocus={prefetchHomepage}
              onMouseEnter={prefetchHomepage}
              onClick={() => markNavigationSelection("homepage")}
              className="flex h-10 min-w-0 items-center gap-2.5 rounded-xl bg-transparent px-2 text-left hover:bg-[#ececec]"
              aria-label={t("nav.goToAllFiles")}
            >
              <Image src={EulerLogo} alt="Euler ESG" className="h-7 w-7 shrink-0" />
              <span className="truncate text-[18px] font-semibold leading-5 text-[#2274BC]">Euler ESG</span>
            </Link>
            <button
              type="button"
              onClick={toggleCollapsed}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-slate-600 transition-colors hover:bg-[#ececec] hover:text-slate-950"
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
              aria-expanded={!collapsed}
            >
              <PanelLeftClose className="h-5 w-5" />
            </button>
          </>
        )}
      </div>

      <nav
        aria-label="Dashboard navigation"
        className={`flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overscroll-y-auto ${collapsed ? "px-2 pt-1" : "px-2.5 pt-1"}`}
      >
        <Link
          href="/dashboard"
          prefetch
          onPointerDown={prefetchHomepage}
          onFocus={prefetchHomepage}
          onMouseEnter={prefetchHomepage}
          onClick={() => markNavigationSelection("homepage")}
          className={navigationClass(isHomepage)}
          title={collapsed ? "Homepage" : undefined}
          aria-current={isHomepage ? "page" : undefined}
        >
          <House className={`h-[18px] w-[18px] shrink-0 ${blueIconClass(homepageClicked)}`} />
          {!collapsed && <span className="truncate text-sm">Homepage</span>}
        </Link>
        <button
          type="button"
          onClick={() => {
            markNavigationSelection("compliance");
            if (!isCompliance && restoreComplianceAnalysis()) return;
            openReportSelector("compliance");
          }}
          onPointerDown={() => prefetchReportFlow("compliance")}
          onFocus={() => prefetchReportFlow("compliance")}
          onMouseEnter={() => prefetchReportFlow("compliance")}
          className={navigationClass(isCompliance)}
          title={collapsed ? "Compliance" : undefined}
        >
          <ShieldCheck className={`h-[18px] w-[18px] shrink-0 ${blueIconClass(complianceClicked)}`} />
          {!collapsed && <span className="truncate text-sm">Compliance</span>}
        </button>
        <button
          type="button"
          onClick={() => {
            markNavigationSelection("cross-analysis");
            if (isDisclosureCompleteness && crossAnalysisSelectedReportIds.length >= 2) {
              const next = new URLSearchParams(searchParams.toString());
              next.set("ids", crossAnalysisSelectedReportIds.join(","));
              next.delete("view");
              const href = `${pathname}?${next.toString()}`;
              const savedReports = crossAnalysisSelection?.reports || [];
              const savedReportIds = savedReports.map(
                (report) => report.fileId,
              );
              const isCommittedSelection =
                savedReportIds.length === crossAnalysisSelectedReportIds.length
                && savedReportIds.every(
                  (fileId, index) =>
                    fileId === crossAnalysisSelectedReportIds[index],
                );
              if (isCommittedSelection) {
                setCrossAnalysisSelection({ href, reports: savedReports });
              }
              apiService.prefetchCrossAnalysis(crossAnalysisSelectedReportIds);
              router.push(href);
              return;
            }
            if (!isCrossAnalysisRoute && restoreCrossAnalysis("issue")) return;
            openReportSelector("cross");
          }}
          onPointerDown={() => prefetchReportFlow("cross")}
          onFocus={() => prefetchReportFlow("cross")}
          onMouseEnter={() => prefetchReportFlow("cross")}
          className={navigationClass(isCrossAnalysis)}
          title={collapsed ? "Cross Analysis" : undefined}
          aria-current={isCrossAnalysis ? "page" : undefined}
        >
          <BarChart3 className={`h-[18px] w-[18px] shrink-0 ${blueIconClass(crossAnalysisClicked)}`} />
          {!collapsed && <span className="truncate text-sm">Cross Analysis</span>}
        </button>
        <div
          role="group"
          aria-label="Cross Analysis"
          data-testid="cross-analysis-subnavigation"
          className="flex min-h-0 flex-col"
        >
          <button
            type="button"
            data-testid="disclosure-completeness-nav"
            onClick={() => {
              markNavigationSelection("disclosure-completeness");
              handleDisclosureCompletenessClick();
            }}
            onPointerDown={() => prefetchReportFlow("disclosure")}
            onFocus={() => prefetchReportFlow("disclosure")}
            onMouseEnter={() => prefetchReportFlow("disclosure")}
            aria-label={t("crossAnalysis.disclosureCompleteness")}
            aria-current={isDisclosureCompleteness ? "page" : undefined}
            className={`flex shrink-0 items-center border border-transparent transition-colors ${
              isDisclosureCompleteness
                ? "bg-[#ececec] text-slate-900 hover:bg-[#e5e5e5]"
                : "text-slate-600 hover:bg-[#ececec] hover:text-slate-950"
            } ${
              collapsed
                ? "h-9 w-full justify-center rounded-lg px-2"
                : "ml-5 w-[calc(100%-1.25rem)] rounded-xl px-3 py-2 text-left"
            }`}
            title={collapsed ? t("crossAnalysis.disclosureCompleteness") : undefined}
          >
            {collapsed ? (
              <ListChecks className={`h-4 w-4 shrink-0 ${blueIconClass(disclosureCompletenessClicked)}`} />
            ) : (
              <span className="truncate text-sm font-medium">{t("crossAnalysis.disclosureCompleteness")}</span>
            )}
          </button>
          {isCrossAnalysis ? (
            <div
              ref={crossAnalysisNavigationSlotRef}
              data-testid="cross-analysis-navigation-slot"
              hidden={collapsed}
              className="min-h-0 max-h-[min(320px,36vh)] overflow-y-auto overscroll-y-auto"
            />
          ) : null}
        </div>
        <Link
          href="/dashboard/favourite"
          prefetch
          onPointerDown={prefetchFavourite}
          onFocus={prefetchFavourite}
          onMouseEnter={prefetchFavourite}
          onClick={() => markNavigationSelection("favourite")}
          className={navigationClass(isFavourite)}
          title={collapsed ? "Favourite" : undefined}
          aria-current={isFavourite ? "page" : undefined}
        >
          <Star className={`h-[18px] w-[18px] shrink-0 ${blueIconClass(favouriteClicked)}`} />
          {!collapsed && <span className="truncate text-sm">Favourite</span>}
        </Link>
        <Link
          href="/dashboard/standards-library"
          prefetch
          onPointerDown={prefetchStandardsLibrary}
          onFocus={prefetchStandardsLibrary}
          onMouseEnter={prefetchStandardsLibrary}
          onClick={() => markNavigationSelection("standards-library")}
          className={navigationClass(isStandardsLibrary)}
          title={collapsed ? "Standards Library" : undefined}
          aria-current={isStandardsLibrary ? "page" : undefined}
        >
          <LibraryBig className={`h-[18px] w-[18px] shrink-0 ${blueIconClass(standardsLibraryClicked)}`} />
          {!collapsed && <span className="truncate text-sm">Standards Library</span>}
        </Link>
        <Link
          href="/dashboard/graph"
          prefetch
          onPointerDown={() => prefetchRoute("/dashboard/graph")}
          onFocus={() => prefetchRoute("/dashboard/graph")}
          onMouseEnter={() => prefetchRoute("/dashboard/graph")}
          onClick={() => markNavigationSelection("graph-exploration")}
          className={navigationClass(isGraphExploration)}
          title={collapsed ? "Graph Exploration" : undefined}
          aria-current={isGraphExploration ? "page" : undefined}
        >
          <Network className={`h-[18px] w-[18px] shrink-0 ${blueIconClass(graphExplorationClicked)}`} />
          {!collapsed && <span className="truncate text-sm">Graph Exploration</span>}
        </Link>
      </nav>

      <div className={`${collapsed ? "p-2 pb-2" : "p-2.5 pb-2.5"}`}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className={`h-12 w-full rounded-xl p-2 hover:bg-[#ececec] ${collapsed ? "justify-center" : "justify-start gap-2.5"}`}
              aria-label={t("nav.userMenu")}
            >
              <Avatar className="h-8 w-8 shrink-0">
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
              {!collapsed && <span className="min-w-0 truncate text-sm font-medium text-slate-700">{displayName}</span>}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            side={collapsed ? "right" : "top"}
            align="start"
            sideOffset={8}
            className="w-[240px] rounded-2xl border-slate-200 p-2 shadow-xl"
          >
            <DropdownMenuLabel className="flex items-center gap-3 px-2 py-2 font-normal">
              <Avatar className="h-8 w-8 shrink-0">
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
              <span className="min-w-0 truncate text-sm font-medium text-slate-800">{displayName}</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="h-10 rounded-lg" onClick={() => showUnavailableMessage("settings")}>
              <Settings className="mr-2 h-[18px] w-[18px]" />
              <span>{t("nav.settings")}</span>
            </DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="h-10 rounded-lg">
                <Languages className="mr-2 h-[18px] w-[18px]" />
                <span>{lang === "zh" ? "语言" : "Language"}</span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="min-w-[150px] rounded-xl p-1.5">
                <DropdownMenuItem className="h-9 rounded-lg" onClick={() => setLang("zh")}>
                  <span className="flex-1">中文</span>
                  {lang === "zh" && <Check className="h-4 w-4" />}
                </DropdownMenuItem>
                <DropdownMenuItem className="h-9 rounded-lg" onClick={() => setLang("en")}>
                  <span className="flex-1">English</span>
                  {lang === "en" && <Check className="h-4 w-4" />}
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuItem className="h-10 rounded-lg" onClick={() => showUnavailableMessage("help")}>
              <HelpCircle className="mr-2 h-[18px] w-[18px]" />
              <span>{lang === "zh" ? "帮助" : "Help"}</span>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="h-10 rounded-lg" onClick={handleSwitchAccount}>
              <Repeat2 className="mr-2 h-[18px] w-[18px]" />
              <span>{lang === "zh" ? "切换账号" : "Switch account"}</span>
            </DropdownMenuItem>
            <DropdownMenuItem className="h-10 rounded-lg" onClick={handleLogout}>
              <LogOut className="mr-2 h-[18px] w-[18px]" />
              <span>{t("nav.logout")}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {selectorMode && (
        <DashboardReportSelector
          mode={selectorMode}
          readyReports={readyReports}
          selectedReportKeys={selectedReportKeys}
          onSelectionChange={setSelectedReportKeys}
          onReportClick={handleReportClick}
          onConfirm={confirmReportSelection}
          onCancel={() => setSelectorMode(null)}
        />
      )}
    </aside>
    </>
  );
}
