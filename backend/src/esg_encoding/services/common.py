"""
ESG System API Endpoints
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import Any, Dict, List, Optional, Set
import os
import re
import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger
import time
import threading
import asyncio
import hashlib

from ..environment import load_backend_environment

load_backend_environment()

from ..models import (
    ProcessingConfig,
    ChatRequest,
    ChatResponse,
    ComplianceAssessment,
    DisclosureAnalysis,
    DisclosureStatus,
    ReportContent,
    DocumentContent,
    TextSegment,
    MetricCollection,
    LoginRequest,
    RegisterRequest,
    AuthResponse
)
from ..exceptions import InputError, AccessError
from ..cross_analysis_models import (
    CrossCompareRequest,
    CrossCompareResponse,
    CrossReportsResponse,
    CrossRecordsRequest,
    CrossRecordsResponse,
    ExcelMetricsRequest,
    ExcelMetricsResponse,
    CrossDisclosedCacheResponse,
)
from ..cross_report_metadata import get_reports_info
# Cross-analysis imports are intentionally lazy. Importing them here pulls in
# HippoRAG / sentence_transformers even for simple compliance/profile operations.


def compare_topic(*args, **kwargs):
    from ..cross_analysis import compare_topic as _compare_topic
    return _compare_topic(*args, **kwargs)


def extract_records_for_topic(*args, **kwargs):
    from ..cross_analysis import extract_records_for_topic as _extract_records_for_topic
    return _extract_records_for_topic(*args, **kwargs)


def _get_cross_cache_dir() -> Path:
    # Keep assessment-driven cache requests independent from the semantic
    # cross-analysis module and its embedding/model imports.
    return file_manager.base_dir / "outputs" / "cross_analysis"


def dimension_by_key(*args, **kwargs):
    from ..cross_catalog import dimension_by_key as _dimension_by_key
    return _dimension_by_key(*args, **kwargs)
from ..auth.service import login, register
from ..auth.dependencies import get_current_user, get_current_user_optional
from ..report_encoder import ReportEncoder
from ..metric_processor import MetricProcessor
from ..retrieval.dual_channel import DualChannelRetriever
from ..retrieval.evidence_retriever import (
    iter_metric_collection_results,
    retrieve_metric_collection,
)
from ..retrieval.metric_profile import find_metric_profile
from ..disclosure_inference import DisclosureInferenceEngine, COMPLIANCE_VALUE_NA
from ..chat.chatbot import ESGChatbot
from ..retrieval.hipporag.patch import enable_hipporag
from ..file_manager import file_manager
from ..excel_exporter import ExcelExporter

# Global variables to store system components
system_components = {
    "config": None,
    "report_encoder": None,
    "metric_processor": None,
    "dual_retriever": None,
    "disclosure_engine": None,
    "chatbot": None,
    "current_report": None,
    "current_assessment": None,
    "current_metrics": None,
    "current_framework": None,  # Store framework (e.g., SASB, GRI)
    "current_industry": None,  # Store main industry
    "current_semi_industry": None,  # Store sub-industry
    "current_gri_sector": None,  # GRI sector slug when framework is GRI
    "current_gri_topic": None,   # GRI topic slug when framework is GRI
    "current_company": None  # Store company name
}

# -----------------------------
# Cross-analysis Excel metrics job state
# -----------------------------
_excel_metrics_jobs = {}  # key -> {"thread": Thread, "started_at": float}
_excel_metrics_jobs_lock = threading.Lock()

# Single global ESGChatbot: serialize context/session ops vs background upload (HippoRAG + load_context).
_chatbot_ops_lock = threading.RLock()

# Per-cache-key locks prevent concurrent cross-analysis requests from rebuilding
# and replacing the same JSON cache file at the same time.  The guard protects
# creation of entries in the lock registry itself.
_cross_disclosed_locks: Dict[str, threading.Lock] = {}
_cross_disclosed_locks_guard = threading.Lock()


# Deleted deprecated function _parse_compliance_report() (179 lines)
# This function parsed Markdown reports with heuristic guessing and preset defaults.
# Now loading assessment data directly from JSON files for accuracy.

# Shared helpers moved out of api/core.py.

def _parse_scope_slugs_json(raw: Optional[str], fallback: Optional[str]) -> List[str]:
    """Parse JSON array of scope slugs from multipart field `scopeSlugs`, else single fallback."""
    if raw:
        s = str(raw).strip()
        if s:
            try:
                data = json.loads(s)
                if isinstance(data, list):
                    out = [str(x).strip() for x in data if str(x).strip()]
                    if out:
                        return out
            except Exception:
                pass
    if fallback and str(fallback).strip():
        return [str(fallback).strip()]
    return []


def _compliance_manifest_path(assessment_dir: Path, file_id: str) -> Path:
    return assessment_dir / f"{file_id}_compliance_manifest.json"


def _load_compliance_manifest(assessment_dir: Path, file_id: str) -> Optional[dict]:
    p = _compliance_manifest_path(assessment_dir, file_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_compliance_manifest(
    assessment_dir: Path,
    file_id: str,
    framework: str,
    outputs: List[dict],
    expected_scope_keys: Optional[List[str]] = None,
) -> None:
    assessment_dir.mkdir(parents=True, exist_ok=True)
    default_sk = None
    if outputs:
        default_sk = outputs[0].get("scope_key")
    elif expected_scope_keys:
        default_sk = expected_scope_keys[0]
    body: dict = {
        "file_id": file_id,
        "framework": framework,
        "default_scope_key": default_sk,
        "outputs": outputs,
    }
    if expected_scope_keys:
        body["expected_scope_keys"] = expected_scope_keys
    _compliance_manifest_path(assessment_dir, file_id).write_text(
        json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _compliance_file_stem_for_scope(fw: str, file_info: dict, scope_key: str) -> str:
    """Filename segment before `_{file_id}_compliance.*` (matches upload naming)."""
    sk = str(scope_key).strip()
    if not sk:
        return _sanitize_compliance_filename_part("report")
    if fw == "GRI":
        gs = (file_info.get("gri_sector") or "").strip()
        return _sanitize_compliance_filename_part(f"GRI_{gs}_{sk}")
    if fw == "SASB":
        return _sanitize_compliance_filename_part(sk)
    if fw == "CDP":
        return _sanitize_compliance_filename_part(f"CDP_{sk}")
    if fw == "TCFD":
        return _sanitize_compliance_filename_part(f"TCFD_{sk}")
    return _sanitize_compliance_filename_part(sk)


def _compliance_json_path_for_scope(
    assessment_dir: Path,
    file_id: str,
    fw: str,
    file_info: dict,
    scope_key: str,
) -> Optional[Path]:
    """Resolve per-scope compliance JSON path (matches upload naming)."""
    sk = str(scope_key).strip()
    if not sk:
        return None
    part = _compliance_file_stem_for_scope(fw, file_info, sk)
    p = assessment_dir / f"{part}_{file_id}_compliance.json"
    return p if p.is_file() else None


def _paths_for_scope_compliance_bundle(
    file_manager, file_id: str, file_info: dict, scope_key: str
) -> tuple[Path, Path, Path]:
    """json, xlsx, markdown paths for one scope (may not exist on disk)."""
    fw = (file_info.get("framework") or "").strip()
    part = _compliance_file_stem_for_scope(fw, file_info, scope_key)
    compliance_dir = Path(file_manager.compliance_outputs)
    json_p = compliance_dir / f"{part}_{file_id}_compliance.json"
    xlsx_p = compliance_dir / f"{part}_{file_id}_compliance.xlsx"
    md_stem = _sanitize_compliance_filename_part(f"{part}")
    md_p = Path(file_manager.markdown_outputs) / f"compliance_report_{file_id}_{md_stem}.md"
    return json_p, xlsx_p, md_p


def _build_scope_rows(
    file_manager, file_info: dict, m: Optional[dict]
) -> List[dict]:
    """One row per expected scope for UI; ready when output JSON exists (or manifest lists it)."""
    file_id = file_info.get("file_id")
    if not file_id:
        return []
    fw = (file_info.get("framework") or "").strip()
    expected: List[str] = []
    if m and isinstance(m.get("expected_scope_keys"), list) and m["expected_scope_keys"]:
        expected = [str(x).strip() for x in m["expected_scope_keys"] if str(x).strip()]
    else:
        raw = file_info.get("scope_slugs_json")
        if raw:
            try:
                slugs = json.loads(raw)
                if isinstance(slugs, list) and len(slugs) > 1:
                    expected = [str(x).strip() for x in slugs if str(x).strip()]
            except Exception:
                pass
    if len(expected) <= 1:
        return []

    done_manifest: Set[str] = set()
    if m:
        for o in m.get("outputs") or []:
            sk = o.get("scope_key")
            if sk is not None:
                done_manifest.add(str(sk))

    assessment_dir = Path(file_manager.compliance_outputs)
    rows: List[dict] = []
    for sk in expected:
        path = _compliance_json_path_for_scope(
            assessment_dir, str(file_id), fw, file_info, sk
        )
        ready = path is not None or sk in done_manifest
        rows.append(
            {
                "scope_key": sk,
                "ready": ready,
                "label": _slug_to_label(sk),
            }
        )
    return rows


def _scope_progress_for_report(file_manager, file_info: dict) -> dict:
    """Derive multi-scope analysis progress from manifest and/or compliance JSON files."""
    if file_info.get("file_type") != "report":
        return {}
    file_id = file_info.get("file_id")
    if not file_id:
        return {}
    assessment_dir = Path(file_manager.compliance_outputs)
    m = _load_compliance_manifest(assessment_dir, file_id)
    scope_rows = _build_scope_rows(file_manager, file_info, m)
    n_done = 0
    n_exp = 0

    if m:
        outs = m.get("outputs") or []
        n_done = len(outs) if isinstance(outs, list) else 0
        try:
            glob_n = len(list(assessment_dir.glob(f"*{file_id}*_compliance.json")))
            n_done = max(n_done, glob_n)
        except Exception:
            pass
        exp = m.get("expected_scope_keys")
        if isinstance(exp, list) and len(exp) > 0:
            n_exp = len(exp)
        elif n_done > 0:
            n_exp = n_done
    else:
        try:
            n_done = len(list(assessment_dir.glob(f"*{file_id}*_compliance.json")))
        except Exception:
            n_done = 0
        raw = file_info.get("scope_slugs_json")
        if raw:
            try:
                slugs = json.loads(raw)
                if isinstance(slugs, list) and slugs:
                    n_exp = len(slugs)
            except Exception:
                pass

    unknown_total = bool(not m and n_done > 0 and n_exp == 0)

    st = str(file_info.get("status", "")).lower()
    partial = (n_exp > 0 and n_done > 0 and n_done < n_exp) or (
        unknown_total and st == "pending"
    )
    all_done = (n_exp > 0 and n_done >= n_exp) or (
        unknown_total and n_done > 0 and st == "processed"
    )
    return {
        "scope_analysis_completed": n_done,
        "scope_analysis_total": n_exp,
        "scope_analysis_partial": partial,
        "scope_analysis_all_done": all_done,
        "scope_analysis_unknown_total": unknown_total,
        "scope_rows": scope_rows,
    }


def _enrich_file_records_with_scope_progress(
    file_manager, files: List[dict]
) -> List[dict]:
    out: List[dict] = []
    for f in files:
        if f.get("file_type") != "report":
            out.append(f)
            continue
        extra = _scope_progress_for_report(file_manager, f)
        merged = {**f, **extra}
        out.append(merged)
    return out


def _json_path_from_manifest(
    assessment_dir: Path, file_id: str, scope_key: Optional[str]
) -> Optional[Path]:
    m = _load_compliance_manifest(assessment_dir, file_id)
    if not m:
        return None
    outs = m.get("outputs") or []
    if not outs:
        return None
    want = (scope_key or "").strip()
    if want:
        for o in outs:
            if o.get("scope_key") == want:
                fn = o.get("json_filename")
                if fn:
                    p = assessment_dir / fn
                    if p.is_file():
                        return p
    fn0 = outs[0].get("json_filename")
    if fn0:
        p0 = assessment_dir / fn0
        if p0.is_file():
            return p0
    return None


def _sanitize_compliance_filename_part(s: Optional[str]) -> str:
    """Make a string safe for use in compliance output filenames (e.g. subindustry)."""
    if not s or not str(s).strip():
        return "report"
    s = str(s).strip()
    for c in '<>:"/\\|?*':
        s = s.replace(c, "_")
    return s[:80] if len(s) > 80 else s


def _slug_to_label(slug: str) -> str:
    """Convert slug (e.g. coal_sector) to display label (e.g. Coal Sector)."""
    if not slug:
        return ""
    return slug.replace("_", " ").strip().title()


def _get_gri_sectors_and_topics() -> dict:
    """Scan backend/data/gri_metrics/*.json and return sectors + topics per sector.
    Filenames are {sector_slug}_{topic_slug}.json (e.g. coal_sector_climate_change.json).
    Sector slug ends with '_sector' or '_sectors'; we split on that to get sector vs topic.
    """
    gri_dir = Path(__file__).parent.parent.parent / "data" / "gri_metrics"
    if not gri_dir.exists():
        return {"sectors": [], "topicsBySector": {}}
    sectors_set = set()
    topics_by_sector = {}
    for p in gri_dir.glob("*.json"):
        stem = p.stem
        sector_slug, topic_slug = None, None
        if "_sectors_" in stem:
            idx = stem.index("_sectors_") + len("_sectors_")
            sector_slug = stem[:idx].rstrip("_")
            topic_slug = stem[idx:].lstrip("_")
        elif "_sector_" in stem:
            idx = stem.index("_sector_") + len("_sector_")
            sector_slug = stem[:idx].rstrip("_")
            topic_slug = stem[idx:].lstrip("_")
        if not sector_slug or not topic_slug:
            continue
        sectors_set.add(sector_slug)
        if sector_slug not in topics_by_sector:
            topics_by_sector[sector_slug] = set()
        topics_by_sector[sector_slug].add(topic_slug)
    sectors = sorted(sectors_set)
    sectors_list = [{"slug": s, "label": _slug_to_label(s)} for s in sectors]
    topics_by_sector_list = {
        s: [{"slug": t, "label": _slug_to_label(t)} for t in sorted(topics_by_sector[s])]
        for s in sectors
    }
    return {"sectors": sectors_list, "topicsBySector": topics_by_sector_list}


def _assessment_date_sydney_iso(dt: datetime) -> str:
    """Return assessment_date as ISO string in Australia/Sydney timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo("Australia/Sydney")).isoformat()


def _compliance_result_filename(report_name: str, llm_model: Optional[str]) -> str:
    """Build filename field: report name + LLM model name + 'result', e.g. BMW2024ESG_GPT5.2result."""
    stem = (Path(report_name).stem if report_name else "report").strip()
    model = (llm_model or "LLM").strip()
    for c in '<>:"/\\|?*':
        model = model.replace(c, "_")
    return f"{stem}_{model}result"


def _canonical_metric_category(raw: Optional[str], fallback: str = "") -> str:
    """Normalize category labels used across upload, assessment and cross-analysis."""
    s = str(raw or "").strip()
    if not s:
        return str(fallback or "").strip()
    lower = s.lower()
    if lower == "quantitative":
        return "Quantitative"
    if lower in {"qualitative", "discussion and analysis", "discussion"}:
        return "Discussion and Analysis"
    return s


def _metric_row_from_disclosure_analysis(analysis: DisclosureAnalysis) -> dict:
    """Canonical per-metric object for compliance JSON (SASB key order)."""
    disclosure_status = (
        analysis.disclosure_status.value
        if hasattr(analysis.disclosure_status, "value")
        else analysis.disclosure_status
    )
    page = getattr(analysis, "page", None)
    value = getattr(analysis, "value", None)
    context = getattr(analysis, "context", None)
    reasoning = analysis.reasoning
    definition = getattr(analysis, "definition", "") or ""
    category = _canonical_metric_category(getattr(analysis, "category", "") or "")
    unit = getattr(analysis, "unit", "") or ""
    topic = getattr(analysis, "topic", "") or ""
    type_name = getattr(analysis, "type", "") or ""
    metric_code = getattr(analysis, "metric_code", "") or getattr(analysis, "metric_id", "") or ""
    metric_name = getattr(analysis, "metric_name", "") or ""
    year_values = [
        dict(item)
        for item in (getattr(analysis, "year_values", None) or [])
        if isinstance(item, dict)
    ]
    selected_year = getattr(analysis, "selected_year", None)
    value_status = getattr(analysis, "value_status", None)

    return {
        "metric_id": analysis.metric_id,
        "metric_name": metric_name,
        "metric_code": metric_code,
        "disclosure_status": disclosure_status,
        "reasoning": reasoning,
        "unit": unit,
        "category": category,
        "topic": topic,
        "type": type_name,
        "definition": definition,
        "page": page,
        "value": value,
        "year_values": year_values,
        "selected_year": selected_year,
        "value_status": value_status,
        "context": context,
        "Metric": metric_name,
        "Category": category,
        "Unit": unit,
        "Code": metric_code,
        "Topic": topic,
        "Type": type_name,
        "Definition": definition,
        "Value": value,
        "Year Values": year_values,
        "Selected Year": selected_year,
        "Value Status": value_status,
        "Page": page,
        "Context": context,
        "Disclosure Status": disclosure_status,
        "LLM Analysis": reasoning,
        "evidence_segments": list(getattr(analysis, "evidence_segments", None) or []),
        "evidence_sources": list(getattr(analysis, "evidence_sources", None) or []),
        "derived_calculation": getattr(analysis, "derived_calculation", None),
        "improvement_suggestions": list(
            getattr(analysis, "improvement_suggestions", None) or []
        ),
    }


def _normalize_non_sasb_compliance_metric_row(
    m: dict, framework: Optional[str]
) -> None:
    """Preserve framework fields for GRI/CDP/TCFD while ensuring stable output keys."""
    fw = (framework or "").strip().upper()
    if fw not in ("GRI", "CDP", "TCFD"):
        return

    metric = str(m.get("Metric") or m.get("metric_name") or "").strip()
    category = str(m.get("Category") or m.get("category") or "").strip()
    unit = str(m.get("Unit") or m.get("unit") or "").strip()
    code = str(m.get("Code") or m.get("metric_code") or m.get("metric_id") or "").strip()
    topic = str(m.get("Topic") or m.get("topic") or "").strip()
    typ = str(m.get("Type") or m.get("type") or "").strip()
    definition = str(m.get("Definition") or m.get("definition") or "").strip()
    value = m.get("Value") if "Value" in m else m.get("value")
    page = m.get("Page") if "Page" in m else m.get("page")
    context = m.get("Context") if "Context" in m else m.get("context")
    disclosure_status = m.get("Disclosure Status") or m.get("disclosure_status") or m.get("Model Disclosure Status") or ""
    llm_analysis = m.get("LLM Analysis") or m.get("reasoning") or ""

    if not category:
        category = "Quantitative" if unit else "Discussion and Analysis"
    category = _canonical_metric_category(category, "Discussion and Analysis")
    if context is None:
        context = ""

    m["metric_name"] = metric
    m["category"] = category
    m["unit"] = unit
    m["metric_code"] = code
    m["topic"] = topic
    m["type"] = typ
    m["definition"] = definition
    m["value"] = value
    m["page"] = page
    m["context"] = context
    m["disclosure_status"] = disclosure_status
    m["reasoning"] = llm_analysis

    m["Metric"] = metric
    m["Category"] = category
    m["Unit"] = unit
    m["Code"] = code
    m["Topic"] = topic
    m["Type"] = typ
    m["Definition"] = definition
    m["Value"] = value
    m["Page"] = page
    m["Context"] = context
    m["Disclosure Status"] = disclosure_status
    m["LLM Analysis"] = llm_analysis


def _apply_partial_disclosure_json_rules(metric_rows: Optional[List[dict]]) -> None:
    """Match upload/analyze pipelines: partial/not disclosed value and page handling."""
    for m in metric_rows or []:
        if not isinstance(m, dict):
            continue
        s = str(m.get("disclosure_status", "") or "").strip().lower()
        if "partial" in s:
            v = m.get("value")
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                m["value"] = COMPLIANCE_VALUE_NA
        elif "not" in s:
            m["page"] = None
            m["value"] = COMPLIANCE_VALUE_NA
            m["year_values"] = []
            m["selected_year"] = None

        m["Value"] = m.get("value")
        m["Year Values"] = m.get("year_values") or []
        m["Selected Year"] = m.get("selected_year")
        m["Page"] = m.get("page")
        m["Context"] = m.get("context")
        m["Disclosure Status"] = m.get("disclosure_status")
        m["LLM Analysis"] = m.get("reasoning")


def _normalize_lookup_text(value: object) -> str:
    """Normalize text for joining assessment results back to canonical metric rows."""
    text = str(value or "").strip().lower()
    text = text.replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_lookup_code(value: object) -> str:
    """Metric-code join key; keeps code identity but ignores incidental surrounding whitespace."""
    return _normalize_lookup_text(value).strip()


def _backend_data_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data"


def _sasb_metrics_dir() -> Path:
    return _backend_data_dir() / "sasb_metrics"


def _sasb_metric_source_file_for_scope(scope_key: Optional[str]) -> Optional[Path]:
    """Return the canonical backend/data/sasb_metrics file for a SASB scope."""
    if not scope_key or not str(scope_key).strip():
        return None
    root = _sasb_metrics_dir()
    scope = str(scope_key).strip()
    manifest_path = root / "manifest.json"
    mapping: Dict[str, str] = {}
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_map = data.get("semi_industry_to_file") if isinstance(data, dict) else None
            if isinstance(raw_map, dict):
                mapping = {str(k): str(v) for k, v in raw_map.items()}
        except Exception as e:
            logger.warning(f"Failed to load SASB manifest for metric result merge: {e}")

    candidates: List[str] = []
    if scope in mapping:
        candidates.append(mapping[scope])
    norm_scope = _normalize_lookup_text(scope)
    for k, v in mapping.items():
        if _normalize_lookup_text(k) == norm_scope:
            candidates.append(v)
    candidates.extend([
        f"{scope}.json",
        f"{scope.replace(' ', '_')}.json",
        f"{scope.replace('&', 'and').replace(' ', '_')}.json",
    ])

    seen = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        path = root / name
        if path.is_file():
            return path
    return None


def _analysis_status_value(analysis: DisclosureAnalysis) -> str:
    status = analysis.disclosure_status
    return status.value if hasattr(status, "value") else str(status or "")


def _metric_result_overlay_from_analysis(analysis: DisclosureAnalysis) -> Dict[str, Any]:
    """Fields written back to canonical SASB metric rows for backend/frontend display."""
    return {
        "Value": getattr(analysis, "value", None),
        "Year Values": list(getattr(analysis, "year_values", None) or []),
        "Selected Year": getattr(analysis, "selected_year", None),
        "Value Status": getattr(analysis, "value_status", None),
        "Page": getattr(analysis, "page", None),
        "Context": getattr(analysis, "context", None),
        "Disclosure Status": _analysis_status_value(analysis),
        "LLM Analysis": getattr(analysis, "reasoning", "") or "",
        "evidence_segments": list(getattr(analysis, "evidence_segments", None) or []),
        "evidence_sources": list(getattr(analysis, "evidence_sources", None) or []),
        "derived_calculation": getattr(analysis, "derived_calculation", None),
        "improvement_suggestions": list(getattr(analysis, "improvement_suggestions", None) or []),
        "metric_id": getattr(analysis, "metric_id", "") or "",
        "metric_name": getattr(analysis, "metric_name", "") or "",
        "metric_code": getattr(analysis, "metric_code", "") or "",
        "disclosure_status": _analysis_status_value(analysis),
        "reasoning": getattr(analysis, "reasoning", "") or "",
        "value": getattr(analysis, "value", None),
        "year_values": list(getattr(analysis, "year_values", None) or []),
        "selected_year": getattr(analysis, "selected_year", None),
        "value_status": getattr(analysis, "value_status", None),
        "page": getattr(analysis, "page", None),
        "context": getattr(analysis, "context", None),
        "unit": getattr(analysis, "unit", "") or "",
        "category": _canonical_metric_category(getattr(analysis, "category", "") or ""),
        "topic": getattr(analysis, "topic", "") or "",
        "type": getattr(analysis, "type", "") or "",
        "definition": getattr(analysis, "definition", "") or "",
    }


def _default_not_disclosed_overlay(row: dict, index: int) -> Dict[str, Any]:
    """Fallback display fields for a canonical row with no matching analysis object."""
    metric = row.get("Metric") or row.get("metric") or ""
    code = row.get("Code") or row.get("code") or ""
    unit = row.get("Unit") or row.get("unit") or ""
    category = _canonical_metric_category(row.get("Category") or row.get("category") or "")
    topic = row.get("Topic") or row.get("topic") or ""
    type_name = row.get("Type") or row.get("type") or ""
    definition = row.get("definition") or row.get("Definition") or row.get("simple_definition") or ""
    metric_id = f"{code}.{index + 1:02d}" if code else re.sub(r"[^a-z0-9]+", "_", f"{metric}_{topic}_{unit}".lower()).strip("_")
    return {
        "Value": COMPLIANCE_VALUE_NA,
        "Year Values": [],
        "Selected Year": None,
        "Value Status": "none",
        "Page": None,
        "Context": "",
        "Disclosure Status": "not_disclosed",
        "LLM Analysis": "No relevant metric content found",
        "evidence_segments": [],
        "evidence_sources": [],
        "derived_calculation": None,
        "improvement_suggestions": [],
        "metric_id": metric_id,
        "metric_name": metric,
        "metric_code": code,
        "disclosure_status": "not_disclosed",
        "reasoning": "No relevant metric content found",
        "value": COMPLIANCE_VALUE_NA,
        "year_values": [],
        "selected_year": None,
        "value_status": "none",
        "page": None,
        "context": "",
        "unit": unit,
        "category": category,
        "topic": topic,
        "type": type_name,
        "definition": definition,
    }


def _build_sasb_metric_result_rows_from_source(assessment: ComplianceAssessment) -> Optional[List[dict]]:
    """
    Build frontend/storage rows from the canonical backend/data/sasb_metrics JSON.

    Metric retrieval profiles are intentionally NOT used here. They only guide recall/rerank.
    Final model outputs are overlaid onto the original SASB metric rows so Value/Page/Context
    and UI fields stay aligned with backend/data/sasb_metrics.
    """
    fw = str(getattr(assessment, "framework", "") or "").strip().upper()
    if fw != "SASB":
        return None
    source_path = _sasb_metric_source_file_for_scope(getattr(assessment, "semi_industry", None))
    if source_path is None or not source_path.is_file():
        return None
    try:
        source_rows = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to read canonical SASB metrics file for result merge: {e}")
        return None
    if not isinstance(source_rows, list):
        return None

    exact: Dict[tuple[str, str], List[DisclosureAnalysis]] = {}
    by_code: Dict[str, List[DisclosureAnalysis]] = {}
    by_metric: Dict[str, List[DisclosureAnalysis]] = {}
    for analysis in assessment.metric_analyses or []:
        code_key = _normalize_lookup_code(getattr(analysis, "metric_code", ""))
        metric_key = _normalize_lookup_text(getattr(analysis, "metric_name", ""))
        exact.setdefault((code_key, metric_key), []).append(analysis)
        if code_key:
            by_code.setdefault(code_key, []).append(analysis)
        if metric_key:
            by_metric.setdefault(metric_key, []).append(analysis)

    out: List[dict] = []
    used_ids: set[int] = set()
    for idx, raw_row in enumerate(source_rows):
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        code_key = _normalize_lookup_code(row.get("Code") or row.get("code") or "")
        metric_key = _normalize_lookup_text(row.get("Metric") or row.get("metric") or "")
        analysis = None

        candidates = exact.get((code_key, metric_key)) or []
        while candidates:
            cand = candidates.pop(0)
            if id(cand) not in used_ids:
                analysis = cand
                break

        if analysis is None and code_key:
            candidates = by_code.get(code_key) or []
            while candidates:
                cand = candidates.pop(0)
                if id(cand) not in used_ids:
                    analysis = cand
                    break

        if analysis is None and metric_key:
            candidates = by_metric.get(metric_key) or []
            while candidates:
                cand = candidates.pop(0)
                if id(cand) not in used_ids:
                    analysis = cand
                    break

        if analysis is not None:
            used_ids.add(id(analysis))
            row.update(_metric_result_overlay_from_analysis(analysis))
        else:
            row.update(_default_not_disclosed_overlay(row, idx))
        out.append(row)
    return out


def _build_compliance_assessment_json(
    assessment: ComplianceAssessment,
    report_path_str: str,
    result_filename: str,
) -> dict:
    """Root + metric_analyses in the same shape as SASB compliance JSON."""
    fw = getattr(assessment, "framework", None)
    sasb_rows = _build_sasb_metric_result_rows_from_source(assessment)
    if sasb_rows is not None:
        metric_rows = sasb_rows
    else:
        metric_rows = [
            _metric_row_from_disclosure_analysis(a) for a in assessment.metric_analyses
        ]
        for row in metric_rows:
            _normalize_non_sasb_compliance_metric_row(row, fw)
    _apply_partial_disclosure_json_rules(metric_rows)
    return {
        "report_id": assessment.report_id,
        "assessment_date": _assessment_date_sydney_iso(assessment.assessment_date),
        "filename": result_filename,
        "total_metrics": assessment.total_metrics_analyzed,
        "overall_score": assessment.overall_compliance_score,
        "total_metrics_analyzed": assessment.total_metrics_analyzed,
        "overall_compliance_score": assessment.overall_compliance_score,
        "report_file_path": report_path_str,
        "framework": fw,
        "metric_result_source": "backend/data/sasb_metrics" if sasb_rows is not None else "assessment",
        "retrieval_profile_source": "backend/data/sasb_metric_profiles" if str(fw or "").upper() == "SASB" else "",
        "disclosure_summary": {
            "fully_disclosed": assessment.disclosure_summary.get(
                DisclosureStatus.FULLY_DISCLOSED, 0
            ),
            "partially_disclosed": assessment.disclosure_summary.get(
                DisclosureStatus.PARTIALLY_DISCLOSED, 0
            ),
            "not_disclosed": assessment.disclosure_summary.get(
                DisclosureStatus.NOT_DISCLOSED, 0
            ),
        },
        "metric_analyses": metric_rows,
        "sasb_metric_rows": metric_rows if sasb_rows is not None else [],
    }


def _resolve_compliance_json_path(
    assessment_dir: Path,
    legacy_dir: Path,
    file_id: str,
    stem: Optional[str] = None,
) -> Optional[Path]:
    """Locate compliance JSON for a file_id (exact names first, then glob for Subindustry_fileid_compliance.json)."""
    jp = _json_path_from_manifest(assessment_dir, file_id, None)
    if jp is not None and jp.is_file():
        return jp
    candidates: list[Path] = [
        assessment_dir / f"{file_id}_compliance.json",
        legacy_dir / f"{file_id}_compliance.json",
    ]
    if stem:
        candidates.append(assessment_dir / f"{stem}_compliance.json")
        candidates.append(legacy_dir / f"{stem}_compliance.json")
    for p in candidates:
        if p.exists():
            return p
    for d in (assessment_dir, legacy_dir):
        if not d.exists():
            continue
        for p in d.glob(f"*{file_id}*_compliance.json"):
            if p.is_file():
                return p
    return None


def _prepare_metrics_for_retrieval(
    processor: MetricProcessor,
    metrics: MetricCollection,
) -> MetricCollection:
    """Ensure framework metrics are retrieval-ready before running dual-channel recall."""
    if not getattr(metrics, "metrics", None):
        return metrics
    skip_profiled = str(
        os.getenv("REPORT_SKIP_PROFILED_METRIC_EXPANSION", "true") or "true"
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    if skip_profiled:
        profiled = [metric for metric in metrics.metrics if find_metric_profile(metric) is not None]
        if len(profiled) == len(metrics.metrics):
            logger.info(
                f"Skipping metric LLM semantic expansion: all {len(profiled)} metrics "
                "have generated retrieval profiles"
            )
            return metrics
    return processor.process_metric_collection(metrics)


def _get_chat_history_path(file_id: str) -> Path:
    """Get path for chat history JSON file"""
    backend_dir = Path(__file__).parent.parent.parent
    history_dir = backend_dir / "outputs" / "chat_histories"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / f"{file_id}_chat.json"


def _load_chat_history(file_id: str) -> list:
    """Load chat history from disk"""
    history_path = _get_chat_history_path(file_id)
    if history_path.exists():
        try:
            with open(history_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading chat history: {e}")
    return []


def _save_chat_history(file_id: str, history: list):
    """Save chat history to disk"""
    history_path = _get_chat_history_path(file_id)
    try:
        with open(history_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving chat history: {e}")


def _load_specific_report_context(file_id: str):
    """Load report content (extracted markdown) and compliance assessment for a given file_id.

    - Robust to process restarts (in-memory context is empty)
    - Backward compatible with older *_compliance.json schemas
    - Best-effort: chat can still work with assessment only
    """
    report_content_obj = None
    assessment_obj = None

    # Resolve file metadata early (needed for robust path resolution)
    file_info = None
    try:
        file_info = file_manager.get_file_info(file_id)
    except Exception:
        file_info = None

    safe_filename = str((file_info or {}).get("safe_filename") or "")
    stem = Path(safe_filename).stem if safe_filename else ""

    # Fast path: reuse in-memory report if it appears to belong to this file.
    # NOTE: ReportContent.document_id is not guaranteed to equal file_id (it can be doc_<stem>_<hash>),
    # so we also match by stem.
    try:
        cr = system_components.get("current_report")
        if cr is not None:
            docid = str(getattr(cr, "document_id", "") or "")
            if docid == file_id or (stem and stem in docid):
                report_content_obj = cr
    except Exception:
        pass

    # Canonical output roots (consistent with FileManager)
    reports_root = Path(file_manager.reports_dir)
    assessment_dir = Path(file_manager.compliance_outputs)
    markdown_outputs_dir = Path(file_manager.markdown_outputs)
    legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"

    def _parse_status(v) -> DisclosureStatus:
        if v is None:
            return DisclosureStatus.NOT_DISCLOSED
        s = str(v).strip().lower()
        s = s.replace("-", "_").replace(" ", "_")
        if "not_clear" in s or "unclear" in s:
            return DisclosureStatus.PARTIALLY_DISCLOSED
        if "not" in s:
            return DisclosureStatus.NOT_DISCLOSED
        if s in {"partially_disclosed", "partial", "partly"} or "partial" in s:
            return DisclosureStatus.PARTIALLY_DISCLOSED
        if s in {"fully_disclosed", "fully", "full", "complete", "disclosed"} or "fully" in s:
            return DisclosureStatus.FULLY_DISCLOSED
        return DisclosureStatus.NOT_DISCLOSED

    def _summary_from_metrics(metrics):
        summary = {
            DisclosureStatus.FULLY_DISCLOSED: 0,
            DisclosureStatus.PARTIALLY_DISCLOSED: 0,
            DisclosureStatus.NOT_DISCLOSED: 0,
        }
        for m in metrics:
            try:
                summary[m.disclosure_status] = summary.get(m.disclosure_status, 0) + 1
            except Exception:
                pass
        return summary

    # 1) Load compliance assessment JSON for this file_id (supports Subindustry_fileid_compliance.json naming)
    assessment_json_path = _resolve_compliance_json_path(
        assessment_dir, legacy_outputs_dir, file_id, stem
    )

    if assessment_json_path is not None and assessment_json_path.exists():
        try:
            assessment_data = json.loads(assessment_json_path.read_text(encoding="utf-8"))

            # metric_analyses (preferred key)
            raw_metrics = assessment_data.get("metric_analyses") or []
            metric_analyses = []
            for item in raw_metrics:
                if not isinstance(item, dict):
                    logger.warning(f"Invalid metric analysis item (not dict) in {assessment_json_path}: {type(item)}")
                    continue
                try:
                    d = dict(item)
                    # Support legacy and canonical status keys
                    status_raw = (
                        d.get("disclosure_status")
                        or d.get("Disclosure Status")
                        or d.get("Model Disclosure Status")
                        or d.get("status")
                    )
                    d["disclosure_status"] = _parse_status(status_raw)
                    d.pop("status", None)
                    # Build from explicit kwargs to avoid KeyError for missing "type"/"category"/"topic"
                    metric_analyses.append(DisclosureAnalysis(
                        metric_id=d.get("metric_id", d.get("metric_code", d.get("Code", ""))),
                        metric_name=d.get("metric_name", d.get("Metric", "")),
                        metric_code=d.get("metric_code", d.get("Code", d.get("metric_id", ""))),
                        disclosure_status=d["disclosure_status"],
                        reasoning=d.get("reasoning", d.get("LLM Analysis", d.get("Reasoning", d.get("Analysis", "")))),
                        evidence_segments=d.get("evidence_segments", []) or [],
                        evidence_sources=d.get("evidence_sources", []) or [],
                        derived_calculation=d.get("derived_calculation"),
                        improvement_suggestions=d.get("improvement_suggestions", []) or [],
                        category=d.get("category", d.get("Category", "")),
                        topic=d.get("topic", d.get("Topic", "")),
                        unit=d.get("unit", d.get("Unit", "")) or "",
                        type=d.get("type", d.get("Type", "")),
                        definition=d.get("definition", d.get("Definition", "")),
                        value=d.get("value", d.get("Value")),
                        year_values=d.get("year_values", d.get("Year Values", [])) or [],
                        selected_year=d.get("selected_year", d.get("Selected Year")),
                        context=d.get("context", d.get("Context")),
                        page=d.get("page", d.get("Page")),
                    ))
                except Exception as e:
                    logger.warning(f"Invalid metric analysis item in {assessment_json_path}: {e}")
                    continue

            # Backward compatible keys
            total_metrics = (
                assessment_data.get("total_metrics_analyzed")
                or assessment_data.get("total_metrics")
                or len(metric_analyses)
            )
            overall_score = (
                assessment_data.get("overall_compliance_score")
                or assessment_data.get("overall_score")
                or 0.0
            )
            framework = assessment_data.get("framework") or "SASB"

            # disclosure_summary: accept either enum-keyed, string-keyed, or missing
            summary_raw = assessment_data.get("disclosure_summary")
            disclosure_summary = None
            if isinstance(summary_raw, dict) and summary_raw:
                tmp = {}
                for k, v in summary_raw.items():
                    try:
                        tmp[_parse_status(k)] = int(v)
                    except Exception:
                        continue
                if tmp:
                    disclosure_summary = tmp
            if not disclosure_summary:
                disclosure_summary = _summary_from_metrics(metric_analyses)

            # report_file_path
            report_file_path = assessment_data.get("report_file_path")
            if not report_file_path and isinstance(file_info, dict):
                report_file_path = file_info.get("file_path")
            report_file_path = str(report_file_path or "")

            assessment_obj = ComplianceAssessment(
                report_id=assessment_data.get("report_id") or file_id,
                framework=framework,
                total_metrics_analyzed=int(total_metrics or 0),
                overall_compliance_score=float(overall_score or 0.0),
                disclosure_summary=disclosure_summary,
                metric_analyses=metric_analyses,
                report_file_path=report_file_path,
            )
            logger.info(f"Loaded specific assessment for {file_id}: {assessment_obj.total_metrics_analyzed} metrics")
        except Exception as e:
            logger.warning(f"Failed to load assessment JSON for {file_id}: {e}")

    # 2) Load report content + embeddings for chat retrieval
    # Priority:
    #   (a) persisted artifacts (segments + embeddings matrix)  -> fastest / best quality
    #   (b) extracted markdown -> parse into segments -> compute embeddings once -> persist
    #   (c) assessment-only
    try:
        # (a) artifacts
        if report_content_obj is None:
            art = file_manager.load_report_artifacts(file_id)
            if art:
                pdf_path_str = (file_info or {}).get("file_path") if isinstance(file_info, dict) else ""
                segments = art.get("segments") or []
                markdown_text = "\n\n".join([getattr(s, "content", "") for s in segments])
                document_content = DocumentContent(
                    document_id=file_id,
                    file_path=str(pdf_path_str or ""),
                    segments=segments,
                    markdown_content=markdown_text,
                )
                report_content_obj = ReportContent(
                    document_id=file_id,
                    document_content=document_content,
                    embeddings=[],
                )
                # Attach fast retrieval cache (avoid converting large matrix to python lists)
                setattr(report_content_obj, "_embedding_matrix", art.get("embedding_matrix"))
                setattr(report_content_obj, "_embedding_segment_ids", art.get("embedding_segment_ids"))
                logger.info(f"Loaded persisted segments+embeddings for {file_id} from {art.get('embeddings_path')}")

        # (b) no artifacts: load markdown -> parse -> compute embeddings -> persist
        if report_content_obj is None:
            pdf_path_str = (file_info or {}).get("file_path") if isinstance(file_info, dict) else None
            candidates = []

            if pdf_path_str:
                pdf_path = Path(pdf_path_str)
                stem = pdf_path.stem
                candidates.append(pdf_path.parent / f"{stem}_extracted.md")
                candidates.append(pdf_path.parent / f"{stem}.md")
                candidates.append(reports_root / "pending" / f"{stem}_extracted.md")
                candidates.append(reports_root / "processed" / f"{stem}_extracted.md")
                candidates.append(reports_root / "failed" / f"{stem}_extracted.md")
            candidates.append(markdown_outputs_dir / f"{file_id}.md")
            if legacy_outputs_dir.exists():
                candidates.append(legacy_outputs_dir / "markdown" / f"{file_id}.md")

            markdown_text = None
            for p in candidates:
                if p and p.exists():
                    markdown_text = p.read_text(encoding="utf-8", errors="ignore")
                    break

            if markdown_text:
                import re
                seg_pat = re.compile(r"\*\*(?P<sid>[A-Za-z0-9_:-]+)\*\*\s*\n\n(?P<body>.*?)(?:\n\n---\n|\Z)", re.DOTALL)
                segments: List[TextSegment] = []
                for m in seg_pat.finditer(markdown_text):
                    sid = m.group("sid").strip()
                    body = (m.group("body") or "").strip()
                    if not body:
                        continue
                    # Best-effort page from "P###" prefix
                    page = 1
                    mm = re.match(r"P(\d{3})_", sid)
                    if mm:
                        try:
                            page = int(mm.group(1))
                        except Exception:
                            page = 1
                    segments.append(TextSegment(segment_id=sid, content=body, page_number=page, position_y=0.0))

                if not segments:
                    # fallback: one big segment
                    segments = [TextSegment(segment_id=f"{file_id}:md", content=markdown_text, page_number=1, position_y=0.0)]

                document_content = DocumentContent(
                    document_id=file_id,
                    file_path=str(pdf_path_str or ""),
                    segments=segments,
                    markdown_content=markdown_text,
                )

                # Compute embeddings once (sync) then persist. This guarantees semantic retrieval quality.
                try:
                    encoder = system_components.get("report_encoder")
                    if encoder is not None:
                        # embed_document() returns a ReportContent (NOT a list of SegmentEmbedding).
                        embedded_report = encoder.embedder.embed_document(document_content)

                        tmp_report = ReportContent(
                            document_id=file_id,
                            document_content=document_content,
                            embeddings=getattr(embedded_report, "embeddings", []),
                        )
                        embedded_matrix = getattr(embedded_report, "_embedding_matrix", None)
                        embedded_ids = getattr(embedded_report, "_embedding_segment_ids", None)
                        if embedded_matrix is not None and embedded_ids is not None:
                            object.__setattr__(tmp_report, "_embedding_matrix", embedded_matrix)
                            object.__setattr__(tmp_report, "_embedding_segment_ids", embedded_ids)
                        metric_corpus = getattr(
                            embedded_report,
                            "_metric_retrieval_corpus",
                            None,
                        )
                        if metric_corpus is not None:
                            object.__setattr__(
                                tmp_report,
                                "_metric_retrieval_corpus",
                                metric_corpus,
                            )
                        file_manager.save_report_artifacts(file_id, tmp_report)
                        report_content_obj = tmp_report
                        # Also attach matrix cache for faster search
                        art2 = file_manager.load_report_artifacts(file_id)
                        if art2:
                            setattr(report_content_obj, "_embedding_matrix", art2.get("embedding_matrix"))
                            setattr(report_content_obj, "_embedding_segment_ids", art2.get("embedding_segment_ids"))
                        logger.info(f"Computed+persisted embeddings for {file_id} from extracted markdown")
                except Exception as e:
                    logger.warning(f"Failed to compute embeddings for {file_id} (will fallback to keyword): {e}")
                    report_content_obj = ReportContent(document_id=file_id, document_content=document_content, embeddings=[])

            else:
                logger.warning(f"No extracted markdown found for file_id={file_id}; searched {len(candidates)} locations")
    except Exception as e:
        logger.warning(f"Failed to load report content for file_id={file_id}: {e}")

    # Return order matches call sites: (assessment, report_content)
    return assessment_obj, report_content_obj


def _load_latest_assessment_for_chat():
    """
    为聊天机器人加载最新的评估数据（从JSON文件）
    """
    try:
        # 获取最新的JSON评估数据（优先使用 uploads/outputs/compliance_reports/）
        canonical_dir = Path(file_manager.compliance_outputs)
        legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # legacy backend/outputs
        json_files = list(canonical_dir.glob("*_compliance.json"))
        if legacy_dir.exists():
            json_files.extend(list(legacy_dir.glob("*_compliance.json")))

        if not json_files:
            logger.warning("No assessment JSON files found")
            return None

        # 使用最新的JSON文件
        json_file = sorted(json_files, key=lambda x: x.stat().st_mtime)[-1]
        logger.info(f"Loading assessment from JSON: {json_file}")

        with open(json_file, 'r', encoding='utf-8') as f:
            assessment_data = json.load(f)

        # 从JSON重建ComplianceAssessment对象
        from ..models import ComplianceAssessment, DisclosureAnalysis, DisclosureStatus

        # 创建metric analyses from JSON
        metric_analyses = []
        for item in assessment_data["metric_analyses"]:
            # Map string status to enum (support canonical and legacy keys)
            status_raw = (
                item.get("disclosure_status")
                or item.get("Disclosure Status")
                or item.get("Model Disclosure Status")
                or item.get("status")
            )
            try:
                status = _parse_status(status_raw)
            except Exception:
                status = DisclosureStatus.NOT_DISCLOSED

            analysis = DisclosureAnalysis(
                metric_id=item.get("metric_id", item.get("metric_code", item.get("Code", ""))),
                metric_name=item.get("metric_name", item.get("Metric", "")),
                metric_code=item.get("metric_code", item.get("Code", item.get("metric_id", ""))),
                disclosure_status=status,
                reasoning=item.get("reasoning", item.get("LLM Analysis", item.get("Reasoning", item.get("Analysis", "")))),
                evidence_segments=item.get("evidence_segments", []),
                evidence_sources=item.get("evidence_sources", []),
                derived_calculation=item.get("derived_calculation"),
                improvement_suggestions=item.get("improvement_suggestions", []),
                category=item.get("category", item.get("Category", "")),
                topic=item.get("topic", item.get("Topic", "")),
                unit=item.get("unit", item.get("Unit", "")),
                type=item.get("type", item.get("Type", "")),
                definition=item.get("definition", item.get("Definition", "")),
                value=item.get("value", item.get("Value")),
                year_values=item.get("year_values", item.get("Year Values", [])) or [],
                selected_year=item.get("selected_year", item.get("Selected Year")),
                context=item.get("context", item.get("Context")),
                page=item.get("page", item.get("Page"))
            )
            metric_analyses.append(analysis)

        # 创建ComplianceAssessment对象 (使用.get()提供默认值以兼容旧JSON)
        assessment = ComplianceAssessment(
            report_id=assessment_data.get("report_id", "unknown"),
            total_metrics_analyzed=assessment_data.get("total_metrics_analyzed", len(metric_analyses)),
            overall_compliance_score=assessment_data.get("overall_compliance_score", 0.0),
            disclosure_summary=assessment_data.get("disclosure_summary", {}),
            metric_analyses=metric_analyses,
            report_file_path=assessment_data.get("report_file_path", "")
        )

        return assessment

    except Exception as e:
        logger.error(f"Failed to load assessment JSON for chat: {e}")
        return None


def _load_report_content_for_chat():
    """
    加载原始报告内容用于聊天检索
    """
    try:
        from ..models import ReportContent, ReportSegment
        
        # 查找提取的markdown文件（通常随 PDF 一起存放在 uploads/reports/**）
        reports_dir = Path(file_manager.reports_dir)
        markdown_files = list(reports_dir.glob("**/*_extracted.md"))
        
        if not markdown_files:
            logger.warning("No extracted markdown files found for chat")
            return None
            
        # 使用最新的markdown文件
        markdown_file = sorted(markdown_files, key=lambda x: x.stat().st_mtime)[-1]
        logger.info(f"Loading report content from: {markdown_file}")
        
        with open(markdown_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 简单分段处理 - 按段落分割
        segments = []
        paragraphs = content.split('\n\n')
        
        for i, paragraph in enumerate(paragraphs):
            if paragraph.strip():
                segment = ReportSegment(
                    segment_id=f"seg_{i}",
                    content=paragraph.strip(),
                    page_number=1  # 简化处理
                )
                segments.append(segment)
        
        # 创建DocumentContent对象
        from ..models import DocumentContent, SegmentEmbedding, TextSegment
        
        # 创建TextSegment列表
        text_segments = []
        for segment in segments[:500]:  # 限制段落数量
            text_segment = TextSegment(
                segment_id=segment.segment_id,
                content=segment.content,
                page_number=segment.page_number,
                position_y=getattr(segment, 'position_y', 0.0)  # 使用默认值兼容旧数据
            )
            text_segments.append(text_segment)
        
        document_content = DocumentContent(
            document_id=markdown_file.stem,
            file_path=str(markdown_file),
            segments=text_segments,
            markdown_content=content
        )
        
        # 创建空的嵌入列表（简化处理）
        embeddings = []
        
        # 创建ReportContent对象
        report_content = ReportContent(
            document_id=markdown_file.stem,
            document_content=document_content,
            embeddings=embeddings
        )
        
        logger.info(f"Loaded {len(report_content.document_content.segments)} segments for chat")
        return report_content
        
    except Exception as e:
        logger.error(f"Failed to load report content for chat: {e}")
        return None


def _create_enhanced_knowledge_base(assessment, report_content):
    """
    创建增强的知识库，结合评估结果和原始报告内容
    """
    try:
        from ..models import ReportSegment
        
        if not assessment:
            return report_content
            
        # 创建评估结果的文档片段
        assessment_segments = []
        
        # 1. 总体评估信息
        summary_text = f"""
ESG合规评估总结:
- 报告ID: {assessment.report_id}
- 分析指标总数: {assessment.total_metrics_analyzed}
- 整体合规分数: {assessment.overall_compliance_score:.1%}
- 已披露指标: {assessment.disclosure_summary.get('fully_disclosed', 0)}个
- 部分披露指标: {assessment.disclosure_summary.get('partially_disclosed', 0)}个  
- 未披露指标: {assessment.disclosure_summary.get('not_disclosed', 0)}个
"""
        
        summary_segment = ReportSegment(
            segment_id="assessment_summary",
            content=summary_text,
            page_number=0,
            embedding=None
        )
        assessment_segments.append(summary_segment)
        
        # 2. 具体指标分析
        if hasattr(assessment, 'metric_analyses') and assessment.metric_analyses:
            for i, analysis in enumerate(assessment.metric_analyses):
                # Validate required fields exist
                if not hasattr(analysis, 'metric_id') or not hasattr(analysis, 'metric_name'):
                    logger.warning(f"Skipping metric analysis {i} - missing required fields")
                    continue
                if not hasattr(analysis, 'disclosure_status') or not hasattr(analysis, 'reasoning'):
                    logger.warning(f"Skipping metric {analysis.metric_id} - missing disclosure_status or reasoning")
                    continue

                metric_text = f"""
指标分析 {i+1}:
- 指标ID: {analysis.metric_id}
- 指标名称: {analysis.metric_name}
- 披露状态: {analysis.disclosure_status}
- 分析理由: {analysis.reasoning}
"""

                metric_segment = ReportSegment(
                    segment_id=f"metric_analysis_{i}",
                    content=metric_text,
                    page_number=0,
                    embedding=None
                )
                assessment_segments.append(metric_segment)
        
        # 合并评估段落和原始报告段落
        from ..models import ReportContent, DocumentContent, TextSegment
        
        if report_content:
            # 将assessment_segments转换为TextSegment
            text_segments = []
            for seg in assessment_segments:
                text_seg = TextSegment(
                    segment_id=seg.segment_id,
                    content=seg.content,
                    page_number=seg.page_number,
                    position_y=0.0
                )
                text_segments.append(text_seg)
            
            # 合并原始报告的segments
            if hasattr(report_content, 'document_content') and report_content.document_content:
                text_segments.extend(report_content.document_content.segments)
            
            # 创建DocumentContent
            original_file_path = ""
            original_markdown = ""
            if hasattr(report_content, 'document_content') and report_content.document_content:
                original_file_path = report_content.document_content.file_path
                original_markdown = getattr(report_content.document_content, 'markdown_content', '')
            
            document_content = DocumentContent(
                document_id=f"enhanced_{assessment.report_id}",
                file_path=original_file_path,
                segments=text_segments,
                markdown_content=original_markdown
            )
            
            # 创建ReportContent
            enhanced_content = ReportContent(
                document_id=f"enhanced_{assessment.report_id}",
                document_content=document_content,
                embeddings=report_content.embeddings if hasattr(report_content, 'embeddings') else []
            )
            source_matrix = getattr(report_content, "_embedding_matrix", None)
            source_embedding_ids = getattr(
                report_content,
                "_embedding_segment_ids",
                None,
            )
            if source_matrix is not None and source_embedding_ids is not None:
                # Assessment-only segments have no document embeddings. The
                # chatbot filters this matrix by segment ID, so retaining the
                # original report rows is both valid and avoids a legacy-list
                # reconstruction (new reports intentionally keep that list empty).
                object.__setattr__(enhanced_content, "_embedding_matrix", source_matrix)
                object.__setattr__(
                    enhanced_content,
                    "_embedding_segment_ids",
                    list(source_embedding_ids),
                )
        else:
            # 只有评估数据，没有原始报告
            text_segments = []
            for seg in assessment_segments:
                text_seg = TextSegment(
                    segment_id=seg.segment_id,
                    content=seg.content,
                    page_number=seg.page_number,
                    position_y=0.0
                )
                text_segments.append(text_seg)
            
            document_content = DocumentContent(
                document_id=f"assessment_{assessment.report_id}",
                file_path="",
                segments=text_segments,
                markdown_content=""
            )
            
            enhanced_content = ReportContent(
                document_id=f"enhanced_{assessment.report_id}",
                document_content=document_content,
                embeddings=[]
            )
        
        logger.info(f"Created enhanced knowledge base with {len(assessment_segments)} assessment segments and {len(report_content.document_content.segments) if report_content and hasattr(report_content, 'document_content') and report_content.document_content else 0} report segments")
        return enhanced_content
        
    except Exception as e:
        logger.error(f"Failed to create enhanced knowledge base: {e}")
        return report_content


def _normalize_assessment_payload(payload: dict) -> dict:
    """Ensure assessment payloads always contain page/value/unit/context fields for frontend stability.

    - Does NOT guess missing values; only fills missing keys with null/empty defaults.
    - Keeps backward compatibility with older *_compliance.json schemas.
    """
    if not isinstance(payload, dict):
        return {
            "report_id": "unknown",
            "assessment_date": datetime.now().isoformat(),
            "total_metrics": 0,
            "overall_score": 0,
            "disclosure_summary": {},
            "metric_analyses": [],
        }

    mas = payload.get("metric_analyses")
    if not isinstance(mas, list):
        mas = []

    norm = []
    for a in mas:
        if not isinstance(a, dict):
            continue
        # Key normalization (ensure exact output fields and legacy fields both exist)
        if "value" not in a:
            if "Value" in a:
                a["value"] = a.get("Value")
            elif "data" in a:
                a["value"] = a.get("data")
        if "page" not in a and "Page" in a:
            a["page"] = a.get("Page")
        if "year_values" not in a and "Year Values" in a:
            a["year_values"] = a.get("Year Values")
        if "selected_year" not in a and "Selected Year" in a:
            a["selected_year"] = a.get("Selected Year")
        a.setdefault("page", None)
        a.setdefault("value", None)
        raw_year_values = a.get("year_values")
        normalized_year_values: List[Dict[str, Any]] = []
        if isinstance(raw_year_values, list):
            seen_year_values = set()
            for raw_year_value in raw_year_values:
                if not isinstance(raw_year_value, dict):
                    continue
                try:
                    year = int(raw_year_value.get("year"))
                except (TypeError, ValueError):
                    continue
                value = raw_year_value.get("value")
                if not 1900 <= year <= 2100 or isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                unit_key = str(raw_year_value.get("unit") or raw_year_value.get("raw_unit") or "").strip().lower()
                key = (year, float(value), unit_key)
                if key in seen_year_values:
                    continue
                seen_year_values.add(key)
                item = dict(raw_year_value)
                item["year"] = year
                normalized_year_values.append(item)
        a["year_values"] = sorted(normalized_year_values, key=lambda item: int(item["year"]))
        try:
            selected_year = int(a.get("selected_year")) if a.get("selected_year") is not None else None
        except (TypeError, ValueError):
            selected_year = None
        a["selected_year"] = selected_year if selected_year is not None and 1900 <= selected_year <= 2100 else None
        a.setdefault("unit", a.get("Unit"))
        a.setdefault(
            "context",
            a.get("context")
            or a.get("Context")
            or a.get("specific_data_found")
            or a.get("specificDataFound")
            or a.get("evidence_text")
            or a.get("evidenceText")
            or a.get("evidence")
            or "",
        )
        a.setdefault("reasoning", a.get("LLM Analysis") or a.get("Reasoning") or a.get("Analysis") or "")
        a.setdefault("disclosure_status", a.get("Disclosure Status") or a.get("Model Disclosure Status") or a.get("status") or "")
        a.setdefault("evidence_segments", [])
        a.setdefault("evidence_sources", [])
        a.setdefault("derived_calculation", None)
        a.setdefault("improvement_suggestions", [])
        a.setdefault("metric_name", a.get("Metric") or a.get("metric") or "")
        a.setdefault("metric_code", a.get("Code") or a.get("code") or a.get("metric_id") or "")
        a.setdefault("type", a.get("Type") or a.get("type") or "")
        a.setdefault("category", a.get("Category") or a.get("category") or "")
        a["category"] = _canonical_metric_category(a.get("category") or a.get("Category") or "")
        a.setdefault("topic", a.get("Topic") or a.get("topic") or "")
        a.setdefault("definition", a.get("Definition") or a.get("definition") or "")

        a["Metric"] = a.get("Metric") or a.get("metric_name") or ""
        a["Category"] = _canonical_metric_category(a.get("Category") or a.get("category") or "")
        a["Unit"] = a.get("Unit") or a.get("unit") or ""
        a["Code"] = a.get("Code") or a.get("metric_code") or a.get("metric_id") or ""
        a["Topic"] = a.get("Topic") or a.get("topic") or ""
        a["Type"] = a.get("Type") or a.get("type") or ""
        a["Definition"] = a.get("Definition") or a.get("definition") or ""
        a["Value"] = a.get("Value") if "Value" in a else a.get("value")
        a["Year Values"] = a.get("year_values") or []
        a["Selected Year"] = a.get("selected_year")
        a["Page"] = a.get("Page") if "Page" in a else a.get("page")
        a["Context"] = a.get("Context") or a.get("context") or ""
        a["Disclosure Status"] = a.get("Disclosure Status") or a.get("disclosure_status") or a.get("Model Disclosure Status") or ""
        a["LLM Analysis"] = a.get("LLM Analysis") or a.get("reasoning") or ""

        # --- UI/Output rules for disclosure statuses ---
        # 1) not_disclosed -> do not output any page/value
        # 2) partially_disclosed -> value should be a textual reason (no concrete numbers)
        status_raw = str(a.get("disclosure_status", "") or "").strip().lower()
        # Normalize common legacy variants
        if "partial" in status_raw:
            status_norm = "partially_disclosed"
        elif "not" in status_raw:
            status_norm = "not_disclosed"
        elif "full" in status_raw:
            status_norm = "fully_disclosed"
        else:
            status_norm = status_raw

        def _payload_value_is_numeric(v) -> bool:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return True
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return False
                s = s.replace(",", "")
                if s.endswith("%"):
                    s = s[:-1].strip()
                try:
                    float(s)
                    return True
                except Exception:
                    return False
            return False

        if status_norm == "not_disclosed":
            a["page"] = None
            a["value"] = COMPLIANCE_VALUE_NA
            a["year_values"] = []
            a["selected_year"] = None
        elif status_norm in ("fully_disclosed", "partially_disclosed"):
            if not _payload_value_is_numeric(a.get("value")):
                a["value"] = COMPLIANCE_VALUE_NA

        a["disclosure_status"] = status_norm or a.get("disclosure_status") or a.get("Disclosure Status") or ""
        a["Value"] = a.get("value")
        a["Year Values"] = a.get("year_values") or []
        a["Selected Year"] = a.get("selected_year")
        a["Page"] = a.get("page")
        a["Context"] = a.get("context") or ""
        a["Disclosure Status"] = a.get("disclosure_status") or a.get("Disclosure Status") or ""
        a["LLM Analysis"] = a.get("reasoning") or a.get("LLM Analysis") or ""
        norm.append(a)

    payload["metric_analyses"] = norm
    return payload


def _compact_assessment_payload(payload: dict) -> dict:
    """Project an assessment to the fields used by the single-report UI.

    Persisted SASB assessments intentionally contain legacy/canonical duplicate
    fields and a second ``sasb_metric_rows`` copy for exports.  Returning all of
    that data makes the interactive report endpoint several times larger than
    the page needs.  This projection leaves the default API response untouched
    and is only used when the client explicitly requests ``compact=true``.
    """
    if not isinstance(payload, dict):
        return {"metric_analyses": []}

    top_level_fields = (
        "report_id",
        "assessment_date",
        "filename",
        "total_metrics",
        "overall_score",
        "total_metrics_analyzed",
        "overall_compliance_score",
        "framework",
        "disclosure_summary",
        "status",
        "message",
        "scope_key",
        "requested_year",
    )
    metric_fields = (
        "metric_id",
        "metric_name",
        "metric_code",
        "disclosure_status",
        "reasoning",
        "unit",
        "category",
        "topic",
        "type",
        "value",
        "page",
        "context",
        "simple_definition",
        "definition",
        "selected_year",
        "value_status",
        "year_selection_status",
    )
    evidence_fields = (
        "source_type",
        "data_page",
        "segment_id",
        "source_report_id",
        "source_report_name",
        "source_report_year",
        "link_source_page",
        "target_page",
        "link_source_segment_id",
        "anchor_text",
        "asset_id",
        "evidence_type",
        "caption",
        "confidence",
        "chart_data",
        "bbox",
        "review_status",
        "structure_confidence",
        "ocr_confidence",
        "header_path",
        "rowspan",
        "colspan",
        "parse_pass",
    )

    compact = {
        key: payload.get(key)
        for key in top_level_fields
        if key in payload
    }
    compact_metrics: List[Dict[str, Any]] = []
    for raw_metric in payload.get("metric_analyses") or []:
        if not isinstance(raw_metric, dict):
            continue
        metric = {
            key: raw_metric.get(key)
            for key in metric_fields
            if key in raw_metric
        }
        evidence_sources: List[Dict[str, Any]] = []
        for raw_source in raw_metric.get("evidence_sources") or []:
            if not isinstance(raw_source, dict):
                continue
            source = {
                key: raw_source.get(key)
                for key in evidence_fields
                if key in raw_source
            }
            conflicts = raw_source.get("conflicts")
            if isinstance(conflicts, list) and conflicts:
                # The report table only needs the count, not the potentially
                # large conflict payloads.
                source["conflicts"] = [{} for _ in conflicts]
            if source:
                evidence_sources.append(source)
        metric["evidence_sources"] = evidence_sources
        compact_metrics.append(metric)

    compact["metric_analyses"] = compact_metrics
    compact["response_view"] = "compact"
    return compact


def _apply_assessment_year_selection(payload: dict, target_year: Optional[int]) -> dict:
    """Project one requested year into legacy scalar fields without losing year_values."""
    if target_year is None:
        return payload
    try:
        year = int(target_year)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="year must be an integer between 1900 and 2100")
    if not 1900 <= year <= 2100:
        raise HTTPException(status_code=422, detail="year must be between 1900 and 2100")

    rows: List[Dict[str, Any]] = []
    for key in ("metric_analyses", "sasb_metric_rows"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)

    for row in rows:
        if not isinstance(row, dict):
            continue
        row["selected_year"] = year
        row["Selected Year"] = year
        status = str(row.get("disclosure_status") or "").strip().lower()
        if status == "not_disclosed":
            row["year_selection_status"] = "not_disclosed"
            continue

        matches = [
            item
            for item in (row.get("year_values") or [])
            if isinstance(item, dict) and item.get("year") == year
        ]
        distinct = {
            (
                float(item["value"]),
                str(item.get("unit") or item.get("raw_unit") or "").strip().lower(),
            )
            for item in matches
            if isinstance(item.get("value"), (int, float)) and not isinstance(item.get("value"), bool)
        }
        if len(distinct) == 1 and matches:
            selected = matches[0]
            row["value"] = selected.get("value")
            row["page"] = selected.get("page")
            if selected.get("context"):
                row["context"] = selected.get("context")
            row["year_selection_status"] = "selected"
        elif not matches:
            row["value"] = COMPLIANCE_VALUE_NA
            row["page"] = None
            row["year_selection_status"] = "not_available"
        else:
            row["value"] = COMPLIANCE_VALUE_NA
            row["page"] = None
            row["year_selection_status"] = "ambiguous"

        row["Value"] = row.get("value")
        row["Page"] = row.get("page")
        row["Context"] = row.get("context") or ""

    payload["requested_year"] = year
    return payload


