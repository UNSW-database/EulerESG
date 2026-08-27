import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import CrossAnalysisDimensionPage from "../page";

const mocks = vi.hoisted(() => ({
  getCrossAnalysisDisclosedCache: vi.fn(),
  getCrossAnalysisReports: vi.fn(),
  navigationSlot: null as HTMLElement | null,
  pathname: "/cross-analysis/environment",
  push: vi.fn(),
  replace: vi.fn(),
  search: "ids=report-a%2Creport-b&primary=Environment&secondary=Energy",
  setCrossAnalysisSelection: vi.fn(),
  crossAnalysisSelection: null as null | {
    href: string;
    reports: Array<{ fileId: string; scopeKey?: string }>;
  },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ dimension: "environment" }),
  usePathname: () => mocks.pathname,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(mocks.search),
}));

vi.mock("@/store/useFileStore", () => ({
  useFileStore: (
    selector: (state: {
      crossAnalysisSelection: typeof mocks.crossAnalysisSelection;
      setCrossAnalysisSelection: typeof mocks.setCrossAnalysisSelection;
    }) => unknown,
  ) => selector({
    crossAnalysisSelection: mocks.crossAnalysisSelection,
    setCrossAnalysisSelection: mocks.setCrossAnalysisSelection,
  }),
}));

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("antd", async () => {
  const React = await import("react");
  return {
    Button: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) =>
      React.createElement("button", props, children),
    Modal: () => null,
    Skeleton: () => React.createElement("div", { "data-testid": "skeleton" }),
  };
});

vi.mock("@/lib/api", () => ({
  apiService: {
    getCrossAnalysisDisclosedCache: mocks.getCrossAnalysisDisclosedCache,
    getCrossAnalysisReports: mocks.getCrossAnalysisReports,
  },
}));

vi.mock("@/features/crossAnalysis/recordAdapter", () => ({
  normalizeCrossRecords: (records: unknown[]) => records,
}));

vi.mock("@/components/cross-analysis/CrossAnalysisNavigationPortal", () => ({
  useCrossAnalysisNavigationSlot: () => mocks.navigationSlot,
}));

vi.mock("@/components/cross-analysis/NewSidebar", async () => {
  const React = await import("react");
  return {
    NewSidebar: () =>
      React.createElement("div", {
        "data-testid": "ported-cross-analysis-directory",
      }),
  };
});

