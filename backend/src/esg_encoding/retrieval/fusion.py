"""RRF fusion and exact-metric reranking for metric-centric RAG."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .metric_profile import MetricRetrievalProfile, best_alias_matches, content_contains_alias
from .scoring import (
    ESGMetric,
    ReportContent,
    RetrievalResult,
    _clamp_score,
    _count_anchor_hits,
    _is_quantitative_metric,
    _metric_evidence_quality_adjustment,
    _normalize_text_for_match,
    _segment_structure_bonus,
    _target_window_size,
    _topic_relevance_adjustment,
)

_CHANNEL_WEIGHTS = {
    # Exact metric-code hits are intentionally dominant.  A SASB/GRI/CDP code
    # match is a stronger canonical-metric signal than alias, BM25 or dense
    # similarity and should not be pushed down by broad semantic matches.
    "exact_code": 8.00,
    "exact_alias": 2.65,
    "bm25": 1.35,
    "keyword": 1.10,
    "dense": 1.00,
    "semantic": 1.00,
    "semantic+rerank": 1.45,
    "linked_page": 9.00,
}
_CHANNEL_PRIORITY = {
    "exact_code": 2.00,
    "exact_alias": 0.86,
    "bm25": 0.62,
    "keyword": 0.58,
    "semantic+rerank": 0.52,
    "dense": 0.42,
    "semantic": 0.42,
    "linked_page": 2.20,
}


def _channel_weight(channel: str) -> float:
    if "linked_page" in channel:
        return float(_CHANNEL_WEIGHTS["linked_page"])
    return float(_CHANNEL_WEIGHTS.get(channel, _CHANNEL_WEIGHTS.get(channel.split("+")[-1], 1.0)))


def _channel_priority(channel: str) -> float:
    if "linked_page" in channel:
        return float(_CHANNEL_PRIORITY["linked_page"])
    return float(_CHANNEL_PRIORITY.get(channel, _CHANNEL_PRIORITY.get(channel.split("+")[-1], 0.35)))


def _merge_keywords(values: Iterable[Sequence[str]]) -> List[str]:
    seen = set()
    merged: List[str] = []
    for items in values:
        for item in items or []:
            cleaned = str(item or "").strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(cleaned)
    return merged[:24]


def rrf_fuse(
    channel_results: Mapping[str, Sequence[RetrievalResult]],
    k: int = 60,
) -> List[RetrievalResult]:
    """Fuse exact, lexical and semantic results using weighted RRF.

    Scores are intentionally rank-driven so dense similarity cannot outrank exact
    SASB/GRI code or exact alias matches merely because a vector score is high.
    """
    accum: Dict[str, Dict[str, object]] = {}
    for channel, results in channel_results.items():
        weight = _channel_weight(channel)
        priority = _channel_priority(channel)
        for rank, result in enumerate(results or [], start=1):
            if not getattr(result, "segment_id", None):
                continue
            bucket = accum.setdefault(
                result.segment_id,
                {
                    "result": result,
                    "score": 0.0,
                    "channels": [],
                    "keywords": [],
                    "priority": 0.0,
                    "best_source_score": 0.0,
                    "link_source_page": None,
                    "link_target_page": None,
                    "link_anchor_text": None,
                    "link_source_segment_id": None,
                },
            )
            bucket["score"] = float(bucket["score"]) + weight / float(k + rank)
            bucket["priority"] = max(float(bucket["priority"]), priority)
            bucket["best_source_score"] = max(float(bucket["best_source_score"]), float(result.score or 0.0))
            bucket["channels"].append(channel)
            bucket["keywords"].append(list(result.matched_keywords or []))
            if getattr(result, "link_target_page", None) is not None:
                bucket["link_source_page"] = getattr(result, "link_source_page", None)
                bucket["link_target_page"] = getattr(result, "link_target_page", None)
                bucket["link_anchor_text"] = getattr(result, "link_anchor_text", None)
                bucket["link_source_segment_id"] = getattr(result, "link_source_segment_id", None)
            existing = bucket["result"]
            if priority > _channel_priority(getattr(existing, "retrieval_type", "")) or float(result.score or 0.0) > float(getattr(existing, "score", 0.0) or 0.0):
                bucket["result"] = result

    if not accum:
        return []

    max_raw = max(float(v["score"]) for v in accum.values()) or 1.0
    fused: List[RetrievalResult] = []
    for segment_id, bucket in accum.items():
        base: RetrievalResult = bucket["result"]  # type: ignore[assignment]
        channels = list(dict.fromkeys(str(c) for c in bucket["channels"]))
        normalized_rrf = float(bucket["score"]) / max_raw
        priority = float(bucket["priority"])
        source_score = float(bucket["best_source_score"])
        fused_score = _clamp_score(0.50 * normalized_rrf + 0.35 * priority + 0.15 * source_score)
        score_breakdown = dict(getattr(base, "score_breakdown", None) or {})
        score_breakdown["rrf"] = fused_score
        fused.append(
            base.model_copy(
                update={
                    "segment_id": segment_id,
                    "score": fused_score,
                    "retrieval_type": "rrf:" + "+".join(channels),
                    "matched_keywords": _merge_keywords(bucket["keywords"]),  # type: ignore[arg-type]
                    "link_source_page": bucket["link_source_page"] or base.link_source_page,
                    "link_target_page": bucket["link_target_page"] or base.link_target_page,
                    "link_anchor_text": bucket["link_anchor_text"] or base.link_anchor_text,
                    "link_source_segment_id": (
                        bucket["link_source_segment_id"]
                        or base.link_source_segment_id
                    ),
                    "score_breakdown": score_breakdown,
                }
            )
        )
    fused.sort(key=lambda item: item.score, reverse=True)
    return fused


def _has_number(text: str) -> bool:
    return bool(re.search(r"-?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|percentage|[A-Za-zµμ³₂/.-]+)?", text or ""))


def exact_metric_rerank(
    fused_results: Sequence[RetrievalResult],
    metric: ESGMetric,
    profile: MetricRetrievalProfile,
    report_content: Optional[ReportContent] = None,
    top_k: Optional[int] = None,
) -> List[RetrievalResult]:
    """Rerank fused chunks for canonical metric evidence, not broad topic similarity."""
    if not fused_results:
        return []

    segment_lookup = {}
    if report_content is not None:
        segment_lookup = {
            getattr(segment, "segment_id", None): segment
            for segment in getattr(report_content.document_content, "segments", [])
            if getattr(segment, "segment_id", None)
        }

    reranked: List[RetrievalResult] = []
    for result in fused_results:
        # Rank against the precise internal retrieval view while preserving the
        # complete canonical evidence block on the public result.
        content = result.matched_content or result.content or ""
        lowered = _normalize_text_for_match(content)
        segment = segment_lookup.get(result.segment_id)
        direct = 0.0
        matched = list(result.matched_keywords or [])

        code_match = bool(profile.metric_code and any(pattern.search(content) for pattern in profile.exact_code_patterns))
        if code_match:
            direct += 0.70
            matched.append(profile.metric_code)

        alias_matches = best_alias_matches(content, [a for a in profile.aliases if a != profile.metric_code], limit=8)
        if alias_matches:
            direct += min(0.34, 0.12 + 0.055 * len(alias_matches))
            matched.extend(alias_matches)

        anchor_hits = _count_anchor_hits(lowered, profile.anchor_terms)
        direct += min(0.18, 0.028 * anchor_hits)

        negative_hits = _count_anchor_hits(lowered, [normalize for normalize in profile.negative_anchor_terms])
        if negative_hits and anchor_hits < 2 and not alias_matches:
            direct -= min(0.18, 0.06 * negative_hits)

        if segment is not None:
            direct += _segment_structure_bonus(
                segment,
                expected_unit=getattr(metric, "unit", None),
                prefer_narrative=not _is_quantitative_metric(metric),
            )
            seg_type = str(getattr(segment, "segment_type", "") or "").lower().replace("_", "-")
            preferred = {str(x).lower().replace("_", "-") for x in (profile.preferred_chunk_types or [])}
            if seg_type in preferred:
                direct += 0.06
            direct += _metric_evidence_quality_adjustment(metric, segment, profile.anchor_terms)
            direct += _topic_relevance_adjustment(metric, content)

        if _is_quantitative_metric(metric):
            if _has_number(content):
                direct += 0.08
            else:
                direct -= 0.10
        else:
            process_terms = ("policy", "process", "procedure", "governance", "oversight", "risk management", "approach", "program", "programme")
            if any(term in lowered for term in process_terms):
                direct += 0.05

        if "exact_code" in result.retrieval_type:
            direct += 0.22
            code_match = True
        elif "linked_page" in result.retrieval_type:
            direct += 0.24
        elif "exact_alias" in result.retrieval_type:
            direct += 0.07
        elif "semantic" in result.retrieval_type and not alias_matches and anchor_hits == 0:
            direct -= 0.08

        final_score = _clamp_score(0.50 * float(result.score or 0.0) + 0.50 * _clamp_score(direct))
        score_breakdown = dict(getattr(result, "score_breakdown", None) or {})
        score_breakdown["exact_metric_rerank"] = final_score
        reranked.append(
            result.model_copy(
                update={
                    "score": final_score,
                    "retrieval_type": (
                        result.retrieval_type + "+exact_metric_rerank"
                        if "exact_metric_rerank" not in result.retrieval_type
                        else result.retrieval_type
                    ),
                    "matched_keywords": _merge_keywords([matched]),
                    "score_breakdown": score_breakdown,
                }
            )
        )

    reranked.sort(key=lambda item: item.score, reverse=True)
    if top_k is None:
        top_k = _target_window_size(getattr(metric, "config", None) or object(), metric, observed_matches=len(reranked)) if False else len(reranked)
    return reranked[: max(1, int(top_k or len(reranked)))]
