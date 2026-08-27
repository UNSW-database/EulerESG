import type { PropsWithChildren } from "react";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardLayout from "../layout";

const mocks = vi.hoisted(() => ({
  dynamicOptions: [] as Array<Record<string, unknown> | undefined>,
  pathname: "/dashboard",
  queryFileId: null as string | null,
  replace: vi.fn(),
  selectedFileId: null as string | null,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => ({
    get: (key: string) => key === "file_id" ? mocks.queryFileId : null,
  }),
}));

vi.mock("next/dynamic", () => ({
  default: (
    _loader: () => Promise<unknown>,
    options?: Record<string, unknown>,
  ) => {
    mocks.dynamicOptions.push(options);
    return function MockFloatingChatAssistant({
      conversationKey,
      fileId,
      includeContext,
    }: {
      conversationKey?: string;
      fileId?: string;
      includeContext?: boolean;
    }) {
      return (
        <button
          data-conversation-key={conversationKey}
          data-file-id={fileId}
          data-include-context={String(includeContext)}
          data-testid="layout-ai-assistant"
          type="button"
        >
          AI Assistant
        </button>
      );
    };
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

vi.mock("@/store/useFileStore", () => ({
  useFileStore: (
    selector: (state: { selectedFileId: string | null }) => unknown,
  ) => selector({ selectedFileId: mocks.selectedFileId }),
}));

vi.mock("@/lib/antd", () => ({
  AntdRegistry: ({ children }: PropsWithChildren) => <>{children}</>,
}));

vi.mock("@/lib/auth", () => ({
  AUTH_TOKEN_KEY: "test-auth-token",
}));

describe("DashboardLayout AI Assistant coverage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.pathname = "/dashboard";
    mocks.queryFileId = null;
    mocks.selectedFileId = null;
    window.localStorage.setItem("test-auth-token", "token");
  });

  it.each([
    "/dashboard",
    "/dashboard/favourite",
    "/dashboard/standards-library",
    "/dashboard/graph",
    "/dashboard/company/acme",
  ])("provides exactly one generic assistant on %s", (pathname) => {
    mocks.pathname = pathname;

    render(
      <DashboardLayout>
        <div>Workspace</div>
      </DashboardLayout>,
    );

    expect(screen.getAllByRole("button", { name: "AI Assistant" })).toHaveLength(1);
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-include-context",
      "false",
    );
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-conversation-key",
      "general",
    );
    expect(screen.getByTestId("layout-ai-assistant")).not.toHaveAttribute(
      "data-file-id",
    );
  });

  it("uses the query report for the single Compliance assistant", () => {
    mocks.pathname = "/dashboard/chat";
    mocks.queryFileId = "report-from-query";
    mocks.selectedFileId = "report-from-store";

    render(
      <DashboardLayout>
        <div>Compliance workspace</div>
      </DashboardLayout>,
    );

    expect(screen.getAllByRole("button", { name: "AI Assistant" })).toHaveLength(1);
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-conversation-key",
      "file:report-from-query",
    );
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-file-id",
      "report-from-query",
    );
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-include-context",
      "true",
    );
  });

  it("falls back to the selected report for the Compliance assistant", () => {
    mocks.pathname = "/dashboard/chat";
    mocks.selectedFileId = "selected-report";

    render(
      <DashboardLayout>
        <div>Compliance workspace</div>
      </DashboardLayout>,
    );

    expect(screen.getAllByRole("button", { name: "AI Assistant" })).toHaveLength(1);
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-conversation-key",
      "file:selected-report",
    );
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-file-id",
      "selected-report",
    );
    expect(screen.getByTestId("layout-ai-assistant")).toHaveAttribute(
      "data-include-context",
      "true",
    );
  });

  it("loads the shared assistant as a client-only chunk", () => {
    expect(mocks.dynamicOptions).toContainEqual({ ssr: false });
  });
});
