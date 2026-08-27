import { describe, expect, it } from "vitest";
import {
  deriveGraphDisplayData,
  graphFilterOptions,
  graphNodeSearchText,
  graphNeighborhood,
  mergeDisclosureGraphs,
  parseStoredGraphPositions,
} from "../graphData";
import type {
  DisclosureGraphFilters,
  DisclosureGraphResponse,
} from "../types";

const noFilters: DisclosureGraphFilters = {
  reportIds: [],
  frameworks: [],
  scopes: [],
  years: [],
  topics: [],
  statuses: [],
  collapsedMetricCodes: [],
};

function dellGraph(): DisclosureGraphResponse {
  const nodes: DisclosureGraphResponse["nodes"] = [];
  const edges: DisclosureGraphResponse["edges"] = [];
  for (let metricIndex = 0; metricIndex < 24; metricIndex += 1) {
    nodes.push({
      id: `metric:${metricIndex}`,
      type: "MetricItem",
      label: `Metric item ${metricIndex}`,
      properties: {
        metric_code: metricIndex < 2 ? "TC-HW-000.A" : `TC-HW-${metricIndex}`,
        framework: "SASB",
        scope_key: "hardware",
        topic: metricIndex % 2 ? "Energy" : "Materials",
      },
    });
  }
  for (let reportIndex = 0; reportIndex < 6; reportIndex += 1) {
    const reportNodeId = `report:dell-${2019 + reportIndex}`;
    nodes.push({
      id: reportNodeId,
      type: "Report",
      label: `Dell ${2019 + reportIndex}`,
      properties: {
        file_id: `dell-${2019 + reportIndex}`,
        report_year: 2019 + reportIndex,
        framework: "SASB",
        scope_key: "hardware",
      },
    });
    for (let metricIndex = 0; metricIndex < 24; metricIndex += 1) {
      const disclosureId = `disclosure:dell-${2019 + reportIndex}:metric-${metricIndex}`;
      const status = metricIndex % 3 === 0
        ? "fully_disclosed"
        : metricIndex % 3 === 1
          ? "partially_disclosed"
          : "not_disclosed";
      nodes.push({
        id: disclosureId,
        type: "Disclosure",
        label: `${status} | metric ${metricIndex}`,
        properties: { disclosure_status: status, framework: "SASB", scope_key: "hardware" },
      });
      edges.push(
        {
          id: `has:${disclosureId}`,
          type: "has_disclosure",
          source: reportNodeId,
          target: disclosureId,
          properties: {},
        },
        {
          id: `assesses:${disclosureId}`,
          type: "assesses",
          source: disclosureId,
          target: `metric:${metricIndex}`,
          properties: {},
        },
      );
    }
  }
  return {
    schema_version: "1.0",
    graph_revision: "dell-revision",
    nodes,
    edges,
    stats: { node_count: nodes.length, edge_count: edges.length },
    truncated: false,
  };
}

const neighborhoodData = {
  nodes: ["a", "b", "c", "d", "isolated"].map((id) => ({
    id,
    type: "metric",
    label: id.toUpperCase(),
    properties: {},
  })),
  edges: [
    { id: "edge:a-b", type: "relation", source: "a", target: "b", properties: {} },
    { id: "edge:b-c", type: "relation", source: "b", target: "c", properties: {} },
    { id: "edge:c-d", type: "relation", source: "c", target: "d", properties: {} },
  ],
  underlyingDisclosureCount: 0,
};

describe("graph neighborhood", () => {
  it("keeps only valid roots and no relationships at degree zero", () => {
    expect(graphNeighborhood(neighborhoodData, ["c", "missing", "c"], 0)).toEqual({
      nodeIds: ["c"],
      edgeIds: [],
    });
  });

  it("returns the deterministic one-degree neighborhood in graph order", () => {
    expect(graphNeighborhood(neighborhoodData, ["b"], 1)).toEqual({
      nodeIds: ["a", "b", "c"],
      edgeIds: ["edge:a-b", "edge:b-c"],
    });
  });

  it("runs breadth-first through exactly two degrees without leaking farther edges", () => {
    expect(graphNeighborhood(neighborhoodData, ["a"], 2)).toEqual({
      nodeIds: ["a", "b", "c"],
      edgeIds: ["edge:a-b", "edge:b-c"],
    });
  });
});