def _validate_cross_analysis_compatibility(file_ids: list[str], reports=None):
    """Raise HTTPException 400 if reports are not comparable: different framework, or GRI with different sector/topic."""
    if len(file_ids) < 2:
        return list(reports or [])
    reports = list(reports) if reports is not None else get_reports_info(file_ids)
    if len(reports) < 2:
        raise HTTPException(status_code=400, detail="Could not resolve at least two reports.")
    frameworks = [str(r.framework or "").strip() for r in reports]
    uniq_fw = set(frameworks)
    if len(uniq_fw) > 1:
        raise HTTPException(
            status_code=400,
            detail="Cross analysis requires the same framework for all reports (e.g. SASB with SASB, GRI with GRI).",
        )
    if uniq_fw == {"GRI"}:
        sectors = [str(getattr(r, "gri_sector", None) or "").strip() for r in reports]
        topics = [str(getattr(r, "gri_topic", None) or "").strip() for r in reports]
        if len(set(sectors)) > 1 or len(set(topics)) > 1:
            raise HTTPException(
                status_code=400,
                detail="GRI cross analysis requires the same Sector and Topic for all reports.",
            )
    return reports


def _cross_disclosed_cache_dir() -> Path:
    """Where we persist assessment-driven cross-analysis JSON outputs."""
    d = _get_cross_cache_dir() / "output" / "json"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cross_disclosed_cache_key(file_ids: list[str], version: str = "v3") -> str:
    """Stable key for a file-id combination."""
    ids_sorted = sorted([str(x).strip() for x in (file_ids or []) if str(x).strip()])
    base = version + "|" + "|".join(ids_sorted)
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
    return f"disclosed_{version}_{h}"  # short, filesystem-friendly


