"use client";

import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  CanvasEvent,
  EdgeEvent,
  Graph,
  GraphEvent,
  NodeEvent,
  type EdgeData,
  type GraphData,
  type IPointerEvent,
  type NodeData,
} from "@antv/g6";
import {
  disclosureStatus,
  graphNeighborhood,
  metricCode,
  normalizeDisclosureStatus,
  normalizeNodeType,
  parseStoredGraphPositions,
  propertyString,
} from "@/features/graph/graphData";
import type {
  GraphDisplayData,
  GraphDisplayEdge,
  GraphDisplayNode,
  GraphLayoutName,
  GraphPosition,
} from "@/features/graph/types";

export interface DisclosureGraphCanvasHandle {
  zoomIn: () => Promise<void>;
  zoomOut: () => Promise<void>;
  fitView: () => Promise<void>;
  actualSize: () => Promise<void>;
  focusNode: (nodeId: string) => Promise<void>;
  fitSelection: () => Promise<void>;
  panBy: (x: number, y: number) => Promise<void>;
  selectAll: () => Promise<void>;
  clearSelection: () => Promise<void>;
  focusSelection: (degree: number | null) => Promise<void>;
  pinSelection: () => Promise<void>;
  unpinSelection: () => Promise<void>;
  pauseLayout: () => void;
  resumeLayout: () => Promise<void>;
  bumpLayout: () => Promise<void>;
}

interface DisclosureGraphCanvasProps {
  data: GraphDisplayData;
  graphRevision: string;
  layout: GraphLayoutName;
  positionStorageKey: string;
  resetNonce: number;
  onNodeSelect: (node: GraphDisplayNode | null) => void;
  onEdgeSelect: (edge: GraphDisplayEdge) => void;
  onMetricGroupToggle: (metricCode: string) => void;
  onZoomChange?: (zoom: number) => void;
  onSelectionChange?: (nodeIds: string[]) => void;
  onPinnedChange?: (nodeIds: string[]) => void;
  onFocusDegreeChange?: (degree: number | null) => void;
  onLayoutPausedChange?: (paused: boolean) => void;
  layoutPaused?: boolean;
  showMinimap?: boolean;
}

interface HoverCard {
  title: string;
  subtitle: string;
  body?: string;
  width: number;
  padding: number;
  x: number;
  y: number;
}

const KUMU_COLORS = {
  background: "#FAFBF9",
  text: "#081D15",
  report: "#123F35",
  reportSelected: "#76AA9C",
  metricBorder: "#526E63",
  elementBorder: "#61786F",
  disclosed: "#008A5B",
  partial: "#E98D00",
  missing: "#D94343",
  connection: "#C2CBC8",
} as const;

const KUMU_SPRING_LENGTH = 560;
const KUMU_GRAVITY = 0.00003;
const KUMU_VIEW_PADDING: [number, number, number, number] = [36, 36, 36, 36];

export interface CircleKeyShapeGeometry {
  x: number;
  y: number;
  size?: number | [number, number];
  lineWidth?: number;
}

export interface CircleKeyShapeBounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  width: number;
  height: number;
  center: [number, number];
}

export function combineCircleKeyShapeBounds(
  shapes: CircleKeyShapeGeometry[],
): CircleKeyShapeBounds | null {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const shape of shapes) {
    if (!Number.isFinite(shape.x) || !Number.isFinite(shape.y)) continue;
    const rawSize = Array.isArray(shape.size)
      ? Math.min(Number(shape.size[0]), Number(shape.size[1]))
      : Number(shape.size);
    const diameter = Number.isFinite(rawSize) && rawSize > 0 ? rawSize : 56;
    const rawLineWidth = Number(shape.lineWidth);
    const lineWidth = Number.isFinite(rawLineWidth) && rawLineWidth > 0 ? rawLineWidth : 0;
    const radius = diameter / 2 + lineWidth / 2;
    minX = Math.min(minX, shape.x - radius);
    minY = Math.min(minY, shape.y - radius);
    maxX = Math.max(maxX, shape.x + radius);
    maxY = Math.max(maxY, shape.y + radius);
  }
  if (![minX, minY, maxX, maxY].every(Number.isFinite)) return null;
  const width = Math.max(1, maxX - minX);
  const height = Math.max(1, maxY - minY);
  return {
    minX,
    minY,
    maxX,
    maxY,
    width,
    height,
    center: [minX + width / 2, minY + height / 2],
  };
}

export function calculateCircleKeyShapeFit(
  bounds: CircleKeyShapeBounds,
  viewportSize: [number, number],
  padding: [number, number, number, number] = KUMU_VIEW_PADDING,
  zoomRange: [number, number] = [0.12, 4],
) {
  const [top, right, bottom, left] = padding;
  const innerWidth = Math.max(1, viewportSize[0] - left - right);
  const innerHeight = Math.max(1, viewportSize[1] - top - bottom);
  const rawZoom = Math.min(innerWidth / bounds.width, innerHeight / bounds.height);
  const minZoom = Number.isFinite(zoomRange[0]) ? zoomRange[0] : 0.12;
  const maxZoom = Number.isFinite(zoomRange[1]) ? zoomRange[1] : 4;
  return {
    zoom: Math.min(maxZoom, Math.max(minZoom, rawZoom)),
    graphCenter: bounds.center,
    viewportCenter: [left + innerWidth / 2, top + innerHeight / 2] as [number, number],
  };
}

const GRAPH_MOTION = {
  duration: 180,
  easing: "cubic-bezier(0.22, 1, 0.36, 1)",
} as const;

const NODE_STATE_MOTION = [
  {
    fields: ["opacity"],
    duration: GRAPH_MOTION.duration,
    easing: GRAPH_MOTION.easing,
  },
  {
    // `size` documents the state intent while `r` is the rendered circle field
    // that G6 can interpolate for circle nodes.
    fields: ["size", "r", "fill", "stroke", "lineWidth"],
    shape: "key",
    duration: GRAPH_MOTION.duration,
    easing: GRAPH_MOTION.easing,
  },
  {
    fields: ["opacity"],
    shape: "label",
    duration: GRAPH_MOTION.duration,
    easing: GRAPH_MOTION.easing,
  },
  {
    fields: ["stroke", "lineWidth", "strokeOpacity"],
    shape: "halo",
    duration: GRAPH_MOTION.duration,
    easing: GRAPH_MOTION.easing,
  },
];

const EDGE_STATE_MOTION = [
  {
    fields: ["stroke", "lineWidth", "strokeOpacity"],
    shape: "key",
    duration: GRAPH_MOTION.duration,
    easing: GRAPH_MOTION.easing,
  },
  {
    fields: ["stroke", "lineWidth", "strokeOpacity"],
    shape: "halo",
    duration: GRAPH_MOTION.duration,
    easing: GRAPH_MOTION.easing,
  },
];

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

const REPORT_SIZE_BY_LABEL: Record<string, number> = {
  "Dell Technologies FY24 ESG Report": 275,
  "Dell Technologies FY23 ESG Report": 266,
  "Dell Technologies FY23 SASB Index": 228,
  "Dell FY26 Impact By the Numbers": 204,
  "Dell Technologies FY25 Impact By The Numbers": 196,
  "Dell FY25 CDP Corporate Questionnaire": 172,
};

const REPORT_FONT_SIZE_BY_LABEL: Record<string, number> = {
  "Dell Technologies FY24 ESG Report": 34,
  "Dell Technologies FY23 ESG Report": 34,
  "Dell Technologies FY23 SASB Index": 33,
  "Dell FY26 Impact By the Numbers": 32,
  "Dell Technologies FY25 Impact By The Numbers": 32,
  "Dell FY25 CDP Corporate Questionnaire": 31,
};

const REPORT_BORDER_WIDTH_BY_LABEL: Record<string, number> = {
  "Dell Technologies FY24 ESG Report": 8,
  "Dell Technologies FY23 ESG Report": 8,
  "Dell Technologies FY23 SASB Index": 7,
  "Dell FY26 Impact By the Numbers": 7,
  "Dell Technologies FY25 Impact By The Numbers": 7,
  "Dell FY25 CDP Corporate Questionnaire": 7,
};

const SELECTION_STATES = new Set([
  "selected",
  "selectionNeighbor",
  "selectionInactive",
]);
const FOCUS_STATES = new Set(["focusActive", "focusInactive"]);

function nodeSelectionIds(graph: Graph): string[] {
  return graph
    .getNodeData()
    .filter((node) => graph.getElementState(node.id).includes("selected"))
    .map((node) => String(node.id));
}

function withoutStates(states: string[], excluded: Set<string>): string[] {
  return states.filter((state) => !excluded.has(state));
}

function labelLevelForZoom(
  zoom: number,
  dense: boolean,
): "hidden" | "compact" | "full" {
  // Preserve the supplied Kumu `font-cutoff: 0` for normal maps. Very large
  // evidence views progressively reduce non-report labels to keep interaction fluid.
  if (dense && zoom < 0.62) return "hidden";
  if (dense && zoom < 1.05) return "compact";
  return "full";
}

/**
 * Kumu's `text-overflow: wrap N` does not wrap to a pixel width. It keeps
 * scanning after character N and breaks at the next space. Pre-inserting the
 * line breaks gives G6 Canvas the same label geometry.
 */
