"""Semantic evidence retrieval."""

import os
import re
import threading
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
from loguru import logger
from sklearn.metrics.pairwise import cosine_similarity

from .hipporag.settings import HippoRAGSettings
from .metric_profile import build_metric_retrieval_profile
from .metric_corpus import (
    MetricRetrievalCorpus,
    attach_metric_embeddings,
    metric_embeddings,
    metric_search_units,
    resolve_metric_retrieval_corpus,
)
from .reranker import get_reranker
from .scoring import *  # noqa: F401,F403
from ..embedding_settings import get_configured_rerank_model_name
from ..exceptions import ContentEmbeddingError, ESGEncodingError
from ..models import ESGMetric, ProcessingConfig, ReportContent, RetrievalResult, SemanticExpansion
from ..shared_embedding_model import encode_query_texts, get_shared_embedding_model
from ..gpu_model_lifecycle import backend_lazy_load_enabled


class SemanticRetriever:
    """Semantic retriever"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize semantic retriever
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.embedding_model = None
        self.reranker = None
        self._reranker_initialized = False
        self.reranker_top_k = max(1, int(os.getenv("RERANK_TOP_K", os.getenv("LOCAL_RERANKER_TOP_K", "46")) or "46"))
        self._reranker_lock = threading.Lock()
        self._query_embedding_cache: Dict[str, np.ndarray] = {}
        self._lazy_models = backend_lazy_load_enabled()
        if not self._lazy_models:
            self._init_embedding_model()
            self._init_reranker()

    def _ensure_models(self, include_reranker: bool = True):
        if self.embedding_model is None:
            self._init_embedding_model()
        # Reranker is optional. Load lazily only once, respecting fallback behavior.
        if include_reranker and not self._reranker_initialized:
            self._init_reranker()

    def _init_embedding_model(self):
        """Initialize embedding model"""
        try:
            # Respect Docker/config device exactly; do not silently move embedding to CPU.
            requested_device = os.getenv("LOCAL_EMBEDDINGS_DEVICE") or str(getattr(self.config, "device", "cuda") or "cuda")
            device = torch.device(requested_device)
            logger.info(f"Loading embedding model: {self.config.embedding_model}")
            self.embedding_model = get_shared_embedding_model(
                self.config.embedding_model,
                device=str(device),
                hf_home=os.getenv("HF_HOME", "/root/.cache/huggingface"),
                trust_remote_code=True,
            )
            logger.info(f"Embedding model loaded successfully, device: {device}")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {str(e)}")
            raise ContentEmbeddingError(f"Failed to load embedding model: {str(e)}")
    
    def _init_reranker(self):
        """Initialize reranker model"""
        try:
            rerank_device = os.getenv("LOCAL_RERANKER_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")
            rerank_model = get_configured_rerank_model_name()
            rerank_use_fp16 = str(os.getenv("RERANK_USE_FP16", os.getenv("LOCAL_RERANKER_USE_FP16", "0")) or "0").strip().lower() in ("1", "true", "yes", "y", "on")
            settings = HippoRAGSettings(
                rerank_model_name_or_path=rerank_model,
                rerank_device=rerank_device,
                rerank_use_fp16=rerank_use_fp16,
            )
            self.reranker_top_k = max(1, int(getattr(settings, "rerank_top_k", self.reranker_top_k) or self.reranker_top_k))
            self.reranker = get_reranker(settings)
            self._reranker_initialized = True
            if self.reranker is not None:
                logger.info(f"Reranker model loaded successfully: {rerank_model}")
            else:
                logger.warning("Reranker not available, will use basic cosine similarity")
        except Exception as e:
            logger.warning(f"Failed to load reranker model, fallback to cosine similarity: {str(e)}")
            self.reranker = None
            self._reranker_initialized = True

    def _segment_structure_bonus(self, segment, prefer_narrative: bool = False) -> float:
        return _segment_structure_bonus(segment, prefer_narrative=prefer_narrative)

    def _build_semantic_query(self, metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None) -> str:
        """Build a metric-centric dense query from the canonical profile."""
        profile = build_metric_retrieval_profile(metric, semantic_expansion)
        return profile.dense_query

    def prepare_metric_queries(
        self,
        metric_expansion_pairs: Sequence[
            Tuple[ESGMetric, Optional[SemanticExpansion]]
        ],
    ) -> None:
        """Encode uncached dense queries in one model forward pass.

        A metric collection commonly contains dozens of metrics.  Encoding
        every metric inside ``search_by_semantic`` creates dozens of tiny GPU
        batches; this method packs the distinct queries once and retains each
        row for subsequent whole-report and linked-page retrieval.
        """

        query_texts: List[str] = []
        seen = set()
        cache = getattr(self, "_query_embedding_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._query_embedding_cache = cache
        for metric, expansion in metric_expansion_pairs:
            query_text = self._build_semantic_query(metric, expansion)
            if query_text and query_text not in cache and query_text not in seen:
                seen.add(query_text)
                query_texts.append(query_text)
        if not query_texts:
            return

        self._ensure_models(include_reranker=False)
        matrix = np.asarray(
            encode_query_texts(
                self.embedding_model,
                query_texts,
                model_name_or_path=self.config.embedding_model,
                batch_size=max(1, int(getattr(self.config, "batch_size", 32) or 32)),
                show_progress_bar=False,
                normalize_embeddings=True,
            ),
            dtype=np.float32,
        )
        if matrix.ndim == 1 and len(query_texts) == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.ndim != 2 or matrix.shape[0] != len(query_texts):
            raise ContentEmbeddingError(
                "Embedding model returned an invalid metric query matrix: "
                f"expected_rows={len(query_texts)}, shape={matrix.shape}"
            )
        for index, query_text in enumerate(query_texts):
            cache[query_text] = np.ascontiguousarray(matrix[index], dtype=np.float32)

    def _query_embedding(self, query_text: str) -> np.ndarray:
        cache = getattr(self, "_query_embedding_cache", None)
        cached = cache.get(query_text) if isinstance(cache, dict) else None
        if cached is not None:
            return np.asarray(cached, dtype=np.float32).reshape(1, -1)
        matrix = np.asarray(
            encode_query_texts(
                self.embedding_model,
                [query_text],
                model_name_or_path=self.config.embedding_model,
                batch_size=max(1, int(getattr(self.config, "batch_size", 32) or 32)),
                show_progress_bar=False,
                normalize_embeddings=True,
            ),
            dtype=np.float32,
        ).reshape(1, -1)
        if not isinstance(cache, dict):
            cache = {}
            self._query_embedding_cache = cache
        cache[query_text] = np.ascontiguousarray(matrix[0], dtype=np.float32)
        return matrix

    def _metric_semantic_corpus(
        self,
        report_content: ReportContent,
    ) -> Optional[Tuple[List[object], np.ndarray, MetricRetrievalCorpus]]:
        if not bool(getattr(self.config, "use_metric_retrieval_corpus", True)):
            return None
        try:
            corpus = resolve_metric_retrieval_corpus(report_content)
            signature = corpus.corpus_signature
            cached = getattr(
                report_content,
                "_metric_semantic_retrieval_cache",
                None,
            )
            if (
                isinstance(cached, tuple)
                and len(cached) == 3
                and cached[0] == signature
            ):
                cached_units = list(cached[1])
                cached_matrix = np.asarray(cached[2], dtype=np.float32)
                if (
                    cached_matrix.ndim == 2
                    and cached_matrix.shape[0] == len(cached_units)
                ):
                    return cached_units, cached_matrix, corpus

            units = metric_search_units(report_content, corpus)
            if len(units) != len(corpus.retrieval_views):
                raise ValueError(
                    "Metric search units do not match retrieval view count"
                )
            embedded = metric_embeddings(corpus)
            expected_model = str(getattr(self.config, "embedding_model", "") or "")
            if embedded is not None:
                matrix, _view_ids, stored_model = embedded
                if stored_model and expected_model and stored_model != expected_model:
                    embedded = None
            if embedded is None:
                if not units:
                    return None
                matrix = np.asarray(
                    self.embedding_model.encode(
                        [str(unit.content or "") for unit in units],
                        batch_size=max(
                            1,
                            int(getattr(self.config, "batch_size", 32) or 32),
                        ),
                        show_progress_bar=False,
                        convert_to_numpy=True,
                        normalize_embeddings=True,
                    ),
                    dtype=np.float32,
                )
                attach_metric_embeddings(
                    corpus,
                    matrix,
                    embedding_model=expected_model,
                    normalized=True,
                )
            else:
                matrix = embedded[0]
            matrix = np.ascontiguousarray(matrix, dtype=np.float32)
            object.__setattr__(
                report_content,
                "_metric_semantic_retrieval_cache",
                (signature, units, matrix),
            )
            return units, matrix, corpus
        except Exception as error:
            logger.warning(
                "Metric semantic corpus unavailable; using canonical embeddings: "
                f"{error}"
            )
            return None

    @staticmethod
    def _result_from_segment(
        segment,
        *,
        metric_id: str,
        score: float,
        retrieval_type: str,
    ) -> RetrievalResult:
        canonical_id = str(
            getattr(segment, "canonical_segment_id", None)
            or getattr(segment, "segment_id", "")
        )
        is_view = bool(getattr(segment, "retrieval_view_id", None))
        evidence_content = str(
            getattr(segment, "evidence_block_content", None)
            or getattr(segment, "content", "")
            or ""
        )
        return RetrievalResult(
            segment_id=canonical_id,
            content=evidence_content,
            page_number=int(getattr(segment, "page_number", 1) or 1),
            score=float(score),
            retrieval_type=retrieval_type,
            matched_keywords=[],
            metric_id=metric_id,
            evidence_block_id=getattr(segment, "evidence_block_id", None),
            retrieval_view_id=getattr(segment, "retrieval_view_id", None),
            source_segment_ids=list(
                getattr(segment, "source_segment_ids", None) or [canonical_id]
            ),
            matched_content=(
                str(
                    getattr(segment, "matched_content", None)
                    or getattr(segment, "content", "")
                    or ""
                )
                if is_view
                else None
            ),
            evidence_block_content=(evidence_content if is_view else None),
            matched_row_index=getattr(segment, "matched_row_index", None),
            matched_column_indexes=list(
                getattr(segment, "matched_column_indexes", None) or []
            ),
            score_breakdown={retrieval_type.split("+")[0]: float(score)},
            **visual_result_fields(segment),
        )

    @staticmethod
    def _collapse_results(
        results: Sequence[RetrievalResult],
    ) -> List[RetrievalResult]:
        collapsed: Dict[str, RetrievalResult] = {}
        for result in results:
            current = collapsed.get(result.segment_id)
            if current is None or float(result.score or 0.0) > float(
                current.score or 0.0
            ):
                collapsed[result.segment_id] = result
        values = list(collapsed.values())
        values.sort(key=lambda item: item.score, reverse=True)
        return values

    def _build_rerank_instruction(self, metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None) -> str:
        profile = build_metric_retrieval_profile(metric, semantic_expansion)
        metric_name = profile.metric_name or str(getattr(metric, "metric_name", "") or "").strip()
        metric_code = profile.metric_code or str(getattr(metric, "metric_code", "") or "").strip()
        topic = profile.topic or str(getattr(metric, "sasb_topic", "") or "").strip()

        focus_terms: List[str] = []
        if metric_name:
            focus_terms.append(f"canonical metric '{metric_name}'")
        if metric_code:
            focus_terms.append(f"standard code '{metric_code}'")
        for alias in profile.aliases[:8]:
            if alias and alias not in {metric_name, metric_code, topic}:
                focus_terms.append(f"alias '{alias}'")

        avoid_terms = [term for term in profile.negative_anchor_terms[:10] if term]
        focus_clause = ", ".join(focus_terms) if focus_terms else "the target canonical ESG metric"
        avoid_clause = (" Avoid confusing it with: " + "; ".join(avoid_terms) + ".") if avoid_terms else ""
        profile_instruction = profile.rerank_instruction or "Judge whether the candidate evidence directly or indirectly discloses this exact canonical metric, not merely a related ESG topic."
        if profile.unit:
            profile_instruction = re.sub(
                rf",?\s*expected\s+unit\s*:\s*{re.escape(profile.unit)}",
                "",
                profile_instruction,
                flags=re.IGNORECASE,
            )
        return (
            "ESG report exact-metric evidence ranking. "
            f"{profile_instruction} "
            f"Prioritize passages explicitly matching {focus_clause}. "
            "Prefer labeled data cells, real data rows, explicit numeric or narrative disclosures, and actual data found on an internal PDF link target. "
            "Treat a code-only framework index as navigation evidence and never rank it above real data solely because the code matches. "
            "Evaluate linked target-page passages by the same metric relevance rules, while giving relevant linked data modest extra attention. "
            "The industry topic is only secondary context. Units, %, percent, and percentage alone are not relevance signals. "
            "Do not let broad topic similarity, adjacent ESG topics, future goals, or generic commitments outrank evidence for the exact metric identity."
            f"{avoid_clause}"
        )

    def _build_unified_rerank_query(
        self,
        metric: ESGMetric,
        semantic_expansion: Optional[SemanticExpansion] = None,
    ) -> str:
        profile = build_metric_retrieval_profile(metric, semantic_expansion)
        parts = []
        if profile.metric_code:
            parts.append(f"Canonical code: {profile.metric_code}")
        if profile.metric_name:
            parts.append(f"Canonical metric: {profile.metric_name}")
        if profile.definition:
            parts.append(f"Definition: {profile.definition}")
        if profile.aliases:
            parts.append("Identity aliases: " + "; ".join(profile.aliases[:12]))
        if profile.topic:
            parts.append(f"Secondary industry topic: {profile.topic}")
        return "\n".join(parts)

    def rerank_candidates(
        self,
        candidates: Sequence[RetrievalResult],
        metric: ESGMetric,
        semantic_expansion: Optional[SemanticExpansion] = None,
    ) -> List[RetrievalResult]:
        """Apply one Qwen3 pass to candidates from every retrieval channel."""
        values = list(candidates)
        if not values:
            return []
        enabled = bool(
            getattr(
                self.config,
                "use_reranker",
                getattr(self.config, "use_semantic_retrieval", True),
            )
        )
        if not enabled:
            return values
        if not self._reranker_initialized:
            self._init_reranker()
        if self.reranker is None:
            return values

        query_text = self._build_unified_rerank_query(metric, semantic_expansion)
        instruction = self._build_rerank_instruction(metric, semantic_expansion)
        passages = []
        for candidate in values:
            retrieval_type = str(candidate.retrieval_type or "")
            source_note = "Internal PDF linked target candidate." if "linked_page" in retrieval_type else "Report candidate."
            passages.append(
                "\n".join(
                    [
                        source_note,
                        f"Retrieval channels: {retrieval_type}",
                        candidate.matched_content or candidate.content or "",
                    ]
                )
            )
        pairs = [[query_text, passage] for passage in passages]
        try:
            with self._reranker_lock:
                try:
                    scores = self.reranker.compute_score(
                        pairs,
                        normalize=True,
                        instruction=instruction,
                    )
                except TypeError:
                    scores = self.reranker.compute_score(pairs, normalize=True)
            if not isinstance(scores, list):
                scores = [scores]
            if len(scores) != len(values):
                logger.warning(
                    "Unified reranker returned an unexpected score count: "
                    f"expected={len(values)}, actual={len(scores)}"
                )
                return values
        except Exception as exc:
            logger.warning(f"Unified candidate reranking failed; keeping deterministic order: {exc}")
            return values

        reranked = []
        for candidate, raw_score in zip(values, scores):
            qwen_score = _clamp_score(float(raw_score))
            final_score = _clamp_score(0.20 * float(candidate.score or 0.0) + 0.80 * qwen_score)
            retrieval_type = str(candidate.retrieval_type or "")
            if "qwen_unified_rerank" not in retrieval_type:
                retrieval_type += "+qwen_unified_rerank"
            reranked.append(
                candidate.model_copy(
                    update={
                        "score": final_score,
                        "retrieval_type": retrieval_type,
                    }
                )
            )
        reranked.sort(key=lambda item: item.score, reverse=True)
        return reranked

    def search_by_semantic(self, report_content: ReportContent,
                          metric: ESGMetric,
                          semantic_expansion: Optional[SemanticExpansion] = None,
                          apply_reranker: bool = True) -> List[RetrievalResult]:
        """
        Search by semantic similarity
        
        Args:
            report_content: Report content
            semantic_expansion: Semantic expansion
            
        Returns:
            List[RetrievalResult]: Retrieval results
        """
        try:
            self._ensure_models(include_reranker=apply_reranker)
            query_text = self._build_semantic_query(metric, semantic_expansion)
            if not query_text:
                raise ValueError("No semantic query text available")
            profile = build_metric_retrieval_profile(metric, semantic_expansion)
            anchor_terms = profile.anchor_terms or _extract_metric_anchor_terms(metric, semantic_expansion)

            query_embedding = self._query_embedding(query_text)
            target_window = _target_window_size(self.config, metric, observed_matches=0)
            pool_size = _internal_pool_size(self.config, metric, observed_matches=0, channel="semantic")
            preselect_limit = min(pool_size, max(1, int(getattr(self, "reranker_top_k", pool_size) or pool_size))) if apply_reranker and self.reranker is not None else pool_size
            
            # Get report segments' embeddings once per report object and reuse the
            # same matrix across metrics. This avoids rebuilding a large numpy array
            # for every metric without moving any passage vectors back onto GPU.
            metric_semantic = self._metric_semantic_corpus(report_content)
            embedding_cache = (
                (metric_semantic[0], metric_semantic[1])
                if metric_semantic is not None
                else getattr(
                    report_content,
                    "_semantic_retrieval_embedding_cache",
                    None,
                )
            )
            if embedding_cache is None:
                segment_lookup = {
                    getattr(seg, "segment_id", None): seg
                    for seg in report_content.document_content.segments
                    if getattr(seg, "segment_id", None)
                }

                native_matrix = getattr(report_content, "_embedding_matrix", None)
                native_ids = getattr(report_content, "_embedding_segment_ids", None)
                if isinstance(native_matrix, np.ndarray) and native_matrix.ndim == 2 and native_ids is not None and len(native_ids) == native_matrix.shape[0]:
                    pairs = [(segment_lookup.get(str(segment_id)), index) for index, segment_id in enumerate(native_ids)]
                    valid_pairs = [(segment, index) for segment, index in pairs if segment is not None]
                    segments = [segment for segment, _ in valid_pairs]
                    indices = [index for _, index in valid_pairs]
                    if indices == list(range(native_matrix.shape[0])) and native_matrix.dtype == np.float32 and native_matrix.flags.c_contiguous:
                        segment_embeddings = native_matrix
                    else:
                        segment_embeddings = np.ascontiguousarray(native_matrix[indices], dtype=np.float32)
                else:
                    raw_embeddings = []
                    segments = []
                    for segment_emb in report_content.embeddings:
                        seg = segment_lookup.get(segment_emb.segment_id)
                        if seg is None:
                            continue
                        raw_embeddings.append(segment_emb.embedding)
                        segments.append(seg)
                    segment_embeddings = np.asarray(raw_embeddings, dtype=np.float32) if raw_embeddings else np.zeros((0, 0), dtype=np.float32)

            else:
                segments, segment_embeddings = embedding_cache

            # Both the persisted representation and the native fast path are
            # NumPy matrices.  Never use their truth value: bool(ndarray) raises
            # for multi-element reports.  Normalizing here also makes a legacy
            # list-based cache safe without copying an already-contiguous
            # float32 matrix.
            if not isinstance(segments, list):
                segments = list(segments)
            segment_embeddings = np.asarray(segment_embeddings, dtype=np.float32)
            if segment_embeddings.size == 0:
                logger.warning("No embedding vectors found in report")
                return []
            if segment_embeddings.ndim != 2 or segment_embeddings.shape[0] != len(segments):
                logger.warning(
                    "Invalid report embedding matrix: rows={} segments={} dimensions={}",
                    segment_embeddings.shape[0] if segment_embeddings.ndim else 0,
                    len(segments),
                    segment_embeddings.ndim,
                )
                return []
            segment_embeddings = np.ascontiguousarray(segment_embeddings, dtype=np.float32)

            normalized_cache = (segments, segment_embeddings)
            if metric_semantic is None and (
                embedding_cache is None
                or embedding_cache[0] is not segments
                or embedding_cache[1] is not segment_embeddings
            ):
                try:
                    setattr(report_content, "_semantic_retrieval_embedding_cache", normalized_cache)
                except Exception:
                    pass

            similarities = cosine_similarity(query_embedding, segment_embeddings)[0]
            relaxed_threshold = max(0.08, float(getattr(self.config, "similarity_threshold", 0.2) or 0.2) * (0.65 if _is_quantitative_metric(metric) else 0.8))

            def _boost_semantic_score(segment, similarity: float) -> float:
                return _clamp_score(
                    float(similarity)
                    + _segment_structure_bonus(segment, expected_unit=getattr(metric, "unit", None), prefer_narrative=not _is_quantitative_metric(metric))
                    + _qualitative_relevance_adjustment(
                        metric,
                        getattr(segment, "content", "") or "",
                        anchor_terms,
                        getattr(segment, "segment_type", ""),
                        segment=segment,
                    )
                    + _metric_evidence_quality_adjustment(metric, segment, anchor_terms)
                    + _topic_relevance_adjustment(metric, getattr(segment, "content", "") or "")
                )

            # Use reranker if available, otherwise fallback to cosine similarity
            if apply_reranker and self.reranker is not None:
                pre_candidates = []
                for segment, similarity in zip(segments, similarities):
                    boosted = _boost_semantic_score(segment, similarity)
                    if boosted >= relaxed_threshold:
                        pre_candidates.append((segment, boosted))
                if not pre_candidates:
                    pre_candidates = [
                        (segment, _boost_semantic_score(segment, similarity))
                        for segment, similarity in zip(segments, similarities)
                    ]
                pre_candidates.sort(key=lambda x: x[1], reverse=True)
                pre_candidates = pre_candidates[:preselect_limit]

                rerank_instruction = self._build_rerank_instruction(metric, semantic_expansion)
                rerank_scores = None

                can_reuse_embeddings = False
                if hasattr(self.reranker, "compute_score_from_embeddings"):
                    try:
                        can_reuse_embeddings = bool(self.reranker.can_reuse_document_embeddings(self.config.embedding_model))
                    except Exception:
                        can_reuse_embeddings = False

                if can_reuse_embeddings:
                    embedding_by_segment_id = {
                        getattr(segment, "segment_id", ""): segment_embeddings[idx]
                        for idx, segment in enumerate(segments)
                    }
                    candidate_embeddings = [
                        embedding_by_segment_id.get(getattr(segment, "segment_id", ""))
                        for segment, _ in pre_candidates
                    ]
                    if all(embedding is not None for embedding in candidate_embeddings):
                        with self._reranker_lock:
                            try:
                                rerank_scores = self.reranker.compute_score_from_embeddings(
                                    query_text,
                                    candidate_embeddings,
                                    normalize=True,
                                    instruction=rerank_instruction,
                                )
                            except TypeError:
                                rerank_scores = self.reranker.compute_score_from_embeddings(query_text, candidate_embeddings, normalize=True)

                if rerank_scores is None:
                    query_doc_pairs = [[query_text, segment.content] for segment, _ in pre_candidates]
                    with self._reranker_lock:
                        try:
                            rerank_scores = self.reranker.compute_score(query_doc_pairs, normalize=True, instruction=rerank_instruction)
                        except TypeError:
                            rerank_scores = self.reranker.compute_score(query_doc_pairs, normalize=True)
                if not isinstance(rerank_scores, list):
                    rerank_scores = [rerank_scores]

                results = []
                for (segment, base_score), score in zip(pre_candidates, rerank_scores):
                    rerank_score = _clamp_score(float(score))
                    final_score = _clamp_score((base_score * 0.35) + (rerank_score * 0.65))
                    if final_score >= relaxed_threshold or len(results) < target_window:
                        results.append(self._result_from_segment(
                            segment,
                            score=float(final_score),
                            retrieval_type="semantic+rerank",
                            metric_id=metric.metric_id,
                        ))
                logger.info("Used reranker for semantic retrieval" + (" with cached report embeddings" if can_reuse_embeddings else ""))
            else:
                results = []
                for segment, similarity in zip(segments, similarities):
                    boosted = _boost_semantic_score(segment, similarity)
                    if boosted >= relaxed_threshold:
                        results.append(self._result_from_segment(
                            segment,
                            score=float(boosted),
                            retrieval_type="semantic",
                            metric_id=metric.metric_id,
                        ))
                
                logger.info(f"Used cosine similarity fallback for semantic retrieval")
            
            # Sort by score
            results = self._collapse_results(results)
            
            observed_matches = len(results)
            final_window = _target_window_size(self.config, metric, observed_matches=observed_matches)
            metric_log_id = getattr(metric, "metric_id", None) or "unknown"
            logger.info(f"Semantic retrieval for metric {metric_log_id} found {observed_matches} results")
            return results[:final_window]
            
        except Exception as e:
            logger.error(f"Semantic retrieval failed: {str(e)}")
            raise ESGEncodingError(f"Semantic retrieval failed: {str(e)}")
