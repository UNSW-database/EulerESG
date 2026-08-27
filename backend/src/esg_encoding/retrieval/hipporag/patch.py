from __future__ import annotations

"""HippoRAG augmentation for ESGChatbot retrieval.

目标（你要的“质量天花板 + 更省资源”形态）：
1) “整篇都能找”永远靠 **npz/向量全量召回**（embedding matrix topN），这是主通道。
2) HippoRAG 永远只做 **加分/增强**（boost），绝不硬过滤候选池；hippo 空了也不影响输出。
3) 降低 HippoRAG empty：先用原 query 试一次；若为空且 query 偏长，再用 LLM 做关键词化重写重试。
4) 最终只返回 max_segment_ids_for_context（例如 12）个段落 ID 给 LLM。

实现方式：运行时 patch ESGChatbot._search_relevant_content。
"""

import time
import re
from typing import List, Dict, Tuple, Optional
from collections import OrderedDict
from dataclasses import replace

import numpy as np
from loguru import logger

from ...shared_embedding_model import encode_query_texts

from ...models import ProcessingConfig
from .settings import HippoRAGSettings, versioned_hipporag_cache_root
from .retriever import HippoRAGRetriever


# -----------------------------
# Small utils
# -----------------------------

def _count_effective_chars(s: str) -> int:
    """A rough 'length' that works for Chinese/English."""
    if not s:
        return 0
    # collapse whitespace
    s2 = re.sub(r"\s+", " ", s).strip()
    return len(s2)


def _ensure_id2idx(chatbot) -> Dict[str, int]:
    """Build (and cache) segment_id -> row index mapping for the embedding matrix."""
    id2idx = getattr(chatbot, "_embedding_id2idx", None)
    seg_ids = getattr(chatbot, "_embedding_segment_ids", None) or []
    if not isinstance(id2idx, dict) or len(id2idx) != len(seg_ids):
        try:
            chatbot._embedding_id2idx = {sid: i for i, sid in enumerate(seg_ids)}
        except Exception:
            chatbot._embedding_id2idx = {}
    return chatbot._embedding_id2idx


def _get_query_embedding(chatbot, query: str, cache_size: int = 64) -> Optional[np.ndarray]:
    """Encode query once and cache it (LRU)."""
    if getattr(chatbot, "_embedder_model", None) is None:
        return None

    cache = getattr(chatbot, "_query_vec_cache", None)
    if not isinstance(cache, OrderedDict):
        cache = OrderedDict()
        chatbot._query_vec_cache = cache

    if query in cache:
        cache.move_to_end(query)
        return cache[query]

    try:
        q_vec = encode_query_texts(chatbot._embedder_model, [query], normalize_embeddings=True, show_progress_bar=False)
        q = np.asarray(q_vec[0], dtype=np.float32)
        cache[query] = q
        cache.move_to_end(query)
        while len(cache) > int(cache_size):
            cache.popitem(last=False)
        return q
    except Exception as e:
        logger.warning(f"[HybridRAG] encode query failed: {e}")
        return None


def _semantic_recall_topn(
    chatbot,
    query: str,
    top_n: int,
) -> List[Tuple[str, float]]:
    """Return [(segment_id, cosine_sim)] from the embedding matrix."""
    mat = getattr(chatbot, "_embedding_matrix", None)
    seg_ids = getattr(chatbot, "_embedding_segment_ids", None) or []
    if not isinstance(mat, np.ndarray) or mat.size == 0 or not seg_ids:
        return []

    q = _get_query_embedding(chatbot, query)
    if q is None:
        return []

    sims = mat @ q  # cosine similarity (mat already normalized)
    n = min(int(top_n), int(sims.shape[0]))
    if n <= 0:
        return []

    # top-n via argpartition (fast)
    idx = np.argpartition(-sims, n - 1)[:n]
    idx = idx[np.argsort(-sims[idx])]
    out = [(str(seg_ids[i]), float(sims[i])) for i in idx]
    return out


def _score_for_id(chatbot, query_vec: np.ndarray, segment_id: str) -> Optional[float]:
    """Compute cosine score for a single segment id (cheap)."""
    mat = getattr(chatbot, "_embedding_matrix", None)
    if not isinstance(mat, np.ndarray) or mat.size == 0:
        return None
    id2idx = _ensure_id2idx(chatbot)
    i = id2idx.get(segment_id)
    if i is None:
        return None
    try:
        return float(mat[i] @ query_vec)
    except Exception:
        return None


