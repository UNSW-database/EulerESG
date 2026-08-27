import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DisclosureCompletenessComparison from "../DisclosureCompletenessComparison";

const mocks = vi.hoisted(() => ({
  crossAnalysisSelection: null as null | {
    href: string;
    reports: Array<{ fileId: string; scopeKey?: string }>;
  },
  files: [] as Array<{ file_id: string; analysis_scope_key?: string }>,
  getAssessmentByFile: vi.fn(),
  prefetchAssessmentByFile: vi.fn(),
  push: vi.fn(),
  setComplianceSelection: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@/lib/api", () => ({
  apiService: {
    getAssessmentByFile: mocks.getAssessmentByFile,
    prefetchAssessmentByFile: mocks.prefetchAssessmentByFile,
  },
}));

vi.mock("@/store/useFileStore", () => {
  const state = {
    get crossAnalysisSelection() {
      return mocks.crossAnalysisSelection;
    },
    get files() {
      return mocks.files;
    },
    setComplianceSelection: mocks.setComplianceSelection,
  };

  return {
    buildComplianceAnalysisHref: (fileId: string, scopeKey?: string | null) => {
      const query = new URLSearchParams({ file_id: fileId });
      if (scopeKey) query.set("scope", scopeKey);
      return `/dashboard/chat?${query.toString()}`;
    },
    useFileStore: (selector: (value: typeof state) => unknown) => selector(state),
  };
});

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    t: (key: string, vars?: Record<string, unknown>) =>
      vars?.page ? `${key}:${vars.page}` : key,
  }),
}));

vi.mock("antd", async () => {
  const ReactModule = await import("react");
  return {
    Alert: ({ title }: { title?: React.ReactNode }) =>
      ReactModule.createElement("div", null, title),
    Popover: ({ children }: React.PropsWithChildren) =>
      ReactModule.createElement(ReactModule.Fragment, null, children),
    Select: () => ReactModule.createElement("div"),
    Spin: () => ReactModule.createElement("div", { role: "status" }),
    Table: () => ReactModule.createElement("div", { "data-testid": "comparison-table" }),
    Tag: ({ children }: React.PropsWithChildren) =>
      ReactModule.createElement("span", null, children),
  };
});

const reports = [
  {
    confidence: 1,
    display_name: "Report A",
    file_id: "report-a",
    filename: "Report A.pdf",
    has_assessment: true,
    short_name: "A",
  },
  {
    confidence: 1,
    display_name: "Report B",
    file_id: "report-b",
    filename: "Report B.pdf",
    has_assessment: true,
    short_name: "B",
  },
  {
    confidence: 1,
    display_name: "Report C",
    file_id: "report-c",
    filename: "Report C.pdf",
    has_assessment: true,
    short_name: "C",
  },
];

