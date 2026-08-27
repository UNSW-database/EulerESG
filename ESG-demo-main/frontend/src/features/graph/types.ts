export type DisclosureGraphNodeType =
  | "report"
  | "metric"
  | "disclosure"
  | "evidence"
  | string;

export interface DisclosureGraphNode {
  id: string;
  kind?: DisclosureGraphNodeType;
  type: DisclosureGraphNodeType;
  label: string;
  group_id?: string | null;
  properties: Record<string, unknown>;
}

export interface DisclosureGraphEdge {
  id: string;
  kind?: string;
  type: string;
  source: string;
  target: string;
  label?: string | null;
  properties: Record<string, unknown>;
}

export interface DisclosureGraphStats {
  node_count: number;
  edge_count: number;
  node_types?: Record<string, number>;
  edge_types?: Record<string, number>;
  [key: string]: unknown;
}

export interface DisclosureGraphResponse {
  schema_version: string;
  graph_id?: string;
  graph_revision: string;
  owner?: {
    type: string;
    id: string;
    label?: string | null;
  };
  scope_key?: string | null;
  framework?: string | null;
  nodes: DisclosureGraphNode[];
  edges: DisclosureGraphEdge[];
  stats: DisclosureGraphStats;
  truncated: boolean;
}

export interface DisclosureGraphQuery {
  scope?: string;
  includeEvidence?: boolean;
  evidenceLimit?: number;
  reportIds?: string[];
  signal?: AbortSignal;
}

export interface DisclosureGraphNeighborsQuery {
  nodeId: string;
  scope?: string;
  reportIds?: string[];
  depth?: number;
  evidenceLimit?: number;
  signal?: AbortSignal;
}

export type GraphLayoutName = "force" | "hierarchical" | "radial";
export type GraphDisplayMode = "overview" | "expanded";

export interface DisclosureGraphFilters {
  reportIds: string[];
  frameworks: string[];
  scopes: string[];
  years: string[];
  topics: string[];
  statuses: string[];
  collapsedMetricCodes: string[];
}

export interface GraphPosition {
  x: number;
  y: number;
}

export interface StoredGraphPositions {
  revision: string;
  positions: Record<string, GraphPosition>;
}

export interface GraphDisplayNode extends DisclosureGraphNode {
  display_label?: string;
  short_label?: string;
  synthetic?: boolean;
}

export interface GraphDisplayEdge extends DisclosureGraphEdge {
  disclosure_id?: string;
  disclosure?: DisclosureGraphNode;
}

export interface GraphDisplayData {
  nodes: GraphDisplayNode[];
  edges: GraphDisplayEdge[];
  underlyingDisclosureCount: number;
}
