"""
Simplified data models for ESG report encoding
"""

from typing import Any, Dict, List, Optional, Tuple, Union

from .embedding_settings import get_configured_embedding_model_name
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


def table_row_scope_key(
    segment: Any,
    table_id: Any = None,
    row_index: Any = None,
) -> Optional[Tuple[str, int]]:
    """Return a page-scoped logical row key for table evidence.

    Paddle can keep one logical ``table_id`` across continuation pages while
    restarting ``row_index`` on every page. The page and source report must be
    part of the lookup scope, otherwise a cell on page N can be joined to the
    same row number on page N+1.
    """
    data = getattr(segment, "structured_data", None)
    data = data if isinstance(data, dict) else {}
    resolved_table_id = (
        table_id
        or getattr(segment, "source_table_id", None)
        or data.get("table_id")
        or data.get("source_table_id")
    )
    resolved_row_index = (
        row_index
        if row_index is not None
        else data.get("row_index", data.get("row_idx"))
    )
    if resolved_table_id is None or resolved_row_index is None:
        return None
    try:
        normalized_row_index = int(resolved_row_index)
    except (TypeError, ValueError):
        return None

    source_report_id = str(
        getattr(segment, "source_report_id", None)
        or data.get("source_report_id")
        or ""
    ).strip()
    raw_page_number = (
        getattr(segment, "page_number", None)
        or data.get("page_number")
        or data.get("page")
        or 0
    )
    try:
        page_number = int(raw_page_number)
    except (TypeError, ValueError):
        page_number = 0
    scoped_table_id = "\x1f".join(
        (source_report_id, str(resolved_table_id), f"p{page_number}")
    )
    return scoped_table_id, normalized_row_index


class MetricCategory(str, Enum):
    """Metric category enumeration"""
    ENVIRONMENTAL = "environmental"
    SOCIAL = "social"
    GOVERNANCE = "governance"
    GENERAL = "general"


class MetricSource(str, Enum):
    """Metric source enumeration"""
    GRI = "gri"
    SASB = "sasb"
    CDP = "cdp"
    TCFD = "tcfd"
    UNGC = "ungc"
    CUSTOM = "custom"


class ESGMetric(BaseModel):
    """ESG metric model"""
    
    metric_id: str = Field(..., description="Metric unique identifier")
    metric_name: str = Field(..., description="Metric name")
    metric_code: str = Field(..., description="Metric code")
    category: MetricCategory = Field(..., description="Metric category")
    source: MetricSource = Field(..., description="Metric source")
    keywords: List[str] = Field(default_factory=list, description="Keywords list")
    description: str = Field(default="", description="Metric description")
    definition: str = Field(default="", description="Metric definition from framework data")
    unit: Optional[str] = Field(default=None, description="Metric unit")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")
    # Additional SASB fields for display
    sasb_category: str = Field(default="", description="Original SASB category (Quantitative/Qualitative)")
    sasb_type: str = Field(default="", description="Original SASB type")
    sasb_topic: Optional[str] = Field(default=None, description="Original SASB topic")


class SemanticExpansion(BaseModel):
    """Semantic expansion model"""
    
    metric_id: str = Field(..., description="Metric ID")
    semantic_description: str = Field(..., description="Semantic description")
    expanded_keywords: List[str] = Field(default_factory=list, description="Expanded keywords")
    context_information: str = Field(default="", description="Context information")
    embedding: Optional[List[float]] = Field(default=None, description="Semantic embedding vector")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")


class MetricCollection(BaseModel):
    """Metric collection model"""
    
    collection_id: str = Field(..., description="Collection ID")
    collection_name: str = Field(..., description="Collection name")
    metrics: List[ESGMetric] = Field(default_factory=list, description="Metrics list")
    semantic_expansions: List[SemanticExpansion] = Field(default_factory=list, description="Semantic expansions list")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")


