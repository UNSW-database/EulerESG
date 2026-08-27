"""
Disclosure Inference Engine - Use LLM to analyze ESG metric disclosure status
"""

import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import re
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
import openai
from loguru import logger

from .content_revision import document_content_revision
from .models import (
    ProcessingConfig, 
    MetricRetrievalResult,
    DisclosureStatus,
    DisclosureAnalysis,
    ComplianceAssessment,
    ReportContent,
    MetricCollection,
    RetrievalResult,
    table_row_scope_key,
)
from .exceptions import DisclosureAnalysisError
from .retrieval.metric_profile import (
    MetricRetrievalProfile,
    build_profile_index,
    compact_metric_text,
    compile_metric_code_patterns,
    content_contains_alias,
    find_metric_profile,
    normalize_metric_text,
)

def _is_claude_model(model_name: str) -> bool:
    """Return True if the model is Claude (Anthropic) so we can use json_schema response_format."""
    if not model_name:
        return False
    m = model_name.strip().lower()
    return "claude" in m or "anthropic" in m

# JSON schema for disclosure analysis (used when response_format is json_schema, e.g. Claude)
DISCLOSURE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "metric_hit": {"type": ["boolean", "null"]},
        "disclosure_status": {
            "type": "string",
            "enum": ["fully_disclosed", "partially_disclosed", "not_disclosed"],
        },
        "has_disclosure": {"type": "boolean"},
        "disclosure_quality": {"type": "string"},
        "value_status": {"type": ["string", "null"]},
        "reasoning": {"type": "string"},
        "value": {"type": ["number", "null"]},
        "raw_value": {"type": ["number", "null"]},
        "raw_unit": {"type": ["string", "null"]},
        "page": {"type": ["integer", "null"]},
        "evidence_segment_id": {"type": ["string", "null"]},
        "evidence_quote": {"type": ["string", "null"]},
        "specific_data_found": {"type": ["string", "null"]},
        "year_values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer"},
                    "value": {"type": "number"},
                    "raw_value": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "page": {"type": ["integer", "null"]},
                    "evidence_segment_id": {"type": ["string", "null"]},
                    "evidence_quote": {"type": ["string", "null"]},
                },
                "required": [
                    "year",
                    "value",
                    "raw_value",
                    "unit",
                    "page",
                    "evidence_segment_id",
                    "evidence_quote",
                ],
                "additionalProperties": False,
            },
        },
        "derived_calculation": {
            "type": ["object", "null"],
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["ratio_percent", "ratio", "sum", "difference"],
                },
                "formula": {"type": "string"},
                "operands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "value": {"type": "number"},
                            "unit": {"type": "string"},
                            "year": {"type": "integer"},
                            "boundary": {"type": "string"},
                            "segment_id": {"type": "string"},
                        },
                        "required": ["name", "value", "unit", "year", "boundary", "segment_id"],
                        "additionalProperties": False,
                    },
                    "minItems": 2,
                },
            },
            "required": ["operation", "formula", "operands"],
            "additionalProperties": False,
        },
        "improvement_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["disclosure_status", "reasoning"],
    "additionalProperties": False,
}

# Stored in assessment JSON when no metric-specific number is disclosed / extractable.
COMPLIANCE_VALUE_NA = "n/a"

_DEFAULT_REJECT_VALUE_SOURCES = {
    "metric_code",
    "reference_index",
    "page_number",
    "row_or_column_number",
    "standalone_year",
}

_TABLE_SEMANTIC_YEAR_BLOCKERS = {
    "ambiguous_year_scope",
    "conflicting_year_scope",
}
_TABLE_SEMANTIC_UNIT_BLOCKERS = {"ambiguous_unit_scope"}
_TABLE_SEMANTIC_VALUE_BLOCKERS = (
    _TABLE_SEMANTIC_YEAR_BLOCKERS | _TABLE_SEMANTIC_UNIT_BLOCKERS
)


def _positive_env_int(name: str, default: int, *, minimum: int = 1, maximum: Optional[int] = None) -> int:
    try:
        value = max(minimum, int(os.getenv(name, str(default)) or str(default)))
    except (TypeError, ValueError):
        value = max(minimum, default)
    return min(value, maximum) if maximum is not None else value

def _parse_llm_numeric_value_only(raw: object) -> Optional[Union[int, float]]:
    """
    Accept a safe numeric literal, or a short numeric phrase like
    '152,341 tCO2e', 'about 12.5 GJ', '1.4 million kWh', '12.5%'.
    Never scrape a number from a longer sentence.
    """
    if raw is None or raw is False:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")):
            return None
        return raw
    s = str(raw).strip()
    if not s or s.lower() in ("n/a", "na", "none", "null", "-", "—", "--"):
        return None
    if len(s) > 80 or "\n" in s:
        return None

    s2 = s.replace(",", "").strip()
    if s2.endswith("%"):
        s2 = s2[:-1].strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s2):
        n = float(s2)
        return int(n) if n == int(n) and abs(n) < 1e15 else n

    m = re.fullmatch(
        r"(?i)(?:about|approximately|approx\.?|around|nearly|roughly)?\s*"
        r"(-?\d[\d,]*(?:\.\d+)?)\s*"
        r"(million|billion|thousand|m|bn|k)?\s*"
        r"([a-zA-Z%/²³0-9._-]{0,20})?",
        s
    )
    if not m:
        return None
    number = m.group(1)
    multiplier = (m.group(2) or "").lower()
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if multiplier in {"thousand", "k"}:
        value *= 1_000
    elif multiplier in {"million", "m"}:
        value *= 1_000_000
    elif multiplier in {"billion", "bn"}:
        value *= 1_000_000_000
    if value == int(value) and abs(value) < 1e15:
        return int(value)
    return value

def _normalize_unit_text(unit: Optional[str]) -> str:
    raw = str(unit or "").strip()
    if not raw:
        return ""
    raw = raw.replace("CO₂", "CO2").replace("co₂", "co2").replace("m³", "m3").replace("％", "%")
    raw = raw.replace("﹪", "%").replace("·", "/").replace("／", "/")
    raw = raw.replace("–", "-").replace("—", "-")
    raw = re.sub(r"[\[\]\{\}]", " ", raw)
    raw = raw.replace("\u00a0", " ")
    raw = re.sub(r"\s+", " ", raw).strip()

    phrase_replacements = [
        (r"(?i)\bmetric\s+tons?\s+of\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\bmetric\s+tons?\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\btons?\s+of\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\btonnes?\s+of\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\bkilograms?\s+of\s+co2(?:e|\s*equivalent)\b", "kgCO2e"),
        (r"(?i)\bkilotons?\s+of\s+co2(?:e|\s*equivalent)\b", "ktCO2e"),
        (r"(?i)\bmegawatt(?:-|\s)?hours?\b", "MWh"),
        (r"(?i)\bkilowatt(?:-|\s)?hours?\b", "kWh"),
        (r"(?i)\bgigawatt(?:-|\s)?hours?\b", "GWh"),
        (r"(?i)\bterawatt(?:-|\s)?hours?\b", "TWh"),
        (r"(?i)\bgigajoules?\b", "GJ"),
        (r"(?i)\bterajoules?\b", "TJ"),
        (r"(?i)\bpetajoules?\b", "PJ"),
        (r"(?i)\bmillion\s+british\s+thermal\s+units?\b", "MMBtu"),
        (r"(?i)\bcubic\s+meters?\b", "m3"),
        (r"(?i)\bcubic\s+metres?\b", "m3"),
        (r"(?i)\bkilolit(?:er|re)s?\b", "kL"),
        (r"(?i)\blit(?:er|re)s?\b", "L"),
        (r"(?i)\bmillilit(?:er|re)s?\b", "mL"),
        (r"(?i)\bmetric\s+tons?\b", "t"),
        (r"(?i)\btons?\b", "t"),
        (r"(?i)\btonnes?\b", "t"),
        (r"(?i)\bkilograms?\b", "kg"),
        (r"(?i)\bgrams?\b", "g"),
        (r"(?i)\bpercent(?:age)?\b", "%"),
    ]
    for pat, repl in phrase_replacements:
        raw = re.sub(pat, repl, raw)

    raw = re.sub(r"(?i)\bper\b", "/", raw)
    raw = re.sub(r"\s*/\s*", "/", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw

def _extract_unit_hint(text: Optional[str]) -> str:
    value = _normalize_unit_text(text)
    if not value:
        return ""
    patterns = [
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(tCO2e|kgCO2e|ktCO2e|MtCO2e|co2e)\b",
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(MWh|kWh|GWh|TWh|GJ|TJ|PJ|MMBtu|therms?|toe|ktoe)\b",
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(m3|kL|L|mL)\b",
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(t|kg|g)\b",
        r"(?i)(?:%|percent)",
    ]
    for pat in patterns:
        m = re.search(pat, value)
        if m:
            return m.group(0).strip()
    return ""

def _clean_converted_number(value: float) -> Union[int, float]:
    if value == int(value) and abs(value) < 1e15:
        return int(value)
    return round(value, 9)

def _coalesce_metric_hit(llm_response: dict) -> bool:
    if "metric_hit" in llm_response and llm_response.get("metric_hit") is not None:
        return bool(llm_response.get("metric_hit"))
    return bool(llm_response.get("has_disclosure", False))

def _normalize_value_status(raw: object) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "exact": "exact",
        "converted": "converted",
        "approximate": "approximate",
        "approx": "approximate",
        "raw": "raw_unit_only",
        "raw_unit_only": "raw_unit_only",
        "unit_mismatch": "unit_mismatch",
        "ambiguous": "ambiguous",
        "derived": "derived",
        "none": "none",
        "null": "none",
    }
    return aliases.get(value, value or "none")

def _finalize_compliance_value_field(
    found_numeric: Optional[Union[int, float]],
) -> Union[int, float, str]:
    if isinstance(found_numeric, (int, float)) and not isinstance(found_numeric, bool):
        return found_numeric
    return COMPLIANCE_VALUE_NA

def _extract_unit_multiplier(unit: Optional[str]) -> float:
    raw = _normalize_unit_text(unit).lower()
    if not raw:
        return 1.0
    if re.search(r"\b(billion|bn)\b", raw):
        return 1_000_000_000.0
    if re.search(r"\bmillion\b", raw):
        return 1_000_000.0
    if re.search(r"\bthousand\b", raw):
        return 1_000.0
    return 1.0

def _normalize_denominator_atom(unit: Optional[str]) -> str:
    raw = _normalize_unit_text(unit).lower()
    if not raw:
        return ""
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    alias_map = {
        "employees": "employee",
        "employee": "employee",
        "fte": "fte",
        "ftes": "fte",
        "revenue": "revenue",
        "sales": "revenue",
        "usd": "usd",
        "product": "product",
        "products": "product",
        "unit": "unit",
        "units": "unit",
    }
    return alias_map.get(raw, raw)

def _normalize_unit_atom(unit: Optional[str]) -> str:
    raw = _normalize_unit_text(unit)
    if not raw:
        return ""
    raw = re.sub(r"(?i)\b(?:thousand|million|billion|bn)\b", " ", raw)
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"(?i)(mwh|kwh|gwh|twh|gj|tj|pj|mmbtu|therm|therms|toe|ktoe|tco2e|kgco2e|ktco2e|mtco2e|m3|kl|l|ml|t|kg|g)(?:\(?\d+\)?|[ivx]+)?$", r"\1", raw)
    lower = raw.lower()
    alias_map = {
        "%": "%", "percent": "%", "percentage": "%", "pct": "%",
        "ratio": "ratio", "fraction": "ratio", "decimal": "ratio",
        "tco2e": "tco2e", "co2e": "tco2e", "kgco2e": "kgco2e", "ktco2e": "ktco2e", "mtco2e": "mtco2e",
        "mwh": "mwh", "kwh": "kwh", "gwh": "gwh", "twh": "twh", "gj": "gj", "tj": "tj", "pj": "pj", "mmbtu": "mmbtu", "therm": "therm", "therms": "therm", "toe": "toe", "ktoe": "ktoe",
        "m3": "m3", "kl": "kl", "l": "l", "ml": "ml",
        "t": "t", "ton": "t", "tons": "t", "tonne": "t", "tonnes": "t", "kg": "kg", "g": "g",
    }
    return alias_map.get(lower, lower)

def _split_unit_expression(unit: Optional[str]) -> Tuple[str, str, float]:
    raw = _normalize_unit_text(unit)
    if not raw:
        return "", "", 1.0
    multiplier = _extract_unit_multiplier(raw)
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        return _normalize_unit_atom(numerator), _normalize_denominator_atom(denominator), multiplier
    return _normalize_unit_atom(raw), "", multiplier

def _unit_profile(unit: Optional[str]) -> Optional[Tuple[str, float]]:
    token = _normalize_unit_atom(_extract_unit_hint(unit) or unit)
    if not token:
        return None
    maps = {
        "ratio_percent": {"%": 1.0, "ratio": 100.0},
        "energy_gj": {"gj": 1.0, "tj": 1000.0, "pj": 1_000_000.0, "kwh": 0.0036, "mwh": 3.6, "gwh": 3600.0, "twh": 3_600_000.0, "mmbtu": 1.055056, "therm": 0.1055056, "toe": 41.868, "ktoe": 41_868.0},
        "volume_m3": {"m3": 1.0, "kl": 1.0, "l": 0.001, "ml": 0.000001},
        "emissions_tco2e": {"tco2e": 1.0, "kgco2e": 0.001, "ktco2e": 1000.0, "mtco2e": 1_000_000.0},
        "mass_t": {"t": 1.0, "kg": 0.001, "g": 0.000001},
    }
    for family, mapping in maps.items():
        if token in mapping:
            return family, mapping[token]
    return None

def _convert_numeric_value_between_units(value: Optional[Union[int, float]], from_unit: Optional[str], to_unit: Optional[str]) -> Optional[Union[int, float]]:
    if value is None:
        return None
    from_num, from_den, from_scale = _split_unit_expression(from_unit)
    to_num, to_den, to_scale = _split_unit_expression(to_unit)
    if not from_num or not to_num:
        return None
    if (from_den or to_den) and from_den != to_den:
        return None
    from_profile = _unit_profile(from_num)
    to_profile = _unit_profile(to_num)
    if from_profile is None or to_profile is None or from_profile[0] != to_profile[0]:
        return None
    converted = float(value) * float(from_scale) * float(from_profile[1]) / (float(to_scale) * float(to_profile[1]))
    return _clean_converted_number(converted)

