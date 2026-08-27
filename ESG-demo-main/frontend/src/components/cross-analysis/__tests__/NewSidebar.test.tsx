import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NewSidebar } from "../NewSidebar";

vi.mock("@/i18n/useT", () => ({
  useT: () => ({
    t: (key: string) =>
      ({
        "crossAnalysis.disclosureCompleteness": "Disclosure Completeness",
        "crossAnalysis.navigation": "Navigation",
        "crossAnalysis.noSecondaryNav": "No secondary navigation",
      })[key] ?? key,
  }),
}));

describe("NewSidebar", () => {
  it("keeps Disclosure Completeness out of the report-level navigation", () => {
    render(
      <NewSidebar
        expandedPrimaries={{ Environment: false }}
        onSelectSecondary={vi.fn()}
        onSelectTertiary={vi.fn()}
        onTogglePrimary={vi.fn()}
        primaryOptions={["Environment"]}
        secondaryByPrimary={new Map([["Environment", ["Energy"]]])}
        selectedPrimary="Environment"
        selectedSecondaries={[]}
        selectedTertiary={null}
        tertiaryByPrimaryAndSecondary={new Map()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "Disclosure Completeness" }),
    ).not.toBeInTheDocument();
  });

  it("renders an embedded directory without the old standalone card chrome", () => {
    const { container } = render(
      <NewSidebar
        embedded
        expandedPrimaries={{ Environment: true }}
        onSelectSecondary={vi.fn()}
        onSelectTertiary={vi.fn()}
        onTogglePrimary={vi.fn()}
        primaryOptions={["Environment"]}
        secondaryByPrimary={new Map([["Environment", ["Energy"]]])}
        selectedPrimary="Environment"
        selectedSecondaries={["Energy"]}
        selectedTertiary={null}
        tertiaryByPrimaryAndSecondary={new Map()}
      />,
    );

    const primaryItem = screen.getByRole("button", { name: "Environment" });

    expect(primaryItem).toBeVisible();
    expect(screen.getByRole("button", { name: "Energy" })).toBeVisible();
    expect(screen.queryByText("Navigation")).not.toBeInTheDocument();

    const directory = container.firstElementChild;
    expect(directory).toHaveClass("pl-5");
    expect(primaryItem).toHaveClass("w-full", "px-3", "rounded-xl");
    expect(directory).not.toHaveClass("w-[320px]");
    expect(directory).not.toHaveClass("rounded-2xl");
    expect(directory).not.toHaveClass("shadow-sm");
  });

  it("does not paint a default data category blue before the user selects navigation", () => {
    render(
      <NewSidebar
        embedded
        directSelection={null}
        expandedPrimaries={{ Environment: true }}
        onSelectSecondary={vi.fn()}
        onSelectTertiary={vi.fn()}
        onTogglePrimary={vi.fn()}
        primaryOptions={["Environment"]}
        secondaryByPrimary={new Map([["Environment", ["Energy"]]])}
        selectedPrimary="Environment"
        selectedSecondaries={["Energy"]}
        selectedTertiary={null}
        tertiaryByPrimaryAndSecondary={new Map()}
      />,
    );

    expect(screen.getByRole("button", { name: "Environment" })).not.toHaveClass(
      "bg-[#EFF6FF]",
    );
    expect(screen.getByRole("button", { name: "Environment" })).not.toHaveClass(
      "border-[#BFDBFE]",
    );
    expect(screen.getByRole("button", { name: "Energy" })).not.toHaveClass(
      "bg-[#EFF6FF]",
    );
    expect(screen.getByRole("button", { name: "Energy" })).not.toHaveClass(
      "border-[#BFDBFE]",
    );
  });

  it("highlights only the navigation level directly selected by the user", () => {
    const commonProps = {
      embedded: true,
      expandedPrimaries: { Environment: true },
      onSelectSecondary: vi.fn(),
      onSelectTertiary: vi.fn(),
      onTogglePrimary: vi.fn(),
      primaryOptions: ["Environment"],
      secondaryByPrimary: new Map([["Environment", ["Energy"]]]),
      selectedPrimary: "Environment",
      selectedSecondaries: ["Energy"],
      selectedTertiary: null,
      tertiaryByPrimaryAndSecondary: new Map(),
    };
    const view = render(
      <NewSidebar
        {...commonProps}
        directSelection={{ level: "primary", primary: "Environment" }}
      />,
    );

    expect(screen.getByRole("button", { name: "Environment" })).toHaveClass(
      "bg-[#EFF6FF]",
    );
    expect(screen.getByRole("button", { name: "Energy" })).not.toHaveClass(
      "bg-[#EFF6FF]",
    );

    view.rerender(
      <NewSidebar
        {...commonProps}
        directSelection={{
          level: "secondary",
          primary: "Environment",
          secondary: "Energy",
        }}
      />,
    );

    expect(screen.getByRole("button", { name: "Environment" })).not.toHaveClass(
      "bg-[#EFF6FF]",
    );
    expect(screen.getByRole("button", { name: "Energy" })).toHaveClass(
      "bg-[#EFF6FF]",
    );
  });

  it("highlights a tertiary item only inside its selected secondary branch", () => {
    render(
      <NewSidebar
        embedded
        directSelection={{
          level: "tertiary",
          metricName: "Shared Metric",
          primary: "Environment",
          secondary: "Energy",
        }}
        expandedPrimaries={{ Environment: true }}
        onSelectSecondary={vi.fn()}
        onSelectTertiary={vi.fn()}
        onTogglePrimary={vi.fn()}
        primaryOptions={["Environment"]}
        secondaryByPrimary={new Map([
          ["Environment", ["Energy", "Water"]],
        ])}
        selectedPrimary="Environment"
        selectedSecondaries={["Energy"]}
        selectedTertiary="Shared Metric"
        tertiaryByPrimaryAndSecondary={new Map([
          [
            "Environment",
            new Map([
              ["Energy", ["Shared Metric"]],
              ["Water", ["Shared Metric"]],
            ]),
          ],
        ])}
      />,
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Energy" })[1]);
    fireEvent.click(screen.getAllByRole("button", { name: "Water" })[1]);

    const sharedMetrics = screen.getAllByRole("button", {
      name: "Shared Metric",
    });
    expect(sharedMetrics).toHaveLength(2);
    expect(sharedMetrics[0]).toHaveClass("bg-[#DBEAFE]");
    expect(sharedMetrics[1]).not.toHaveClass("bg-[#DBEAFE]");
  });
});
