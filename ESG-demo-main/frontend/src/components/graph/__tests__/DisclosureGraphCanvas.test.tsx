import { act, createRef } from "react";
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CanvasEvent,
  EdgeEvent,
  GraphEvent,
  NodeEvent,
} from "@antv/g6";
import DisclosureGraphCanvas, {
  calculateCircleKeyShapeFit,
  combineCircleKeyShapeBounds,
  type DisclosureGraphCanvasHandle,
  wrapKumuLabel,
} from "../DisclosureGraphCanvas";
import type {
  GraphDisplayData,
  GraphDisplayNode,
} from "@/features/graph/types";

const graphHarness = vi.hoisted(() => ({
  instances: [] as unknown[],
}));

vi.mock("@antv/g6", () => {
  const events = {
    CanvasEvent: {
      CLICK: "canvas:click",
      DRAG: "canvas:drag",
      DRAG_END: "canvas:dragend",
      DRAG_START: "canvas:dragstart",
    },
    EdgeEvent: {
      CLICK: "edge:click",
      POINTER_MOVE: "edge:pointermove",
      POINTER_OUT: "edge:pointerout",
      POINTER_OVER: "edge:pointerover",
    },
    GraphEvent: {
      AFTER_TRANSFORM: "graph:aftertransform",
      BEFORE_TRANSFORM: "graph:beforetransform",
    },
    NodeEvent: {
      CLICK: "node:click",
      DBLCLICK: "node:dblclick",
      DRAG_END: "node:dragend",
      DRAG_START: "node:dragstart",
      POINTER_MOVE: "node:pointermove",
      POINTER_OUT: "node:pointerout",
      POINTER_OVER: "node:pointerover",
    },
  };

  class GraphMock {
    options: Record<string, any>;
    nodeData: any[];
    edgeData: any[];
    states = new Map<string, string[]>();
    positions = new Map<string, [number, number]>();
    handlers = new Map<string, Array<(event: any) => void>>();
    zoom = 0.75;
    viewportPosition: [number, number] = [0, 0];
    viewportSize: [number, number] = [1200, 800];

    constructor(options: Record<string, any>) {
      this.options = options;
      this.nodeData = [...(options.data?.nodes || [])];
      this.edgeData = [...(options.data?.edges || [])];
      this.nodeData.forEach((node, index) => {
        this.positions.set(String(node.id), [20 + index * 30, 30 + index * 40]);
      });
      graphHarness.instances.push(this);
    }

    on = vi.fn((event: string, handler: (event: any) => void) => {
      const listeners = this.handlers.get(event) || [];
      listeners.push(handler);
      this.handlers.set(event, listeners);
    });

    emit(event: string, payload: Record<string, unknown> = {}) {
      for (const handler of this.handlers.get(event) || []) handler(payload);
    }

    render = vi.fn(async () => undefined);
    destroy = vi.fn();
    draw = vi.fn(async () => undefined);
    layout = vi.fn(async () => undefined);
    stopLayout = vi.fn();
    setLayout = vi.fn((layout: Record<string, unknown>) => {
      this.options.layout = layout;
    });
    setTransforms = vi.fn((transforms: unknown[]) => {
      this.options.transforms = transforms;
    });
    setData = vi.fn((data: { nodes?: any[]; edges?: any[] }) => {
      this.nodeData = [...(data.nodes || [])];
      this.edgeData = [...(data.edges || [])];
      this.nodeData.forEach((node, index) => {
        const x = Number(node.style?.x ?? 20 + index * 30);
        const y = Number(node.style?.y ?? 30 + index * 40);
        this.positions.set(String(node.id), [x, y]);
      });
    });
    setSize = vi.fn();

    getNodeData = vi.fn((id?: string) => {
      if (id === undefined) return this.nodeData;
      return this.nodeData.find((node) => String(node.id) === String(id));
    });

    getEdgeData = vi.fn((id?: string) => {
      if (id === undefined) return this.edgeData;
      return this.edgeData.find((edge) => String(edge.id) === String(id));
    });

    hasNode = vi.fn((id: string) =>
      this.nodeData.some((node) => String(node.id) === String(id)),
    );
    hasEdge = vi.fn((id: string) =>
      this.edgeData.some((edge) => String(edge.id) === String(id)),
    );
    getElementState = vi.fn((id: string) => [...(this.states.get(String(id)) || [])]);
    setElementState = vi.fn(async (next: Record<string, string[]>) => {
      for (const [id, states] of Object.entries(next)) {
        this.states.set(String(id), [...states]);
      }
    });

    getElementPosition = vi.fn((id: string) =>
      this.positions.get(String(id)) || [0, 0],
    );
    getElementRenderStyle = vi.fn((id: string) => {
      const node = this.nodeData.find((item) => String(item.id) === String(id));
      const style = this.options.node?.style;
      return typeof style === "function" ? style(node) : (style || {});
    });
    updateNodeData = vi.fn((updates: Array<{ id: string; style?: { x?: number; y?: number } }>) => {
      for (const update of updates) {
        const previous = this.positions.get(String(update.id)) || [0, 0];
        this.positions.set(String(update.id), [
          Number(update.style?.x ?? previous[0]),
          Number(update.style?.y ?? previous[1]),
        ]);
      }
    });

    getZoom = vi.fn(() => this.zoom);
    getZoomRange = vi.fn(() => this.options.zoomRange || [0.12, 4]);
    getSize = vi.fn(() => this.viewportSize);
    getCanvasCenter = vi.fn(() => [this.viewportSize[0] / 2, this.viewportSize[1] / 2]);
    getViewportByCanvas = vi.fn((point: [number, number]) => [
      point[0] * this.zoom + this.viewportPosition[0],
      point[1] * this.zoom + this.viewportPosition[1],
    ]);
    zoomBy = vi.fn(async (ratio: number) => {
      this.zoom *= ratio;
    });
    zoomTo = vi.fn(async (zoom: number, _animation?: unknown, origin?: [number, number]) => {
      if (origin) {
        const worldAtOrigin: [number, number] = [
          (origin[0] - this.viewportPosition[0]) / this.zoom,
          (origin[1] - this.viewportPosition[1]) / this.zoom,
        ];
        this.viewportPosition = [
          origin[0] - worldAtOrigin[0] * zoom,
          origin[1] - worldAtOrigin[1] * zoom,
        ];
      }
      this.zoom = zoom;
    });
    fitView = vi.fn(async () => undefined);
    fitCenter = vi.fn(async () => undefined);
    focusElement = vi.fn(async () => undefined);
    translateBy = vi.fn(async (offset: [number, number]) => {
      this.viewportPosition = [
        this.viewportPosition[0] + offset[0],
        this.viewportPosition[1] + offset[1],
      ];
    });
    translateTo = vi.fn(async (position: [number, number]) => {
      this.viewportPosition = position;
    });
    getPosition = vi.fn(() => this.viewportPosition);
  }

  return { Graph: GraphMock, ...events };
});