describe("disclosure graph projection", () => {
  it("builds filter options and duplicate-code counts from one graph catalog", () => {
    const options = graphFilterOptions(dellGraph());

    expect(options.frameworks).toEqual(["SASB"]);
    expect(options.scopes).toEqual(["hardware"]);
    expect(options.years).toEqual(["2024", "2023", "2022", "2021", "2020", "2019"]);
    expect(options.topics).toEqual(["Energy", "Materials"]);
    expect(options.statuses).toEqual([
      "fully_disclosed",
      "not_disclosed",
      "partially_disclosed",
    ]);
    expect(options.metricCodes).toHaveLength(23);
    expect(options.metricCodeCounts.find(({ code }) => code === "TC-HW-000.A"))
      .toEqual({ code: "TC-HW-000.A", count: 2 });
  });

  it("keeps six reports x 24 metric items as 144 independent disclosure relationships", () => {
    const view = deriveGraphDisplayData(dellGraph(), noFilters, "overview");

    expect(view.underlyingDisclosureCount).toBe(144);
    const disclosureEdges = view.edges.filter((edge) => edge.disclosure_id);
    expect(disclosureEdges).toHaveLength(144);
    expect(new Set(disclosureEdges.map((edge) => edge.disclosure_id)).size).toBe(144);
    expect(view.nodes.filter((node) => node.type === "MetricItem")).toHaveLength(24);
  });

  it("never coalesces metric items that share a SASB code", () => {
    const view = deriveGraphDisplayData(dellGraph(), noFilters, "overview");
    const repeatedCodeMetrics = view.nodes.filter(
      (node) => !node.synthetic && node.properties.metric_code === "TC-HW-000.A",
    );

    expect(repeatedCodeMetrics.map((node) => node.id)).toEqual(["metric:0", "metric:1"]);
    expect(
      view.edges.filter(
        (edge) => edge.disclosure_id && ["metric:0", "metric:1"].includes(edge.target),
      ),
    ).toHaveLength(12);
    expect(
      view.nodes.some((node) => node.synthetic && node.properties.metric_code === "TC-HW-000.A"),
    ).toBe(false);
    expect(view.edges.some((edge) => edge.type === "groups_metric")).toBe(false);

    const firstFamilyTags = view.edges
      .filter((edge) => edge.disclosure_id && edge.target === "metric:0")
      .map((edge) => edge.properties.tags);
    const secondFamilyTags = view.edges
      .filter((edge) => edge.disclosure_id && edge.target === "metric:1")
      .map((edge) => edge.properties.tags);
    expect(firstFamilyTags).toEqual(Array(6).fill(["curve-n70"]));
    expect(secondFamilyTags).toEqual(Array(6).fill(["curve-p70"]));
  });

  it("collapses only the visual code group without changing disclosure cardinality", () => {
    const view = deriveGraphDisplayData(
      dellGraph(),
      { ...noFilters, collapsedMetricCodes: ["TC-HW-000.A"] },
      "overview",
    );

    expect(view.underlyingDisclosureCount).toBe(144);
    expect(view.edges).toHaveLength(144);
    expect(view.nodes.filter((node) => node.synthetic)).toHaveLength(1);
    expect(view.nodes.some((node) => node.id === "metric:0")).toBe(false);
    expect(view.nodes.some((node) => node.id === "metric:1")).toBe(false);
    const groupNode = view.nodes.find((node) => node.synthetic);
    expect(groupNode?.label).toBe(
      "TC-HW-000.A — Units produced by product category",
    );
    expect(groupNode?.properties.metric_names).toEqual(["Metric item 0", "Metric item 1"]);
    expect(groupNode?.properties.description).toContain("Sub-items:\n- Metric item 0");
    expect(graphNodeSearchText(groupNode!)).toContain("metric item 1");
    const groupedTags = view.edges
      .filter((edge) => edge.target === "metric-group:TC-HW-000.A")
      .flatMap((edge) => edge.properties.tags as string[]);
    expect(groupedTags.filter((tag) => tag === "curve-n70")).toHaveLength(6);
    expect(groupedTags.filter((tag) => tag === "curve-p70")).toHaveLength(6);
  });

  it("uses the supplied Kumu code-level title for a single Hardware metric", () => {
    const graph = dellGraph();
    const metric = graph.nodes.find((node) => node.id === "metric:2")!;
    metric.properties.metric_code = "TC-HW-410a.1";
    const view = deriveGraphDisplayData(graph, noFilters, "overview");

    expect(view.nodes.find((node) => node.id === "metric:2")?.display_label).toBe(
      "TC-HW-410a.1 — IEC 62474 declarable substances",
    );
  });

  it("filters to one report without mutating the source disclosures", () => {
    const graph = dellGraph();
    const view = deriveGraphDisplayData(
      graph,
      { ...noFilters, reportIds: ["dell-2024"] },
      "overview",
    );

    expect(view.underlyingDisclosureCount).toBe(24);
    expect(view.edges.filter((edge) => edge.disclosure_id)).toHaveLength(24);
    expect(graph.nodes.filter((node) => node.type === "Disclosure")).toHaveLength(144);
  });

  it("keeps metric-family curvature stable when disclosure filters hide siblings", () => {
    const graph = dellGraph();
    const fullView = deriveGraphDisplayData(graph, noFilters, "overview");
    const filteredView = deriveGraphDisplayData(
      graph,
      { ...noFilters, statuses: ["fully_disclosed"] },
      "overview",
    );
    const fullEdge = fullView.edges.find((edge) => edge.target === "metric:0");
    const filteredEdge = filteredView.edges.find((edge) => edge.target === "metric:0");

    expect(fullEdge?.properties.tags).toEqual(["curve-n70"]);
    expect(filteredEdge?.properties.tags).toEqual(fullEdge?.properties.tags);
  });

  it("uses only supported Kumu curvature tags for metric families larger than eight", () => {
    const graph = dellGraph();
    graph.nodes
      .filter((node) => node.type === "MetricItem")
      .slice(0, 10)
      .forEach((node) => {
        node.properties.metric_code = "TC-HW-SHARED";
      });
    const view = deriveGraphDisplayData(graph, noFilters, "overview");
    const tags = view.edges
      .filter((edge) => /^metric:[0-9]$/.test(edge.target))
      .map((edge) => (edge.properties.tags as string[])[0]);

    expect(tags).toHaveLength(60);
    expect(new Set(tags).size).toBe(10);
    expect(tags.every((tag) => /^curve-(?:0|[np](?:10|15|20|25|30|40|50|60|70))$/.test(tag)))
      .toBe(true);
  });

  it("preserves candidate evidence as a candidate relationship", () => {
    const graph = dellGraph();
    graph.nodes.push({
      id: "evidence:candidate",
      type: "evidence",
      label: "Retrieved paragraph",
      properties: { evidence_kind: "complete_block" },
    });
    graph.edges.push({
      id: "candidate:one",
      type: "candidate_evidence",
      source: "disclosure:dell-2024:metric-2",
      target: "evidence:candidate",
      properties: { role: "candidate" },
    });

    const view = deriveGraphDisplayData(graph, noFilters, "expanded");
    const evidenceEdge = view.edges.find((edge) => edge.target === "evidence:candidate");
    expect(evidenceEdge?.type).toBe("candidate_evidence");
    expect(evidenceEdge?.properties.evidence_role).toBe("candidate");
  });

  it("invalidates the cached topology when an in-place evidence expansion changes array sizes", () => {
    const graph = dellGraph();
    const before = deriveGraphDisplayData(graph, noFilters, "expanded");
    expect(before.nodes.some((node) => node.id === "evidence:late")).toBe(false);

    graph.nodes.push({
      id: "evidence:late",
      type: "evidence",
      label: "Late evidence",
      properties: {},
    });
    graph.edges.push({
      id: "candidate:late",
      type: "candidate_evidence",
      source: "disclosure:dell-2024:metric-2",
      target: "evidence:late",
      properties: {},
    });

    const after = deriveGraphDisplayData(graph, noFilters, "expanded");
    expect(after.nodes.some((node) => node.id === "evidence:late")).toBe(true);
    expect(after.edges.some((edge) => edge.id === "candidate_evidence:disclosure:dell-2024:metric-2:evidence:late"))
      .toBe(true);
  });
});

