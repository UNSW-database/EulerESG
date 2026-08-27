import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  dynamicIndex: 0,
  dynamicOptions: [] as Array<Record<string, unknown> | undefined>,
}));

vi.mock("next/dynamic", () => ({
  default: (
    _loader: () => Promise<unknown>,
    options?: Record<string, unknown>,
  ) => {
    const index = mocks.dynamicIndex++;
    mocks.dynamicOptions.push(options);
    if (index === 0) {
      return function MockDashboardWorkspace() {
        return <main data-testid="dashboard-files" />;
      };
    }
    return function MockStatusButton() {
      return null;
    };
  },
}));

vi.mock("@/components/pdfviewer/PDFViewer", () => ({
  default: () => <main data-testid="dashboard-files" />,
}));

vi.mock("@/components/status/FloatingStatusButton", () => ({
  default: () => null,
}));

describe("DashboardPage", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("keeps the homepage workspace client-only while the shared layout owns its assistant", async () => {
    vi.stubEnv("NEXT_PUBLIC_SHOW_DEV_TOOLS", "false");
    const { default: DashboardPage } = await import("../page");
    render(<DashboardPage />);

    expect(screen.getByTestId("dashboard-files")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI Assistant" })).not.toBeInTheDocument();
    expect(mocks.dynamicOptions).toContainEqual({ ssr: false });
    expect(mocks.dynamicOptions[0]).toMatchObject({
      ssr: false,
      loading: expect.any(Function),
    });
  });
});