def _cross_disclosed_lock_for(key: str) -> threading.Lock:
    with _cross_disclosed_locks_guard:
        lk = _cross_disclosed_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _cross_disclosed_locks[key] = lk
        return lk


def _safe_strip_file_ext(name: str) -> str:
    try:
        s = str(name or "").strip()
        if not s:
            return ""
        return re.sub(r"\.[^/.]+$", "", s)
    except Exception:
        return str(name or "")


def _extract_year_from_text(text: str) -> Optional[str]:
    """Extract a 4-digit year from a label like 'Bosch (2024)' or 'Bosch_2024_ESG'.

    If multiple years exist, return the latest one.
    """
    try:
        s = str(text or "")
        years = re.findall(r"\b(19\d{2}|20\d{2})\b", s)
        if not years:
            return None
        # choose the latest year
        return str(max(int(y) for y in years))
    except Exception:
        return None


def _find_assessment_json_path(file_id: str, file_info: dict) -> Optional[Path]:
    """Locate the assessment JSON for a given file_id (canonical + legacy)."""
    canonical_dir = Path(file_manager.compliance_outputs)
    manifest_path = _json_path_from_manifest(canonical_dir, file_id, None)
    if manifest_path is not None and manifest_path.is_file():
        return manifest_path

    safe_filename = str(file_info.get("safe_filename") or "")
    base_name = Path(safe_filename).stem if safe_filename else ""

    legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # backend/outputs
    search_dirs = [canonical_dir]
    if legacy_dir.exists():
        search_dirs.append(legacy_dir)

    candidate_names = [f"{file_id}_compliance.json"]
    if base_name:
        candidate_names.append(f"{base_name}_compliance.json")

    for d in search_dirs:
        for name in candidate_names:
            p = d / name
            if p.exists():
                return p

    # strict fuzzy match
    fuzzy_patterns = [f"*{file_id}*compliance*.json", f"*{file_id}*_compliance.json"]
    if base_name:
        fuzzy_patterns.extend([f"*{base_name}*compliance*.json", f"*{base_name}*_compliance.json"])
    matches: list[Path] = []
    for d in search_dirs:
        for pat in fuzzy_patterns:
            matches.extend(list(d.glob(pat)))
    matches = [m for m in matches if m.is_file()]
    if matches:
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return matches[0]
    return None


