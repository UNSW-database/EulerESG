"""
M0: Direct LLM baseline

This baseline bypasses retrieval/keyword enrichment and directly feeds the whole
report text into the LLM, while reusing the same prompt + JSON parsing +
classification logic as the full pipeline (M1).

Key requirement:
- Output must match the same `metric_analyses` fields used by pipeline exports
  so it can be evaluated by the same evaluation script.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from loguru import logger

from ..models import (
    ProcessingConfig,
    ReportContent,
    MetricCollection,
    ESGMetric,
    RetrievalResult,
    MetricRetrievalResult,
    DisclosureStatus,
    DisclosureAnalysis,
    ComplianceAssessment,
)
from ..disclosure_inference import DisclosureInferenceEngine, COMPLIANCE_VALUE_NA


def _compute_summary_and_score(
    metric_analyses: List[DisclosureAnalysis],
) -> Tuple[Dict[DisclosureStatus, int], float]:
    disclosure_summary: Dict[DisclosureStatus, int] = {
        DisclosureStatus.FULLY_DISCLOSED: 0,
        DisclosureStatus.PARTIALLY_DISCLOSED: 0,
        DisclosureStatus.NOT_DISCLOSED: 0,
    }

    for analysis in metric_analyses:
        disclosure_summary[analysis.disclosure_status] = (
            disclosure_summary.get(analysis.disclosure_status, 0) + 1
        )

    total = len(metric_analyses)
    if total <= 0:
        return disclosure_summary, 0.0

    fully = disclosure_summary[DisclosureStatus.FULLY_DISCLOSED]
    partial = disclosure_summary[DisclosureStatus.PARTIALLY_DISCLOSED]
    overall_score = (fully * 1.0 + partial * 0.5) / total
    return disclosure_summary, overall_score


def _build_direct_report_text(report_content: ReportContent, max_report_chars: Optional[int]) -> str:
    # Canonical source: extracted markdown for the report.
    text = getattr(report_content.document_content, "markdown_content", "") or ""
    if not text.strip():
        # Fallback: join segment contents.
        segments = getattr(report_content.document_content, "segments", []) or []
        text = "\n\n".join([getattr(s, "content", "") for s in segments if getattr(s, "content", None)]) or ""

    if max_report_chars is not None and max_report_chars > 0:
        if len(text) > max_report_chars:
            logger.warning(
                f"DirectLLM: truncating report text from {len(text)} to {max_report_chars} characters"
            )
            text = text[:max_report_chars]

    return text


def _apply_output_rules_to_analysis(analysis: DisclosureAnalysis) -> DisclosureAnalysis:
    """
    Apply strict null/evidence rules to keep M0 outputs compatible with schema v1.
    """
    if analysis.disclosure_status == DisclosureStatus.NOT_DISCLOSED:
        return analysis.model_copy(
            update={
                "evidence_segments": [],
                "value": COMPLIANCE_VALUE_NA,
                "context": None,
                "page": None,
                "improvement_suggestions": [],
            }
        )
    return analysis


class DirectLLMBaselineRunner:
    """
    Run M0 (Direct LLM) for all metrics in a MetricCollection.

    Implementation approach:
    - For each metric, build a *single* synthetic retrieval result (one "evidence segment")
      whose content is the whole report.
    - Reuse `DisclosureInferenceEngine._analyze_single_metric` so prompt/JSON parsing/category
      mapping remains identical to M1.
    """

    def __init__(self, config: ProcessingConfig):
        self.config = config
        self.engine = DisclosureInferenceEngine(config)

    def analyze_report(
        self,
        report_content: ReportContent,
        metrics: MetricCollection,
        report_file_path: str = "",
        framework: Optional[str] = None,
        industry: Optional[str] = None,
        semi_industry: Optional[str] = None,
        max_report_chars: Optional[int] = None,
    ) -> ComplianceAssessment:
        report_text = _build_direct_report_text(report_content, max_report_chars=max_report_chars)
        if not report_text.strip():
            raise ValueError("DirectLLM: report text is empty; cannot run baseline.")

        # Stable synthetic evidence segment id for "whole report".
        pseudo_segment_id = f"DIRECT_LLM_FULL_REPORT::{report_content.document_id}"

        metric_analyses: List[DisclosureAnalysis] = []
        for i, metric in enumerate(metrics.metrics):
            logger.info(f"[M0] Analyzing metric {i + 1}/{len(metrics.metrics)}: {metric.metric_name}")

            pseudo_retrieval = RetrievalResult(
                segment_id=pseudo_segment_id,
                content=report_text,
                page_number=1,
                score=1.0,
                retrieval_type="direct_llm",
                matched_keywords=[],
                metric_id=metric.metric_id,
            )

            metric_retrieval = MetricRetrievalResult(
                metric_id=metric.metric_id,
                metric_name=metric.metric_name,
                metric_code=metric.metric_code,
                keyword_results=[],
                semantic_results=[],
                combined_results=[pseudo_retrieval],
                total_matches=1,
            )

            analysis = self.engine._analyze_single_metric(
                metric_retrieval,
                report_content,
                metric=metric,
            )
            analysis = _apply_output_rules_to_analysis(analysis)
            metric_analyses.append(analysis)

        disclosure_summary, overall_score = _compute_summary_and_score(metric_analyses)

        assessment = ComplianceAssessment(
            report_id=report_content.document_id,
            total_metrics_analyzed=len(metric_analyses),
            disclosure_summary=disclosure_summary,
            metric_analyses=metric_analyses,
            overall_compliance_score=overall_score,
            report_file_path=report_file_path,
            framework=framework,
            industry=industry,
            semi_industry=semi_industry,
        )
        return assessment

    def export_assessment_json(
        self,
        assessment: ComplianceAssessment,
        filename: str,
        report_file_path: str = "",
        gri_sector: Optional[str] = None,
        gri_topic: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Export output in the same JSON shape used by current pipeline exports
        (so the same evaluation script can read it).
        """

        metric_analyses_json: List[Dict[str, Any]] = []
        for analysis in assessment.metric_analyses:
            disclosure_status = (
                analysis.disclosure_status.value
                if hasattr(analysis.disclosure_status, "value")
                else analysis.disclosure_status
            )

            item: Dict[str, Any] = {
                "metric_id": analysis.metric_id,
                "metric_name": analysis.metric_name,
                "metric_code": analysis.metric_code,
                "disclosure_status": disclosure_status,
                "reasoning": analysis.reasoning,
                "unit": getattr(analysis, "unit", "") or "",
                "category": getattr(analysis, "category", "") or "",
                "topic": getattr(analysis, "topic", "") or "",
                "type": getattr(analysis, "type", "") or "",
                "page": getattr(analysis, "page", None),
                "value": getattr(analysis, "value", None),
                "context": getattr(analysis, "context", None),
                "evidence_segments": getattr(analysis, "evidence_segments", None) or [],
                "improvement_suggestions": getattr(analysis, "improvement_suggestions", None) or [],
            }

            # Match current UI/output enforcement used in pipeline JSON exports:
            status_lower = str(disclosure_status).strip().lower()
            if "partial" in status_lower:
                v = item.get("value")
                if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                    item["value"] = COMPLIANCE_VALUE_NA
            elif "not" in status_lower:
                # Strict null/evidence requirement
                item["page"] = None
                item["value"] = COMPLIANCE_VALUE_NA
                item["context"] = None
                item["evidence_segments"] = []
                item["improvement_suggestions"] = []

            metric_analyses_json.append(item)

        disclosure_summary_json = {
            "fully_disclosed": assessment.disclosure_summary.get(DisclosureStatus.FULLY_DISCLOSED, 0),
            "partially_disclosed": assessment.disclosure_summary.get(
                DisclosureStatus.PARTIALLY_DISCLOSED, 0
            ),
            "not_disclosed": assessment.disclosure_summary.get(DisclosureStatus.NOT_DISCLOSED, 0),
        }

        assessment_json: Dict[str, Any] = {
            "report_id": assessment.report_id,
            "assessment_date": assessment.assessment_date.isoformat() if hasattr(assessment, "assessment_date") else datetime.now().isoformat(),
            "filename": filename,
            "total_metrics": assessment.total_metrics_analyzed,
            "overall_score": assessment.overall_compliance_score,
            "total_metrics_analyzed": assessment.total_metrics_analyzed,
            "overall_compliance_score": assessment.overall_compliance_score,
            "report_file_path": str(report_file_path or assessment.report_file_path or ""),
            "framework": assessment.framework,
            "gri_sector": gri_sector,
            "gri_topic": gri_topic,
            "disclosure_summary": disclosure_summary_json,
            "metric_analyses": metric_analyses_json,
        }

        return assessment_json

