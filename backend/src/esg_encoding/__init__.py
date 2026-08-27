"""
ESG 报告披露导向编码模块。

该包的若干子模块依赖较重（例如 sentence-transformers、LLM/RAG 相关组件）。
为了避免在仅使用轻量功能时因为可选依赖缺失而导致 `import esg_encoding`
立即失败，这里采用惰性导入：
- 数据模型与异常类保持直接可用；
- 其余重量级组件在首次访问时再导入。
"""

from importlib import import_module

from .exceptions import ESGEncodingError, ContentExtractionError, ContentEmbeddingError
from .models import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSession,
    ComplianceAssessment,
    DisclosureAnalysis,
    DisclosureStatus,
    DocumentContent,
    ESGMetric,
    MetricCategory,
    MetricCollection,
    MetricRetrievalResult,
    MetricSource,
    ProcessingConfig,
    ReportContent,
    RetrievalResult,
    SegmentEmbedding,
    SemanticExpansion,
    TextSegment,
)

__version__ = "1.0.1"

_LAZY_IMPORTS = {
    "ContentExtractor": (".content_extractor", "ContentExtractor"),
    "ContentEmbedder": (".content_embedder", "ContentEmbedder"),
    "ReportEncoder": (".report_encoder", "ReportEncoder"),
    "MetricProcessor": (".metric_processor", "MetricProcessor"),
    "KeywordRetriever": (".retrieval.keyword", "KeywordRetriever"),
    "SemanticRetriever": (".retrieval.semantic", "SemanticRetriever"),
    "DualChannelRetriever": (".retrieval.dual_channel", "DualChannelRetriever"),
    "DisclosureInferenceEngine": (".disclosure_inference", "DisclosureInferenceEngine"),
    "ESGChatbot": (".chat.chatbot", "ESGChatbot"),
    "flag_reranker": (".retrieval.reranker", None),
}

__all__ = [
    "TextSegment",
    "DocumentContent",
    "ReportContent",
    "SegmentEmbedding",
    "ProcessingConfig",
    "ESGMetric",
    "MetricCategory",
    "MetricSource",
    "SemanticExpansion",
    "MetricCollection",
    "RetrievalResult",
    "MetricRetrievalResult",
    "DisclosureStatus",
    "DisclosureAnalysis",
    "ComplianceAssessment",
    "ChatMessage",
    "ChatSession",
    "ChatRequest",
    "ChatResponse",
    "ContentExtractor",
    "ContentEmbedder",
    "ReportEncoder",
    "MetricProcessor",
    "KeywordRetriever",
    "SemanticRetriever",
    "DualChannelRetriever",
    "DisclosureInferenceEngine",
    "ESGChatbot",
    "ESGEncodingError",
    "ContentExtractionError",
    "ContentEmbeddingError",
    "flag_reranker",
]


def __getattr__(name: str):
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    try:
        module = import_module(module_name, __name__)
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            f"无法加载 esg_encoding.{name}：缺少可选依赖 {exc.name!r}。请先安装对应依赖后再使用该功能。"
        ) from exc

    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value
    return value
