from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, Field, conlist


class CrossAnalysisReport(BaseModel):
    file_id: str
    display_name: str
    short_name: str
    report_year: Optional[int] = None
    confidence: float = Field(ge=0.0, le=1.0)
    filename: str
    has_assessment: bool
    framework: Optional[str] = None
    industry: Optional[str] = None
    semi_industry: Optional[str] = None
    gri_sector: Optional[str] = None
    gri_topic: Optional[str] = None


class CrossReportsResponse(BaseModel):
    reports: List[CrossAnalysisReport]


class EvidenceRef(BaseModel):
    page: Optional[int] = None
    position_y: Optional[float] = None
    snippet: str
    segment_id: Optional[str] = None
    reason: Optional[str] = None


class ExtractedMetric(BaseModel):
    name: str
    value: Optional[float] = None
    unit: Optional[str] = None
    year: Optional[str] = None
    scope: Optional[str] = None
    meaning: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence: Optional[EvidenceRef] = None


class CrossCompareReport(BaseModel):
    file_id: str
    display_name: str
    short_name: str
    report_year: Optional[int] = None
    status: Literal["ok", "no_structured_metrics", "error"] = "ok"
    reason: Optional[str] = None
    metrics: List[ExtractedMetric] = Field(default_factory=list)
    summary: List[str] = Field(default_factory=list)
    evidence: List[EvidenceRef] = Field(default_factory=list)


class CrossTopicLabels(BaseModel):
    """UI-provided labels for the current navigation selection.

    Optional but helps:
    - Make extracted metric names human readable
    - Provide context for LLM extraction (when enabled)
    """

    dimension_zh: Optional[str] = None
    dimension_en: Optional[str] = None
    issue_zh: Optional[str] = None
    issue_en: Optional[str] = None
    metric_zh: Optional[str] = None
    metric_en: Optional[str] = None


class CrossCompareRequest(BaseModel):
    file_ids: conlist(str, min_length=2)
    topic_key: str = Field(min_length=3)
    query_pack: List[str] = Field(default_factory=list)

    # Optional labels from UI. Backward compatible: callers may omit.
    labels: Optional[CrossTopicLabels] = None
    # NOTE: Cross Analysis needs higher recall and richer evidence lists.
    # top_n_candidates controls the recall pool before rerank.
    top_n_candidates: int = Field(default=320, ge=80, le=1200)
    # Evidence snippets shown to users; allow richer drilldown.
    top_k_evidence: int = Field(default=8, ge=3, le=20)
    align_intensity: bool = True
    align_year: bool = True


class CrossCompareResponse(BaseModel):
    topic_key: str
    reports: List[CrossCompareReport]
    generated_at: str


# ------------------------------
# Cross Analysis Records (issue-level tables)
# ------------------------------


class CrossExtractedRecord(BaseModel):
    """A single comparable disclosure record.

    One record = one (detail, year) datapoint (or one text disclosure when year is not required).
    """

    id: str  # 报告 id (file_id)
    name: str  # 报告简称（公司 + 年份）
    topic: str  # 一级导航（显示用）
    type: str  # 二级导航（显示用）

    # 指标主名称（例如：Scope 1强度、TRIR、召回次数）
    label: str
    # 标签细节/口径（例如：location-based、market-based、operational control）。无则为 null。
    detail: Optional[str] = None
    page: Optional[int] = None
    data: Optional[str] = None
    year: Optional[str] = None
    unit: Optional[str] = None
    context: Optional[str] = None


class CrossRecordsRequest(BaseModel):
    file_ids: conlist(str, min_length=2)
    # dimension key: environment / social_capital / human_capital / leadership_governance / business_model_innovation
    topic_key: str = Field(min_length=3)
    # selected issue keys (multi-select). If empty, backend may default to all issues under the topic.
    issue_keys: List[str] = Field(default_factory=list)

    # retrieval controls
    top_n_candidates: int = Field(default=420, ge=120, le=1400)
    top_k_evidence: int = Field(default=12, ge=5, le=30)

    # whether to write JSON outputs into /uploads/outputs/cross_analysis/output
    persist_output: bool = True


class CrossRecordsResponse(BaseModel):
    topic_key: str
    issue_keys: List[str]
    records: List[CrossExtractedRecord]
    generated_at: str


# ------------------------------
# Cross Analysis Disclosed Cache (assessment-driven)
# ------------------------------


class CrossDisclosedRecord(BaseModel):
    """A lightweight record built from per-report assessment outputs.

    This schema matches the frontend's normalized CrossExtractedRecord shape
    (primary_navigation / secondary_navigation / topic / sub_topic / data / page / year / unit / detail).
    """

    id: str  # file_id
    name: str  # display label (prefer filename stem)

    primary_navigation: str
    secondary_navigation: str

    topic: str
    sub_topic: str = ""

    category: Optional[str] = None  # Quantitative / Qualitative; charts only for Quantitative

    page: Optional[int] = None
    # Extracted value (string). UI will parse numbers when drawing charts.
    # NOTE: Historically this field was named `data` in the v2 schema.
    # We now also expose `value` for clarity/compat with assessment outputs.
    data: str = ""
    value: str = ""
    year: Optional[str] = None
    unit: Optional[str] = None
    detail: str = ""

    # Optional fields for debugging / future UX
    disclosure_status: Optional[str] = None
    metric_id: Optional[str] = None

    # Keep `data` and `value` in sync both ways.
    def model_post_init(self, __context):
        try:
            if self.value and not self.data:
                object.__setattr__(self, "data", self.value)
            elif self.data and not self.value:
                object.__setattr__(self, "value", self.data)
        except Exception:
            pass


class CrossDisclosedCacheResponse(BaseModel):
    cache_key: str
    file_ids: List[str]
    from_cache: bool
    generated_at: str
    records: List[CrossDisclosedRecord]


# ------------------------------
# Excel Metrics Extraction (catalog-driven)
# ------------------------------


class ExcelMetricsRequest(BaseModel):
    """Extract ESG metrics based on an Excel catalog (Primary/Secondary/Topic/Sub-topic)."""

    file_ids: conlist(str, min_length=1)
    # Optional override path on server; otherwise uses ESG_METRICS_CATALOG_PATH or package default.
    catalog_path: Optional[str] = None

    # retrieval controls
    top_n_candidates: int = Field(default=420, ge=120, le=2000)
    persist_output: bool = True


class ExcelMetricsResponse(BaseModel):
    records: List[dict]
    generated_at: str
