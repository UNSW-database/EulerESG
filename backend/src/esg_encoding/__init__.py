"""
ESG报告披露导向编码模块（完整版）

该模块提供了完整的ESG报告处理流程，包括：
- PDF内容提取 + 段落标签
- BGE-M3嵌入生成
- Markdown格式输出
- 指标处理与语义扩展
- 双通道检索（关键词+语义）
- LLM驱动的披露推理
- 交互式ESG聊天机器人
"""

# 核心数据模型
from .models import (
    TextSegment, DocumentContent, ReportContent, SegmentEmbedding, ProcessingConfig,
    # 指标相关模型
    ESGMetric, MetricCategory, MetricSource, SemanticExpansion, MetricCollection,
    # 检索结果模型
    RetrievalResult, MetricRetrievalResult,
    # 披露分析模型
    DisclosureStatus, DisclosureAnalysis, ComplianceAssessment,
    # 聊天机器人模型
    ChatMessage, ChatSession, ChatRequest, ChatResponse
)

# 内容提取和嵌入
from .content_extractor import ContentExtractor
from .content_embedder import ContentEmbedder
from .report_encoder import ReportEncoder

# 指标处理
from .metric_processor import MetricProcessor

# 双通道检索
from .dual_channel_retrieval import KeywordRetriever, SemanticRetriever, DualChannelRetriever

# 披露推理
from .disclosure_inference import DisclosureInferenceEngine

# ESG聊天机器人
from .esg_chatbot import ESGChatbot

# 异常类
from .exceptions import ESGEncodingError, ContentExtractionError, ContentEmbeddingError

__version__ = "1.0.0"
__all__ = [
    # 数据模型
    "TextSegment",
    "DocumentContent",
    "ReportContent", 
    "SegmentEmbedding",
    "ProcessingConfig",
    # 指标相关模型
    "ESGMetric",
    "MetricCategory", 
    "MetricSource",
    "SemanticExpansion",
    "MetricCollection",
    # 检索结果模型
    "RetrievalResult",
    "MetricRetrievalResult",
    # 披露分析模型
    "DisclosureStatus",
    "DisclosureAnalysis",
    "ComplianceAssessment",
    # 聊天机器人模型
    "ChatMessage",
    "ChatSession",
    "ChatRequest",
    "ChatResponse",
    # 内容处理
    "ContentExtractor",
    "ContentEmbedder",
    "ReportEncoder",
    # 指标处理
    "MetricProcessor",
    # 双通道检索
    "KeywordRetriever",
    "SemanticRetriever", 
    "DualChannelRetriever",
    # 披露推理
    "DisclosureInferenceEngine",
    # ESG聊天机器人
    "ESGChatbot",
    # 异常类
    "ESGEncodingError",
    "ContentExtractionError",
    "ContentEmbeddingError",
] 