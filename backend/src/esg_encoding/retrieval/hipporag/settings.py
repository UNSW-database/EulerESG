"""HippoRAG retrieval augmentation settings (code-first).

Put this file at:
  backend/src/esg_encoding/retrieval/hipporag/settings.py

核心思路（质量天花板 + 速度更稳）：
- “整篇都能找”靠 **npz/向量全量召回**（embedding matrix）。
- HippoRAG 只做 **加分/增强**，永远不做硬过滤；HippoRAG 空了也不影响输出。
- HippoRAG 的索引务必做“小而稳”（pack + 上限），避免全量索引带来的 I/O 和内存压力。

注意：
- HippoRAG v2.x 对 `embedding_model_name` 有“支持列表”限制。
  如果你要让 HippoRAG 也用你自己的 embedding（比如 bge-m3），推荐走：
  **OpenAI-compatible embeddings endpoint**（例如 vLLM / Text-Embeddings-Inference / 你自己的 embeddings server），
  然后在下面填 `embedding_base_url` / `embedding_api_key`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re

from ...embedding_settings import (
    get_configured_rerank_model_dtype,
    get_configured_rerank_model_name,
)


def _backend_dir() -> Path:
    # .../backend/src/esg_encoding/retrieval/hipporag/settings.py -> parents[2] == .../backend
    return Path(__file__).resolve().parents[4]


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "") or default))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_rerank_use_fp16(default: bool = False) -> bool:
    """Derive legacy FlagReranker fp16 switch from Docker precision when set."""
    configured_dtype = str(os.getenv("RERANK_MODEL_DTYPE", "") or "").strip()
    if configured_dtype:
        return get_configured_rerank_model_dtype() == "float16"
    return _env_bool("LOCAL_RERANKER_USE_FP16", default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, "") or default))
    except Exception:
        return default


def _env_optional(name: str) -> str | None:
    value = str(os.getenv(name, "") or "").strip()
    return value or None


def hipporag_package_version() -> str:
    """Return the installed HippoRAG version for cache compatibility."""
    try:
        return version("hipporag")
    except PackageNotFoundError:
        return "unavailable"


def resolve_hipporag_embedding_model_name(settings: object) -> str:
    """Resolve a model backend supported by the pinned HippoRAG 2.x API."""
    configured = str(getattr(settings, "embedding_model_name", "") or "").strip()
    lower = configured.lower()
    supported_markers = (
        "gritlm",
        "contriever",
        "text-embedding",
        "cohere",
    )
    if configured and any(marker in lower for marker in supported_markers):
        return configured
    return str(
        getattr(settings, "fallback_embedding_model_name", "facebook/contriever")
        or "facebook/contriever"
    ).strip()


def versioned_hipporag_cache_root(settings: object) -> Path:
    """Return one cache namespace for Chat and Cross Analysis."""
    model_name = resolve_hipporag_embedding_model_name(settings)
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", model_name).strip("_")
    safe_version = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        hipporag_package_version(),
    ).strip("_")
    suffix = (
        f"schema2__hipporag-{safe_version or 'unknown'}"
        f"__{safe_model or 'embedding'}"
        f"__docs{int(getattr(settings, 'max_docs_to_index', 0) or 0)}"
        f"__chars{int(getattr(settings, 'target_chars_per_doc', 0) or 0)}"
    )
    cache_root = Path(getattr(settings, "cache_root"))
    return cache_root if cache_root.name == suffix else cache_root / suffix


@dataclass(frozen=True)
class HippoRAGSettings:
    """Tunable knobs for HippoRAG + hybrid retrieval."""

    # ========= 主开关 =========
    enabled: bool = field(default_factory=lambda: _env_bool("HIPPO_ENABLED", True))

    # ========= 向量召回（整篇召回靠它） =========
    # 候选池大小（越大召回越稳，但后续 rerank/加分也会更慢；一般 200 很均衡）
    vector_recall_top_n: int = field(default_factory=lambda: _env_int("HIPPO_VECTOR_RECALL_TOP_N", 320))

    # 最终喂给 LLM 的上下文段落数（你之前 12 很合理）
    max_segment_ids_for_context: int = 12

    # hippo + embeddings 合并后的候选池上限（避免极端 query 造成候选爆炸）
    max_union_candidates: int = field(default_factory=lambda: _env_int("HIPPO_MAX_UNION_CANDIDATES", 520))

    # HippoRAG 加分强度（只加分，不硬过滤）
    # 经验值：0.06~0.12；越大越“相信”hippo 的排序信号
    hippo_boost_alpha: float = field(default_factory=lambda: _env_float("HIPPO_BOOST_ALPHA", 0.09))

    # ========= HippoRAG 检索参数 =========
    # HippoRAG 每次检索返回的 doc 数（doc 级别，不等于 segment）
    top_k_docs: int = field(default_factory=lambda: _env_int("HIPPO_TOP_K_DOCS", 12))

    # ========= HippoRAG 索引参数（核心性能开关） =========
    # True：把短段落打包成中等长度 doc（更快、更稳、命中率通常更高）
    pack_segments: bool = True

    # 每个 doc 目标字符数（越大 doc 越少 -> 索引更快；过大可能降低细粒度）
    target_chars_per_doc: int = 1800

    # 索引 doc 上限（越小越快、越省内存；建议 400~800）
    max_docs_to_index: int = field(default_factory=lambda: _env_int("HIPPO_MAX_DOCS_TO_INDEX", 900))

    # 过滤太短的段落（太短会导致短语统计不稳定，也会拖慢索引）
    min_chars_per_segment: int = 80

    # ========= HippoRAG 的 embedding backend =========
    # IMPORTANT:
    # - HippoRAG v2.x 不一定接受任意 HuggingFace model id。
    # - 如果你要让 hippo 也用 bge-m3，请优先配置 embedding_base_url。
    #
    # 你仍然可以把这里改成 "BAAI/bge-m3"，但如果 HippoRAG 报 Unknown embedding model，
    # 会在 wrapper 里提示你改用 embedding_base_url。
    # HippoRAG 官方文档：embedding_model_name 目前支持 NV-Embed、GritLM、Contriever（含 facebook/contriever 系列）。

    # 这里默认用 Facebook 的 Contriever（MSMARCO 版本），不使用 NVIDIA embedding。

    # HippoRAG 2.0.0a4 does not accept arbitrary application embedding models.
    # Keep its graph embeddings on a separately configurable supported backend.
    embedding_model_name: str = field(
        default_factory=lambda: os.getenv(
            "HIPPO_EMBEDDING_MODEL",
            "facebook/contriever",
        ).strip()
        or "facebook/contriever"
    )


    # 当 HippoRAG 对 embedding_model_name 校验更严格时，用这个兜底再试一次。

    fallback_embedding_model_name: str = "facebook/contriever"

    # Optional: override EulerESG ProcessingConfig values just for HippoRAG
    llm_model_name: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None

    # Optional: OpenAI-compatible embeddings endpoint (recommended for bge-m3)
    embedding_base_url: str | None = field(default_factory=lambda: _env_optional("HIPPO_EMBEDDING_BASE_URL"))
    embedding_api_key: str | None = field(default_factory=lambda: _env_optional("HIPPO_EMBEDDING_API_KEY"))

    # ========= LLM 重写（降低 HippoRAG empty） =========
    # True：当第一次 HippoRAG 结果为空，且 query 偏长时，再调用 LLM 做“关键词化重写”重试
    llm_rewrite_enabled: bool = True
    llm_rewrite_min_chars: int = 24     # 中文/英文都通用：query 字符数超过再触发重写
    llm_rewrite_n: int = 3              # 最多生成多少条短 query


    # ========= FlagEmbedding Reranker（cross-encoder，可选但强烈推荐） =========
    # 只对融合排序后的 topK 做 rerank（避免爆算力），最终排序以 rerank 结果为准
    rerank_enabled: bool = True
    rerank_model_name_or_path: str = field(default_factory=get_configured_rerank_model_name)
    rerank_top_k: int = field(default_factory=lambda: _env_int("LOCAL_RERANKER_TOP_K", 46))
    rerank_batch_size: int = field(default_factory=lambda: _env_int("LOCAL_RERANKER_BATCH_SIZE", 64))
    # 质量优先：默认不启用 fp16（不会以牺牲准确性为代价）
    rerank_use_fp16: bool = field(default_factory=lambda: _env_rerank_use_fp16(False))
    rerank_device: str = field(default_factory=lambda: os.getenv("LOCAL_RERANKER_DEVICE", "cuda:0"))  # 没 GPU 就改成 "cpu"
    rerank_max_chars_per_passage: int = field(default_factory=lambda: _env_int("LOCAL_RERANKER_MAX_CHARS_PER_PASSAGE", 4096))

    # ========= 存储与行为 =========
    cache_root: Path = _backend_dir() / "outputs" / "hipporag_cache"
    max_cached_indexes: int = field(
        default_factory=lambda: _env_int("HIPPO_MAX_CACHED_INDEXES", 1)
    )
    force_reindex: bool = False
    warm_index_in_background: bool = field(
        default_factory=lambda: _env_bool("HIPPO_WARM_INDEX_IN_BACKGROUND", True)
    )
