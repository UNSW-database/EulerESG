"""Lightweight metadata resolution for cross-report analysis.

This module deliberately avoids importing :mod:`esg_encoding.cross_analysis`.
The latter imports the semantic retrieval and embedding stack, while the report
bootstrap endpoint only needs file metadata and small assessment JSON files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from loguru import logger

from .cross_analysis_models import CrossAnalysisReport
from .file_manager import file_manager


_YEAR4 = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_REPORT_WORDS = re.compile(
    r"\b(esg|csr|sustainability|annual|integrated|report|fy)\b",
    re.IGNORECASE,
)
_UNKNOWN_DISPLAY_NAMES = {
    "unknown",
    "unknown company",
    "unidentified company",
    "未识别公司主体",
}


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _usable_display_name(value: object) -> Optional[str]:
    name = _clean_text(value)
    if not name or name.casefold() in _UNKNOWN_DISPLAY_NAMES:
        return None
    return name


def _year(value: object) -> Optional[int]:
    try:
        year = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return year if 2000 <= year <= 2035 else None


def _year_from_filename(filename: str) -> Optional[int]:
    try:
        years = [
            int(value)
            for value in _YEAR4.findall(Path(filename).stem)
            if 2000 <= int(value) <= 2035
        ]
        return max(years) if years else None
    except Exception:
        return None


def _display_from_filename(filename: str) -> str:
    """Return a stable legacy fallback without reading embeddings.

    New uploads persist ``company_name``.  Older uploads often only have a
    descriptive original filename; using its non-report portion is both faster
    and more useful than invoking semantic retrieval during page bootstrap.
    """

    stem = Path(filename).stem
    stem = re.sub(r"[_-]+", " ", stem)
    stem = _YEAR4.sub(" ", stem)
    stem = _REPORT_WORDS.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._-()[]")
    return stem or Path(filename).stem or filename


def _report_label(info: dict, filename: str) -> Tuple[str, str, float, Optional[int]]:
    report_year = _year(info.get("report_year")) or _year_from_filename(filename)

    display = _usable_display_name(info.get("company_name"))
    confidence = 0.98 if display else 0.0
    if not display:
        display = _usable_display_name(info.get("display_name"))
        if display:
            try:
                confidence = min(1.0, max(0.0, float(info.get("display_confidence") or 0.7)))
            except (TypeError, ValueError):
                confidence = 0.7
    if not display:
        display = _display_from_filename(filename)
        confidence = 0.35
    if not display:
        display = str(info.get("file_id") or filename)
        confidence = 0.0

    short = display
    if report_year is not None and str(report_year) not in display:
        short = f"{display} {report_year}"
    return display, short, confidence, report_year


def _assessment_dirs() -> Tuple[Path, ...]:
    canonical = Path(file_manager.compliance_outputs)
    legacy = Path(__file__).resolve().parents[2] / "outputs"
    return (canonical, legacy) if legacy != canonical else (canonical,)


def _assessment_files(file_id: str, directories: Iterable[Path]) -> List[Path]:
    matches: List[Path] = []
    seen: set[Path] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        exact = directory / f"{file_id}_compliance.json"
        candidates = [exact] if exact.is_file() else []
        candidates.extend(directory.glob(f"*{file_id}*_compliance.json"))
        for path in candidates:
            if path.is_file() and path not in seen:
                seen.add(path)
                matches.append(path)
    return matches


def _load_manifest(directory: Path, file_id: str) -> Optional[dict]:
    path = directory / f"{file_id}_compliance_manifest.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _gri_sector_from_stem(stem: str, file_id: str, gri_topic: str) -> Optional[str]:
    suffix = f"_{file_id}_compliance"
    if not stem.endswith(suffix) or not stem.startswith("GRI_"):
        return None
    inner = stem[4 : -len(suffix)]
    topic_suffix = f"_{gri_topic}"
    if not inner.endswith(topic_suffix):
        return None
    return inner[: -len(topic_suffix)] or None


def _assessment_scope_fallback(
    file_id: str,
    assessment_paths: List[Path],
    framework: Optional[str],
    industry: Optional[str],
    semi_industry: Optional[str],
    gri_sector: Optional[str],
    gri_topic: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Read at most one small assessment JSON when metadata lacks scope fields."""

    needs_fallback = framework is None or semi_industry is None
    needs_gri = framework == "GRI" and (gri_sector is None or gri_topic is None)
    if not assessment_paths or (not needs_fallback and not needs_gri):
        return framework, industry, semi_industry, gri_sector, gri_topic

    path = assessment_paths[0]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        data = value if isinstance(value, dict) else {}
    except Exception as exc:
        logger.debug("[cross-report-metadata] Could not read {}: {}", path, exc)
        return framework, industry, semi_industry, gri_sector, gri_topic

    framework = framework or (_clean_text(data.get("framework")) or None)
    industry = industry or (_clean_text(data.get("industry")) or None)
    semi_industry = semi_industry or (_clean_text(data.get("semi_industry")) or None)
    if framework == "GRI":
        gri_sector = gri_sector or (_clean_text(data.get("gri_sector")) or None)
        gri_topic = gri_topic or (_clean_text(data.get("gri_topic")) or None)
        if gri_sector is None or gri_topic is None:
            manifest = _load_manifest(path.parent, file_id)
            outputs = manifest.get("outputs") if isinstance(manifest, dict) else None
            first = outputs[0] if isinstance(outputs, list) and outputs else None
            if gri_topic is None and isinstance(first, dict):
                gri_topic = _clean_text(first.get("scope_key")) or None
            if gri_topic and gri_sector is None:
                gri_sector = _gri_sector_from_stem(path.stem, file_id, gri_topic)

    return framework, industry, semi_industry, gri_sector, gri_topic