class RetrievalResult(BaseModel):
    """Retrieval result model"""
    
    segment_id: str = Field(..., description="Segment ID")
    content: str = Field(..., description="Segment content")
    page_number: int = Field(..., description="Page number")
    score: float = Field(..., description="Relevance score")
    retrieval_type: str = Field(..., description="Retrieval type (keyword/semantic)")
    matched_keywords: List[str] = Field(default_factory=list, description="Matched keywords")
    metric_id: str = Field(..., description="Related metric ID")
    evidence_block_id: Optional[str] = Field(
        default=None,
        description="Stable complete evidence-block identifier used for retrieval grouping",
    )
    retrieval_view_id: Optional[str] = Field(
        default=None,
        description="Internal retrieval-view identifier that produced this hit",
    )
    source_segment_ids: List[str] = Field(
        default_factory=list,
        description="Canonical report segments belonging to the complete evidence block",
    )
    matched_content: Optional[str] = Field(
        default=None,
        description="Precise retrieval view text used for ranking and highlighting",
    )
    evidence_block_content: Optional[str] = Field(
        default=None,
        description="Untruncated complete paragraph, list, table, or visual evidence block",
    )
    matched_row_index: Optional[int] = Field(
        default=None,
        description="Matched table row when the retrieval view is row-scoped",
    )
    matched_column_indexes: List[int] = Field(
        default_factory=list,
        description="Matched table columns when a wide-row view is column-scoped",
    )
    score_breakdown: Dict[str, float] = Field(
        default_factory=dict,
        description="Optional per-stage retrieval scores for diagnostics",
    )
    link_source_page: Optional[int] = Field(default=None, description="Page containing an internal PDF link")
    link_target_page: Optional[int] = Field(default=None, description="Internal PDF page followed for this result")
    link_anchor_text: Optional[str] = Field(default=None, description="Visible PDF link anchor text")
    link_source_segment_id: Optional[str] = Field(
        default=None,
        description="Report segment containing the internal PDF link",
    )
    source_report_id: Optional[str] = Field(
        default=None,
        description="Source report identifier for company-level retrieval",
    )
    source_report_name: Optional[str] = Field(
        default=None,
        description="User-facing source report name",
    )
    source_report_year: Optional[int] = Field(
        default=None,
        description="Declared reporting year of the source report",
    )
    evidence_type: Optional[str] = Field(default=None, description="text/table/chart/figure evidence kind")
    asset_id: Optional[str] = Field(default=None, description="Stable visual asset identifier")
    asset_url: Optional[str] = Field(default=None, description="Authenticated visual asset API URL")
    bbox: Optional[List[float]] = Field(default=None, description="Normalized [x1,y1,x2,y2] PDF bounds")
    caption: Optional[str] = None
    confidence: Optional[float] = None
    chart_data: Optional[Dict[str, Any]] = None
    structure_confidence: Optional[float] = None
    ocr_confidence: Optional[float] = None
    header_path: List[str] = Field(default_factory=list)
    rowspan: int = 1
    colspan: int = 1
    parse_pass: int = 1
    review_status: Optional[str] = None
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)


class MetricRetrievalResult(BaseModel):
    """Metric retrieval result model"""
    
    metric_id: str = Field(..., description="Metric ID")
    metric_name: str = Field(..., description="Metric name")
    metric_code: str = Field(..., description="Metric code")
    keyword_results: List[RetrievalResult] = Field(default_factory=list, description="Keyword retrieval results")
    semantic_results: List[RetrievalResult] = Field(default_factory=list, description="Semantic retrieval results")
    combined_results: List[RetrievalResult] = Field(default_factory=list, description="Combined retrieval results")
    total_matches: int = Field(default=0, description="Total matches")
    qualified_total: int = Field(
        default=0,
        description="Unique candidates remaining after evidence qualification",
    )
    rerank_pool_k: int = Field(
        default=0,
        description="Number of candidates passed to the local reranker",
    )
    target_k: int = Field(
        default=0,
        description="Number of reranked candidates retained for final analysis",
    )


class TextSegment(BaseModel):
    """Text segment model"""
    
    segment_id: str = Field(..., description="Segment unique identifier")
    content: str = Field(..., description="Segment text content")
    page_number: int = Field(..., description="Page number")
    position_y: float = Field(..., description="Y coordinate position in page")
    segment_type: str = Field(default="text", description="Segment type (text/table/table_row/table_cell/ocr_text/chart/figure/image_text/chart_data)")
    position_x: Optional[float] = Field(default=None, description="X coordinate position in page")
    source_table_id: Optional[str] = Field(default=None, description="Related table ID if the segment comes from a table")
    row_header: Optional[str] = Field(default=None, description="Structured table row header")
    col_header: Optional[str] = Field(default=None, description="Structured table column header")
    value_text: Optional[str] = Field(default=None, description="Structured table cell value text")
    unit: Optional[str] = Field(default=None, description="Structured table unit")
    structure_confidence: Optional[float] = None
    ocr_confidence: Optional[float] = None
    header_path: List[str] = Field(default_factory=list)
    rowspan: int = 1
    colspan: int = 1
    parse_pass: int = 1
    review_status: Optional[str] = None
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    structured_data: Optional[Dict[str, Any]] = Field(default=None, description="Structured evidence payload")
    

class DocumentContent(BaseModel):
    """Document content model"""
    
    document_id: str = Field(..., description="Document unique identifier")
    file_path: str = Field(..., description="File path")
    segments: List[TextSegment] = Field(..., description="Text segments list")
    markdown_content: str = Field(..., description="Complete markdown format content")
    content_revision: int = Field(
        default=1,
        ge=1,
        description="Explicit retrieval-corpus revision; bump after in-place segment edits",
    )
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")


