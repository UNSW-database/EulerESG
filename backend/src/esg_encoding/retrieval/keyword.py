"""Metric-centric lexical evidence retrieval.

This module separates exact code search, exact alias search and BM25-style
keyword search.  The channels are fused later by weighted RRF so exact SASB/GRI
codes and canonical metric aliases cannot be drowned out by dense similarity.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..content_revision import document_content_revision
from ..models import table_row_scope_key
from .metric_corpus import (
    MetricRetrievalCorpus,
    metric_search_units,
    resolve_metric_retrieval_corpus,
)
from .metric_profile import MetricRetrievalProfile, best_alias_matches, build_metric_retrieval_profile, tokenize_metric_text
from .scoring import *  # noqa: F401,F403


class KeywordRetriever:
    """Metric-centric lexical retriever."""

    def __init__(self, config: ProcessingConfig):
        self.config = config

    def _search_segments(
        self,
        report_content: ReportContent,
    ) -> Tuple[List[Any], Optional[MetricRetrievalCorpus]]:
        enabled = bool(getattr(self.config, "use_metric_retrieval_corpus", True))
        if enabled:
            try:
                corpus = resolve_metric_retrieval_corpus(report_content)
                units = metric_search_units(report_content, corpus)
                if units:
                    return units, corpus
            except Exception as error:
                logger.warning(
                    "Metric lexical corpus unavailable; using canonical segments: "
                    f"{error}"
                )
        return list(report_content.document_content.segments), None

    @staticmethod
    def _result_from_segment(
        segment: Any,
        *,
        score: float,
        retrieval_type: str,
        matched_keywords: Sequence[str],
        metric_id: str,
        content: Optional[str] = None,
    ) -> RetrievalResult:
        canonical_id = str(
            getattr(segment, "canonical_segment_id", None)
            or getattr(segment, "segment_id", "")
        )
        matched_content = (
            str(
                getattr(segment, "matched_content", None)
                or getattr(segment, "content", "")
                or ""
            )
            if getattr(segment, "retrieval_view_id", None)
            else None
        )
        evidence_content = str(
            getattr(segment, "evidence_block_content", None)
            or content
            or getattr(segment, "content", "")
            or ""
        )
        return RetrievalResult(
            segment_id=canonical_id,
            content=evidence_content,
            page_number=int(getattr(segment, "page_number", 1) or 1),
            score=float(score),
            retrieval_type=retrieval_type,
            matched_keywords=list(matched_keywords or []),
            metric_id=metric_id,
            evidence_block_id=getattr(segment, "evidence_block_id", None),
            retrieval_view_id=getattr(segment, "retrieval_view_id", None),
            source_segment_ids=list(
                getattr(segment, "source_segment_ids", None) or [canonical_id]
            ),
            matched_content=matched_content,
            evidence_block_content=(
                str(getattr(segment, "evidence_block_content", "") or "")
                or None
            ),
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
        """Keep the best view per canonical parent without score accumulation."""
        collapsed: Dict[str, RetrievalResult] = {}
        for result in results:
            current = collapsed.get(result.segment_id)
            if current is None or float(result.score or 0.0) > float(
                current.score or 0.0
            ):
                best, other = result, current
            else:
                best, other = current, result
            if other is not None:
                keywords = list(
                    dict.fromkeys(
                        [
                            *(best.matched_keywords or []),
                            *(other.matched_keywords or []),
                        ]
                    )
                )[:24]
                source_ids = list(
                    dict.fromkeys(
                        [
                            *(best.source_segment_ids or []),
                            *(other.source_segment_ids or []),
                        ]
                    )
                )
                best = best.model_copy(
                    update={
                        "matched_keywords": keywords,
                        "source_segment_ids": source_ids,
                    }
                )
            collapsed[result.segment_id] = best
        values = list(collapsed.values())
        values.sort(key=lambda item: item.score, reverse=True)
        return values

    def search_in_report(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        semantic_expansion: Optional[SemanticExpansion] = None,
    ) -> List[RetrievalResult]:
        """Backward-compatible lexical retrieval entry point."""
        profile = build_metric_retrieval_profile(metric, semantic_expansion)
        return self.search_metric_profile(report_content, metric, profile)

    def search_metric_profile(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: MetricRetrievalProfile,
    ) -> List[RetrievalResult]:
        channels = []
        channels.extend(self.search_exact_code(report_content, metric, profile))
        channels.extend(self.search_exact_alias(report_content, metric, profile))
        channels.extend(self.search_bm25(report_content, metric, profile))
        deduped: Dict[str, RetrievalResult] = {}
        for item in channels:
            prev = deduped.get(item.segment_id)
            if prev is None or float(item.score or 0.0) > float(prev.score or 0.0):
                deduped[item.segment_id] = item
        results = list(deduped.values())
        results.sort(key=lambda item: item.score, reverse=True)
        observed_matches = len(results)
        logger.info(f"Lexical metric retrieval for {getattr(metric, 'metric_id', 'unknown')} found {observed_matches} results")
        return results[:_internal_pool_size(self.config, metric, observed_matches=observed_matches, channel="keyword")]

    def search_exact_code(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[RetrievalResult]:
        """Exact standard-code index over report chunks."""
        profile = profile or build_metric_retrieval_profile(metric)
        if not profile.exact_code_patterns:
            return []
        results: List[RetrievalResult] = []
        segments, metric_corpus = self._search_segments(report_content)
        row_lookup = (
            self._get_table_row_lookup(report_content, segments)
            if metric_corpus is None
            else {}
        )
        for segment in segments:
            content = getattr(segment, "content", "") or ""
            if not any(pattern.search(content) for pattern in profile.exact_code_patterns):
                continue
            if metric_corpus is None:
                evidence_segment, evidence_content, upgraded = (
                    self._upgrade_code_cell_to_table_row(segment, row_lookup)
                )
            else:
                evidence_segment = segment
                evidence_content = content
                upgraded = getattr(segment, "view_type", "") == "table_row"
            score = self._score_exact_segment(evidence_segment, metric, profile, base=1.00)
            evidence_seg_type = str(getattr(evidence_segment, "segment_type", "") or "").lower()
            is_row_context = upgraded or evidence_seg_type == "table_row"
            if is_row_context:
                score = _clamp_score(score + 0.04)
            results.append(
                self._result_from_segment(
                    evidence_segment,
                    score=score,
                    retrieval_type="exact_code+table_row_context" if is_row_context else "exact_code",
                    matched_keywords=[profile.metric_code],
                    metric_id=profile.metric_id or getattr(metric, "metric_id", ""),
                    content=evidence_content,
                )
            )
        deduped: Dict[str, RetrievalResult] = {}
        for item in results:
            prev = deduped.get(item.segment_id)
            if prev is None or float(item.score or 0.0) > float(prev.score or 0.0):
                deduped[item.segment_id] = item
        results = list(deduped.values())
        results.sort(key=lambda item: ("table_row_context" in item.retrieval_type, item.score), reverse=True)
        return results[:_internal_pool_size(self.config, metric, observed_matches=len(results), channel="keyword")]

    def search_exact_alias(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[RetrievalResult]:
        """Exact canonical alias / metric-name inverted index over chunks."""
        profile = profile or build_metric_retrieval_profile(metric)
        aliases = [alias for alias in profile.aliases if alias and alias != profile.metric_code]
        if not aliases:
            return []
        results: List[RetrievalResult] = []
        segments, _metric_corpus = self._search_segments(report_content)
        for segment in segments:
            content = getattr(segment, "content", "") or ""
            matched_aliases = best_alias_matches(content, aliases, limit=16)
            if not matched_aliases:
                continue
            base = min(0.94, 0.66 + 0.06 * len(matched_aliases))
            score = self._score_exact_segment(segment, metric, profile, base=base)
            results.append(
                self._result_from_segment(
                    segment,
                    score=score,
                    retrieval_type="exact_alias",
                    matched_keywords=matched_aliases,
                    metric_id=profile.metric_id or getattr(metric, "metric_id", ""),
                    content=content,
                )
            )
        results = self._collapse_results(results)
        return results[:_internal_pool_size(self.config, metric, observed_matches=len(results), channel="keyword")]

    def search_bm25(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[RetrievalResult]:
        """BM25-style retrieval over Markdown/TextSegment chunks."""
        profile = profile or build_metric_retrieval_profile(metric)
        query_tokens = self._query_tokens(profile)
        if not query_tokens:
            return []
        identity_tokens = self._identity_query_tokens(profile)

        segments, doc_tokens, doc_freq, avg_len = self._get_metric_bm25_corpus(
            report_content
        )
        if not doc_tokens:
            return []

        n_docs = len(doc_tokens)

        raw_scores: List[Tuple[object, float, List[str]]] = []
        for segment, tokens in zip(segments, doc_tokens):
            if not tokens:
                continue
            counts = Counter(tokens)
            matched = [token for token in query_tokens if counts.get(token, 0) > 0]
            if not matched:
                continue
            if identity_tokens and not any(token in identity_tokens for token in matched):
                continue
            score = self._bm25_score(counts, len(tokens), query_tokens, doc_freq, n_docs, avg_len)
            if score <= 0:
                continue
            raw_scores.append((segment, score, matched))

        if not raw_scores:
            return []
        max_score = max(score for _, score, _ in raw_scores) or 1.0
        anchor_terms = profile.anchor_terms or _extract_metric_anchor_terms(metric)
        results: List[RetrievalResult] = []
        for segment, score, matched in raw_scores:
            normalized = min(1.0, score / max_score)
            adjusted = _clamp_score(
                0.50 * normalized
                + _segment_structure_bonus(
                    segment,
                    expected_unit=getattr(metric, "unit", None),
                    prefer_narrative=not _is_quantitative_metric(metric),
                )
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
            if adjusted < 0.08:
                continue
            results.append(
                self._result_from_segment(
                    segment,
                    score=adjusted,
                    retrieval_type="bm25",
                    matched_keywords=matched[:18],
                    metric_id=profile.metric_id or getattr(metric, "metric_id", ""),
                    content=getattr(segment, "content", "") or "",
                )
            )
        results = self._collapse_results(results)
        observed_matches = len(results)
        return results[:_internal_pool_size(self.config, metric, observed_matches=observed_matches, channel="keyword")]

    def search_keywords_in_text(
        self,
        text: str,
        keywords: Sequence[str],
        case_sensitive: bool = False,
    ) -> List[Tuple[str, List[int]]]:
        """Simple phrase search kept for compatibility with older callers."""
        results = []
        flags = 0 if case_sensitive else re.IGNORECASE
        for keyword in keywords:
            pattern = re.escape(str(keyword or ""))
            if not pattern:
                continue
            matches = [(m.start(), m.end()) for m in re.finditer(pattern, text or "", flags)]
            if matches:
                results.append((str(keyword), matches))
        return results

    def _score_exact_segment(
        self,
        segment,
        metric: ESGMetric,
        profile: MetricRetrievalProfile,
        base: float,
    ) -> float:
        score = float(base)
        score += _segment_structure_bonus(
            segment,
            expected_unit=getattr(metric, "unit", None),
            prefer_narrative=not _is_quantitative_metric(metric),
        )
        score += _metric_evidence_quality_adjustment(metric, segment, profile.anchor_terms)
        score += _topic_relevance_adjustment(metric, getattr(segment, "content", "") or "")
        return _clamp_score(score)

    def _build_table_row_lookup(self, segments: Sequence[Any]) -> Dict[Tuple[str, int], Any]:
        lookup: Dict[Tuple[str, int], Any] = {}
        for segment in segments:
            if str(getattr(segment, "segment_type", "") or "").lower() != "table_row":
                continue
            key = self._table_row_key(segment)
            if key is not None:
                lookup[key] = segment
        return lookup

    @staticmethod
    def _cache_signature(
        report_content: ReportContent,
        metric_corpus: Optional[MetricRetrievalCorpus] = None,
    ) -> Tuple[str, int, int, str]:
        # A table-repair pass can mutate existing segment objects without
        # replacing the list.  A content revision detects that case whereas
        # the former (id(list), len(list)) signature remained stale.
        return (
            *document_content_revision(report_content),
            str(getattr(metric_corpus, "corpus_signature", "") or "legacy"),
        )

    def _get_table_row_lookup(
        self,
        report_content: ReportContent,
        segments: Sequence[Any],
    ) -> Dict[Tuple[str, int], Any]:
        source_segments = report_content.document_content.segments
        signature = self._cache_signature(report_content)
        cached = getattr(report_content, "_keyword_table_row_cache", None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == signature:
            return cached[1]
        lookup = self._build_table_row_lookup(segments)
        try:
            object.__setattr__(report_content, "_keyword_table_row_cache", (signature, lookup))
        except Exception:
            pass
        return lookup

    def _get_bm25_corpus(
        self,
        report_content: ReportContent,
    ) -> Tuple[List[Any], List[List[str]], Counter[str], float]:
        """Historical canonical corpus accessor kept for non-metric callers/tests."""
        segments = list(report_content.document_content.segments)
        signature = self._cache_signature(report_content)
        cached = getattr(report_content, "_keyword_bm25_cache", None)
        if isinstance(cached, tuple) and len(cached) == 5 and cached[0] == signature:
            return cached[1], cached[2], cached[3], cached[4]

        doc_tokens = [self._segment_tokens(segment) for segment in segments]
        avg_len = sum(len(tokens) for tokens in doc_tokens) / max(len(doc_tokens), 1)
        doc_freq: Counter[str] = Counter()
        for tokens in doc_tokens:
            doc_freq.update(set(tokens))
        cache_value = (signature, segments, doc_tokens, doc_freq, avg_len)
        try:
            object.__setattr__(report_content, "_keyword_bm25_cache", cache_value)
        except Exception:
            pass
        return segments, doc_tokens, doc_freq, avg_len

    def _get_metric_bm25_corpus(
        self,
        report_content: ReportContent,
    ) -> Tuple[List[Any], List[List[str]], Counter[str], float]:
        segments, metric_corpus = self._search_segments(report_content)
        signature = self._cache_signature(report_content, metric_corpus)
        cached = getattr(report_content, "_metric_keyword_bm25_cache", None)
        if isinstance(cached, tuple) and len(cached) == 5 and cached[0] == signature:
            return cached[1], cached[2], cached[3], cached[4]

        doc_tokens = [self._segment_tokens(segment) for segment in segments]
        avg_len = sum(len(tokens) for tokens in doc_tokens) / max(len(doc_tokens), 1)
        doc_freq: Counter[str] = Counter()
        for tokens in doc_tokens:
            doc_freq.update(set(tokens))
        cache_value = (signature, segments, doc_tokens, doc_freq, avg_len)
        try:
            object.__setattr__(
                report_content,
                "_metric_keyword_bm25_cache",
                cache_value,
            )
        except Exception:
            pass
        return segments, doc_tokens, doc_freq, avg_len

    def _table_row_key(self, segment: Any) -> Optional[Tuple[str, int]]:
        table_id = getattr(segment, "source_table_id", None)
        row_index = None
        structured = getattr(segment, "structured_data", None)
        if isinstance(structured, dict):
            table_id = table_id or structured.get("table_id") or structured.get("source_table_id")
            row_index = structured.get("row_index", structured.get("row_idx"))
        return table_row_scope_key(segment, table_id=table_id, row_index=row_index)

    def _upgrade_code_cell_to_table_row(self, segment: Any, row_lookup: Dict[Tuple[str, int], Any]) -> Tuple[Any, str, bool]:
        """Return full table-row evidence when exact Code matched a cell.

        Exact code search often first hits a `table_cell` containing only the
        SASB code.  Evidence extraction needs the full row with Metric/Value/Unit,
        so promote the result to the corresponding `table_row` whenever possible.
        """
        seg_type = str(getattr(segment, "segment_type", "") or "").lower()
        content = getattr(segment, "content", "") or ""
        if seg_type != "table_cell":
            return segment, content, False
        key = self._table_row_key(segment)
        if key is not None and key in row_lookup:
            row_segment = row_lookup[key]
            return row_segment, getattr(row_segment, "content", "") or content, True
        structured = getattr(segment, "structured_data", None)
        if isinstance(structured, dict):
            row_text = str(structured.get("row_text") or "").strip()
            if row_text:
                return segment, row_text, True
        return segment, content, False

    def _query_tokens(self, profile: MetricRetrievalProfile) -> List[str]:
        tokens: List[str] = []
        for term in profile.bm25_terms:
            tokens.extend(tokenize_metric_text(term))
        # Keep deterministic order and avoid huge BM25 queries.
        seen = set()
        out = []
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= 48:
                break
        return out

    def _identity_query_tokens(self, profile: MetricRetrievalProfile) -> set[str]:
        """Tokens that can qualify BM25 evidence before topic-only bonuses."""
        values = [
            profile.metric_name,
            profile.metric_code,
            profile.definition,
            *profile.aliases,
        ]
        identity_tokens = {
            token
            for value in values
            for token in tokenize_metric_text(value)
        }
        identity_tokens.difference_update(tokenize_metric_text(profile.topic))
        return identity_tokens

    def _segment_tokens(self, segment) -> List[str]:
        structured_parts = [
            getattr(segment, "content", "") or "",
            getattr(segment, "row_header", "") or "",
            getattr(segment, "col_header", "") or "",
            getattr(segment, "value_text", "") or "",
            getattr(segment, "unit", "") or "",
        ]
        structured_data = getattr(segment, "structured_data", None)
        if isinstance(structured_data, dict):
            for key in ("table_title", "table_id", "row_header", "column_headers", "caption"):
                value = structured_data.get(key)
                if isinstance(value, list):
                    structured_parts.extend(str(v) for v in value)
                elif value:
                    structured_parts.append(str(value))
        return tokenize_metric_text(" ".join(structured_parts))

    def _bm25_score(
        self,
        counts: Counter[str],
        doc_len: int,
        query_tokens: Sequence[str],
        doc_freq: Counter[str],
        n_docs: int,
        avg_len: float,
    ) -> float:
        k1 = 1.4
        b = 0.72
        score = 0.0
        for token in query_tokens:
            tf = counts.get(token, 0)
            if tf <= 0:
                continue
            df = max(1, doc_freq.get(token, 0))
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1.0 - b + b * (doc_len / max(avg_len, 1.0)))
            score += idf * (tf * (k1 + 1.0)) / max(denom, 1e-9)
        return float(score)

    # Legacy helper kept for callers that still use the old class method.
    def _calculate_keyword_score(
        self,
        segment,
        metric: ESGMetric,
        matched_keywords: List[str],
        total_matches: int,
        weight_map: Optional[Dict[str, float]] = None,
        anchor_terms: Optional[List[str]] = None,
    ) -> float:
        weight_map = weight_map or {kw: 1.0 for kw in matched_keywords}
        total_weight = max(0.0001, sum(float(v or 0.0) for v in weight_map.values()))
        matched_weight = sum(float(weight_map.get(term, 0.0)) for term in set(matched_keywords))
        unique_match_score = matched_weight / total_weight
        density_bonus = min(0.18, 0.02 * max(0, total_matches - len(set(matched_keywords))))
        structure_bonus = _segment_structure_bonus(segment, expected_unit=getattr(metric, "unit", None), prefer_narrative=not _is_quantitative_metric(metric))
        relevance_adjustment = _qualitative_relevance_adjustment(
            metric,
            getattr(segment, "content", "") or "",
            anchor_terms or [],
            getattr(segment, "segment_type", ""),
            segment=segment,
        )
        return float(max(0.0, min(1.0, unique_match_score + density_bonus + structure_bonus + relevance_adjustment)))

    def _segment_structure_bonus(self, segment, expected_unit: Optional[str] = None, prefer_narrative: bool = False) -> float:
        return _segment_structure_bonus(segment, expected_unit=expected_unit, prefer_narrative=prefer_narrative)