describe("DisclosureCompletenessComparison report navigation", () => {
  beforeEach(() => {
    mocks.crossAnalysisSelection = null;
    mocks.files = [];
    mocks.getAssessmentByFile.mockResolvedValue({ metric_analyses: [] });
    mocks.prefetchAssessmentByFile.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("prefetches the compact assessment and navigates immediately without a progress delay", async () => {
    render(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={reports}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: "Report A" }));

    expect(mocks.prefetchAssessmentByFile).toHaveBeenCalledWith(
      "report-a",
      undefined,
      false,
      true,
    );
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/chat?file_id=report-a",
    );
  });

  it("uses each committed report scope when the Cross selection exactly matches the rendered reports", async () => {
    mocks.crossAnalysisSelection = {
      href: "/cross-analysis/disclosure-completeness?ids=report-a%2Creport-b",
      reports: [
        { fileId: "report-a", scopeKey: "scope-a" },
        { fileId: "report-b", scopeKey: "scope-b" },
      ],
    };

    render(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={reports}
      />,
    );

    await waitFor(() => {
      expect(mocks.getAssessmentByFile).toHaveBeenCalledTimes(2);
    });
    expect(mocks.getAssessmentByFile).toHaveBeenCalledWith(
      "report-a",
      "scope-a",
      false,
      true,
    );
    expect(mocks.getAssessmentByFile).toHaveBeenCalledWith(
      "report-b",
      "scope-b",
      false,
      true,
    );

    fireEvent.click(screen.getByRole("button", { name: "Report A" }));
    fireEvent.click(screen.getByRole("button", { name: "Report B" }));

    expect(mocks.prefetchAssessmentByFile).toHaveBeenCalledWith(
      "report-a",
      "scope-a",
      false,
      true,
    );
    expect(mocks.prefetchAssessmentByFile).toHaveBeenCalledWith(
      "report-b",
      "scope-b",
      false,
      true,
    );
    expect(mocks.setComplianceSelection).toHaveBeenCalledWith(
      "report-a",
      "scope-a",
    );
    expect(mocks.setComplianceSelection).toHaveBeenCalledWith(
      "report-b",
      "scope-b",
    );
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/chat?file_id=report-a&scope=scope-a",
    );
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/chat?file_id=report-b&scope=scope-b",
    );
  });

  it("does not borrow scopes from a committed Cross selection with different report IDs", async () => {
    mocks.crossAnalysisSelection = {
      href: "/cross-analysis/disclosure-completeness?ids=report-a%2Creport-c",
      reports: [
        { fileId: "report-a", scopeKey: "stale-scope-a" },
        { fileId: "report-c", scopeKey: "stale-scope-c" },
      ],
    };

    render(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={reports}
      />,
    );

    await waitFor(() => {
      expect(mocks.getAssessmentByFile).toHaveBeenCalledTimes(2);
    });
    expect(mocks.getAssessmentByFile).toHaveBeenCalledWith(
      "report-a",
      undefined,
      false,
      true,
    );
    expect(mocks.getAssessmentByFile).toHaveBeenCalledWith(
      "report-b",
      undefined,
      false,
      true,
    );

    fireEvent.click(screen.getByRole("button", { name: "Report A" }));

    expect(mocks.prefetchAssessmentByFile).toHaveBeenCalledWith(
      "report-a",
      undefined,
      false,
      true,
    );
    expect(mocks.prefetchAssessmentByFile).not.toHaveBeenCalledWith(
      "report-a",
      "stale-scope-a",
      false,
      true,
    );
    expect(mocks.setComplianceSelection).toHaveBeenCalledWith(
      "report-a",
      undefined,
    );
    expect(mocks.push).toHaveBeenCalledWith(
      "/dashboard/chat?file_id=report-a",
    );
  });

  it("updates report metadata without requesting the same assessments again", async () => {
    const view = render(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={[]}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getAssessmentByFile).toHaveBeenCalledTimes(2);
    mocks.getAssessmentByFile.mockClear();

    view.rerender(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={reports}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(mocks.getAssessmentByFile).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Report A" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report B" })).toBeInTheDocument();
  });

  it("reloads only the reports in the current Cross Analysis selection", async () => {
    const view = render(
      <DisclosureCompletenessComparison
        fileIds={["report-a", "report-b"]}
        reports={reports}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getAssessmentByFile).toHaveBeenCalledTimes(2);
    expect(mocks.getAssessmentByFile).toHaveBeenCalledWith(
      "report-a",
      undefined,
      false,
      true,
    );
    expect(mocks.getAssessmentByFile).toHaveBeenCalledWith(
      "report-b",
      undefined,
      false,
      true,
    );
    expect(
      mocks.getAssessmentByFile.mock.calls.map(([fileId]) => fileId).sort(),
    ).toEqual(["report-a", "report-b"]);
    expect(screen.getByRole("button", { name: "Report A" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report B" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Report C" })).not.toBeInTheDocument();

    mocks.getAssessmentByFile.mockClear();
    view.rerender(
      <DisclosureCompletenessComparison
        fileIds={["report-b", "report-c"]}
        reports={reports}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getAssessmentByFile).toHaveBeenCalledTimes(2);
    expect(
      mocks.getAssessmentByFile.mock.calls.map(([fileId]) => fileId).sort(),
    ).toEqual(["report-b", "report-c"]);
    expect(screen.queryByRole("button", { name: "Report A" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report B" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report C" })).toBeInTheDocument();
  });
});
