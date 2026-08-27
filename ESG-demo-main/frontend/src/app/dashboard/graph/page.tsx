"use client";

import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  CircleDot,
  Command,
  Expand,
  Focus,
  GitBranch,
  HelpCircle,
  Layers3,
  LoaderCircle,
  Map as MapIcon,
  Maximize2,
  Minus,
  Network,
  Pause,
  Pin,
  Play,
  Plus,
  RefreshCcw,
  RotateCcw,
  Search,
  Settings2,
  PinOff,
  X,
} from "lucide-react";
import {
  Button,
  Drawer,
  Empty,
  Modal,
  Select,
  Skeleton,
  Switch,
  Tag,
  Tooltip,
} from "antd";
import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  Suspense,
} from "react";
import type { DisclosureGraphCanvasHandle } from "@/components/graph/DisclosureGraphCanvas";
import {
  deriveGraphDisplayData,
  disclosureStatus,
  graphFilterOptions,
  graphNodeSearchText,
  mergeDisclosureGraphs,
  mergeGraphExpansion,
  metricCode,
  normalizeNodeType,
  propertyString,
  propertyValue,
  reportId,
} from "@/features/graph/graphData";
import type {
  DisclosureGraphFilters,
  DisclosureGraphNode,
  DisclosureGraphResponse,
  GraphDisplayEdge,
  GraphDisplayData,
  GraphDisplayMode,
  GraphLayoutName,
} from "@/features/graph/types";
import { getStoredAuth } from "@/lib/auth";
import { apiService, type CompanySummary } from "@/lib/api";
import { errorSummary } from "@/lib/logger";
import { useFileStore, type File as ReportFile } from "@/store/useFileStore";

const DisclosureGraphCanvas = dynamic(
  () => import("@/components/graph/DisclosureGraphCanvas"),
  {
    ssr: false,
    loading: () => (
      <div className="grid h-full min-h-[480px] place-items-center rounded-2xl bg-slate-50" role="status">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <LoaderCircle className="h-4 w-4 animate-spin" />
          Starting graph canvas...
        </div>
      </div>
    ),
  },
);

const STANDALONE_OWNER = "__ready_reports__";
const DEFAULT_SELECTED_REPORT_COUNT = 1;
const SHOW_NON_BLOCKING_ERROR_BANNER = false;

type LoadState = "idle" | "loading" | "success" | "error";
type SelectedDetail =
  | { kind: "node"; node: DisclosureGraphNode }
  | { kind: "edge"; edge: GraphDisplayEdge; node: DisclosureGraphNode };

const EMPTY_FILTERS: DisclosureGraphFilters = {
  reportIds: [],
  frameworks: [],
  scopes: [],
  years: [],
  topics: [],
  statuses: [],
  collapsedMetricCodes: [],
};

function newestFirst(left: ReportFile, right: ReportFile) {
  const yearDifference = Number(right.report_year || 0) - Number(left.report_year || 0);
  if (yearDifference) return yearDifference;
  return Number(right.uploadedAtMs || 0) - Number(left.uploadedAtMs || 0);
}

function selectableReports(files: ReportFile[], companyId: string) {
  return files
    .filter(
      (file) =>
        file.status === "ready" &&
        Boolean(file.file_id) &&
        (companyId === STANDALONE_OWNER ? true : file.company_id === companyId),
    )
    .sort(newestFirst);
}

const REPORT_ID_SEPARATOR = "\u0000";

function reportIdsFromKey(key: string): string[] {
  return key ? key.split(REPORT_ID_SEPARATOR) : [];
}

