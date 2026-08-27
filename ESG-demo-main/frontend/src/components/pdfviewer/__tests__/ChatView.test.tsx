import React from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ChatView from "../ChatView";

const mocks = vi.hoisted(() => ({ dynamicIndex: 0 }));

vi.mock("next/dynamic", () => ({
  default: () => {
    const index = mocks.dynamicIndex++;
    if (index === 0) {
      return function MockPdfViewer({
        fileUrl,
        targetPage,
        targetPageNonce,
      }: {
        fileUrl: string;
        targetPage: number;
        targetPageNonce: number;
      }) {
        return (
          <div
            data-file-url={fileUrl}
            data-target-page={targetPage}
            data-target-page-nonce={targetPageNonce}
            data-testid="pdf-viewer"
          />
        );
      };
    }
    return function MockDynamicSummaryDrawer() {
      return null;
    };
  },
}));

vi.mock("../AnalysisResults", () => ({
  default: ({
    headerAction,
    onPageNavigate,
  }: {
    headerAction?: React.ReactNode;
    onPageNavigate?: (target: {
      page: number;
      fileId?: string;
      reportName?: string;
    }) => void;
  }) => (
    <div data-testid="analysis-results">
      <div data-testid="analysis-report-heading">{headerAction}</div>
      <button type="button" onClick={() => onPageNavigate?.({ page: 77 })}>
        jump-page-77
      </button>
      <button
        type="button"
        onClick={() => onPageNavigate?.({
          page: 9,
          fileId: "report-b",
          reportName: "Report B.pdf",
        })}
      >
        jump-source-report
      </button>
    </div>
  ),
}));

vi.mock("../ComplianceSummaryDrawer", () => ({
  default: () => null,
}));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ t: (key: string) => key }),
}));

const renderChatView = () =>
  render(
    <ChatView
      activeFile={null}
      fileId="report-1"
    />,
  );

describe("ChatView report workspace", () => {
  it("places Generate beside the report heading rather than the Analysis card title", () => {
    renderChatView();

    const generate = screen.getByRole("button", { name: "analysis.generateSummary" });
    expect(screen.getByTestId("analysis-report-heading")).toContainElement(generate);
    expect(screen.getByText("chat.analysis").parentElement?.parentElement).not.toContainElement(generate);
  });

  it("does not render a second assistant owned by the report workspace", () => {
    renderChatView();

    expect(screen.queryByRole("button", { name: "AI Assistant" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("compliance-ai-assistant")).not.toBeInTheDocument();
  });

  it("does not carry a page jump into a different report", async () => {
    const view = render(
      <ChatView
        activeFile={null}
        fileId="report-a"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "jump-page-77" }));
    expect(screen.getByTestId("pdf-viewer")).toHaveAttribute("data-target-page", "77");

    view.rerender(
      <ChatView
        activeFile={null}
        fileId="report-b"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pdf-viewer")).toHaveAttribute(
        "data-file-url",
        "/api/files/report-b/pdf",
      );
      expect(screen.getByTestId("pdf-viewer")).toHaveAttribute("data-target-page", "1");
      expect(screen.getByTestId("pdf-viewer")).toHaveAttribute("data-target-page-nonce", "0");
    });
  });

  it("opens evidence in its source report instead of the currently viewed PDF", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderChatView();

    fireEvent.click(screen.getByRole("button", { name: "jump-source-report" }));

    expect(open).toHaveBeenCalledWith(
      "/cross-analysis/evidence?file_id=report-b&page=9&name=Report+B.pdf",
      "_blank",
      "noopener,noreferrer",
    );
    open.mockRestore();
  });
});
