"""Retrieval package.

Heavy retrieval components are imported lazily so lightweight utilities such as
metric_profile can be used without eagerly importing semantic embedding backends.
"""

from .metric_profile import (
    MetricRetrievalProfile,
    build_metric_retrieval_profile,
    build_profile_index,
    find_metric_profile,
    load_all_metric_profiles,
)

__all__ = [
    "retrieve_evidence",
    "iter_metric_collection_results",
    "retrieve_metric_collection",
    "map_document_metrics",
    "DualChannelRetriever",
    "KeywordRetriever",
    "SemanticRetriever",
    "MetricRetrievalProfile",
    "build_metric_retrieval_profile",
    "build_profile_index",
    "find_metric_profile",
    "load_all_metric_profiles",
]


def __getattr__(name: str):
    if name in {
        "retrieve_evidence",
        "iter_metric_collection_results",
        "retrieve_metric_collection",
        "map_document_metrics",
    }:
        from . import evidence_retriever as _evidence_retriever
        return getattr(_evidence_retriever, name)
    if name == "DualChannelRetriever":
        from .dual_channel import DualChannelRetriever
        return DualChannelRetriever
    if name == "KeywordRetriever":
        from .keyword import KeywordRetriever
        return KeywordRetriever
    if name == "SemanticRetriever":
        from .semantic import SemanticRetriever
        return SemanticRetriever
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