function sameOrderedStrings(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function statusLabel(status: string) {
  if (status === "fully_disclosed") return "Disclosed";
  if (status === "partially_disclosed") return "Partially disclosed";
  if (status === "not_disclosed") return "Not disclosed";
  return status.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusTagColor(status: string) {
  if (status === "fully_disclosed") return "green";
  if (status === "partially_disclosed") return "orange";
  if (status === "not_disclosed") return "red";
  return "default";
}

function option(label: string) {
  return { label, value: label };
}

function compactFilterSelection(omittedValues: unknown[]) {
  const count = omittedValues.length;
  return `${count} ${count === 1 ? "item" : "items"} filtered`;
}

function nodeReportId(
  node: DisclosureGraphNode,
  graph: DisclosureGraphResponse,
): string {
  if (normalizeNodeType(node.type) === "report") return reportId(node);
  const adjacent = new Set<string>();
  graph.edges.forEach((edge) => {
    if (edge.source === node.id) adjacent.add(edge.target);
    if (edge.target === node.id) adjacent.add(edge.source);
  });
  const directReport = graph.nodes.find(
    (candidate) => adjacent.has(candidate.id) && normalizeNodeType(candidate.type) === "report",
  );
  if (directReport) return reportId(directReport);
  const disclosure = graph.nodes.find(
    (candidate) => adjacent.has(candidate.id) && normalizeNodeType(candidate.type) === "disclosure",
  );
  if (disclosure) {
    const disclosureNeighbors = new Set<string>();
    graph.edges.forEach((edge) => {
      if (edge.source === disclosure.id) disclosureNeighbors.add(edge.target);
      if (edge.target === disclosure.id) disclosureNeighbors.add(edge.source);
    });
    const report = graph.nodes.find(
      (candidate) => disclosureNeighbors.has(candidate.id) && normalizeNodeType(candidate.type) === "report",
    );
    if (report) return reportId(report);
  }
  return propertyString(node.properties, "file_id", "report_id");
}

function DetailDrawer({
  detail,
  open,
  evidenceLoading,
  onClose,
  onLoadEvidence,
  evidenceHref,
}: {
  detail: SelectedDetail | null;
  open: boolean;
  evidenceLoading: boolean;
  onClose: () => void;
  onLoadEvidence: () => void;
  evidenceHref?: string;
}) {
  const node = detail?.node;
  const type = node ? normalizeNodeType(node.type) : "other";
  const properties = node?.properties || {};
  const status = node && type === "disclosure" ? disclosureStatus(node) : "";
  const priorityKeys = [
    "metric_code",
    "metric_name",
    "disclosure_status",
    "status",
    "value",
    "unit",
    "year_values",
    "reasoning",
    "recommendation",
    "improvement_suggestions",
    "page_number",
    "page",
    "page_numbers",
    "topic",
    "definition",
    "filename",
    "report_year",
    "framework",
    "scope_key",
  ];
  const entries = Object.entries(properties)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .sort(([left], [right]) => {
      const leftIndex = priorityKeys.indexOf(left);
      const rightIndex = priorityKeys.indexOf(right);
      return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex);
    });

  return (
    <Drawer
      title={
        <div className="min-w-0 pr-6">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-700">
            {type === "other" ? "Graph item" : type}
          </div>
          <div className="truncate text-base font-semibold text-slate-900">{node?.label}</div>
        </div>
      }
      open={open}
      onClose={onClose}
      size={430}
      styles={{ body: { padding: 20 }, header: { borderBottomColor: "#e2e8f0" } }}
    >
      {node ? (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Tag color="cyan" bordered={false}>{type}</Tag>
            {status ? <Tag color={statusTagColor(status)}>{statusLabel(status)}</Tag> : null}
            {detail?.kind === "edge" ? <Tag bordered={false}>Disclosure relationship</Tag> : null}
          </div>

          <dl className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {entries.length ? entries.map(([key, value]) => (
              <div key={key} className="px-3.5 py-3">
                <dt className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  {key.replace(/_/g, " ")}
                </dt>
                <dd className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-800">
                  {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
                </dd>
              </div>
            )) : (
              <div className="px-3.5 py-5 text-sm text-slate-500">No additional properties.</div>
            )}
          </dl>

          {type !== "evidence" ? (
            <Button
              block
              icon={evidenceLoading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Expand className="h-4 w-4" />}
              loading={evidenceLoading}
              onClick={onLoadEvidence}
            >
              Load evidence neighbors
            </Button>
          ) : null}
          {evidenceHref ? (
            <a
              href={evidenceHref}
              className="flex h-10 w-full items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 px-4 text-sm font-medium text-emerald-800 transition-colors hover:bg-emerald-100"
            >
              Open report evidence
            </a>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}

function isEditableTarget(target: EventTarget | null): boolean {
  const element = target instanceof HTMLElement ? target : null;
  if (!element) return false;
  return Boolean(
    element.closest("input, textarea, select, [contenteditable='true'], [role='combobox']"),
  );
}

function GraphShortcutHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const groups = [
    {
      title: "Navigate",
      items: [
        ["S or /", "Search the current map"],
        ["[ / ] or Ctrl/Cmd + - / =", "Zoom out / in"],
        ["Ctrl/Cmd + 0", "Reset zoom to 100%"],
        ["Arrow keys", "Pan the canvas"],
        ["F", "Fit the full graph"],
        ["Alt + F", "Fullscreen graph page"],
        ["Shift + Alt + F", "Fullscreen canvas"],
      ],
    },
    {
      title: "Explore",
      items: [
        ["Shift + click", "Toggle multi-selection"],
        ["Shift + drag", "Box-select nodes"],
        ["0–9", "Set focus degree"],
        ["+ / -", "Expand / contract focus"],
        ["A", "Select all visible nodes"],
        ["Esc", "Clear focus and selection"],
      ],
    },
    {
      title: "Arrange",
      items: [
        ["P", "Pin selected nodes"],
        ["Alt + P", "Unpin selected nodes"],
        ["Space", "Pause / resume Force layout"],
        ["B", "Bump and rerun the layout"],
        ["Double click", "Select, center, and quick zoom"],
      ],
    },
  ];
  return (
    <Modal
      title={
        <div className="flex items-center gap-2">
          <Command className="h-4 w-4 text-emerald-700" />
          Graph shortcuts
        </div>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={680}
      centered
    >
      <div className="grid gap-3 pt-2 md:grid-cols-3">
        {groups.map((group) => (
          <section key={group.title} className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-slate-600">
              {group.title}
            </h3>
            <dl className="space-y-2.5">
              {group.items.map(([shortcut, description]) => (
                <div key={shortcut}>
                  <dt><kbd className="rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[10px] font-semibold text-slate-700 shadow-sm">{shortcut}</kbd></dt>
                  <dd className="mt-1 text-[11px] leading-4 text-slate-500">{description}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Modal>
  );
}

function GraphExplorationContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const files = useFileStore((state) => state.files);
  const loadFilesFromBackend = useFileStore((state) => state.loadFilesFromBackend);
  const canvasRef = useRef<DisclosureGraphCanvasHandle>(null);
  const pageRef = useRef<HTMLElement>(null);
  const canvasFrameRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const zoomReadoutRef = useRef<HTMLButtonElement>(null);
  const viewMenuRef = useRef<HTMLDivElement>(null);
  const [companies, setCompanies] = useState<CompanySummary[]>([]);
  const [ownerId, setOwnerId] = useState("");
  const [selectedReportIds, setSelectedReportIds] = useState<string[]>(() => [
    ...new Set(
      (searchParams.get("report_ids") || searchParams.get("file_id") || "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ]);
  const [appliedReportIds, setAppliedReportIds] = useState<string[]>(() => selectedReportIds);
  const [graph, setGraph] = useState<DisclosureGraphResponse | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [loadError, setLoadError] = useState("");
  const [usingReportFallback, setUsingReportFallback] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [filters, setFilters] = useState<DisclosureGraphFilters>(() => ({
    ...EMPTY_FILTERS,
    frameworks: (searchParams.get("framework") || "").split(",").filter(Boolean),
    years: (searchParams.get("year") || "").split(",").filter(Boolean),
    topics: (searchParams.get("topic") || "").split(",").filter(Boolean),
    statuses: (searchParams.get("status") || "").split(",").filter(Boolean),
  }));
  const [scope, setScope] = useState(searchParams.get("scope") || "");
  const [displayMode, setDisplayMode] = useState<GraphDisplayMode>(
    searchParams.get("mode") === "expanded" ? "expanded" : "overview",
  );
  const [layout, setLayout] = useState<GraphLayoutName>(() => {
    const requested = searchParams.get("layout");
    return requested === "hierarchical" || requested === "radial" ? requested : "force";
  });
  const [resetNonce, setResetNonce] = useState(0);
  const [selectedDetail, setSelectedDetail] = useState<SelectedDetail | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [searchIndex, setSearchIndex] = useState(-1);
  const [searchOpen, setSearchOpen] = useState(false);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [pinnedNodeIds, setPinnedNodeIds] = useState<string[]>([]);
  const [focusDegree, setFocusDegree] = useState<number | null>(null);
  const [layoutPaused, setLayoutPaused] = useState(false);
  const [showMinimap, setShowMinimap] = useState(false);
  const [viewMenuOpen, setViewMenuOpen] = useState(false);
  const [shortcutHelpOpen, setShortcutHelpOpen] = useState(false);
  const [fullscreenTarget, setFullscreenTarget] = useState<"page" | "canvas" | null>(null);
  const previousOwnerRef = useRef("");
  const requestedCompanyRef = useRef(
    searchParams.get("owner") === "reports"
      ? STANDALONE_OWNER
      : searchParams.get("company_id") || "",
  );
  const requestedStandaloneRef = useRef(
    searchParams.get("owner") === "reports" ||
      (!searchParams.get("company_id") && Boolean(searchParams.get("report_ids") || searchParams.get("file_id"))),
  );
  const initialNodeIdRef = useRef(searchParams.get("node_id") || "");
  const initialNodeAppliedRef = useRef(false);
  const defaultGroupsRevisionRef = useRef("");
  const retainedDisplayDataRef = useRef<GraphDisplayData | null>(null);
  const graphAvailableRef = useRef(false);
  graphAvailableRef.current = Boolean(graph);

  const readyReports = useMemo(
    () => files.filter((file) => file.status === "ready" && Boolean(file.file_id)).sort(newestFirst),
    [files],
  );
  const ownerReports = useMemo(
    () => selectableReports(files, ownerId),
    [files, ownerId],
  );
  const ownerReportIdsKey = useMemo(
    () => [...new Set(ownerReports.map((file) => file.file_id!).filter(Boolean))]
      .join(REPORT_ID_SEPARATOR),
    [ownerReports],
  );
  const ownerReportSnapshotKey = useMemo(
    () => JSON.stringify(ownerReports.map((file) => [
      file.file_id,
      file.status,
      file.backend_status,
      file.analysis_scope_key,
      file.scope_analysis_completed,
      file.scope_analysis_total,
      file.scope_analysis_all_done,
      file.company_analysis_version,
    ])),
    [ownerReports],
  );

  useEffect(() => {
    if (files.length === 0) void loadFilesFromBackend({ showLoading: false });
  }, [files.length, loadFilesFromBackend]);

  useEffect(() => {
    let active = true;
    setCatalogLoading(true);
    void apiService
      .getCompanies()
      .then((response) => {
        if (!active) return;
        const nextCompanies = Array.isArray(response.companies) ? response.companies : [];
        setCompanies(nextCompanies);
        const requestedCompany = requestedCompanyRef.current;
        setOwnerId((current) => {
          if (current) return current;
          if (requestedCompany === STANDALONE_OWNER) return STANDALONE_OWNER;
          if (requestedCompany && nextCompanies.some((company) => company.company_id === requestedCompany)) {
            return requestedCompany;
          }
          if (requestedStandaloneRef.current) return STANDALONE_OWNER;
          const preferred = nextCompanies.find(
            (company) =>
              !company.stale &&
              Number(company.report_count ?? company.report_ids?.length ?? 0) > 0,
          ) || nextCompanies.find(
            (company) => Number(company.report_count ?? company.report_ids?.length ?? 0) > 0,
          );
          return preferred?.company_id || STANDALONE_OWNER;
        });
      })
      .catch(() => {
        if (active) setOwnerId((current) => current || STANDALONE_OWNER);
      })
      .finally(() => {
        if (active) setCatalogLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!ownerId) return;
    const allOwnerReportIds = reportIdsFromKey(ownerReportIdsKey);
    if (files.length === 0) return;
    const allowed = new Set(allOwnerReportIds);
    const available = allOwnerReportIds
      .slice(0, DEFAULT_SELECTED_REPORT_COUNT)
      .filter(Boolean);
    const ownerChanged = Boolean(previousOwnerRef.current && previousOwnerRef.current !== ownerId);
    previousOwnerRef.current = ownerId;
    setSelectedReportIds((current) => {
      const validCurrent = [...new Set(current)].filter((reportId) => allowed.has(reportId));
      const next = !ownerChanged && validCurrent.length > 0 ? validCurrent : available;
      return sameOrderedStrings(current, next) ? current : next;
    });
    if (ownerChanged) {
      setScope("");
      setFilters(EMPTY_FILTERS);
      setSelectedDetail(null);
      setSelectedNodeIds([]);
      setPinnedNodeIds([]);
      setFocusDegree(null);
      setLayoutPaused(false);
    }
  }, [files.length, ownerId, ownerReportIdsKey]);

  useEffect(() => {
    if (sameOrderedStrings(appliedReportIds, selectedReportIds)) return;
    const timer = window.setTimeout(() => {
      if (graphAvailableRef.current) setLoadState("loading");
      setAppliedReportIds((current) => (
        sameOrderedStrings(current, selectedReportIds) ? current : selectedReportIds
      ));
    }, graphAvailableRef.current ? 220 : 0);
    return () => window.clearTimeout(timer);
  }, [appliedReportIds, selectedReportIds]);

  const installLoadedGraph = useCallback((nextGraph: DisclosureGraphResponse) => {
    const revisionKey = `${ownerId}:${nextGraph.graph_revision}`;
    if (defaultGroupsRevisionRef.current !== revisionKey) {
      defaultGroupsRevisionRef.current = revisionKey;
      const defaultCodes = graphFilterOptions(nextGraph).metricCodeCounts
        .filter(({ count }) => count > 1)
        .map(({ code }) => code);
      if (defaultCodes.length) {
        setFilters((current) => current.collapsedMetricCodes.length
          ? current
          : { ...current, collapsedMetricCodes: defaultCodes });
      }
    }
    setGraph(nextGraph);
  }, [ownerId]);

  const loadGraph = useCallback(async (signal?: AbortSignal) => {
    if (!ownerId) return;
    // Completion/version changes must invalidate an otherwise identical report
    // selection so a newly finished assessment replaces the cached graph.
    void ownerReportSnapshotKey;
    const ownerFileIds = new Set(reportIdsFromKey(ownerReportIdsKey));
    const validSelection = [...new Set(appliedReportIds)].filter((fileId) => ownerFileIds.has(fileId));
    const fallbackIds = validSelection.length
      ? validSelection
      : [...ownerFileIds].slice(0, DEFAULT_SELECTED_REPORT_COUNT);
    setLoadState("loading");
    setLoadError("");
    setUsingReportFallback(false);
    let companyError: unknown;

    if (ownerId !== STANDALONE_OWNER) {
      try {
        const companyGraph = await apiService.getCompanyDisclosureGraph(ownerId, {
          scope: scope || undefined,
          reportIds: fallbackIds,
          includeEvidence: false,
          evidenceLimit: 8,
          signal,
        });
        const disclosureCount = companyGraph.nodes.filter(
          (node) => normalizeNodeType(node.type) === "disclosure",
        ).length;
        if (companyGraph.nodes.length > 0 && disclosureCount > 0) {
          installLoadedGraph(companyGraph);
          setLoadState("success");
          return;
        }
        companyError = new Error("No per-report disclosures are available for this company yet.");
      } catch (error) {
        if (signal?.aborted) return;
        companyError = error;
      }
    }

    if (fallbackIds.length > 0) {
      const results = await Promise.allSettled(
        fallbackIds.map((fileId) =>
          apiService.getReportDisclosureGraph(fileId, {
            scope: scope || undefined,
            includeEvidence: false,
            evidenceLimit: 8,
            signal,
          }),
        ),
      );
      if (signal?.aborted) return;
      const successful = results
        .filter((result): result is PromiseFulfilledResult<DisclosureGraphResponse> => result.status === "fulfilled")
        .map((result) => result.value);
      if (successful.length > 0) {
        const fallbackGraph = mergeDisclosureGraphs(successful);
        installLoadedGraph(fallbackGraph);
        setUsingReportFallback(ownerId !== STANDALONE_OWNER);
        setLoadState("success");
        return;
      }
      const firstFailure = results.find(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      companyError ||= firstFailure?.reason;
    }

    setGraph(null);
    setLoadError(
      companyError
        ? errorSummary(companyError)
        : "No completed report assessments are available for graph exploration.",
    );
    setLoadState("error");
  }, [appliedReportIds, installLoadedGraph, ownerId, ownerReportIdsKey, ownerReportSnapshotKey, scope]);

  useEffect(() => {
    if (!ownerId) return;
    const controller = new AbortController();
    void loadGraph(controller.signal);
    return () => controller.abort();
  }, [loadGraph, ownerId]);

  const availableFilters = useMemo(
    () => graph ? graphFilterOptions(graph) : {
      frameworks: [], scopes: [], years: [], topics: [], statuses: [], metricCodes: [], metricCodeCounts: [],
    },
    [graph],
  );
  const availableScopes = useMemo(
    () => [...new Set([
      ...availableFilters.scopes,
      ...ownerReports
        .map((report) => String(report.analysis_scope_key || "").trim())
        .filter(Boolean),
    ])].sort((left, right) => left.localeCompare(right)),
    [availableFilters.scopes, ownerReports],
  );
  const filterSelectOptions = useMemo(() => ({
    frameworks: availableFilters.frameworks.map(option),
    scopes: availableScopes.map(option),
    years: availableFilters.years.map(option),
    topics: availableFilters.topics.map(option),
    statuses: availableFilters.statuses.map((value) => ({
      label: statusLabel(value),
      value,
    })),
  }), [availableFilters, availableScopes]);

  const reportOptions = useMemo(() => {
    const optionsById = new Map(
      ownerReports.map((file) => [file.file_id!, { label: file.name, value: file.file_id! }]),
    );
    if (graph) {
      graph.nodes
        .filter((node) => normalizeNodeType(node.type) === "report")
        .forEach((node) => {
          const value = reportId(node);
          if (value) optionsById.set(value, { label: node.label, value });
        });
    }
    return [...optionsById.values()];
  }, [graph, ownerReports]);

  const duplicateMetricCodes = useMemo(() => {
    return availableFilters.metricCodeCounts
      .filter(({ count }) => count > 1)
      .map(({ code, count }) => ({ label: `${code} (${count})`, value: code }));
  }, [availableFilters.metricCodeCounts]);

  const effectiveFilters = useMemo(() => {
    return {
      ...filters,
      reportIds: appliedReportIds,
      scopes: scope ? [scope] : [],
    };
  }, [appliedReportIds, filters, scope]);
  const deferredGraphFilters = useDeferredValue(effectiveFilters);
  const deferredDisplayMode = useDeferredValue(displayMode);
  const displayData = useMemo(
    () => graph
      ? deriveGraphDisplayData(graph, deferredGraphFilters, deferredDisplayMode)
      : null,
    [deferredDisplayMode, deferredGraphFilters, graph],
  );
  useEffect(() => {
    if (displayData?.nodes.length) retainedDisplayDataRef.current = displayData;
  }, [displayData]);
  const canvasDisplayData = loadState === "loading"
    ? retainedDisplayDataRef.current || displayData
    : displayData;

  const deferredSearchText = useDeferredValue(searchText);
  const deferredSearchQuery = useMemo(
    () => deferredSearchText.trim().toLocaleLowerCase(),
    [deferredSearchText],
  );
  const searchActive = Boolean(deferredSearchQuery);
  const searchableNodes = useMemo(
    () => searchActive && displayData
      ? displayData.nodes.map((node) => ({
          node,
          searchText: graphNodeSearchText(node),
        }))
      : [],
    [displayData, searchActive],
  );
  const searchResults = useMemo(() => {
    if (!deferredSearchQuery) return [];
    return searchableNodes
      .filter((entry) => entry.searchText.includes(deferredSearchQuery))
      .map((entry) => entry.node);
  }, [deferredSearchQuery, searchableNodes]);
  const searchResultWindow = useMemo(() => {
    const windowSize = 10;
    const activeIndex = searchIndex < 0 ? 0 : searchIndex;
    const start = Math.max(
      0,
      Math.min(
        Math.max(0, searchResults.length - windowSize),
        activeIndex - Math.floor(windowSize / 2),
      ),
    );
    return searchResults.slice(start, start + windowSize).map((node, offset) => ({
      node,
      index: start + offset,
    }));
  }, [searchIndex, searchResults]);

  useEffect(() => setSearchIndex(-1), [searchText, displayData]);

  useEffect(() => {
    if (!displayData || !selectedDetail) return;
    const stillVisible = selectedDetail.kind === "edge"
      ? displayData.edges.some((edge) => edge.disclosure_id === selectedDetail.node.id)
      : displayData.nodes.some((node) => node.id === selectedDetail.node.id);
    if (!stillVisible) setSelectedDetail(null);
  }, [displayData, selectedDetail]);

  useEffect(() => {
    if (!graph || initialNodeAppliedRef.current || !initialNodeIdRef.current) return;
    const node = graph.nodes.find((candidate) => candidate.id === initialNodeIdRef.current);
    if (!node) return;
    initialNodeAppliedRef.current = true;
    setSelectedDetail({ kind: "node", node });
    window.setTimeout(() => void canvasRef.current?.focusNode(node.id), 0);
  }, [displayData, graph]);

  useEffect(() => {
    if (!ownerId) return;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams();
      if (ownerId !== STANDALONE_OWNER) params.set("company_id", ownerId);
      else params.set("owner", "reports");
      if (selectedReportIds.length) params.set("report_ids", selectedReportIds.join(","));
      if (scope) params.set("scope", scope);
      if (filters.frameworks.length) params.set("framework", filters.frameworks.join(","));
      if (filters.years.length) params.set("year", filters.years.join(","));
      if (filters.topics.length) params.set("topic", filters.topics.join(","));
      if (filters.statuses.length) params.set("status", filters.statuses.join(","));
      if (displayMode !== "overview") params.set("mode", displayMode);
      if (layout !== "force") params.set("layout", layout);
      if (selectedDetail?.node.id) params.set("node_id", selectedDetail.node.id);
      router.replace(`/dashboard/graph?${params.toString()}`, { scroll: false });
    }, 150);
    return () => window.clearTimeout(timer);
  }, [
    displayMode,
    filters.frameworks,
    filters.statuses,
    filters.topics,
    filters.years,
    layout,
    ownerId,
    router,
    scope,
    selectedDetail,
    selectedReportIds,
  ]);

  const focusSearchResult = useCallback((index: number) => {
    if (!searchResults.length) return;
    const wrapped = (index + searchResults.length) % searchResults.length;
    setSearchIndex(wrapped);
    const node = searchResults[wrapped];
    void canvasRef.current?.focusNode(node.id);
    setSelectedDetail({ kind: "node", node });
    setSearchOpen(false);
  }, [searchResults]);

  const handleNodeSelect = useCallback((node: DisclosureGraphNode | null) => {
    setSelectedDetail(node ? { kind: "node", node } : null);
  }, []);
  const handleEdgeSelect = useCallback((edge: GraphDisplayEdge) => {
    const disclosure = edge.disclosure;
    if (disclosure) setSelectedDetail({ kind: "edge", edge, node: disclosure });
  }, []);
  const handleMetricGroupToggle = useCallback((code: string) => {
    setSelectedDetail(null);
    setFilters((current) => ({
      ...current,
      collapsedMetricCodes: current.collapsedMetricCodes.includes(code)
        ? current.collapsedMetricCodes.filter((item) => item !== code)
        : [...current.collapsedMetricCodes, code],
    }));
  }, []);
  const handleZoomChange = useCallback((value: number) => {
    const readout = zoomReadoutRef.current;
    if (!readout) return;
    const nextText = `${Math.round(value * 100)}%`;
    if (readout.textContent !== nextText) readout.textContent = nextText;
  }, []);
  const handleSelectionChange = useCallback((nodeIds: string[]) => {
    setSelectedNodeIds((current) => (
      sameOrderedStrings(current, nodeIds) ? current : nodeIds
    ));
    if (!nodeIds.length) setFocusDegree(null);
  }, []);
  const handlePinnedChange = useCallback((nodeIds: string[]) => {
    setPinnedNodeIds((current) => (
      sameOrderedStrings(current, nodeIds) ? current : nodeIds
    ));
  }, []);

  const positionStorageKey = useMemo(() => {
    const userId = getStoredAuth()?.userId || "anonymous";
    const owner = ownerId === STANDALONE_OWNER
      ? "reports"
      : `company:${ownerId}`;
    // v2 invalidates coordinates saved by the old hub-based topology. Keeping
    // those pins would prevent the corrected Kumu Force layout from ever
    // becoming visible, while new v2 drag positions still persist normally.
    return `esg-disclosure-graph-positions:v2:${userId}:${owner}:${scope || "default"}`;
  }, [ownerId, scope]);

  const resetLayout = useCallback(() => {
    try {
      window.localStorage.removeItem(positionStorageKey);
    } catch {
      // Reset still reruns the in-memory layout when storage is unavailable.
    }
    setPinnedNodeIds([]);
    setLayoutPaused(false);
    setLayout("force");
    setResetNonce((value) => value + 1);
  }, [positionStorageKey]);

  const loadEvidence = async () => {
    if (!graph || !selectedDetail?.node) return;
    const node = selectedDetail.node;
    setEvidenceLoading(true);
    try {
      let expansion: DisclosureGraphResponse;
      if (ownerId !== STANDALONE_OWNER && !usingReportFallback) {
        expansion = await apiService.getCompanyDisclosureGraphNeighbors(ownerId, {
          nodeId: node.id,
          scope: scope || undefined,
          reportIds: appliedReportIds,
          depth: 2,
          evidenceLimit: 8,
        });
      } else {
        const nodeType = normalizeNodeType(node.type);
        if (nodeType === "metric" && appliedReportIds.length > 1) {
          const results = await Promise.allSettled(
            appliedReportIds.map((fileId) =>
              apiService.getReportDisclosureGraphNeighbors(fileId, {
                nodeId: node.id,
                scope: scope || undefined,
                depth: 2,
                evidenceLimit: 8,
              }),
            ),
          );
          const successful = results
            .filter((result): result is PromiseFulfilledResult<DisclosureGraphResponse> => result.status === "fulfilled")
            .map((result) => result.value);
          if (!successful.length) {
            const failure = results.find(
              (result): result is PromiseRejectedResult => result.status === "rejected",
            );
            throw failure?.reason || new Error("No report-specific evidence is available.");
          }
          expansion = mergeDisclosureGraphs(successful, "Metric evidence across selected reports");
        } else {
          const fileId = nodeReportId(node, graph) || appliedReportIds[0];
          if (!fileId) throw new Error("This node is not connected to a report.");
          expansion = await apiService.getReportDisclosureGraphNeighbors(fileId, {
            nodeId: node.id,
            scope: scope || undefined,
            depth: 2,
            evidenceLimit: 8,
          });
        }
      }
      setGraph((current) => current ? mergeGraphExpansion(current, expansion) : expansion);
      setDisplayMode("expanded");
    } catch (error) {
      setLoadError(`Evidence could not be loaded: ${errorSummary(error)}`);
    } finally {
      setEvidenceLoading(false);
    }
  };

  const companyOptions = useMemo(
    () => [
      ...companies.map((company) => ({ label: company.company_name, value: company.company_id })),
      { label: "Standalone ready reports", value: STANDALONE_OWNER },
    ],
    [companies],
  );

  const handleOwnerChange = useCallback((value: string) => {
    if (graphAvailableRef.current) setLoadState("loading");
    setOwnerId(value);
  }, []);
  const handleReportSelectionChange = useCallback((values: string[]) => {
    const uniqueValues = [...new Set(values)].filter(Boolean);
    setSelectedReportIds(
      uniqueValues.length
        ? uniqueValues
        : reportIdsFromKey(ownerReportIdsKey).slice(0, DEFAULT_SELECTED_REPORT_COUNT),
    );
  }, [ownerReportIdsKey]);
  const handleScopeChange = useCallback((value?: string) => {
    if (graphAvailableRef.current) setLoadState("loading");
    setScope(value || "");
  }, []);

  const updateArrayFilter = <K extends "frameworks" | "years" | "topics" | "statuses" | "collapsedMetricCodes">(
    key: K,
    values: string[],
  ) => setFilters((current) => ({ ...current, [key]: values }));

  const hasActiveFilters = Boolean(
    scope || filters.frameworks.length || filters.years.length || filters.topics.length || filters.statuses.length,
  );

  const applyFocusDegree = useCallback((degree: number | null) => {
    const normalized = degree === null ? null : Math.max(0, Math.min(9, Math.floor(degree)));
    setFocusDegree(normalized);
    void canvasRef.current?.focusSelection(normalized);
  }, []);

  const adjustFocusDegree = useCallback((offset: number) => {
    if (!selectedNodeIds.length) return;
    applyFocusDegree(Math.max(0, Math.min(9, (focusDegree ?? 0) + offset)));
  }, [applyFocusDegree, focusDegree, selectedNodeIds.length]);

  const clearGraphInteraction = useCallback(() => {
    setSelectedDetail(null);
    setSearchOpen(false);
    setFocusDegree(null);
    void canvasRef.current?.clearSelection();
  }, []);

  const toggleLayoutPause = useCallback(() => {
    if (layout !== "force") return;
    if (layoutPaused) {
      setLayoutPaused(false);
      void canvasRef.current?.resumeLayout();
    } else {
      canvasRef.current?.pauseLayout();
      setLayoutPaused(true);
    }
  }, [layout, layoutPaused]);

  const toggleFullscreen = useCallback(async (target: "page" | "canvas") => {
    const element = target === "canvas" ? canvasFrameRef.current : pageRef.current;
    if (!element || typeof element.requestFullscreen !== "function") return;
    try {
      if (document.fullscreenElement === element) {
        await document.exitFullscreen();
      } else {
        if (document.fullscreenElement) await document.exitFullscreen();
        await element.requestFullscreen();
      }
    } catch {
      setLoadError("Fullscreen is not available in this browser context.");
    }
  }, []);

  useEffect(() => {
    const updateFullscreenTarget = () => {
      if (document.fullscreenElement === canvasFrameRef.current) setFullscreenTarget("canvas");
      else if (document.fullscreenElement === pageRef.current) setFullscreenTarget("page");
      else setFullscreenTarget(null);
    };
    document.addEventListener("fullscreenchange", updateFullscreenTarget);
    return () => document.removeEventListener("fullscreenchange", updateFullscreenTarget);
  }, []);

  useEffect(() => {
    if (!viewMenuOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!viewMenuRef.current?.contains(event.target as Node)) setViewMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [viewMenuOpen]);

  useEffect(() => {
    setLayoutPaused(false);
  }, [layout]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (isEditableTarget(event.target)) {
        if (event.key === "Escape") {
          setSearchOpen(false);
          searchInputRef.current?.blur();
        }
        return;
      }
      const key = event.key.toLowerCase();

      if (event.altKey && key === "f") {
        event.preventDefault();
        void toggleFullscreen(event.shiftKey ? "canvas" : "page");
        return;
      }
      if ((event.ctrlKey || event.metaKey) && (event.key === "+" || event.key === "=")) {
        event.preventDefault();
        void canvasRef.current?.zoomIn();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && (event.key === "-" || event.key === "_")) {
        event.preventDefault();
        void canvasRef.current?.zoomOut();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "0") {
        event.preventDefault();
        void canvasRef.current?.actualSize();
        return;
      }
      if (key === "s" || event.key === "/") {
        event.preventDefault();
        searchInputRef.current?.focus();
        setSearchOpen(true);
        return;
      }
      if (event.key === "?") {
        event.preventDefault();
        setShortcutHelpOpen(true);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setShortcutHelpOpen(false);
        setViewMenuOpen(false);
        clearGraphInteraction();
        return;
      }
      if (event.key === "[") {
        event.preventDefault();
        void canvasRef.current?.zoomOut();
        return;
      }
      if (event.key === "]") {
        event.preventDefault();
        void canvasRef.current?.zoomIn();
        return;
      }
      if (key === "f") {
        event.preventDefault();
        void canvasRef.current?.fitView();
        return;
      }
      if (event.key.startsWith("Arrow")) {
        event.preventDefault();
        const offsets: Record<string, [number, number]> = {
          ArrowLeft: [64, 0],
          ArrowRight: [-64, 0],
          ArrowUp: [0, 64],
          ArrowDown: [0, -64],
        };
        const [x, y] = offsets[event.key];
        void canvasRef.current?.panBy(x, y);
        return;
      }
      if (key === "a") {
        event.preventDefault();
        void canvasRef.current?.selectAll();
        return;
      }
      if (key === "p") {
        event.preventDefault();
        if (event.altKey) void canvasRef.current?.unpinSelection();
        else void canvasRef.current?.pinSelection();
        return;
      }
      if (key === "b") {
        event.preventDefault();
        setLayoutPaused(false);
        void canvasRef.current?.bumpLayout();
        return;
      }
      if (event.code === "Space" && layout === "force") {
        event.preventDefault();
        toggleLayoutPause();
        return;
      }
      if (/^[0-9]$/.test(event.key) && selectedNodeIds.length) {
        event.preventDefault();
        applyFocusDegree(Number(event.key));
        return;
      }
      if ((event.key === "+" || event.key === "=") && selectedNodeIds.length) {
        event.preventDefault();
        adjustFocusDegree(1);
        return;
      }
      if ((event.key === "-" || event.key === "_") && selectedNodeIds.length) {
        event.preventDefault();
        adjustFocusDegree(-1);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    adjustFocusDegree,
    applyFocusDegree,
    clearGraphInteraction,
    layout,
    selectedNodeIds.length,
    toggleFullscreen,
    toggleLayoutPause,
  ]);

  return (
    <main ref={pageRef} className="flex h-dvh min-h-[720px] w-full min-w-0 flex-col overflow-hidden bg-[#FAFBF9]">
      <header className="shrink-0 border-b border-slate-200 bg-white px-5 py-4 xl:px-7">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2.5">
              <div className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-950 text-white">
                <Network className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-xl font-semibold tracking-tight text-slate-950">Graph Exploration</h1>
                <p className="text-xs text-slate-500">Explore report-to-metric disclosure relationships</p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 p-1.5">
            <span className="pl-1.5 text-xs font-medium text-slate-600">Expanded</span>
            <Switch
              size="small"
              checked={displayMode === "expanded"}
              onChange={(checked) => setDisplayMode(checked ? "expanded" : "overview")}
              aria-label="Show disclosure nodes"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2" aria-label="Graph filters">
          <Select
            aria-label="Company"
            className="min-w-[190px]"
            loading={catalogLoading}
            value={ownerId || undefined}
            options={companyOptions}
            placeholder="Company"
            onChange={handleOwnerChange}
            showSearch
            optionFilterProp="label"
          />
          <Select
            aria-label="Reports"
            mode="multiple"
            maxTagCount={0}
            maxTagPlaceholder={compactFilterSelection}
            className="min-w-[220px] max-w-[360px]"
            value={selectedReportIds}
            options={reportOptions}
            placeholder="Select reports"
            onChange={handleReportSelectionChange}
            showSearch
            optionFilterProp="label"
          />
          <Select
            aria-label="Framework"
            mode="multiple"
            maxTagCount={0}
            maxTagPlaceholder={compactFilterSelection}
            className="min-w-[125px]"
            value={filters.frameworks}
            options={filterSelectOptions.frameworks}
            placeholder="Framework"
            onChange={(values) => updateArrayFilter("frameworks", values)}
          />
          <Select
            aria-label="Scope"
            allowClear
            className="min-w-[135px]"
            value={scope || undefined}
            options={filterSelectOptions.scopes}
            placeholder="Scope"
            onChange={handleScopeChange}
          />
          <Select
            aria-label="Year"
            mode="multiple"
            maxTagCount={0}
            maxTagPlaceholder={compactFilterSelection}
            className="min-w-[105px]"
            value={filters.years}
            options={filterSelectOptions.years}
            placeholder="Year"
            onChange={(values) => updateArrayFilter("years", values)}
          />
          <Select
            aria-label="Topic"
            mode="multiple"
            maxTagCount={0}
            maxTagPlaceholder={compactFilterSelection}
            className="min-w-[130px]"
            value={filters.topics}
            options={filterSelectOptions.topics}
            placeholder="Topic"
            onChange={(values) => updateArrayFilter("topics", values)}
          />
          <Select
            aria-label="Disclosure status"
            mode="multiple"
            maxTagCount={0}
            maxTagPlaceholder={compactFilterSelection}
            className="min-w-[170px]"
            value={filters.statuses}
            options={filterSelectOptions.statuses}
            placeholder="Disclosure status"
            onChange={(values) => updateArrayFilter("statuses", values)}
          />
          {duplicateMetricCodes.length ? (
            <Select
              aria-label="Collapsed metric groups"
              mode="multiple"
              maxTagCount={0}
              maxTagPlaceholder={compactFilterSelection}
              className="min-w-[175px]"
              value={filters.collapsedMetricCodes}
              options={duplicateMetricCodes}
              placeholder="Collapse metric groups"
              onChange={(values) => updateArrayFilter("collapsedMetricCodes", values)}
            />
          ) : null}
          {hasActiveFilters ? (
            <Button
              type="text"
              icon={<X className="h-4 w-4" />}
              onClick={() => {
                setScope("");
                setFilters((current) => ({ ...EMPTY_FILTERS, collapsedMetricCodes: current.collapsedMetricCodes }));
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>
      </header>

      <section className="relative min-h-0 flex-1 p-3 xl:p-4">
        <div
          ref={canvasFrameRef}
          className="relative h-full min-h-[520px] overflow-hidden rounded-2xl border border-[#C2CBC8] bg-[#FAFBF9] shadow-sm [&_button]:transition-[transform,background-color,color,box-shadow,border-color] [&_button]:duration-150 [&_button]:ease-out [&_button:active:not(:disabled)]:scale-[0.94] motion-reduce:[&_button]:transform-none motion-reduce:[&_button]:transition-none fullscreen:rounded-none fullscreen:border-0"
        >
          <div className="pointer-events-none absolute left-3 right-3 top-3 z-20 flex items-start justify-between gap-3">
            <div className="pointer-events-auto min-w-[260px] max-w-[480px] font-[Arial]">
              <div
                data-testid="graph-map-title"
                className="w-fit rounded-xl border border-[#C2CBC8] bg-[#FAFBF9]/95 p-[18px] shadow-md backdrop-blur"
              >
                <div className="text-xl font-extrabold tracking-[-0.02em] text-[#0D1D17]">
                  ESG Metrics Analysis Graph
                </div>
              </div>
            </div>

            <div
              data-testid="graph-right-controls"
              className="pointer-events-none flex min-w-0 flex-1 flex-col items-end gap-2 font-[Arial]"
            >
              <div data-testid="graph-search-control" className="pointer-events-auto relative w-full max-w-[480px]">
              <div className="flex items-center rounded-xl border border-[#C2CBC8] bg-[#FAFBF9]/95 px-3 shadow-md backdrop-blur">
                <Search className="h-4 w-4 shrink-0 text-slate-400" />
                <input
                  ref={searchInputRef}
                  value={searchText}
                  onFocus={() => setSearchOpen(true)}
                  onBlur={() => window.setTimeout(() => setSearchOpen(false), 120)}
                  onChange={(event) => {
                    setSearchText(event.target.value);
                    setSearchOpen(true);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "ArrowDown") {
                      event.preventDefault();
                      setSearchIndex((current) => searchResults.length
                        ? (current + 1 + searchResults.length) % searchResults.length
                        : -1);
                    } else if (event.key === "ArrowUp") {
                      event.preventDefault();
                      setSearchIndex((current) => searchResults.length
                        ? (current < 0 ? searchResults.length - 1 : (current - 1 + searchResults.length) % searchResults.length)
                        : -1);
                    } else if (event.key === "Enter" && searchResults.length) {
                      event.preventDefault();
                      focusSearchResult(searchIndex < 0 ? 0 : searchIndex);
                    } else if (event.key === "Escape") {
                      setSearchOpen(false);
                      event.currentTarget.blur();
                    }
                  }}
                  className="h-10 min-w-0 flex-1 bg-transparent px-2 text-sm outline-none placeholder:text-slate-400"
                  placeholder="Search reports, metric codes, or metric names"
                  role="combobox"
                  aria-label="Search graph"
                  aria-autocomplete="list"
                  aria-expanded={searchOpen && Boolean(searchText)}
                  aria-controls="graph-search-results"
                  aria-activedescendant={searchIndex >= 0 ? `graph-search-result-${searchIndex}` : undefined}
                />
                {searchText ? (
                  <>
                    <span className="whitespace-nowrap text-[11px] text-slate-500">
                      {searchResults.length
                        ? `${searchIndex >= 0 ? searchIndex + 1 : 0}/${searchResults.length}`
                        : "0 results"}
                    </span>
                    <button
                      type="button"
                      className="grid h-7 w-7 place-items-center rounded-md text-slate-500 hover:bg-slate-100 disabled:opacity-40"
                      disabled={!searchResults.length}
                      onClick={() => focusSearchResult(searchIndex < 0 ? searchResults.length - 1 : searchIndex - 1)}
                      aria-label="Previous search result"
                    ><ChevronUp className="h-4 w-4" /></button>
                    <button
                      type="button"
                      className="grid h-7 w-7 place-items-center rounded-md text-slate-500 hover:bg-slate-100 disabled:opacity-40"
                      disabled={!searchResults.length}
                      onClick={() => focusSearchResult(searchIndex + 1)}
                      aria-label="Next search result"
                    ><ChevronDown className="h-4 w-4" /></button>
                  </>
                ) : (
                  <kbd className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-400">S</kbd>
                )}
              </div>
              {searchOpen && searchText ? (
                <div
                  id="graph-search-results"
                  role="listbox"
                  className="absolute left-0 right-0 top-12 max-h-[360px] origin-top overflow-y-auto rounded-xl border border-slate-200 bg-white/98 p-1.5 shadow-xl backdrop-blur animate-in fade-in-0 slide-in-from-top-1 zoom-in-95 duration-150 motion-reduce:animate-none"
                >
                  {searchResults.length ? searchResultWindow.map(({ node, index }) => {
                    const nodeType = normalizeNodeType(node.type);
                    const status = nodeType === "disclosure" ? disclosureStatus(node) : "";
                    const code = nodeType === "metric" ? metricCode(node) : "";
                    const dotColor = status === "fully_disclosed"
                      ? "#008A5B"
                      : status === "partially_disclosed"
                        ? "#E98D00"
                        : status === "not_disclosed"
                          ? "#D94343"
                          : "#C2CBC8";
                    return (
                      <button
                        id={`graph-search-result-${index}`}
                        key={node.id}
                        type="button"
                        role="option"
                        aria-selected={searchIndex === index}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => focusSearchResult(index)}
                        className={`flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${searchIndex === index ? "bg-emerald-50" : "hover:bg-slate-50"}`}
                      >
                        <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: dotColor }} />
                        <span className="min-w-0 flex-1">
                          <span className="line-clamp-1 block text-xs font-medium text-slate-800">{node.label}</span>
                          <span className="mt-0.5 block text-[10px] uppercase tracking-wide text-slate-400">
                            {[nodeType, code].filter(Boolean).join(" · ")}
                          </span>
                        </span>
                      </button>
                    );
                  }) : (
                    <div className="px-3 py-5 text-center text-xs text-slate-500">No matching graph items.</div>
                  )}
                  {searchResults.length > 10 ? (
                    <div className="px-3 py-2 text-[10px] text-slate-400">Use Enter or the arrow buttons to visit all {searchResults.length} results.</div>
                  ) : null}
                </div>
              ) : null}
              </div>

            <div data-testid="graph-map-controls" className="pointer-events-auto grid grid-cols-[minmax(0,auto)_44px] items-start gap-2 font-[Arial]">
              <div className="contents">
              <div ref={viewMenuRef} className="relative">
                <div className="flex items-center gap-1 rounded-xl border border-[#C2CBC8] bg-[#FAFBF9]/95 p-1 shadow-md backdrop-blur">
                  <Tooltip title="Force layout"><button type="button" className={`grid h-8 w-8 place-items-center rounded-lg ${layout === "force" ? "bg-emerald-100 text-emerald-800" : "text-slate-500 hover:bg-slate-100"}`} onClick={() => setLayout("force")} aria-label="Force layout" aria-pressed={layout === "force"}><GitBranch className="h-4 w-4" /></button></Tooltip>
                  <Tooltip title="Hierarchical layout"><button type="button" className={`grid h-8 w-8 place-items-center rounded-lg ${layout === "hierarchical" ? "bg-emerald-100 text-emerald-800" : "text-slate-500 hover:bg-slate-100"}`} onClick={() => setLayout("hierarchical")} aria-label="Hierarchical layout" aria-pressed={layout === "hierarchical"}><Layers3 className="h-4 w-4" /></button></Tooltip>
                  <Tooltip title="Radial layout"><button type="button" className={`grid h-8 w-8 place-items-center rounded-lg ${layout === "radial" ? "bg-emerald-100 text-emerald-800" : "text-slate-500 hover:bg-slate-100"}`} onClick={() => setLayout("radial")} aria-label="Radial layout" aria-pressed={layout === "radial"}><CircleDot className="h-4 w-4" /></button></Tooltip>
                  {layout === "force" ? <Tooltip title={layoutPaused ? "Resume layout (Space)" : "Pause layout (Space)"}><button type="button" onClick={toggleLayoutPause} aria-label={layoutPaused ? "Resume layout" : "Pause layout"} aria-pressed={layoutPaused} className={`grid h-8 w-8 place-items-center rounded-lg ${layoutPaused ? "bg-amber-100 text-amber-800" : "text-slate-500 hover:bg-slate-100"}`}>{layoutPaused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}</button></Tooltip> : null}
                  <span className="mx-0.5 h-5 w-px bg-slate-200" />
                  <Tooltip title="View settings"><button type="button" onClick={() => setViewMenuOpen((value) => !value)} aria-label="View settings" aria-expanded={viewMenuOpen} className={`grid h-8 w-8 place-items-center rounded-lg ${viewMenuOpen ? "bg-slate-100 text-slate-800" : "text-slate-500 hover:bg-slate-100"}`}><Settings2 className="h-4 w-4" /></button></Tooltip>
                  <Tooltip title="Keyboard shortcuts (?)"><button type="button" onClick={() => setShortcutHelpOpen(true)} aria-label="Graph shortcuts" className="grid h-8 w-8 place-items-center rounded-lg text-slate-500 hover:bg-slate-100"><HelpCircle className="h-4 w-4" /></button></Tooltip>
                </div>
                {viewMenuOpen ? (
                  <div className="absolute right-0 top-12 z-30 w-64 origin-top-right rounded-xl border border-slate-200 bg-white/98 p-3 shadow-xl backdrop-blur animate-in fade-in-0 slide-in-from-top-1 zoom-in-95 duration-150 motion-reduce:animate-none">
                    <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">View settings</div>
                    <label className="flex items-center justify-between gap-3 py-1.5 text-xs text-slate-700"><span>Expanded disclosures</span><Switch size="small" checked={displayMode === "expanded"} onChange={(checked) => setDisplayMode(checked ? "expanded" : "overview")} /></label>
                    <label className="flex items-center justify-between gap-3 py-1.5 text-xs text-slate-700"><span>Overview minimap</span><Switch size="small" checked={showMinimap} onChange={setShowMinimap} /></label>
                    <div className="my-2 h-px bg-slate-100" />
                    <button type="button" onClick={() => { setViewMenuOpen(false); setLayoutPaused(false); void canvasRef.current?.bumpLayout(); }} className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs text-slate-600 hover:bg-slate-50"><RefreshCcw className="h-3.5 w-3.5" />Bump layout <kbd className="ml-auto text-[9px] text-slate-400">B</kbd></button>
                    <button type="button" onClick={() => { setViewMenuOpen(false); resetLayout(); }} className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs text-slate-600 hover:bg-slate-50"><RotateCcw className="h-3.5 w-3.5" />Reset layout</button>
                  </div>
                ) : null}
              </div>

              <div className="row-span-2 flex w-11 flex-col items-center gap-0.5 rounded-xl border border-[#C2CBC8] bg-[#FAFBF9]/95 p-1 shadow-md backdrop-blur">
                <Tooltip placement="left" title="Zoom in (])"><button type="button" onClick={() => void canvasRef.current?.zoomIn()} aria-label="Zoom in" className="grid h-8 w-8 place-items-center rounded-lg text-slate-600 hover:bg-slate-100"><Plus className="h-4 w-4" /></button></Tooltip>
                <button ref={zoomReadoutRef} type="button" onClick={() => void canvasRef.current?.actualSize()} aria-label="Actual size" className="w-9 rounded py-1 text-center text-[9px] font-semibold tabular-nums text-slate-500 hover:bg-slate-100">100%</button>
                <Tooltip placement="left" title="Zoom out ([)"><button type="button" onClick={() => void canvasRef.current?.zoomOut()} aria-label="Zoom out" className="grid h-8 w-8 place-items-center rounded-lg text-slate-600 hover:bg-slate-100"><Minus className="h-4 w-4" /></button></Tooltip>
                <span className="my-0.5 h-px w-6 bg-slate-200" />
                <Tooltip placement="left" title="Fit graph (F)"><button type="button" onClick={() => void canvasRef.current?.fitView()} aria-label="Fit graph" className="grid h-8 w-8 place-items-center rounded-lg text-slate-600 hover:bg-slate-100"><Maximize2 className="h-4 w-4" /></button></Tooltip>
                <Tooltip placement="left" title={fullscreenTarget === "canvas" ? "Exit fullscreen" : "Fullscreen canvas"}><button type="button" onClick={() => void toggleFullscreen("canvas")} aria-label="Toggle canvas fullscreen" aria-pressed={fullscreenTarget === "canvas"} className={`grid h-8 w-8 place-items-center rounded-lg ${fullscreenTarget === "canvas" ? "bg-emerald-100 text-emerald-800" : "text-slate-600 hover:bg-slate-100"}`}><Expand className="h-4 w-4" /></button></Tooltip>
              </div>
              </div>
              {selectedNodeIds.length ? (
                <div data-testid="graph-focus-toolbar" className="flex max-w-[420px] origin-bottom-right flex-wrap items-center justify-end gap-1 rounded-xl border border-[#61786F] bg-[#123F35]/95 p-1 text-white shadow-xl backdrop-blur animate-in fade-in-0 slide-in-from-bottom-1 zoom-in-95 duration-150 motion-reduce:animate-none" role="toolbar" aria-label="Selection actions">
                  <span className="px-2 text-[11px] font-semibold tabular-nums">{selectedNodeIds.length} selected</span>
                  <span className="h-5 w-px bg-white/15" />
                  <Tooltip title="Fit selection"><button type="button" onClick={() => void canvasRef.current?.fitSelection()} aria-label="Fit selection" className="grid h-8 w-8 place-items-center rounded-lg text-slate-200 hover:bg-white/10"><Focus className="h-4 w-4" /></button></Tooltip>
                  <Tooltip title={focusDegree === null ? "Focus one degree" : "Clear focus"}><button type="button" onClick={() => applyFocusDegree(focusDegree === null ? 1 : null)} aria-label="Toggle focus" aria-pressed={focusDegree !== null} className={`grid h-8 w-8 place-items-center rounded-lg ${focusDegree !== null ? "bg-emerald-500 text-white" : "text-slate-200 hover:bg-white/10"}`}><MapIcon className="h-4 w-4" /></button></Tooltip>
                  <button type="button" disabled={focusDegree === null || focusDegree <= 0} onClick={() => adjustFocusDegree(-1)} aria-label="Contract focus" className="grid h-8 w-7 place-items-center rounded-lg text-slate-200 hover:bg-white/10 disabled:opacity-30"><Minus className="h-3.5 w-3.5" /></button>
                  <span className="min-w-7 text-center text-[10px] font-semibold tabular-nums text-slate-300">{focusDegree === null ? "off" : focusDegree}</span>
                  <button type="button" onClick={() => adjustFocusDegree(1)} aria-label="Expand focus" className="grid h-8 w-7 place-items-center rounded-lg text-slate-200 hover:bg-white/10"><Plus className="h-3.5 w-3.5" /></button>
                  <span className="h-5 w-px bg-white/15" />
                  <Tooltip title="Pin selection (P)"><button type="button" onClick={() => void canvasRef.current?.pinSelection()} aria-label="Pin selection" className="grid h-8 w-8 place-items-center rounded-lg text-slate-200 hover:bg-white/10"><Pin className="h-4 w-4" /></button></Tooltip>
                  <Tooltip title="Unpin selection (Alt+P)"><button type="button" onClick={() => void canvasRef.current?.unpinSelection()} aria-label="Unpin selection" className="grid h-8 w-8 place-items-center rounded-lg text-slate-200 hover:bg-white/10"><PinOff className="h-4 w-4" /></button></Tooltip>
                  {pinnedNodeIds.length ? <span className="px-1 text-[9px] text-emerald-300">{pinnedNodeIds.length} pinned</span> : null}
                  <button type="button" onClick={clearGraphInteraction} aria-label="Clear selection" className="grid h-8 w-8 place-items-center rounded-lg text-slate-300 hover:bg-white/10"><X className="h-4 w-4" /></button>
                </div>
              ) : null}
            </div>
            </div>
          </div>

          {(loadState === "loading" || catalogLoading)
            && !(canvasDisplayData && canvasDisplayData.nodes.length > 0 && graph) ? (
            <div className="absolute inset-0 z-[5] grid place-items-center bg-white px-8 pt-16" role="status">
              <div className="w-full max-w-xl text-center">
                <LoaderCircle className="mx-auto mb-3 h-7 w-7 animate-spin text-emerald-700" />
                <p className="mb-5 text-sm font-medium text-slate-700">Building the disclosure graph...</p>
                <Skeleton active paragraph={{ rows: 4 }} />
              </div>
            </div>
          ) : loadState === "error" ? (
            <div className="absolute inset-0 z-[5] grid place-items-center bg-white p-8">
              <div className="max-w-lg text-center">
                <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-amber-50 text-amber-700"><AlertCircle className="h-6 w-6" /></div>
                <h2 className="mb-2 text-base font-semibold text-slate-900">Graph data is not available</h2>
                <p className="mb-5 text-sm leading-6 text-slate-500">{loadError}</p>
                <Button icon={<RefreshCcw className="h-4 w-4" />} onClick={() => void loadGraph()}>Try again</Button>
              </div>
            </div>
          ) : canvasDisplayData && canvasDisplayData.nodes.length > 0 && graph ? (
            <DisclosureGraphCanvas
              ref={canvasRef}
              data={canvasDisplayData}
              graphRevision={graph.graph_revision}
              layout={layout}
              positionStorageKey={positionStorageKey}
              resetNonce={resetNonce}
              onNodeSelect={handleNodeSelect}
              onEdgeSelect={handleEdgeSelect}
              onMetricGroupToggle={handleMetricGroupToggle}
              onZoomChange={handleZoomChange}
              onSelectionChange={handleSelectionChange}
              onPinnedChange={handlePinnedChange}
              onFocusDegreeChange={setFocusDegree}
              onLayoutPausedChange={setLayoutPaused}
              layoutPaused={layoutPaused}
              showMinimap={showMinimap}
            />
          ) : (
            <div className="absolute inset-0 grid place-items-center bg-white pt-14">
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  hasActiveFilters
                    ? "No disclosure relationships match the active filters."
                    : readyReports.length
                      ? "The selected reports do not have completed assessments."
                      : "Upload and analyze a report to begin graph exploration."
                }
              >
                {hasActiveFilters ? <Button onClick={() => { setScope(""); setFilters(EMPTY_FILTERS); }}>Clear filters</Button> : null}
              </Empty>
            </div>
          )}

          {(loadState === "loading" || catalogLoading)
            && canvasDisplayData && canvasDisplayData.nodes.length > 0 && graph ? (
            <div
              data-testid="graph-refresh-indicator"
              className="pointer-events-none absolute left-1/2 top-3 z-[19] -translate-x-1/2 rounded-full border border-emerald-200 bg-white/95 px-3 py-1.5 text-xs font-medium text-emerald-800 shadow-md backdrop-blur"
              role="status"
            >
              <span className="flex items-center gap-2">
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                Updating graph...
              </span>
            </div>
          ) : null}

          {loadState === "success" && displayData ? (
            <div className="pointer-events-none absolute bottom-3 left-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-col gap-2">
              {graph?.truncated ? (
                <div className="pointer-events-auto rounded-lg border border-amber-200 bg-amber-50/95 px-3 py-2 text-xs text-amber-800 shadow-sm">
                  This graph was truncated for performance. Expand evidence from a node to load more.
                </div>
              ) : null}
              {usingReportFallback ? (
                <div className="pointer-events-auto rounded-lg border border-blue-200 bg-blue-50/95 px-3 py-2 text-xs text-blue-800 shadow-sm">
                  Showing merged per-report assessments because a company-level per-report graph was unavailable.
                </div>
              ) : null}
              {SHOW_NON_BLOCKING_ERROR_BANNER && loadError ? (
                <button type="button" onClick={() => setLoadError("")} className="pointer-events-auto flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/95 px-3 py-2 text-left text-xs text-amber-800 shadow-sm">
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {loadError}
                </button>
              ) : null}
              <div data-testid="graph-status-legend" className="pointer-events-auto flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-[#C2CBC8] bg-[#FAFBF9]/95 px-3 py-2 font-[Arial] text-xs font-bold text-[#1B2823] shadow-md backdrop-blur">
                <span>{displayData.nodes.length} nodes | {displayData.edges.length} relationships | {displayData.underlyingDisclosureCount} disclosures</span>
                <span className="flex items-center gap-1.5"><i className="h-[7px] w-5 rounded bg-[#008A5B]" />Disclosed</span>
                <span className="flex items-center gap-1.5"><i className="h-0.5 w-5 border-t-[6px] border-dashed border-[#E98D00]" />Partially disclosed</span>
                <span className="flex items-center gap-1.5"><i className="h-0.5 w-5 border-t-4 border-dotted border-[#D94343]" />Not disclosed</span>
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <DetailDrawer
        detail={selectedDetail}
        open={Boolean(selectedDetail)}
        evidenceLoading={evidenceLoading}
        onClose={clearGraphInteraction}
        onLoadEvidence={() => void loadEvidence()}
        evidenceHref={(() => {
          if (!graph || !selectedDetail?.node) return undefined;
          const fileId = nodeReportId(selectedDetail.node, graph);
          const rawPage = propertyValue(
            selectedDetail.node.properties,
            "page_number",
            "page",
            "page_numbers",
          );
          const page = Array.isArray(rawPage) ? rawPage[0] : rawPage;
          if (!fileId || page === undefined || page === null || page === "") return undefined;
          const params = new URLSearchParams({ file_id: fileId, page: String(page) });
          if (scope) params.set("scope", scope);
          return `/cross-analysis/evidence?${params.toString()}`;
        })()}
      />
      <GraphShortcutHelp open={shortcutHelpOpen} onClose={() => setShortcutHelpOpen(false)} />
    </main>
  );
}

export default function GraphExplorationPage() {
  return (
    <Suspense
      fallback={
        <main className="grid h-dvh min-h-[720px] w-full place-items-center bg-[#FAFBF9]" role="status">
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <LoaderCircle className="h-4 w-4 animate-spin" />
            Loading Graph Exploration...
          </div>
        </main>
      }
    >
      <GraphExplorationContent />
    </Suspense>
  );
}
