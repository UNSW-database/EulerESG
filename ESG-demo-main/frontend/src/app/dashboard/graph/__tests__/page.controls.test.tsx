import type { ForwardedRef, PropsWithChildren } from "react";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  actualSize: vi.fn(),
  dynamicIndex: 0,
  files: [] as Array<Record<string, unknown>>,
  getCompanies: vi.fn(),
  getReportDisclosureGraph: vi.fn(),
  graphCanvasProps: null as Record<string, unknown> | null,
  loadFilesFromBackend: vi.fn(),
  replace: vi.fn(),
  searchParams: "owner=reports&file_id=file-1",
  selectChanges: {} as Record<string, ((value: unknown) => void) | undefined>,
  selectRenderCount: 0,
  zoomIn: vi.fn(),
  zoomOut: vi.fn(),
}));

vi.mock("next/dynamic", async () => {
  const React = await import("react");
  return {
    default: () => {
      const index = mocks.dynamicIndex++;
      if (index === 0) {
        return React.forwardRef(function MockGraphCanvas(
          props: Record<string, unknown>,
          ref: ForwardedRef<unknown>,
        ) {
          React.useImperativeHandle(ref, () => ({
            actualSize: mocks.actualSize,
            zoomIn: mocks.zoomIn,
            zoomOut: mocks.zoomOut,
          }));
          mocks.graphCanvasProps = props;
          return React.createElement("div", { "data-testid": "mock-graph-canvas" });
        });
      }
      return function MockAssistant() {
        return React.createElement("div", { "data-testid": "mock-ai-assistant" });
      };
    },
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(mocks.searchParams),
}));

vi.mock("antd", async () => {
  const React = await import("react");
  const Empty = Object.assign(
    ({ children, description }: PropsWithChildren<{ description?: React.ReactNode }>) =>
      React.createElement("div", null, description, children),
    { PRESENTED_IMAGE_SIMPLE: "simple" },
  );
  return {
    Button: ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
      React.createElement("button", props, children),
    Drawer: ({ children, open }: PropsWithChildren<{ open?: boolean }>) =>
      open ? React.createElement("aside", null, children) : null,
    Empty,
    Modal: ({ children, open }: PropsWithChildren<{ open?: boolean }>) =>
      open ? React.createElement("div", { role: "dialog" }, children) : null,
    Select: ({
      "aria-label": ariaLabel,
      maxTagCount,
      maxTagPlaceholder,
      mode,
      onChange,
      value,
    }: {
      "aria-label"?: string;
      maxTagCount?: number | string;
      maxTagPlaceholder?: (omittedValues: unknown[]) => React.ReactNode;
      mode?: string;
      onChange?: (value: unknown) => void;
      value?: unknown;
    }) => {
      mocks.selectRenderCount += 1;
      if (ariaLabel) mocks.selectChanges[ariaLabel] = onChange;
      const selectedValues = Array.isArray(value) ? value : [];
      const summary = mode === "multiple" && maxTagCount === 0 && selectedValues.length
        ? maxTagPlaceholder?.(selectedValues.map((selectedValue) => ({ value: selectedValue })))
        : null;
      return React.createElement(
        "div",
        {
          "aria-label": ariaLabel,
          "data-max-tag-count": maxTagCount,
          "data-mode": mode,
          role: "combobox",
        },
        summary,
      );
    },
    Skeleton: () => React.createElement("div", { role: "progressbar" }),
    Switch: ({ "aria-label": ariaLabel }: { "aria-label"?: string }) =>
      React.createElement("button", { "aria-label": ariaLabel, role: "switch" }),
    Tag: ({ children }: PropsWithChildren) => React.createElement("span", null, children),
    Tooltip: ({ children }: PropsWithChildren) => React.createElement(React.Fragment, null, children),
  };
});

vi.mock("@/lib/auth", () => ({
  getStoredAuth: () => ({ userId: "test-user" }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    getCompanies: mocks.getCompanies,
    getCompanyDisclosureGraph: vi.fn(),
    getCompanyDisclosureGraphNeighbors: vi.fn(),
    getReportDisclosureGraph: mocks.getReportDisclosureGraph,
    getReportDisclosureGraphNeighbors: vi.fn(),
  },
}));

