"""
Dual-Channel Metrics Retrieval Module

This module implements dual-channel metric retrieval, including:
1. Keyword Retrieval - Keyword-based retrieval
2. Semantic Retrieval - Semantic-based retrieval
"""

import os
import re
import math
import threading
from typing import Dict, List, Optional, Union, Tuple
from loguru import logger

from .hipporag.settings import HippoRAGSettings
from ..embedding_settings import get_configured_rerank_model_name

from ..models import (
    ESGMetric, SemanticExpansion, MetricCollection, ReportContent,
    RetrievalResult, MetricRetrievalResult, ProcessingConfig
)
from ..exceptions import ESGEncodingError, ContentEmbeddingError

_GENERIC_METRIC_TERMS = {
    "description", "discussion", "approach", "management", "number", "percentage", "percent", "pct", "amount", "total",
    "weight", "list", "countries", "products", "services", "employees", "facilities", "metric",
    "associated", "including", "resulting", "data", "user", "users", "information", "risks", "risk",
    "process", "policies", "practices", "related", "activity", "activities", "topics", "topic",
    "sustainability", "disclosure", "metrics", "all", "other", "core", "required", "eligible",
}


def visual_result_fields(segment) -> Dict[str, object]:
    data = getattr(segment, "structured_data", None)
    data = data if isinstance(data, dict) else {}
    fields = {
        "structure_confidence": getattr(segment, "structure_confidence", None) or data.get("structure_confidence"),
        "ocr_confidence": getattr(segment, "ocr_confidence", None) or data.get("ocr_confidence"),
        "header_path": getattr(segment, "header_path", None) or data.get("header_path") or [],
        "rowspan": getattr(segment, "rowspan", 1),
        "colspan": getattr(segment, "colspan", 1),
        "parse_pass": getattr(segment, "parse_pass", 1),
        "review_status": getattr(segment, "review_status", None) or data.get("review_status"),
        "conflicts": getattr(segment, "conflicts", None) or data.get("conflicts") or [],
    }
    if not data.get("asset_id"):
        return fields
    fields.update({
        "evidence_type": data.get("evidence_type") or getattr(segment, "segment_type", None),
        "asset_id": data.get("asset_id"),
        "bbox": data.get("bbox"),
        "caption": data.get("caption") or data.get("summary"),
        "confidence": data.get("confidence"),
        "chart_data": data.get("chart_data"),
    })
    return fields

_ENVIRONMENTAL_NOISE_TERMS = {
    "emissions", "ghg", "scope 1", "scope 2", "scope 3", "water", "waste", "renewable", "electricity",
    "energy", "supplier environmental", "recycled", "recycling", "e-waste", "potable", "mwh", "gj",
    "co2e", "tco2e", "landfilled", "environmental program", "emission reduction", "supplier emissions",
}

_FOCUS_TERMS = {
    "privacy_security": {"privacy", "data security", "cybersecurity", "breach", "breaches", "vulnerability", "user information", "law enforcement", "security", "targeted advertising", "monitoring", "censoring"},
    "workforce": {"gender", "diversity", "executive", "non-executive", "technical employees", "employee engagement", "work visa", "people leaders", "leadership", "management"},
    "operations": {"service disruption", "downtime", "business continuity", "performance issues", "operations", "cloud-based", "data storage", "data processing capacity", "licences", "subscriptions"},
    "supply_chain": {"tier 1", "rba", "vap", "supplier facilities", "corrective action", "non-conformance", "audit", "high-risk facilities"},
    "product_compliance": {"epeat", "iec 62474", "energy efficiency certification", "declarable substances", "critical materials", "end-of-life", "e-waste"},
}


_SOFT_CATEGORY_ALIASES = {
    "executive management": ["executive leadership", "executive officers", "senior leadership", "c-suite", "senior officials"],
    "non-executive management": ["people leaders", "people managers", "management roles", "management", "leaders"],
    "technical employees": ["technical workforce", "engineers", "engineering", "developers", "technical talent", "r&d employees"],
    "all other employees": ["global workforce", "workforce", "employees", "remaining employees", "broader workforce"],
    "data security risks in products": ["product security", "secure development lifecycle", "secure by design", "sbom", "passwordless authentication", "data sanitization"],
}