# -----------------------------
# LLM rewrite (only when needed)
# -----------------------------

_REWRITE_CACHE_MAX = 256

def _rewrite_query_llm_multi(chatbot, config: ProcessingConfig, query: str, n: int) -> List[str]:
    """Use LLM to rewrite into short keyword-style queries. Cached (LRU)."""
    if getattr(chatbot, "llm_client", None) is None:
        return [query]

    cache = getattr(chatbot, "_hippo_rewrite_cache", None)
    if not isinstance(cache, OrderedDict):
        cache = OrderedDict()
        chatbot._hippo_rewrite_cache = cache

    if query in cache:
        cache.move_to_end(query)
        return cache[query]

    try:
        t0 = time.time()
        resp = chatbot.llm_client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You rewrite user questions into short keyword-style queries for ESG report retrieval. "
                        "Return ONLY 5-12 keywords separated by single spaces. "
                        "Remove stopwords. Keep core entities/topics. Fix obvious typos. "
                        "No punctuation, no quotes, no explanations."
                    ),
                },
                {"role": "user", "content": query},
            ],
            temperature=0.0,
            max_tokens=64,
        )
        rewritten = (resp.choices[0].message.content or "").strip()
        if not rewritten or len(rewritten) < 3:
            return [query]

        # split into candidates (still allow single line)
        parts = [p.strip() for p in re.split(r"[\n;；]+", rewritten) if p.strip()]
        # de-dup preserve order, keep short list
        out: List[str] = []
        seen = set()
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
            if len(out) >= max(1, int(n)):
                break
        if not out:
            out = [query]

        cache[query] = out
        cache.move_to_end(query)
        while len(cache) > _REWRITE_CACHE_MAX:
            cache.popitem(last=False)

        logger.info(f"[HybridRAG] llm_rewrite took {time.time()-t0:.2f}s in='{query[:60]}' out={out}")
        return out
    except Exception as e:
        logger.warning(f"[HybridRAG] LLM rewrite failed, using original query: {e}")
        return [query]


# -----------------------------
# Main patch
# -----------------------------

