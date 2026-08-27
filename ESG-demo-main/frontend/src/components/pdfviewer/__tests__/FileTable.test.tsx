import type { PropsWithChildren, ReactNode } from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FileTable from "../FileTable";

const FAVOURITE_REPORTS_STORAGE_KEY = "euleresg-favourite-reports";

const mocks = vi.hoisted(() => ({
  files: [
    {
      dateUploaded: "2026-08-17",
      file_id: "report-a",
      framework: "SASB",
      key: "report-a",
      name: "Report A",
      size: "1 MB",
      status: "ready" as const,
      type: "PDF",
    },
    {
      analysis_scope_key: "scope-b",
      dateUploaded: "2026-08-18",
      file_id: "report-b",
      framework: "SASB",
      key: "report-b::scope-b",
      name: "Report B Scope B",
      size: "2 MB",
      status: "ready" as const,
      type: "PDF",
    },
    {
      analysis_scope_key: "scope-c",
      dateUploaded: "2026-08-18",
      file_id: "report-b",
      framework: "SASB",
      key: "report-b::scope-c",
      name: "Report B Scope C",
      size: "2 MB",
      status: "ready" as const,
      type: "PDF",
    },
    {
      company_id: "company-x",
      company_name: "Company X",
      dateUploaded: "2026-08-19",
      file_id: "multi-report-a",
      framework: "SASB",
      key: "multi-report-a",
      name: "Multi Report A",
      size: "3 MB",
      status: "ready" as const,
      type: "PDF",
      upload_mode: "multi",
    },
    {
      company_id: "company-x",
      company_name: "Company X",
      dateUploaded: "2026-08-19",
      file_id: "multi-report-b",
      framework: "SASB",
      key: "multi-report-b",
      name: "Multi Report B",
      size: "4 MB",
      status: "ready" as const,
      type: "PDF",
      upload_mode: "multi",
    },
  ],
  loadFilesFromBackend: vi.fn(),
  prefetch: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ prefetch: mocks.prefetch, push: mocks.push }),
}));

vi.mock("@ant-design/icons", async () => {
  const React = await import("react");
  const Icon = () => React.createElement("span", { "aria-hidden": "true" });
  return {
    BarChartOutlined: Icon,
    DeleteOutlined: Icon,
    EllipsisOutlined: Icon,
    StarFilled: Icon,
    StarOutlined: Icon,
    SyncOutlined: Icon,
  };
});

vi.mock("antd", async () => {
  const React = await import("react");
  const Wrapper = ({ children }: PropsWithChildren) =>
    React.createElement(React.Fragment, null, children);
  const Button = ({
    "aria-label": ariaLabel,
    children,
    disabled,
    icon,
    onClick,
    title,
    type: _type,
    ...props
  }: PropsWithChildren<Record<string, any>>) => {
    void _type;
    return React.createElement(
      "button",
      { ...props, "aria-label": ariaLabel, disabled, onClick, title, type: "button" },
      icon,
      children,
    );
  };
  const Modal = Object.assign(
    ({ children, open }: PropsWithChildren<{ open?: boolean }>) =>
      open ? React.createElement("div", null, children) : null,
    { confirm: vi.fn() },
  );
  const message = {
    error: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  };
  const modal = { confirm: vi.fn() };

  return {
    App: {
      useApp: () => ({ message, modal }),
    },
    Button,
    Dropdown: ({ children, menu }: PropsWithChildren<{ menu: any }>) =>
      React.createElement(
        "div",
        null,
        children,
        menu.items.map((item: { key: string; label: ReactNode }) =>
          React.createElement(
            "button",
            {
              key: item.key,
              onClick: (event: MouseEvent) =>
                menu.onClick({ domEvent: event, key: item.key }),
              type: "button",
            },
            item.label,
          ),
        ),
      ),
    Modal,
    Space: Wrapper,
    Table: ({ columns, dataSource }: { columns: any[]; dataSource: any[] }) => {
      const actions = columns.find((column) => column.key === "actions");
      return React.createElement(
        "div",
        { "data-testid": "file-table" },
        dataSource.map((row, index) =>
          React.createElement(
            "div",
            { "data-testid": `report-row-${row.key}`, key: row.key },
            React.createElement("span", null, row.name),
            actions?.render?.(undefined, row, index),
          ),
        ),
      );
    },
    Tag: Wrapper,
    Tooltip: Wrapper,
  };
});

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    lang: "en",
    t: (key: string) =>
      ({
        "common.noDataAvailable": "No data available",
        "common.unknown": "Unknown",
        "files.actions.analysis": "Analysis",
        "files.crossAnalysisBeta": "Cross Analysis",
        "files.actions.delete": "Delete",
        "files.columns.actions": "Actions",
        "files.columns.dateUploaded": "Uploaded",
        "files.columns.framework": "Framework",
        "files.columns.industry": "Industry",
        "files.columns.name": "Name",
        "files.columns.size": "Size",
        "files.columns.status": "Status",
        "files.columns.subOption": "Sub-option",
        "files.columns.type": "Type",
        "files.status.ready": "Ready",
        "files.title": "Reports",
      })[key] ?? key,
  }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    invalidateAssessmentByFileCache: vi.fn(),
    invalidateCrossAnalysisCache: vi.fn(),
    prefetchCrossAnalysis: vi.fn(),
    reanalyzeReport: vi.fn(),
    subscribeReportJob: vi.fn(),
  },
}));

