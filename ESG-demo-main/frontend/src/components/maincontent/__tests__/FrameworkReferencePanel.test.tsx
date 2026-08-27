import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  StandardsLibraryCatalogResponse,
  StandardsLibraryMetric,
  StandardsLibraryMetricsResponse,
} from "@/lib/api";

import FrameworkReferencePanel from "../FrameworkReferencePanel";

const standardsApi = vi.hoisted(() => ({
  getStandardMetrics: vi.fn(),
  getStandardsCatalog: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ apiService: standardsApi }));

vi.mock("@/i18n/useT", () => ({
  useT: () => ({ lang: "en" }),
}));

const catalog: StandardsLibraryCatalogResponse = {
  frameworks: [
    {
      id: "sasb",
      name: "SASB",
      as_of: "Jan 2026",
      source_url: "https://www.ifrs.org/issued-standards/sasb-standards/",
      available: true,
      scope_count: 4,
      group_label: "Industry",
      scope_label: "Sub-industry",
      groups: [
        {
          id: "financials",
          label: "Financials",
          scopes: [
            { id: "Commercial Banks", label: "Commercial Banks" },
            { id: "Insurance", label: "Insurance" },
          ],
        },
        {
          id: "technology_communications",
          label: "Technology & Communications",
          scopes: [
            { id: "Hardware", label: "Hardware" },
            { id: "Software & IT Services", label: "Software & IT Services" },
          ],
        },
      ],
    },
    {
      id: "gri",
      name: "GRI",
      as_of: "Jun 2026",
      source_url:
        "https://www.globalreporting.org/standards/gri-standards-download-center/",
      available: true,
      scope_count: 4,
      group_label: "Sector",
      scope_label: "Topic",
      groups: [
        {
          id: "coal_sector",
          label: "Coal Sector",
          scopes: [
            { id: "all", label: "All disclosures" },
            { id: "emissions", label: "Emissions" },
          ],
        },
        {
          id: "oil_and_gas_sector",
          label: "Oil & Gas Sector",
          scopes: [
            { id: "all", label: "All disclosures" },
            { id: "water", label: "Water" },
          ],
        },
      ],
    },
    {
      id: "cdp",
      name: "CDP",
      as_of: "Apr 2026",
      source_url: "https://www.cdp.net/en/disclosure-2026",
      available: true,
      scope_count: 1,
      group_label: "Topic groups",
      scope_label: "Topic",
      groups: [
        {
          id: "topics",
          label: "Topics",
          scopes: [{ id: "climate", label: "Climate" }],
        },
      ],
    },
    {
      id: "aasb",
      name: "AASB",
      as_of: "Nov 2025",
      source_url: "https://standards.aasb.gov.au/sustainability-reporting-standards",
      available: false,
      scope_count: 0,
      group_label: "Standards",
      scope_label: "Standard",
      groups: [],
    },
  ],
};

function metric(id: string, name: string, code?: string): StandardsLibraryMetric {
  return {
    id,
    code,
    name,
    topic: "Test topic",
    category: "Quantitative",
    unit: "Number",
  };
}