def _persist_label_updates(updates: Dict[str, Dict[str, object]]) -> None:
    """Persist only actual changes, avoiding an fsync on every GET request."""

    if not updates or not isinstance(getattr(file_manager, "metadata", None), dict):
        return
    changed = False
    lock = getattr(file_manager, "_metadata_lock", None)
    context = lock if lock is not None else _NullContext()
    try:
        with context:
            files = file_manager.metadata.get("files", {})
            if not isinstance(files, dict):
                return
            for file_id, fields in updates.items():
                entry = files.get(file_id)
                if not isinstance(entry, dict):
                    continue
                for key, value in fields.items():
                    if entry.get(key) != value:
                        entry[key] = value
                        changed = True
            if changed:
                file_manager._save_metadata()
    except Exception as exc:
        logger.debug("[cross-report-metadata] Metadata update skipped: {}", exc)


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def get_reports_info(file_ids: List[str]) -> List[CrossAnalysisReport]:
    """Resolve report labels and comparison scope without loading model modules."""

    raw_metadata = getattr(file_manager, "metadata", None)
    raw_files = raw_metadata.get("files", {}) if isinstance(raw_metadata, dict) else {}
    files = raw_files if isinstance(raw_files, dict) else {}
    directories = _assessment_dirs()
    reports: List[CrossAnalysisReport] = []
    updates: Dict[str, Dict[str, object]] = {}

    for file_id in file_ids:
        raw_info = files.get(file_id, {})
        info = raw_info if isinstance(raw_info, dict) else {}
        filename = str(info.get("original_name") or info.get("safe_filename") or file_id)
        try:
            display, short, confidence, report_year = _report_label(info, filename)
            assessment_paths = _assessment_files(file_id, directories)
            framework = _clean_text(info.get("framework")) or None
            industry = _clean_text(info.get("industry")) or None
            semi_industry = _clean_text(info.get("semi_industry")) or None
            gri_sector = _clean_text(info.get("gri_sector")) or None
            gri_topic = _clean_text(info.get("gri_topic")) or None
            framework, industry, semi_industry, gri_sector, gri_topic = (
                _assessment_scope_fallback(
                    file_id,
                    assessment_paths,
                    framework,
                    industry,
                    semi_industry,
                    gri_sector,
                    gri_topic,
                )
            )
            has_assessment = bool(assessment_paths)
        except Exception as exc:
            logger.warning("[cross-report-metadata] Fallback for {}: {}", file_id, exc)
            display = file_id[:24] + ("..." if len(file_id) > 24 else "")
            short = display
            confidence = 0.0
            report_year = None
            has_assessment = False
            framework = industry = semi_industry = gri_sector = gri_topic = None

        reports.append(
            CrossAnalysisReport(
                file_id=file_id,
                display_name=display,
                short_name=short,
                report_year=report_year,
                confidence=float(confidence),
                filename=filename,
                has_assessment=has_assessment,
                framework=framework,
                industry=industry,
                semi_industry=semi_industry,
                gri_sector=gri_sector,
                gri_topic=gri_topic,
            )
        )
        if isinstance(raw_info, dict) and raw_info:
            updates[file_id] = {
                "display_name": display,
                "short_name": short,
                "display_confidence": float(confidence),
            }

    _persist_label_updates(updates)
    return reports
