"""HippoRAG retrieval integration package.

Heavy HippoRAG hooks are imported lazily so modules that only need
``HippoRAGSettings`` do not eagerly import OpenAI/chat/upload dependencies.
"""

from .settings import HippoRAGSettings

__all__ = [
    "HippoRAGSettings",
    "HippoRAGRetriever",
    "enable_hipporag",
    "warm_hipporag_after_upload",
]


def __getattr__(name: str):
    if name == "HippoRAGRetriever":
        from .retriever import HippoRAGRetriever
        return HippoRAGRetriever
    if name == "enable_hipporag":
        from .patch import enable_hipporag
        return enable_hipporag
    if name == "warm_hipporag_after_upload":
        from .hooks import warm_hipporag_after_upload
        return warm_hipporag_after_upload
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