class SegmentEmbedding(BaseModel):
    """Segment embedding model"""
    
    segment_id: str = Field(..., description="Segment ID")
    embedding: List[float] = Field(..., description="Embedding vector")
    

class ReportContent(BaseModel):
    """Report content model"""
    
    document_id: str = Field(..., description="Document ID")
    document_content: DocumentContent = Field(..., description="Document content")
    # NOTE:
    # - embeddings 在 legacy/chat-only 场景可能不存在，因此必须允许为空以避免 Pydantic 直接报错。
    # - 但我们会在 upload 或首次 chat load 时尽量补齐并持久化 embeddings，以保证检索性能。
    embeddings: List[SegmentEmbedding] = Field(default_factory=list, description="Embedding vectors list")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")
    

class ProcessingConfig(BaseModel):
    """Processing configuration"""
    
    # Text extraction configuration
    min_text_length: int = Field(default=10, description="Minimum text length")
    enable_ocr: bool = Field(default=False, description="Enable OCR fallback for low-text or image-heavy pages")
    ocr_lang: Optional[str] = Field(default=None, description="OCR language, e.g. eng or chi_sim")
    ocr_use_gpu: bool = Field(default=False, description="Reserved OCR GPU flag")
    ocr_render_zoom: float = Field(default=2.0, description="Render zoom used before OCR")
    ocr_page_text_threshold: int = Field(default=50, description="Trigger OCR when extracted text chars are below this threshold")
    ocr_min_text_len: int = Field(default=12, description="Minimum OCR text length kept as a segment")
    ocr_image_min_area: int = Field(default=50000, description="Minimum image area that can trigger OCR fallback")
    ocr_max_images_per_page: int = Field(default=4, description="Maximum large images to consider per page when deciding OCR")
    enable_ocr_table: bool = Field(default=False, description="Enable OCR fallback on image-heavy pages when table detection fails")
    ocr_table_max_per_image: int = Field(default=2, description="Reserved OCR table fallback limit per image")
    
    # Embedding configuration
    embedding_model: str = Field(default_factory=get_configured_embedding_model_name, description="Embedding model name")
    batch_size: int = Field(default=32, description="Batch size")
    max_length: int = Field(default=512, description="Maximum text length")
    
    # Device configuration
    #device: str = Field(default="cpu", description="Computing device")
    device: str = Field(default="cuda", description="Computing device")
    
    # Retrieval configuration
    top_k: int = Field(default=10, description="Number of retrieval results")
    similarity_threshold: float = Field(default=0.3, description="Similarity threshold")
    use_metric_retrieval_corpus: bool = Field(
        default=True,
        description=(
            "Use structure-preserving paragraph/table views for report metric retrieval"
        ),
    )
    target_year: Optional[int] = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Optional year projected into the legacy scalar value field",
    )
    
    # LLM configuration
    llm_api_key: Optional[str] = Field(default=None, description="LLM API key")
    llm_model: str = Field(default="qwen-plus-2025-07-28", description="LLM model name")
    llm_base_url: Optional[str] = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", description="LLM API base URL")


class DisclosureStatus(str, Enum):
    """Disclosure status enumeration"""
    FULLY_DISCLOSED = "fully_disclosed"           # Fully disclosed
    PARTIALLY_DISCLOSED = "partially_disclosed"   # Partially disclosed  
    NOT_DISCLOSED = "not_disclosed"               # Not disclosed


class ReportSegment(BaseModel):
    """Report content segment"""
    segment_id: str = Field(..., description="Segment ID")
    content: str = Field(..., description="Segment content")
    page_number: Optional[int] = Field(default=None, description="Page number")
    section_title: Optional[str] = Field(default=None, description="Section title")


