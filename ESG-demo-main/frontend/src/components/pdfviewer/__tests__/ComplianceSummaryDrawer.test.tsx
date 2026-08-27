import React from "react";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ComplianceSummaryDrawer from "../ComplianceSummaryDrawer";

const mocks = vi.hoisted(() => ({
  drawerBodyStyle: {} as Record<string, unknown>,
}));

vi.mock("antd", async () => {
  const ReactModule = await import("react");
  return {
    Drawer: ({ children, styles }: React.PropsWithChildren<Record<string, any>>) => {
      mocks.drawerBodyStyle = styles?.body ?? {};
      return ReactModule.createElement(
        "div",
        { "data-testid": "summary-drawer" },
        children,
      );
    },
    Tag: ({ children }: React.PropsWithChildren) =>
      ReactModule.createElement("span", null, children),
  };
});

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ lang: "en", t: (key: string) => key }),
}));

describe("ComplianceSummaryDrawer scrolling", () => {
  it("contains vertical scrolling inside the modal body", () => {
    render(
      <ComplianceSummaryDrawer
        metrics={[]}
        onClose={vi.fn()}
        open={true}
        reportName="Annual report.pdf"
      />,
    );

    expect(screen.getByTestId("summary-drawer")).toBeInTheDocument();
    expect(mocks.drawerBodyStyle).toMatchObject({
      overflowY: "auto",
      overscrollBehaviorY: "contain",
      WebkitOverflowScrolling: "touch",
    });
  });
});