interface GraphMockInstance {
  options: Record<string, any>;
  states: Map<string, string[]>;
  positions: Map<string, [number, number]>;
  zoom: number;
  emit: (event: string, payload?: Record<string, unknown>) => void;
  render: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
  draw: ReturnType<typeof vi.fn>;
  layout: ReturnType<typeof vi.fn>;
  stopLayout: ReturnType<typeof vi.fn>;
  setLayout: ReturnType<typeof vi.fn>;
  setTransforms: ReturnType<typeof vi.fn>;
  setData: ReturnType<typeof vi.fn>;
  updateNodeData: ReturnType<typeof vi.fn>;
  getElementState: (id: string) => string[];
  getElementRenderStyle: ReturnType<typeof vi.fn>;
  setElementState: ReturnType<typeof vi.fn>;
  focusElement: ReturnType<typeof vi.fn>;
  fitView: ReturnType<typeof vi.fn>;
  fitCenter: ReturnType<typeof vi.fn>;
  zoomBy: ReturnType<typeof vi.fn>;
  zoomTo: ReturnType<typeof vi.fn>;
  translateBy: ReturnType<typeof vi.fn>;
  translateTo: ReturnType<typeof vi.fn>;
  getZoom: ReturnType<typeof vi.fn>;
  getZoomRange: ReturnType<typeof vi.fn>;
  getSize: ReturnType<typeof vi.fn>;
  getCanvasCenter: ReturnType<typeof vi.fn>;
  getViewportByCanvas: ReturnType<typeof vi.fn>;
}

const graphData: GraphDisplayData = {
  nodes: ["a", "b", "c", "d"].map((id, index) => ({
    id,
    type: index === 0 ? "report" : "metric",
    label: id.toUpperCase(),
    properties: index === 0 ? { overall_score: 0.8 } : { metric_code: `CODE-${id}` },
  })),
  edges: [
    { id: "edge:a-b", type: "relation", source: "a", target: "b", properties: {} },
    { id: "edge:b-c", type: "relation", source: "b", target: "c", properties: {} },
    { id: "edge:c-d", type: "relation", source: "c", target: "d", properties: {} },
  ],
  underlyingDisclosureCount: 3,
};

const kumuStyleGraphData: GraphDisplayData = {
  nodes: [
    {
      id: "report",
      type: "report",
      label: "Example ESG Report",
      properties: { overall_score: 0.8 },
    },
    {
      id: "metric",
      type: "metric",
      label: "Example ESG metric",
      properties: { metric_code: "TC-TEST-000.A" },
    },
  ],
  edges: [
    {
      id: "edge:fully",
      type: "relation",
      source: "report",
      target: "metric",
      properties: { disclosure_status: "fully_disclosed" },
    },
    {
      id: "edge:partially",
      type: "relation",
      source: "report",
      target: "metric",
      properties: { disclosure_status: "partially_disclosed" },
    },
    {
      id: "edge:not",
      type: "relation",
      source: "report",
      target: "metric",
      properties: { disclosure_status: "not_disclosed" },
    },
    {
      id: "edge:curve-negative",
      type: "relation",
      source: "report",
      target: "metric",
      properties: { tags: ["curve-n70"] },
    },
    {
      id: "edge:curve-positive",
      type: "relation",
      source: "report",
      target: "metric",
      properties: { tags: "curve-p25" },
    },
    {
      id: "edge:curve-zero",
      type: "relation",
      source: "report",
      target: "metric",
      properties: { tags: "curve-0" },
    },
  ],
  underlyingDisclosureCount: 3,
};

function latestGraph(): GraphMockInstance {
  const graph = graphHarness.instances.at(-1);
  if (!graph) throw new Error("Graph mock was not constructed");
  return graph as GraphMockInstance;
}

function callbacks() {
  return {
    onNodeSelect: vi.fn(),
    onEdgeSelect: vi.fn(),
    onMetricGroupToggle: vi.fn(),
    onZoomChange: vi.fn(),
    onSelectionChange: vi.fn(),
    onPinnedChange: vi.fn(),
    onFocusDegreeChange: vi.fn(),
    onLayoutPausedChange: vi.fn(),
  };
}

function setReducedMotionPreference(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn((query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)" && matches,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(() => true),
    })),
  });
}

beforeEach(() => {
  setReducedMotionPreference(false);
});