def _normalize_nav_label(v: Optional[str], default: str) -> str:
    s = str(v or "").strip()
    return s if s else default


def _build_disclosed_records_for_files(
    file_ids: list[str], user_id: int, reports=None
) -> tuple[list[dict], list[dict], list[float]]:
    """Build CrossExtractedRecord-like dicts from per-report assessments.

    Returns:
      - records (list of dict)
      - reports_info (list of dict from get_reports_info)
      - assessment_mtimes (list of mtime floats used for cache invalidation)
    """
    # Labels + years from backend heuristics
    reports = list(reports) if reports is not None else get_reports_info(file_ids)
    report_map = {r.file_id: r for r in reports}

    # Simple normalizers (keep consistent with frontend expectations)
    def normalize_type_label(x: Optional[str]) -> str:
        s = _normalize_nav_label(x, "Metrics")
        return (
            s.replace("discolosure", "Disclosure")
             .replace("Sustainability Disclosure", "Disclosure")
             .replace("activity metric", "Activity Metrics")
             .replace("Activity Metric", "Activity Metrics")
        )

    def normalize_category_label(x: Optional[str]) -> str:
        s = _canonical_metric_category(x, "General")
        return s or "General"

    def strip_metric_prefix(name: str) -> str:
        s = str(name or "").strip()
        if not s:
            return ""
        s = re.sub(r"^\(\d+\)\s*", "", s)
        s = re.sub(r"^\d+\s*[\.|\)]\s*", "", s)
        return s.strip()

    def to_page(v) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v == v:
            return int(v)
        m = re.search(r"\d+", str(v))
        return int(m.group(0)) if m else None

    def looks_numeric(v: str) -> bool:
        return bool(re.search(r"\d", str(v or "")))

    def is_purely_numeric_value(v) -> bool:
        """True if value is a single number (int/float or with %), for GRI cross-analysis comparison."""
        if v is None:
            return False
        s = str(v).strip()
        if not s:
            return False
        s = s.rstrip("%").strip()
        try:
            float(s)
            return True
        except ValueError:
            return False

    records: list[dict] = []
    mtimes: list[float] = []

    for fid in file_ids:
        file_info = file_manager.get_file_info(fid, user_id=user_id)
        if not file_info:
            # access denied / missing
            continue

        rep = report_map.get(fid)
        framework = (file_info or {}).get("framework") or (getattr(rep, "framework", None) if rep else None)

        assessment_path = _find_assessment_json_path(fid, file_info)
        if assessment_path is None or not assessment_path.exists():
            continue
        try:
            mtimes.append(float(assessment_path.stat().st_mtime))
        except Exception:
            pass

        try:
            with open(assessment_path, "r", encoding="utf-8") as f:
                assessment_data = json.load(f)
            assessment_data = _normalize_assessment_payload(assessment_data)
        except Exception as e:
            logger.warning(f"Failed to read assessment json for {fid}: {e}")
            continue

        analyses = assessment_data.get("metric_analyses") or []

        # prefer filename stem as label
        rep = report_map.get(fid)
        label = ""
        if rep is not None:
            label = _safe_strip_file_ext(getattr(rep, "filename", "") or "")
            if not label:
                label = _safe_strip_file_ext(getattr(rep, "display_name", "") or "")
            if not label:
                label = _safe_strip_file_ext(getattr(rep, "short_name", "") or "")
        if not label:
            label = fid

        # report year fallback
        report_year = None
        if rep is not None:
            try:
                ry = getattr(rep, "report_year", None)
                report_year = str(ry).strip() if ry is not None else None
            except Exception:
                report_year = None

        # Normalize display name to the old UI-friendly format: "<Company> (<Year>)" when possible.
        name = label
        name_year = _extract_year_from_text(name)
        if (not name_year) and report_year:
            # only append when name doesn't already contain a year
            name = f"{label} ({report_year})"
            name_year = _extract_year_from_text(name)
        if not name_year:
            name_year = report_year

        for a in analyses:
            metric_name = strip_metric_prefix(a.get("metric_name") or a.get("metric") or a.get("Metric") or "")
            metric_id = str(a.get("metric_id") or a.get("metricId") or a.get("metric_code") or a.get("code") or a.get("Code") or "").strip()
            metric_code = str(a.get("metric_code") or a.get("code") or a.get("Code") or "").strip()

            value = a.get("value")
            ds = str(a.get("disclosure_status") or "").strip().lower()
            is_disclosed = ds == "fully_disclosed" or ((not ds) and value is not None and looks_numeric(str(value)))
            value_str = "" if value is None else str(value)
            if not is_disclosed:
                value_str = ""
            # GRI cross-analysis: only purely numeric values count as disclosed; otherwise treat as not disclosed
            disclosure_status = a.get("disclosure_status")
            if framework == "GRI" and value_str and not is_purely_numeric_value(value_str):
                value_str = ""
                is_disclosed = False
                disclosure_status = "not_disclosed"
            unit = str(a.get("unit") or a.get("Unit") or "").strip() or None
            page = to_page(a.get("page") or a.get("Page") or a.get("page_number") or a.get("pageNumber"))
            typ = normalize_type_label(a.get("type") or a.get("Type"))
            cat = normalize_category_label(a.get("category") or a.get("Category"))
            detail = str(a.get("reasoning") or "").strip()

            year = _extract_year_from_text(name) or name_year

            # Primary nav = type; Secondary nav = SASB Topic (type → topic hierarchy).
            # For "Activity Metrics", secondary = metric_name only (no category like "Quantitative").
            sasb_topic = str(a.get("topic") or a.get("Topic") or "").strip() or cat
            topic_label = metric_name or metric_code or metric_id or "Metric"
            sub_topic = metric_code or metric_id or ""
            secondary_nav = (
                topic_label
                if typ and str(typ).strip().lower() in ("activity metrics",)
                else sasb_topic
            )

            records.append(
                {
                    "id": fid,
                    "name": name,
                    "primary_navigation": typ,
                    "secondary_navigation": secondary_nav,
                    "topic": topic_label,
                    "sub_topic": sub_topic,
                    "category": normalize_category_label(a.get("category") or a.get("Category")) or None,
                    "page": page,
                    "data": value_str,
                    "value": value_str or "",  # CrossDisclosedRecord.value is str; use "" for not disclosed
                    "year": year,
                    "unit": unit,
                    "detail": detail,
                    "disclosure_status": disclosure_status,
                    "metric_id": metric_id or None,
                }
            )

    reports_payload = [r.model_dump() for r in reports]
    return records, reports_payload, mtimes


