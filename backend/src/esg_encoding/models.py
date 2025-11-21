"""
Simplified data models for ESG report encoding
"""

from typing import Dict, List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


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


class MetricRetrievalResult(BaseModel):
    """Metric retrieval result model"""
    
    metric_id: str = Field(..., description="Metric ID")
    metric_name: str = Field(..., description="Metric name")
    metric_code: str = Field(..., description="Metric code")
    keyword_results: List[RetrievalResult] = Field(default_factory=list, description="Keyword retrieval results")
    semantic_results: List[RetrievalResult] = Field(default_factory=list, description="Semantic retrieval results")
    combined_results: List[RetrievalResult] = Field(default_factory=list, description="Combined retrieval results")
    total_matches: int = Field(default=0, description="Total matches")


class TextSegment(BaseModel):
    """Text segment model"""
    
    segment_id: str = Field(..., description="Segment unique identifier")
    content: str = Field(..., description="Segment text content")
    page_number: int = Field(..., description="Page number")
    position_y: float = Field(..., description="Y coordinate position in page")
    

class DocumentContent(BaseModel):
    """Document content model"""
    
    document_id: str = Field(..., description="Document unique identifier")
    file_path: str = Field(..., description="File path")
    segments: List[TextSegment] = Field(..., description="Text segments list")
    markdown_content: str = Field(..., description="Complete markdown format content")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")


class SegmentEmbedding(BaseModel):
    """Segment embedding model"""
    
    segment_id: str = Field(..., description="Segment ID")
    embedding: List[float] = Field(..., description="Embedding vector")
    

class ReportContent(BaseModel):
    """Report content model"""
    
    document_id: str = Field(..., description="Document ID")
    document_content: DocumentContent = Field(..., description="Document content")
    embeddings: List[SegmentEmbedding] = Field(..., description="Embedding vectors list")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation time")
    

class ProcessingConfig(BaseModel):
    """Processing configuration"""
    
    # Text extraction configuration
    min_text_length: int = Field(default=10, description="Minimum text length")
    
    # Embedding configuration
    embedding_model: str = Field(default="BAAI/bge-m3", description="Embedding model name")
    batch_size: int = Field(default=32, description="Batch size")
    max_length: int = Field(default=512, description="Maximum text length")
    
    # Device configuration
    device: str = Field(default="cpu", description="Computing device")
    #device: str = Field(default="cuda", description="Computing device")
    
    # Retrieval configuration
    top_k: int = Field(default=10, description="Number of retrieval results")
    similarity_threshold: float = Field(default=0.3, description="Similarity threshold")
    
    # LLM configuration
    # value is:
    # sk-aaa2bcbd6f4c40e8926fcacb205484f8
    llm_api_key: Optional[str] = Field(default="sk-", description="LLM API key")
    # show key
    print(llm_api_key)
    llm_model: str = Field(default="qwen-plus-2025-07-28", description="LLM model name")
    llm_base_url: Optional[str] = Field(default="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", description="LLM API base URL")


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
    unit: str = Field(default="", description="Metric unit")
    type: str = Field(default="", description="Metric type")
    value: Optional[str] = Field(default=None, description="Found value from report")
    page: Optional[int] = Field(default=None, description="Page number where found")


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
    include_context: bool = Field(default=True, description="Whether to include context")


class ChatResponse(BaseModel):
    """Chat response model"""
    session_id: str = Field(..., description="Session ID")
    response: str = Field(..., description="Bot response")
    relevant_segments: List[str] = Field(default_factory=list, description="Relevant segment IDs") 