export function wrapKumuLabel(value: string, characters: number): string {
  let remaining = String(value || "").replace(/\s+/g, " ").trim();
  if (!remaining || characters <= 0) return remaining;
  const lines: string[] = [];
  while (remaining.length > characters) {
    const breakAt = remaining.indexOf(" ", characters);
    if (breakAt < 0) break;
    lines.push(remaining.slice(0, breakAt));
    remaining = remaining.slice(breakAt + 1).trimStart();
  }
  if (remaining) lines.push(remaining);
  return lines.join("\n");
}

function reportLabelOverride<T>(map: Record<string, T>, label: string): T | undefined {
  const normalized = label
    .replace(/\.(?:pdf|docx?|pptx?)$/i, "")
    .trim()
    .replace(/\s+/g, " ")
    .toLocaleLowerCase();
  const key = Object.keys(map).find(
    (candidate) => candidate.toLocaleLowerCase() === normalized,
  );
  return key ? map[key] : undefined;
}

function reportSize(node?: GraphDisplayNode): number {
  if (!node) return 195;
  const exact = reportLabelOverride(REPORT_SIZE_BY_LABEL, node.label);
  if (exact) return exact;
  const properties = node.properties;
  const raw = Number(
    properties.overall_score ??
      properties.compliance_score ??
      properties.score ??
      Number.NaN,
  );
  if (!Number.isFinite(raw)) return 195;
  const normalized = Math.min(100, Math.max(0, raw <= 1 ? raw * 100 : raw));
  return Math.round(172 + normalized * 1.03);
}

function statusColor(value: unknown) {
  switch (normalizeDisclosureStatus(value)) {
    case "fully_disclosed":
      return KUMU_COLORS.disclosed;
    case "partially_disclosed":
      return KUMU_COLORS.partial;
    case "not_disclosed":
      return KUMU_COLORS.missing;
    default:
      return KUMU_COLORS.connection;
  }
}

function layoutOptions(
  layout: GraphLayoutName,
  nodeCount = 0,
  motionEnabled = true,
) {
  const liveLayout = motionEnabled && nodeCount <= 350;
  if (layout === "hierarchical") {
    return {
      type: "dagre" as const,
      rankdir: "LR" as const,
      ranksep: 120,
      nodesep: 48,
      animation: liveLayout,
    };
  }
  if (layout === "radial") {
    return {
      type: "radial" as const,
      unitRadius: 135,
      preventOverlap: true,
      nodeSize: 72,
      animation: liveLayout,
    };
  }
  return {
    type: "d3-force" as const,
    linkDistance: KUMU_SPRING_LENGTH,
    edgeStrength: 0.06,
    nodeStrength: -1900,
    centerStrength: KUMU_GRAVITY,
    // Kumu gravity pulls every item toward the true map center. D3's center
    // force only translates the aggregate centroid, so x/y forces are needed
    // for equivalent per-node attraction.
    x: { strength: KUMU_GRAVITY },
    y: { strength: KUMU_GRAVITY },
    preventOverlap: true,
    collideStrength: 1,
    alphaMin: 0.002,
    alphaDecay: nodeCount > 500 ? 0.045 : 0.0228,
    velocityDecay: 0.28,
    collideIterations: nodeCount > 500 ? 1 : 2,
    iterations: nodeCount > 500 ? 220 : 320,
    // In G6 iterative layouts `animation` means live onTick updates rather
    // than a cosmetic tween. Keeping it on for normal graphs is what makes
    // Force settle and react to dragging continuously instead of jumping.
    animation: liveLayout,
  };
}

function edgeCurvature(edge?: GraphDisplayEdge): number | null {
  const rawTags = edge?.properties.tags;
  const tags = Array.isArray(rawTags) ? rawTags.join(" ") : String(rawTags || "");
  if (/(?:^|[\s,])curve-0(?=$|[\s,])/i.test(tags)) return 0;
  const match = tags.match(/(?:^|[\s,])curve-([np])(10|15|20|25|30|40|50|60|70)(?=$|[\s,])/i);
  if (!match) return null;
  const value = Number(match[2]) / 100;
  return match[1].toLowerCase() === "n" ? -value : value;
}

function toG6Data(data: GraphDisplayData): GraphData {
  return {
    nodes: data.nodes.map((node) => ({
      id: node.id,
      data: {
        nodeType: normalizeNodeType(node.type),
        graphNode: node,
        shortLabel: node.short_label || node.label,
        fullLabel: node.display_label || node.label,
      },
    })),
    edges: data.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      data: {
        graphEdge: edge,
        curvature: edgeCurvature(edge),
        status:
          normalizeDisclosureStatus(
            edge.properties.disclosure_status ??
              edge.properties.status ??
              edge.disclosure?.properties.disclosure_status ??
              edge.disclosure?.properties.status,
          ) || edge.label || "",
      },
    })),
  };
}

function hasExplicitCurveTag(edge: GraphDisplayEdge): boolean {
  const rawTags = edge.properties.tags;
  const tags = Array.isArray(rawTags) ? rawTags.join(" ") : String(rawTags || "");
  return /(?:^|[\s,])curve-(?:0|[np](?:10|15|20|25|30|40|50|60|70))(?=$|[\s,])/i.test(tags);
}

function automaticParallelEdgeIds(edges: GraphDisplayEdge[]): string[] {
  const pairs = new Map<string, string[]>();
  for (const edge of edges) {
    if (hasExplicitCurveTag(edge)) continue;
    const pair = edge.source < edge.target
      ? `${edge.source}\u0000${edge.target}`
      : `${edge.target}\u0000${edge.source}`;
    const ids = pairs.get(pair);
    if (ids) ids.push(edge.id);
    else pairs.set(pair, [edge.id]);
  }
  return [...pairs.values()].filter((ids) => ids.length > 1).flat();
}

function graphNodeFromDatum(datum: NodeData): GraphDisplayNode | undefined {
  return datum.data?.graphNode as GraphDisplayNode | undefined;
}

function graphEdgeFromDatum(datum: EdgeData): GraphDisplayEdge | undefined {
  return datum.data?.graphEdge as GraphDisplayEdge | undefined;
}

function pointFromGraph(graph: Graph, id: string): GraphPosition | null {
  try {
    const point = graph.getElementPosition(id);
    if (Number.isFinite(point[0]) && Number.isFinite(point[1])) {
      return { x: point[0], y: point[1] };
    }
  } catch {
    // The node may have disappeared while a filter update was rendering.
  }
  return null;
}

async function fitCircleKeyShapes(graph: Graph): Promise<boolean> {
  const shapes: CircleKeyShapeGeometry[] = [];
  for (const node of graph.getNodeData()) {
    const id = String(node.id);
    const point = pointFromGraph(graph, id);
    if (!point) continue;
    try {
      const style = graph.getElementRenderStyle(id);
      shapes.push({
        x: point.x,
        y: point.y,
        size: style.size,
        lineWidth: Number(style.lineWidth || 0),
      });
    } catch {
      shapes.push({ x: point.x, y: point.y, size: 56, lineWidth: 0 });
    }
  }
  const bounds = combineCircleKeyShapeBounds(shapes);
  if (!bounds) return false;
  const rawRange = graph.getZoomRange();
  const zoomRange: [number, number] = Array.isArray(rawRange)
    ? [Number(rawRange[0] ?? 0.12), Number(rawRange[1] ?? 4)]
    : [0.12, 4];
  const fit = calculateCircleKeyShapeFit(
    bounds,
    graph.getSize(),
    KUMU_VIEW_PADDING,
    zoomRange,
  );
  await graph.zoomTo(fit.zoom, false, graph.getCanvasCenter());
  const currentCenter = graph.getViewportByCanvas(fit.graphCenter);
  await graph.translateBy([
    fit.viewportCenter[0] - currentCenter[0],
    fit.viewportCenter[1] - currentCenter[1],
  ], false);
  return true;
}

function eventTargetId(event: IPointerEvent): string {
  return String((event.target as { id?: string }).id || "");
}

function hoverPoint(
  event: IPointerEvent,
  container: HTMLElement,
  cardWidth = 440,
): { x: number; y: number } {
  const pointer = event as IPointerEvent & { clientX?: number; clientY?: number };
  const bounds = container.getBoundingClientRect();
  const rawX = Number(pointer.clientX ?? bounds.left + bounds.width / 2) - bounds.left + 16;
  const rawY = Number(pointer.clientY ?? bounds.top + bounds.height / 2) - bounds.top + 16;
  return {
    x: Math.max(12, Math.min(bounds.width - cardWidth - 12, rawX)),
    y: Math.max(12, Math.min(bounds.height - 180, rawY)),
  };
}