vi.mock("@/store/useFileStore", () => {
  const state = {
    deleteFile: vi.fn(),
    files: mocks.files,
    loadFilesFromBackend: mocks.loadFilesFromBackend,
    loading: false,
  };
  const useFileStore = Object.assign(
    (selector: (value: typeof state) => unknown) => selector(state),
    { getState: () => state },
  );
  return {
    canCrossAnalyzeFiles: () => true,
    getReportCatalogMode: (file: { upload_mode?: string }) =>
      file.upload_mode === "multi" ? "multi" : "single",
    useFileStore,
  };
});

function renderFavouriteTable() {
  return render(
    <FileTable
      favouritesOnly
      onChatClick={vi.fn()}
      onSelectionChange={vi.fn()}
      reportCatalogMode="single"
      selectedRows={[]}
    />,
  );
}

function renderHomepageTable() {
  return render(
    <FileTable
      onChatClick={vi.fn()}
      onSelectionChange={vi.fn()}
      reportCatalogMode="single"
      selectedRows={[]}
    />,
  );
}

describe("FileTable compliance actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("uses the same shield-check icon as the dashboard sidebar", () => {
    renderHomepageTable();

    const actions = screen.getAllByRole("button", { name: "Analysis" });
    expect(actions.length).toBeGreaterThan(0);
    for (const action of actions) {
      expect(action.querySelector('[data-testid="compliance-action-icon"]')).not.toBeNull();
      expect(action.querySelector(".lucide-shield-check")).not.toBeNull();
    }
  });

  it("warms the compliance route once across pointer, hover, and focus intent", () => {
    renderHomepageTable();

    const action = screen.getAllByRole("button", { name: "Analysis" })[0];
    fireEvent.pointerDown(action);
    fireEvent.mouseEnter(action);
    fireEvent.focus(action);

    expect(mocks.prefetch).toHaveBeenCalledTimes(1);
    expect(mocks.prefetch).toHaveBeenCalledWith("/dashboard/chat");
  });

  it("warms the exact company route before opening a company assessment", () => {
    render(
      <FileTable
        onChatClick={vi.fn()}
        onSelectionChange={vi.fn()}
        reportCatalogMode="multi"
        selectedRows={[]}
      />,
    );

    const action = screen.getByRole("button", { name: "Analysis" });
    fireEvent.pointerDown(action);
    fireEvent.mouseEnter(action);

    expect(mocks.prefetch).toHaveBeenCalledTimes(1);
    expect(mocks.prefetch).toHaveBeenCalledWith(
      "/dashboard/company/company-x",
    );
  });
});

describe("FileTable favourites directory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("shows only the stored report scope", async () => {
    window.localStorage.setItem(
      FAVOURITE_REPORTS_STORAGE_KEY,
      JSON.stringify(["report-b::scope-b"]),
    );

    renderFavouriteTable();

    expect(await screen.findByText("Report B Scope B")).toBeVisible();
    expect(screen.queryByText("Report A")).not.toBeInTheDocument();
    expect(screen.queryByText("Report B Scope C")).not.toBeInTheDocument();
  });

  it("shows favourited multi-report uploads globally as flat report rows", async () => {
    window.localStorage.setItem(
      FAVOURITE_REPORTS_STORAGE_KEY,
      JSON.stringify(["multi-report-a::", "multi-report-b::"]),
    );

    renderFavouriteTable();

    expect(await screen.findByText("Multi Report A")).toBeVisible();
    expect(screen.getByText("Multi Report B")).toBeVisible();
    expect(screen.getByTestId("report-row-multi-report-a")).toBeVisible();
    expect(screen.getByTestId("report-row-multi-report-b")).toBeVisible();
    expect(screen.queryByText("Company X")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("report-row-company::company-x"),
    ).not.toBeInTheDocument();
  });

  it("removes a report from the directory immediately when it is unfavourited", async () => {
    window.localStorage.setItem(
      FAVOURITE_REPORTS_STORAGE_KEY,
      JSON.stringify(["report-b::scope-b"]),
    );
    renderFavouriteTable();
    await screen.findByText("Report B Scope B");

    fireEvent.click(screen.getByRole("button", { name: "Remove from favourites" }));

    await waitFor(() => {
      expect(screen.queryByText("Report B Scope B")).not.toBeInTheDocument();
      expect(screen.getByText("No data available")).toBeVisible();
    });
    expect(
      JSON.parse(
        window.localStorage.getItem(FAVOURITE_REPORTS_STORAGE_KEY) || "null",
      ),
    ).toEqual([]);
  });

  it.each([
    ["missing", null],
    ["malformed", "{not-json"],
  ])("uses a safe empty state for %s favourite storage", async (_label, stored) => {
    if (stored !== null) {
      window.localStorage.setItem(FAVOURITE_REPORTS_STORAGE_KEY, stored);
    }

    renderFavouriteTable();

    expect(await screen.findByText("No data available")).toBeVisible();
    expect(screen.queryByTestId("file-table")).not.toBeInTheDocument();
  });
});
