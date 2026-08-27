import type { PropsWithChildren } from "react";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CrossAnalysisLayout from "../layout";

const mocks = vi.hoisted(() => ({
  pathname: "/cross-analysis",
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
}));

vi.mock("next/dynamic", () => ({
  default: () =>
    function MockFloatingChatAssistant({
      conversationKey,
      includeContext,
    }: {
      conversationKey?: string;
      includeContext?: boolean;
    }) {
      return (
        <button
          data-conversation-key={conversationKey}
          data-include-context={String(includeContext)}
          data-testid="layout-ai-assistant"
          type="button"
        >
          AI Assistant
        </button>
      );
    },
}));

vi.mock("antd", async () => {
  const React = await import("react");
  const LayoutRoot = ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
    React.createElement("div", props, children);
  const Content = ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) =>
    React.createElement("main", props, children);
  return { Layout: Object.assign(LayoutRoot, { Content }) };
});

vi.mock("@/components/navbar/DashboardSidebar", () => ({
  default: () => <aside data-testid="dashboard-sidebar" />,
}));

vi.mock("@/lib/antd", () => ({
  AntdRegistry: ({ children }: PropsWithChildren) => <>{children}</>,
}));

describe("CrossAnalysisLayout AI Assistant coverage", () => {
  beforeEach(() => {
    mocks.pathname = "/cross-analysis";
  });

  it("provides one assistant even before reports can be compared", () => {
    render(
      <CrossAnalysisLayout>
        <div>Choose reports</div>
      </CrossAnalysisLayout>,
    );

    expect(screen.getAllByRole("button", { name: "AI Assistant" })).toHaveLength(1);
    expect(screen.getByTestId("dashboard-sidebar")).toBeVisible();
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-conversation-key",
      "general",
    );
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-include-context",
      "false",
    );
  });

  it("also provides one assistant on the standalone evidence reader", () => {
    mocks.pathname = "/cross-analysis/evidence";

    render(
      <CrossAnalysisLayout>
        <div>Evidence reader</div>
      </CrossAnalysisLayout>,
    );

    expect(screen.getAllByRole("button", { name: "AI Assistant" })).toHaveLength(1);
    expect(screen.queryByTestId("dashboard-sidebar")).not.toBeInTheDocument();
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-conversation-key",
      "general",
    );
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-include-context",
      "false",
    );
  });
});
