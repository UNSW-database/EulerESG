import type {
  DisclosureGraphEdge,
  DisclosureGraphFilters,
  DisclosureGraphNode,
  DisclosureGraphResponse,
  GraphDisplayData,
  GraphDisplayMode,
  StoredGraphPositions,
} from "./types";

const REPORT_TYPES = new Set(["report", "reportnode"]);
const METRIC_TYPES = new Set(["metric", "metricitem", "metric_item"]);
const DISCLOSURE_TYPES = new Set(["disclosure", "assessment", "disclosureitem"]);
const EVIDENCE_TYPES = new Set(["evidence", "evidenceblock", "evidence_block"]);

export function normalizeNodeType(type: unknown): "report" | "metric" | "disclosure" | "evidence" | "other" {
  const normalized = String(type ?? "").replace(/[\s-]/g, "_").toLowerCase();
  if (REPORT_TYPES.has(normalized)) return "report";
  if (METRIC_TYPES.has(normalized)) return "metric";
  if (DISCLOSURE_TYPES.has(normalized)) return "disclosure";
  if (EVIDENCE_TYPES.has(normalized)) return "evidence";
  return "other";
}

export function propertyValue(
  properties: Record<string, unknown> | undefined,
  ...keys: string[]
): unknown {
  if (!properties) return undefined;
  for (const key of keys) {
    const value = properties[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

export function propertyString(
  properties: Record<string, unknown> | undefined,
  ...keys: string[]
): string {
  const value = propertyValue(properties, ...keys);
  if (Array.isArray(value)) return value.map(String).filter(Boolean).join(", ");
  return value === undefined ? "" : String(value).trim();
}

export function normalizeDisclosureStatus(value: unknown): string {
  const normalized = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  if (["fully", "full", "fully_disclosed", "disclosed"].includes(normalized)) {
    return "fully_disclosed";
  }
  if (["partial", "partially", "partially_disclosed"].includes(normalized)) {
    return "partially_disclosed";
  }
  if (["not", "none", "not_disclosed", "undisclosed"].includes(normalized)) {
    return "not_disclosed";
  }
  return normalized;
}

export function disclosureStatus(node: DisclosureGraphNode): string {
  return normalizeDisclosureStatus(
    propertyValue(node.properties, "disclosure_status", "status", "assessment_status"),
  );
}

export function metricCode(node: DisclosureGraphNode): string {
  return (
    propertyString(node.properties, "metric_code", "code", "metric_id") ||
    node.group_id ||
    node.label
  );
}

export function reportId(node: DisclosureGraphNode): string {
  return propertyString(node.properties, "file_id", "report_id") || node.id.replace(/^report:/, "");
}

export function graphNodeSearchText(node: DisclosureGraphNode): string {
  const metricNames = node.properties.metric_names;
  const searchTerms = node.properties.search_terms;
  return [
    node.label,
    node.id,
    node.group_id,
    propertyString(node.properties, "filename", "report_name", "display_name"),
    metricCode(node),
    propertyString(
      node.properties,
      "metric_name",
      "name",
      "family_label",
      "description",
      "definition",
      "topic",
    ),
    Array.isArray(metricNames) ? metricNames.join(" ") : String(metricNames || ""),
    Array.isArray(searchTerms) ? searchTerms.join(" ") : String(searchTerms || ""),
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
}

interface GraphEdgeLike {
  id: string;
  source: string;
  target: string;
}

interface IndexedGraphEdge<T extends GraphEdgeLike> {
  edge: T;
  otherId: string;
}

function buildEdgeIndex<T extends GraphEdgeLike>(
  edges: T[],
): Map<string, IndexedGraphEdge<T>[]> {
  const index = new Map<string, IndexedGraphEdge<T>[]>();
  const append = (nodeId: string, item: IndexedGraphEdge<T>) => {
    const bucket = index.get(nodeId);
    if (bucket) bucket.push(item);
    else index.set(nodeId, [item]);
  };
  for (const edge of edges) {
    append(edge.source, { edge, otherId: edge.target });
    append(edge.target, { edge, otherId: edge.source });
  }
  return index;
}

interface DisplayGraphIndex {
  nodeSource: GraphDisplayData["nodes"];
  edgeSource: GraphDisplayData["edges"];
  nodeCount: number;
  edgeCount: number;
  validNodeIds: Set<string>;
  edgeIndex: Map<string, IndexedGraphEdge<GraphDisplayData["edges"][number]>[]>;
}

const displayGraphIndexCache = new WeakMap<GraphDisplayData, DisplayGraphIndex>();

function displayGraphIndex(data: GraphDisplayData): DisplayGraphIndex {
  const cached = displayGraphIndexCache.get(data);
  if (
    cached
    && cached.nodeSource === data.nodes
    && cached.edgeSource === data.edges
    && cached.nodeCount === data.nodes.length
    && cached.edgeCount === data.edges.length
  ) {
    return cached;
  }
  const index: DisplayGraphIndex = {
    nodeSource: data.nodes,
    edgeSource: data.edges,
    nodeCount: data.nodes.length,
    edgeCount: data.edges.length,
    validNodeIds: new Set(data.nodes.map((node) => node.id)),
    edgeIndex: buildEdgeIndex(data.edges),
  };
  displayGraphIndexCache.set(data, index);
  return index;
}

/**
 * Return a deterministic n-degree neighborhood for Kumu-style focus. The edge
 * index keeps this linear in the visible graph instead of rescanning every edge
 * for every selected root.
 */
export function graphNeighborhood(
  data: GraphDisplayData,
  seedIds: string[],
  degree: number,
): { nodeIds: string[]; edgeIds: string[] } {
  const { validNodeIds, edgeIndex } = displayGraphIndex(data);
  const includedNodes = new Set(
    seedIds.filter((nodeId) => validNodeIds.has(nodeId)),
  );
  const includedEdges = new Set<string>();
  let frontier = [...includedNodes];
  const maxDegree = Math.max(0, Math.floor(Number.isFinite(degree) ? degree : 0));

  for (let step = 0; step < maxDegree && frontier.length; step += 1) {
    const nextFrontier = new Set<string>();
    for (const nodeId of frontier) {
      for (const relation of edgeIndex.get(nodeId) || []) {
        includedEdges.add(relation.edge.id);
        if (!includedNodes.has(relation.otherId)) {
          includedNodes.add(relation.otherId);
          nextFrontier.add(relation.otherId);
        }
      }
    }
    frontier = [...nextFrontier];
  }

  return {
    nodeIds: data.nodes
      .filter((node) => includedNodes.has(node.id))
      .map((node) => node.id),
    edgeIds: data.edges
      .filter((edge) => includedEdges.has(edge.id))
      .map((edge) => edge.id),
  };
}

interface CompiledDisclosureFilters {
  reportIds?: Set<string>;
  frameworks?: Set<string>;
  scopes?: Set<string>;
  years?: Set<string>;
  topics?: Set<string>;
  statuses?: Set<string>;
}

function compileDisclosureFilters(filters: DisclosureGraphFilters): CompiledDisclosureFilters {
  const selected = (values: string[]) => values.length ? new Set(values) : undefined;
  return {
    reportIds: selected(filters.reportIds),
    frameworks: selected(filters.frameworks),
    scopes: selected(filters.scopes),
    years: selected(filters.years),
    topics: selected(filters.topics),
    statuses: selected(filters.statuses),
  };
}

function matchesOne(value: string, selected?: Set<string>): boolean {
  return !selected || selected.has(value);
}

function matchesDisclosureFilters(
  disclosure: DisclosureGraphNode,
  report: DisclosureGraphNode | undefined,
  metric: DisclosureGraphNode | undefined,
  filters: CompiledDisclosureFilters,
): boolean {
  if (!report || !metric) return false;
  const reportProperties = report.properties;
  const metricProperties = metric.properties;
  const disclosureProperties = disclosure.properties;
  const framework =
    propertyString(disclosureProperties, "framework") ||
    propertyString(metricProperties, "framework") ||
    propertyString(reportProperties, "framework");
  const scope =
    propertyString(disclosureProperties, "scope_key", "scope") ||
    propertyString(metricProperties, "scope_key", "scope") ||
    propertyString(reportProperties, "scope_key", "scope");
  const year =
    propertyString(disclosureProperties, "report_year", "year") ||
    propertyString(reportProperties, "report_year", "year");
  const topic =
    propertyString(metricProperties, "topic", "category", "dimension") ||
    propertyString(disclosureProperties, "topic", "category", "dimension");

  return (
    matchesOne(reportId(report), filters.reportIds) &&
    matchesOne(framework, filters.frameworks) &&
    matchesOne(scope, filters.scopes) &&
    matchesOne(year, filters.years) &&
    matchesOne(topic, filters.topics) &&
    matchesOne(disclosureStatus(disclosure), filters.statuses)
  );
}

const KUMU_CURVATURES: Record<number, number[]> = {
  1: [0],
  2: [-0.7, 0.7],
  3: [-0.7, 0, 0.7],
  4: [-0.7, -0.2, 0.2, 0.7],
  5: [-0.7, -0.3, 0, 0.3, 0.7],
  6: [-0.7, -0.4, -0.15, 0.15, 0.4, 0.7],
  7: [-0.7, -0.5, -0.25, 0, 0.25, 0.5, 0.7],
  8: [-0.7, -0.5, -0.3, -0.1, 0.1, 0.3, 0.5, 0.7],
};

// These are the concise code-level titles used by the supplied Kumu Hardware
// map. Other standards continue to use explicit family metadata or a safe
// topic/name fallback, so the projection remains framework agnostic.
const KUMU_METRIC_FAMILY_TITLES: Record<string, string> = {
  "TC-HW-000.A": "Units produced by product category",
  "TC-HW-000.B": "Area of manufacturing facilities",
  "TC-HW-000.C": "Production from owned facilities",
  "TC-HW-230A.1": "Product data security risk management",
  "TC-HW-330A.1": "Employee diversity representation",
  "TC-HW-410A.1": "IEC 62474 declarable substances",
  "TC-HW-410A.2": "EPEAT registration",
  "TC-HW-410A.3": "Energy efficiency certification",
  "TC-HW-410A.4": "End-of-life products & e-waste",
  "TC-HW-430A.1": "Tier 1 supplier facility audits",
  "TC-HW-430A.2": "Supplier non-conformance & corrective actions",
  "TC-HW-440A.1": "Critical materials risk management",
};

function normalizedMetricCodeKey(code: string): string {
  return code.replace(/\s+/g, "").toLocaleUpperCase();
}

function metricFamilyTitle(
  code: string,
  metrics: DisclosureGraphNode[],
  useTopicFallback = true,
): string {
  const referenceTitle = KUMU_METRIC_FAMILY_TITLES[normalizedMetricCodeKey(code)];
  if (referenceTitle) return referenceTitle;
  for (const metric of metrics) {
    const explicit = propertyString(
      metric.properties,
      "family_label",
      "metric_family_label",
      "code_label",
      "graph_label",
    ).trim();
    if (!explicit) continue;
    const withoutCode = explicit.replace(code, "").replace(/^\s*[—–-]\s*/, "").trim();
    return withoutCode || explicit;
  }
  if (!useTopicFallback) return "";
  return propertyString(metrics[0]?.properties || {}, "topic", "category").trim()
    || String(metrics[0]?.label || "").trim();
}

// These are the only curvature selectors defined by the supplied Kumu theme
// and understood by DisclosureGraphCanvas. Larger metric families must reuse
// an available selector rather than emitting an inert tag such as curve-n52.
const SUPPORTED_KUMU_CURVATURES = [
  -0.7,
  -0.6,
  -0.5,
  -0.4,
  -0.3,
  -0.25,
  -0.2,
  -0.15,
  -0.1,
  0,
  0.1,
  0.15,
  0.2,
  0.25,
  0.3,
  0.4,
  0.5,
  0.6,
  0.7,
] as const;

function metricSort(left: DisclosureGraphNode, right: DisclosureGraphNode): number {
  return left.label.localeCompare(right.label, "en", {
    numeric: true,
    sensitivity: "base",
  }) || left.id.localeCompare(right.id);
}

function curvatureForMetric(index: number, count: number): number {
  const configured = KUMU_CURVATURES[count];
  if (configured) return configured[index] ?? 0;
  if (count <= 1) return 0;
  const boundedIndex = Math.max(0, Math.min(count - 1, index));
  const selectorIndex = Math.round(
    ((SUPPORTED_KUMU_CURVATURES.length - 1) * boundedIndex) / (count - 1),
  );
  return SUPPORTED_KUMU_CURVATURES[selectorIndex] ?? 0;
}

function curvatureTag(curvature: number): string {
  if (Math.abs(curvature) < 0.005) return "curve-0";
  const direction = curvature < 0 ? "n" : "p";
  return `curve-${direction}${Math.round(Math.abs(curvature) * 100)}`;
}

function withKumuCurveTag(
  properties: Record<string, unknown>,
  tag: string,
): Record<string, unknown> {
  const rawTags = properties.tags;
  const tags = Array.isArray(rawTags)
    ? rawTags.map(String)
    : String(rawTags || "")
      .split(/[\s,|]+/)
      .filter(Boolean);
  if (tags.some((item) => /^curve-(?:0|[np]\d+)$/i.test(item))) {
    return properties;
  }
  return { ...properties, tags: [...tags, tag] };
}

function metricDisplayLabel(
  node: DisclosureGraphNode,
  useFamilyTitle = true,
): string {
  const code = metricCode(node).trim();
  const label = String(node.label || "").trim();
  const familyTitle = useFamilyTitle ? metricFamilyTitle(code, [node], false) : "";
  if (code && familyTitle && familyTitle !== label) {
    return `${code} — ${familyTitle}`;
  }
  if (!code || !label || label.toLocaleLowerCase().includes(code.toLocaleLowerCase())) {
    return label || code;
  }
  return `${code} — ${label}`;
}

interface IndexedDisclosureRelation {
  disclosure: DisclosureGraphNode;
  report?: DisclosureGraphNode;
  metric?: DisclosureGraphNode;
  evidence: Array<{ node: DisclosureGraphNode; relationType: string }>;
}

interface GraphProjectionIndex {
  nodeSource: DisclosureGraphNode[];
  edgeSource: DisclosureGraphEdge[];
  nodeCount: number;
  edgeCount: number;
  disclosureRelations: IndexedDisclosureRelation[];
  canonicalMetricFamilies: Map<string, DisclosureGraphNode[]>;
  metricCurveTags: Map<string, string>;
}

// Graph responses are treated as immutable throughout the page (`setGraph`
// replaces them after loading/expansion). Cache only topology-derived data so
// filter, search and display-mode changes do not rebuild the O(N + E) index.
const projectionIndexCache = new WeakMap<DisclosureGraphResponse, GraphProjectionIndex>();

function graphProjectionIndex(graph: DisclosureGraphResponse): GraphProjectionIndex {
  const cached = projectionIndexCache.get(graph);
  if (
    cached
    && cached.nodeSource === graph.nodes
    && cached.edgeSource === graph.edges
    && cached.nodeCount === graph.nodes.length
    && cached.edgeCount === graph.edges.length
  ) {
    return cached;
  }

  const nodesById = new Map<string, DisclosureGraphNode>();
  const nodeTypesById = new Map<string, ReturnType<typeof normalizeNodeType>>();
  const disclosures: DisclosureGraphNode[] = [];
  for (const node of graph.nodes) {
    const nodeType = normalizeNodeType(node.type);
    nodesById.set(node.id, node);
    nodeTypesById.set(node.id, nodeType);
    if (nodeType === "disclosure") disclosures.push(node);
  }

  const edgeIndex = buildEdgeIndex(graph.edges);
  const canonicalFamilyMembers = new Map<string, Map<string, DisclosureGraphNode>>();
  const disclosureRelations: IndexedDisclosureRelation[] = [];

  for (const disclosure of disclosures) {
    let report: DisclosureGraphNode | undefined;
    let metric: DisclosureGraphNode | undefined;
    const evidence: IndexedDisclosureRelation["evidence"] = [];
    for (const { edge, otherId } of edgeIndex.get(disclosure.id) || []) {
      const node = nodesById.get(otherId);
      if (!node) continue;
      const nodeType = nodeTypesById.get(otherId);
      if (nodeType === "report" && !report) report = node;
      else if (nodeType === "metric" && !metric) metric = node;
      else if (nodeType === "evidence") evidence.push({ node, relationType: edge.type });
    }
    disclosureRelations.push({ disclosure, report, metric, evidence });
    if (report && metric) {
      const code = metricCode(metric);
      const family = canonicalFamilyMembers.get(code) ?? new Map<string, DisclosureGraphNode>();
      family.set(metric.id, metric);
      canonicalFamilyMembers.set(code, family);
    }
  }

  const canonicalMetricFamilies = new Map<string, DisclosureGraphNode[]>();
  const metricCurveTags = new Map<string, string>();
  for (const [code, family] of canonicalFamilyMembers) {
    const metrics = [...family.values()].sort(metricSort);
    canonicalMetricFamilies.set(code, metrics);
    metrics.forEach((metric, index) => {
      metricCurveTags.set(
        metric.id,
        curvatureTag(curvatureForMetric(index, metrics.length)),
      );
    });
  }

  const index: GraphProjectionIndex = {
    nodeSource: graph.nodes,
    edgeSource: graph.edges,
    nodeCount: graph.nodes.length,
    edgeCount: graph.edges.length,
    disclosureRelations,
    canonicalMetricFamilies,
    metricCurveTags,
  };
  projectionIndexCache.set(graph, index);
  return index;
}

/**
 * Produces the visual graph without mutating the canonical response. In overview
 * mode each Disclosure is projected to one report-to-metric edge, while the
 * original disclosure node remains attached to that edge for details.
 */
export function deriveGraphDisplayData(
  graph: DisclosureGraphResponse,
  filters: DisclosureGraphFilters,
  mode: GraphDisplayMode,
): GraphDisplayData {
  const {
    disclosureRelations,
    canonicalMetricFamilies,
    metricCurveTags,
  } = graphProjectionIndex(graph);
  const compiledFilters = compileDisclosureFilters(filters);
  const includedRelations = disclosureRelations.filter(({ disclosure, report, metric }) =>
    matchesDisclosureFilters(disclosure, report, metric, compiledFilters));

  const visibleNodeIds = new Set<string>();
  const displayEdges: GraphDisplayData["edges"] = [];
  const collapsedMetricCodes = new Set(filters.collapsedMetricCodes);
  const metricGroups = new Map<
    string,
    {
      id: string;
      metrics: DisclosureGraphNode[];
      metricIds: Set<string>;
      disclosures: DisclosureGraphNode[];
    }
  >();

  // Visible groups control rendering, while the cached canonical families
  // control stable curve assignment independently of active filters.
  for (const relation of includedRelations) {
    const { disclosure, report: relationReport, metric: relationMetric } = relation;
    const report = relationReport!;
    const metric = relationMetric!;
    const code = metricCode(metric);
    const group = metricGroups.get(code) ?? {
      id: `metric-group:${encodeURIComponent(code)}`,
      metrics: [],
      metricIds: new Set<string>(),
      disclosures: [],
    };
    if (!group.metricIds.has(metric.id)) {
      group.metricIds.add(metric.id);
      group.metrics.push(metric);
    }
    group.disclosures.push(disclosure);
    metricGroups.set(code, group);
    const curveTag = metricCurveTags.get(metric.id) || "curve-0";
    let metricTargetId = metric.id;

    if (collapsedMetricCodes.has(code)) {
      metricTargetId = group.id;
    } else {
      visibleNodeIds.add(metric.id);
    }

    visibleNodeIds.add(report.id);
    if (mode === "overview") {
      displayEdges.push({
        id: `projected:${disclosure.id}`,
        type: "disclosure",
        source: report.id,
        target: metricTargetId,
        label: disclosureStatus(disclosure),
        properties: withKumuCurveTag(
          { ...disclosure.properties, disclosure_id: disclosure.id },
          curveTag,
        ),
        disclosure_id: disclosure.id,
        disclosure,
      });
    } else {
      visibleNodeIds.add(disclosure.id);
      displayEdges.push(
        {
          id: `has-disclosure:${disclosure.id}`,
          type: "has_disclosure",
          source: report.id,
          target: disclosure.id,
          properties: { disclosure_id: disclosure.id },
          disclosure_id: disclosure.id,
          disclosure,
        },
        {
          id: `assesses:${disclosure.id}`,
          type: "assesses",
          source: disclosure.id,
          target: metricTargetId,
          properties: withKumuCurveTag(
            { ...disclosure.properties, disclosure_id: disclosure.id },
            curveTag,
          ),
          disclosure_id: disclosure.id,
          disclosure,
        },
      );
      for (const evidenceRelation of relation.evidence) {
        const evidence = evidenceRelation.node;
        visibleNodeIds.add(evidence.id);
        const relationType = evidenceRelation.relationType === "candidate_evidence"
          ? "candidate_evidence"
          : "supported_by";
        displayEdges.push({
          id: `${relationType}:${disclosure.id}:${evidence.id}`,
          type: relationType,
          source: disclosure.id,
          target: evidence.id,
          properties: {
            disclosure_id: disclosure.id,
            evidence_role: relationType === "candidate_evidence" ? "candidate" : "supporting",
          },
          disclosure_id: disclosure.id,
          disclosure,
        });
      }
    }
  }

  const displayNodes: GraphDisplayData["nodes"] = graph.nodes
    .filter((node) => visibleNodeIds.has(node.id))
    .map((node) => {
      const isMetric = normalizeNodeType(node.type) === "metric";
      return {
        ...node,
        short_label: isMetric ? metricCode(node) : node.label,
        display_label: isMetric
          ? metricDisplayLabel(
              node,
              (canonicalMetricFamilies.get(metricCode(node))?.length || 1) === 1,
            )
          : node.label,
      };
    });

  for (const [code, group] of metricGroups) {
    const collapsed = collapsedMetricCodes.has(code);
    // The code node is a visual projection only. When a family is expanded,
    // injecting an extra hub and member edges changes both cardinality and the
    // Force topology, which is why the old graph did not resemble Kumu.
    if (!collapsed) continue;
    const topic = propertyString(group.metrics[0]?.properties, "topic", "category");
    const familyTitle = metricFamilyTitle(code, group.metrics);
    const groupLabel = familyTitle ? `${code} — ${familyTitle}` : code;
    const metricNames = group.metrics.map((metric) => metric.label).filter(Boolean);
    const description = [
      topic ? `Topic: ${topic}` : "",
      metricNames.length > 1
        ? `Sub-items:\n${metricNames.map((name) => `- ${name}`).join("\n")}`
        : "",
    ].filter(Boolean).join("\n");
    displayNodes.push({
      id: group.id,
      type: "metric",
      label: groupLabel,
      short_label: code,
      display_label: groupLabel,
      group_id: code,
      synthetic: true,
      properties: {
        metric_code: code,
        name: familyTitle,
        family_label: familyTitle,
        description,
        metric_names: metricNames,
        search_terms: metricNames,
        topic,
        metric_count: group.metrics.length,
        metric_ids: group.metrics.map((metric) => metric.id),
        disclosure_count: group.disclosures.length,
        collapsed,
      },
    });
    visibleNodeIds.add(group.id);
  }

  return {
    nodes: displayNodes,
    edges: displayEdges.filter(
      (edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
    ),
    underlyingDisclosureCount: includedRelations.length,
  };
}

export function mergeDisclosureGraphs(
  graphs: DisclosureGraphResponse[],
  ownerLabel = "Selected reports",
): DisclosureGraphResponse {
  const nodes = new Map<string, DisclosureGraphNode>();
  const edges = new Map<string, DisclosureGraphEdge>();

  for (const graph of graphs) {
    for (const node of graph.nodes) {
      const existing = nodes.get(node.id);
      if (!existing) {
        nodes.set(node.id, node);
      } else if (normalizeNodeType(node.type) === "metric") {
        nodes.set(node.id, {
          ...existing,
          ...node,
          properties: { ...existing.properties, ...node.properties },
        });
      }
    }
    for (const edge of graph.edges) {
      let id = edge.id;
      let suffix = 2;
      while (edges.has(id)) {
        const existing = edges.get(id)!;
        if (
          existing.source === edge.source &&
          existing.target === edge.target &&
          existing.type === edge.type
        ) {
          id = "";
          break;
        }
        id = `${edge.id}:${suffix++}`;
      }
      if (id) edges.set(id, id === edge.id ? edge : { ...edge, id });
    }
  }

  const nodeList = [...nodes.values()];
  const edgeList = [...edges.values()];
  const countTypes = (values: Array<{ type: string }>) =>
    values.reduce<Record<string, number>>((accumulator, item) => {
      accumulator[item.type] = (accumulator[item.type] || 0) + 1;
      return accumulator;
    }, {});
  return {
    schema_version: graphs[0]?.schema_version || "1.0",
    graph_id: `reports:${graphs.map((graph) => graph.graph_id || graph.owner?.id || "unknown").join(",")}`,
    graph_revision: graphs.map((graph) => graph.graph_revision).join("|"),
    owner: { type: "reports", id: "selected", label: ownerLabel },
    scope_key: graphs.every((graph) => graph.scope_key === graphs[0]?.scope_key)
      ? graphs[0]?.scope_key
      : null,
    framework: graphs.every((graph) => graph.framework === graphs[0]?.framework)
      ? graphs[0]?.framework
      : null,
    nodes: nodeList,
    edges: edgeList,
    stats: {
      node_count: nodeList.length,
      edge_count: edgeList.length,
      node_types: countTypes(nodeList),
      edge_types: countTypes(edgeList),
    },
    truncated: graphs.some((graph) => graph.truncated),
  };
}

export function mergeGraphExpansion(
  base: DisclosureGraphResponse,
  expansion: DisclosureGraphResponse,
): DisclosureGraphResponse {
  const merged = mergeDisclosureGraphs([base, expansion], base.owner?.label || "Graph");
  return {
    ...merged,
    graph_id: base.graph_id,
    graph_revision: base.graph_revision,
    owner: base.owner,
    scope_key: base.scope_key,
    framework: base.framework,
  };
}

export function parseStoredGraphPositions(
  raw: string | null,
  validNodeIds: Iterable<string>,
): StoredGraphPositions {
  const valid = new Set(validNodeIds);
  try {
    const parsed = JSON.parse(raw || "") as Partial<StoredGraphPositions>;
    const positions: StoredGraphPositions["positions"] = {};
    if (parsed && typeof parsed.positions === "object" && parsed.positions) {
      for (const [id, position] of Object.entries(parsed.positions)) {
        const x = Number((position as { x?: unknown }).x);
        const y = Number((position as { y?: unknown }).y);
        if (valid.has(id) && Number.isFinite(x) && Number.isFinite(y)) {
          positions[id] = { x, y };
        }
      }
    }
    return { revision: String(parsed?.revision || ""), positions };
  } catch {
    return { revision: "", positions: {} };
  }
}

export function graphFilterOptions(graph: DisclosureGraphResponse) {
  const frameworks = new Set<string>();
  const scopes = new Set<string>();
  const years = new Set<string>();
  const topics = new Set<string>();
  const statuses = new Set<string>();
  const metricCodeCounts = new Map<string, number>();
  const add = (values: Set<string>, value: string) => {
    if (value) values.add(value);
  };

  add(frameworks, graph.framework || "");
  add(scopes, graph.scope_key || "");
  for (const node of graph.nodes) {
    add(frameworks, propertyString(node.properties, "framework"));
    add(scopes, propertyString(node.properties, "scope_key", "scope"));
    const nodeType = normalizeNodeType(node.type);
    if (nodeType === "report") {
      add(years, propertyString(node.properties, "report_year", "year"));
    } else if (nodeType === "metric") {
      add(topics, propertyString(node.properties, "topic", "category", "dimension"));
      const code = metricCode(node);
      if (code) metricCodeCounts.set(code, (metricCodeCounts.get(code) || 0) + 1);
    } else if (nodeType === "disclosure") {
      add(statuses, disclosureStatus(node));
    }
  }

  const alphabetical = (values: Set<string>) =>
    [...values].sort((left, right) => left.localeCompare(right));
  const orderedMetricCounts = [...metricCodeCounts.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([code, count]) => ({ code, count }));
  return {
    frameworks: alphabetical(frameworks),
    scopes: alphabetical(scopes),
    years: [...years].sort(
      (a, b) => Number(b) - Number(a),
    ),
    topics: alphabetical(topics),
    statuses: alphabetical(statuses),
    metricCodes: orderedMetricCounts.map(({ code }) => code),
    metricCodeCounts: orderedMetricCounts,
  };
}