class DisclosureAnalysis(BaseModel):
    """Disclosure analysis result for a single metric"""
    metric_id: str = Field(..., description="Metric ID")
    metric_name: str = Field(..., description="Metric name")
    metric_code: str = Field(default="", description="Metric code")
    disclosure_status: DisclosureStatus = Field(..., description="Disclosure status")
    reasoning: str = Field(..., description="LLM analysis reasoning")
    evidence_segments: List[str] = Field(default_factory=list, description="Evidence segment ID list")
    improvement_suggestions: List[str] = Field(default_factory=list, description="Improvement suggestions")
    # Additional SASB fields for display
    category: str = Field(default="", description="Metric category (Quantitative/Qualitative)")
    topic: str = Field(default="", description="SASB topic / theme (e.g. Energy Management, Data Privacy)")
    unit: str = Field(default="", description="Metric unit")
    type: str = Field(default="", description="Metric type")
    definition: str = Field(default="", description="Metric definition from framework data")
    # NOTE:
    # - value/page/context 需要支持“有数值的量化披露”与“只有定性描述”的两类输出。
    # - value：有则仅为数字；无量化或未披露时用 "n/a"；理由与叙述放在 reasoning / context。
    # - context 用于前端 hover/Popover 展示“证据摘要/摘录”，同时可用于回溯。
    value: Optional[Union[str, int, float]] = Field(
        default=None,
        description="Metric-specific numeric disclosure only; otherwise 'n/a'",
    )
    year_values: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "All metric-specific annual values retained as one metric result; "
            "each item may contain year, value, unit, page, context and evidence_segment_id"
        ),
    )
    selected_year: Optional[int] = Field(
        default=None,
        description="Year currently projected into value/page/context for backward compatibility",
    )
    value_status: Optional[str] = Field(
        default=None,
        description="Value resolution state such as exact, ambiguous, conflict or none",
    )
    context: Optional[str] = Field(default=None, description="Evidence context/excerpt for the found value")
    page: Optional[int] = Field(default=None, description="Page number where value/context is found")
    evidence_sources: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Public evidence provenance without internal paths or worker details",
    )
    derived_calculation: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Validated formula, operands and result when a value is strictly derived",
    )


class ComplianceAssessment(BaseModel):
    """Overall compliance assessment report"""
    report_id: str = Field(..., description="Report ID")
    assessment_date: datetime = Field(default_factory=datetime.now, description="Assessment date")
    total_metrics_analyzed: int = Field(..., description="Total number of analyzed metrics")
    disclosure_summary: Dict[DisclosureStatus, int] = Field(..., description="Statistics for each status")
    metric_analyses: List[DisclosureAnalysis] = Field(default_factory=list, description="Metric analysis list")
    overall_compliance_score: float = Field(ge=0.0, le=1.0, description="Overall compliance score")
    report_file_path: str = Field(..., description="Report file path")
    framework: Optional[str] = Field(None, description="Framework used (e.g., SASB, GRI)")
    industry: Optional[str] = Field(None, description="Industry sector")
    semi_industry: Optional[str] = Field(None, description="Sub-industry sector")
    company_id: Optional[str] = Field(None, description="Company identifier for aggregated assessments")
    company_name: Optional[str] = Field(None, description="Company name for aggregated assessments")
    source_reports: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Reports contributing evidence to an aggregated assessment",
    )


class ChatMessage(BaseModel):
    """Chat message model"""
    role: str = Field(..., description="Role (user/assistant/system)")
    content: str = Field(..., description="Message content")
    timestamp: datetime = Field(default_factory=datetime.now, description="Timestamp")


class ChatSession(BaseModel):
    """Chat session model"""
    session_id: str = Field(..., description="Session ID")
    messages: List[ChatMessage] = Field(default_factory=list, description="Message history")
    report_context: Optional[str] = Field(default=None, description="Report context ID")
    compliance_context: Optional[str] = Field(default=None, description="Compliance assessment context ID")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")
    updated_at: datetime = Field(default_factory=datetime.now, description="Update time")


class ChatRequest(BaseModel):
    """Chat request model"""
    session_id: Optional[str] = Field(default=None, description="会话ID")
    message: str = Field(..., description="User message")
    include_context: bool = Field(
        default=True,
        description="Whether to include loaded report and compliance context",
    )
    # Optional UI/runtime context (e.g., Cross Analysis: selected file_ids, current taxonomy node)
    # This must remain optional for backward compatibility.
    context: Optional[Dict[str, Union[str, int, float, bool, List, Dict]]] = Field(
        default=None,
        description="Optional context payload injected by the frontend (ids, dimension/topic/metric, etc.)"
    )




class ChatResponse(BaseModel):
    """Chat response model"""
    session_id: str = Field(..., description="Session ID")
    response: str = Field(..., description="Bot response")
    relevant_segments: List[str] = Field(default_factory=list, description="Relevant segment IDs")


# Authentication models
class LoginRequest(BaseModel):
    """Login request model"""
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class RegisterRequest(BaseModel):
    """Register request model"""
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")
    name: str = Field(..., description="User name")


class AuthResponse(BaseModel):
    """Authentication response model"""
    token: str = Field(..., description="JWT token")
    userId: int = Field(..., description="User ID")
    # 💡 return user name so frontend can display name instead of email
    name: str = Field(..., description="User name")


class User(BaseModel):
    """User data model (for internal storage)"""
    userId: int = Field(..., description="User ID")
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password (plain text)")
    name: str = Field(..., description="User name")
    sessionActive: bool = Field(default=True, description="Session active status")