def enable_hipporag(chatbot, config: ProcessingConfig) -> None:
    """Enable HippoRAG hybrid retrieval for an ESGChatbot instance."""
    settings = HippoRAGSettings()

    # Version the cache path by key indexing params so config changes trigger re-index.
    cache_root = versioned_hipporag_cache_root(settings)
    settings = replace(settings, cache_root=cache_root)
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning(f"[HippoRAG] cache directory is not writable: {exc}")

    # Create retriever wrapper
    retriever = HippoRAGRetriever(settings=settings, config=config)

    # Canonical integration attributes used by upload/pre-analysis hooks.
    chatbot._hipporag_settings = settings
    chatbot._hipporag_cache_root = cache_root
    chatbot._hipporag_retriever = retriever

    # Keep the original aliases for compatibility with existing integrations.
    chatbot._hippo_settings = settings
    chatbot._hippo_cache_root = cache_root
    chatbot._hippo_retriever = retriever

    original_search = chatbot._search_relevant_content

    def patched_search(query: str) -> List[str]:
        if not settings.enabled:
            return original_search(query)

        report_content = getattr(chatbot, "report_content", None)
        if report_content is None:
            return original_search(query)

        file_id = getattr(report_content, "file_id", None) or getattr(report_content, "document_id", None)
        if not file_id:
            return original_search(query)

        # --- 1) 全量向量召回：主通道（整篇 topN） ---
        vec = _semantic_recall_topn(chatbot, query, top_n=settings.vector_recall_top_n)
        if not vec:
            # 最差情况：回到原逻辑（包含 keyword fallback）
            return original_search(query)

        vec_ids = [sid for sid, _ in vec]
        vec_scores: Dict[str, float] = {sid: score for sid, score in vec}

        # Prepare query vec for scoring extras
        q = _get_query_embedding(chatbot, query)
        if q is None:
            return vec_ids[: int(settings.max_segment_ids_for_context)]

        # --- 2) HippoRAG：只做加分（先试原 query；必要时再重写） ---
        hippo_ids: List[str] = []
        tried_queries: List[str] = [query]
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)

                # 2.1 first try original query
                logger.info(f"[HippoRAG] retrieve called file_id={file_id} top_k_docs={settings.top_k_docs} q='{query[:120]}'")
                got = retriever.retrieve_segment_ids(file_id, report_content, query)
                if got:
                    for sid in got:
                        if sid and sid not in hippo_ids:
                            hippo_ids.append(sid)

                # 2.2 if empty -> optional LLM rewrite retries
                if not hippo_ids and settings.llm_rewrite_enabled and _count_effective_chars(query) >= int(settings.llm_rewrite_min_chars):
                    rewrites = _rewrite_query_llm_multi(chatbot, config, query, n=int(settings.llm_rewrite_n))
                    for hq in rewrites:
                        if not hq or hq == query:
                            continue
                        tried_queries.append(hq)
                        logger.info(f"[HippoRAG] retry with rewritten q='{hq[:120]}'")
                        got2 = retriever.retrieve_segment_ids(file_id, report_content, hq)
                        if got2:
                            for sid in got2:
                                if sid and sid not in hippo_ids:
                                    hippo_ids.append(sid)
                        if len(hippo_ids) >= int(settings.max_segment_ids_for_context) * 2:
                            break

        except Exception as e:
            logger.warning(f"[HippoRAG] retrieval failed (use embeddings only). Error: {e}")
            hippo_ids = []

        # --- 3) 合并候选：不硬过滤，只增补，然后统一打分 ---
        merged_ids: List[str] = []
        seen = set()

        # 优先保证 embedding topN 都在（这是“整篇都能找”的底座）
        for sid in vec_ids:
            if sid and sid not in seen:
                seen.add(sid)
                merged_ids.append(sid)

        # hippo 命中的补进来（可能不在 topN 中）
        for sid in hippo_ids:
            if sid and sid not in seen:
                seen.add(sid)
                merged_ids.append(sid)

        # cap merged pool
        merged_ids = merged_ids[: int(settings.max_union_candidates)]

        # Filter to segments that exist (avoid stale ids)
        seg_map = getattr(chatbot, "_segment_map", None) or {}
        if isinstance(seg_map, dict) and seg_map:
            merged_ids = [sid for sid in merged_ids if sid in seg_map]

        # --- 4) 打分：base(embedding cosine) + alpha * hippo_rank_boost ---
        # hippo_rank_boost: 越靠前加分越多，且只对 hippo 命中生效
        hippo_boost: Dict[str, float] = {}
        if hippo_ids:
            denom = max(1, len(hippo_ids) - 1)
            for r, sid in enumerate(hippo_ids):
                # [1.0, 0.0] linear
                hippo_boost[sid] = float(1.0 - (r / denom))

        alpha = float(settings.hippo_boost_alpha)

        scored: List[Tuple[str, float]] = []
        for sid in merged_ids:
            base = vec_scores.get(sid)
            if base is None:
                base = _score_for_id(chatbot, q, sid)
            if base is None:
                continue
            final = float(base) + alpha * float(hippo_boost.get(sid, 0.0))
            scored.append((sid, final))

        # Sort by final score
        scored.sort(key=lambda x: x[1], reverse=True)

        # --- 5) 可选：FlagEmbedding cross-encoder rerank（只对 topK，避免爆算力） ---
        if getattr(settings, "rerank_enabled", False) and scored:
            try:
                from ..reranker import rerank_segment_ids

                seg_map = getattr(chatbot, "_segment_map", None) or {}

                def _get_passage(sid: str) -> str:
                    seg = seg_map.get(sid)
                    if seg is None:
                        return ""
                    return getattr(seg, "content", "") or ""

                scored = rerank_segment_ids(query, scored, _get_passage, settings)
            except Exception as e:
                logger.warning(f"[Rerank] failed/skip: {e}")


        out = [sid for sid, _ in scored[: int(settings.max_segment_ids_for_context)]]

        if hippo_ids:
            logger.info(
                f"[HybridRAG] vec_topN={len(vec_ids)} hippo={len(hippo_ids)} merged={len(merged_ids)} rerank={'on' if getattr(settings,'rerank_enabled',False) else 'off'} "
                f"tried={tried_queries} -> out={len(out)}"
            )
        else:
            logger.info(f"[HybridRAG] hippo empty -> embeddings only out={len(out)}")

        return out

    chatbot._search_relevant_content = patched_search

    logger.info(
        f"[HippoRAG] enabled={settings.enabled} top_k_docs={settings.top_k_docs} pack={settings.pack_segments} "
        f"max_docs_to_index={settings.max_docs_to_index} target_chars_per_doc={settings.target_chars_per_doc} "
        f"vector_recall_top_n={settings.vector_recall_top_n} alpha={settings.hippo_boost_alpha} "
        f"cache_root={cache_root}"
    )