def _expected_scope_keys_from_meta(file_info: dict, m: Optional[dict]) -> List[str]:
    if m and isinstance(m.get("expected_scope_keys"), list) and m["expected_scope_keys"]:
        return [str(x).strip() for x in m["expected_scope_keys"] if str(x).strip()]
    raw = file_info.get("scope_slugs_json")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return []


def _unlink_compliance_reports_dir_for_file_id(
    file_id: str,
    stem: Optional[str],
    deleted_items: List[str],
) -> None:
    """Delete every artifact under compliance_reports/ that belongs to this upload (JSON, XLSX, manifest)."""
    canonical = Path(file_manager.compliance_outputs)
    if not canonical.is_dir():
        return
    fid = str(file_id).strip()
    if not fid:
        return
    candidates: Set[Path] = set()

    for suffix in ("_compliance.json", "_compliance.xlsx", "_compliance_manifest.json"):
        p = canonical / f"{fid}{suffix}"
        if p.is_file():
            candidates.add(p)

    for pattern in (
        f"*{fid}*_compliance*.json",
        f"*{fid}*_compliance*.xlsx",
    ):
        try:
            for p in canonical.glob(pattern):
                if p.is_file():
                    candidates.add(p)
        except Exception as e:
            logger.warning(f"Compliance glob failed {pattern!r} in {canonical}: {e}")

    if stem:
        st = str(stem).strip()
        if st:
            for suffix in ("_compliance.json", "_compliance.xlsx"):
                p = canonical / f"{st}{suffix}"
                if p.is_file():
                    candidates.add(p)

    for p in sorted(candidates, key=lambda x: str(x)):
        try:
            p.unlink()
            deleted_items.append(f"合规报告: {p.name}")
        except Exception as e:
            logger.warning(f"Failed to remove compliance artifact {p}: {e}")