function nodeHoverCard(node: GraphDisplayNode, point: GraphPosition): HoverCard {
  const type = normalizeNodeType(node.type);
  const code = type === "metric" ? metricCode(node) : "";
  const status = type === "disclosure" ? disclosureStatus(node) : "";
  const relationCount = Number(node.properties.relationship_count ?? node.properties.degree ?? 0);
  const details = [
    code && code !== node.label ? code : "",
    status ? status.replace(/_/g, " ") : "",
    relationCount > 0 ? `${relationCount} relationships` : "",
    type,
  ].filter(Boolean);
  const reportSummary = type === "report"
    ? [
        propertyString(node.properties, "report_year", "year"),
        propertyString(node.properties, "framework"),
        propertyString(node.properties, "industry", "scope_key"),
      ].filter(Boolean).join(" · ")
    : "";
  return {
    title: node.display_label || node.label,
    subtitle: details.join(" · "),
    body: propertyString(
      node.properties,
      "description",
      "simple_definition",
      "definition",
      "reasoning",
    ) || reportSummary,
    width: type === "report" ? 620 : type === "metric" ? 780 : 760,
    padding: type === "metric" ? 30 : 28,
    ...point,
  };
}

function edgeHoverCard(edge: GraphDisplayEdge, point: GraphPosition): HoverCard {
  const disclosure = edge.disclosure;
  const properties = disclosure?.properties || edge.properties;
  const status = normalizeDisclosureStatus(
    properties.disclosure_status ?? properties.status ?? edge.label,
  );
  const value = properties.value;
  const unit = String(properties.unit || "").trim();
  const page = properties.page_number ?? properties.page;
  const details = [
    status ? status.replace(/_/g, " ") : edge.type.replace(/_/g, " "),
    value !== undefined && value !== null && String(value).toLowerCase() !== "n/a"
      ? `${String(value)}${unit ? ` ${unit}` : ""}`
      : "",
    page !== undefined && page !== null && page !== "" ? `page ${String(page)}` : "",
  ].filter(Boolean);
  return {
    title: disclosure?.label || "Disclosure relationship",
    subtitle: details.join(" · "),
    body: propertyString(properties, "description", "reasoning", "evidence_quote"),
    width: 800,
    padding: 30,
    ...point,
  };
}

function isShiftModifiedGesture(event: unknown): boolean {
  const raw = ((event as { nativeEvent?: unknown })?.nativeEvent || event) as {
    shiftKey?: boolean;
  };
  return Boolean(raw?.shiftKey);
}

