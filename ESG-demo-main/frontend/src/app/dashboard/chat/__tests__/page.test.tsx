import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "../page";

const mocks = vi.hoisted(() => ({
  files: [
    {
      analysis_scope_key: "scope-a",
      file_id: "report-a",
      key: "report-a::scope-a",
      name: "Report A / Scope A",
      status: "ready",
      type: "PDF",
    },
    {
      analysis_scope_key: "scope-b",
      file_id: "report-a",
      key: "report-a::scope-b",
      name: "Report A / Scope B",
      status: "ready",
      type: "PDF",
    },
    {
      analysis_scope_key: "scope-c",
      file_id: "report-b",
      key: "report-b::scope-c",
      name: "Report B / Scope C",
      status: "ready",
      type: "PDF",
    },
  ],
  replace: vi.fn(),
  search: "",
  selectedFileId: null as string | null,
  selectedFileScopeKey: null as string | null,
  setComplianceSelection: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(mocks.search),
}));

vi.mock("next/dynamic", () => ({
  default: () =>
    function MockChatView({
      activeFile,
      fileId,
      scopeKey,
    }: {
      activeFile?: { key?: string; name?: string } | null;
      fileId?: string;
      scopeKey?: string;
    }) {
      return (
        <div
          data-active-file-key={activeFile?.key || ""}
          data-file-id={fileId || ""}
          data-scope-key={scopeKey || ""}
          data-testid="chat-view"
        >
          {activeFile?.name || "No active report"}
        </div>
      );
    },
}));

vi.mock("@/hooks/useEnsureReportFiles", () => ({
  useEnsureReportFiles: () => undefined,
}));

vi.mock("@/store/useFileStore", () => {
  const state = {
    get files() {
      return mocks.files;
    },
    get selectedFileId() {
      return mocks.selectedFileId;
    },
    get selectedFileScopeKey() {
      return mocks.selectedFileScopeKey;
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

describe("Compliance report restoration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.search = "";
    mocks.selectedFileId = null;
    mocks.selectedFileScopeKey = null;
    mocks.setComplianceSelection.mockImplementation(
      (fileId: string | null, scopeKey?: string | null) => {
        mocks.selectedFileId = fileId;
        mocks.selectedFileScopeKey = fileId ? scopeKey || null : null;
      },
    );
  });

  it("prefers the query scope and restores that exact scope after leaving", async () => {
    mocks.selectedFileId = "report-a";
    mocks.selectedFileScopeKey = "scope-a";
    mocks.search = "file_id=report-a&scope=scope-b";

    const selectedFromQuery = render(<ChatPage />);
    const queryView = await screen.findByTestId("chat-view");
    expect(queryView).toHaveAttribute("data-file-id", "report-a");
    expect(queryView).toHaveAttribute("data-scope-key", "scope-b");
    expect(queryView).toHaveAttribute(
      "data-active-file-key",
      "report-a::scope-b",
    );
    await waitFor(() => {
      expect(mocks.setComplianceSelection).toHaveBeenCalledWith(
        "report-a",
        "scope-b",
      );
    });

    selectedFromQuery.unmount();
    mocks.replace.mockClear();
    mocks.search = "";
    render(<ChatPage />);

    const restoredView = await screen.findByTestId("chat-view");
    expect(restoredView).toHaveAttribute("data-file-id", "report-a");
    expect(restoredView).toHaveAttribute("data-scope-key", "scope-b");
    expect(restoredView).toHaveAttribute(
      "data-active-file-key",
      "report-a::scope-b",
    );
    await waitFor(() => {
      expect(mocks.replace).toHaveBeenCalledWith(
        "/dashboard/chat?file_id=report-a&scope=scope-b",
      );
    });
  });

  it("does not fall back to the first row when an explicit scope is invalid", async () => {
    mocks.selectedFileId = "report-a";
    mocks.selectedFileScopeKey = "scope-a";
    mocks.search = "file_id=report-a&scope=missing-scope";

    render(<ChatPage />);

    const view = await screen.findByTestId("chat-view");
    expect(view).toHaveAttribute("data-file-id", "");
    expect(view).toHaveAttribute("data-scope-key", "");
    expect(view).toHaveAttribute("data-active-file-key", "");
    expect(view).toHaveTextContent("No active report");
    expect(mocks.setComplianceSelection).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
  });
});