vi.mock("@/components/cross-analysis/NewHeader", () => ({
  NewHeader: () => null,
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

describe("CrossAnalysisDimensionPage navigation directory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.pathname = "/cross-analysis/environment";
    mocks.search =
      "ids=report-a%2Creport-b&primary=Environment&secondary=Energy";
    mocks.crossAnalysisSelection = null;

    mocks.navigationSlot = document.createElement("div");
    mocks.navigationSlot.dataset.testid = "cross-analysis-navigation-slot";
    document.body.appendChild(mocks.navigationSlot);

    mocks.getCrossAnalysisReports.mockResolvedValue({
      reports: [
        {
          display_name: "Report A",
          file_id: "report-a",
          filename: "report-a.pdf",
          framework: "SASB",
          industry: "Software & IT Services",
          semi_industry: "Application Software",
          short_name: "A",
        },
        {
          display_name: "Report B",
          file_id: "report-b",
          filename: "report-b.pdf",
          framework: "SASB",
          industry: "Software & IT Services",
          semi_industry: "Application Software",
          short_name: "B",
        },
      ],
    });
    mocks.getCrossAnalysisDisclosedCache.mockResolvedValue({
      records: [
        {
          data: "75",
          file_id: "report-a",
          name: "Report A",
          primary_navigation: "Environment",
          secondary_navigation: "Environment",
          topic: "Energy",
          unit: "%",
          year: "2025",
        },
      ],
    });
  });

  afterEach(() => {
    mocks.navigationSlot?.remove();
    mocks.navigationSlot = null;
  });

  it("portals exactly one directory into the global sidebar and leaves none in the page content", async () => {
    const view = render(<CrossAnalysisDimensionPage />);

    const directory = await screen.findByTestId(
      "ported-cross-analysis-directory",
    );

    expect(screen.getAllByTestId("ported-cross-analysis-directory")).toHaveLength(1);
    expect(directory.parentElement).toBe(mocks.navigationSlot);
    expect(
      view.container.querySelector('[data-testid="ported-cross-analysis-directory"]'),
    ).not.toBeInTheDocument();
  });

  it("updates only the saved href when URL ids match the committed reports", async () => {
    const committedReports = [
      { fileId: "report-a", scopeKey: "application-software" },
      { fileId: "report-b", scopeKey: "application-software" },
    ];
    mocks.crossAnalysisSelection = {
      href: "/cross-analysis/environment?ids=report-a%2Creport-b",
      reports: committedReports,
    };

    render(<CrossAnalysisDimensionPage />);

    await waitFor(() => {
      expect(mocks.setCrossAnalysisSelection).toHaveBeenCalledTimes(1);
    });
    expect(mocks.setCrossAnalysisSelection).toHaveBeenCalledWith({
      href:
        "/cross-analysis/environment?ids=report-a%2Creport-b&primary=Environment&secondary=Energy",
      reports: committedReports,
    });
    expect(
      mocks.setCrossAnalysisSelection.mock.calls[0]?.[0]?.reports,
    ).toBe(committedReports);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("does not overwrite the committed selection when URL ids differ", async () => {
    mocks.search =
      "ids=report-a%2Creport-c&primary=Environment&secondary=Energy";
    mocks.crossAnalysisSelection = {
      href: "/cross-analysis/environment?ids=report-a%2Creport-b",
      reports: [
        { fileId: "report-a", scopeKey: "scope-a" },
        { fileId: "report-b", scopeKey: "scope-b" },
      ],
    };

    render(<CrossAnalysisDimensionPage />);

    await waitFor(() => {
      expect(mocks.getCrossAnalysisReports).toHaveBeenCalledWith([
        "report-a",
        "report-c",
      ]);
    });
    expect(mocks.setCrossAnalysisSelection).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("restores a bare cross-analysis route from a valid matching persisted href", async () => {
    mocks.pathname = "/cross-analysis";
    mocks.search = "";
    mocks.crossAnalysisSelection = {
      href:
        "/cross-analysis/environment?ids=report-a%2Creport-b&primary=Environment",
      reports: [
        { fileId: "report-a", scopeKey: "scope-a" },
        { fileId: "report-b", scopeKey: "scope-b" },
      ],
    };

    render(<CrossAnalysisDimensionPage />);

    await waitFor(() => {
      expect(mocks.replace).toHaveBeenCalledWith(
        "/cross-analysis/environment?ids=report-a%2Creport-b&primary=Environment",
      );
    });
    expect(
      mocks.replace.mock.calls.map(([href]) => href),
    ).toEqual(
      Array(mocks.replace.mock.calls.length).fill(
        "/cross-analysis/environment?ids=report-a%2Creport-b&primary=Environment",
      ),
    );
    expect(mocks.setCrossAnalysisSelection).not.toHaveBeenCalled();
  });

  it.each([
    {
      href: "/dashboard?ids=report-a%2Creport-b",
      reports: [
        { fileId: "report-a", scopeKey: "scope-a" },
        { fileId: "report-b", scopeKey: "scope-b" },
      ],
    },
    {
      href: "/cross-analysis/environment?ids=report-a%2Creport-c",
      reports: [
        { fileId: "report-a", scopeKey: "scope-a" },
        { fileId: "report-b", scopeKey: "scope-b" },
      ],
    },
    {
      href: "/cross-analysis/environment?ids=report-a",
      reports: [{ fileId: "report-a", scopeKey: "scope-a" }],
    },
  ])("does not restore a bare route from an invalid persisted selection", async (selection) => {
    mocks.pathname = "/cross-analysis";
    mocks.search = "";
    mocks.crossAnalysisSelection = selection;

    render(<CrossAnalysisDimensionPage />);

    expect(
      await screen.findByText("files.selectAtLeastTwoReports"),
    ).toBeInTheDocument();
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.setCrossAnalysisSelection).not.toHaveBeenCalled();
  });
});