const DisclosureGraphCanvas = memo(forwardRef<
  DisclosureGraphCanvasHandle,
  DisclosureGraphCanvasProps
>(function DisclosureGraphCanvas(
  {
    data,
    graphRevision,
    layout,
    positionStorageKey,
    resetNonce,
    onNodeSelect,
    onEdgeSelect,
    onMetricGroupToggle,
    onZoomChange,
    onSelectionChange,
    onPinnedChange,
    onFocusDegreeChange,
    onLayoutPausedChange,
    layoutPaused = false,
    showMinimap = false,
  },
  forwardedRef,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const minimapRef = useRef<HTMLDivElement>(null);
  const hoverCardRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const dataRef = useRef(data);
  const layoutRef = useRef(layout);
  const renderedDataRef = useRef<GraphDisplayData | null>(null);
  const renderedLayoutRef = useRef<GraphLayoutName | null>(null);
  const dataUpdateVersionRef = useRef(0);
  dataRef.current = data;
  layoutRef.current = layout;
  const persistedPositionsRef = useRef<Record<string, GraphPosition>>({});
  const transientPositionsRef = useRef<Record<string, GraphPosition>>({});
  const viewportRef = useRef<{ zoom: number; position: [number, number] } | null>(null);
  const lastResetNonceRef = useRef(resetNonce);
  const focusDegreeRef = useRef<number | null>(null);
  const selectedNodeIdsRef = useRef<string[]>([]);
  const layoutPausedRef = useRef(layoutPaused);
  layoutPausedRef.current = layoutPaused;
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(() => (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(REDUCED_MOTION_QUERY).matches
      : false
  ));
  const prefersReducedMotionRef = useRef(prefersReducedMotion);
  prefersReducedMotionRef.current = prefersReducedMotion;
  const elementMotionEnabledRef = useRef(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [hoverCard, setHoverCard] = useState<HoverCard | null>(null);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(REDUCED_MOTION_QUERY);
    const syncPreference = () => setPrefersReducedMotion(media.matches);
    syncPreference();
    media.addEventListener?.("change", syncPreference);
    return () => media.removeEventListener?.("change", syncPreference);
  }, []);

  const viewportMotion = useCallback((duration: number) => (
    prefersReducedMotionRef.current ? false : { duration }
  ), []);

  const writePersistedPositions = useCallback((positions: Record<string, GraphPosition>) => {
    persistedPositionsRef.current = positions;
    try {
      window.localStorage.setItem(
        positionStorageKey,
        JSON.stringify({ revision: graphRevision, positions }),
      );
    } catch {
      // Position persistence is optional in restricted/private browsing modes.
    }
    onPinnedChange?.(Object.keys(positions));
  }, [graphRevision, onPinnedChange, positionStorageKey]);

  const selectedIds = useCallback(() => {
    const graph = graphRef.current;
    return graph ? nodeSelectionIds(graph) : [];
  }, []);

  const emitSelection = useCallback(() => {
    const ids = selectedIds();
    selectedNodeIdsRef.current = ids;
    onSelectionChange?.(ids);
  }, [onSelectionChange, selectedIds]);

  const applyFocusState = useCallback(async (degree: number | null) => {
    const graph = graphRef.current;
    if (!graph) return;
    const roots = nodeSelectionIds(graph);
    const activeDegree = degree === null || roots.length === 0
      ? null
      : Math.max(0, Math.min(9, Math.floor(degree)));
    focusDegreeRef.current = activeDegree;
    const neighborhood = activeDegree === null
      ? { nodeIds: [], edgeIds: [] }
      : graphNeighborhood(dataRef.current, roots, activeDegree);
    const activeNodes = new Set(neighborhood.nodeIds);
    const activeEdges = new Set(neighborhood.edgeIds);
    const states: Record<string, string[]> = {};
    for (const node of graph.getNodeData()) {
      const current = withoutStates(graph.getElementState(node.id), FOCUS_STATES);
      if (activeDegree !== null) {
        current.push(activeNodes.has(String(node.id)) ? "focusActive" : "focusInactive");
      }
      states[String(node.id)] = current;
    }
    for (const edge of graph.getEdgeData()) {
      const current = withoutStates(graph.getElementState(String(edge.id)), FOCUS_STATES);
      if (activeDegree !== null) {
        current.push(activeEdges.has(String(edge.id)) ? "focusActive" : "focusInactive");
      }
      states[String(edge.id)] = current;
    }
    await graph.setElementState(states, elementMotionEnabledRef.current);
    onFocusDegreeChange?.(activeDegree);
  }, [onFocusDegreeChange]);

  const selectOnlyNode = useCallback(async (nodeId: string) => {
    const graph = graphRef.current;
    if (!graph?.hasNode(nodeId)) return;
    const neighborhood = graphNeighborhood(dataRef.current, [nodeId], 1);
    const neighbors = new Set(neighborhood.nodeIds.filter((id) => id !== nodeId));
    const relationEdges = new Set(neighborhood.edgeIds);
    const states: Record<string, string[]> = {};
    for (const node of graph.getNodeData()) {
      const current = withoutStates(graph.getElementState(node.id), SELECTION_STATES);
      const id = String(node.id);
      current.push(
        id === nodeId
          ? "selected"
          : neighbors.has(id)
            ? "selectionNeighbor"
            : "selectionInactive",
      );
      states[id] = current;
    }
    for (const edge of graph.getEdgeData()) {
      const current = withoutStates(graph.getElementState(String(edge.id)), SELECTION_STATES);
      current.push(relationEdges.has(String(edge.id)) ? "selectionNeighbor" : "selectionInactive");
      states[String(edge.id)] = current;
    }
    await graph.setElementState(states, elementMotionEnabledRef.current);
    emitSelection();
    if (focusDegreeRef.current !== null) {
      await applyFocusState(focusDegreeRef.current);
    }
  }, [applyFocusState, emitSelection]);

  const restorePinnedAfterLayout = useCallback(async (graph: Graph) => {
    const recovered = Object.entries(persistedPositionsRef.current);
    if (!recovered.length) return;
    graph.updateNodeData(
      recovered
        .filter(([id]) => graph.hasNode(id))
        .map(([id, point]) => ({ id, style: { x: point.x, y: point.y } })),
    );
    await graph.draw();
    const pinnedStates: Record<string, string[]> = {};
    for (const [id] of recovered) {
      if (!graph.hasNode(id)) continue;
      const current = graph.getElementState(id).filter((state) => state !== "pinned");
      pinnedStates[id] = [...current, "pinned"];
    }
    if (Object.keys(pinnedStates).length) {
      await graph.setElementState(pinnedStates, false);
    }
  }, []);

  const rerunLayout = useCallback(async () => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.setLayout(layoutOptions(
      layoutRef.current,
      dataRef.current.nodes.length,
      !prefersReducedMotionRef.current,
    ));
    await graph.layout();
    await restorePinnedAfterLayout(graph);
  }, [restorePinnedAfterLayout]);

  useImperativeHandle(
    forwardedRef,
    () => ({
      zoomIn: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        await graph.zoomBy(1.2, viewportMotion(160));
        onZoomChange?.(graph.getZoom());
      },
      zoomOut: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        await graph.zoomBy(1 / 1.2, viewportMotion(160));
        onZoomChange?.(graph.getZoom());
      },
      fitView: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        await graph.fitView({ direction: "both" }, viewportMotion(320));
        onZoomChange?.(graph.getZoom());
      },
      actualSize: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        await graph.zoomTo(1, viewportMotion(220));
        await graph.fitCenter(viewportMotion(220));
        onZoomChange?.(graph.getZoom());
      },
      focusNode: async (nodeId: string) => {
        const graph = graphRef.current;
        if (!graph?.hasNode(nodeId)) return;
        await selectOnlyNode(nodeId);
        await graph.focusElement(nodeId, viewportMotion(300));
        if (graph.getZoom() < 0.9) await graph.zoomTo(0.9, viewportMotion(180));
        onZoomChange?.(graph.getZoom());
      },
      fitSelection: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        const ids = nodeSelectionIds(graph);
        if (!ids.length) return;
        await graph.focusElement(ids, viewportMotion(300));
        onZoomChange?.(graph.getZoom());
      },
      panBy: async (x: number, y: number) => {
        const graph = graphRef.current;
        if (!graph) return;
        await graph.translateBy([x, y], viewportMotion(140));
      },
      selectAll: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        const states: Record<string, string[]> = {};
        for (const node of graph.getNodeData()) {
          states[String(node.id)] = [
            ...withoutStates(graph.getElementState(node.id), SELECTION_STATES),
            "selected",
          ];
        }
        for (const edge of graph.getEdgeData()) {
          states[String(edge.id)] = withoutStates(
            graph.getElementState(String(edge.id)),
            SELECTION_STATES,
          );
        }
        await graph.setElementState(states, elementMotionEnabledRef.current);
        emitSelection();
        if (focusDegreeRef.current !== null) {
          await applyFocusState(focusDegreeRef.current);
        }
      },
      clearSelection: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        const states: Record<string, string[]> = {};
        for (const node of graph.getNodeData()) {
          states[String(node.id)] = withoutStates(
            withoutStates(graph.getElementState(node.id), SELECTION_STATES),
            FOCUS_STATES,
          );
        }
        for (const edge of graph.getEdgeData()) {
          states[String(edge.id)] = withoutStates(
            withoutStates(graph.getElementState(String(edge.id)), SELECTION_STATES),
            FOCUS_STATES,
          );
        }
        focusDegreeRef.current = null;
        await graph.setElementState(states, elementMotionEnabledRef.current);
        onNodeSelect(null);
        onFocusDegreeChange?.(null);
        emitSelection();
      },
      focusSelection: applyFocusState,
      pinSelection: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        const ids = nodeSelectionIds(graph);
        if (!ids.length) return;
        const next = { ...persistedPositionsRef.current };
        const states: Record<string, string[]> = {};
        for (const id of ids) {
          const point = pointFromGraph(graph, id);
          if (point) next[id] = point;
          states[id] = [
            ...graph.getElementState(id).filter((state) => state !== "pinned"),
            "pinned",
          ];
        }
        writePersistedPositions(next);
        await graph.setElementState(states, elementMotionEnabledRef.current);
      },
      unpinSelection: async () => {
        const graph = graphRef.current;
        if (!graph) return;
        const ids = nodeSelectionIds(graph);
        if (!ids.length) return;
        const next = { ...persistedPositionsRef.current };
        const states: Record<string, string[]> = {};
        for (const id of ids) {
          delete next[id];
          states[id] = graph.getElementState(id).filter((state) => state !== "pinned");
        }
        writePersistedPositions(next);
        await graph.setElementState(states, elementMotionEnabledRef.current);
        if (layoutRef.current === "force") await rerunLayout();
      },
      pauseLayout: () => {
        layoutPausedRef.current = true;
        graphRef.current?.stopLayout();
        onLayoutPausedChange?.(true);
      },
      resumeLayout: async () => {
        layoutPausedRef.current = false;
        onLayoutPausedChange?.(false);
        await rerunLayout();
      },
      bumpLayout: async () => {
        layoutPausedRef.current = false;
        onLayoutPausedChange?.(false);
        await rerunLayout();
      },
    }),
    [
      applyFocusState,
      emitSelection,
      onFocusDegreeChange,
      onLayoutPausedChange,
      onNodeSelect,
      onZoomChange,
      rerunLayout,
      selectOnlyNode,
      viewportMotion,
      writePersistedPositions,
    ],
  );

  const graphDensityTier = data.nodes.length > 500 || data.edges.length > 900
    ? "dense"
    : data.nodes.length > 350
      ? "medium"
      : "normal";

  useEffect(() => {
    const container = containerRef.current;
    const initialData = dataRef.current;
    const initialLayout = layoutRef.current;
    if (!container || initialData.nodes.length === 0) return;
    let disposed = false;
    renderedDataRef.current = initialData;
    renderedLayoutRef.current = initialLayout;
    const isDenseGraph = graphDensityTier === "dense";
    const motionEnabled = !prefersReducedMotion;
    const elementMotionEnabled = motionEnabled && !isDenseGraph;
    const liveForceEnabled = motionEnabled && initialData.nodes.length <= 350;
    elementMotionEnabledRef.current = elementMotionEnabled;
    const shouldRestoreViewport = lastResetNonceRef.current === resetNonce;
    if (!shouldRestoreViewport) {
      viewportRef.current = null;
      transientPositionsRef.current = {};
    }
    lastResetNonceRef.current = resetNonce;
    let labelLevel: "hidden" | "compact" | "full" = "full";
    let labelTimer: ReturnType<typeof setTimeout> | null = null;
    let selectionTimer: ReturnType<typeof setTimeout> | null = null;
    let hoverTimer: ReturnType<typeof setTimeout> | null = null;
    let hoverClearTimer: ReturnType<typeof setTimeout> | null = null;
    let zoomFrame: number | null = null;
    let hoverFrame: number | null = null;
    let resizeFrame: number | null = null;
    let pendingCanvasSize: [number, number] | null = null;
    let appliedCanvasSize: [number, number] | null = null;
    let draggingNodeIds: string[] = [];
    let canvasPanActive = false;
    let canvasPanLastTime = 0;
    let canvasPanVelocity = { x: 0, y: 0 };
    const wrappedLabelCache = new Map<string, string>();
    const validIds = initialData.nodes.map((node) => node.id);
    let rawStoredPositions: string | null = null;
    try {
      rawStoredPositions = window.localStorage.getItem(positionStorageKey);
    } catch {
      // Continue with an in-memory layout when storage access is unavailable.
    }
    const stored = parseStoredGraphPositions(rawStoredPositions, validIds);
    persistedPositionsRef.current = { ...stored.positions };
    onPinnedChange?.(Object.keys(stored.positions));

    const persistPositions = (ids: string[]) => {
      const graph = graphRef.current;
      if (!graph) return;
      const next = { ...persistedPositionsRef.current };
      for (const id of ids) {
        const position = pointFromGraph(graph, id);
        if (position) next[id] = position;
      }
      writePersistedPositions(next);
      const pinnedStates: Record<string, string[]> = {};
      for (const id of ids) {
        if (!graph.hasNode(id)) continue;
        pinnedStates[id] = [
          ...graph.getElementState(id).filter(
            (state) => state !== "pinned" && state !== "dragging",
          ),
          "pinned",
        ];
      }
      if (Object.keys(pinnedStates).length) {
        void graph.setElementState(pinnedStates, elementMotionEnabled);
      }
    };

    let graph: Graph;
    const scheduleSelectionSync = () => {
      if (selectionTimer) clearTimeout(selectionTimer);
      selectionTimer = setTimeout(() => {
        if (disposed) return;
        emitSelection();
        if (focusDegreeRef.current !== null) {
          void applyFocusState(focusDegreeRef.current);
        }
      }, 0);
    };
    const refreshZoomLabels = () => {
      if (zoomFrame !== null) return;
      zoomFrame = window.requestAnimationFrame(() => {
        zoomFrame = null;
        if (disposed) return;
        const zoom = graph.getZoom();
        const nextLevel = labelLevelForZoom(zoom, isDenseGraph);
        onZoomChange?.(zoom);
        if (nextLevel === labelLevel) return;
        labelLevel = nextLevel;
        if (labelTimer) clearTimeout(labelTimer);
        labelTimer = setTimeout(() => {
          if (!disposed) void graph.draw();
        }, 80);
      });
    };

    try {
      const automaticCurvedEdgeIds = automaticParallelEdgeIds(initialData.edges);
      graph = new Graph({
        container,
        // A single ResizeObserver below owns sizing. Running G6's observer in
        // parallel causes duplicate layout work while drawers/filters animate.
        autoResize: false,
        animation: motionEnabled ? GRAPH_MOTION : false,
        background: KUMU_COLORS.background,
        data: toG6Data(initialData),
        zoomRange: [0.12, 4],
        // Kumu controls float over the canvas; they do not shrink the graph's
        // world viewport. A small symmetric inset keeps key shapes prominent.
        padding: KUMU_VIEW_PADDING,
        layout: layoutOptions(initialLayout, initialData.nodes.length, motionEnabled),
        transforms: automaticCurvedEdgeIds.length
          ? [{
              type: "process-parallel-edges",
              mode: "bundle",
              distance: 18,
              edges: automaticCurvedEdgeIds,
            }]
          : [],
        plugins: [
          {
            type: "contextmenu" as const,
            key: "disclosure-contextmenu",
            trigger: "contextmenu" as const,
            offset: [8, 8] as [number, number],
            getItems: (event: { targetType?: string }) => {
              if (event.targetType === "canvas") {
                return [
                  { name: "Fit graph", value: "fit-graph" },
                  { name: "Reset to 100%", value: "actual-size" },
                  { name: "Bump layout", value: "bump-layout" },
                ];
              }
              if (event.targetType === "edge") {
                return [
                  { name: "Open disclosure details", value: "open-edge" },
                  { name: "Fit relationship", value: "fit-edge" },
                  { name: "Copy relationship ID", value: "copy-element-id" },
                ];
              }
              return [
                { name: "Open details", value: "open-details" },
                { name: "Focus 1 degree", value: "focus-one" },
                { name: "Fit selection", value: "fit-selection" },
                { name: "Pin", value: "pin-node" },
                { name: "Unpin", value: "unpin-node" },
                { name: "Copy node ID", value: "copy-element-id" },
              ];
            },
            onClick: (value: string, _target: HTMLElement, current: { id?: string }) => {
              const id = String(current?.id || "");
              if (value === "fit-graph") {
                void graph.fitView({ direction: "both" }, viewportMotion(320));
                return;
              }
              if (value === "actual-size") {
                void graph.zoomTo(1, viewportMotion(220)).then(() => (
                  graph.fitCenter(viewportMotion(220))
                ));
                return;
              }
              if (value === "bump-layout") {
                layoutPausedRef.current = false;
                onLayoutPausedChange?.(false);
                void rerunLayout();
                return;
              }
              if (value === "copy-element-id") {
                const copy = navigator.clipboard?.writeText(id);
                if (copy) void copy.catch(() => undefined);
                return;
              }
              if (id && graph.hasEdge(id)) {
                const edgeDatum = graph.getEdgeData(id);
                const edge = graphEdgeFromDatum(edgeDatum);
                if (value === "open-edge" && edge) onEdgeSelect(edge);
                else if (value === "fit-edge") {
                  void graph.focusElement(
                    [String(edgeDatum.source), String(edgeDatum.target)],
                    viewportMotion(300),
                  );
                }
                return;
              }
              if (!id || !graph.hasNode(id)) return;
              void selectOnlyNode(id).then(async () => {
                if (disposed || !graph.hasNode(id)) return;
                if (value === "open-details") {
                  const node = graphNodeFromDatum(graph.getNodeData(id));
                  if (node?.synthetic) onMetricGroupToggle(metricCode(node));
                  else if (node) onNodeSelect(node);
                } else if (value === "focus-one") {
                  await applyFocusState(1);
                } else if (value === "fit-selection") {
                  await graph.focusElement(nodeSelectionIds(graph), viewportMotion(300));
                } else if (value === "pin-node") {
                  persistPositions([id]);
                } else if (value === "unpin-node") {
                  const next = { ...persistedPositionsRef.current };
                  delete next[id];
                  writePersistedPositions(next);
                  await graph.setElementState({
                    [id]: graph.getElementState(id).filter((state) => state !== "pinned"),
                  }, elementMotionEnabled);
                  if (layoutRef.current === "force") await rerunLayout();
                }
              });
            },
          },
          ...(showMinimap && minimapRef.current
            ? [{
                type: "minimap" as const,
                key: "disclosure-minimap",
                container: minimapRef.current,
                size: [184, 116] as [number, number],
                padding: 10,
                delay: 180,
                filter: (_id: string, elementType: string) => elementType === "node",
                maskStyle: {
                  border: `1px solid ${KUMU_COLORS.disclosed}`,
                  background: "rgba(0, 138, 91, 0.08)",
                },
              }]
            : []),
          ...(initialData.nodes.length <= 500
            ? [{
                type: "snapline" as const,
                key: "disclosure-snapline",
                autoSnap: false,
                tolerance: 5,
                verticalLineStyle: { stroke: KUMU_COLORS.disclosed, lineWidth: 1 },
                horizontalLineStyle: { stroke: KUMU_COLORS.disclosed, lineWidth: 1 },
              }]
            : []),
        ],
        node: {
          type: "circle",
          style: (datum) => {
            const node = graphNodeFromDatum(datum);
            const type = String(datum.data?.nodeType || "other");
            const rawFullLabel = String(datum.data?.fullLabel || node?.label || datum.id);
            const fullLabel = type === "report"
              ? rawFullLabel.replace(/\.(?:pdf|docx?|pptx?)$/i, "").trim()
              : rawFullLabel;
            const shortLabel = String(datum.data?.shortLabel || fullLabel);
            const visibleLabel = type === "report"
              ? fullLabel
              : labelLevel === "hidden"
                ? ""
                : labelLevel === "compact"
                  ? shortLabel
                  : fullLabel;
            const wrapCharacters = type === "report" ? 13 : 21;
            const wrappedLabelKey = `${labelLevel}\u0000${wrapCharacters}\u0000${visibleLabel}`;
            let wrappedLabel = wrappedLabelCache.get(wrappedLabelKey);
            if (wrappedLabel === undefined) {
              wrappedLabel = wrapKumuLabel(visibleLabel, wrapCharacters);
              wrappedLabelCache.set(wrappedLabelKey, wrappedLabel);
            }
            const common = {
              cursor: "pointer" as const,
              zIndex: 2,
              size: 56,
              fill: "#FFFFFF",
              stroke: KUMU_COLORS.elementBorder,
              lineWidth: 3.2,
              halo: true,
              haloStroke: KUMU_COLORS.reportSelected,
              haloLineWidth: 8,
              haloStrokeOpacity: 0,
              labelText: wrappedLabel,
              labelFontFamily: "Arial, sans-serif",
              labelFontSize: labelLevel === "compact" ? 16 : 28,
              labelLineHeight: labelLevel === "compact" ? 19 : 32,
              labelFontWeight: 700,
              labelFill: KUMU_COLORS.text,
              labelBackground: false,
              labelPlacement: "bottom" as const,
              labelOffsetY: 6,
              labelWordWrap: false,
            };
            if (type === "report") {
              const reportFontSize = reportLabelOverride(
                REPORT_FONT_SIZE_BY_LABEL,
                node?.label || "",
              ) || 32;
              return {
                ...common,
                size: reportSize(node),
                fill: KUMU_COLORS.report,
                stroke: "#FFFFFF",
                lineWidth: reportLabelOverride(
                  REPORT_BORDER_WIDTH_BY_LABEL,
                  node?.label || "",
                ) || 7,
                labelFill: "#FFFFFF",
                labelFontSize: reportFontSize,
                labelLineHeight: Math.round(reportFontSize * 1.1),
                labelPlacement: "center" as const,
                labelOffsetY: 0,
              };
            }
            if (type === "metric") {
              return {
                ...common,
                size: 56,
                fill: "#FFFFFF",
                stroke: KUMU_COLORS.metricBorder,
                lineWidth: 3.5,
                labelFill: "#061A13",
              };
            }
            return common;
          },
          animation: elementMotionEnabled
            ? { state: NODE_STATE_MOTION }
            : false,
          state: {
            selected: (datum) => {
              const type = String(datum.data?.nodeType || "other");
              if (type === "report") {
                return { stroke: KUMU_COLORS.reportSelected, lineWidth: 11 };
              }
              if (type === "metric") {
                return { size: 64, stroke: KUMU_COLORS.disclosed, lineWidth: 6 };
              }
              return { stroke: KUMU_COLORS.disclosed, lineWidth: 6 };
            },
            pinned: {
              halo: true,
              haloStroke: KUMU_COLORS.reportSelected,
              haloLineWidth: 5,
              haloStrokeOpacity: 0.48,
            },
            selectionNeighbor: {
              halo: true,
              haloStroke: KUMU_COLORS.reportSelected,
              haloLineWidth: 6,
              haloStrokeOpacity: 0.28,
            },
            selectionInactive: { opacity: 0.12, labelOpacity: 0.12 },
            hoverActive: (datum) => {
              const type = String(datum.data?.nodeType || "other");
              return {
                halo: true,
                haloStroke: KUMU_COLORS.reportSelected,
                haloLineWidth: type === "report" ? 12 : 9,
                haloStrokeOpacity: 0.42,
                ...(type === "report" ? {} : { size: 60 }),
              };
            },
            hoverInactive: { opacity: 0.12, labelOpacity: 0.12 },
            dragging: {
              halo: true,
              haloStroke: KUMU_COLORS.reportSelected,
              haloLineWidth: 14,
              haloStrokeOpacity: 0.72,
            },
            focusActive: { opacity: 1, labelOpacity: 1 },
            focusInactive: { opacity: 0.12, labelOpacity: 0.12 },
          },
        },
        edge: {
          type: (datum) => {
            const precomputedCurvature = datum.data?.curvature;
            const curvature = typeof precomputedCurvature === "number"
              ? precomputedCurvature
              : edgeCurvature(graphEdgeFromDatum(datum));
            if (curvature !== null) return curvature === 0 ? "line" : "quadratic";
            return String(datum.type || "line");
          },
          style: (datum) => {
            const graphEdge = graphEdgeFromDatum(datum);
            const status =
              datum.data?.status ||
              graphEdge?.properties.disclosure_status ||
              graphEdge?.properties.status;
            const normalized = normalizeDisclosureStatus(status);
            const precomputedCurvature = datum.data?.curvature;
            const curvature = typeof precomputedCurvature === "number"
              ? precomputedCurvature
              : edgeCurvature(graphEdge);
            const curveStyle = curvature === null
              ? {}
              : { curveOffset: curvature * KUMU_SPRING_LENGTH };
            return {
              cursor: "pointer",
              zIndex: 1,
              stroke: statusColor(normalized),
              lineWidth: normalized === "fully_disclosed"
                ? 7
                : normalized === "partially_disclosed"
                  ? 5.7
                  : normalized === "not_disclosed"
                    ? 4
                    : 2.2,
              lineDash:
                normalized === "partially_disclosed"
                  ? [12, 6]
                  : normalized === "not_disclosed"
                    ? [2, 8]
                    : undefined,
              strokeOpacity: normalized === "not_disclosed" ? 0.92 : normalized ? 1 : 0.55,
              ...curveStyle,
              labelText: "",
              startArrow: false,
              endArrow: false,
              increasedLineWidthForHitTesting: 12,
            };
          },
          animation: elementMotionEnabled
            ? { state: EDGE_STATE_MOTION }
            : false,
          state: {
            selected: {
              strokeOpacity: 1,
              halo: true,
              haloLineWidth: 14,
              haloStrokeOpacity: 0.16,
            },
            selectionNeighbor: { strokeOpacity: 1 },
            selectionInactive: { strokeOpacity: 0.12 },
            hoverActive: {
              strokeOpacity: 1,
              halo: true,
              haloLineWidth: 14,
              haloStrokeOpacity: 0.14,
            },
            hoverInactive: { strokeOpacity: 0.12 },
            focusActive: { strokeOpacity: 1 },
            focusInactive: { strokeOpacity: 0.12 },
          },
        },
        behaviors: [
          {
            type: "drag-canvas",
            enable: (event: IPointerEvent) => {
              const native = ((event as { nativeEvent?: unknown }).nativeEvent || event) as {
                shiftKey?: boolean;
              };
              return event.targetType === "canvas" && !native.shiftKey;
            },
          },
          {
            type: "zoom-canvas",
            key: "wheel-zoom",
            // G6 treats an empty shortcut as an unmodified wheel gesture.
            trigger: [],
            sensitivity: 0.28,
            preventDefault: true,
            onFinish: refreshZoomLabels,
          },
          {
            type: "zoom-canvas",
            key: "control-wheel-zoom",
            trigger: ["Control"],
            sensitivity: 0.28,
            preventDefault: true,
            onFinish: refreshZoomLabels,
          },
          {
            type: "zoom-canvas",
            key: "meta-wheel-zoom",
            trigger: ["Meta"],
            sensitivity: 0.28,
            preventDefault: true,
            onFinish: refreshZoomLabels,
          },
          {
            type: "zoom-canvas",
            key: "pinch-zoom",
            trigger: ["pinch"],
            sensitivity: 0.65,
            preventDefault: true,
            onFinish: refreshZoomLabels,
          },
          {
            type: "drag-element-force",
            enable: (event: unknown) => (
              layoutRef.current === "force" &&
              liveForceEnabled &&
              !layoutPausedRef.current &&
              !isShiftModifiedGesture(event)
            ),
            fixed: true,
            state: "selected",
            hideEdge: initialData.edges.length > 700 ? "all" : "none",
          },
          {
            type: "drag-element",
            enable: (event: unknown) => (
              (
                layoutRef.current !== "force" ||
                !liveForceEnabled ||
                layoutPausedRef.current
              ) &&
              !isShiftModifiedGesture(event)
            ),
            state: "selected",
            hideEdge: initialData.edges.length > 700 ? "all" : "none",
          },
          {
            type: "click-select",
            multiple: true,
            trigger: ["shift"],
            degree: 1,
            state: "selected",
            neighborState: "selectionNeighbor",
            unselectedState: "selectionInactive",
            animation: elementMotionEnabled,
            onClick: scheduleSelectionSync,
          },
          {
            type: "brush-select",
            trigger: ["shift"],
            enable: (event: IPointerEvent) => event.targetType === "canvas",
            mode: "union",
            enableElements: ["node"],
            state: "selected",
            animation: elementMotionEnabled,
            onSelect: (states: Record<string, string | string[]>) => {
              for (const [id, rawStates] of Object.entries(states)) {
                const values = Array.isArray(rawStates) ? rawStates : [rawStates];
                if (values.includes("selected")) {
                  states[id] = values.filter((state) => state !== "selectionInactive");
                }
              }
              scheduleSelectionSync();
            },
            style: {
              fill: "rgba(37, 99, 235, 0.08)",
              stroke: "#2563eb",
              lineDash: [5, 4],
            },
          },
          {
            type: "hover-activate",
            degree: 1,
            state: "hoverActive",
            inactiveState: initialData.nodes.length <= 250 ? "hoverInactive" : undefined,
            animation: elementMotionEnabled && initialData.nodes.length <= 250,
          },
        ],
      });
    } catch (error) {
      setRenderError(error instanceof Error ? error.message : "Unable to initialize graph canvas");
      return;
    }

    graphRef.current = graph;
    setRenderError(null);

    const scheduleHover = (card: HoverCard) => {
      if (hoverClearTimer) clearTimeout(hoverClearTimer);
      if (hoverTimer) clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => {
        if (!disposed) setHoverCard(card);
      }, 110);
    };
    const moveHover = (event: IPointerEvent) => {
      if (hoverFrame !== null) return;
      hoverFrame = window.requestAnimationFrame(() => {
        hoverFrame = null;
        if (disposed) return;
        const card = hoverCardRef.current;
        if (!card) return;
        const width = Number(card.dataset.cardWidth || 440);
        const point = hoverPoint(event, container, width);
        card.style.left = `${point.x}px`;
        card.style.top = `${point.y}px`;
      });
    };
    const clearHover = (immediate = false) => {
      if (hoverTimer) clearTimeout(hoverTimer);
      if (hoverClearTimer) clearTimeout(hoverClearTimer);
      if (immediate) setHoverCard(null);
      else {
        hoverClearTimer = setTimeout(() => {
          if (!disposed) setHoverCard(null);
        }, 90);
      }
    };

    graph.on(NodeEvent.CLICK, (event: IPointerEvent) => {
      const id = eventTargetId(event);
      if (!graph.hasNode(id)) return;
      const node = graphNodeFromDatum(graph.getNodeData(id));
      if (!node) return;
      if (node.synthetic) {
        onMetricGroupToggle(metricCode(node));
        return;
      }
      onNodeSelect(node);
    });
    graph.on(NodeEvent.DBLCLICK, (event: IPointerEvent) => {
      const id = eventTargetId(event);
      if (!graph.hasNode(id)) return;
      void selectOnlyNode(id).then(async () => {
        if (disposed) return;
        await graph.focusElement(id, viewportMotion(300));
        if (graph.getZoom() < 0.9) await graph.zoomTo(0.9, viewportMotion(180));
        onZoomChange?.(graph.getZoom());
      });
    });
    graph.on(NodeEvent.POINTER_OVER, (event: IPointerEvent) => {
      const id = eventTargetId(event);
      if (!graph.hasNode(id)) return;
      const node = graphNodeFromDatum(graph.getNodeData(id));
      if (node) {
        const card = nodeHoverCard(node, { x: 0, y: 0 });
        scheduleHover({ ...card, ...hoverPoint(event, container, card.width) });
      }
    });
    graph.on(NodeEvent.POINTER_MOVE, moveHover);
    graph.on(NodeEvent.POINTER_OUT, () => clearHover());
    graph.on(NodeEvent.DRAG_START, (event: IPointerEvent) => {
      clearHover(true);
      const targetId = eventTargetId(event);
      if (!graph.hasNode(targetId)) return;
      const targetIsSelected = graph.getElementState(targetId).includes("selected");
      draggingNodeIds = targetIsSelected
        ? nodeSelectionIds(graph)
        : [targetId];
      const draggingStates: Record<string, string[]> = {};
      for (const id of draggingNodeIds) {
        draggingStates[id] = [
          ...graph.getElementState(id).filter((state) => state !== "dragging"),
          "dragging",
        ];
      }
      void graph.setElementState(draggingStates, elementMotionEnabled);
    });
    graph.on(NodeEvent.DRAG_END, (event: IPointerEvent) => {
      const targetId = eventTargetId(event);
      const selectedIds = graph
        .getNodeData()
        .filter((node) => graph.getElementState(node.id).includes("selected"))
        .map((node) => String(node.id));
      persistPositions([...new Set([targetId, ...selectedIds, ...draggingNodeIds])]);
      draggingNodeIds = [];
    });
    graph.on(EdgeEvent.CLICK, (event: IPointerEvent) => {
      const id = eventTargetId(event);
      if (!graph.hasEdge(id)) return;
      const edge = graphEdgeFromDatum(graph.getEdgeData(id));
      if (edge) onEdgeSelect(edge);
    });
    graph.on(EdgeEvent.POINTER_OVER, (event: IPointerEvent) => {
      const id = eventTargetId(event);
      if (!graph.hasEdge(id)) return;
      const edge = graphEdgeFromDatum(graph.getEdgeData(id));
      if (edge) {
        const card = edgeHoverCard(edge, { x: 0, y: 0 });
        scheduleHover({ ...card, ...hoverPoint(event, container, card.width) });
      }
    });
    graph.on(EdgeEvent.POINTER_MOVE, moveHover);
    graph.on(EdgeEvent.POINTER_OUT, () => clearHover());
    graph.on(CanvasEvent.CLICK, () => {
      onNodeSelect(null);
      void applyFocusState(null);
      scheduleSelectionSync();
    });
    graph.on(CanvasEvent.DRAG_START, (event: IPointerEvent) => {
      const native = ((event as { nativeEvent?: unknown }).nativeEvent || event) as {
        shiftKey?: boolean;
        timeStamp?: number;
      };
      canvasPanActive = !native.shiftKey;
      canvasPanLastTime = Number(native.timeStamp) || performance.now();
      canvasPanVelocity = { x: 0, y: 0 };
    });
    graph.on(CanvasEvent.DRAG, (event: IPointerEvent) => {
      if (!canvasPanActive) return;
      const pointer = event as IPointerEvent & {
        dx?: number;
        dy?: number;
        movement?: { x?: number; y?: number };
        nativeEvent?: {
          movementX?: number;
          movementY?: number;
          timeStamp?: number;
        };
      };
      const native = pointer.nativeEvent || {};
      const now = Number(native.timeStamp) || performance.now();
      const elapsed = Math.max(8, Math.min(48, now - canvasPanLastTime || 16));
      canvasPanLastTime = now;
      const dx = Number(pointer.movement?.x ?? pointer.dx ?? native.movementX ?? 0);
      const dy = Number(pointer.movement?.y ?? pointer.dy ?? native.movementY ?? 0);
      const blend = 0.42;
      canvasPanVelocity = {
        x: canvasPanVelocity.x * (1 - blend) + (dx / elapsed) * blend,
        y: canvasPanVelocity.y * (1 - blend) + (dy / elapsed) * blend,
      };
    });
    graph.on(CanvasEvent.DRAG_END, () => {
      if (!canvasPanActive) return;
      canvasPanActive = false;
      if (!motionEnabled) return;
      const speed = Math.hypot(canvasPanVelocity.x, canvasPanVelocity.y);
      if (speed < 0.04) return;
      const clampOffset = (value: number) => Math.max(-280, Math.min(280, value * 180));
      void graph.translateBy(
        [clampOffset(canvasPanVelocity.x), clampOffset(canvasPanVelocity.y)],
        viewportMotion(320),
      );
    });
    graph.on(GraphEvent.BEFORE_TRANSFORM, () => clearHover(true));
    graph.on(GraphEvent.AFTER_TRANSFORM, refreshZoomLabels);

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry || disposed) return;
      const width = Math.floor(entry.contentRect.width);
      const height = Math.floor(entry.contentRect.height);
      if (width <= 0 || height <= 0) return;
      pendingCanvasSize = [width, height];
      if (resizeFrame !== null) return;
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null;
        if (disposed || !pendingCanvasSize) return;
        const [nextWidth, nextHeight] = pendingCanvasSize;
        pendingCanvasSize = null;
        if (
          appliedCanvasSize?.[0] === nextWidth
          && appliedCanvasSize?.[1] === nextHeight
        ) return;
        appliedCanvasSize = [nextWidth, nextHeight];
        graph.setSize(nextWidth, nextHeight);
      });
    });
    resizeObserver.observe(container);

    void graph
      .render()
      .then(async () => {
        if (disposed) return;
        const recoveredPositions = {
          ...(shouldRestoreViewport ? transientPositionsRef.current : {}),
          ...stored.positions,
        };
        const recovered = Object.entries(recoveredPositions).filter(([id]) => graph.hasNode(id));
        if (recovered.length) {
          graph.updateNodeData(
            recovered.map(([id, point]) => ({ id, style: { x: point.x, y: point.y } })),
          );
          await graph.draw();
        }
        await restorePinnedAfterLayout(graph);
        const survivingSelection = selectedNodeIdsRef.current.filter((id) => graph.hasNode(id));
        if (survivingSelection.length) {
          const selectionStates: Record<string, string[]> = {};
          for (const id of survivingSelection) {
            selectionStates[id] = [
              ...withoutStates(graph.getElementState(id), SELECTION_STATES),
              "selected",
            ];
          }
          await graph.setElementState(selectionStates, false);
        }
        selectedNodeIdsRef.current = survivingSelection;
        onSelectionChange?.(survivingSelection);
        if (focusDegreeRef.current !== null && survivingSelection.length) {
          await applyFocusState(focusDegreeRef.current);
        }
        if (!disposed) {
          if (shouldRestoreViewport && viewportRef.current) {
            await graph.zoomTo(viewportRef.current.zoom, false);
            await graph.translateTo(viewportRef.current.position, false);
          } else {
            const fitted = await fitCircleKeyShapes(graph);
            if (!fitted) await graph.fitView({ direction: "both" }, false);
          }
          labelLevel = labelLevelForZoom(graph.getZoom(), isDenseGraph);
          onZoomChange?.(graph.getZoom());
        }
      })
      .catch((error) => {
        if (!disposed) {
          setRenderError(error instanceof Error ? error.message : "Unable to render graph");
        }
      });

    return () => {
      try {
        viewportRef.current = {
          zoom: graph.getZoom(),
          position: graph.getPosition() as [number, number],
        };
        transientPositionsRef.current = Object.fromEntries(
          graph.getNodeData().flatMap((node) => {
            const point = pointFromGraph(graph, String(node.id));
            return point ? [[String(node.id), point] as const] : [];
          }),
        );
      } catch {
        // A graph can already be torn down after a failed render.
      }
      disposed = true;
      if (labelTimer) clearTimeout(labelTimer);
      if (selectionTimer) clearTimeout(selectionTimer);
      if (hoverTimer) clearTimeout(hoverTimer);
      if (hoverClearTimer) clearTimeout(hoverClearTimer);
      if (zoomFrame !== null) window.cancelAnimationFrame(zoomFrame);
      if (hoverFrame !== null) window.cancelAnimationFrame(hoverFrame);
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame);
      resizeObserver.disconnect();
      if (graphRef.current === graph) graphRef.current = null;
      graph.destroy();
    };
  }, [
    graphDensityTier,
    graphRevision,
    onEdgeSelect,
    onFocusDegreeChange,
    onLayoutPausedChange,
    onMetricGroupToggle,
    onNodeSelect,
    onPinnedChange,
    onSelectionChange,
    onZoomChange,
    positionStorageKey,
    prefersReducedMotion,
    resetNonce,
    restorePinnedAfterLayout,
    rerunLayout,
    selectOnlyNode,
    showMinimap,
    viewportMotion,
    writePersistedPositions,
    applyFocusState,
    emitSelection,
  ]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || renderedDataRef.current === data || data.nodes.length === 0) return;

    const updateVersion = ++dataUpdateVersionRef.current;
    renderedDataRef.current = data;
    const previousNodeIds = new Set(
      graph.getNodeData().map((node) => String(node.id)),
    );
    const previousEdgeTopology = new Map(
      graph.getEdgeData().map((edge) => [
        String(edge.id),
        `${String(edge.source)}\u0000${String(edge.target)}`,
      ]),
    );
    const nextNodeIds = new Set(data.nodes.map((node) => node.id));
    const nodeTopologyChanged = previousNodeIds.size !== nextNodeIds.size
      || [...nextNodeIds].some((id) => !previousNodeIds.has(id));
    const edgeTopologyChanged = previousEdgeTopology.size !== data.edges.length
      || data.edges.some(
        (edge) => previousEdgeTopology.get(edge.id) !== `${edge.source}\u0000${edge.target}`,
      );
    const topologyChanged = nodeTopologyChanged || edgeTopologyChanged;
    if (topologyChanged) graph.stopLayout();
    const currentPositions = new Map<string, GraphPosition>();
    const previousMetricGroupPositions = new Map<string, GraphPosition>();
    const previousMetricMemberPositions = new Map<string, GraphPosition[]>();
    for (const node of graph.getNodeData()) {
      const id = String(node.id);
      const point = pointFromGraph(graph, id);
      if (!point) continue;
      currentPositions.set(id, point);
      const graphNode = graphNodeFromDatum(node);
      if (!graphNode || normalizeNodeType(graphNode.type) !== "metric") continue;
      const code = metricCode(graphNode);
      if (graphNode.synthetic) {
        previousMetricGroupPositions.set(code, point);
      } else {
        const members = previousMetricMemberPositions.get(code) || [];
        members.push(point);
        previousMetricMemberPositions.set(code, members);
      }
    }
    const existingPoints = [...currentPositions.values()];
    const center = existingPoints.length
      ? existingPoints.reduce(
          (sum, point) => ({ x: sum.x + point.x, y: sum.y + point.y }),
          { x: 0, y: 0 },
        )
      : { x: 0, y: 0 };
    if (existingPoints.length) {
      center.x /= existingPoints.length;
      center.y /= existingPoints.length;
    }

    const projected = toG6Data(data);
    const edgesByNode = new Map<string, GraphDisplayEdge[]>();
    for (const edge of data.edges) {
      for (const id of [edge.source, edge.target]) {
        const entries = edgesByNode.get(id);
        if (entries) entries.push(edge);
        else edgesByNode.set(id, [edge]);
      }
    }
    let addedIndex = 0;
    for (const node of projected.nodes || []) {
      const id = String(node.id);
      let point = currentPositions.get(id);
      if (!point) {
        const graphNode = graphNodeFromDatum(node);
        const code = graphNode && normalizeNodeType(graphNode.type) === "metric"
          ? metricCode(graphNode)
          : "";
        const previousMembers = code ? previousMetricMemberPositions.get(code) || [] : [];
        const memberCentroid = previousMembers.length
          ? previousMembers.reduce(
              (sum, member) => ({ x: sum.x + member.x, y: sum.y + member.y }),
              { x: 0, y: 0 },
            )
          : null;
        if (memberCentroid) {
          memberCentroid.x /= previousMembers.length;
          memberCentroid.y /= previousMembers.length;
        }
        const familyOrigin = graphNode?.synthetic
          ? memberCentroid
          : code
            ? previousMetricGroupPositions.get(code)
            : null;
        const anchor = (edgesByNode.get(id) || [])
          .map((edge) => edge.source === id ? edge.target : edge.source)
          .map((neighborId) => currentPositions.get(neighborId))
          .find((candidate): candidate is GraphPosition => Boolean(candidate));
        let hash = 0;
        for (const character of id) {
          hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
        }
        const angle = ((Math.abs(hash) % 360) * Math.PI) / 180;
        const radius = graphNode?.synthetic
          ? 0
          : familyOrigin
            ? 72 + (addedIndex % 4) * 22
            : 86 + (addedIndex % 4) * 24;
        const origin = familyOrigin || anchor || center;
        point = {
          x: origin.x + Math.cos(angle) * radius,
          y: origin.y + Math.sin(angle) * radius,
        };
        addedIndex += 1;
      }
      node.style = { ...node.style, x: point.x, y: point.y };
    }

    const automaticCurvedEdgeIds = automaticParallelEdgeIds(data.edges);
    graph.setTransforms(automaticCurvedEdgeIds.length
      ? [{
          type: "process-parallel-edges",
          mode: "bundle",
          distance: 18,
          edges: automaticCurvedEdgeIds,
        }]
      : []);
    graph.setData(projected);

    void graph.draw().then(async () => {
      if (
        graphRef.current !== graph ||
        updateVersion !== dataUpdateVersionRef.current
      ) return;

      const survivingSelection = selectedNodeIdsRef.current.filter((id) => graph.hasNode(id));
      selectedNodeIdsRef.current = survivingSelection;
      if (survivingSelection.length) {
        const neighborhood = graphNeighborhood(data, survivingSelection, 1);
        const selected = new Set(survivingSelection);
        const neighboringNodes = new Set(neighborhood.nodeIds);
        const neighboringEdges = new Set(neighborhood.edgeIds);
        const states: Record<string, string[]> = {};
        for (const node of graph.getNodeData()) {
          const id = String(node.id);
          const current = withoutStates(graph.getElementState(id), SELECTION_STATES);
          current.push(
            selected.has(id)
              ? "selected"
              : neighboringNodes.has(id)
                ? "selectionNeighbor"
                : "selectionInactive",
          );
          states[id] = current;
        }
        for (const edge of graph.getEdgeData()) {
          const id = String(edge.id);
          const current = withoutStates(graph.getElementState(id), SELECTION_STATES);
          current.push(neighboringEdges.has(id) ? "selectionNeighbor" : "selectionInactive");
          states[id] = current;
        }
        await graph.setElementState(states, elementMotionEnabledRef.current);
      }

      const survivingIds = new Set(data.nodes.map((node) => node.id));
      const nextPinned = Object.fromEntries(
        Object.entries(persistedPositionsRef.current).filter(([id]) => survivingIds.has(id)),
      );
      if (Object.keys(nextPinned).length !== Object.keys(persistedPositionsRef.current).length) {
        writePersistedPositions(nextPinned);
      }
      if (
        topologyChanged
        && (layoutRef.current !== "force" || !layoutPausedRef.current)
      ) {
        graph.setLayout(layoutOptions(
          layoutRef.current,
          data.nodes.length,
          !prefersReducedMotionRef.current,
        ));
        await graph.layout();
        if (
          graphRef.current !== graph
          || updateVersion !== dataUpdateVersionRef.current
        ) return;
        await restorePinnedAfterLayout(graph);
      }
      onSelectionChange?.(survivingSelection);
      if (focusDegreeRef.current !== null && survivingSelection.length) {
        await applyFocusState(focusDegreeRef.current);
      }
      if (topologyChanged) {
        onZoomChange?.(graph.getZoom());
      }
    }).catch((error) => {
      if (updateVersion === dataUpdateVersionRef.current) {
        setRenderError(error instanceof Error ? error.message : "Unable to update graph data");
      }
    });

    return () => {
      if (updateVersion === dataUpdateVersionRef.current) {
        dataUpdateVersionRef.current += 1;
      }
    };
  }, [
    applyFocusState,
    data,
    onSelectionChange,
    onZoomChange,
    restorePinnedAfterLayout,
    writePersistedPositions,
  ]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || renderedLayoutRef.current === layout) return;
    renderedLayoutRef.current = layout;
    graph.stopLayout();
    graph.setLayout(layoutOptions(
      layout,
      dataRef.current.nodes.length,
      !prefersReducedMotionRef.current,
    ));
    void graph.layout()
      .then(() => restorePinnedAfterLayout(graph))
      .catch((error) => {
        if (graphRef.current === graph) {
          setRenderError(error instanceof Error ? error.message : "Unable to update graph layout");
        }
      });
  }, [layout, restorePinnedAfterLayout]);

  return (
    <div
      className="relative h-full min-h-[480px] w-full overflow-hidden rounded-2xl bg-[#FAFBF9] font-[Arial]"
    >
      <div
        ref={containerRef}
        data-testid="disclosure-graph-canvas"
        className="h-full min-h-[480px] w-full touch-none bg-[#FAFBF9] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#008A5B]"
        role="application"
        tabIndex={0}
        aria-label="Interactive disclosure graph"
        aria-describedby="graph-canvas-instructions"
        aria-keyshortcuts="Control+= Control+- Control+0 Meta+= Meta+- Meta+0 S / [ ] A P Alt+P Escape ArrowUp ArrowDown ArrowLeft ArrowRight"
      />
      {showMinimap ? (
        <div className="pointer-events-auto absolute bottom-3 right-3 z-[3] overflow-hidden rounded-xl border border-[#C2CBC8] bg-[#FAFBF9]/95 p-1.5 shadow-lg backdrop-blur">
          <div className="mb-1 px-1 text-[9px] font-semibold uppercase tracking-[0.14em] text-slate-400">
            Overview
          </div>
          <div
            ref={minimapRef}
            data-testid="disclosure-graph-minimap"
            className="h-[116px] w-[184px] overflow-hidden rounded-lg bg-[#FAFBF9]"
            aria-hidden="true"
          />
        </div>
      ) : null}
      {hoverCard ? (
        <div
          ref={hoverCardRef}
          data-card-width={hoverCard.width}
          className="pointer-events-none absolute z-20 origin-top-left animate-in fade-in-0 zoom-in-95 rounded-xl border border-[#C2CBC8] bg-[#FAFBF9]/98 font-[Arial] shadow-xl backdrop-blur duration-150 motion-reduce:animate-none"
          style={{
            left: hoverCard.x,
            top: hoverCard.y,
            width: `min(${hoverCard.width}px, calc(100% - 24px))`,
            padding: hoverCard.padding,
          }}
          role="status"
        >
          <div className="line-clamp-2 text-base font-extrabold leading-6 text-[#0D1D17]">
            {hoverCard.title}
          </div>
          <div className="my-2 h-px bg-[#C2CBC8]" />
          <div className="line-clamp-2 text-xs font-bold uppercase tracking-wide text-[#526E63]">
            {hoverCard.subtitle}
          </div>
          {hoverCard.body ? (
            <div className="mt-2 line-clamp-5 whitespace-pre-wrap text-xs leading-5 text-[#1B2823]">
              {hoverCard.body}
            </div>
          ) : null}
        </div>
      ) : null}
      {renderError ? (
        <div className="absolute inset-0 grid place-items-center bg-white/90 p-6">
          <div className="max-w-md rounded-xl border border-red-200 bg-red-50 p-4 text-center text-sm text-red-700" role="alert">
            The graph renderer could not start. {renderError}
          </div>
        </div>
      ) : null}
      <span id="graph-canvas-instructions" className="sr-only">
        Drag to pan; use a mouse wheel, touchpad, Control or Command plus wheel, or pinch to zoom. Shift-drag selects nodes, and question mark opens shortcuts.
      </span>
    </div>
  );
}));

export default DisclosureGraphCanvas;