def _unlink_compliance_markdown_for_file_id(file_id: str, deleted_items: List[str]) -> None:
    """Remove compliance_report_* markdown written next to PDF pipeline (uploads/outputs/markdown/)."""
    md_dir = Path(file_manager.markdown_outputs)
    if not md_dir.is_dir():
        return
    fid = str(file_id).strip()
    if not fid:
        return
    candidates: Set[Path] = set()
    single = md_dir / f"compliance_report_{fid}.md"
    if single.is_file():
        candidates.add(single)
    try:
        for p in md_dir.glob(f"compliance_report_{fid}_*.md"):
            if p.is_file():
                candidates.add(p)
    except Exception as e:
        logger.warning(f"Compliance markdown glob failed for {fid}: {e}")
    for p in sorted(candidates, key=lambda x: str(x)):
        try:
            p.unlink()
            deleted_items.append(f"合规Markdown: {p.name}")
        except Exception as e:
            logger.warning(f"Failed to remove {p}: {e}")


def _patch_file_primary_scope_fields(file_id: str, fw: str, new_exp: List[str]) -> None:
    finfo = file_manager.metadata.get("files", {}).get(file_id)
    if not isinstance(finfo, dict) or not new_exp:
        return
    finfo["scope_slugs_json"] = json.dumps(new_exp, ensure_ascii=False)
    if fw == "GRI":
        finfo["gri_topic"] = new_exp[0]
        # gri_sector unchanged
        finfo["semi_industry"] = None
    elif fw == "SASB":
        finfo["semi_industry"] = new_exp[0]
    elif fw in ("CDP", "TCFD"):
        finfo["semi_industry"] = new_exp[0]
    file_manager._save_metadata()