describe("multi-report graph merge", () => {
  it("deduplicates stable metric nodes while preserving report-specific disclosures", () => {
    const source = dellGraph();
    const first = {
      ...source,
      graph_id: "report:one",
      nodes: source.nodes.filter((node) =>
        node.id === "metric:0" || node.id === "report:dell-2023" || node.id.includes("dell-2023:metric-0"),
      ),
      edges: source.edges.filter((edge) => edge.id.includes("dell-2023:metric-0")),
    };
    const second = {
      ...source,
      graph_id: "report:two",
      nodes: source.nodes.filter((node) =>
        node.id === "metric:0" || node.id === "report:dell-2024" || node.id.includes("dell-2024:metric-0"),
      ),
      edges: source.edges.filter((edge) => edge.id.includes("dell-2024:metric-0")),
    };

    const merged = mergeDisclosureGraphs([first, second]);
    expect(merged.nodes.filter((node) => node.id === "metric:0")).toHaveLength(1);
    expect(merged.nodes.filter((node) => node.type === "Disclosure")).toHaveLength(2);
  });
});

describe("stored graph positions", () => {
  it("restores finite positions only for nodes that still exist after reanalysis", () => {
    const parsed = parseStoredGraphPositions(
      JSON.stringify({
        revision: "old",
        positions: {
          retained: { x: 12, y: 24 },
          deleted: { x: 3, y: 5 },
          invalid: { x: "nan", y: 8 },
        },
      }),
      ["retained", "invalid", "new"],
    );

    expect(parsed.revision).toBe("old");
    expect(parsed.positions).toEqual({ retained: { x: 12, y: 24 } });
  });
});
