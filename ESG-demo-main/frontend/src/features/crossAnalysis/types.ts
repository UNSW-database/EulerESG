// src/features/crossAnalysis/types.ts

export type EvidenceRef = {
  page: number | null;
  position_y?: number | null;
  snippet: string;
  segment_id?: string;
  reason?: string;
};

export type ExtractedMetric = {
  name: string;
  value: number | null;
  unit: string | null;
  year: string | null;
  scope: string | null;
  meaning?: string | null;
  confidence: number;
  evidence?: EvidenceRef;
};

export type CrossReportSummary = {
  file_id: string;
  display_name: string;
  short_name: string;
  report_year?: number | null;
  confidence: number;
  filename: string;
  has_assessment: boolean;
  framework?: string | null;
  industry?: string | null;
  semi_industry?: string | null;
};

export type CrossCompareReport = {
  file_id: string;
  display_name: string;
  short_name: string;
  report_year?: number | null;
  status: "ok" | "no_structured_metrics" | "error";
  reason?: string;
  metrics: ExtractedMetric[];
  summary: string[];
  evidence: EvidenceRef[];
};

export type CrossCompareResponse = {
  topic_key: string;
  reports: CrossCompareReport[];
  generated_at: string;
};

// ------------------------------
// Cross Analysis Records (issue-level tables)
// ------------------------------

export type CrossExtractedRecord = {
  // ------------------------------
  // Normalized schema (requested)
  // ------------------------------
  id: string; // 报告 id (file_id)
  name: string; // 公司/报告名称

  // 一级/二级导航
  primary_navigation: string; // Primary Navigation
  secondary_navigation: string; // Secondary Navigation

  // 指标名称与细分
  topic: string; // Topic (例如 Scope 1 / Scope 2 ...)
  sub_topic: string; // Sub-topic (例如 location-based / market-based ...)

  // 证据与数值
  page: number | null; // 报告页码
  data: string; // 具体数据（文本形式，前端解析数值）
  year: string | null;
  unit: string | null;
  category?: string | null; // Quantitative | Qualitative; charts only for Quantitative

  // 对数据的具体解读（不再使用 context）
  detail: string;

  // ------------------------------
  // Legacy fields (backend may still return)
  // ------------------------------
  // (Do not render in UI; used only for normalization.)
  type?: string;
  label?: string | null;
  context?: string | null;
};

export type CrossRecordsResponse = {
  topic_key: string;
  issue_keys: string[];
  records: CrossExtractedRecord[];
  generated_at: string;
};


export type AllRecordsRowRaw = Record<string, unknown> & {
  id?: string;
  file_id?: string;
  report_id?: string;
  name?: string;
  display_name?: string;
  short_name?: string;
  primary_navigation?: string;
  secondary_navigation?: string;
  topic?: string;
  sub_topic?: string;
  page?: number | string | null;
  data?: string | number | null;
  value?: string | number | null;
  year?: string | number | null;
  unit?: string | null;
  detail?: string | null;
};

export type AllRecord = {
  id: string;
  name: string;
  primaryNavigation: string;
  secondaryNavigation: string;
  topic: string;
  subTopic: string;
  page: number | null;
  data: string;
  year: string | null;
  unit: string | null;
  detail: string;
};