def _soft_metric_aliases(metric: ESGMetric) -> List[str]:
    text = " ".join([str(getattr(metric, "metric_name", "") or ""), str(getattr(metric, "sasb_topic", "") or "")]).lower()
    aliases: List[str] = []
    for needle, values in _SOFT_CATEGORY_ALIASES.items():
        if needle in text:
            aliases.extend(values)
    return list(dict.fromkeys([alias for alias in aliases if alias]))


def _is_quantitative_metric(metric: ESGMetric) -> bool:
    category = str(getattr(metric, "sasb_category", "") or "").strip().lower()
    metric_type = str(getattr(metric, "sasb_type", "") or "").strip().lower()
    unit = str(getattr(metric, "unit", "") or "").strip()
    return bool(unit) or category == "quantitative" or "quantitative" in metric_type


def _metric_complexity_score(metric: ESGMetric) -> int:
    metric_name = str(getattr(metric, "metric_name", "") or "")
    topic = str(getattr(metric, "sasb_topic", "") or "")
    definition = str(getattr(metric, "definition", "") or getattr(metric, "description", "") or "")
    keywords = list(getattr(metric, "keywords", None) or [])
    token_count = len(re.findall(r"[A-Za-z0-9]{3,}", " ".join([metric_name, topic, definition])))
    complexity = 0
    complexity += min(6, token_count // 14)
    complexity += min(5, len(keywords) // 4)
    if any(ch in metric_name for ch in ["(", ")", "/", "%"]):
        complexity += 1
    return complexity


def _compute_target_window(metric: ESGMetric, observed_matches: int = 0, base_top_k: int = 10) -> int:
    """Dynamic window with no hard upper cap. Grows with metric complexity and available evidence."""
    base = 22 if _is_quantitative_metric(metric) else 14
    complexity_bonus = _metric_complexity_score(metric)
    observed_matches = max(0, int(observed_matches or 0))
    richness_bonus = 0
    if observed_matches > 0:
        richness_bonus += int(math.log1p(observed_matches) * (6 if _is_quantitative_metric(metric) else 5))
        richness_bonus += int(math.sqrt(observed_matches) / (4 if _is_quantitative_metric(metric) else 5))
        if observed_matches >= 120:
            richness_bonus += 4
        if observed_matches >= 300:
            richness_bonus += 6
        if observed_matches >= 800:
            richness_bonus += 8
    return max(11, max(int(base_top_k or 10), base) + complexity_bonus + richness_bonus)


def _positive_float_env(name: str, default: float, minimum: float = 0.01) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value)


def compute_dynamic_top_k(qualified_count: int) -> int:
    """Return the small final evidence window derived from qualified candidates.

    The minimum is a target, not padding: when fewer qualified candidates exist,
    all real candidates are returned. Above the minimum the window grows only
    logarithmically and is never larger than the available evidence.
    """
    q = max(0, int(qualified_count or 0))
    if q == 0:
        return 0
    minimum = max(
        1,
        int(_positive_float_env("REPORT_DYNAMIC_TOPK_MIN", 46, minimum=1)),
    )
    if q < minimum:
        return q
    factor = _positive_float_env("REPORT_DYNAMIC_TOPK_LOG_FACTOR", 4.0)
    target = math.ceil(minimum + factor * math.log2(q / minimum))
    return min(q, max(minimum, target))


def compute_rerank_pool_k(qualified_count: int, target_k: Optional[int] = None) -> int:
    """Return the bounded candidate pool sent to Qwen3 reranking."""
    q = max(0, int(qualified_count or 0))
    if q == 0:
        return 0
    target = compute_dynamic_top_k(q) if target_k is None else max(0, int(target_k))
    multiplier = _positive_float_env("REPORT_RERANK_POOL_MULTIPLIER", 1.5, minimum=1.0)
    return min(q, max(target, math.ceil(target * multiplier)))


def _compute_internal_pool(metric: ESGMetric, observed_matches: int = 0, base_top_k: int = 10, channel: str = "keyword") -> int:
    target = _compute_target_window(metric, observed_matches=observed_matches, base_top_k=base_top_k)
    is_quant = _is_quantitative_metric(metric)
    if channel == "semantic":
        floor = 180 if is_quant else 96
        multiplier = 4 if is_quant else 3
        overflow = max(0, observed_matches // (5 if is_quant else 7))
    else:
        floor = 120 if is_quant else 72
        multiplier = 3 if is_quant else 2
        overflow = max(0, observed_matches // (7 if is_quant else 10))
    return max(target * multiplier + overflow, floor)


def _target_window_size(config: ProcessingConfig, metric: ESGMetric, observed_matches: int = 0) -> int:
    base_top_k = int(getattr(config, "top_k", 10) or 10)
    return _compute_target_window(metric, observed_matches=observed_matches, base_top_k=base_top_k)


def _internal_pool_size(config: ProcessingConfig, metric: ESGMetric, observed_matches: int = 0, channel: str = "keyword") -> int:
    base_top_k = int(getattr(config, "top_k", 10) or 10)
    return _compute_internal_pool(metric, observed_matches=observed_matches, base_top_k=base_top_k, channel=channel)


def _segment_structure_bonus(segment, expected_unit: Optional[str] = None, prefer_narrative: bool = False) -> float:
    bonus = 0.0
    seg_type = str(getattr(segment, "segment_type", "") or "").lower()
    content = str(getattr(segment, "content", "") or "")
    if prefer_narrative:
        if seg_type == "heading":
            bonus += 0.12
        elif seg_type == "paragraph_cluster":
            bonus += 0.16
        elif seg_type in {"body_text", "text"}:
            bonus += 0.08
        elif seg_type == "table_row":
            bonus += 0.03
        elif seg_type == "table_cell":
            bonus += 0.01
        elif seg_type == "ocr_text":
            bonus -= 0.01
    else:
        if seg_type == "table_cell":
            bonus += 0.14
        elif seg_type == "table_row":
            bonus += 0.08
        elif seg_type == "table":
            bonus += 0.04
        elif seg_type == "chart_data":
            bonus += 0.15
        elif seg_type == "chart":
            bonus += 0.09
        elif seg_type in {"figure", "image_text"}:
            bonus += 0.025
        elif seg_type == "paragraph_cluster":
            bonus += 0.04
        elif seg_type == "heading":
            bonus += 0.03
        elif seg_type == "body_text":
            bonus += 0.02
        elif seg_type == "ocr_text":
            bonus -= 0.01
    if re.search(r"-?\d[\d,]*(?:\.\d+)?", content):
        bonus += 0.04
    if getattr(segment, "row_header", None):
        bonus += 0.05
    if getattr(segment, "col_header", None):
        bonus += 0.04
    if getattr(segment, "value_text", None):
        bonus += 0.06
    return bonus


def _normalize_text_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _extract_metric_anchor_terms(metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None, max_terms: int = 28) -> List[str]:
    raw_terms: List[str] = []
    for field in [getattr(metric, "metric_name", None), getattr(metric, "metric_code", None), getattr(metric, "definition", None), getattr(metric, "description", None)]:
        if field:
            raw_terms.append(str(field))
    for term in (getattr(metric, "keywords", None) or []):
        raw_terms.append(str(term))
    if semantic_expansion is not None:
        if getattr(semantic_expansion, "semantic_description", None):
            raw_terms.append(str(getattr(semantic_expansion, "semantic_description", "")))
        for term in (getattr(semantic_expansion, "expanded_keywords", None) or []):
            raw_terms.append(str(term))
    for alias in _soft_metric_aliases(metric):
        raw_terms.append(str(alias))

    anchors: List[str] = []
    seen = set()
    for raw in raw_terms:
        raw = _normalize_text_for_match(raw)
        if not raw:
            continue
        if len(raw) <= 32 and raw not in _GENERIC_METRIC_TERMS and not raw.isdigit():
            if raw not in seen:
                anchors.append(raw)
                seen.add(raw)
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", raw):
            if token in _GENERIC_METRIC_TERMS or token.isdigit():
                continue
            if token not in seen:
                anchors.append(token)
                seen.add(token)
            if len(anchors) >= max_terms:
                return anchors
        if len(anchors) >= max_terms:
            return anchors
    return anchors[:max_terms]


def _detect_metric_focus(metric: ESGMetric) -> str:
    text = " ".join([
        str(getattr(metric, "metric_name", "") or ""),
        str(getattr(metric, "sasb_topic", "") or ""),
        str(getattr(metric, "definition", "") or getattr(metric, "description", "") or ""),
    ]).lower()
    for focus, terms in _FOCUS_TERMS.items():
        if any(term in text for term in terms):
            return focus
    return "general"


def _count_anchor_hits(text: str, anchors: List[str]) -> int:
    lowered = _normalize_text_for_match(text)
    hits = 0
    for anchor in anchors:
        if anchor and anchor in lowered:
            hits += 1
    return hits


def _topic_relevance_adjustment(metric: ESGMetric, content: str) -> float:
    """Use the industry topic only as a bounded secondary ranking signal."""
    topic = _normalize_text_for_match(getattr(metric, "sasb_topic", "") or "")
    if not topic:
        return 0.0
    lowered = _normalize_text_for_match(content)
    if topic in lowered:
        return 0.08
    topic_terms = {
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", topic)
        if token not in _GENERIC_METRIC_TERMS
    }
    if not topic_terms:
        return 0.0
    hits = sum(1 for token in topic_terms if token in lowered)
    if hits < 2:
        return 0.0
    return min(0.06, 0.015 * hits)


def _qualitative_relevance_adjustment(
    metric: ESGMetric,
    content: str,
    anchors: List[str],
    segment_type: str = "",
    segment=None,
) -> float:
    """Return the bounded qualitative-ranking adjustment for one segment.

    ``segment`` is optional to preserve compatibility with callers that only
    have text and a segment type.  When it is available, its review metadata is
    used to prefer verified evidence and penalize evidence that still needs
    review.
    """
    category = str(getattr(metric, "sasb_category", "") or "").strip().lower()
    metric_type = str(getattr(metric, "sasb_type", "") or "").strip().lower()
    unit = str(getattr(metric, "unit", "") or "").strip()
    is_quantitative = bool(unit) or category == "quantitative" or "quantitative" in metric_type
    lowered = _normalize_text_for_match(content)
    anchor_hits = _count_anchor_hits(lowered, anchors)
    seg_type = str(segment_type or "").lower()
    if is_quantitative:
        return min(0.10, anchor_hits * 0.02)

    adjustment = 0.0
    structured = getattr(segment, "structured_data", None) if segment is not None else None
    structured = structured if isinstance(structured, dict) else {}
    review_status = str(
        (getattr(segment, "review_status", None) if segment is not None else None)
        or structured.get("review_status")
        or ""
    ).strip().lower()
    if review_status == "verified":
        adjustment += 0.04
    elif review_status == "needs_review":
        adjustment -= 0.12
    if anchor_hits > 0:
        adjustment += min(0.34, 0.07 * anchor_hits)
    else:
        adjustment -= 0.12

    narrative_phrases = [
        "policy", "policies", "practice", "practices", "process", "processes", "approach", "governance",
        "framework", "program", "programme", "oversight", "management", "strategy", "responsibility",
        "responsible", "risk management", "procedure", "procedures", "controls", "control environment"
    ]
    if any(term in lowered for term in narrative_phrases):
        adjustment += 0.10

    if seg_type == "paragraph_cluster":
        adjustment += 0.16
    elif seg_type == "heading":
        adjustment += 0.08
    elif seg_type in {"body_text", "text", "ocr_text"}:
        adjustment += 0.06
    elif seg_type == "table_row":
        adjustment -= 0.02
    elif seg_type == "table_cell":
        adjustment -= 0.12

    focus = _detect_metric_focus(metric)
    focus_terms = _FOCUS_TERMS.get(focus, set())
    focus_hits = sum(1 for term in focus_terms if term in lowered)
    env_noise_hits = sum(1 for term in _ENVIRONMENTAL_NOISE_TERMS if term in lowered)
    if focus != "general":
        if focus_hits > 0:
            adjustment += min(0.24, 0.07 * focus_hits)
        elif env_noise_hits >= 2 and anchor_hits == 0:
            adjustment -= 0.34

    alias_hits = sum(1 for alias in _soft_metric_aliases(metric) if alias in lowered)
    if alias_hits > 0:
        adjustment += min(0.18, 0.06 * alias_hits)
    return adjustment


def _metric_evidence_quality_adjustment(metric: ESGMetric, segment, anchors: List[str]) -> float:
    """Small CPU-only evidence-quality signal for final retrieval ranking.

    This improves ranking quality without increasing model size, GPU batch size,
    or passage re-encoding. It only uses already available text/metadata.
    """
    content = str(getattr(segment, "content", "") or "")
    lowered = _normalize_text_for_match(content)
    anchor_hits = _count_anchor_hits(lowered, anchors)
    has_number = bool(re.search(r"-?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|percentage|[a-zA-Zµμ³₂/.-]+)?", content))
    adjustment = 0.0

    if _is_quantitative_metric(metric):
        if has_number:
            adjustment += 0.06
        else:
            adjustment -= 0.10
        if anchor_hits >= 2:
            adjustment += 0.04
        elif anchor_hits == 0:
            adjustment -= 0.05
        future_only_terms = ("target", "goal", "aim", "aspire", "by 2030", "by 2040", "by 2050", "commitment")
        if any(term in lowered for term in future_only_terms) and not re.search(r"\b20(?:1\d|2[0-9])\b|fy\s?2[0-9]|fiscal year|during the year|reported", lowered):
            adjustment -= 0.05
    else:
        if anchor_hits >= 2:
            adjustment += 0.05
        elif anchor_hits == 0:
            adjustment -= 0.06
        process_terms = ("policy", "process", "procedure", "control", "governance", "oversight", "risk management", "approach", "program", "programme")
        if any(term in lowered for term in process_terms):
            adjustment += 0.04

    focus = _detect_metric_focus(metric)
    if focus != "general":
        focus_terms = _FOCUS_TERMS.get(focus, set())
        focus_hits = sum(1 for term in focus_terms if term in lowered)
        noise_hits = sum(1 for term in _ENVIRONMENTAL_NOISE_TERMS if term in lowered)
        if focus_hits > 0:
            adjustment += min(0.05, 0.02 * focus_hits)
        elif noise_hits >= 2 and anchor_hits == 0:
            adjustment -= 0.08

    return float(max(-0.18, min(0.18, adjustment)))


def _clamp_score(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _is_narrative_segment(segment) -> bool:
    seg_type = str(getattr(segment, "segment_type", "") or "").lower()
    return seg_type in {"text", "body_text", "heading", "paragraph_cluster", "ocr_text"}


def _ordered_segments(report_content: ReportContent):
    return sorted(
        report_content.document_content.segments,
        key=lambda seg: (
            int(getattr(seg, "page_number", 0) or 0),
            float(getattr(seg, "position_y", 0.0) or 0.0),
            float(getattr(seg, "position_x", 0.0) or 0.0),
            getattr(seg, "segment_id", ""),
        ),
    )


def _synthesize_qualitative_clusters(report_content: ReportContent, metric: ESGMetric, base_results: List[RetrievalResult], anchors: List[str]) -> List[RetrievalResult]:
    if _is_quantitative_metric(metric):
        return []
    result_ids = {r.segment_id for r in base_results}
    by_id = {getattr(seg, "segment_id", ""): seg for seg in report_content.document_content.segments}
    ordered = _ordered_segments(report_content)
    clusters: List[RetrievalResult] = []
    seen_keys = set()
    for idx, seg in enumerate(ordered):
        if getattr(seg, "segment_id", "") not in result_ids or not _is_narrative_segment(seg):
            continue
        seg_text = getattr(seg, "content", "") or ""
        if _count_anchor_hits(seg_text, anchors) <= 0:
            continue
        chain = [seg]
        for nxt in ordered[idx + 1: idx + 3]:
            if not _is_narrative_segment(nxt):
                break
            if int(getattr(nxt, "page_number", 0) or 0) != int(getattr(seg, "page_number", 0) or 0):
                break
            y_gap = float(getattr(nxt, "position_y", 0.0) or 0.0) - float(getattr(chain[-1], "position_y", 0.0) or 0.0)
            x_gap = abs(float(getattr(nxt, "position_x", 0.0) or 0.0) - float(getattr(chain[-1], "position_x", 0.0) or 0.0))
            if y_gap > 90 or x_gap > 70:
                break
            if _count_anchor_hits(getattr(nxt, "content", "") or "", anchors) <= 0 and str(getattr(nxt, "segment_type", "") or "").lower() != "body_text":
                break
            chain.append(nxt)
        if len(chain) < 2:
            continue
        key = tuple(getattr(s, "segment_id", "") for s in chain)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        content_parts = []
        score_seed = []
        matched_terms = []
        for part in chain:
            label = str(getattr(part, "segment_type", "") or "body_text").lower()
            header = "[Heading]" if label == "heading" else "[Narrative]"
            content_parts.append(f"{header}\n{getattr(part, 'content', '') or ''}")
            part_result = next((r for r in base_results if r.segment_id == getattr(part, "segment_id", "")), None)
            if part_result is not None:
                score_seed.append(float(part_result.score or 0.0))
                matched_terms.extend(part_result.matched_keywords or [])
        cluster_text = "\n\n".join(content_parts).strip()
        if not cluster_text:
            continue
        cluster_score = (sum(score_seed) / len(score_seed)) if score_seed else 0.35
        cluster_score += min(0.18, 0.04 * sum(_count_anchor_hits(getattr(part, "content", "") or "", anchors) for part in chain))
        clusters.append(RetrievalResult(
            segment_id=f"CLUSTER::{key[0]}::{key[-1]}",
            content=cluster_text,
            page_number=int(getattr(seg, "page_number", 0) or 0),
            score=max(0.0, min(1.0, cluster_score)),
            retrieval_type="qualitative_cluster",
            matched_keywords=list(dict.fromkeys(matched_terms))[:12],
            metric_id=metric.metric_id,
        ))
    return clusters


def _rebalance_qualitative_results(metric: ESGMetric, results: List[RetrievalResult], report_content: Optional[ReportContent] = None) -> List[RetrievalResult]:
    if _is_quantitative_metric(metric) or not results:
        return results
    by_id = {getattr(seg, "segment_id", ""): seg for seg in (report_content.document_content.segments if report_content is not None else [])}
    def is_narrative_result(res: RetrievalResult) -> bool:
        if str(getattr(res, "retrieval_type", "") or "") == "qualitative_cluster":
            return True
        seg = by_id.get(res.segment_id)
        if seg is not None:
            return _is_narrative_segment(seg)
        lowered = _normalize_text_for_match(getattr(res, "content", "") or "")
        return lowered.startswith("[heading]") or lowered.startswith("[narrative]")
    narrative = [r for r in results if is_narrative_result(r)]
    other = [r for r in results if not is_narrative_result(r)]
    narrative.sort(key=lambda r: float(r.score or 0.0), reverse=True)
    other.sort(key=lambda r: float(r.score or 0.0), reverse=True)
    merged: List[RetrievalResult] = []
    seen = set()
    for bucket in (narrative, other):
        for item in bucket:
            if item.segment_id in seen:
                continue
            merged.append(item)
            seen.add(item.segment_id)
    return merged

# Export underscore helpers too for split retrievers.
__all__ = [name for name in globals() if not name.startswith('__')]