vi.mock("@/lib/logger", () => ({
  errorSummary: (error: unknown) => String(error),
}));

vi.mock("@/store/useFileStore", () => ({
  useFileStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({
      files: mocks.files,
      loadFilesFromBackend: mocks.loadFilesFromBackend,
    }),
}));

describe("Graph Exploration Kumu control placement", () => {
  beforeEach(() => {
    mocks.actualSize.mockReset();
    mocks.dynamicIndex = 0;
    mocks.graphCanvasProps = null;
    mocks.files = [
      {
        key: "file-1",
        name: "Example ESG Report.pdf",
        size: "1 MB",
        dateUploaded: "2026-08-24",
        type: "pdf",
        status: "ready",
        file_id: "file-1",
        report_year: 2024,
      },
    ];
    mocks.selectChanges = {};
    mocks.selectRenderCount = 0;
    mocks.searchParams = "owner=reports&file_id=file-1";
    mocks.zoomIn.mockReset();
    mocks.zoomOut.mockReset();
    mocks.getCompanies.mockResolvedValue({ companies: [] });
    mocks.getReportDisclosureGraph.mockResolvedValue({
      schema_version: "1.0",
      graph_revision: "revision-1",
      nodes: [],
      edges: [],
      stats: { node_count: 0, edge_count: 0 },
      truncated: false,
    });
  });

  it("exposes the attachment's top-left, top-right, and bottom-left control regions", async () => {
    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);

    const title = screen.getByTestId("graph-map-title");
    expect(title).toHaveTextContent("ESG Metrics Analysis Graph");

    const search = screen.getByTestId("graph-search-control");
    expect(within(search).getByRole("combobox", { name: "Search graph" })).toBeVisible();

    const rightControls = screen.getByTestId("graph-right-controls");
    expect(rightControls).toContainElement(search);

    const controls = screen.getByTestId("graph-map-controls");
    expect(rightControls).toContainElement(controls);
    expect(within(controls).getByRole("button", { name: "Zoom in" })).toBeVisible();
    expect(within(controls).getByRole("button", { name: "View settings" })).toBeVisible();

    const legend = await screen.findByTestId("graph-status-legend");
    await waitFor(() => {
      expect(legend).toHaveTextContent("Disclosed");
      expect(legend).toHaveTextContent("Partially disclosed");
      expect(legend).toHaveTextContent("Not disclosed");
    });
  });

  it("routes Control and Command zoom shortcuts to the graph canvas", async () => {
    mocks.getReportDisclosureGraph.mockResolvedValueOnce({
      schema_version: "1.0",
      graph_revision: "revision-with-keyboard-zoom",
      nodes: [
        {
          id: "report:file-1",
          type: "Report",
          label: "Example ESG Report",
          properties: { file_id: "file-1", report_year: 2024 },
        },
        {
          id: "metric:one",
          type: "MetricItem",
          label: "Energy metric",
          properties: { metric_code: "TC-TEST-000.A" },
        },
        {
          id: "disclosure:one",
          type: "Disclosure",
          label: "Energy disclosure",
          properties: { disclosure_status: "fully_disclosed" },
        },
      ],
      edges: [
        {
          id: "has:one",
          type: "has_disclosure",
          source: "report:file-1",
          target: "disclosure:one",
          properties: {},
        },
        {
          id: "assesses:one",
          type: "assesses",
          source: "disclosure:one",
          target: "metric:one",
          properties: {},
        },
      ],
      stats: { node_count: 3, edge_count: 2 },
      truncated: false,
    });

    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);
    expect(await screen.findByTestId("mock-graph-canvas")).toBeVisible();

    fireEvent.keyDown(window, { key: "=", ctrlKey: true });
    fireEvent.keyDown(window, { key: "-", ctrlKey: true });
    fireEvent.keyDown(window, { key: "0", ctrlKey: true });
    fireEvent.keyDown(window, { key: "+", metaKey: true });

    expect(mocks.zoomIn).toHaveBeenCalledTimes(2);
    expect(mocks.zoomOut).toHaveBeenCalledTimes(1);
    expect(mocks.actualSize).toHaveBeenCalledTimes(1);
  });

  it("summarizes multi-select filters by count instead of selected labels", async () => {
    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);

    const multiSelectLabels = [
      "Reports",
      "Framework",
      "Year",
      "Topic",
      "Disclosure status",
    ];
    multiSelectLabels.forEach((label) => {
      const select = screen.getByRole("combobox", { name: label });
      expect(select).toHaveAttribute("data-mode", "multiple");
      expect(select).toHaveAttribute("data-max-tag-count", "0");
    });

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Reports" })).toHaveTextContent("1 item filtered");
      expect(screen.getByRole("combobox", { name: "Reports" })).not.toHaveTextContent("Example ESG Report.pdf");
    });
  });

  it("selects only the newest report when no reports are requested", async () => {
    mocks.searchParams = "owner=reports";
    mocks.files = [
      {
        key: "file-old",
        name: "Example ESG Report 2024.pdf",
        type: "pdf",
        status: "ready",
        file_id: "file-old",
        report_year: 2024,
      },
      {
        key: "file-new",
        name: "Example ESG Report 2025.pdf",
        type: "pdf",
        status: "ready",
        file_id: "file-new",
        report_year: 2025,
      },
    ];

    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Reports" })).toHaveTextContent("1 item filtered");
      expect(mocks.getReportDisclosureGraph).toHaveBeenCalled();
    });
    const requestedReportIds = mocks.getReportDisclosureGraph.mock.calls.map(([fileId]) => fileId);
    expect(requestedReportIds).toContain("file-new");
    expect(requestedReportIds).not.toContain("file-old");
  });

  it("does not refetch graph data when result state rerenders with unchanged report ids", async () => {
    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);

    expect(await screen.findByText("The selected reports do not have completed assessments."))
      .toBeVisible();
    expect(mocks.getReportDisclosureGraph).toHaveBeenCalledTimes(1);
  });

  it("keeps the existing canvas mounted while refreshing and isolates zoom readout updates", async () => {
    const graph = {
      schema_version: "1.0",
      graph_revision: "revision-with-data",
      nodes: [
        {
          id: "report:file-1",
          type: "Report",
          label: "Example ESG Report",
          properties: { file_id: "file-1", report_year: 2024, scope_key: "hardware" },
        },
        {
          id: "metric:one",
          type: "MetricItem",
          label: "Energy metric",
          properties: { metric_code: "TC-TEST-000.A", scope_key: "hardware" },
        },
        {
          id: "disclosure:one",
          type: "Disclosure",
          label: "Energy disclosure",
          properties: { disclosure_status: "fully_disclosed", scope_key: "hardware" },
        },
      ],
      edges: [
        {
          id: "has:one",
          type: "has_disclosure",
          source: "report:file-1",
          target: "disclosure:one",
          properties: {},
        },
        {
          id: "assesses:one",
          type: "assesses",
          source: "disclosure:one",
          target: "metric:one",
          properties: {},
        },
      ],
      stats: { node_count: 3, edge_count: 2 },
      truncated: false,
    };
    mocks.getReportDisclosureGraph.mockResolvedValueOnce(graph);

    const { default: GraphExplorationPage } = await import("../page");
    render(<GraphExplorationPage />);
    expect(await screen.findByTestId("mock-graph-canvas")).toBeVisible();

    const selectRendersBeforeZoom = mocks.selectRenderCount;
    act(() => {
      (mocks.graphCanvasProps?.onZoomChange as ((zoom: number) => void) | undefined)?.(1.25);
    });
    expect(screen.getByRole("button", { name: "Actual size" })).toHaveTextContent("125%");
    expect(mocks.selectRenderCount).toBe(selectRendersBeforeZoom);

    mocks.getReportDisclosureGraph.mockImplementationOnce(() => new Promise(() => undefined));
    act(() => mocks.selectChanges.Scope?.("different-scope"));
    await waitFor(() => expect(mocks.getReportDisclosureGraph).toHaveBeenCalledTimes(2));

    expect(screen.getByTestId("mock-graph-canvas")).toBeVisible();
    expect(screen.getByTestId("graph-refresh-indicator")).toHaveTextContent("Updating graph");
    expect(screen.queryByText("Building the disclosure graph...")).not.toBeInTheDocument();
  });
});