def _try_delete_one_scope_only(
    file_id: str, file_info: dict, scope_key: str
) -> Optional[dict]:
    """
    Delete one multi-scope compliance bundle; update manifest + metadata.
    Returns response dict if handled; None => caller should run full file delete.
    """
    assessment_dir = Path(file_manager.compliance_outputs)
    m = _load_compliance_manifest(assessment_dir, file_id)
    expected = _expected_scope_keys_from_meta(file_info, m)
    fw = (file_info.get("framework") or "").strip() or "SASB"

    json_p, xlsx_p, md_p = _paths_for_scope_compliance_bundle(
        file_manager, file_id, file_info, scope_key
    )
    deleted_items: List[str] = []
    for p in (json_p, xlsx_p, md_p):
        try:
            if p.exists():
                p.unlink()
                deleted_items.append(f"合规输出: {p.name}")
        except Exception as e:
            logger.warning(f"Failed to remove {p}: {e}")

    # Multi-scope upload: drop this slug from manifest + metadata, keep PDF
    if len(expected) >= 2 and scope_key in expected:
        new_exp = [x for x in expected if x != scope_key]
        if not new_exp:
            return None
        outputs = [
            o
            for o in (m.get("outputs") or [])
            if str(o.get("scope_key")) != str(scope_key)
        ]
        _write_compliance_manifest(
            assessment_dir, file_id, fw, outputs, expected_scope_keys=new_exp
        )
        _patch_file_primary_scope_fields(file_id, fw, new_exp)
        return {
            "status": "success",
            "message": "Removed this analysis scope; report PDF kept",
            "deleted_items": deleted_items,
            "scope_only": True,
        }

    # Single scope in manifest matches this row → remove whole report
    if len(expected) == 1 and expected[0] == scope_key:
        return None

    # Drift / legacy: strip manifest outputs for this key; keep file record + PDF
    if m:
        outputs = [
            o
            for o in (m.get("outputs") or [])
            if str(o.get("scope_key")) != str(scope_key)
        ]
        ek = [x for x in (m.get("expected_scope_keys") or []) if str(x) != str(scope_key)]
        _write_compliance_manifest(
            assessment_dir,
            file_id,
            fw,
            outputs,
            expected_scope_keys=ek if ek else None,
        )
    return {
        "status": "success",
        "message": "Removed compliance outputs for this scope",
        "deleted_items": deleted_items,
        "scope_only": True,
    }

# Export underscore helpers too for service modules.
__all__ = [name for name in globals() if not name.startswith('__')]