function renderCanvas({
  data = graphData,
  layout = "force",
  layoutPaused = false,
  resetNonce = 0,
  storageKey = "graph-test-positions",
}: {
  data?: GraphDisplayData;
  layout?: "force" | "hierarchical" | "radial";
  layoutPaused?: boolean;
  resetNonce?: number;
  storageKey?: string;
} = {}) {
  const ref = createRef<DisclosureGraphCanvasHandle>();
  const handlers = callbacks();
  const props = {
    data,
    graphRevision: "revision-1",
    layout,
    layoutPaused,
    positionStorageKey: storageKey,
    resetNonce,
    showMinimap: false,
    ...handlers,
  };
  const view = render(<DisclosureGraphCanvas ref={ref} {...props} />);
  return { ...view, handlers, props, ref };
}

async function graphReady() {
  await waitFor(() => expect(graphHarness.instances.length).toBeGreaterThan(0));
  await waitFor(() => expect(latestGraph().render).toHaveBeenCalledTimes(1));
  await act(async () => {
    await Promise.resolve();
  });
  return latestGraph();
}

function expectState(graph: GraphMockInstance, id: string, state: string) {
  expect(graph.getElementState(id)).toContain(state);
}

function g6NodeDatum(graph: GraphMockInstance, id: string) {
  const datum = graph.options.data.nodes.find(
    (node: { id: string }) => String(node.id) === id,
  );
  if (!datum) throw new Error(`Missing G6 node datum: ${id}`);
  return datum;
}

function g6EdgeDatum(graph: GraphMockInstance, id: string) {
  const datum = graph.options.data.edges.find(
    (edge: { id: string }) => String(edge.id) === id,
  );
  if (!datum) throw new Error(`Missing G6 edge datum: ${id}`);
  return datum;
}

function g6Behavior(
  graph: GraphMockInstance,
  type: string,
  occurrence = 0,
): Record<string, any> {
  const matches = graph.options.behaviors.filter(
    (behavior: { type?: string }) => behavior.type === type,
  );
  const behavior = matches[occurrence];
  if (!behavior) throw new Error(`Missing G6 behavior: ${type}[${occurrence}]`);
  return behavior;
}