class DisclosureInferenceEngine:
    """Disclosure Inference Engine - Call LLM to analyze disclosure status"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize disclosure inference engine
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.llm_client = self._init_llm_client()
        
    def _init_llm_client(self):
        """Initialize LLM client"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for disclosure inference. Please configure LLM_API_KEY in your .env file.")
        
        client = openai.OpenAI(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url if self.config.llm_base_url else "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        logger.info("LLM client initialized successfully for disclosure inference")
        return client

    def _not_disclosed_analysis_for_metric(self, metric, reasoning: str) -> DisclosureAnalysis:
        return DisclosureAnalysis(
            metric_id=metric.metric_id,
            metric_name=metric.metric_name,
            metric_code=metric.metric_code,
            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
            reasoning=reasoning,
            evidence_segments=[],
            improvement_suggestions=[],
            category=getattr(metric, "sasb_category", ""),
            topic=(getattr(metric, "sasb_topic", None) or ""),
            unit=getattr(metric, "unit", ""),
            type=getattr(metric, "sasb_type", ""),
            definition=(getattr(metric, "definition", None) or ""),
            value=COMPLIANCE_VALUE_NA,
            page=None,
        )

    def _analyze_collection_metric(
        self,
        metric,
        retrieval_result: Optional[MetricRetrievalResult],
        report_content: ReportContent,
    ) -> DisclosureAnalysis:
        if retrieval_result is None or retrieval_result.total_matches <= 0:
            return self._not_disclosed_analysis_for_metric(
                metric,
                "No relevant metric content found",
            )
        return self._analyze_single_metric(retrieval_result, report_content, metric)
    
    def analyze_compliance(
        self,
        retrieval_results: Iterable[MetricRetrievalResult],
        report_content: ReportContent,
        report_file_path: str = "",
        all_metrics: Optional[MetricCollection] = None,
        framework: Optional[str] = None,
        industry: Optional[str] = None,
        semi_industry: Optional[str] = None
    ) -> ComplianceAssessment:
        """
        Analyze compliance status of all metrics

        Args:
            retrieval_results: Dual-channel retrieval results
            report_content: Report content
            report_file_path: Report file path
            all_metrics: All metrics to analyze (if provided, will analyze all metrics)
            framework: Framework used (e.g., SASB, GRI)
            industry: Industry sector
            semi_industry: Sub-industry sector

        Returns:
            ComplianceAssessment: Compliance assessment report
        """
        
        # If all metrics are provided, analyze all metrics; otherwise only analyze retrieved metrics
        if all_metrics:
            logger.info(f"Starting compliance analysis for all {len(all_metrics.metrics)} metrics in collection")
            
            metrics = list(all_metrics.metrics)
            concurrency = _positive_env_int(
                "REPORT_DISCLOSURE_LLM_CONCURRENCY",
                200,
                maximum=200,
            )
            concurrency = min(concurrency, max(1, len(metrics)))
            logger.info(
                f"Compliance metric analysis concurrency={concurrency}, metrics={len(metrics)}"
            )

            # Build immutable report lookup caches before worker threads begin.
            self._get_report_segment_cache(report_content)
            ordered_analyses: List[Optional[DisclosureAnalysis]] = [None] * len(metrics)
            metric_indices_by_id: Dict[str, List[int]] = {}
            for index, metric in enumerate(metrics):
                metric_indices_by_id.setdefault(
                    str(getattr(metric, "metric_id", "") or ""),
                    [],
                ).append(index)
            submitted_indices = set()
            analysis_intervals: List[Tuple[float, float]] = []

            def analyze_indexed(
                index: int,
                metric,
                retrieval_result: Optional[MetricRetrievalResult],
            ):
                started = time.perf_counter()
                logger.info(f"Analyzing metric {index + 1}/{len(metrics)}: {metric.metric_name}")
                analysis = self._analyze_collection_metric(
                    metric,
                    retrieval_result,
                    report_content,
                )
                finished = time.perf_counter()
                logger.info(
                    f"Metric analysis completed {index + 1}/{len(metrics)}: "
                    f"{metric.metric_name}, elapsed={finished - started:.2f}s"
                )
                return index, analysis, started, finished

            pending = {}
            pending_limit = max(concurrency, concurrency * 2)

            def collect_completed(*, block: bool) -> None:
                if not pending:
                    return
                completed, _ = wait(
                    tuple(pending),
                    timeout=None if block else 0,
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    pending.pop(future, None)
                    result_index, analysis, started, finished = future.result()
                    ordered_analyses[result_index] = analysis
                    analysis_intervals.append((started, finished))

            def submit_analysis(
                executor: ThreadPoolExecutor,
                index: int,
                retrieval_result: Optional[MetricRetrievalResult],
            ) -> None:
                if index in submitted_indices:
                    return
                submitted_indices.add(index)
                pending[
                    executor.submit(
                        analyze_indexed,
                        index,
                        metrics[index],
                        retrieval_result,
                    )
                ] = index
                # Surface already-failed tasks promptly and keep the executor's
                # otherwise-unbounded work queue small while retrieval continues.
                collect_completed(block=False)
                if len(pending) >= pending_limit:
                    collect_completed(block=True)

            retrieval_count = 0
            with ThreadPoolExecutor(
                max_workers=concurrency,
                thread_name_prefix="disclosure-llm",
            ) as executor:
                try:
                    for retrieval_result in retrieval_results or []:
                        retrieval_count += 1
                        metric_id = str(
                            getattr(retrieval_result, "metric_id", "") or ""
                        )
                        matching_indices = metric_indices_by_id.get(metric_id, [])
                        if not matching_indices:
                            logger.warning(
                                "Ignoring extra retrieval result without an "
                                "unsubmitted matching "
                                f"collection metric: metric_id={metric_id!r}"
                            )
                            continue
                        # A collection can contain repeated metric IDs. Consume
                        # those positions one by one so each streamed retrieval
                        # result remains paired with exactly one metric entry.
                        index = matching_indices.pop(0)
                        submit_analysis(executor, index, retrieval_result)
                        logger.info(
                            "Submitted disclosure inference immediately after "
                            f"retrieval metric_id={metric_id}, "
                            f"retrieved={retrieval_count}/{len(metrics)}, "
                            f"in_flight={len(pending)}"
                        )

                    # Preserve the existing all-metrics behavior when a retrieval
                    # implementation omits a metric entirely.
                    for index in range(len(metrics)):
                        if index not in submitted_indices:
                            submit_analysis(executor, index, None)

                    while pending:
                        collect_completed(block=True)
                except Exception:
                    for future in pending:
                        future.cancel()
                    raise

            analysis_work_seconds = sum(
                max(0.0, finished - started)
                for started, finished in analysis_intervals
            )
            analysis_active_seconds = 0.0
            active_end: Optional[float] = None
            for started, finished in sorted(analysis_intervals):
                if finished <= started:
                    continue
                if active_end is None or started > active_end:
                    analysis_active_seconds += finished - started
                    active_end = finished
                    continue
                if finished > active_end:
                    analysis_active_seconds += finished - active_end
                    active_end = finished
            for telemetry_name, telemetry_value in (
                ("disclosure_active_seconds", analysis_active_seconds),
                ("disclosure_work_seconds", analysis_work_seconds),
            ):
                try:
                    setattr(retrieval_results, telemetry_name, telemetry_value)
                except Exception:
                    pass

            missing_analysis_indices = [
                index
                for index, analysis in enumerate(ordered_analyses)
                if analysis is None
            ]
            if missing_analysis_indices:
                raise RuntimeError(
                    "Compliance pipeline completed without results for metric "
                    f"indices: {missing_analysis_indices}"
                )
            metric_analyses = list(ordered_analyses)
                
        else:
            retrieval_results = list(retrieval_results or [])
            logger.info(f"Starting compliance analysis for {len(retrieval_results)} retrieved metrics")
            
            # Analyze each retrieved metric
            metric_analyses = []
            for i, retrieval_result in enumerate(retrieval_results):
                logger.info(f"Analyzing metric {i+1}/{len(retrieval_results)}: {retrieval_result.metric_name}")
                analysis = self._analyze_single_metric(retrieval_result, report_content)
                metric_analyses.append(analysis)
        
        # Count quantities for each status
        disclosure_summary = {
            DisclosureStatus.FULLY_DISCLOSED: 0,
            DisclosureStatus.PARTIALLY_DISCLOSED: 0,
            DisclosureStatus.NOT_DISCLOSED: 0
        }
        
        for analysis in metric_analyses:
            disclosure_summary[analysis.disclosure_status] += 1
        
        # Calculate overall compliance score
        total_metrics = len(metric_analyses)
        if total_metrics > 0:
            fully_disclosed = disclosure_summary[DisclosureStatus.FULLY_DISCLOSED]
            partially_disclosed = disclosure_summary[DisclosureStatus.PARTIALLY_DISCLOSED]
            overall_score = (fully_disclosed * 1.0 + partially_disclosed * 0.5) / total_metrics
        else:
            overall_score = 0.0
        
        # Create assessment report
        assessment = ComplianceAssessment(
            report_id=report_content.document_id,
            total_metrics_analyzed=total_metrics,
            disclosure_summary=disclosure_summary,
            metric_analyses=metric_analyses,
            overall_compliance_score=overall_score,
            report_file_path=report_file_path,
            framework=framework,
            industry=industry,
            semi_industry=semi_industry
        )
        
        logger.info(f"Compliance analysis completed. Overall score: {overall_score:.2%}")
        logger.info(f"Disclosure summary - Fully: {disclosure_summary[DisclosureStatus.FULLY_DISCLOSED]}, "
                   f"Partially: {disclosure_summary[DisclosureStatus.PARTIALLY_DISCLOSED]}, "
                   f"Not disclosed: {disclosure_summary[DisclosureStatus.NOT_DISCLOSED]}")
        
        return assessment
    
    def _extract_year_from_text(self, text: Optional[str]) -> Optional[int]:
        """Extract the first 4-digit reporting year from free text."""
        value = str(text or "").strip()
        if not value:
            return None
        match = re.search(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)", value)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _extract_json_from_llm_response(self, content: str) -> Optional[dict]:
        """Extract a JSON object from LLM response text. Returns None if no valid JSON found."""
        if not content or not content.strip():
            return None
        content = content.strip()
        # 1) Direct parse
        try:
            return json.loads(content)
        except Exception:
            pass
        # 2) Strip markdown code fences (```json ... ``` or ``` ... ```)
        for pattern in [
            r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
            r"```\s*(\{[\s\S]*?\})\s*```",
        ]:
            m = re.search(pattern, content, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1).strip())
                except Exception:
                    pass
        # 3) Find first { and extract balanced-brace JSON
        start = content.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(content)):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(content[start : i + 1])
                        except Exception:
                            break
        # 4) Greedy first {...} (original fallback)
        mobj = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
        if mobj:
            try:
                return json.loads(mobj.group(0))
            except Exception:
                pass
        return None
    
    def _metric_code_candidates(
        self,
        retrieval_result: MetricRetrievalResult,
        metric: Optional['ESGMetric'] = None,
    ) -> List[str]:
        """Return robust current-metric code candidates for deterministic evidence checks."""
        primary_candidates: List[object] = [
            getattr(metric, "metric_code", None) if metric is not None else None,
            getattr(retrieval_result, "metric_code", None),
        ]
        candidates: List[str] = []
        seen = set()

        def append_candidates(raw_values: List[object], *, strict_shape: bool = False) -> None:
            for raw in raw_values:
                if raw is None:
                    continue
                for part in re.split(r"[,;\n|]+", str(raw)):
                    code = part.replace("\u00a0", " ").strip().strip("()[]{}")
                    if not code:
                        continue
                    if len(code) < 3 or not re.search(r"[A-Za-z]", code) or not re.search(r"\d", code):
                        continue
                    if strict_shape and (
                        len(code) > 40
                        or not re.search(r"[-./]", code)
                        or re.search(r"\s{2,}|_", code)
                    ):
                        continue
                    key = re.sub(r"[^A-Za-z0-9]", "", code).upper()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    candidates.append(code)

        append_candidates(primary_candidates)
        if not candidates:
            append_candidates(
                [
                    getattr(metric, "metric_id", None) if metric is not None else None,
                    getattr(retrieval_result, "metric_id", None),
                ],
                strict_shape=True,
            )
        return candidates

    def _resolve_metric_profile(
        self,
        metric: Optional['ESGMetric'],
        retrieval_result: Optional[MetricRetrievalResult] = None,
    ) -> Optional[MetricRetrievalProfile]:
        """Resolve the exact generated profile for runtime extraction rules."""
        try:
            if metric is not None:
                profile = find_metric_profile(metric)
                if profile is not None:
                    return profile
            if retrieval_result is not None:
                return find_metric_profile(retrieval_result.metric_code or retrieval_result.metric_id)
        except Exception as exc:
            logger.debug(f"Metric profile lookup failed; using base extraction rules: {exc}")
        return None

    def _profile_identity_aliases(
        self,
        profile: MetricRetrievalProfile,
        *,
        unique_for_shared_code: bool = True,
    ) -> List[str]:
        aliases = [profile.metric_name, profile.canonical_label, *profile.aliases]
        blocked = {
            normalize_metric_text(profile.metric_code),
            normalize_metric_text(profile.topic),
            normalize_metric_text(profile.unit),
        }
        cleaned: List[str] = []
        seen = set()
        for alias in aliases:
            value = re.sub(r"\s+", " ", str(alias or "")).strip()
            normalized = normalize_metric_text(value)
            if not normalized or normalized in blocked or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(value)

        if not unique_for_shared_code or not profile.metric_code:
            return cleaned

        siblings = [
            item
            for item in self._profiles_for_metric_codes(profile=profile)
            if item.metric_id != profile.metric_id
        ]
        if not siblings:
            return cleaned

        sibling_aliases = {
            normalize_metric_text(alias)
            for sibling in siblings
            for alias in [sibling.metric_name, sibling.canonical_label, *sibling.aliases]
            if str(alias or "").strip()
        }
        unique_aliases = [
            alias for alias in cleaned
            if normalize_metric_text(alias) not in sibling_aliases
        ]
        return unique_aliases or [profile.metric_name]

    def _profiles_for_metric_codes(
        self,
        *,
        profile: Optional[MetricRetrievalProfile] = None,
        code_candidates: Optional[Sequence[str]] = None,
    ) -> List[MetricRetrievalProfile]:
        """Return de-duplicated profiles belonging to the current SASB code."""
        raw_codes: List[object] = list(code_candidates or [])
        if profile is not None:
            raw_codes.insert(0, profile.metric_code)
        try:
            index = build_profile_index()
        except Exception:
            return []

        matches: List[MetricRetrievalProfile] = []
        seen = set()
        for raw_code in raw_codes:
            for key in (str(raw_code or "").strip().lower(), compact_metric_text(raw_code)):
                if not key:
                    continue
                for candidate in index["by_code"].get(key.lower(), []):
                    identity = (
                        candidate.industry,
                        candidate.metric_id,
                        candidate.metric_code,
                        candidate.metric_name,
                        candidate.unit,
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    matches.append(candidate)
        return matches

    def _metric_code_is_shared(
        self,
        profile: Optional[MetricRetrievalProfile],
        code_candidates: Optional[Sequence[str]] = None,
    ) -> bool:
        return len(
            self._profiles_for_metric_codes(
                profile=profile,
                code_candidates=code_candidates,
            )
        ) > 1

    def _profile_component_matches(
        self,
        text: object,
        profile: Optional[MetricRetrievalProfile],
    ) -> bool:
        if profile is None:
            return False
        evidence = str(text or "")
        return any(
            content_contains_alias(evidence, alias)
            for alias in self._profile_identity_aliases(profile)
        )

    def _profile_reject_value_sources(
        self,
        profile: Optional[MetricRetrievalProfile],
    ) -> set[str]:
        if profile is not None and profile.reject_values_from:
            return {str(value).strip().lower() for value in profile.reject_values_from}
        return set(_DEFAULT_REJECT_VALUE_SOURCES)

    def _candidate_profile_text(self, candidate: Dict[str, Any]) -> str:
        segment = candidate.get("segment")
        return "\n".join(
            str(value or "")
            for value in [
                candidate.get("description"),
                candidate.get("row_label"),
                candidate.get("column_label"),
                candidate.get("label"),
                getattr(segment, "content", "") if segment is not None else "",
            ]
            if str(value or "").strip()
        )

    def _candidate_has_meaningful_label(self, candidate: Dict[str, Any]) -> bool:
        label_text = " ".join(
            str(value or "")
            for value in [
                candidate.get("row_label"),
                candidate.get("label"),
                candidate.get("column_label"),
                candidate.get("description"),
            ]
            if str(value or "").strip()
        )
        label_text = re.sub(r"(?i)\b(?:FY|CY)?\s*['\u2019]?\d{2,4}\b", " ", label_text)
        label_text = re.sub(r"[-+]?\d[\d,.]*\s*%?", " ", label_text)
        label_text = re.sub(r"[^A-Za-z]+", " ", label_text).strip().lower()
        label_text = re.sub(
            r"\b(?:reported|result|value|unit|measure|fy|cy)\b",
            " ",
            label_text,
        )
        label_text = re.sub(r"\s+", " ", label_text).strip()
        return bool(
            label_text
            and label_text not in {"value", "unit", "unit of measure", "percentage", "percent"}
        )

    def _candidate_satisfies_profile(
        self,
        candidate: Dict[str, Any],
        profile: Optional[MetricRetrievalProfile],
    ) -> bool:
        if candidate.get("blocking_semantic_quality_reasons"):
            return False
        if profile is None:
            return True
        if (
            profile.requires_value_label or profile.requires_dimension_labels
        ) and not self._candidate_has_meaningful_label(candidate):
            return False
        requires_component_match = self._metric_code_is_shared(profile) or bool(
            profile.value_selection_rules.get(
                "match_current_component_before_sibling_values", False
            )
        )
        if requires_component_match and not self._profile_component_matches(
            self._candidate_profile_text(candidate), profile
        ):
            return False
        return True

    def _profile_prompt_rules(
        self,
        profile: Optional[MetricRetrievalProfile],
    ) -> Dict[str, Any]:
        if profile is None:
            return {}
        return {
            "profile_metric_id": profile.metric_id,
            "target_identity_aliases": self._profile_identity_aliases(profile)[:16],
            "direct_disclosure_rules": profile.direct_disclosure_rules,
            "value_type": profile.value_type,
            "expected_units": profile.expected_units,
            "output_shape": profile.output_shape,
            "variable_dimensions": profile.variable_dimensions,
            "requires_dimension_labels": profile.requires_dimension_labels,
            "requires_value_label": profile.requires_value_label,
            "reject_values_from": profile.reject_values_from,
            "year_rules": profile.year_rules,
            "value_selection_rules": profile.value_selection_rules,
            "similar_metric_warnings": profile.similar_metric_warnings[:12],
        }

    def _contains_metric_code(self, text: object, code_candidates: List[str]) -> bool:
        """True when evidence contains the current code with OCR-tolerant separators."""
        evidence = str(text or "")
        if not evidence or not code_candidates:
            return False
        for code in code_candidates:
            if any(pattern.search(evidence) for pattern in compile_metric_code_patterns(code)):
                return True
        return False

    def _direct_evidence_bundle_for_segment(
        self,
        report_content: ReportContent,
        segment,
        code_candidates: List[str],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Build same-row/same-segment evidence and return its real data cells."""
        if segment is None:
            return "", []

        row_context, _, _, _ = self._build_table_row_aggregation_context(
            report_content,
            segment,
            max_chars=1800,
        )
        structured_hint = self._build_structured_segment_hint(segment)
        hit_text = self._truncate_segment_text(getattr(segment, "content", "") or "", 1000)
        evidence_text = "\n\n".join(x for x in [row_context, structured_hint, hit_text] if x)

        if not self._contains_metric_code(evidence_text, code_candidates):
            return evidence_text, []

        numeric_candidates = self._real_data_candidates_for_row(
            report_content,
            segment,
            code_candidates,
            metric_profile,
        )
        return evidence_text, numeric_candidates

    def _table_row_has_unresolved_structure(
        self,
        report_content: ReportContent,
        segment,
    ) -> bool:
        """Prevent deterministic disclosure from trusting conflicted table rows."""
        row_key = self._get_table_row_scope_key(segment)
        related = (
            self._get_report_segment_cache(report_content)["table_rows"].get(
                row_key,
                [],
            )
            if row_key != (None, None)
            else [segment]
        )
        for item in related or [segment]:
            data = self._segment_structured_data_dict(item)
            review_status = str(
                getattr(item, "review_status", None)
                or data.get("review_status")
                or ""
            ).strip().lower()
            conflicts = getattr(item, "conflicts", None) or data.get("conflicts") or []
            quality_reasons = (
                getattr(item, "quality_reasons", None)
                or data.get("quality_reasons")
                or []
            )
            if review_status == "needs_review" or bool(conflicts):
                return True
            if any(
                "ambiguous" in str(reason or "").lower()
                or "conflict" in str(reason or "").lower()
                for reason in quality_reasons
            ):
                return True
        return False

    def _direct_code_data_disclosure_analysis(
        self,
        retrieval_result: MetricRetrievalResult,
        report_content: ReportContent,
        metric: Optional['ESGMetric'],
        segment_metadata: List[Dict],
        evidence_segment_ids: List[str],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Optional[DisclosureAnalysis]:
        """Classify as fully_disclosed when current metric code and direct data are found together.

        This is intentionally a high-confidence deterministic shortcut placed before
        the LLM. It only triggers when the retrieved evidence contains the current
        metric code and a directly extractable numeric data value in the same
        segment/table row. Otherwise, the normal LLM assessment path is preserved.
        """
        code_candidates = self._metric_code_candidates(retrieval_result, metric)
        if not code_candidates or not segment_metadata:
            return None

        metric_profile = metric_profile or self._resolve_metric_profile(
            metric, retrieval_result
        )
        direct_rules = (
            metric_profile.direct_disclosure_rules if metric_profile is not None else {}
        )
        shared_code = self._metric_code_is_shared(metric_profile, code_candidates)
        # A shared code without an exactly resolved sub-metric profile is
        # intrinsically ambiguous and must continue through normal analysis.
        if shared_code and metric_profile is None:
            return None
        if (
            metric_profile is not None
            and metric_profile.output_shape in {"breakdown", "table"}
            and metric_profile.variable_dimensions
        ):
            return None

        expected_unit = getattr(metric, "unit", "") if metric else ""
        best_hit: Optional[Dict[str, Any]] = None

        for meta in segment_metadata:
            segment_id = meta.get("segment_id")
            if not segment_id:
                continue
            try:
                segment = self._get_segment_by_id(report_content, segment_id)
            except Exception:
                segment = None
            if segment is None:
                continue
            if self._table_row_has_unresolved_structure(report_content, segment):
                continue

            evidence_text, numeric_candidates = self._direct_evidence_bundle_for_segment(
                report_content=report_content,
                segment=segment,
                code_candidates=code_candidates,
                metric_profile=metric_profile,
            )
            if not evidence_text or not self._contains_metric_code(evidence_text, code_candidates):
                continue
            requires_component_match = shared_code or bool(
                direct_rules.get("requires_component_label_when_code_shared", False)
            )
            if requires_component_match and not self._profile_component_matches(
                evidence_text, metric_profile
            ):
                continue
            numeric_candidates = [
                candidate
                for candidate in numeric_candidates
                if self._candidate_satisfies_profile(candidate, metric_profile)
            ]
            if not numeric_candidates:
                continue

            # A direct row may contain one value or one unambiguous value per
            # reporting year. Multiple values within the same year still go
            # through normal sub-metric analysis.
            if len(numeric_candidates) > 1:
                candidate_years = [candidate.get("year") for candidate in numeric_candidates]
                if any(year is None for year in candidate_years):
                    continue
                if len(set(int(year) for year in candidate_years)) != len(candidate_years):
                    continue

            year_values = self._metric_year_values_from_candidates(
                numeric_candidates,
                metric,
                metric_profile,
            )
            target_year = self._target_year_for_metric(metric)
            selected_year, selected_year_value = self._select_metric_year_value(
                year_values,
                target_year,
                metric_profile,
            )
            selected_candidate: Optional[Dict[str, Any]] = None
            if selected_year_value is not None:
                for candidate in numeric_candidates:
                    if (
                        candidate.get("year") == selected_year_value.get("year")
                        and _parse_llm_numeric_value_only(candidate.get("value"))
                        == _parse_llm_numeric_value_only(selected_year_value.get("raw_value"))
                    ):
                        selected_candidate = candidate
                        break
            elif len(numeric_candidates) == 1 and not year_values:
                selected_candidate = numeric_candidates[0]

            if selected_year_value is not None:
                stored_numeric = selected_year_value.get("value")
                numeric_value = selected_year_value.get("raw_value")
                raw_unit = selected_year_value.get("raw_unit")
                data_desc = selected_year_value.get("context") or ""
                data_segment = selected_candidate.get("segment") if selected_candidate else None
            elif selected_candidate is not None:
                numeric_value = selected_candidate.get("value")
                raw_unit = selected_candidate.get("unit")
                converted_value = _convert_numeric_value_between_units(numeric_value, raw_unit, expected_unit)
                stored_numeric = converted_value if converted_value is not None else numeric_value
                data_desc = str(selected_candidate.get("description") or "")
                data_segment = selected_candidate.get("segment")
            else:
                numeric_value = None
                raw_unit = None
                stored_numeric = None
                data_desc = ""
                data_segment = None

            page_number = (
                (selected_year_value or {}).get("page")
                or getattr(data_segment, "page_number", None)
                or getattr(segment, "page_number", None)
                or meta.get("page_number")
            )
            score = float(meta.get("score", 0) or 0)
            data_segment_id = (
                (selected_year_value or {}).get("evidence_segment_id")
                or getattr(data_segment, "segment_id", None)
                or segment_id
            )

            hit = {
                "segment_id": data_segment_id,
                "page": page_number,
                "value": stored_numeric,
                "raw_value": numeric_value,
                "raw_unit": raw_unit,
                "context": self._format_short_metadata_value(evidence_text, 1400),
                "data_desc": data_desc or f"{numeric_value or ''} {raw_unit or ''}".strip(),
                "year_values": year_values,
                "selected_year": selected_year,
                "data_segment_ids": [
                    candidate.get("segment_id")
                    for candidate in numeric_candidates
                    if candidate.get("segment_id")
                ],
                "score": score,
            }
            if best_hit is None or hit["score"] > best_hit.get("score", 0):
                best_hit = hit

        if best_hit is None:
            return None

        code_text = "/".join(code_candidates[:2])
        raw_unit_text = f" {best_hit['raw_unit']}" if best_hit.get("raw_unit") else ""
        years_text = ", ".join(
            str(item.get("year")) for item in best_hit.get("year_values", [])
        )
        reasoning = (
            f"Direct code-data extraction found the current metric code ({code_text}) "
            f"and directly extractable metric data in the same evidence row/segment"
            f"{f' for reporting years {years_text}' if years_text else ''}. "
            f"The currently projected value is {best_hit['raw_value']}{raw_unit_text}; "
            "all annual values are retained for later year selection."
        )
        direct_year_values = self._attach_year_value_sources(
            best_hit.get("year_values") or [],
            segment_metadata,
        )

        return DisclosureAnalysis(
            metric_id=retrieval_result.metric_id,
            metric_name=retrieval_result.metric_name,
            metric_code=retrieval_result.metric_code,
            disclosure_status=DisclosureStatus.FULLY_DISCLOSED,
            reasoning=reasoning,
            evidence_segments=list(dict.fromkeys([
                best_hit["segment_id"],
                *(best_hit.get("data_segment_ids") or []),
                *(evidence_segment_ids or []),
            ])),
            improvement_suggestions=[],
            category=getattr(metric, 'sasb_category', '') if metric else '',
            topic=(getattr(metric, 'sasb_topic', None) or '') if metric else '',
            unit=getattr(metric, 'unit', '') or '' if metric else '',
            type=getattr(metric, 'sasb_type', '') if metric else '',
            definition=(getattr(metric, 'definition', None) or '') if metric else '',
            value=_finalize_compliance_value_field(best_hit.get("value")),
            year_values=direct_year_values,
            selected_year=best_hit.get("selected_year"),
            value_status="exact",
            context=best_hit.get("context"),
            page=best_hit.get("page"),
            evidence_sources=self._build_evidence_sources(
                segment_metadata,
                preferred_segment_id=best_hit.get("segment_id"),
                preferred_page=best_hit.get("page"),
            ),
        )

    def _direct_code_label_disclosure_analysis(
        self,
        retrieval_result: MetricRetrievalResult,
        report_content: ReportContent,
        metric: Optional['ESGMetric'],
        segment_metadata: List[Dict],
        evidence_segment_ids: List[str],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Optional[DisclosureAnalysis]:
        """Accept an explicit current-code/current-label report statement as disclosure.

        This fallback deliberately requires both identities.  A bare SASB code in a
        navigation/index cell is still insufficient, while a report row that names
        the exact code and exact metric cannot be reclassified by the LLM as
        unrelated merely because table extraction failed to recover its value.
        """
        code_candidates = self._metric_code_candidates(retrieval_result, metric)
        metric_name = str(
            getattr(metric, "metric_name", None)
            or retrieval_result.metric_name
            or ""
        ).strip()
        if not code_candidates or not metric_name or not segment_metadata:
            return None

        metric_profile = metric_profile or self._resolve_metric_profile(metric, retrieval_result)
        # One framework code can represent several separately assessed
        # sub-metrics (for example, the TC-SI-330a.3 representation splits).
        # Code + label alone is not a safe shortcut for those rows because the
        # LLM may still need to select the current component and its value.
        if self._metric_code_is_shared(metric_profile, code_candidates):
            return None

        best_hit: Optional[Dict[str, Any]] = None
        for meta in segment_metadata:
            segment_id = meta.get("segment_id")
            if not segment_id:
                continue
            try:
                segment = self._get_segment_by_id(report_content, segment_id)
            except Exception:
                segment = None
            if segment is None:
                continue
            evidence_text, _ = self._direct_evidence_bundle_for_segment(
                report_content, segment, code_candidates, metric_profile
            )
            if not self._contains_metric_code(evidence_text, code_candidates):
                continue
            label_matches = (
                self._profile_component_matches(evidence_text, metric_profile)
                if metric_profile is not None
                else content_contains_alias(evidence_text, metric_name)
            )
            if not label_matches:
                continue
            hit = {
                "segment_id": segment_id,
                "page": getattr(segment, "page_number", None) or meta.get("page_number"),
                "context": self._format_short_metadata_value(evidence_text, 1400),
                "score": float(meta.get("score", 0) or 0),
            }
            if best_hit is None or hit["score"] > best_hit["score"]:
                best_hit = hit

        if best_hit is None:
            return None

        code_text = "/".join(code_candidates[:2])
        return DisclosureAnalysis(
            metric_id=retrieval_result.metric_id,
            metric_name=retrieval_result.metric_name,
            metric_code=retrieval_result.metric_code,
            disclosure_status=DisclosureStatus.FULLY_DISCLOSED,
            reasoning=(
                f"The report explicitly identifies the current metric by both its exact "
                f"code ({code_text}) and metric label ({metric_name}). This direct "
                "same-code statement is treated as disclosure even though no reliable "
                "scalar value was recovered from the parsed segment."
            ),
            evidence_segments=list(dict.fromkeys([
                best_hit["segment_id"], *(evidence_segment_ids or [])
            ])),
            improvement_suggestions=[],
            category=getattr(metric, 'sasb_category', '') if metric else '',
            topic=(getattr(metric, 'sasb_topic', None) or '') if metric else '',
            unit=getattr(metric, 'unit', '') or '' if metric else '',
            type=getattr(metric, 'sasb_type', '') if metric else '',
            definition=(getattr(metric, 'definition', None) or '') if metric else '',
            value=None,
            value_status="unavailable",
            context=best_hit["context"],
            page=best_hit["page"],
            evidence_sources=self._build_evidence_sources(
                segment_metadata,
                preferred_segment_id=best_hit["segment_id"],
                preferred_page=best_hit["page"],
            ),
        )

    @staticmethod
    def _estimate_evidence_tokens(
        segments: List[str],
        metadata: List[Dict[str, Any]],
    ) -> int:
        char_count = sum(len(str(item or "")) for item in segments)
        try:
            char_count += len(json.dumps(metadata or [], ensure_ascii=False))
        except Exception:
            pass
        # Conservative approximation for mixed English/table/number content.
        return max(1, math.ceil(char_count / 3.5))

    def _evidence_chunks(
        self,
        segments: List[str],
        metadata: List[Dict[str, Any]],
        token_budget: int,
    ) -> List[List[Tuple[str, Dict[str, Any]]]]:
        """Pack evidence without splitting a table row or link source/target unit."""
        grouped: Dict[tuple, List[Tuple[str, Dict[str, Any]]]] = {}
        group_order: List[tuple] = []
        for index, segment in enumerate(segments):
            meta = dict(metadata[index] if index < len(metadata) else {})
            report_id = str(meta.get("source_report_id") or "")
            if meta.get("source_table_id") is not None and meta.get("row_index") is not None:
                key = (
                    "table_row",
                    report_id,
                    str(meta.get("source_table_id")),
                    str(meta.get("page_number") or ""),
                    str(meta.get("row_index")),
                )
            elif meta.get("link_target_page") is not None:
                key = (
                    "pdf_link",
                    report_id,
                    str(meta.get("link_source_page")),
                    str(meta.get("link_target_page")),
                )
            else:
                key = ("segment", str(meta.get("segment_id") or index))
            if key not in grouped:
                grouped[key] = []
                group_order.append(key)
            grouped[key].append((str(segment or ""), meta))

        chunks: List[List[Tuple[str, Dict[str, Any]]]] = []
        current: List[Tuple[str, Dict[str, Any]]] = []
        current_tokens = 0
        for key in group_order:
            unit = grouped[key]
            unit_tokens = self._estimate_evidence_tokens(
                [item[0] for item in unit],
                [item[1] for item in unit],
            )
            if current and current_tokens + unit_tokens > token_budget:
                chunks.append(current)
                current = []
                current_tokens = 0
            current.extend(unit)
            current_tokens += unit_tokens
        if current:
            chunks.append(current)
        return chunks

    def _summarize_evidence_chunks(
        self,
        retrieval_result: MetricRetrievalResult,
        segments: List[str],
        metadata: List[Dict[str, Any]],
        metric: Optional['ESGMetric'],
        token_budget: int,
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Extract compact structured candidates from oversized evidence windows."""
        chunks = self._evidence_chunks(segments, metadata, token_budget)
        if len(chunks) <= 1:
            return segments, metadata

        compact_segments: List[str] = []
        compact_metadata: List[Dict[str, Any]] = []
        metric_name = retrieval_result.metric_name
        metric_code = retrieval_result.metric_code or retrieval_result.metric_id
        metric_unit = str(getattr(metric, "unit", "") or "") if metric else ""
        logger.info(
            f"Chunking final evidence metric={retrieval_result.metric_id}: "
            f"segments={len(segments)}, chunks={len(chunks)}, token_budget={token_budget}"
        )

        for chunk_index, chunk in enumerate(chunks, start=1):
            entries = []
            for text, meta in chunk:
                entries.append(
                    {
                        "segment_id": meta.get("segment_id"),
                        "source_report_id": meta.get("source_report_id"),
                        "source_report_name": meta.get("source_report_name"),
                        "source_report_year": meta.get("source_report_year"),
                        "page": meta.get("page_number"),
                        "retrieval_type": meta.get("retrieval_type"),
                        "link_source_page": meta.get("link_source_page"),
                        "link_target_page": meta.get("link_target_page"),
                        "text": text,
                    }
                )
            user_prompt = (
                "Extract only evidence relevant to the current ESG metric from this evidence chunk. "
                "Preserve every explicit year, numeric value, unit, dimension label, report ID, page, "
                "and segment ID. Keep conflicting values. Do not decide the final disclosure status and "
                "do not invent values. Return JSON with keys summary and candidates.\n\n"
                f"Metric: {metric_name}\nCode: {metric_code}\nExpected unit: {metric_unit}\n"
                f"Chunk {chunk_index}/{len(chunks)}:\n"
                f"{json.dumps(entries, ensure_ascii=False)}"
            )
            try:
                response = self.llm_client.chat.completions.create(
                    model=self.config.llm_model,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a strict ESG evidence extraction stage. Return valid JSON only. "
                                "A candidate must retain its original source IDs and must not merge "
                                "different years, dimensions, or reports."
                            ),
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                )
            except Exception as exc:
                raise DisclosureAnalysisError(
                    f"LLM evidence chunk failed for metric '{metric_name}'.",
                    metric_id=retrieval_result.metric_id,
                    metric_name=metric_name,
                    error_type="llm_chunk_request_failed",
                ) from exc
            raw_content = (response.choices[0].message.content or "").strip()
            parsed = self._extract_json_from_llm_response(raw_content)
            if not isinstance(parsed, dict):
                raise DisclosureAnalysisError(
                    f"LLM returned invalid chunk JSON for metric '{metric_name}'.",
                    metric_id=retrieval_result.metric_id,
                    metric_name=metric_name,
                    error_type="invalid_llm_chunk_json",
                )
            compact_text = json.dumps(
                {"evidence_chunk": chunk_index, **parsed},
                ensure_ascii=False,
            )
            compact_segments.append(self._truncate_segment_text(compact_text, 16000))
            first_meta = dict(chunk[0][1] if chunk else {})
            first_meta.update(
                {
                    "retrieval_type": "structured_evidence_chunk",
                    "score": max(
                        [float(item[1].get("score") or 0.0) for item in chunk] or [0.0]
                    ),
                }
            )
            compact_metadata.append(first_meta)
        return compact_segments, compact_metadata

    @staticmethod
    def _all_other_employee_category_is_only_proxy(
        metric_name: object,
        evidence_segments: Sequence[object],
    ) -> bool:
        """Detect a non-technical proxy for the narrower SASB employee category.

        ``Non-technical roles`` can include executive or non-executive management,
        while SASB ``all other employees`` excludes those groups as well as
        technical employees.  Retrieval should still keep the proxy evidence, but
        it cannot support a fully-disclosed result unless the report supplies an
        exact all-other value or explicitly defines the two categories as equal.
        """
        target = str(metric_name or "").replace("\u2013", "-").replace("\u2014", "-")
        all_other_pattern = re.compile(
            r"\ball[\s-]+other[\s-]+employees?\b",
            re.IGNORECASE,
        )
        if (
            not all_other_pattern.search(target)
            or not re.search(r"\brepresentation\b", target, re.IGNORECASE)
        ):
            return False

        normalized_segments = [
            re.sub(
                r"\s+",
                " ",
                str(value or "")
                .replace("\u2010", "-")
                .replace("\u2011", "-")
                .replace("\u2012", "-")
                .replace("\u2013", "-")
                .replace("\u2014", "-")
                .lower(),
            ).strip()
            for value in evidence_segments or []
            if str(value or "").strip()
        ]
        non_technical_pattern = re.compile(
            r"\bnon[\s-]*technical[\s-]+(?:roles?|employees?|workforce)\b",
            re.IGNORECASE,
        )
        if not any(
            non_technical_pattern.search(value) for value in normalized_segments
        ):
            return False

        # An actual all-other row with a reported percentage is exact evidence.
        # Merely seeing the framework label in a SASB index/link source is not.
        reported_percent_pattern = re.compile(
            r"(?<![\w.])[-+]?(?:\d[\d,]*)(?:\.\d+)?\s*%"
        )
        for value in normalized_segments:
            for exact_match in all_other_pattern.finditer(value):
                following = value[exact_match.end(): exact_match.end() + 500]
                reported_value = reported_percent_pattern.search(following)
                proxy_label = non_technical_pattern.search(following)
                if reported_value and (
                    proxy_label is None or reported_value.start() < proxy_label.start()
                ):
                    return False

        joined = "\n".join(normalized_segments)
        non_technical_label = r"non[\s-]*technical[\s-]+(?:roles?|employees?)"
        all_other_label = r"all[\s-]+other[\s-]+employees?"
        forward_relation = re.compile(
            rf"\b{non_technical_label}\b.{{0,180}}\b(?:"
            r"means?|refers?\s+to|is\s+(?:the\s+)?(?:reporting\s+)?label\s+for|"
            r"are\s+(?:the\s+)?(?:reporting\s+)?label\s+for|"
            r"is\s+defined\s+as|are\s+defined\s+as|equivalent\s+to|"
            r"same\s+as|corresponds?\s+to|maps?\s+to"
            rf")\b.{{0,180}}\b{all_other_label}\b",
            re.IGNORECASE,
        )
        reverse_relation = re.compile(
            rf"\b{all_other_label}\b.{{0,180}}\b(?:"
            r"means?|refers?\s+to|is\s+(?:reported|labelled|labeled)\s+as|"
            r"are\s+(?:reported|labelled|labeled)\s+as|"
            r"is\s+defined\s+as|are\s+defined\s+as|equivalent\s+to|"
            r"same\s+as|corresponds?\s+to|maps?\s+to"
            rf")\b.{{0,180}}\b{non_technical_label}\b",
            re.IGNORECASE,
        )
        if forward_relation.search(joined) or reverse_relation.search(joined):
            return False

        # Also accept the framework boundary stated by definition even when the
        # report does not repeat the literal phrase "all other employees".
        exclusion_cue_pattern = re.compile(
            r"\b(?:not\s+classified\s+as|outside|exclud(?:e|es|ed|ing)|"
            r"except(?:\s+for)?|other\s+than|neither)\b",
            re.IGNORECASE,
        )
        non_executive_pattern = re.compile(
            r"\bnon[\s-]*executive\s+management\b",
            re.IGNORECASE,
        )
        for value in normalized_segments:
            if (
                not non_technical_pattern.search(value)
                or not exclusion_cue_pattern.search(value)
            ):
                continue
            without_non_executive = non_executive_pattern.sub(" ", value)
            without_non_technical = non_technical_pattern.sub(" ", value)
            if (
                re.search(r"\bexecutive\s+management\b", without_non_executive)
                and non_executive_pattern.search(value)
                and re.search(
                    r"\btechnical\s+(?:employees?|roles?)\b",
                    without_non_technical,
                )
            ):
                return False

        return True

    def _analyze_single_metric(
        self, 
        retrieval_result: MetricRetrievalResult,
        report_content: ReportContent,
        metric: Optional['ESGMetric'] = None
    ) -> DisclosureAnalysis:
        """
        Analyze disclosure status of a single metric
        
        Args:
            retrieval_result: Retrieval result for single metric
            report_content: Report content
            
        Returns:
            DisclosureAnalysis: Analysis result for this metric
        """
        # Get relevant segment content and tag information
        relevant_segments = []
        category_boundary_segments = []
        evidence_segment_ids = []
        segment_metadata = []
        metric_profile = self._resolve_metric_profile(metric, retrieval_result)

        # Retrieval has already computed and applied the dynamic target window.
        # Do not impose a second fixed 46-segment truncation here.
        final_window = int(
            getattr(retrieval_result, "target_k", 0)
            or len(retrieval_result.combined_results or [])
        )
        max_evidence_chars = _positive_env_int(
            "REPORT_ANALYSIS_MAX_CHARS_PER_EVIDENCE",
            8192,
            minimum=600,
            maximum=8192,
        )
        for result in retrieval_result.combined_results[:final_window]:
            # Prefer segment from report_content; fallback to retrieval payload (more robust across caches)
            segment = None
            try:
                segment = self._get_segment_by_id(report_content, result.segment_id)
            except Exception:
                segment = None

            page_number = getattr(segment, "page_number", None) if segment is not None else getattr(result, "page_number", None)
            content = self._build_augmented_segment_context(
                report_content=report_content,
                segment_id=result.segment_id,
                fallback_content=getattr(result, "content", None),
            )
            category_boundary_content = content

            link_source_context = None
            link_source_segment_id = getattr(result, "link_source_segment_id", None)
            if getattr(result, "link_target_page", None) is not None:
                content, link_source_context, link_source_segment_id = self._build_linked_evidence_context(
                    report_content=report_content,
                    result=result,
                    target_context=content,
                    max_chars=max_evidence_chars,
                )

            if content:
                if getattr(result, "link_target_page", None) is None:
                    content = self._truncate_segment_text(content, max_evidence_chars)
                relevant_segments.append(content)
                category_boundary_segments.append(category_boundary_content or content)
                evidence_segment_ids.append(result.segment_id)

                table_id, row_index = self._get_table_row_key(segment) if segment is not None else (None, None)
                col_index = self._get_table_column_index(segment) if segment is not None else None
                structured = getattr(segment, "structured_data", None) if segment is not None else None
                structured = structured if isinstance(structured, dict) else {}
                metadata = {
                    "segment_id": result.segment_id,
                    "page_number": page_number,
                    "score": getattr(result, "score", 0),
                    "retrieval_type": getattr(result, "retrieval_type", ""),
                    "matched_keywords": getattr(result, "matched_keywords", None),
                    "source_table_id": table_id,
                    "row_index": row_index,
                    "column_index": col_index,
                    "link_source_page": getattr(result, "link_source_page", None),
                    "link_target_page": getattr(result, "link_target_page", None),
                    "link_anchor_text": getattr(result, "link_anchor_text", None),
                    "link_source_segment_id": link_source_segment_id,
                    "link_source_context": link_source_context,
                    "source_report_id": (
                        getattr(result, "source_report_id", None)
                        or structured.get("source_report_id")
                    ),
                    "source_report_name": (
                        getattr(result, "source_report_name", None)
                        or structured.get("source_report_name")
                    ),
                    "source_report_year": (
                        getattr(result, "source_report_year", None)
                        or structured.get("source_report_year")
                    ),
                    "evidence_type": getattr(result, "evidence_type", None) or structured.get("evidence_type"),
                    "asset_id": getattr(result, "asset_id", None) or structured.get("asset_id"),
                    "bbox": getattr(result, "bbox", None) or structured.get("bbox"),
                    "caption": getattr(result, "caption", None) or structured.get("caption") or structured.get("summary"),
                    "confidence": getattr(result, "confidence", None) or structured.get("confidence"),
                    "chart_data": getattr(result, "chart_data", None) or structured.get("chart_data"),
                    "structure_confidence": getattr(result, "structure_confidence", None) or structured.get("structure_confidence"),
                    "ocr_confidence": getattr(result, "ocr_confidence", None) or structured.get("ocr_confidence"),
                    "header_path": getattr(result, "header_path", None) or structured.get("header_path") or [],
                    "rowspan": getattr(result, "rowspan", 1),
                    "colspan": getattr(result, "colspan", 1),
                    "parse_pass": getattr(result, "parse_pass", 1),
                    "review_status": getattr(result, "review_status", None) or structured.get("review_status"),
                    "conflicts": getattr(result, "conflicts", None) or structured.get("conflicts") or [],
                }
                segment_metadata.append(metadata)

        # Deterministic high-confidence shortcut:
        # If the retrieved evidence itself contains the current metric code AND
        # a directly extractable data value, classify it as fully_disclosed
        # before asking the LLM. This prevents the LLM from downgrading clear
        # same-code + data table rows because of over-checking definitions.
        direct_analysis = self._direct_code_data_disclosure_analysis(
            retrieval_result=retrieval_result,
            report_content=report_content,
            metric=metric,
            segment_metadata=segment_metadata,
            evidence_segment_ids=evidence_segment_ids,
            metric_profile=metric_profile,
        )
        if direct_analysis is not None:
            return direct_analysis

        # If parsing did not recover a scalar, an explicit report statement that
        # contains both the exact current code and current metric label is still a
        # deterministic disclosure.  This also prevents unrelated retrieved
        # diversity/workforce passages from overruling the direct code evidence.
        direct_label_analysis = self._direct_code_label_disclosure_analysis(
            retrieval_result=retrieval_result,
            report_content=report_content,
            metric=metric,
            segment_metadata=segment_metadata,
            evidence_segment_ids=evidence_segment_ids,
            metric_profile=metric_profile,
        )
        if direct_label_analysis is not None:
            return direct_label_analysis

        prompt_segments = relevant_segments
        prompt_metadata = segment_metadata
        chunk_token_budget = _positive_env_int(
            "REPORT_ANALYSIS_EVIDENCE_CHUNK_TOKEN_BUDGET",
            24000,
            minimum=4000,
        )
        if self._estimate_evidence_tokens(relevant_segments, segment_metadata) > chunk_token_budget:
            prompt_segments, prompt_metadata = self._summarize_evidence_chunks(
                retrieval_result,
                relevant_segments,
                segment_metadata,
                metric,
                chunk_token_budget,
            )

# Build prompt containing tag information
        prompt = self._build_analysis_prompt(
            retrieval_result.metric_name,
            retrieval_result.metric_id,
            prompt_segments,
            prompt_metadata,
            metric_unit=(getattr(metric, "unit", None) or "") if metric else "",
            metric_description=((getattr(metric, "definition", None) or getattr(metric, "description", None) or "").strip())
            if metric
            else "",
            metric_code=(getattr(metric, "metric_code", None) or retrieval_result.metric_id) if metric else retrieval_result.metric_id,
            metric_topic=(getattr(metric, "sasb_topic", None) or "") if metric else "",
            metric_category=(getattr(metric, "sasb_category", None) or "") if metric else "",
            metric_type=(getattr(metric, "sasb_type", None) or "") if metric else "",
            metric_keywords=(getattr(metric, "keywords", None) or []) if metric else [],
            metric_profile=metric_profile,
        )
        
        try:
            json_example = """
            {
              "metric_hit": true,
              "disclosure_status": "fully_disclosed",
              "has_disclosure": true,
              "disclosure_quality": "high",
              "value_status": "converted",
              "reasoning": "The report clearly addresses the metric and discloses total energy use as 511 MWh, which can be safely converted to 1839.6 GJ.",
              "value": 1839.6,
              "raw_value": 511,
              "raw_unit": "MWh",
              "page": 23,
              "evidence_segment_id": "SEG_000123",
              "evidence_quote": "Total energy use was 511 MWh in FY2024...",
              "specific_data_found": "511 MWh (FY2024), normalized to 1839.6 GJ",
              "year_values": [
                {
                  "year": 2023,
                  "value": 478,
                  "raw_value": 478,
                  "unit": "MWh",
                  "page": 23,
                  "evidence_segment_id": "SEG_000123",
                  "evidence_quote": "FY2023 total energy use: 478 MWh"
                },
                {
                  "year": 2024,
                  "value": 511,
                  "raw_value": 511,
                  "unit": "MWh",
                  "page": 23,
                  "evidence_segment_id": "SEG_000123",
                  "evidence_quote": "FY2024 total energy use: 511 MWh"
                }
              ],
              "derived_calculation": null,
              "improvement_suggestions": []
            }
            """

            system_prompt_text = f"""
            You are a professional ESG compliance analysis expert. Please analyze metric
            disclosure status based on the provided information.

            Respond ONLY with a JSON object in the following format. Do not include
            any other text, explanations, or especially, markdown backticks.

            Example Format:
            {json_example}
            """
            
            system_prompt_json = """
You are a professional ESG/SASB disclosure assessment expert.
You must directly decide the final disclosure_status as exactly one of: fully_disclosed, partially_disclosed, not_disclosed.
The disclosure_status field is the final model classification. Python will not infer a different status from metric_hit, has_disclosure, disclosure_quality, value_status, units, or numeric values; it may only reject a fully_disclosed result through a narrow deterministic employee-category boundary check.

Assessment principles:
- Use only the provided metric information and retrieved report segments.
- The current Metric Name is the target being assessed. When the SASB definition contains multiple numbered components under the same code, do not assess the entire combined definition as the current metric unless those components are part of the current Metric Name.
- The metric definition/guidance is interpretive context, not an exhaustive checklist. Use it to understand the metric core, denominator, required split, and measurement basis. Do not require every technical-protocol clause, note, example, or auxiliary guidance item to appear.
- A retrieved report segment that explicitly contains the current SASB metric code and a metric-specific value or narrative is the strongest evidence of disclosure. When the row/section label semantically belongs to the current metric or current code-level metric family, treat it as a direct metric hit.
- If a same-code report row/table or same-metric-label row/table provides a numeric value or narrative for the current metric, classify it as fully_disclosed. Do not downgrade because sibling sub-items under the same code are missing, because the unit is different but convertible, because the report does not restate the conversion formula, or because the framework definition contains additional guidance.
- For split metrics under one SASB code, assess the current sub-item only. Missing sibling sub-items must not reduce the status for the current sub-item. A value for a clearly different sub-item should not be used as this sub-item's value.
- For internal PDF links, assess the link-source row and its surrounding context together with the linked target-page evidence. The source row can itself contain a valid disclosure value; do not discard it merely because it also contains a link or appears in a reporting-framework index.
- For representation/distribution metrics, a table can legitimately contain several group percentages rather than one scalar. If the requested employee category and reporting period are present, treat the distribution as disclosed evidence; set value to null/ambiguous when no single scalar represents the metric and preserve the reported percentages in specific_data_found/evidence_quote. Do not classify it as not_disclosed merely because the evidence has multiple values.
- When report categories are broader proxies rather than exact framework categories (for example, "people leader roles" versus executive/non-executive management, or "non-technical roles" versus all other employees), use partially_disclosed and state the category mismatch. An exact category such as "Technical" is stronger evidence.
- If the report label is semantically aligned with the current metric, do not require verbatim wording from the framework. Category labels, employee groups, product labels, operational labels, and line items may be equivalent even when phrased differently.
- Unit differences must be handled by judgment. If the reported value can be safely converted, normalized, or interpreted as an equivalent unit for the current metric, keep the disclosure as fully_disclosed when the metric itself is directly disclosed. Provide raw_value/raw_unit and converted value when possible.
- Do not downgrade from fully_disclosed solely because of source unit wording, reporting-unit wording, missing conversion narrative, non-core scope uncertainty, or definition/guidance text when the reported value is directly usable for the current metric.
- Never put narrative text in value. Do not choose a number from a clearly different line item or clearly different sub-item.
- Extract every explicitly disclosed annual value for the current metric into year_values. Keep one metric assessment with multiple year entries; do not discard older years merely because value contains the latest year.
- Each year_values item must bind one year to its own metric-specific value and evidence. Do not infer missing years and do not copy one year's value into another year.
- Derive a value only when the metric definition explicitly states the formula and every operand is present in the evidence with the same year, reporting boundary and compatible units. Otherwise derived_calculation must be null.
"""

            FORCE_JSON = True # If model outputs thought train in response
            
            api_kwargs = {
                "model": self.config.llm_model,
                "temperature": 0.2  # Lower for more stable JSON extraction
            }
            
            queries = []
            
            if (FORCE_JSON):
                if _is_claude_model(self.config.llm_model):
                    api_kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "disclosure_analysis",
                            "strict": True,
                            "schema": DISCLOSURE_JSON_SCHEMA,
                        },
                    }
                else:
                    api_kwargs["response_format"] = {"type": "json_object"}
                queries.append({"role": "system", "content": system_prompt_json})
                queries.append({"role": "user", "content": prompt})

                # (Optional) Add the assistant prefill for models like Claude
                # messages.append({"role": "assistant", "content": "{"}) 
            else:
                queries.append({"role": "system", "content": system_prompt_text})
                queries.append({"role": "user", "content": prompt})

            api_kwargs["messages"] = queries

            # Call LLM for analysis
            try:
                response = self.llm_client.chat.completions.create(**api_kwargs)
            except Exception as exc:
                logger.exception(
                    f"LLM request failed for metric {retrieval_result.metric_name}"
                )
                raise DisclosureAnalysisError(
                    f"LLM request failed for metric '{retrieval_result.metric_name}'.",
                    metric_id=retrieval_result.metric_id,
                    metric_name=retrieval_result.metric_name,
                    error_type="llm_request_failed",
                ) from exc
            
            #print("======== DEBUG LLM RESPONSE ========")
            #print(response.choices[0].message.content)
            
            # Parse JSON response (response_format=json_object should already guarantee JSON)
            content = (response.choices[0].message.content or "").strip()
            llm_result = self._extract_json_from_llm_response(content)
            if llm_result is None:
                logger.error(
                    f"LLM did not return valid JSON for metric "
                    f"{retrieval_result.metric_name}"
                )
                raise DisclosureAnalysisError(
                    f"LLM returned invalid JSON for metric '{retrieval_result.metric_name}'.",
                    metric_id=retrieval_result.metric_id,
                    metric_name=retrieval_result.metric_name,
                    error_type="invalid_llm_json",
                )

            # Validate required fields from LLM
            if "reasoning" not in llm_result or not llm_result["reasoning"]:
                logger.error(
                    f"LLM response missing required 'reasoning' for metric "
                    f"{retrieval_result.metric_name}"
                )
                raise DisclosureAnalysisError(
                    f"LLM response validation failed for metric "
                    f"'{retrieval_result.metric_name}'.",
                    metric_id=retrieval_result.metric_id,
                    metric_name=retrieval_result.metric_name,
                    error_type="invalid_llm_response",
                )

            # -----------------------------
            # Read LLM final status / value / page / context
            # -----------------------------
            # IMPORTANT:
            # disclosure_status is normally decided by the prompt/LLM directly.
            # Python does not infer it from auxiliary response fields, but applies
            # narrow deterministic evidence-boundary validation below.
            try:
                disclosure_status = self._map_llm_disclosure_status(llm_result)
            except ValueError as exc:
                raise DisclosureAnalysisError(
                    f"LLM response validation failed for metric "
                    f"'{retrieval_result.metric_name}'.",
                    metric_id=retrieval_result.metric_id,
                    metric_name=retrieval_result.metric_name,
                    error_type="invalid_llm_response",
                ) from exc

            metric_name_for_boundary = (
                getattr(metric, "metric_name", None)
                if metric is not None
                else None
            ) or retrieval_result.metric_name
            category_boundary_evidence = category_boundary_segments
            raw_boundary_page = llm_result.get("page")
            try:
                boundary_page_match = re.search(r"\d+", str(raw_boundary_page))
                boundary_page = (
                    int(boundary_page_match.group(0))
                    if boundary_page_match is not None
                    else None
                )
            except (TypeError, ValueError):
                boundary_page = None
            if boundary_page is not None:
                page_local_evidence = [
                    evidence
                    for evidence, metadata in zip(
                        category_boundary_segments,
                        segment_metadata,
                    )
                    if metadata.get("page_number") == boundary_page
                ]
                if page_local_evidence:
                    category_boundary_evidence = page_local_evidence
            if (
                disclosure_status == DisclosureStatus.FULLY_DISCLOSED
                and self._all_other_employee_category_is_only_proxy(
                    metric_name_for_boundary,
                    category_boundary_evidence,
                )
            ):
                disclosure_status = DisclosureStatus.PARTIALLY_DISCLOSED
                llm_result["reasoning"] = (
                    "The report provides representation data for its "
                    "'Non-technical roles' category, which is relevant proxy "
                    "evidence. However, the evidence does not define that category "
                    "as SASB 'all other employees' (employees outside executive "
                    "management, non-executive management, and technical employees). "
                    "Because the employee-category boundary is not exact, the metric "
                    "is partially disclosed."
                )
                boundary_suggestion = (
                    "Define how 'Non-technical roles' maps to SASB 'all other "
                    "employees', or report the representation distribution for the "
                    "SASB category directly."
                )
                suggestions = llm_result.get("improvement_suggestions")
                suggestions = list(suggestions) if isinstance(suggestions, list) else []
                if boundary_suggestion not in suggestions:
                    suggestions.insert(0, boundary_suggestion)
                llm_result["improvement_suggestions"] = suggestions
                logger.info(
                    "Adjusted disclosure status to partially_disclosed for metric "
                    f"{retrieval_result.metric_id}: report category "
                    "'Non-technical roles' is not explicitly equivalent to SASB "
                    "'all other employees'."
                )

            llm_value_status = _normalize_value_status(llm_result.get("value_status"))
            preserve_ambiguous_value = llm_value_status == "ambiguous"

            found_numeric = _parse_llm_numeric_value_only(llm_result.get("value", None))
            if found_numeric is None:
                found_numeric = _parse_llm_numeric_value_only(llm_result.get("raw_value", None))
            validated_derived_calculation = self._validated_derived_calculation(
                llm_result,
                metric,
                segment_metadata,
            )
            if validated_derived_calculation is not None:
                found_numeric = validated_derived_calculation["result"]
            elif llm_value_status == "derived":
                found_numeric = None
            elif preserve_ambiguous_value:
                # A distribution is valid evidence but may not have one canonical
                # scalar. Do not replace that explicit decision with an arbitrary
                # table-cell value during deterministic fallback processing.
                found_numeric = None

            # A scalar explicitly selected by the LLM, or produced by a
            # validated calculation, has precedence over deterministic table
            # candidates. Those candidates remain available as evidence and as
            # a fallback only when no authoritative scalar was selected.
            authoritative_scalar_selected = found_numeric is not None

            found_page = None
            found_context = None

            # Candidate pages set (for validation)
            candidate_pages = {
                m.get("page_number")
                for m in (segment_metadata or [])
                if m.get("page_number") is not None
            }

            # 1) Prefer page specified by LLM if valid
            llm_page = llm_result.get("page", None)
            if llm_page is not None:
                try:
                    # Allow formats like "p. 12" / "page 12"
                    mpage = re.search(r"\d+", str(llm_page))
                    llm_page_int = int(mpage.group(0)) if mpage else int(str(llm_page).strip())
                    if (not candidate_pages) or (llm_page_int in candidate_pages):
                        found_page = llm_page_int
                except Exception:
                    pass

            # 2) If LLM returned an evidence segment id, map it back to page
            evidence_seg_id = llm_result.get("evidence_segment_id", None)
            if found_page is None and evidence_seg_id and segment_metadata:
                for meta in segment_metadata:
                    if meta.get("segment_id") == evidence_seg_id and meta.get("page_number") is not None:
                        found_page = meta.get("page_number")
                        break

            # 3) As fallback, pick page of highest scoring segment (or top retrieval result)
            best_segment_meta = None
            if segment_metadata:
                best_segment_meta = max(segment_metadata, key=lambda x: x.get("score", 0) or 0)
                if found_page is None:
                    found_page = best_segment_meta.get("page_number")

            if found_page is None:
                try:
                    top = (retrieval_result.combined_results or [])[0]
                    if top and getattr(top, "page_number", None) is not None:
                        found_page = top.page_number
                except Exception:
                    pass

            # 4) Context/value: for table-cell evidence, preserve the full table row and prefer latest-year value.
            selected_segment_id = evidence_seg_id or (best_segment_meta or {}).get("segment_id")
            selected_row_context = None
            selected_latest_numeric = None
            selected_latest_unit = None
            selected_metric_candidate = self._select_metric_candidate_from_exact_code_evidence(
                report_content,
                segment_metadata,
                metric,
                self._metric_code_candidates(retrieval_result, metric),
                metric_profile,
            )
            if selected_segment_id:
                try:
                    selected_segment = self._get_segment_by_id(report_content, selected_segment_id)
                    if selected_segment is not None:
                        row_ctx, latest_num, latest_unit, _latest_desc = self._build_table_row_aggregation_context(
                            report_content, selected_segment, max_chars=1600
                        )
                        if row_ctx:
                            selected_row_context = row_ctx
                            selected_latest_numeric = latest_num
                            selected_latest_unit = latest_unit
                        selected_segment_candidate = self._select_metric_numeric_candidate(
                            report_content,
                            selected_segment,
                            metric,
                            self._metric_code_candidates(retrieval_result, metric),
                            metric_profile,
                        )
                        if selected_metric_candidate is None:
                            selected_metric_candidate = selected_segment_candidate
                except Exception:
                    pass

            # Prefer the LLM-returned metric-specific value. Table row aggregation is
            # only a fallback because aggregated cells can contain paired sibling values
            # from the same table row (for example an absolute value plus a percentage).
            if (
                disclosure_status != DisclosureStatus.NOT_DISCLOSED
                and not preserve_ambiguous_value
                and found_numeric is None
                and selected_latest_numeric is not None
                and not (
                    metric_profile is not None
                    and metric_profile.value_selection_rules.get(
                        "do_not_select_first_of_multiple_unlabeled_values", False
                    )
                )
            ):
                expected_unit = getattr(metric, "unit", "") if metric else ""
                converted_latest = _convert_numeric_value_between_units(selected_latest_numeric, selected_latest_unit, expected_unit)
                found_numeric = converted_latest if converted_latest is not None else selected_latest_numeric

            # For multi-value rows, use the candidate selected by the current
            # sub-metric name/unit. This is part of normal analysis, not the
            # same-code deterministic shortcut.
            if (
                disclosure_status != DisclosureStatus.NOT_DISCLOSED
                and not preserve_ambiguous_value
                and found_numeric is None
                and selected_metric_candidate is not None
                and validated_derived_calculation is None
            ):
                candidate_value = selected_metric_candidate.get("value")
                candidate_unit = selected_metric_candidate.get("unit")
                expected_unit = getattr(metric, "unit", "") if metric else ""
                converted_candidate = _convert_numeric_value_between_units(candidate_value, candidate_unit, expected_unit)
                found_numeric = converted_candidate if converted_candidate is not None else candidate_value
                candidate_segment = selected_metric_candidate.get("segment")
                candidate_segment_id = getattr(candidate_segment, "segment_id", None)
                if candidate_segment_id and candidate_segment_id not in evidence_segment_ids:
                    evidence_segment_ids.insert(0, candidate_segment_id)
                candidate_page = getattr(candidate_segment, "page_number", None)
                if candidate_page is not None:
                    found_page = candidate_page

            year_values: List[Dict[str, Any]] = []
            selected_year: Optional[int] = None
            selected_year_context: Optional[str] = None
            value_conflict = False
            target_year = self._target_year_for_metric(metric)
            if disclosure_status != DisclosureStatus.NOT_DISCLOSED:
                code_candidates = self._metric_code_candidates(retrieval_result, metric)
                deterministic_year_values = self._collect_metric_year_values_from_exact_code_evidence(
                    report_content,
                    segment_metadata,
                    metric,
                    code_candidates,
                    metric_profile,
                )
                selected_candidate_year_values = (
                    self._metric_year_values_from_candidates(
                        [selected_metric_candidate], metric, metric_profile
                    )
                    if selected_metric_candidate is not None
                    else []
                )
                llm_year_values = self._normalise_llm_year_values(
                    llm_result,
                    metric,
                    segment_metadata,
                    metric_profile,
                )
                derived_year_values: List[Dict[str, Any]] = []
                if validated_derived_calculation is not None:
                    derived_year = validated_derived_calculation.get("year")
                    derived_result = validated_derived_calculation.get("result")
                    if derived_year is not None and derived_result is not None:
                        derived_year_values.append(
                            {
                                "year": derived_year,
                                "value": derived_result,
                                "raw_value": derived_result,
                                "raw_unit": getattr(metric, "unit", "") if metric else "",
                                "unit": getattr(metric, "unit", "") if metric else "",
                                "page": found_page,
                                "context": validated_derived_calculation.get("formula"),
                                "evidence_segment_id": evidence_seg_id,
                                "source": "derived_calculation",
                            }
                        )
                year_values = self._merge_metric_year_values(
                    *[
                        self._attach_year_value_sources(values, segment_metadata)
                        for values in (
                            derived_year_values,
                            llm_year_values,
                            deterministic_year_values,
                            selected_candidate_year_values,
                        )
                    ]
                )
                candidate_selected_year, selected_year_value = self._select_metric_year_value(
                    year_values,
                    target_year,
                    metric_profile,
                )
                selected_year = candidate_selected_year
                value_conflict = self._year_has_conflicting_values(
                    year_values,
                    candidate_selected_year,
                )
                if value_conflict:
                    found_numeric = None
                elif selected_year_value is not None and not preserve_ambiguous_value:
                    selected_source = str(selected_year_value.get("source") or "")
                    authoritative_year_value = selected_source in {
                        "llm_evidence",
                        "derived_calculation",
                    }
                    if not authoritative_scalar_selected or authoritative_year_value:
                        found_numeric = selected_year_value.get("value")
                        if selected_year_value.get("page") is not None:
                            found_page = selected_year_value.get("page")
                        selected_year_context = str(
                            selected_year_value.get("context") or ""
                        ).strip() or None
                        selected_year_segment = selected_year_value.get("evidence_segment_id")
                        if selected_year_segment and selected_year_segment not in evidence_segment_ids:
                            evidence_segment_ids.insert(0, selected_year_segment)
                elif target_year is not None:
                    # Never silently substitute the latest value when the caller
                    # explicitly requested a year that is absent or ambiguous.
                    found_numeric = None
                    found_page = None

                if (
                    metric_profile is not None
                    and metric_profile.output_shape in {"breakdown", "table"}
                    and metric_profile.variable_dimensions
                ):
                    found_numeric = None

            # Prefer the LLM-selected quote/context. Use full-row context only when
            # the LLM did not return metric-specific context.
            quote = llm_result.get("evidence_quote", None)
            if quote:
                found_context = str(quote).strip()
            else:
                specific_data = llm_result.get("specific_data_found", None)
                if specific_data:
                    if isinstance(specific_data, list):
                        found_context = "; ".join(str(x) for x in specific_data if x is not None).strip() or None
                    else:
                        found_context = str(specific_data).strip() or None

            if (
                not found_context
                and not preserve_ambiguous_value
                and selected_metric_candidate is not None
            ):
                found_context = str(selected_metric_candidate.get("description") or "").strip() or None

            if (
                disclosure_status != DisclosureStatus.NOT_DISCLOSED
                and not found_context
                and selected_row_context
            ):
                found_context = selected_row_context

            if not found_context and best_segment_meta is not None:
                # Provide a short excerpt for UI hover to reduce "empty" feeling.
                idx = None
                # Map back to segments list by order
                for i, meta in enumerate(segment_metadata):
                    if meta.get("segment_id") == best_segment_meta.get("segment_id"):
                        idx = i
                        break
                if idx is not None and idx < len(relevant_segments):
                    excerpt = str(relevant_segments[idx]).strip()
                    if excerpt:
                        found_context = excerpt[:900]

            if selected_year_context:
                found_context = selected_year_context

            if disclosure_status == DisclosureStatus.NOT_DISCLOSED:
                stored_value: Union[int, float, str] = COMPLIANCE_VALUE_NA
            elif value_conflict:
                stored_value = COMPLIANCE_VALUE_NA
            else:
                stored_value = _finalize_compliance_value_field(found_numeric)

            # Create analysis result
            analysis = DisclosureAnalysis(
                metric_id=retrieval_result.metric_id,
                metric_name=retrieval_result.metric_name,
                metric_code=retrieval_result.metric_code,
                disclosure_status=disclosure_status,
                reasoning=llm_result["reasoning"],
                evidence_segments=evidence_segment_ids,
                improvement_suggestions=llm_result.get("improvement_suggestions", []),  # This field is optional
                # SASB display fields
                category=getattr(metric, 'sasb_category', '') if metric else '',
                topic=(getattr(metric, 'sasb_topic', None) or '') if metric else '',
                unit=getattr(metric, 'unit', '') or '' if metric else '',
                type=getattr(metric, 'sasb_type', '') if metric else '',
                definition=(getattr(metric, 'definition', None) or '') if metric else '',
                value=stored_value,
                year_values=year_values,
                selected_year=selected_year,
                value_status="conflict" if value_conflict else (llm_value_status or None),
                context=found_context,
                page=found_page,
                evidence_sources=self._build_evidence_sources(
                    segment_metadata,
                    preferred_segment_id=evidence_seg_id or selected_segment_id,
                    preferred_page=found_page,
                ),
                derived_calculation=validated_derived_calculation,
            )
            
        except DisclosureAnalysisError:
            raise
        except json.JSONDecodeError as exc:
            logger.exception(
                f"Failed to parse LLM JSON for metric {retrieval_result.metric_name}"
            )
            raise DisclosureAnalysisError(
                f"LLM returned invalid JSON for metric '{retrieval_result.metric_name}'.",
                metric_id=retrieval_result.metric_id,
                metric_name=retrieval_result.metric_name,
                error_type="invalid_llm_json",
            ) from exc
        except Exception as exc:
            logger.exception(
                f"Disclosure analysis failed for metric {retrieval_result.metric_name}"
            )
            raise DisclosureAnalysisError(
                f"Disclosure analysis failed for metric "
                f"'{retrieval_result.metric_name}'.",
                metric_id=retrieval_result.metric_id,
                metric_name=retrieval_result.metric_name,
                error_type="analysis_execution_failed",
            ) from exc

        return analysis

    def _validated_derived_calculation(
        self,
        llm_result: Dict[str, Any],
        metric: Optional['ESGMetric'],
        segment_metadata: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        raw = llm_result.get("derived_calculation")
        if not isinstance(raw, dict):
            return None
        operation = str(raw.get("operation") or "").strip().lower()
        if operation not in {"ratio_percent", "ratio", "sum", "difference"}:
            return None
        formula = str(raw.get("formula") or "").strip()
        if not formula:
            return None

        definition = str(
            getattr(metric, "definition", "") or getattr(metric, "description", "") or ""
        ).lower() if metric is not None else ""
        formula_cues = {
            "ratio_percent": ("divided by", "percentage", "rate as", "ratio"),
            "ratio": ("divided by", "ratio"),
            "sum": ("sum of", "calculated as the sum", "total of"),
            "difference": ("difference between", "subtract"),
        }
        if not definition or not any(cue in definition for cue in formula_cues[operation]):
            return None

        operands = raw.get("operands")
        if not isinstance(operands, list) or len(operands) < 2:
            return None
        if operation == "difference" and len(operands) != 2:
            return None
        valid_segment_ids = {
            str(item.get("segment_id")) for item in segment_metadata if item.get("segment_id")
        }
        parsed: List[Dict[str, Any]] = []
        for operand in operands:
            if not isinstance(operand, dict):
                return None
            value = _parse_llm_numeric_value_only(operand.get("value"))
            unit = str(operand.get("unit") or "").strip()
            boundary = re.sub(r"\s+", " ", str(operand.get("boundary") or "").strip().lower())
            segment_id = str(operand.get("segment_id") or "").strip()
            try:
                year = int(operand.get("year"))
            except Exception:
                return None
            if value is None or not unit or not boundary or segment_id not in valid_segment_ids:
                return None
            parsed.append(
                {
                    "name": str(operand.get("name") or "").strip(),
                    "value": value,
                    "unit": unit,
                    "year": year,
                    "boundary": str(operand.get("boundary") or "").strip(),
                    "segment_id": segment_id,
                }
            )

        years = {item["year"] for item in parsed}
        boundaries = {re.sub(r"\s+", " ", item["boundary"].strip().lower()) for item in parsed}
        if len(years) != 1 or len(boundaries) != 1:
            return None

        values = [float(item["value"]) for item in parsed]
        if operation in {"ratio", "ratio_percent"}:
            left_profile = _unit_profile(parsed[0]["unit"])
            right_profile = _unit_profile(parsed[1]["unit"])
            if left_profile is None or right_profile is None or left_profile[0] != right_profile[0]:
                return None
            denominator = values[1] * _extract_unit_multiplier(parsed[1]["unit"]) * right_profile[1]
            if denominator == 0:
                return None
            numerator = values[0] * _extract_unit_multiplier(parsed[0]["unit"]) * left_profile[1]
            result = numerator / denominator
            if operation == "ratio_percent":
                result *= 100.0
        else:
            result_unit = parsed[0]["unit"]
            compatible_values = [values[0]]
            for item, value in zip(parsed[1:], values[1:]):
                if _normalize_unit_text(item["unit"]).lower() == _normalize_unit_text(result_unit).lower():
                    compatible_values.append(value)
                    continue
                converted = _convert_numeric_value_between_units(value, item["unit"], result_unit)
                if converted is None:
                    return None
                compatible_values.append(float(converted))
            result = (
                sum(compatible_values)
                if operation == "sum"
                else compatible_values[0] - compatible_values[1]
            )

        if operation == "ratio_percent":
            result_unit = "%"
        elif operation == "ratio":
            result_unit = "ratio"
        else:
            result_unit = parsed[0]["unit"]

        return {
            "operation": operation,
            "formula": formula,
            "operands": parsed,
            "result": _clean_converted_number(result),
            "result_unit": result_unit,
            "year": next(iter(years)),
            "boundary": parsed[0]["boundary"],
        }

    def _build_evidence_sources(
        self,
        segment_metadata: List[Dict[str, Any]],
        preferred_segment_id: Optional[str] = None,
        preferred_page: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ordered = sorted(
            list(segment_metadata or []),
            key=lambda item: (
                1 if preferred_segment_id and item.get("segment_id") == preferred_segment_id else 0,
                1 if item.get("link_target_page") else 0,
                float(item.get("score", 0) or 0),
            ),
            reverse=True,
        )
        sources: List[Dict[str, Any]] = []
        seen = set()
        for item in ordered:
            data_page = item.get("page_number")
            link_source_page = item.get("link_source_page")
            link_target_page = item.get("link_target_page")
            source_report_id = item.get("source_report_id")
            source_report_name = item.get("source_report_name")
            source_report_year = item.get("source_report_year")
            source_type = "linked_page" if link_target_page else "report_page"
            key = (
                source_report_id,
                source_type,
                data_page,
                link_source_page,
                link_target_page,
            )
            if key in seen:
                continue
            seen.add(key)
            source: Dict[str, Any] = {
                "source_type": source_type,
                "data_page": data_page,
                "segment_id": item.get("segment_id"),
            }
            for field in ("evidence_type", "asset_id", "bbox", "caption", "confidence", "chart_data"):
                if item.get(field) is not None:
                    source[field] = item[field]
            for field in ("structure_confidence", "ocr_confidence", "header_path", "rowspan", "colspan", "parse_pass", "review_status", "conflicts"):
                if item.get(field) not in (None, [], ""):
                    source[field] = item[field]
            if source_report_id:
                source["source_report_id"] = source_report_id
            if source_report_name:
                source["source_report_name"] = source_report_name
            if source_report_year is not None:
                source["source_report_year"] = source_report_year
            if link_target_page:
                source["link_source_page"] = link_source_page
                source["target_page"] = link_target_page
                anchor_text = self._format_short_metadata_value(
                    item.get("link_anchor_text"),
                    320,
                )
                source_segment_id = str(item.get("link_source_segment_id") or "").strip()
                source_context = self._format_short_metadata_value(
                    item.get("link_source_context"),
                    1200,
                )
                if anchor_text:
                    source["anchor_text"] = anchor_text
                if source_segment_id:
                    source["link_source_segment_id"] = source_segment_id
                if source_context:
                    source["source_context"] = source_context
            sources.append(source)
            if len(sources) >= 8:
                break
        if not sources and preferred_page is not None:
            sources.append({"source_type": "report_page", "data_page": preferred_page})
        return sources

    def _truncate_segment_text(self, text: str, max_chars: int = 1200) -> str:
        value = str(text or "").strip()
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 3].rstrip() + "..."

    def _get_report_segment_cache(self, report_content: ReportContent) -> Dict[str, Any]:
        segments = report_content.document_content.segments
        signature = document_content_revision(report_content)
        cached = getattr(report_content, "_disclosure_segment_cache", None)
        if isinstance(cached, dict) and cached.get("signature") == signature:
            return cached

        ordered = sorted(
            segments,
            key=lambda seg: (
                getattr(seg, "page_number", 0) or 0,
                getattr(seg, "position_y", 0.0) or 0.0,
                getattr(seg, "segment_id", ""),
            ),
        )
        by_id = {
            str(getattr(segment, "segment_id", "")): segment
            for segment in segments
            if getattr(segment, "segment_id", None)
        }
        ordered_index = {
            str(getattr(segment, "segment_id", "")): index
            for index, segment in enumerate(ordered)
            if getattr(segment, "segment_id", None)
        }
        table_rows: Dict[Tuple[str, int], List[Any]] = {}
        for segment in segments:
            row_key = self._get_table_row_scope_key(segment)
            if row_key != (None, None):
                table_rows.setdefault(row_key, []).append(segment)

        cached = {
            "signature": signature,
            "ordered": ordered,
            "ordered_index": ordered_index,
            "by_id": by_id,
            "table_rows": table_rows,
        }
        try:
            object.__setattr__(report_content, "_disclosure_segment_cache", cached)
        except Exception:
            pass
        return cached

    def _get_ordered_segments(self, report_content: ReportContent):
        return self._get_report_segment_cache(report_content)["ordered"]

    def _is_adjacent_context_segment(self, target_segment, candidate_segment) -> bool:
        if candidate_segment is None or target_segment is None:
            return False
        tp = getattr(target_segment, "page_number", None)
        cp = getattr(candidate_segment, "page_number", None)
        if tp is None or cp is None:
            return False
        if abs(int(cp) - int(tp)) > 1:
            return False
        tc = str(getattr(target_segment, "content", "") or "").strip()
        cc = str(getattr(candidate_segment, "content", "") or "").strip()
        if not cc or cc == tc:
            return False
        return True

    def _segment_structured_data_dict(self, segment) -> Dict[str, Any]:
        """Return structured_data as a dict when available."""
        if segment is None:
            return {}
        structured_data = getattr(segment, "structured_data", None)
        if isinstance(structured_data, dict):
            return structured_data
        if isinstance(structured_data, str) and structured_data.strip():
            try:
                parsed = json.loads(structured_data)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _segment_table_semantic_quality_reasons(self, segment) -> set[str]:
        raw_reasons = self._segment_structured_data_dict(segment).get(
            "quality_reasons"
        ) or []
        if isinstance(raw_reasons, str):
            raw_reasons = [raw_reasons]
        return {
            str(reason).strip().lower()
            for reason in raw_reasons
            if str(reason).strip().lower() in _TABLE_SEMANTIC_VALUE_BLOCKERS
        }

    def _segment_id_table_parts(self, segment) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """Best-effort parser for segment IDs like P003_T001_R006_C002."""
        segment_id = str(getattr(segment, "segment_id", "") or "")
        m = re.search(r"(P\d+_T\d+)_R(\d+)_C(\d+)", segment_id)
        if not m:
            return None, None, None
        try:
            return m.group(1), int(m.group(2)), int(m.group(3))
        except Exception:
            return m.group(1), None, None

    def _get_segment_field(self, segment, *field_names: str) -> Any:
        """Read a field from segment attrs first, then structured_data, ignoring blank/null strings."""
        for name in field_names:
            value = getattr(segment, name, None) if segment is not None else None
            if value is not None and str(value).strip().lower() not in {"", "none", "null", "nan"}:
                return value
        data = self._segment_structured_data_dict(segment)
        for name in field_names:
            value = data.get(name)
            if value is not None and str(value).strip().lower() not in {"", "none", "null", "nan"}:
                return value
        return None

    def _get_table_row_key(self, segment) -> Tuple[Optional[str], Optional[int]]:
        """Return the public table ID and row index, with segment ID fallback."""
        table_id = self._get_segment_field(
            segment,
            "source_table_id", "table_id", "source_table", "table_uid", "table_index", "source_table_index",
        )
        row_index = self._get_segment_field(segment, "row_index", "row_idx", "row_number", "source_row_index")
        parsed_table_id, parsed_row, _ = self._segment_id_table_parts(segment)
        if table_id is None:
            table_id = parsed_table_id
        if row_index is None:
            row_index = parsed_row
        try:
            row_index = int(row_index) if row_index is not None else None
        except Exception:
            m = re.search(r"\d+", str(row_index))
            row_index = int(m.group(0)) if m else None
        if table_id is None or row_index is None:
            return None, None
        return str(table_id), row_index

    def _get_table_row_scope_key(self, segment) -> Tuple[Optional[str], Optional[int]]:
        """Return an internal report/page-scoped row key for joins and caches."""
        table_id, row_index = self._get_table_row_key(segment)
        if table_id is None or row_index is None:
            return None, None
        return table_row_scope_key(
            segment,
            table_id=table_id,
            row_index=row_index,
        ) or (None, None)

    def _get_table_column_index(self, segment) -> Optional[int]:
        col_index = self._get_segment_field(segment, "column_index", "col_index", "column_idx", "col_number", "source_column_index")
        if col_index is None:
            _, _, parsed_col = self._segment_id_table_parts(segment)
            col_index = parsed_col
        try:
            return int(col_index) if col_index is not None else None
        except Exception:
            m = re.search(r"\d+", str(col_index))
            return int(m.group(0)) if m else None

    def _extract_years_from_text(self, text: object) -> List[int]:
        raw = str(text or "")
        years: List[int] = []

        def append_year(raw_year: object) -> None:
            try:
                y = int(raw_year)
                if 1990 <= y <= 2100 and y not in years:
                    years.append(y)
            except Exception:
                pass

        for m in re.finditer(r"(?i)\b(?:(?:FY|CY)\s*|FISCAL\s+YEAR\s*)?(20\d{2}|19\d{2})\b", raw):
            append_year(m.group(1))
        for m in re.finditer(r"(?i)\b(?:FY|CY)\s*['\u2019]?(\d{2})\b", raw):
            append_year(2000 + int(m.group(1)))
        return sorted(years)

    def _strip_metric_codes(self, text: object, code_candidates: Optional[List[str]] = None) -> str:
        cleaned = str(text or "")
        for code in code_candidates or []:
            for pattern in compile_metric_code_patterns(code):
                cleaned = pattern.sub(" ", cleaned)
        return cleaned

    def _numeric_mentions_from_cell_text(
        self,
        text: object,
        code_candidates: Optional[List[str]] = None,
        reject_values_from: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        rejected_sources = {
            str(value).strip().lower()
            for value in (reject_values_from or _DEFAULT_REJECT_VALUE_SOURCES)
        }
        raw = str(text or "")
        if "metric_code" in rejected_sources:
            raw = self._strip_metric_codes(raw, code_candidates)
        raw = raw.strip()
        if not raw:
            return []
        raw = raw.replace("\\n", "\n").replace("\\%", "%")
        raw = re.sub(r"\$\s*\^\{.*?\}\s*\$", " ", raw)
        mentions: List[Dict[str, Any]] = []
        seen = set()
        for line in re.split(r"[\n|]+", raw):
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            for match in re.finditer(r"-?\d[\d,]*(?:\.\d+)?\s*%?", line):
                token = match.group(0).strip()
                cleaned = token.replace(",", "").rstrip("%").strip()
                try:
                    numeric = float(cleaned)
                except Exception:
                    continue
                before = line[max(0, match.start() - 24):match.start()].lower()
                if (
                    "page_number" in rejected_sources
                    and re.search(r"(?:page|p\.)\s*[:#-]?\s*$", before)
                ):
                    continue
                if (
                    "row_or_column_number" in rejected_sources
                    and re.search(r"(?:row|column|col)\s*[:#-]?\s*$", before)
                ):
                    continue
                if (
                    "reference_index" in rejected_sources
                    and re.search(r"(?:index|indices|reference)\s*[:#-]?\s*$", before)
                ):
                    continue
                if (
                    "standalone_year" in rejected_sources
                    and re.search(r"(?:fy|cy|fiscal\s+year)\s*$", before)
                    and 0 <= numeric <= 2100
                ):
                    continue
                if (
                    "standalone_year" in rejected_sources
                    and re.fullmatch(r"\d{4}", cleaned)
                    and 1900 <= numeric <= 2100
                ):
                    continue
                value: Union[int, float] = int(numeric) if numeric == int(numeric) and abs(numeric) < 1e15 else numeric
                unit = "%" if token.endswith("%") else _extract_unit_hint(line)
                key = (value, _normalize_unit_text(unit).lower(), line.lower())
                if key in seen:
                    continue
                seen.add(key)
                mentions.append({"value": value, "unit": unit or None, "description": line})
        return mentions

    def _is_navigation_or_code_cell(
        self,
        segment,
        code_candidates: Optional[List[str]] = None,
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> bool:
        if str(getattr(segment, "segment_type", "") or "").lower() != "table_cell":
            return True
        rejected_sources = self._profile_reject_value_sources(metric_profile)
        header = str(self._get_segment_field(segment, "col_header", "column_header") or "").strip().lower()
        if "reference_index" in rejected_sources and re.search(r"\b(reference|indices?)\b", header):
            return True
        if "metric_code" in rejected_sources and re.search(r"\b(code|sasb|gri)\b", header):
            return True
        if "page_number" in rejected_sources and re.search(r"\b(page|location|link)\b", header):
            return True
        value_text = str(self._get_segment_field(segment, "value_text", "cell_value", "value") or "").strip()
        if (
            "metric_code" in rejected_sources
            and value_text
            and self._contains_metric_code(value_text, code_candidates or [])
        ):
            without_codes = self._strip_metric_codes(value_text, code_candidates)
            if not re.search(r"-?\d[\d,]*(?:\.\d+)?\s*%?", without_codes):
                return True
        return False

    def _real_data_candidates_for_row(
        self,
        report_content: ReportContent,
        target_segment,
        code_candidates: Optional[List[str]] = None,
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[Dict[str, Any]]:
        row_key = self._get_table_row_scope_key(target_segment)
        if row_key == (None, None):
            return []
        candidates: List[Dict[str, Any]] = []
        row_segments = self._get_report_segment_cache(report_content)["table_rows"].get(
            row_key,
            [],
        )
        for segment in row_segments:
            if self._is_navigation_or_code_cell(
                segment, code_candidates, metric_profile
            ):
                continue

            preferred_value = self._get_segment_field(
                segment,
                "value_text", "cell_value", "raw_value", "numeric_value",
                "amount", "figure", "data", "extracted_value",
            )
            source_text = preferred_value if preferred_value is not None else getattr(segment, "content", "")
            mentions = self._numeric_mentions_from_cell_text(
                source_text,
                code_candidates,
                self._profile_reject_value_sources(metric_profile),
            )
            col_header = self._format_short_metadata_value(
                self._get_segment_field(segment, "col_header", "column_header"),
                120,
            )
            row_header = self._format_short_metadata_value(
                self._get_segment_field(segment, "row_header"),
                220,
            )
            blocking_reasons = self._segment_table_semantic_quality_reasons(
                segment
            )
            year_blocked = bool(
                blocking_reasons & _TABLE_SEMANTIC_YEAR_BLOCKERS
            )
            unit_blocked = bool(
                blocking_reasons & _TABLE_SEMANTIC_UNIT_BLOCKERS
            )
            explicit_year = (
                None
                if year_blocked
                else self._get_segment_field(segment, "year")
            )
            try:
                parsed_explicit_year = (
                    int(explicit_year) if explicit_year is not None else None
                )
            except (TypeError, ValueError):
                parsed_explicit_year = None
            if year_blocked:
                local_years = []
            elif (
                parsed_explicit_year is not None
                and 1900 <= parsed_explicit_year <= 2100
            ):
                local_years = [parsed_explicit_year]
            else:
                local_years = self._extract_years_from_text(
                    " ".join(
                        part
                        for part in [col_header, str(preferred_value or "")]
                        if part
                    )
                )
                if not local_years:
                    content_years = self._extract_years_from_text(
                        getattr(segment, "content", "")
                    )
                    if len(content_years) == 1:
                        local_years = content_years
            candidate_year = local_years[0] if len(local_years) == 1 else None
            source_year_label = self._format_short_metadata_value(
                self._get_segment_field(segment, "source_year_label"),
                80,
            )
            explicit_unit = None
            if not unit_blocked:
                explicit_unit = self._format_short_metadata_value(
                    self._get_segment_field(
                        segment,
                        "unit",
                        "cell_unit",
                        "raw_unit",
                    ),
                    80,
                ) or None
            dimension_labels: Dict[str, str] = {}
            if metric_profile is not None and metric_profile.variable_dimensions and row_header:
                dimension_labels[metric_profile.variable_dimensions[0]] = row_header
            for mention in mentions:
                candidates.append(
                    {
                        "segment": segment,
                        "value": mention["value"],
                        "unit": (
                            None
                            if unit_blocked
                            else (mention.get("unit") or explicit_unit)
                        ),
                        "description": mention.get("description") or str(source_text),
                        "year": candidate_year,
                        "page": getattr(segment, "page_number", None),
                        "segment_id": getattr(segment, "segment_id", None),
                        "label": row_header or col_header or None,
                        "row_label": row_header or None,
                        "column_label": col_header or None,
                        "source_year_label": (
                            source_year_label or col_header
                            if candidate_year is not None
                            else None
                        ),
                        "dimensions": dimension_labels,
                        "blocking_semantic_quality_reasons": sorted(
                            blocking_reasons
                        ),
                    }
                )
        return candidates

    def _metric_candidate_rank(
        self,
        candidate: Dict[str, Any],
        metric: Optional['ESGMetric'],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Tuple[float, int, bool, bool]:
        metric_name = str(getattr(metric, "metric_name", "") or "") if metric is not None else ""
        metric_tokens = {
            token for token in re.findall(r"[a-z][a-z0-9-]{2,}", metric_name.lower())
            if token not in {"the", "and", "for", "with", "from", "number", "total", "percentage"}
        }
        segment = candidate.get("segment")
        description = " ".join(
            str(part or "")
            for part in [
                candidate.get("description"),
                candidate.get("label"),
                self._get_segment_field(segment, "row_header") if segment is not None else None,
            ]
        ).lower()
        description_tokens = set(re.findall(r"[a-z][a-z0-9-]{2,}", description))
        overlap = len(metric_tokens & description_tokens)
        score = float(overlap)
        expected_units = [
            str(getattr(metric, "unit", "") or "") if metric is not None else "",
            *(metric_profile.expected_units if metric_profile is not None else []),
        ]
        expected_profiles = [
            unit_profile
            for unit_profile in (_unit_profile(unit) for unit in expected_units)
            if unit_profile is not None
        ]
        candidate_profile = _unit_profile(candidate.get("unit"))
        unit_match = False
        if expected_profiles and candidate_profile:
            unit_match = any(
                expected_profile[0] == candidate_profile[0]
                for expected_profile in expected_profiles
            )
            score += 8.0 if unit_match else -8.0
        elif (
            any(profile[0] == "ratio_percent" for profile in expected_profiles)
            and "%" in description
        ):
            unit_match = True
            score += 8.0
        component_match = self._profile_component_matches(description, metric_profile)
        if metric_profile is not None and metric_profile.value_selection_rules.get(
            "match_current_component_before_sibling_values", False
        ):
            score += 24.0 if component_match else -24.0
        return score, overlap, unit_match, component_match

    def _select_metric_candidate_from_candidates(
        self,
        candidates: List[Dict[str, Any]],
        metric: Optional['ESGMetric'],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        metric_profile = metric_profile or self._resolve_metric_profile(metric)
        candidates = [
            candidate
            for candidate in candidates
            if self._candidate_satisfies_profile(candidate, metric_profile)
        ]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        ranked: List[Tuple[float, int, bool, bool, Dict[str, Any]]] = []
        for candidate in candidates:
            score, overlap, unit_match, component_match = self._metric_candidate_rank(
                candidate, metric, metric_profile
            )
            ranked.append((score, overlap, unit_match, component_match, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked[0][0] <= 0:
            return None
        if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1e-9:
            return None
        selected = dict(ranked[0][4])
        selected["selection_score"] = ranked[0][0]
        selected["selection_margin"] = ranked[0][0] - ranked[1][0] if len(ranked) > 1 else ranked[0][0]
        selected["name_overlap"] = ranked[0][1]
        selected["unit_match"] = ranked[0][2]
        selected["component_match"] = ranked[0][3]
        return selected

    def _select_metric_numeric_candidate(
        self,
        report_content: ReportContent,
        target_segment,
        metric: Optional['ESGMetric'],
        code_candidates: Optional[List[str]] = None,
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Optional[Dict[str, Any]]:
        metric_profile = metric_profile or self._resolve_metric_profile(metric)
        candidates = self._real_data_candidates_for_row(
            report_content,
            target_segment,
            code_candidates,
            metric_profile,
        )
        if not candidates:
            return None
        dated = [candidate for candidate in candidates if candidate.get("year") is not None]
        if dated:
            latest_year = max(int(candidate["year"]) for candidate in dated)
            candidates = [candidate for candidate in dated if int(candidate["year"]) == latest_year]
        return self._select_metric_candidate_from_candidates(
            candidates, metric, metric_profile
        )

    def _metric_year_values_from_candidates(
        self,
        candidates: List[Dict[str, Any]],
        metric: Optional['ESGMetric'],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[Dict[str, Any]]:
        metric_profile = metric_profile or self._resolve_metric_profile(metric)
        expected_unit = str(getattr(metric, "unit", "") or "") if metric is not None else ""
        by_year: Dict[int, List[Dict[str, Any]]] = {}
        for candidate in candidates:
            try:
                year = int(candidate.get("year"))
            except (TypeError, ValueError):
                continue
            if 1900 <= year <= 2100:
                by_year.setdefault(year, []).append(candidate)

        year_values: List[Dict[str, Any]] = []
        for year in sorted(by_year):
            preserve_dimensions = bool(
                metric_profile is not None
                and metric_profile.variable_dimensions
                and metric_profile.value_selection_rules.get(
                    "preserve_variable_dimension_labels", False
                )
            )
            if preserve_dimensions:
                selected_candidates = [
                    candidate
                    for candidate in by_year[year]
                    if self._candidate_satisfies_profile(candidate, metric_profile)
                ]
            else:
                selected = self._select_metric_candidate_from_candidates(
                    by_year[year], metric, metric_profile
                )
                selected_candidates = [selected] if selected is not None else []

            for selected in selected_candidates:
                raw_value = selected.get("value")
                raw_unit = selected.get("unit")
                converted = _convert_numeric_value_between_units(
                    raw_value, raw_unit, expected_unit
                )
                value = converted if converted is not None else raw_value
                unit = (
                    expected_unit
                    if converted is not None and expected_unit
                    else (raw_unit or expected_unit or "")
                )
                year_values.append(
                    {
                        "year": year,
                        "source_year_label": selected.get("source_year_label"),
                        "value": _finalize_compliance_value_field(value),
                        "raw_value": raw_value,
                        "raw_unit": raw_unit,
                        "unit": unit,
                        "page": selected.get("page"),
                        "context": str(selected.get("description") or "").strip() or None,
                        "evidence_segment_id": selected.get("segment_id"),
                        "label": selected.get("label"),
                        "dimensions": dict(selected.get("dimensions") or {}),
                        "source": "structured_table",
                    }
                )
        return year_values

    def _merge_metric_year_values(self, *collections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        index_by_key: Dict[tuple, int] = {}

        def merge_sources(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
            sources = [
                dict(item)
                for item in (target.get("sources") or [])
                if isinstance(item, dict)
            ]
            seen_sources = {
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in sources
            }
            for source in incoming.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                source_key = json.dumps(source, ensure_ascii=False, sort_keys=True)
                if source_key in seen_sources:
                    continue
                sources.append(dict(source))
                seen_sources.add(source_key)
            if sources:
                target["sources"] = sources

        for collection in collections:
            for raw in collection or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    year = int(raw.get("year"))
                except (TypeError, ValueError):
                    continue
                value = _parse_llm_numeric_value_only(raw.get("value"))
                if not 1900 <= year <= 2100 or value is None:
                    continue
                item = dict(raw)
                item["year"] = year
                item["value"] = value
                raw_unit = item.get("unit") or item.get("raw_unit")
                unit_key = _normalize_unit_atom(
                    _extract_unit_hint(raw_unit) or raw_unit
                ).lower()
                value_key = float(value)
                dimensions_key = json.dumps(
                    item.get("dimensions") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                key = (year, value_key, unit_key, dimensions_key)
                if key in index_by_key:
                    merge_sources(merged[index_by_key[key]], item)
                    continue
                index_by_key[key] = len(merged)
                merged.append(item)
        return sorted(merged, key=lambda item: (int(item["year"]), str(item.get("label") or "")))

    @staticmethod
    def _year_value_source_from_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
        source: Dict[str, Any] = {
            "source_type": (
                "linked_page" if meta.get("link_target_page") is not None else "report_page"
            ),
            "data_page": meta.get("page_number"),
            "segment_id": meta.get("segment_id"),
        }
        for key in ("source_report_id", "source_report_name", "source_report_year"):
            if meta.get(key) is not None:
                source[key] = meta.get(key)
        if meta.get("link_target_page") is not None:
            source["link_source_page"] = meta.get("link_source_page")
            source["target_page"] = meta.get("link_target_page")
        return source

    def _attach_year_value_sources(
        self,
        year_values: List[Dict[str, Any]],
        segment_metadata: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_segment = {
            str(item.get("segment_id")): item
            for item in segment_metadata or []
            if item.get("segment_id")
        }
        by_page: Dict[int, List[Dict[str, Any]]] = {}
        for item in segment_metadata or []:
            try:
                page = int(item.get("page_number"))
            except (TypeError, ValueError):
                continue
            by_page.setdefault(page, []).append(item)

        enriched: List[Dict[str, Any]] = []
        for raw in year_values or []:
            item = dict(raw)
            existing_sources = [
                dict(source)
                for source in (item.get("sources") or [])
                if isinstance(source, dict)
            ]
            segment_id = str(item.get("evidence_segment_id") or "")
            meta = by_segment.get(segment_id)
            if meta is None and item.get("page") is not None:
                try:
                    page_matches = by_page.get(int(item.get("page")), [])
                except (TypeError, ValueError):
                    page_matches = []
                # Page numbers are only safe as fallback when they identify one
                # source report in the company corpus.
                report_ids = {
                    str(match.get("source_report_id") or "") for match in page_matches
                }
                if len(report_ids) <= 1 and page_matches:
                    meta = page_matches[0]
            if meta is not None:
                source = self._year_value_source_from_metadata(meta)
                source_key = json.dumps(source, ensure_ascii=False, sort_keys=True)
                existing_keys = {
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    for value in existing_sources
                }
                if source_key not in existing_keys:
                    existing_sources.append(source)
            if existing_sources:
                item["sources"] = existing_sources
            enriched.append(item)
        return enriched

    @staticmethod
    def _year_has_conflicting_values(
        year_values: List[Dict[str, Any]],
        year: Optional[int],
    ) -> bool:
        if year is None:
            return False
        distinct = {
            (
                float(value),
                _normalize_unit_atom(
                    _extract_unit_hint(item.get("unit") or item.get("raw_unit"))
                    or item.get("unit")
                    or item.get("raw_unit")
                ).lower(),
            )
            for item in year_values or []
            if int(item.get("year") or 0) == int(year)
            for value in [_parse_llm_numeric_value_only(item.get("value"))]
            if value is not None
        }
        return len(distinct) > 1

    def _target_year_for_metric(self, metric: Optional['ESGMetric']) -> Optional[int]:
        candidates = []
        if metric is not None:
            candidates.extend(
                getattr(metric, name, None)
                for name in ("target_year", "reporting_year", "year")
            )
        candidates.append(getattr(getattr(self, "config", None), "target_year", None))
        candidates.append(os.getenv("REPORT_TARGET_YEAR"))
        for raw in candidates:
            try:
                year = int(raw)
            except (TypeError, ValueError):
                continue
            if 1900 <= year <= 2100:
                return year
        return None

    def _select_metric_year_value(
        self,
        year_values: List[Dict[str, Any]],
        target_year: Optional[int] = None,
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        if not year_values:
            return target_year, None
        selected_year = target_year or max(int(item["year"]) for item in year_values)
        matches = [item for item in year_values if int(item["year"]) == selected_year]
        if (
            metric_profile is not None
            and metric_profile.variable_dimensions
            and metric_profile.output_shape in {"breakdown", "table"}
        ):
            return selected_year, None
        distinct = {
            (
                float(item["value"]),
                _normalize_unit_text(item.get("unit") or item.get("raw_unit")).lower(),
            )
            for item in matches
            if _parse_llm_numeric_value_only(item.get("value")) is not None
        }
        if len(distinct) != 1 or not matches:
            return selected_year, None
        return selected_year, matches[0]

    def _select_metric_candidate_from_exact_code_evidence(
        self,
        report_content: ReportContent,
        segment_metadata: List[Dict[str, Any]],
        metric: Optional['ESGMetric'],
        code_candidates: List[str],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> Optional[Dict[str, Any]]:
        """Select an unambiguous submetric value across all exact-code table rows."""
        metric_profile = metric_profile or self._resolve_metric_profile(metric)
        best: Optional[Dict[str, Any]] = None
        seen_rows = set()
        for meta in segment_metadata:
            segment_id = meta.get("segment_id")
            if not segment_id:
                continue
            try:
                segment = self._get_segment_by_id(report_content, segment_id)
            except Exception:
                segment = None
            if segment is None:
                continue
            segment_data = getattr(segment, "structured_data", None)
            segment_data = segment_data if isinstance(segment_data, dict) else {}
            if (
                str(getattr(segment, "review_status", None) or segment_data.get("review_status") or "").lower()
                == "needs_review"
            ):
                # Conflicted table structure may be cited, but it must never take
                # the deterministic fully-disclosed shortcut as a confirmed value.
                continue

            row_key = self._get_table_row_scope_key(segment)
            if row_key == (None, None) or row_key in seen_rows:
                continue
            seen_rows.add(row_key)
            row_context, _, _, _ = self._build_table_row_aggregation_context(
                report_content,
                segment,
                max_chars=1800,
            )
            evidence_text = "\n".join(
                part
                for part in [
                    row_context,
                    str(getattr(segment, "content", "") or ""),
                    str(meta.get("link_source_context") or ""),
                ]
                if part
            )
            if not self._contains_metric_code(evidence_text, code_candidates):
                continue

            candidate = self._select_metric_numeric_candidate(
                report_content,
                segment,
                metric,
                code_candidates,
                metric_profile,
            )
            if candidate is None:
                continue
            candidate["retrieval_score"] = float(meta.get("score", 0) or 0)
            rank = (
                float(candidate.get("selection_score", 0) or 0),
                float(candidate.get("selection_margin", 0) or 0),
                candidate["retrieval_score"],
            )
            if best is None or rank > best["_rank"]:
                candidate["_rank"] = rank
                best = candidate
        if best is not None:
            best.pop("_rank", None)
        return best

    def _collect_metric_year_values_from_exact_code_evidence(
        self,
        report_content: ReportContent,
        segment_metadata: List[Dict[str, Any]],
        metric: Optional['ESGMetric'],
        code_candidates: List[str],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[Dict[str, Any]]:
        """Collect all unambiguous annual values from exact-code table rows."""
        metric_profile = metric_profile or self._resolve_metric_profile(metric)
        year_values: List[Dict[str, Any]] = []
        seen_rows = set()
        for meta in segment_metadata:
            segment_id = meta.get("segment_id")
            if not segment_id:
                continue
            segment = self._get_segment_by_id(report_content, segment_id)
            if segment is None:
                continue
            row_key = self._get_table_row_scope_key(segment)
            if row_key == (None, None) or row_key in seen_rows:
                continue
            seen_rows.add(row_key)
            row_context, _, _, _ = self._build_table_row_aggregation_context(
                report_content,
                segment,
                max_chars=2200,
            )
            evidence_text = "\n".join(
                part for part in [row_context, str(getattr(segment, "content", "") or "")] if part
            )
            if not self._contains_metric_code(evidence_text, code_candidates):
                continue
            candidates = self._real_data_candidates_for_row(
                report_content,
                segment,
                code_candidates,
                metric_profile,
            )
            year_values.extend(
                self._metric_year_values_from_candidates(
                    candidates, metric, metric_profile
                )
            )
        return self._merge_metric_year_values(year_values)

    def _normalise_llm_year_values(
        self,
        llm_result: Dict[str, Any],
        metric: Optional['ESGMetric'],
        segment_metadata: List[Dict[str, Any]],
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[Dict[str, Any]]:
        metric_profile = metric_profile or self._resolve_metric_profile(metric)
        if (
            metric_profile is not None
            and not metric_profile.year_rules.get("extract_all_reported_years", True)
        ):
            return []
        raw_values = llm_result.get("year_values")
        if not isinstance(raw_values, list):
            return []
        expected_unit = str(getattr(metric, "unit", "") or "") if metric is not None else ""
        valid_pages = {
            int(meta["page_number"])
            for meta in segment_metadata
            if meta.get("page_number") is not None
        }
        valid_segment_ids = {
            str(meta["segment_id"])
            for meta in segment_metadata
            if meta.get("segment_id")
        }
        year_values: List[Dict[str, Any]] = []
        for raw in raw_values:
            if not isinstance(raw, dict):
                continue
            try:
                year = int(raw.get("year"))
            except (TypeError, ValueError):
                continue
            if not 1900 <= year <= 2100:
                continue
            raw_value = _parse_llm_numeric_value_only(raw.get("raw_value"))
            value = _parse_llm_numeric_value_only(raw.get("value"))
            if raw_value is None:
                raw_value = value
            if raw_value is None:
                continue
            raw_unit = str(raw.get("unit") or raw.get("raw_unit") or "").strip() or None
            converted = _convert_numeric_value_between_units(raw_value, raw_unit, expected_unit)
            normalized_value = converted if converted is not None else (value if value is not None else raw_value)
            normalized_unit = expected_unit if converted is not None and expected_unit else (raw_unit or expected_unit or "")

            page = None
            try:
                candidate_page = int(raw.get("page")) if raw.get("page") is not None else None
                if candidate_page is not None and (not valid_pages or candidate_page in valid_pages):
                    page = candidate_page
            except (TypeError, ValueError):
                page = None
            segment_id = str(raw.get("evidence_segment_id") or "").strip() or None
            if segment_id is not None and valid_segment_ids and segment_id not in valid_segment_ids:
                segment_id = None
            year_values.append(
                {
                    "year": year,
                    "value": normalized_value,
                    "raw_value": raw_value,
                    "raw_unit": raw_unit,
                    "unit": normalized_unit,
                    "page": page,
                    "context": str(raw.get("evidence_quote") or "").strip() or None,
                    "evidence_segment_id": segment_id,
                    "source_year_label": str(raw.get("source_year_label") or "").strip() or None,
                    "label": str(raw.get("label") or "").strip() or None,
                    "dimensions": dict(raw.get("dimensions") or {})
                    if isinstance(raw.get("dimensions"), dict)
                    else {},
                    "source": "llm_evidence",
                }
            )
        return self._merge_metric_year_values(year_values)

    def _extract_numeric_from_cell_text(self, text: object) -> Optional[Union[int, float]]:
        mentions = self._numeric_mentions_from_cell_text(text)
        return mentions[0]["value"] if len(mentions) == 1 else None

    def _build_table_row_aggregation_context(
        self,
        report_content: ReportContent,
        target_segment,
        max_chars: int = 1400,
    ) -> Tuple[str, Optional[Union[int, float]], Optional[str], Optional[str]]:
        """Aggregate all cells from the same table row.

        The row key is source report + table + page + row index, with a
        segment-ID fallback for legacy segments.
        The returned context is for the LLM/UI only; it does not directly classify status.
        The numeric candidate is the latest-year cell in that row when a year can be identified.
        """
        row_key = self._get_table_row_scope_key(target_segment)
        if row_key == (None, None):
            return "", None, None, None
        table_id, row_index = self._get_table_row_key(target_segment)

        row_segments = list(
            self._get_report_segment_cache(report_content)["table_rows"].get(
                row_key,
                [],
            )
        )

        if len(row_segments) <= 1:
            return "", None, None, None

        def sort_key(seg):
            col = self._get_table_column_index(seg)
            return (
                col if col is not None else 10_000,
                getattr(seg, "position_x", 0.0) or 0.0,
                str(getattr(seg, "segment_id", "") or ""),
            )

        row_segments = sorted(row_segments, key=sort_key)

        row_header_values = []
        for seg in row_segments:
            rh = self._format_short_metadata_value(self._get_segment_field(seg, "row_header"), 220)
            if rh and rh not in row_header_values:
                row_header_values.append(rh)
        row_header = row_header_values[0] if row_header_values else ""

        cells: List[str] = []
        latest_year: Optional[int] = None
        latest_numeric: Optional[Union[int, float]] = None
        latest_unit: Optional[str] = None
        latest_desc: Optional[str] = None
        annual_descriptions: Dict[int, str] = {}

        for seg in row_segments:
            col_index = self._get_table_column_index(seg)
            col_header = self._format_short_metadata_value(self._get_segment_field(seg, "col_header", "column_header"), 120)
            value_text = self._format_short_metadata_value(self._get_segment_field(seg, "value_text", "cell_value", "value"), 180)
            unit = self._format_short_metadata_value(self._get_segment_field(seg, "unit", "cell_unit", "raw_unit"), 80)
            content = self._format_short_metadata_value(getattr(seg, "content", "") or "", 260)

            display_value = value_text or content
            if unit and display_value and unit.lower() not in display_value.lower():
                display_value = f"{display_value} {unit}"
            label = col_header or (f"C{col_index}" if col_index is not None else "Cell")
            cell_line = f"{label}: {display_value}" if display_value else label
            if cell_line and cell_line not in cells:
                cells.append(cell_line)

            year_candidates = self._extract_years_from_text(" ".join([col_header, value_text]))
            if not year_candidates:
                content_years = self._extract_years_from_text(content)
                if len(content_years) == 1:
                    year_candidates = content_years
            cell_year = max(year_candidates) if year_candidates else None
            numeric_candidate = self._extract_numeric_from_cell_text(value_text or content)
            if self._segment_table_semantic_quality_reasons(seg):
                # Keep the cell in row context for the LLM/reviewer, but never
                # turn an explicitly ambiguous/conflicting semantic cell back
                # into a deterministic scalar shortcut.
                numeric_candidate = None
            if cell_year is not None and numeric_candidate is not None:
                annual_descriptions.setdefault(cell_year, cell_line)
                if latest_year is None or cell_year > latest_year:
                    latest_year = cell_year
                    latest_numeric = numeric_candidate
                    latest_unit = unit or self._format_short_metadata_value(self._get_segment_field(seg, "raw_unit"), 80) or None
                    latest_desc = cell_line

        if not cells:
            return "", None, None, None

        parts = ["[Full Table Row Context]"]
        parts.append(f"- Row Key: {table_id} / row {row_index}")
        if row_header:
            parts.append(f"- Row Header: {row_header}")
        parts.append("- Row Cells: " + " | ".join(cells))
        if annual_descriptions:
            parts.append(
                "- Annual Value Candidates: "
                + " | ".join(
                    f"FY{year}: {annual_descriptions[year]}"
                    for year in sorted(annual_descriptions)
                )
            )
        if latest_year is not None and latest_desc:
            parts.append(f"- Latest Year Candidate: FY{latest_year}: {latest_desc}")
        row_context = "\n".join(parts)
        if len(row_context) > max_chars:
            row_context = row_context[: max_chars - 3].rstrip() + "..."
        return row_context, latest_numeric, latest_unit, latest_desc

    def _format_short_metadata_value(self, value: object, max_chars: int = 260) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        if len(text) > max_chars:
            return text[: max_chars - 3].rstrip() + "..."
        return text

    def _build_structured_segment_hint(self, segment) -> str:
        """Build a compact metadata hint for table/cell-derived evidence.

        This only improves the evidence text shown to the LLM. It does not
        classify disclosure status or post-process the LLM decision.
        """
        if segment is None:
            return ""

        hint_lines: List[str] = []
        segment_type = self._format_short_metadata_value(getattr(segment, "segment_type", None), 80)
        if segment_type:
            hint_lines.append(f"- Segment Type: {segment_type}")

        table_id, row_index = self._get_table_row_key(segment)
        col_index = self._get_table_column_index(segment)
        if table_id is not None:
            hint_lines.append(f"- Source Table ID: {table_id}")
        if row_index is not None:
            hint_lines.append(f"- Row Index: {row_index}")
        if col_index is not None:
            hint_lines.append(f"- Column Index: {col_index}")

        row_header = self._format_short_metadata_value(self._get_segment_field(segment, "row_header"))
        if row_header:
            hint_lines.append(f"- Row Header: {row_header}")

        col_header = self._format_short_metadata_value(self._get_segment_field(segment, "col_header", "column_header"))
        if col_header:
            hint_lines.append(f"- Column Header: {col_header}")

        value_text = self._format_short_metadata_value(self._get_segment_field(segment, "value_text", "cell_value", "value"))
        if value_text:
            hint_lines.append(f"- Cell Value: {value_text}")

        unit = self._format_short_metadata_value(self._get_segment_field(segment, "unit", "cell_unit", "raw_unit"), 120)
        if unit:
            hint_lines.append(f"- Cell Unit: {unit}")

        reporting_year = self._get_segment_field(segment, "year")
        source_year_label = self._format_short_metadata_value(
            self._get_segment_field(segment, "source_year_label"),
            80,
        )
        if reporting_year is not None:
            year_text = str(reporting_year)
            if source_year_label:
                year_text += f" ({source_year_label})"
            hint_lines.append(f"- Reporting Year: {year_text}")

        unit_multiplier = self._get_segment_field(segment, "unit_multiplier")
        unit_scope = self._format_short_metadata_value(
            self._get_segment_field(segment, "unit_scope"),
            80,
        )
        if unit_multiplier is not None:
            multiplier_text = str(unit_multiplier)
            if unit_scope:
                multiplier_text += f" ({unit_scope})"
            hint_lines.append(f"- Unit Multiplier: {multiplier_text}")

        header_path = getattr(segment, "header_path", None)
        if not header_path:
            header_path = self._segment_structured_data_dict(segment).get("header_path")
        if isinstance(header_path, (list, tuple)):
            rendered_header_path = " > ".join(
                str(item).strip() for item in header_path if str(item).strip()
            )
            if rendered_header_path:
                hint_lines.append(f"- Header Path: {rendered_header_path}")

        structured_data = getattr(segment, "structured_data", None)
        if structured_data:
            try:
                structured_text = json.dumps(structured_data, ensure_ascii=False, default=str)
            except Exception:
                structured_text = str(structured_data)
            structured_text = self._format_short_metadata_value(structured_text, 420)
            if structured_text:
                hint_lines.append(f"- Structured Data: {structured_text}")

        if not hint_lines:
            return ""
        return "[Structured Evidence Metadata]\n" + "\n".join(hint_lines)

    def _build_augmented_segment_context(self, report_content: ReportContent, segment_id: str, fallback_content: Optional[str] = None) -> Optional[str]:
        cache = self._get_report_segment_cache(report_content)
        target_segment = cache["by_id"].get(segment_id)
        if target_segment is None:
            return fallback_content
        ordered_segments = cache["ordered"]
        target_index = cache["ordered_index"].get(segment_id)
        if target_index is None:
            return getattr(target_segment, "content", None) or fallback_content
        parts: List[str] = []
        if target_index > 0:
            prev_seg = ordered_segments[target_index - 1]
            if self._is_adjacent_context_segment(target_segment, prev_seg):
                prev_text = self._truncate_segment_text(getattr(prev_seg, "content", ""))
                if prev_text:
                    parts.append(f"[Previous Context]\n{prev_text}")
        structured_hint = self._build_structured_segment_hint(target_segment)
        if structured_hint:
            parts.append(structured_hint)

        row_context, _, _, _ = self._build_table_row_aggregation_context(report_content, target_segment)
        if row_context:
            parts.append(row_context)

        hit_text = self._truncate_segment_text(getattr(target_segment, "content", "") or fallback_content or "")
        if hit_text:
            parts.append(f"[Hit Segment]\n{hit_text}")
        if target_index + 1 < len(ordered_segments):
            next_seg = ordered_segments[target_index + 1]
            if self._is_adjacent_context_segment(target_segment, next_seg):
                next_text = self._truncate_segment_text(getattr(next_seg, "content", ""))
                if next_text:
                    parts.append(f"[Next Context]\n{next_text}")
        return "\n\n".join(parts) if parts else hit_text or fallback_content

    def _resolve_link_source_segment_id(
        self,
        report_content: ReportContent,
        result: RetrievalResult,
    ) -> Optional[str]:
        explicit = str(getattr(result, "link_source_segment_id", "") or "").strip()
        if explicit and self._get_segment_by_id(report_content, explicit) is not None:
            return explicit

        source_page = getattr(result, "link_source_page", None)
        if source_page is None:
            return None
        anchor = re.sub(
            r"\s+",
            " ",
            re.sub(r"[^a-z0-9]+", " ", str(getattr(result, "link_anchor_text", "") or "").lower()),
        ).strip()
        target_page = getattr(result, "link_target_page", None)
        type_rank = {
            "table_cell": 6,
            "table_row": 5,
            "link_anchor": 4,
            "text": 3,
            "heading": 3,
            "table": 1,
        }
        candidates: List[Tuple[int, str]] = []
        for segment in report_content.document_content.segments or []:
            if getattr(segment, "page_number", None) != source_page:
                continue
            segment_id = str(getattr(segment, "segment_id", "") or "")
            if not segment_id:
                continue
            segment_type = str(getattr(segment, "segment_type", "") or "").lower()
            score = type_rank.get(segment_type, 2)
            value_text = re.sub(
                r"\s+",
                " ",
                re.sub(r"[^a-z0-9]+", " ", str(getattr(segment, "value_text", "") or "").lower()),
            ).strip()
            content = re.sub(
                r"\s+",
                " ",
                re.sub(r"[^a-z0-9]+", " ", str(getattr(segment, "content", "") or "").lower()),
            ).strip()
            if anchor and anchor in value_text:
                score += 5
            elif anchor and anchor in content:
                score += 3
            data = getattr(segment, "structured_data", None)
            if isinstance(data, dict):
                for link in data.get("pdf_links") or []:
                    if not isinstance(link, dict) or link.get("link_type") != "internal":
                        continue
                    try:
                        link_target = int(link.get("target_page"))
                    except Exception:
                        continue
                    if target_page is not None and link_target == int(target_page):
                        score += 6
                        break
            if score > type_rank.get(segment_type, 2) or not anchor:
                candidates.append((score, segment_id))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _build_linked_evidence_context(
        self,
        report_content: ReportContent,
        result: RetrievalResult,
        target_context: Optional[str],
        max_chars: int,
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """Pair the link's source-row context with the resolved target evidence."""
        source_segment_id = self._resolve_link_source_segment_id(report_content, result)
        source_context = None
        if source_segment_id and source_segment_id != getattr(result, "segment_id", None):
            source_segment = self._get_segment_by_id(report_content, source_segment_id)
            if source_segment is not None:
                source_parts: List[str] = []
                source_hit = self._truncate_segment_text(
                    getattr(source_segment, "content", "") or "",
                    1000,
                )
                if source_hit:
                    source_parts.append(f"[Link Source Hit]\n{source_hit}")
                row_context, _, _, _ = self._build_table_row_aggregation_context(
                    report_content,
                    source_segment,
                    max_chars=1800,
                )
                if row_context:
                    source_parts.append(row_context)
                augmented_source = self._build_augmented_segment_context(
                    report_content=report_content,
                    segment_id=source_segment_id,
                )
                if augmented_source:
                    source_parts.append(f"[Link Source Surrounding Context]\n{augmented_source}")
                source_context = "\n\n".join(source_parts)

        max_chars = max(600, int(max_chars or 2400))
        source_budget = min(1200, max(300, max_chars // 2))
        source_excerpt = (
            self._truncate_segment_text(source_context, source_budget)
            if source_context
            else None
        )
        target_budget = max(300, max_chars - (len(source_excerpt) if source_excerpt else 0) - 260)
        target_parts: List[str] = []
        target_hit = str(getattr(result, "content", "") or "").strip()
        if target_hit:
            target_parts.append(f"[Link Target Hit]\n{target_hit}")
        if target_context:
            target_parts.append(f"[Link Target Surrounding Context]\n{target_context}")
        target_excerpt = self._truncate_segment_text("\n\n".join(target_parts), target_budget)
        anchor_text = str(getattr(result, "link_anchor_text", "") or "").strip()
        source_page = getattr(result, "link_source_page", None)
        root_target_page = getattr(result, "link_target_page", None)

        parts = [
            "[Internal PDF Link Source Context]",
            f"- Source Page: {source_page}",
        ]
        if anchor_text:
            parts.append(f"- Anchor Text: {anchor_text}")
        if source_excerpt:
            parts.append(source_excerpt)
        parts.extend(
            [
                "[Internal PDF Link Target Context]",
                f"- Root Target Page: {root_target_page}",
                f"- Data Page: {getattr(result, 'page_number', None)}",
            ]
        )
        if target_excerpt:
            parts.append(target_excerpt)
        return "\n".join(parts), source_excerpt, source_segment_id

    def _build_analysis_prompt(
        self,
        metric_name: str,
        metric_id: str,
        segments: List[str],
        segment_metadata: List[Dict] = None,
        metric_unit: str = "",
        metric_description: str = "",
        metric_code: str = "",
        metric_topic: str = "",
        metric_category: str = "",
        metric_type: str = "",
        metric_keywords: Optional[List[str]] = None,
        metric_profile: Optional[MetricRetrievalProfile] = None,
    ) -> str:
        """
        Build LLM analysis prompt containing segment tag information
        
        Args:
            metric_name: Metric name
            metric_id: Metric ID
            segments: Related segment content
            segment_metadata: Segment metadata information
            metric_unit: Expected unit for quantitative metrics (if any)
            metric_description: Extra metric definition from framework data
            metric_topic: Framework topic/theme, if available
            metric_category: Framework category, if available
            metric_type: Framework type, if available
            metric_keywords: Framework/search keywords, if available
            
        Returns:
            str: Prompt text
        """
        # Build segment text containing tag information.
        # IMPORTANT: Include segment_id to allow the LLM to point back to the exact evidence.
        segments_text_parts: List[str] = []
        for i, seg in enumerate(segments):
            seg = str(seg or "").strip()
            segment_info = f"Segment {i+1}:\n{seg}"

            if segment_metadata and i < len(segment_metadata):
                meta = segment_metadata[i] or {}
                seg_id = meta.get("segment_id", "")
                page = meta.get("page_number", None)
                rtype = meta.get("retrieval_type", "")
                score = meta.get("score", 0.0)
                segment_info += f"\n[Segment ID: {seg_id}; Tag Info: Page {page}, Retrieval Type: {rtype}, Match Score: {score:.3f}"
                if meta.get("matched_keywords"):
                    try:
                        kws = ", ".join(meta.get("matched_keywords") or [])
                        if kws:
                            segment_info += f", Matched Keywords: {kws}"
                    except Exception:
                        pass
                if meta.get("link_target_page") is not None:
                    segment_info += (
                        f", Internal Link: Page {meta.get('link_source_page')} -> "
                        f"Page {meta.get('link_target_page')}"
                    )
                    if meta.get("link_anchor_text"):
                        segment_info += f", Anchor: {meta.get('link_anchor_text')}"
                segment_info += "]"

            segments_text_parts.append(segment_info)

        segments_text = "\n\n".join(segments_text_parts)

        unit_line = (
            f"- Expected unit (if quantitative): {metric_unit}\n"
            if (metric_unit or "").strip()
            else ""
        )
        topic_line = (
            f"- Framework topic: {metric_topic}\n"
            if (metric_topic or "").strip()
            else ""
        )
        category_line = (
            f"- Framework category: {metric_category}\n"
            if (metric_category or "").strip()
            else ""
        )
        type_line = (
            f"- Framework type: {metric_type}\n"
            if (metric_type or "").strip()
            else ""
        )
        keyword_values = [str(x).strip() for x in (metric_keywords or []) if str(x).strip()]
        keywords_line = (
            f"- Metric keywords: {', '.join(keyword_values[:18])}\n"
            if keyword_values
            else ""
        )
        desc_text = str(metric_description or "").strip()
        if len(desc_text) > 3600:
            desc_text = desc_text[:3597].rstrip() + "..."
        desc_line = (
            f"- Metric definition / guidance: {desc_text}\n"
            if desc_text
            else ""
        )
        profile_rules = self._profile_prompt_rules(metric_profile)
        profile_rules_text = (
            json.dumps(profile_rules, ensure_ascii=False, sort_keys=True)
            if profile_rules
            else "No generated metric-specific extraction rules are available."
        )

        prompt = f"""As a professional ESG/SASB compliance analyst, conduct one unified disclosure assessment for the current metric.

Metric Information:
- Metric Name: {metric_name}
- Metric Code: {metric_code or metric_id}
{topic_line}{category_line}{type_line}{unit_line}{keywords_line}{desc_line}
Mandatory Metric Profile Extraction Rules:
{profile_rules_text}

All Related Retrieved Segments (with segment_id + tag info):
{segments_text if segments_text else "No related segments found"}

Analyze all retrieved segments together. Do not score segments independently. The final answer must be based on the best evidence for the current metric.

Core assessment principles:
1) Respond ONLY with a JSON object. No markdown and no backticks.
1a) The Mandatory Metric Profile Extraction Rules above are executable constraints. Apply the shared-Code component rule, rejected value sources, year rules, variable dimensions, required labels, and sibling warnings before selecting a value or status. They override generic same-Code shortcuts below when stricter.
2) The final "disclosure_status" must be exactly one of: "fully_disclosed", "partially_disclosed", "not_disclosed".
3) Python will not derive or broadly correct the status later. Your "disclosure_status" is the final model classification, subject only to a narrow deterministic employee-category boundary check that prevents a proxy category from being treated as an exact category.
4) The current Metric Name is the metric being assessed. If the framework definition contains multiple components under the same SASB code, do not require components that are not part of the current Metric Name.
5) Assess the current metric itself, not the broader topic and not all sibling sub-items under the same SASB code.
6) Treat the metric definition/guidance as interpretive context, not as a clause-by-clause checklist. It helps identify the metric core, denominator, required split, and measurement basis. Do not require every note, example, auxiliary detail, or technical-protocol phrase unless it materially changes the current metric itself.
7) Same SASB code / same metric direct-disclosure rule: if a retrieved report segment, table row, or table section explicitly contains the current SASB code OR clearly contains the current metric label, and provides the current metric's value or narrative, classify the current metric as fully_disclosed.
8) Same-code evidence has priority over definition overchecking. Do not downgrade a direct disclosure only because sibling components are absent, because definition/guidance contains extra clauses, because the wording is not identical, because the report does not restate the technical protocol, or because the source unit differs from the expected unit but is equivalent or convertible.
9) For split metrics under one SASB code, assess only the current Metric Name. A direct value for the current sub-item is fully_disclosed even if sibling sub-items under the same code are absent. A value for a clearly different sub-item should not be used as the value for this sub-item.
10) Unit differences must be handled by judgment, not treated as automatic gaps. If the reported unit is equivalent, safely convertible, or standardly normalizable to the expected unit, use the raw value/raw unit and provide the converted value when possible. Do not downgrade from fully_disclosed when unit conversion, unit wording, or missing conversion narrative is the only remaining issue.
11) Do not require the report to state a conversion factor if the conversion is standard and safe. Use ordinary conversions such as MWh to GJ, kWh to GJ, liters to cubic meters, kilograms to metric tons, and percentage fractions when appropriate.
12) Use "value_status" as one of: exact, converted, derived, approximate, raw_unit_only, unit_mismatch, ambiguous, none.
13) For qualitative/narrative metrics, a direct description of the requested approach, policy, process, practice, governance mechanism, or management system can be fully_disclosed without a numeric value. Set "value" to null in that case.
14) Distinguish imperfect disclosure from no disclosure. If the current metric itself is disclosed but has non-core ambiguity, use partially_disclosed rather than not_disclosed. If the current metric is directly and sufficiently disclosed, use fully_disclosed.
15) evidence_quote must be a short excerpt (<= 180 chars) that supports your conclusion; when using table evidence, quote from the full row context rather than an isolated cell.
16) If a segment includes [Full Table Row Context], treat that row as one evidence unit. Use the row header, all row cells, column headers, values, and units together; do not judge a table cell in isolation.
17) For table rows or narrative evidence with multiple years, extract every explicitly reported metric-specific annual value into year_values. Keep all years in this one metric result. The scalar value may represent the latest year for compatibility unless the current metric explicitly requests another period.
18) If a segment includes [Structured Evidence Metadata], use row headers, column headers, cell values, and units as table context.
19) If multiple candidate segments conflict, choose the segment that most directly matches the current metric name, current metric code, expected unit, category/split, and framework context.
20) Derive a value only when the framework definition explicitly gives the formula and all operands are disclosed for the same year, same reporting boundary and compatible units. Include operation, formula and fully sourced operands; otherwise set derived_calculation to null.
21) For evidence reached through an internal PDF link, evaluate [Internal PDF Link Source Context] and [Internal PDF Link Target Context] together. The source row, anchor text and surrounding cells establish what the target means. If the source row itself contains a real metric value, such as "Employee engagement as a percentage: 87%", extract that value even when the same row also contains a link or is in a reporting-framework index.
22) Representation/distribution metrics may be answered by multiple percentages in one category table. When the requested category and latest year are present, treat the complete distribution as metric evidence, set value to null with value_status "ambiguous" if there is no single canonical scalar, and preserve the values in specific_data_found/evidence_quote. Multiple legitimate values are not a reason for not_disclosed.
23) A broader report category may be a partial proxy: "people leader roles" does not uniquely separate executive from non-executive management, and "non-technical roles" is not necessarily identical to all other employees. Use partially_disclosed for those mappings and explain the boundary; do not discard the table. "Technical" directly supports technical employees, subject to any geographic or scope limitation.

Disclosure status definitions:

fully_disclosed:
The evidence directly discloses the current metric itself and provides the metric-specific value or metric-specific narrative needed to answer it. A report row/table/section that explicitly contains the current SASB code or the current metric label and gives a value or narrative for the current metric is fully_disclosed. For quantitative metrics, values may be reported in the expected unit, an equivalent unit, or a safely convertible/normalizable unit. For qualitative metrics, a direct description of the requested approach, policy, process, practice, governance mechanism, or management system is fully_disclosed. Fully_disclosed does not require the report to reproduce all definition/guidance clauses, technical-protocol details, examples, notes, auxiliary explanatory items, all sibling components, or an explicit conversion formula unless those items materially change the current metric core, denominator, required split, or measurement basis. Non-core scope or methodology uncertainty should not prevent fully_disclosed when the current metric label/code and value/narrative are directly disclosed.

partially_disclosed:
The evidence addresses the current metric itself but does not fully answer it. This includes cases where the metric is clearly hit but the disclosed value is not directly usable, the current sub-item or denominator is not clear, the value is only a proxy for the current metric, the evidence is limited to a narrower required split/category, or the qualitative evidence covers only part of the requested approach. Partially_disclosed requires evidence for the current metric itself, not merely the broader topic or a sibling sub-item. Do not use partially_disclosed merely because the report omits sibling sub-items, uses a convertible unit, or does not repeat every definition/guidance detail.

not_disclosed:
The evidence does not disclose the current metric itself. This includes no retrieved evidence, unrelated evidence, broader-topic discussion without the current metric, a value for a clearly different metric or clearly different sub-item, target-only or future-only statements for a current-performance metric, generic policies or activities that do not answer the requested metric, or evidence that lacks a metric-specific value or metric-specific narrative. Same topic, same SASB topic area, or same general sustainability theme is not enough when the current metric itself is not disclosed. However, do not use not_disclosed when a report row/table directly provides the current metric's value or narrative under the same SASB code or current metric label.

Return JSON format:
{{
  "metric_hit": <true|false|null>,
  "disclosure_status": "fully_disclosed|partially_disclosed|not_disclosed",
  "has_disclosure": true/false,
  "disclosure_quality": "high/medium/low/none",
  "value_status": "exact|converted|approximate|raw_unit_only|unit_mismatch|ambiguous|none|null",
  "reasoning": "Comprehensive reasoning based on all segments",
  "value": <number|null>,
  "raw_value": <number|null>,
  "raw_unit": "<unit|null>",
  "page": <int|null>,
  "evidence_segment_id": "<segment_id|null>",
  "evidence_quote": "<short quote|null>",
  "specific_data_found": "<detailed context, may include unit/year if present>",
  "year_values": [
    {{
      "year": <int>,
      "value": <number>,
      "raw_value": <number|null>,
      "unit": "<reported unit|null>",
      "page": <int|null>,
      "evidence_segment_id": "<segment_id|null>",
      "evidence_quote": "<year-specific short quote|null>"
    }}
  ],
  "derived_calculation": null OR {{
    "operation": "ratio_percent|ratio|sum|difference",
    "formula": "<formula stated by the framework>",
    "operands": [
      {{"name": "<name>", "value": <number>, "unit": "<unit>", "year": <int>, "boundary": "<reporting boundary>", "segment_id": "<segment_id>"}}
    ]
  }},
  "improvement_suggestions": ["Suggestion 1", "Suggestion 2", ...]
}}
"""
        return prompt
    
    def _map_llm_disclosure_status(self, llm_response: dict) -> DisclosureStatus:
        """Map the LLM-provided final disclosure_status to the internal enum.

        This function intentionally does not infer or reclassify from other fields
        such as metric_hit, has_disclosure, disclosure_quality, value_status,
        numeric values, or units. The prompt is responsible for the final
        disclosure decision.
        """
        raw_status = str(llm_response.get("disclosure_status", "") or "").strip().lower()
        raw_status = raw_status.replace(" ", "_").replace("-", "_")
        aliases = {
            "fully_disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "full_disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "full": DisclosureStatus.FULLY_DISCLOSED,
            "high_quality_disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "partially_disclosed": DisclosureStatus.PARTIALLY_DISCLOSED,
            "partial_disclosed": DisclosureStatus.PARTIALLY_DISCLOSED,
            "partial": DisclosureStatus.PARTIALLY_DISCLOSED,
            "partially": DisclosureStatus.PARTIALLY_DISCLOSED,
            "disclosed_but_not_clear": DisclosureStatus.PARTIALLY_DISCLOSED,
            "not_disclosed": DisclosureStatus.NOT_DISCLOSED,
            "non_disclosed": DisclosureStatus.NOT_DISCLOSED,
            "none": DisclosureStatus.NOT_DISCLOSED,
            "not": DisclosureStatus.NOT_DISCLOSED,
            "no_disclosure": DisclosureStatus.NOT_DISCLOSED,
            "undisclosed": DisclosureStatus.NOT_DISCLOSED,
        }
        if raw_status not in aliases:
            raise ValueError(
                "LLM response missing or invalid final disclosure_status; "
                "expected fully_disclosed, partially_disclosed, or not_disclosed."
            )
        return aliases[raw_status]

    def _get_segment_by_id(self, report_content: ReportContent, segment_id: str):
        """
        Get segment content by ID
        
        Args:
            report_content: Report content
            segment_id: Segment ID
            
        Returns:
            TextSegment or None
        """
        return self._get_report_segment_cache(report_content)["by_id"].get(segment_id)
    
    def generate_compliance_report(self, assessment: ComplianceAssessment) -> str:
        """
        Generate Markdown format compliance report
        
        Args:
            assessment: Compliance assessment result
            
        Returns:
            str: Markdown format report
        """
        def _status_value(analysis: DisclosureAnalysis) -> str:
            raw = getattr(analysis, "disclosure_status", "")
            return raw.value if hasattr(raw, "value") else str(raw or "")

        def _status_label(status: str) -> str:
            return {
                "fully_disclosed": "Disclosed",
                "partially_disclosed": "Partially Disclosed",
                "not_disclosed": "Not Disclosed",
            }.get(status, status.replace("_", " ").title() or "Unknown")

        def _clean_text(value: object, *, default: str = "") -> str:
            text = str(value or default).replace("\r\n", "\n").replace("\r", "\n")
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

        def _compact(value: object, limit: int = 180) -> str:
            text = re.sub(r"\s+", " ", _clean_text(value)).strip()
            if not text:
                return ""
            return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."

        def _table_cell(value: object, limit: int = 180) -> str:
            text = _compact(value, limit=limit)
            if not text:
                return "-"
            return text.replace("|", "\\|")

        def _metric_code(analysis: DisclosureAnalysis) -> str:
            return (
                _clean_text(getattr(analysis, "metric_code", ""))
                or _clean_text(getattr(analysis, "metric_id", ""))
                or "-"
            )

        def _metric_name(analysis: DisclosureAnalysis) -> str:
            return _clean_text(getattr(analysis, "metric_name", "")) or "Unnamed metric"

        def _value_text(analysis: DisclosureAnalysis) -> str:
            value = getattr(analysis, "value", None)
            if value is None or str(value).strip().lower() in {"", "n/a", "na", "none", "null"}:
                return "n/a"
            unit = _clean_text(getattr(analysis, "unit", ""))
            return f"{value} {unit}".strip()

        def _year_values_text(analysis: DisclosureAnalysis) -> str:
            rendered: List[str] = []
            for item in getattr(analysis, "year_values", None) or []:
                if not isinstance(item, dict):
                    continue
                year = item.get("year")
                value = item.get("value")
                if year is None or value is None:
                    continue
                unit = _clean_text(item.get("unit") or getattr(analysis, "unit", ""))
                text = f"FY{year}: {value} {unit}".strip()
                if text not in rendered:
                    rendered.append(text)
            return "; ".join(rendered) or "-"

        def _page_text(analysis: DisclosureAnalysis) -> str:
            page = getattr(analysis, "page", None)
            return str(page) if page not in (None, "") else "-"

        def _evidence_text(analysis: DisclosureAnalysis, limit: int = 3) -> str:
            segments = [
                str(x).strip()
                for x in (getattr(analysis, "evidence_segments", None) or [])
                if str(x).strip()
            ]
            if not segments:
                return "-"
            shown = segments[:limit]
            suffix = f" (+{len(segments) - limit} more)" if len(segments) > limit else ""
            return ", ".join(shown) + suffix

        def _evidence_source_text(analysis: DisclosureAnalysis) -> str:
            rendered: List[str] = []
            for source in getattr(analysis, "evidence_sources", None) or []:
                if not isinstance(source, dict):
                    continue
                if source.get("source_type") == "linked_page":
                    text = f"internal link page {source.get('link_source_page')} -> page {source.get('target_page')}"
                else:
                    text = f"report page {source.get('data_page')}"
                if text not in rendered:
                    rendered.append(text)
            return "; ".join(rendered)

        def _first_suggestion(analysis: DisclosureAnalysis) -> str:
            suggestions = [
                _compact(x, 220)
                for x in (getattr(analysis, "improvement_suggestions", None) or [])
                if _clean_text(x)
            ]
            if suggestions:
                return suggestions[0]
            status = _status_value(analysis)
            if status == "not_disclosed":
                return "Add an explicit metric-level disclosure with value, unit, reporting period, and boundary."
            if status == "partially_disclosed":
                return "Clarify the disclosed value or narrative with scope, methodology, unit, and reporting period."
            return "Maintain consistent metric-level disclosure and evidence references in future reports."

        def _pct(count: int, total: int) -> str:
            return f"{count / max(total, 1):.1%}"

        def _metric_phrase(count: int) -> str:
            return f"{count} metric" if count == 1 else f"{count} metrics"

        def _topic_key(analysis: DisclosureAnalysis) -> str:
            return (
                _clean_text(getattr(analysis, "topic", ""))
                or _clean_text(getattr(analysis, "type", ""))
                or _clean_text(getattr(analysis, "category", ""))
                or "General"
            )

        def _append_field(lines: List[str], label: str, value: object, *, limit: int = 0) -> None:
            text = _clean_text(value)
            if not text:
                return
            if limit > 0:
                text = _compact(text, limit)
            lines.append(f"- **{label}**: {text}")

        def _append_suggestions(lines: List[str], analysis: DisclosureAnalysis) -> None:
            suggestions = [
                _clean_text(x)
                for x in (getattr(analysis, "improvement_suggestions", None) or [])
                if _clean_text(x)
            ]
            if not suggestions and _status_value(analysis) != "fully_disclosed":
                suggestions = [_first_suggestion(analysis)]
            if not suggestions:
                return
            lines.append("- **Recommended actions**:")
            for suggestion in suggestions[:5]:
                lines.append(f"  - {suggestion}")

        seen_metric_ids = set()
        unique_metric_analyses = []
        for analysis in assessment.metric_analyses:
            key = (
                _clean_text(getattr(analysis, "metric_id", ""))
                or _clean_text(getattr(analysis, "metric_code", ""))
                or _metric_name(analysis)
            )
            if key not in seen_metric_ids:
                unique_metric_analyses.append(analysis)
                seen_metric_ids.add(key)

        total_unique_metrics = len(unique_metric_analyses)
        disclosure_summary = {
            "fully_disclosed": 0,
            "partially_disclosed": 0,
            "not_disclosed": 0,
        }
        for analysis in unique_metric_analyses:
            status = _status_value(analysis)
            if status in disclosure_summary:
                disclosure_summary[status] += 1
            else:
                disclosure_summary["not_disclosed"] += 1

        overall_score = (
            disclosure_summary["fully_disclosed"]
            + 0.5 * disclosure_summary["partially_disclosed"]
        ) / max(total_unique_metrics, 1)

        needs_attention = [
            a
            for a in unique_metric_analyses
            if _status_value(a) in {"not_disclosed", "partially_disclosed"}
        ]
        priority_gaps = sorted(
            needs_attention,
            key=lambda a: 0 if _status_value(a) == "not_disclosed" else 1,
        )

        topic_counts: Dict[str, int] = {}
        for analysis in needs_attention:
            topic = _topic_key(analysis)
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        top_topics = sorted(topic_counts.items(), key=lambda item: item[1], reverse=True)[:3]

        lines: List[str] = [
            "# ESG Compliance Assessment Report",
            "",
            "## Executive Summary",
            f"- **Overall compliance score**: {overall_score:.2%}",
            (
                f"- **Coverage**: {disclosure_summary['fully_disclosed']} disclosed, "
                f"{disclosure_summary['partially_disclosed']} partially disclosed, "
                f"{disclosure_summary['not_disclosed']} not disclosed out of "
                f"{_metric_phrase(total_unique_metrics)}."
            ),
            (
                f"- **Priority workload**: {_metric_phrase(len(needs_attention))} "
                f"{'needs' if len(needs_attention) == 1 else 'need'} follow-up "
                f"({disclosure_summary['not_disclosed']} missing, "
                f"{disclosure_summary['partially_disclosed']} incomplete)."
            ),
        ]
        if top_topics:
            topic_text = "; ".join(f"{topic}: {count}" for topic, count in top_topics)
            lines.append(f"- **Largest disclosure gaps by topic/type**: {topic_text}.")

        lines.extend(
            [
                "",
                "## Report Overview",
                f"- **Report ID**: {assessment.report_id}",
                f"- **Assessment Date**: {assessment.assessment_date.strftime('%Y-%m-%d %H:%M:%S')}",
                f"- **Analyzed Metrics**: {total_unique_metrics}",
                f"- **Overall Compliance Score**: {overall_score:.2%}",
                "",
                "## Disclosure Status Statistics",
                "",
                "| Disclosure Status | Count | Percentage |",
                "|---|---:|---:|",
                (
                    f"| Disclosed | {disclosure_summary['fully_disclosed']} | "
                    f"{_pct(disclosure_summary['fully_disclosed'], total_unique_metrics)} |"
                ),
                (
                    f"| Partially Disclosed | {disclosure_summary['partially_disclosed']} | "
                    f"{_pct(disclosure_summary['partially_disclosed'], total_unique_metrics)} |"
                ),
                (
                    f"| Not Disclosed | {disclosure_summary['not_disclosed']} | "
                    f"{_pct(disclosure_summary['not_disclosed'], total_unique_metrics)} |"
                ),
                "",
            ]
        )

        if priority_gaps:
            lines.extend(
                [
                    "## Priority Gaps",
                    "",
                    "| Priority | Status | Code | Metric | Evidence/Page | Recommended Action |",
                    "|---:|---|---|---|---|---|",
                ]
            )
            for idx, analysis in enumerate(priority_gaps[:10], start=1):
                evidence_page = f"{_evidence_text(analysis)} / page {_page_text(analysis)}"
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(idx),
                            _table_cell(_status_label(_status_value(analysis)), 80),
                            _table_cell(_metric_code(analysis), 80),
                            _table_cell(_metric_name(analysis), 220),
                            _table_cell(evidence_page, 120),
                            _table_cell(_first_suggestion(analysis), 260),
                        ]
                    )
                    + " |"
                )
            if len(priority_gaps) > 10:
                lines.append("")
                lines.append(f"_Showing top 10 of {len(priority_gaps)} metrics needing follow-up._")
            lines.append("")

        lines.extend(
            [
                "## Disclosure Matrix",
                "",
                "| Status | Code | Metric | Selected Value | Annual Values | Page | Evidence Segments |",
                "|---|---|---|---|---|---:|---|",
            ]
        )
        for analysis in unique_metric_analyses:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(_status_label(_status_value(analysis)), 80),
                        _table_cell(_metric_code(analysis), 80),
                        _table_cell(_metric_name(analysis), 240),
                        _table_cell(_value_text(analysis), 120),
                        _table_cell(_year_values_text(analysis), 320),
                        _table_cell(_page_text(analysis), 40),
                        _table_cell(_evidence_text(analysis), 160),
                    ]
                )
                + " |"
            )
        lines.extend(["", "## Detailed Analysis Results", ""])

        status_sections = [
            ("partially_disclosed", "Partially Disclosed - Needs Follow-up"),
            ("not_disclosed", "Not Disclosed - Missing Metrics"),
            ("fully_disclosed", "Disclosed - Supported Metrics"),
        ]
        for status, title in status_sections:
            status_metrics = [a for a in unique_metric_analyses if _status_value(a) == status]
            if not status_metrics:
                continue
            lines.extend([f"### {title}", ""])
            for analysis in status_metrics:
                lines.append(f"#### {_metric_name(analysis)} ({_metric_code(analysis)})")
                lines.append(f"- **Status**: {_status_label(status)}")
                _append_field(lines, "Topic/Type", _topic_key(analysis))
                _append_field(lines, "Expected unit", getattr(analysis, "unit", ""))
                lines.append(f"- **Reported value**: {_value_text(analysis)}")
                if _year_values_text(analysis) != "-":
                    lines.append(f"- **All annual values**: {_year_values_text(analysis)}")
                    _append_field(lines, "Selected year", getattr(analysis, "selected_year", None))
                lines.append(f"- **Page**: {_page_text(analysis)}")
                if _evidence_text(analysis) != "-":
                    lines.append(f"- **Evidence segments**: {_evidence_text(analysis)}")
                _append_field(lines, "Evidence sources", _evidence_source_text(analysis), limit=500)
                derived = getattr(analysis, "derived_calculation", None)
                if isinstance(derived, dict):
                    _append_field(lines, "Derived formula", derived.get("formula"), limit=500)
                _append_field(lines, "Evidence context", getattr(analysis, "context", ""), limit=700)
                _append_field(lines, "Analysis reasoning", getattr(analysis, "reasoning", ""), limit=1200)
                _append_suggestions(lines, analysis)
                lines.append("")

        lines.extend(["## Improvement Recommendations Summary", ""])
        if needs_attention:
            lines.append(
                "1. Prioritize metric-level remediation for missing and partial "
                "disclosures before broad narrative edits."
            )
            if disclosure_summary["not_disclosed"]:
                missing = [
                    f"{_metric_code(a)} {_metric_name(a)}"
                    for a in priority_gaps
                    if _status_value(a) == "not_disclosed"
                ][:5]
                lines.append(
                    "2. Add explicit disclosures for missing metrics: "
                    + "; ".join(missing)
                    + "."
                )
            else:
                lines.append(
                    "2. No fully missing metrics were detected; focus on strengthening partial disclosures."
                )
            if disclosure_summary["partially_disclosed"]:
                lines.append(
                    "3. For partially disclosed metrics, add the missing value, "
                    "unit, reporting boundary, period, and evidence page reference."
                )
            else:
                lines.append(
                    "3. Keep current disclosed metrics traceable by preserving "
                    "value, unit, period, and source page references."
                )
            lines.append(
                "4. Align future report sections with framework metric codes so "
                "retrieval and reviewer traceability are stronger."
            )
        else:
            lines.append(
                "All analyzed metrics are disclosed. Maintain the current "
                "metric-code mapping, evidence citations, and reporting-period "
                "consistency in future reports."
            )

        return "\n".join(lines).rstrip() + "\n"