function metricsResponse(
  frameworkId: "sasb" | "gri" | "cdp",
  groupId: string,
  groupLabel: string,
  scopeId: string,
  scopeLabel: string,
  metrics: StandardsLibraryMetric[],
): StandardsLibraryMetricsResponse {
  const framework = catalog.frameworks.find((item) => item.id === frameworkId);
  if (!framework) throw new Error(`Missing fixture framework: ${frameworkId}`);
  return {
    framework: {
      id: framework.id,
      name: framework.name,
      as_of: framework.as_of,
      source_url: framework.source_url,
      group_label: framework.group_label,
      scope_label: framework.scope_label,
    },
    group: { id: groupId, label: groupLabel },
    scope: { id: scopeId, label: scopeLabel },
    total_metrics: metrics.length,
    metrics,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

async function frameworkButton(name: string) {
  const frameworks = await screen.findByRole("group", { name: "Frameworks" });
  return within(frameworks).getByRole("button", {
    name: new RegExp(`^${name}\\b`, "i"),
  });
}

async function chooseFramework(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(await frameworkButton(name));
}

describe("FrameworkReferencePanel", () => {
  beforeEach(() => {
    standardsApi.getStandardsCatalog.mockReset();
    standardsApi.getStandardMetrics.mockReset();
    standardsApi.getStandardsCatalog.mockResolvedValue(catalog);
  });

  it("shows catalog loading and a recoverable catalog error", async () => {
    const pendingCatalog = deferred<StandardsLibraryCatalogResponse>();
    standardsApi.getStandardsCatalog.mockReturnValueOnce(pendingCatalog.promise);

    const { unmount } = render(<FrameworkReferencePanel />);

    expect(screen.getByText(/loading/i)).toBeVisible();
    unmount();

    standardsApi.getStandardsCatalog
      .mockRejectedValueOnce(new Error("Catalog service unavailable"))
      .mockResolvedValueOnce(catalog);
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    expect(await screen.findByText("Catalog service unavailable")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(await frameworkButton("SASB")).toBeVisible();
    expect(standardsApi.getStandardsCatalog).toHaveBeenLastCalledWith(true);
  });

  it("renders the four frameworks returned by the catalog without TCFD", async () => {
    render(<FrameworkReferencePanel />);

    for (const name of ["SASB", "GRI", "CDP", "AASB"]) {
      expect(await frameworkButton(name)).toBeVisible();
    }
    expect(screen.queryByRole("button", { name: /^TCFD\b/i })).not.toBeInTheDocument();
  });

  it("keeps the library surface borderless with one semantically selected framework", async () => {
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    const library = await screen.findByRole("region", { name: "Standards Library" });
    const heading = screen.getByRole("heading", { level: 1, name: "Standards Library" });
    expect(library).toHaveAttribute("data-testid", "standards-library");
    expect(library).toHaveAttribute("aria-labelledby", heading.id);
    expect(heading.parentElement).toHaveClass("mb-6");
    expect(heading.parentElement).not.toHaveClass("mb-9");

    const staticLibraryClasses = library.className
      .split(/\s+/)
      .filter((token) => token && !token.includes(":"));
    expect(
      staticLibraryClasses.some((token) => /^(?:border|shadow|rounded)(?:-|$)/.test(token)),
    ).toBe(false);

    const frameworks = screen.getByRole("group", { name: "Frameworks" });
    expect(frameworks).toHaveClass("gap-4", "sm:gap-5", "overflow-x-auto");
    expect(frameworks).not.toHaveClass("gap-6");
    const browser = document.getElementById("standards-browser");
    expect(browser).not.toBeNull();
    expect(browser as HTMLElement).toHaveClass("mt-6");
    expect(browser as HTMLElement).not.toHaveClass("mt-9");
    const browserGrid = screen.getByLabelText("SASB taxonomy").parentElement;
    expect(browserGrid).toHaveClass(
      "grid",
      "gap-6",
      "lg:grid-cols-[248px_minmax(0,1fr)]",
      "xl:gap-8",
    );
    expect(browserGrid).not.toHaveClass("gap-9", "xl:gap-14");
    const frameworkArticles = Array.from(frameworks.children).filter(
      (child): child is HTMLElement => child instanceof HTMLElement && child.tagName === "ARTICLE",
    );
    expect(frameworkArticles).toHaveLength(catalog.frameworks.length);
    for (const article of frameworkArticles) {
      const staticClasses = article.className
        .split(/\s+/)
        .filter((token) => token && !token.includes(":"));
      expect(
        staticClasses.some((token) => /^(?:border(?:-|$)|ring-1(?:$|\/))/.test(token)),
      ).toBe(false);
    }

    const frameworkButtons = within(frameworks).getAllByRole("button");
    expect(
      frameworkButtons.filter((button) => button.getAttribute("aria-pressed") === "true"),
    ).toEqual([await frameworkButton("SASB")]);

    await user.click(await frameworkButton("GRI"));
    expect(
      frameworkButtons.filter((button) => button.getAttribute("aria-pressed") === "true"),
    ).toEqual([await frameworkButton("GRI")]);
  });

  it("switches the SASB industry before loading the selected Hardware sub-industry", async () => {
    const hardwareMetrics = metricsResponse(
      "sasb",
      "technology_communications",
      "Technology & Communications",
      "Hardware",
      "Hardware",
      [
        metric("sasb:hardware:1", "Product security disclosure", "TC-HW-230a.1"),
        metric("sasb:hardware:2", "Materials sourcing disclosure", "TC-HW-440a.1"),
      ],
    );
    standardsApi.getStandardMetrics.mockResolvedValue(hardwareMetrics);
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "SASB");
    const industry = screen.getByRole("combobox", { name: "Industry" });
    expect(industry).toHaveValue("financials");
    const initialScope = screen.getByRole("button", { name: /^Commercial Banks$/i });
    expect(initialScope).toBeVisible();
    expect(initialScope.parentElement).toHaveClass(
      "overflow-y-auto",
      "overscroll-y-auto",
    );
    expect(screen.queryByRole("button", { name: /^Hardware$/i })).not.toBeInTheDocument();

    await user.selectOptions(industry, "technology_communications");
    expect(industry).toHaveValue("technology_communications");
    expect(screen.getByRole("button", { name: /^Hardware$/i })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /^Commercial Banks$/i }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Hardware$/i }));

    await waitFor(() => {
      expect(standardsApi.getStandardMetrics).toHaveBeenCalledWith(
        "sasb",
        "technology_communications",
        "Hardware",
        expect.any(AbortSignal),
      );
    });
    expect(await screen.findByText("Product security disclosure")).toBeVisible();
    expect(screen.getByText("TC-HW-230a.1")).toBeVisible();
    expect(screen.getByText("Materials sourcing disclosure")).toBeVisible();
  });

  it("shows only the leading category in the Type column", async () => {
    standardsApi.getStandardMetrics.mockResolvedValue(
      metricsResponse(
        "sasb",
        "technology_communications",
        "Technology & Communications",
        "Hardware",
        "Hardware",
        [
          {
            ...metric("sasb:hardware:type", "Type display metric", "TC-HW-000.C"),
            category: "Quantitative",
            type: "Sustainability Disclosure Topics & Metrics",
          },
        ],
      ),
    );
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "SASB");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Industry" }),
      "technology_communications",
    );
    await user.click(screen.getByRole("button", { name: /^Hardware$/i }));
    const row = (await screen.findByText("Type display metric")).closest("tr");

    expect(row).not.toBeNull();
    expect(within(row as HTMLTableRowElement).getByText("Quantitative")).toBeVisible();
    expect(
      within(row as HTMLTableRowElement).queryByText("Sustainability Disclosure Topics & Metrics"),
    ).not.toBeInTheDocument();
  });

  it("shows the complete simple definition when both definition fields exist", async () => {
    const simpleDefinition = [
      "Concise metric definition with its reporting boundary.",
      "The second line preserves the requested denominator and unit.",
      "This final sentence proves that the simple definition was not truncated.",
    ].join("\n");
    const fullDefinition = "Long technical protocol that should remain hidden when a simple definition exists.";
    standardsApi.getStandardMetrics.mockResolvedValue(
      metricsResponse(
        "sasb",
        "technology_communications",
        "Technology & Communications",
        "Hardware",
        "Hardware",
        [
          {
            ...metric("sasb:hardware:definition", "Simple definition metric", "TC-HW-000.A"),
            definition: fullDefinition,
            simple_definition: simpleDefinition,
          },
        ],
      ),
    );
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "SASB");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Industry" }),
      "technology_communications",
    );
    await user.click(screen.getByRole("button", { name: /^Hardware$/i }));
    expect(await screen.findByText("Simple definition metric")).toBeVisible();

    await user.click(screen.getByText("View definition"));
    const renderedDefinition = screen.getByText(
      /final sentence proves that the simple definition was not truncated/i,
    );
    expect(renderedDefinition).toBeVisible();
    expect(renderedDefinition.textContent).toBe(simpleDefinition);
    expect(renderedDefinition).toHaveClass("whitespace-pre-wrap", "break-words");
    expect(screen.queryByText(fullDefinition)).not.toBeInTheDocument();
  });

  it("falls back to the full definition when the simple definition is missing", async () => {
    const fullDefinition = "Fallback full definition, including its final sentence.";
    standardsApi.getStandardMetrics.mockResolvedValue(
      metricsResponse(
        "sasb",
        "technology_communications",
        "Technology & Communications",
        "Hardware",
        "Hardware",
        [
          {
            ...metric("sasb:hardware:fallback-definition", "Fallback definition metric", "TC-HW-000.B"),
            definition: fullDefinition,
            simple_definition: null,
          },
        ],
      ),
    );
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "SASB");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Industry" }),
      "technology_communications",
    );
    await user.click(screen.getByRole("button", { name: /^Hardware$/i }));
    expect(await screen.findByText("Fallback definition metric")).toBeVisible();

    await user.click(screen.getByText("View definition"));
    expect(screen.getByText(fullDefinition)).toBeVisible();
  });

  it("switches GRI sector groups before requesting a topic", async () => {
    const response = metricsResponse(
      "gri",
      "oil_and_gas_sector",
      "Oil & Gas Sector",
      "water",
      "Water",
      [metric("gri:oil:water:1", "Water withdrawal")],
    );
    standardsApi.getStandardMetrics.mockResolvedValue(response);
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "GRI");
    const sector = screen.getByRole("combobox", { name: "Sector" });
    expect(sector).toHaveValue("coal_sector");
    expect(screen.getByRole("button", { name: /^Emissions$/i })).toBeVisible();
    await user.selectOptions(sector, "oil_and_gas_sector");
    expect(sector).toHaveValue("oil_and_gas_sector");
    expect(screen.getByRole("button", { name: /^Water$/i })).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Emissions$/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Water$/i }));
    expect(await screen.findByText("Water withdrawal")).toBeVisible();
    expect(standardsApi.getStandardMetrics).toHaveBeenCalledWith(
      "gri",
      "oil_and_gas_sector",
      "water",
      expect.any(AbortSignal),
    );
  });

  it("keeps the official AASB link available when no local metrics exist", async () => {
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "AASB");

    const emptyStateHeading = screen.getByRole("heading", { level: 2, name: "AASB" });
    const emptyState = emptyStateHeading.parentElement;
    expect(emptyState).not.toBeNull();
    expect(within(emptyState as HTMLElement).getByText(/metrics collection/i)).toBeVisible();
    const link = within(emptyState as HTMLElement).getByRole("link", {
      name: /official.*AASB|AASB.*official/i,
    });
    expect(link).toHaveAttribute(
      "href",
      "https://standards.aasb.gov.au/sustainability-reporting-standards",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect((link.getAttribute("rel") ?? "").split(/\s+/)).toEqual(
      expect.arrayContaining(["noopener", "noreferrer"]),
    );
    expect(standardsApi.getStandardMetrics).not.toHaveBeenCalled();
  });

  it("aborts and ignores a stale metrics request during rapid scope switching", async () => {
    const hardwareRequest = deferred<StandardsLibraryMetricsResponse>();
    const softwareRequest = deferred<StandardsLibraryMetricsResponse>();
    standardsApi.getStandardMetrics.mockImplementation(
      (_frameworkId: string, _groupId: string, scopeId: string) =>
        scopeId === "Hardware" ? hardwareRequest.promise : softwareRequest.promise,
    );
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "SASB");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Industry" }),
      "technology_communications",
    );
    await user.click(screen.getByRole("button", { name: /^Hardware$/i }));
    await waitFor(() => expect(standardsApi.getStandardMetrics).toHaveBeenCalledTimes(1));
    const firstSignal = standardsApi.getStandardMetrics.mock.calls[0][3] as AbortSignal;

    await user.click(screen.getByRole("button", { name: /^Software & IT Services$/i }));
    await waitFor(() => expect(standardsApi.getStandardMetrics).toHaveBeenCalledTimes(2));
    expect(firstSignal.aborted).toBe(true);

    await act(async () => {
      softwareRequest.resolve(
        metricsResponse(
          "sasb",
          "technology_communications",
          "Technology & Communications",
          "Software & IT Services",
          "Software & IT Services",
          [metric("sasb:software:1", "Software data privacy")],
        ),
      );
    });
    expect(await screen.findByText("Software data privacy")).toBeVisible();

    await act(async () => {
      hardwareRequest.resolve(
        metricsResponse(
          "sasb",
          "technology_communications",
          "Technology & Communications",
          "Hardware",
          "Hardware",
          [metric("sasb:hardware:late", "Stale hardware metric")],
        ),
      );
    });
    expect(screen.queryByText("Stale hardware metric")).not.toBeInTheDocument();
    expect(screen.getByText("Software data privacy")).toBeVisible();
  });

  it("searches the selected scope's metrics and paginates the filtered result", async () => {
    const manyMetrics = Array.from({ length: 45 }, (_, index) =>
      metric(
        `sasb:hardware:${index + 1}`,
        `Hardware metric ${String(index + 1).padStart(2, "0")}`,
        `TC-HW-${String(index + 1).padStart(2, "0")}`,
      ),
    );
    standardsApi.getStandardMetrics.mockResolvedValue(
      metricsResponse(
        "sasb",
        "technology_communications",
        "Technology & Communications",
        "Hardware",
        "Hardware",
        manyMetrics,
      ),
    );
    const user = userEvent.setup();
    render(<FrameworkReferencePanel />);

    await chooseFramework(user, "SASB");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Industry" }),
      "technology_communications",
    );
    await user.click(screen.getByRole("button", { name: /^Hardware$/i }));
    expect(await screen.findByText("Hardware metric 01")).toBeVisible();
    expect(screen.queryByText("Hardware metric 45")).not.toBeInTheDocument();

    const metricsRegion = screen.getByRole("region", { name: /metrics/i });
    await user.click(within(metricsRegion).getByRole("button", { name: /next/i }));
    expect(within(metricsRegion).queryByText("Hardware metric 01")).not.toBeInTheDocument();

    const search = within(metricsRegion).getByRole("searchbox", { name: /search metrics/i });
    await user.clear(search);
    await user.type(search, "Hardware metric 45");
    expect(within(metricsRegion).getByText("Hardware metric 45")).toBeVisible();
    expect(within(metricsRegion).queryByText("Hardware metric 01")).not.toBeInTheDocument();
    expect(within(metricsRegion).getByText(/page 1 of 1/i)).toBeVisible();
  });
});