describe("DisclosureGraphCanvas Kumu visual contract", () => {
  beforeEach(() => {
    graphHarness.instances.length = 0;
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("uses the attachment's light canvas and force-layout constants", async () => {
    renderCanvas({ data: kumuStyleGraphData });
    const graph = await graphReady();

    expect(graph.options.background).toBe("#FAFBF9");
    expect(graph.options.layout).toMatchObject({
      type: "d3-force",
      centerStrength: 0.00003,
      nodeStrength: -1900,
      linkDistance: 560,
      edgeStrength: 0.06,
      x: { strength: 0.00003 },
      y: { strength: 0.00003 },
    });
  });

  it("uses Kumu's character-count wrapping instead of a one-line pixel ellipsis", async () => {
    expect(wrapKumuLabel("Dell Technologies FY25 Impact By The Numbers", 13)).toBe(
      "Dell Technologies\nFY25 Impact By\nThe Numbers",
    );
    expect(wrapKumuLabel("TC-HW-430a.1 — Tier 1 supplier facility audits", 21)).toBe(
      "TC-HW-430a.1 — Tier 1\nsupplier facility audits",
    );

    renderCanvas({ data: kumuStyleGraphData });
    const graph = await graphReady();
    const metricStyle = graph.options.node.style(g6NodeDatum(graph, "metric"));
    expect(metricStyle).toMatchObject({
      labelWordWrap: false,
      labelLineHeight: 32,
    });
  });

  it("renders both reports and metrics as Arial-labelled circles", async () => {
    renderCanvas({ data: kumuStyleGraphData });
    const graph = await graphReady();
    const report = g6NodeDatum(graph, "report");
    const metric = g6NodeDatum(graph, "metric");

    const nodeType = (datum: Record<string, unknown>) =>
      typeof graph.options.node.type === "function"
        ? graph.options.node.type(datum)
        : graph.options.node.type;
    expect(nodeType(report)).toBe("circle");
    expect(nodeType(metric)).toBe("circle");

    const reportStyle = graph.options.node.style(report);
    expect(reportStyle).toMatchObject({
      fill: "#123F35",
      stroke: "#FFFFFF",
    });
    expect(reportStyle.labelFontFamily).toContain("Arial");

    const metricStyle = graph.options.node.style(metric);
    expect(metricStyle).toMatchObject({
      fill: "#FFFFFF",
      stroke: "#526E63",
    });
    expect(metricStyle.labelFontFamily).toContain("Arial");
  });

  it.each([
    ["edge:fully", "#008A5B", 7, undefined, 1],
    ["edge:partially", "#E98D00", 5.7, [12, 6], 1],
    ["edge:not", "#D94343", 4, [2, 8], 0.92],
  ] as const)(
    "maps %s to the attachment's disclosure relationship style",
    async (edgeId, stroke, lineWidth, lineDash, strokeOpacity) => {
      renderCanvas({ data: kumuStyleGraphData });
      const graph = await graphReady();
      const edgeStyle = graph.options.edge.style(g6EdgeDatum(graph, edgeId));

      expect(edgeStyle).toMatchObject({ stroke, lineWidth, strokeOpacity });
      expect(edgeStyle.lineDash).toEqual(lineDash);
    },
  );

  it.each([
    ["edge:curve-negative", "quadratic", -392],
    ["edge:curve-positive", "quadratic", 140],
    ["edge:curve-zero", "line", 0],
  ] as const)(
    "maps the Kumu curvature tag on %s to a G6 curve offset",
    async (edgeId, edgeType, curveOffset) => {
      renderCanvas({ data: kumuStyleGraphData });
      const graph = await graphReady();
      const edge = g6EdgeDatum(graph, edgeId);

      expect(graph.options.edge.type(edge)).toBe(edgeType);
      expect(graph.options.edge.style(edge).curveOffset).toBe(curveOffset);
    },
  );
});

describe("DisclosureGraphCanvas motion and tactile-feedback contract", () => {
  beforeEach(() => {
    graphHarness.instances.length = 0;
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("uses short eased state transitions with a live force simulation", async () => {
    renderCanvas();
    const graph = await graphReady();

    const motion = graph.options.animation;
    expect(motion).not.toBe(false);
    expect(motion).toEqual(
      expect.objectContaining({
        duration: expect.any(Number),
        easing: expect.any(String),
      }),
    );
    expect(motion.duration).toBeGreaterThanOrEqual(120);
    expect(motion.duration).toBeLessThanOrEqual(320);
    expect(motion.easing).toMatch(/ease|cubic|quad/i);

    const nodeStateAnimation = graph.options.node.animation?.state;
    expect(nodeStateAnimation).toEqual(expect.any(Array));
    expect(
      nodeStateAnimation.flatMap((effect: { fields?: string[] }) => effect.fields || []),
    ).toEqual(expect.arrayContaining(["size", "opacity", "stroke", "lineWidth"]));

    const edgeStateAnimation = graph.options.edge.animation?.state;
    expect(edgeStateAnimation).toEqual(expect.any(Array));
    expect(
      edgeStateAnimation.flatMap((effect: { fields?: string[] }) => effect.fields || []),
    ).toEqual(expect.arrayContaining(["stroke", "lineWidth", "strokeOpacity"]));

    // G6 uses this flag to stream iterative Force ticks; it does not tween
    // individual ticks because the runtime applies each onTick with animation=false.
    expect(graph.options.layout.animation).toBe(true);
    expect(graph.options.layout).toMatchObject({
      alphaMin: 0.002,
      alphaDecay: 0.0228,
      velocityDecay: 0.28,
      collideIterations: 2,
    });
    for (const type of ["click-select", "brush-select", "hover-activate"]) {
      expect(g6Behavior(graph, type).animation).toBe(true);
    }
  });

  it("gives drag start and release immediate animated dragging-to-pinned feedback", async () => {
    const storageKey = "motion-drag-positions";
    renderCanvas({ storageKey });
    const graph = await graphReady();
    graph.setElementState.mockClear();
    graph.positions.set("a", [145, 233]);

    await act(async () => {
      graph.emit(NodeEvent.DRAG_START, { target: { id: "a" } });
      await Promise.resolve();
    });
    expectState(graph, "a", "dragging");
    expect(graph.options.node.state.dragging).toEqual(
      expect.objectContaining({ halo: true }),
    );

    await act(async () => {
      graph.emit(NodeEvent.DRAG_END, { target: { id: "a" } });
      await Promise.resolve();
    });
    expect(graph.getElementState("a")).not.toContain("dragging");
    expectState(graph, "a", "pinned");
    expect(JSON.parse(window.localStorage.getItem(storageKey) || "{}")).toMatchObject({
      positions: { a: { x: 145, y: 233 } },
    });
    expect(graph.setElementState).toHaveBeenLastCalledWith(
      expect.objectContaining({
        a: expect.arrayContaining(["pinned"]),
      }),
      true,
    );
  });

  it("disables decorative and viewport animation for reduced-motion users", async () => {
    setReducedMotionPreference(true);
    const { ref } = renderCanvas();
    const graph = await graphReady();

    expect(graph.options.animation).toBe(false);
    expect(graph.options.layout.animation).toBe(false);
    for (const type of ["click-select", "brush-select", "hover-activate"]) {
      expect(g6Behavior(graph, type).animation).toBe(false);
    }

    graph.zoom = 0.5;
    await act(async () => {
      await ref.current?.zoomIn();
      await ref.current?.focusNode("b");
    });
    expect(graph.zoomBy).toHaveBeenCalledWith(1.2, false);
    expect(graph.focusElement).toHaveBeenCalledWith("b", false);
    expect(graph.zoomTo).toHaveBeenCalledWith(0.9, false);
  });
});

describe("DisclosureGraphCanvas imperative interaction contract", () => {
  beforeEach(() => {
    graphHarness.instances.length = 0;
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("selects one node, focuses its n-degree neighborhood, and centers it", async () => {
    const { handlers, ref } = renderCanvas();
    const graph = await graphReady();
    handlers.onSelectionChange.mockClear();
    handlers.onFocusDegreeChange.mockClear();
    graph.zoom = 0.5;

    await act(async () => {
      await ref.current?.focusNode("b");
    });

    expect(graph.focusElement).toHaveBeenLastCalledWith("b", { duration: 300 });
    expect(graph.zoomTo).toHaveBeenLastCalledWith(0.9, { duration: 180 });
    expectState(graph, "b", "selected");
    expectState(graph, "a", "selectionNeighbor");
    expectState(graph, "c", "selectionNeighbor");
    expectState(graph, "d", "selectionInactive");
    expectState(graph, "edge:a-b", "selectionNeighbor");
    expectState(graph, "edge:b-c", "selectionNeighbor");
    expectState(graph, "edge:c-d", "selectionInactive");
    expect(handlers.onSelectionChange).toHaveBeenLastCalledWith(["b"]);

    await act(async () => {
      await ref.current?.focusSelection(1);
    });
    expect(handlers.onFocusDegreeChange).toHaveBeenLastCalledWith(1);
    for (const id of ["a", "b", "c", "edge:a-b", "edge:b-c"]) {
      expectState(graph, id, "focusActive");
    }
    expectState(graph, "d", "focusInactive");
    expectState(graph, "edge:c-d", "focusInactive");

    await act(async () => {
      await ref.current?.focusSelection(2);
    });
    expect(handlers.onFocusDegreeChange).toHaveBeenLastCalledWith(2);
    expectState(graph, "d", "focusActive");
    expectState(graph, "edge:c-d", "focusActive");
  });

  it("selects all, fits and pans the selection, then clears every interaction state", async () => {
    const { handlers, ref } = renderCanvas();
    const graph = await graphReady();
    handlers.onSelectionChange.mockClear();

    await act(async () => {
      await ref.current?.selectAll();
      await ref.current?.fitSelection();
      await ref.current?.panBy(24, -16);
    });

    for (const id of ["a", "b", "c", "d"]) expectState(graph, id, "selected");
    expect(graph.focusElement).toHaveBeenLastCalledWith(
      ["a", "b", "c", "d"],
      { duration: 300 },
    );
    expect(graph.translateBy).toHaveBeenLastCalledWith([24, -16], { duration: 140 });
    expect(handlers.onSelectionChange).toHaveBeenLastCalledWith(["a", "b", "c", "d"]);

    await act(async () => {
      await ref.current?.focusSelection(1);
      await ref.current?.clearSelection();
    });

    for (const id of ["a", "b", "c", "d", "edge:a-b", "edge:b-c", "edge:c-d"]) {
      expect(graph.getElementState(id)).not.toEqual(
        expect.arrayContaining([
          "selected",
          "selectionNeighbor",
          "selectionInactive",
          "focusActive",
          "focusInactive",
        ]),
      );
    }
    expect(handlers.onNodeSelect).toHaveBeenLastCalledWith(null);
    expect(handlers.onFocusDegreeChange).toHaveBeenLastCalledWith(null);
    expect(handlers.onSelectionChange).toHaveBeenLastCalledWith([]);
  });

  it("pins and unpins selected coordinates and controls the force layout", async () => {
    const storageKey = "pinned-graph";
    const { handlers, ref } = renderCanvas({ storageKey });
    const graph = await graphReady();

    await act(async () => {
      await ref.current?.focusNode("b");
      await ref.current?.pinSelection();
    });

    const stored = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
    expect(stored).toMatchObject({
      revision: "revision-1",
      positions: { b: { x: 50, y: 70 } },
    });
    expectState(graph, "b", "pinned");
    expect(handlers.onPinnedChange).toHaveBeenLastCalledWith(["b"]);

    ref.current?.pauseLayout();
    expect(graph.stopLayout).toHaveBeenCalledTimes(1);

    graph.layout.mockClear();
    await act(async () => {
      await ref.current?.resumeLayout();
      await ref.current?.bumpLayout();
    });
    expect(graph.setLayout).toHaveBeenCalled();
    expect(graph.layout).toHaveBeenCalledTimes(2);
    expect(graph.updateNodeData).toHaveBeenCalledWith([
      { id: "b", style: { x: 50, y: 70 } },
    ]);

    graph.layout.mockClear();
    await act(async () => {
      await ref.current?.unpinSelection();
    });
    expect(JSON.parse(window.localStorage.getItem(storageKey) || "{}").positions).toEqual({});
    expect(graph.getElementState("b")).not.toContain("pinned");
    expect(handlers.onPinnedChange).toHaveBeenLastCalledWith([]);
    expect(graph.layout).toHaveBeenCalledTimes(1);
  });

  it("synchronizes pause, resume, and bump commands with the controlled layout state", async () => {
    const { handlers, ref } = renderCanvas();
    const graph = await graphReady();
    handlers.onLayoutPausedChange.mockClear();

    act(() => ref.current?.pauseLayout());
    expect(graph.stopLayout).toHaveBeenCalledTimes(1);
    expect(handlers.onLayoutPausedChange).toHaveBeenNthCalledWith(1, true);

    await act(async () => {
      await ref.current?.resumeLayout();
      await ref.current?.bumpLayout();
    });
    expect(handlers.onLayoutPausedChange).toHaveBeenNthCalledWith(2, false);
    expect(handlers.onLayoutPausedChange).toHaveBeenNthCalledWith(3, false);
    expect(graph.layout).toHaveBeenCalledTimes(2);
  });

  it("provides zoom, fit, and actual-size viewport commands", async () => {
    const { handlers, ref } = renderCanvas();
    const graph = await graphReady();
    graph.fitView.mockClear();
    handlers.onZoomChange.mockClear();

    await act(async () => {
      await ref.current?.zoomIn();
      await ref.current?.zoomOut();
      await ref.current?.fitView();
      await ref.current?.actualSize();
    });

    expect(graph.zoomBy).toHaveBeenNthCalledWith(1, 1.2, { duration: 160 });
    expect(graph.zoomBy).toHaveBeenNthCalledWith(2, 1 / 1.2, { duration: 160 });
    expect(graph.fitView).toHaveBeenCalledWith(
      { direction: "both" },
      { duration: 320 },
    );
    expect(graph.zoomTo).toHaveBeenLastCalledWith(1, { duration: 220 });
    expect(graph.fitCenter).toHaveBeenLastCalledWith({ duration: 220 });
    expect(handlers.onZoomChange).toHaveBeenCalledTimes(4);
  });
});

describe("DisclosureGraphCanvas G6 event and reset contract", () => {
  beforeEach(() => {
    graphHarness.instances.length = 0;
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("maps node, edge, canvas, double-click, and drag events to application callbacks", async () => {
    const groupNode: GraphDisplayNode = {
      id: "group",
      type: "metric",
      label: "TC-TEST (2)",
      synthetic: true,
      properties: { metric_code: "TC-TEST" },
    };
    const data = { ...graphData, nodes: [...graphData.nodes, groupNode] };
    const storageKey = "event-positions";
    const { handlers } = renderCanvas({ data, storageKey });
    const graph = await graphReady();

    act(() => {
      graph.emit(NodeEvent.CLICK, { target: { id: "b" } });
      graph.emit(NodeEvent.CLICK, { target: { id: "group" } });
      graph.emit(EdgeEvent.CLICK, { target: { id: "edge:a-b" } });
    });
    expect(handlers.onNodeSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: "b" }),
    );
    expect(handlers.onMetricGroupToggle).toHaveBeenCalledWith("TC-TEST");
    expect(handlers.onEdgeSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: "edge:a-b" }),
    );

    act(() => {
      graph.emit(NodeEvent.DBLCLICK, { target: { id: "c" } });
    });
    await waitFor(() =>
      expect(graph.focusElement).toHaveBeenCalledWith("c", { duration: 300 }),
    );
    expectState(graph, "c", "selected");

    graph.states.set("c", []);
    graph.states.set("b", ["selected"]);
    graph.positions.set("a", [101, 102]);
    graph.positions.set("b", [201, 202]);
    act(() => {
      graph.emit(NodeEvent.DRAG_END, { target: { id: "a" } });
    });
    expect(JSON.parse(window.localStorage.getItem(storageKey) || "{}").positions).toEqual({
      a: { x: 101, y: 102 },
      b: { x: 201, y: 202 },
    });
    expectState(graph, "a", "pinned");
    expectState(graph, "b", "pinned");

    act(() => {
      graph.emit(CanvasEvent.CLICK, { target: { id: "canvas" } });
      graph.emit(GraphEvent.BEFORE_TRANSFORM);
      graph.emit(GraphEvent.AFTER_TRANSFORM);
    });
    expect(handlers.onNodeSelect).toHaveBeenLastCalledWith(null);
    await waitFor(() =>
      expect(handlers.onFocusDegreeChange).toHaveBeenLastCalledWith(null),
    );
  });

  it("recreates a clean graph when resetNonce changes after stored pins are cleared", async () => {
    const storageKey = "reset-positions";
    window.localStorage.setItem(
      storageKey,
      JSON.stringify({ revision: "old", positions: { b: { x: 8, y: 9 } } }),
    );
    const ref = createRef<DisclosureGraphCanvasHandle>();
    const handlers = callbacks();
    const props = {
      data: graphData,
      graphRevision: "revision-1",
      layout: "force" as const,
      positionStorageKey: storageKey,
      showMinimap: false,
      ...handlers,
    };
    const view = render(
      <DisclosureGraphCanvas ref={ref} resetNonce={0} {...props} />,
    );
    const first = await graphReady();
    await waitFor(() => expect(first.updateNodeData).toHaveBeenCalled());
    expectState(first, "b", "pinned");

    window.localStorage.removeItem(storageKey);
    view.rerender(
      <DisclosureGraphCanvas ref={ref} resetNonce={1} {...props} />,
    );
    await waitFor(() => expect(graphHarness.instances).toHaveLength(2));
    const second = latestGraph();
    await waitFor(() => expect(second.render).toHaveBeenCalledTimes(1));

    expect(first.destroy).toHaveBeenCalledTimes(1);
    expect(second.updateNodeData).not.toHaveBeenCalled();
    await waitFor(() => expect(second.zoomTo).toHaveBeenCalled());
    expect(second.translateTo).not.toHaveBeenCalled();
    expect(second.translateBy).toHaveBeenCalled();
    expect(second.fitView).not.toHaveBeenCalled();
    expect(handlers.onPinnedChange).toHaveBeenLastCalledWith([]);
  });

  it("updates filtered or expanded graph data in place without resetting the viewport", async () => {
    const ref = createRef<DisclosureGraphCanvasHandle>();
    const handlers = callbacks();
    const props = {
      graphRevision: "revision-1",
      layout: "force" as const,
      positionStorageKey: "incremental-data-positions",
      resetNonce: 0,
      showMinimap: false,
      ...handlers,
    };
    const view = render(
      <DisclosureGraphCanvas ref={ref} data={graphData} {...props} />,
    );
    const graph = await graphReady();
    graph.positions.set("a", [321, 654]);
    graph.fitView.mockClear();
    graph.layout.mockClear();

    const expandedData: GraphDisplayData = {
      ...graphData,
      nodes: [
        ...graphData.nodes,
        {
          id: "e",
          type: "metric",
          label: "E",
          properties: { metric_code: "CODE-E" },
        },
      ],
      edges: [
        ...graphData.edges,
        { id: "edge:a-e", type: "relation", source: "a", target: "e", properties: {} },
      ],
    };
    view.rerender(
      <DisclosureGraphCanvas ref={ref} data={expandedData} {...props} />,
    );

    await waitFor(() => expect(graph.setData).toHaveBeenCalledTimes(1));
    const projected = graph.setData.mock.calls[0][0];
    expect(projected.nodes.find((node: { id: string }) => node.id === "a").style).toMatchObject({
      x: 321,
      y: 654,
    });
    const addedStyle = projected.nodes.find((node: { id: string }) => node.id === "e").style;
    expect([addedStyle.x, addedStyle.y]).not.toEqual([0, 0]);
    expect(graphHarness.instances).toHaveLength(1);
    expect(graph.destroy).not.toHaveBeenCalled();
    expect(graph.fitView).not.toHaveBeenCalled();
    expect(graph.layout).toHaveBeenCalledTimes(1);
  });

  it("redraws property changes without rerunning a non-force layout", async () => {
    const ref = createRef<DisclosureGraphCanvasHandle>();
    const handlers = callbacks();
    const props = {
      graphRevision: "revision-1",
      layout: "hierarchical" as const,
      positionStorageKey: "property-update-positions",
      resetNonce: 0,
      showMinimap: false,
      ...handlers,
    };
    const view = render(
      <DisclosureGraphCanvas ref={ref} data={graphData} {...props} />,
    );
    const graph = await graphReady();
    graph.draw.mockClear();
    graph.layout.mockClear();
    graph.stopLayout.mockClear();

    const updatedData: GraphDisplayData = {
      ...graphData,
      nodes: graphData.nodes.map((node) => (
        node.id === "b" ? { ...node, label: "Updated metric label" } : node
      )),
    };
    view.rerender(
      <DisclosureGraphCanvas ref={ref} data={updatedData} {...props} />,
    );

    await waitFor(() => expect(graph.setData).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(graph.draw).toHaveBeenCalledTimes(1));
    expect(graph.stopLayout).not.toHaveBeenCalled();
    expect(graph.layout).not.toHaveBeenCalled();
    expect(graphHarness.instances).toHaveLength(1);
  });

  it("switches layouts in place so the canvas and selection do not blink", async () => {
    const ref = createRef<DisclosureGraphCanvasHandle>();
    const handlers = callbacks();
    const props = {
      data: graphData,
      graphRevision: "revision-1",
      positionStorageKey: "incremental-layout-positions",
      resetNonce: 0,
      showMinimap: false,
      ...handlers,
    };
    const view = render(
      <DisclosureGraphCanvas ref={ref} layout="force" {...props} />,
    );
    const graph = await graphReady();
    graph.setLayout.mockClear();
    graph.layout.mockClear();

    view.rerender(
      <DisclosureGraphCanvas ref={ref} layout="hierarchical" {...props} />,
    );

    await waitFor(() => expect(graph.setLayout).toHaveBeenCalledTimes(1));
    expect(graph.setLayout).toHaveBeenCalledWith(
      expect.objectContaining({ type: "dagre", animation: true }),
    );
    expect(graph.layout).toHaveBeenCalledTimes(1);
    expect(graphHarness.instances).toHaveLength(1);
    expect(graph.destroy).not.toHaveBeenCalled();
    expect(g6Behavior(graph, "drag-element-force").enable({ nativeEvent: { shiftKey: false } })).toBe(false);
    expect(g6Behavior(graph, "drag-element").enable({ nativeEvent: { shiftKey: false } })).toBe(true);
  });
});

describe("DisclosureGraphCanvas G6 behavior routing contract", () => {
  beforeEach(() => {
    graphHarness.instances.length = 0;
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("keeps Force drag, paused drag, and Shift brush gestures mutually exclusive", async () => {
    const { ref } = renderCanvas();
    const graph = await graphReady();
    const forceDrag = g6Behavior(graph, "drag-element-force");
    const pausedDrag = g6Behavior(graph, "drag-element");
    const plainGesture = { targetType: "node", nativeEvent: { shiftKey: false } };
    const shiftGesture = { targetType: "node", nativeEvent: { shiftKey: true } };

    expect(forceDrag.enable(plainGesture)).toBe(true);
    expect(pausedDrag.enable(plainGesture)).toBe(false);
    expect(forceDrag.enable(shiftGesture)).toBe(false);
    expect(pausedDrag.enable(shiftGesture)).toBe(false);

    act(() => ref.current?.pauseLayout());
    expect(forceDrag.enable(plainGesture)).toBe(false);
    expect(pausedDrag.enable(plainGesture)).toBe(true);
    expect(forceDrag.enable(shiftGesture)).toBe(false);
    expect(pausedDrag.enable(shiftGesture)).toBe(false);

    await act(async () => ref.current?.resumeLayout());
    expect(forceDrag.enable(plainGesture)).toBe(true);
    expect(pausedDrag.enable(plainGesture)).toBe(false);
  });

  it("disables ordinary drag on Shift outside Force layout", async () => {
    renderCanvas({ layout: "hierarchical" });
    const graph = await graphReady();
    const drag = g6Behavior(graph, "drag-element");

    expect(drag.enable({ nativeEvent: { shiftKey: false } })).toBe(true);
    expect(drag.enable({ nativeEvent: { shiftKey: true } })).toBe(false);
    const dormantForceDrag = g6Behavior(graph, "drag-element-force");
    expect(dormantForceDrag.enable({ nativeEvent: { shiftKey: false } })).toBe(false);
  });

  it("only starts brush selection from the canvas", async () => {
    renderCanvas();
    const graph = await graphReady();
    const brush = g6Behavior(graph, "brush-select");

    expect(brush.enable({ targetType: "canvas" })).toBe(true);
    expect(brush.enable({ targetType: "node" })).toBe(false);
    expect(brush.enable({ targetType: "edge" })).toBe(false);
  });

  it("supports touchpad, modified wheel, and pinch zoom without wheel panning", async () => {
    renderCanvas();
    const graph = await graphReady();
    const wheelZoom = g6Behavior(graph, "zoom-canvas");
    const controlWheelZoom = g6Behavior(graph, "zoom-canvas", 1);
    const metaWheelZoom = g6Behavior(graph, "zoom-canvas", 2);
    const pinchZoom = g6Behavior(graph, "zoom-canvas", 3);

    expect(wheelZoom).toMatchObject({
      key: "wheel-zoom",
      trigger: [],
      sensitivity: 0.28,
      preventDefault: true,
    });
    expect(controlWheelZoom).toMatchObject({
      key: "control-wheel-zoom",
      trigger: ["Control"],
      sensitivity: 0.28,
      preventDefault: true,
    });
    expect(metaWheelZoom).toMatchObject({
      key: "meta-wheel-zoom",
      trigger: ["Meta"],
      sensitivity: 0.28,
      preventDefault: true,
    });
    expect(pinchZoom).toMatchObject({
      key: "pinch-zoom",
      trigger: ["pinch"],
      sensitivity: 0.65,
      preventDefault: true,
    });
    expect(graph.options.behaviors).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ type: "scroll-canvas" })]),
    );
  });

  it("continues a quick canvas pan with one bounded release glide", async () => {
    renderCanvas();
    const graph = await graphReady();
    graph.translateBy.mockClear();

    act(() => {
      graph.emit(CanvasEvent.DRAG_START, {
        targetType: "canvas",
        nativeEvent: { shiftKey: false, timeStamp: 100 },
      });
      graph.emit(CanvasEvent.DRAG, {
        targetType: "canvas",
        movement: { x: 12, y: -5 },
        nativeEvent: { timeStamp: 116 },
      });
      graph.emit(CanvasEvent.DRAG, {
        targetType: "canvas",
        movement: { x: 10, y: -4 },
        nativeEvent: { timeStamp: 132 },
      });
      graph.emit(CanvasEvent.DRAG_END, { targetType: "canvas" });
    });

    expect(graph.translateBy).toHaveBeenCalledTimes(1);
    const [offset, animation] = graph.translateBy.mock.calls[0];
    expect(Math.abs(offset[0])).toBeLessThanOrEqual(280);
    expect(Math.abs(offset[1])).toBeLessThanOrEqual(280);
    expect(animation).toEqual({ duration: 320 });
  });
});

describe("DisclosureGraphCanvas G6 projection safety contract", () => {
  beforeEach(() => {
    graphHarness.instances.length = 0;
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  it("fits the first viewport to circle key shapes without shrinking for labels", async () => {
    renderCanvas({ data: kumuStyleGraphData });
    const graph = await graphReady();

    await waitFor(() => expect(graph.zoomTo).toHaveBeenCalled());
    expect(graph.zoomTo).toHaveBeenCalledWith(
      expect.any(Number),
      false,
      [600, 400],
    );
    expect(graph.translateBy).toHaveBeenCalledWith(
      [expect.any(Number), expect.any(Number)],
      false,
    );
    expect(graph.fitView).not.toHaveBeenCalled();
  });

  it("calculates circle bounds from diameter and stroke, independent of label length", () => {
    const bounds = combineCircleKeyShapeBounds([
      { x: 0, y: 0, size: 100, lineWidth: 10 },
      { x: 100, y: 0, size: [40, 80], lineWidth: 4 },
    ]);

    expect(bounds).toEqual({
      minX: -55,
      minY: -55,
      maxX: 122,
      maxY: 55,
      width: 177,
      height: 110,
      center: [33.5, 0],
    });
    expect(combineCircleKeyShapeBounds([])).toBeNull();
  });

  it("clamps key-shape fitting to the graph zoom range", () => {
    const compact = combineCircleKeyShapeBounds([
      { x: 0, y: 0, size: 100, lineWidth: 0 },
    ])!;
    expect(calculateCircleKeyShapeFit(
      compact,
      [1000, 700],
      [36, 36, 36, 36],
      [0.12, 4],
    )).toEqual({
      zoom: 4,
      graphCenter: [0, 0],
      viewportCenter: [500, 350],
    });

    const enormous = combineCircleKeyShapeBounds([
      { x: 0, y: 0, size: 20_000, lineWidth: 0 },
    ])!;
    expect(calculateCircleKeyShapeFit(
      enormous,
      [1000, 700],
      [36, 36, 36, 36],
      [0.12, 4],
    ).zoom).toBe(0.12);
  });

  it("uses a compact symmetric inset because Kumu controls overlay the canvas", async () => {
    renderCanvas();
    const graph = await graphReady();

    expect(graph.options.padding).toEqual([36, 36, 36, 36]);
  });

  it("lets the parallel-edge transform retain its generated type and curve offset", async () => {
    const data: GraphDisplayData = {
      nodes: graphData.nodes.slice(0, 2),
      edges: [
        { id: "parallel:1", type: "relation", source: "a", target: "b", properties: {} },
        { id: "parallel:2", type: "relation", source: "a", target: "b", properties: {} },
      ],
      underlyingDisclosureCount: 2,
    };
    renderCanvas({ data });
    const graph = await graphReady();

    expect(graph.options.transforms).toEqual([
      expect.objectContaining({
        type: "process-parallel-edges",
        mode: "bundle",
        distance: 18,
        edges: ["parallel:1", "parallel:2"],
      }),
    ]);

    const transformed = {
      ...g6EdgeDatum(graph, "parallel:1"),
      type: "quadratic",
    };
    expect(graph.options.edge.type(transformed)).toBe("quadratic");
    expect(graph.options.edge.style(transformed)).not.toHaveProperty("curveOffset");
  });

  it("uses expanded disclosure properties when has_disclosure has no direct status", async () => {
    const data: GraphDisplayData = {
      nodes: graphData.nodes.slice(0, 2),
      edges: [
        {
          id: "expanded:has-disclosure",
          type: "has_disclosure",
          source: "a",
          target: "b",
          properties: {},
          disclosure: {
            id: "disclosure:expanded",
            type: "disclosure",
            label: "Disclosed",
            properties: { status: "fully_disclosed" },
          },
        },
      ],
      underlyingDisclosureCount: 1,
    };
    renderCanvas({ data });
    const graph = await graphReady();
    const edgeStyle = graph.options.edge.style(
      g6EdgeDatum(graph, "expanded:has-disclosure"),
    );

    expect(edgeStyle).toMatchObject({
      stroke: "#008A5B",
      lineWidth: 7,
      strokeOpacity: 1,
    });
  });
});
