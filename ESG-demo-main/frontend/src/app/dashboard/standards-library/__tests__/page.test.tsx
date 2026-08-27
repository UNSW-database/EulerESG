import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StandardsLibraryPage from "../page";

vi.mock("@/components/maincontent/FrameworkReferencePanel", () => ({
  default: () => (
    <section aria-label="Standards Library" data-testid="framework-reference-panel">
      Standards Library panel
    </section>
  ),
}));

describe("StandardsLibraryPage", () => {
  it("places the lazily loaded Standards Library panel in the page's main content", async () => {
    render(<StandardsLibraryPage />);

    const main = screen.getByRole("main");
    const panel = await within(main).findByRole("region", { name: "Standards Library" });
    const content = screen.getByTestId("standards-library-content");

    expect(main).toBeVisible();
    expect(panel).toBeVisible();
    expect(panel).toHaveAttribute("data-testid", "framework-reference-panel");
    expect(content).toHaveClass(
      "max-w-[1760px]",
      "px-4",
      "py-5",
      "sm:px-6",
      "sm:py-6",
      "lg:px-8",
    );
    expect(content).not.toHaveClass(
      "max-w-[1540px]",
      "px-5",
      "py-8",
      "sm:px-8",
      "lg:px-12",
      "lg:py-10",
    );
  });
});
