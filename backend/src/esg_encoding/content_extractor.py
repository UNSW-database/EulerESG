"""PaddleOCR-VL v1.6 Redis 页批次报告内容提取器。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from loguru import logger

from .content_revision import bump_document_content_revision
from .exceptions import ContentExtractionError
from .models import DocumentContent, ProcessingConfig, TextSegment
from .visual_assets import (
    append_visual_markers,
    collect_table_records,
    load_visual_manifest,
    parse_visual_marker,
    promote_visual_assets,
    write_empty_visual_manifest,
)


_HTML_IMAGE_TAG_RE = re.compile(r"<\s*img\b[^>]*>", re.IGNORECASE | re.DOTALL)
_HTML_WRAPPER_TAG_RE = re.compile(
    r"</?\s*(?:div|span|p|center|figure|picture|source|br)\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_MARKDOWN_IMAGE_TAG_RE = re.compile(
    r"!\[([^\]]*)\]\((?:[^()\s]+|\([^)]*\))+(?:\s+['\"].*?['\"])?\)",
    re.IGNORECASE | re.DOTALL,
)
_BARE_IMAGE_PATH_RE = re.compile(
    r"^(?:\.\.?/)?(?:imgs?|images?|assets?)/[^\s]+\.(?:png|jpe?g|webp|gif|svg)$",
    re.IGNORECASE,
)
_GENERIC_VISUAL_LABEL_RE = re.compile(
    r"^(?:image|img|figure|fig|photo|picture|chart|graphic|illustration|untitled|"
    r"visual(?:\s+evidence)?)(?:[\s_.-]*\d+)?$",
    re.IGNORECASE,
)
_INTERNAL_SEGMENT_MARKER_RE = re.compile(
    r"^(?:\*{1,2})?P\d{1,5}_[ST]\d{1,5}(?:\*{1,2})?$",
    re.IGNORECASE,
)
_BARE_VISUAL_EVIDENCE_RE = re.compile(
    r"^visual\s+evidence\s+va_[0-9a-f]{8,}$",
    re.IGNORECASE,
)

_TABLE_SECOND_PASS_ACTIONABLE_REASONS = frozenset(
    {
        "malformed_html",
        "inconsistent_column_count",
        "missing_header",
        "year_value_count_mismatch",
        "structure_source_conflict",
        "low_structure_confidence",
        "low_ocr_confidence",
        "weak_table_record_match",
        "missing_table_record",
        "unexplained_needs_review",
    }
)
_TABLE_SECOND_PASS_CRITICAL_REASONS = frozenset(
    {
        "malformed_html",
        "inconsistent_column_count",
        "missing_header",
        "year_value_count_mismatch",
        "structure_source_conflict",
        "cell_geometry_alignment_mismatch",
        "cell_bbox_count_mismatch",
    }
)
_TABLE_NON_ACTIONABLE_REVIEW_REASONS = frozenset(
    {
        "ambiguous_unit_scope",
        "conflicting_year_scope",
        "ambiguous_year_scope",
    }
)


@dataclass(frozen=True)
class TableSecondPassCandidate:
    source_table_id: str
    table_segment_id: str
    page_number: int
    bbox: Optional[Tuple[float, float, float, float]]
    reading_order: Optional[int]
    reasons: Tuple[str, ...]
    conflict_count: int
    rank_key: Tuple[Any, ...]


@dataclass(frozen=True)
class TableSecondPassPlan:
    total_tables: int
    budget_tables: int
    candidates: Tuple[TableSecondPassCandidate, ...]
    selected_table_ids: Tuple[str, ...]
    pages: Tuple[int, ...]
    render_zoom: float
    prediction_options: Dict[int, Dict[str, Any]]


def _visual_has_searchable_content(value: Dict[str, Any]) -> bool:
    """Return whether a visual asset carries evidence worth indexing as text."""
    if value.get("searchable") is False:
        return False
    if value.get("chart_data") not in (None, "", [], {}):
        return True
    for field in ("caption", "summary", "ocr_text"):
        text = re.sub(r"\s+", " ", str(value.get(field) or "")).strip()
        if text and not _GENERIC_VISUAL_LABEL_RE.fullmatch(text):
            return True
    return False


def _clean_non_table_markdown_block(value: str) -> str:
    """Remove image-only HTML/Markdown emitted alongside durable visual assets.

    PaddleOCR saves image crops separately and may also emit ``<div><img ...>``
    fragments in Markdown.  Keeping both creates duplicate, non-semantic retrieval
    rows.  Meaningful surrounding prose is retained while image-only fragments are
    discarded.
    """
    text = str(value or "")
    text = _HTML_IMAGE_TAG_RE.sub(" ", text)

    def replace_markdown_image(match: re.Match[str]) -> str:
        alt = re.sub(r"\s+", " ", str(match.group(1) or "")).strip()
        if alt.lower() in {"", "image", "img", "figure", "photo", "chart"}:
            return " "
        return f" {alt} "

    text = _MARKDOWN_IMAGE_TAG_RE.sub(replace_markdown_image, text)
    text = _HTML_WRAPPER_TAG_RE.sub("\n", text)
    # Remaining presentation tags (for example <b>) should not become embedding
    # tokens.  Table HTML is handled before this helper is called.
    text = re.sub(r"</?\s*[A-Za-z][^>]*>", " ", text)
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", unescape(line)).strip()
        if not cleaned or _BARE_IMAGE_PATH_RE.fullmatch(cleaned):
            continue
        lines.append(cleaned)
    text = "\n".join(lines).strip()
    if (
        text.lower() in {"image", "img", "figure", "photo", "chart"}
        or _INTERNAL_SEGMENT_MARKER_RE.fullmatch(text)
        or _BARE_VISUAL_EVIDENCE_RE.fullmatch(text)
    ):
        return ""
    return text


def _is_meaningful_short_text(value: str) -> bool:
    """Keep standalone codes, years, units and values despite length limits."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    patterns = (
        r"\b[A-Z]{2,8}-[A-Z]{2,8}-\d{3}[a-z]?\.\d\b",
        r"\b(?:19|20)\d{2}\b",
        r"\b(?:FY|CY)\s*['\u2019]?\s*(?:\d{2}|(?:19|20)\d{2})\b",
        r"\b(?:scope|范围)\s*[123]\b",
        r"[-+]?\d+(?:[.,]\d+)?\s*%",
        r"[-+]?\d+(?:[.,]\d+)?\s*(?:tco2e|co2e|mwh|kwh|gj|mj|kg|mt|t|m3|m²|m2)\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _shared_dir_mode() -> int:
    """返回 PaddleOCR 跨容器共享目录权限，默认 0777。"""
    raw = os.getenv("PADDLEOCR_SHARED_DIR_MODE", "0777")
    try:
        return int(str(raw), 8)
    except Exception:
        return 0o777


def _shared_file_mode() -> int:
    """返回 PaddleOCR 跨容器共享文件权限，默认 0666。"""
    raw = os.getenv("PADDLEOCR_SHARED_FILE_MODE", "0666")
    try:
        return int(str(raw), 8)
    except Exception:
        return 0o666


def _ensure_shared_writable_dir(path: Path) -> Path:
    """创建并放宽 backend/worker 共享目录权限。

    页级 batch 队列中，backend 负责拆页和提交任务，worker 负责写 batch 结果。
    两类容器可能使用不同 Linux 用户，因此共享目录需要允许双方读写。
    """
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, _shared_dir_mode())
    except Exception as exc:
        logger.warning(f"设置共享目录权限失败: {path} ({exc})")
    return path


def _ensure_shared_file(path: Path) -> Path:
    """尽量放宽共享文件权限，便于其他容器读取。"""
    try:
        os.chmod(path, _shared_file_mode())
    except Exception as exc:
        logger.debug(f"设置共享文件权限跳过: {path} ({exc})")
    return path


def _fsync_parent_dir(path: Path) -> None:
    """尽量刷新父目录元数据，减少 Docker Desktop 共享目录可见性竞态。"""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        # 某些平台/文件系统不支持目录 fsync，跳过即可。
        pass


def _write_shared_ready_marker(
    target: Path,
    *,
    payload: Optional[Dict[str, Any]] = None,
    sync_parent: bool = True,
) -> Path:
    """为 batch PDF 写入 .ready 标记。

    backend 先原子写入 batch PDF，再写 ready 标记，worker 只有在 PDF 和
    ready 标记都可见时才开始解析。这样可以避免 Redis 入队速度快于
    bind mount 文件可见性导致的 FileNotFound。
    """
    marker = target.with_name(target.name + ".ready")
    marker_tmp = marker.with_name(marker.name + ".tmp")
    data = {"path": str(target), "size": target.stat().st_size if target.exists() else 0, "created_at": datetime.now().isoformat()}
    if payload:
        data.update(payload)
    with marker_tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(marker_tmp, marker)
    _ensure_shared_file(marker)
    if sync_parent:
        _fsync_parent_dir(marker)
    return marker

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _page_batch_ranges(total_pages: int, batch_size: int) -> list[tuple[int, int]]:
    """Return one-based inclusive page ranges for Redis OCR tasks."""
    if total_pages <= 0:
        return []
    effective_batch_size = max(1, int(batch_size))
    return [
        (start + 1, min(start + effective_batch_size, total_pages))
        for start in range(0, total_pages, effective_batch_size)
    ]


def _selected_page_batch_ranges(
    total_pages: int,
    batch_size: int,
    page_numbers: Optional[Sequence[int]] = None,
    page_options: Optional[Dict[int, Dict[str, Any]]] = None,
) -> list[tuple[int, int]]:
    """Return contiguous OCR ranges for selected one-based pages.

    Native digital pages can be omitted while scanned/hybrid runs are still
    grouped up to ``batch_size``.  ``None`` preserves the legacy all-page plan.
    """
    if page_numbers is None:
        return _page_batch_ranges(total_pages, batch_size)
    selected = sorted({int(page) for page in page_numbers if 1 <= int(page) <= total_pages})
    if not selected:
        return []
    effective_batch_size = max(1, int(batch_size))
    ranges: list[tuple[int, int]] = []

    def signature(page: int) -> str:
        options = (page_options or {}).get(page, {})
        return json.dumps(options, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    run_start = selected[0]
    previous = selected[0]
    current_signature = signature(run_start)
    for page in selected[1:] + [None]:
        contiguous = page is not None and page == previous + 1
        within_batch = page is not None and page - run_start < effective_batch_size
        same_options = page is not None and signature(page) == current_signature
        if contiguous and within_batch and same_options:
            previous = page
            continue
        ranges.append((run_start, previous))
        if page is not None:
            run_start = page
            previous = page
            current_signature = signature(page)
    return ranges


_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(\d+)\b[^>]*-->", re.IGNORECASE)


def _markdown_content_by_page(markdown: str) -> Dict[int, str]:
    """Split Paddle/native Markdown into page bodies without retaining markers."""
    text = str(markdown or "")
    matches = list(_PAGE_MARKER_RE.finditer(text))
    pages: Dict[int, str] = {}
    for index, match in enumerate(matches):
        page = max(1, int(match.group(1)))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            pages[page] = body
        else:
            pages.setdefault(page, "")
    return pages


def _normalise_page_text(value: str) -> str:
    text = unescape(str(value or "")).casefold()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\w%]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _bbox_values(value: Any) -> Optional[List[float]]:
    """Coerce rects or four-point polygons to ``[x0, y0, x1, y1]``."""
    if not isinstance(value, (list, tuple)):
        return None
    coordinates: List[float] = []
    try:
        if value and all(isinstance(item, (list, tuple)) for item in value):
            for point in value:
                if len(point) >= 2:
                    coordinates.extend((float(point[0]), float(point[1])))
        else:
            coordinates = [float(item) for item in value]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(coordinates) == 4:
        x0, y0, x1, y1 = coordinates
    elif len(coordinates) >= 8 and len(coordinates) % 2 == 0:
        xs = coordinates[0::2]
        ys = coordinates[1::2]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    else:
        return None
    if not all(value == value and abs(value) != float("inf") for value in (x0, y0, x1, y1)):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _normalized_bbox(
    value: Any,
    *,
    page_width: float = 0.0,
    page_height: float = 0.0,
) -> Optional[List[float]]:
    bbox = _bbox_values(value)
    if bbox is None:
        return None
    if max(abs(item) for item in bbox) > 1.0001:
        if page_width <= 0.0 or page_height <= 0.0:
            return None
        bbox = [
            bbox[0] / page_width,
            bbox[1] / page_height,
            bbox[2] / page_width,
            bbox[3] / page_height,
        ]
    return [max(0.0, min(1.0, item)) for item in bbox]


def _bbox_iou(first: Any, second: Any) -> float:
    left = _bbox_values(first)
    right = _bbox_values(second)
    if left is None or right is None:
        return 0.0
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if intersection <= 0.0:
        return 0.0
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _bbox_union(values: Sequence[Any]) -> Optional[List[float]]:
    boxes = [box for value in values if (box := _bbox_values(value)) is not None]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _normalised_table_text(rows: Sequence[Sequence[Any]]) -> str:
    parts: List[str] = []
    for row in rows:
        cells = [
            re.sub(r"\s+", " ", unicodedata.normalize("NFKC", unescape(str(cell or ""))))
            .casefold()
            .strip()
            for cell in row
        ]
        if any(cells):
            parts.append(" | ".join(cells))
    return " || ".join(parts)


def _merge_native_and_ocr_page(native_markdown: str, ocr_markdown: str) -> str:
    """Prefer native text while retaining OCR-only structures on hybrid pages."""
    native = str(native_markdown or "").strip()
    ocr = str(ocr_markdown or "").strip()
    if not native:
        return ocr
    if not ocr:
        return native
    native_key = _normalise_page_text(native)
    ocr_key = _normalise_page_text(ocr)
    if native_key and (native_key in ocr_key or ocr_key in native_key):
        if "<table" not in ocr.casefold() and "|" not in ocr and "visual-asset:" not in ocr:
            return native

    # Paddle often repeats the native paragraph before an OCR-only table.  A
    # page-level concatenation indexes that paragraph twice and can introduce a
    # second, mis-OCRed copy of important numbers.  Keep structural blocks and
    # only retain non-structural OCR text when it is genuinely absent natively.
    fragments = [fragment.strip() for fragment in re.split(r"\n\s*\n", ocr) if fragment.strip()]
    supplements: List[str] = []
    native_tokens = set(re.findall(r"[\w%+.-]+", native_key, flags=re.UNICODE))
    for fragment in fragments:
        lowered = fragment.casefold()
        fragment_lines = [line.strip() for line in fragment.splitlines() if line.strip()]
        is_markdown_table = (
            len(fragment_lines) >= 2
            and sum("|" in line for line in fragment_lines) >= 2
            and any(re.search(r"\|?\s*:?-{2,}:?\s*\|", line) for line in fragment_lines)
        )
        is_structure = (
            "<table" in lowered
            or "visual-asset:" in lowered
            or is_markdown_table
        )
        if is_structure:
            supplements.append(fragment)
            continue
        cleaned = _clean_non_table_markdown_block(fragment)
        if not cleaned or _PAGE_MARKER_RE.search(cleaned):
            continue
        fragment_key = _normalise_page_text(cleaned)
        if not fragment_key:
            continue
        if fragment_key in native_key or native_key in fragment_key:
            continue
        similarity = SequenceMatcher(None, fragment_key, native_key, autojunk=False).ratio()
        fragment_tokens = set(re.findall(r"[\w%+.-]+", fragment_key, flags=re.UNICODE))
        token_coverage = (
            len(fragment_tokens & native_tokens) / len(fragment_tokens)
            if fragment_tokens
            else 0.0
        )
        if similarity >= 0.82 or token_coverage >= 0.72:
            continue
        supplements.append(cleaned)
    if not supplements:
        return native
    return f"{native}\n\n" + "\n\n".join(supplements)


def _adaptive_analysis_maps(analysis: Any) -> tuple[Dict[int, str], Dict[int, str]]:
    """Return route and native-Markdown maps from a page-parser analysis."""
    routes: Dict[int, str] = {}
    native_pages: Dict[int, str] = {}
    if analysis is None or not bool(getattr(analysis, "available", False)):
        return routes, native_pages
    for fallback_page, profile in enumerate(list(getattr(analysis, "pages", []) or []), 1):
        try:
            page = max(1, int(getattr(profile, "page_number", fallback_page) or fallback_page))
        except Exception:
            page = fallback_page
        route = str(getattr(profile, "route", "ocr") or "ocr").strip().lower()
        if route not in {"native", "ocr", "hybrid"}:
            route = "ocr"
        routes[page] = route
        native_markdown = str(getattr(profile, "native_markdown", "") or "").strip()
        if native_markdown or route == "native":
            native_pages[page] = native_markdown
    return routes, native_pages


def _adaptive_prediction_options_by_page(analysis: Any) -> Dict[int, Dict[str, Any]]:
    """Translate conservative page hints into worker prediction overrides."""
    options_by_page: Dict[int, Dict[str, Any]] = {}
    if analysis is None or not bool(getattr(analysis, "available", False)):
        return options_by_page
    try:
        high_min = max(784, int(os.getenv("PADDLEOCR_VLM_HIGH_RES_MIN_PIXELS", "200704") or "200704"))
        high_max = max(high_min, int(os.getenv("PADDLEOCR_VLM_HIGH_RES_MAX_PIXELS", "1605632") or "1605632"))
    except (TypeError, ValueError, OverflowError):
        high_min, high_max = 200704, 1605632
    for fallback_page, profile in enumerate(list(getattr(analysis, "pages", []) or []), 1):
        try:
            page = max(1, int(getattr(profile, "page_number", fallback_page) or fallback_page))
        except (TypeError, ValueError, OverflowError):
            page = fallback_page
        hints = getattr(profile, "complexity_hints", {}) or {}
        if not isinstance(hints, dict):
            hints = {}
        possible_chart = bool(hints.get("possible_chart"))
        visual_heavy = bool(hints.get("visual_heavy"))
        options: Dict[str, Any] = {
            "use_doc_orientation_classify": bool(hints.get("needs_orientation")),
            "use_doc_unwarping": bool(hints.get("needs_unwarping")),
            "use_chart_recognition": possible_chart,
            "use_ocr_for_image_block": possible_chart or visual_heavy,
        }
        if bool(hints.get("needs_high_resolution_ocr")):
            options["min_pixels"] = high_min
            options["max_pixels"] = high_max
        options_by_page[page] = options
    return options_by_page


def _native_only_result(analysis: Any) -> Dict[str, Any]:
    """Build a queue-compatible extraction result for an all-native PDF."""
    routes, native_pages = _adaptive_analysis_maps(analysis)
    total_pages = int(getattr(analysis, "total_pages", 0) or len(routes))
    missing = [page for page in range(1, total_pages + 1) if page not in native_pages]
    if total_pages <= 0 or missing:
        raise ValueError(f"Native PDF analysis is incomplete; missing pages={missing[:20]}")
    markdown = "\n\n".join(
        f"<!-- Page {page} | adaptive parser route=native -->\n\n{native_pages[page]}"
        for page in range(1, total_pages + 1)
    )
    return {
        "status": "success",
        "parser": "adaptive-native",
        "pipeline_version": "adaptive-v1",
        "queue_granularity": "none",
        "mode": "native",
        "page_batch_size": 0,
        "total_pages": total_pages,
        "units_processed": 0,
        "elapsed_worker_seconds_sum": 0.0,
        "visual_asset_count": 0,
        "visual_assets": [],
        "table_records": [],
        "output_dir": "",
        "result_markdown_path": "",
        "intermediate_output_removed": True,
        "page_routes": routes,
        "native_page_count": total_pages,
        "ocr_page_count": 0,
        "markdown": markdown,
    }


def _native_manifest_pages(analysis: Any) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    for fallback_page, profile in enumerate(list(getattr(analysis, "pages", []) or []), 1):
        try:
            page_number = max(1, int(getattr(profile, "page_number", fallback_page) or fallback_page))
        except (TypeError, ValueError, OverflowError):
            page_number = fallback_page
        pages.append(
            {
                "page_number": page_number,
                "page_width": float(getattr(profile, "page_width", 0.0) or 0.0) or None,
                "page_height": float(getattr(profile, "page_height", 0.0) or 0.0) or None,
                "rotation": int(getattr(profile, "rotation", 0) or 0),
                "parser_route": str(getattr(profile, "route", "native") or "native"),
            }
        )
    return pages


def _paddle_markdown_page_markers(markdown: object) -> list[int]:
    """Return one-based page markers emitted by PaddleOCR-VL batch workers."""
    return [
        int(match.group(1))
        for match in re.finditer(
            r"<!--\s*page\s+(\d+)\s*\|",
            str(markdown or ""),
            flags=re.IGNORECASE,
        )
    ]


def _duration_summary(values: Sequence[object]) -> Dict[str, float | int]:
    """Build stable internal timing statistics from worker duration values."""
    durations: list[float] = []
    for value in values:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration >= 0:
            durations.append(duration)

    if not durations:
        return {"count": 0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    ordered = sorted(durations)

    def percentile(fraction: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

    return {
        "count": len(ordered),
        "avg": round(statistics.fmean(ordered), 3),
        "p50": round(percentile(0.50), 3),
        "p95": round(percentile(0.95), 3),
        "max": round(ordered[-1], 3),
    }


def _batch_timing_summary(batch_states: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float | int]]:
    elapsed_values: list[object] = []
    predict_values: list[object] = []
    for state in batch_states:
        raw_result = state.get("result_json")
        if isinstance(raw_result, dict):
            result = raw_result
        else:
            try:
                result = json.loads(str(raw_result or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                result = {}

        elapsed = result.get("elapsed_seconds", state.get("elapsed_seconds"))
        if elapsed is not None and elapsed != "":
            elapsed_values.append(elapsed)
        predict = result.get("predict_seconds", state.get("predict_seconds"))
        if predict is not None and predict != "":
            predict_values.append(predict)

    return {
        "elapsed_seconds": _duration_summary(elapsed_values),
        "predict_seconds": _duration_summary(predict_values),
    }


def _normalise_link_text(value: object) -> str:
    text = unescape(str(value or "")).lower()
    text = text.replace("\\n", " ").replace("\\%", "%")
    text = re.sub(r"\$\s*\^\{.*?\}\s*\$", " ", text)
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_PDF_LINK_RESOLUTION_VERSION = 2


def _link_record_key(record: Dict[str, Any]) -> tuple:
    return (
        str(record.get("link_type") or ""),
        int(record.get("source_page") or 0),
        int(record.get("target_page") or 0),
        str(record.get("uri") or ""),
        str(record.get("anchor_text") or ""),
    )


def _append_pdf_link(segment: TextSegment, record: Dict[str, Any]) -> bool:
    data = dict(segment.structured_data or {})
    links = [dict(item) for item in (data.get("pdf_links") or []) if isinstance(item, dict)]
    key = _link_record_key(record)
    if any(_link_record_key(item) == key for item in links):
        return False
    links.append(dict(record))
    data["pdf_links"] = links
    segment.structured_data = data
    return True


def _anchor_match_score(anchor: str, content: str, segment: TextSegment) -> float:
    if not anchor:
        return 0.0
    if not content:
        return 0.0
    if anchor in content:
        score = 1.0 + min(0.2, len(anchor) / max(len(content), 1))
    else:
        anchor_tokens = set(anchor.split())
        content_tokens = set(content.split())
        if not anchor_tokens:
            return 0.0
        score = len(anchor_tokens & content_tokens) / len(anchor_tokens)
    segment_type = str(segment.segment_type or "").lower()
    if segment_type == "table_row":
        score += 0.08
    elif segment_type == "table_cell":
        score += 0.05
    return score


def _link_attachment_group(segment: TextSegment) -> tuple:
    """Group row-derived segments so one annotation is not assigned to every cell."""
    data = segment.structured_data or {}
    table_id = segment.source_table_id or data.get("table_id")
    row_index = data.get("row_index")
    if table_id and row_index is not None:
        return (
            "table_row",
            str(table_id),
            str(getattr(segment, "page_number", None) or data.get("page_number") or ""),
            str(row_index),
        )
    return ("segment", str(segment.segment_id))


def _duplicate_anchor_candidates(
    anchor: str,
    candidates: Sequence[TextSegment],
    normalised_content: Dict[int, str],
) -> List[TextSegment]:
    """Return one ordered candidate per visual occurrence of a repeated anchor."""
    exact = [
        segment
        for segment in candidates
        if anchor and anchor in normalised_content.get(id(segment), "")
    ]
    if not exact:
        return []

    # Paddle table cells repeat the complete row context. Prefer the row segment
    # so duplicate PDF annotations map to distinct rows instead of sibling cells.
    for preferred_type in ("table_row", "text", "heading", "table_cell", "table"):
        typed = [
            segment
            for segment in exact
            if str(segment.segment_type or "").lower() == preferred_type
        ]
        if not typed:
            continue
        unique: Dict[tuple, TextSegment] = {}
        for segment in sorted(
            typed,
            key=lambda item: (
                float(getattr(item, "position_y", 0.0) or 0.0),
                float(getattr(item, "position_x", 0.0) or 0.0),
                str(item.segment_id),
            ),
        ):
            unique.setdefault(_link_attachment_group(segment), segment)
        return list(unique.values())
    return []


def _extract_pdf_link_records(pdf_path: Path) -> List[Dict[str, Any]]:
    try:
        import fitz  # type: ignore
    except Exception as exc:
        logger.warning(f"PyMuPDF unavailable; PDF links will not be resolved: {exc}")
        return []

    records: List[Dict[str, Any]] = []
    try:
        document = fitz.open(str(pdf_path))
    except Exception as exc:
        logger.warning(f"Unable to open PDF for link extraction: {pdf_path} ({exc})")
        return []

    try:
        for page_index in range(len(document)):
            page = document[page_index]
            try:
                page_links = page.get_links() or []
            except Exception:
                continue
            try:
                page_words = page.get_text("words", sort=True) or []
            except Exception:
                page_words = []
            for link in page_links:
                if not isinstance(link, dict):
                    continue
                rect = link.get("from")
                anchor_text = ""
                if rect is not None:
                    try:
                        rx0, ry0, rx1, ry1 = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
                        anchor_words = [
                            str(word[4])
                            for word in page_words
                            if len(word) >= 5
                            and float(word[2]) > rx0
                            and float(word[0]) < rx1
                            and float(word[3]) > ry0
                            and float(word[1]) < ry1
                        ]
                        anchor_text = re.sub(r"\s+", " ", " ".join(anchor_words)).strip()
                    except Exception:
                        anchor_text = ""

                raw_target_page = link.get("page")
                target_page: Optional[int] = None
                try:
                    if raw_target_page is not None and int(raw_target_page) >= 0:
                        target_page = int(raw_target_page) + 1
                except Exception:
                    target_page = None

                uri = str(link.get("uri") or "").strip()
                remote_file = str(link.get("file") or "").strip()
                if target_page is not None and not uri and not remote_file:
                    link_type = "internal"
                elif uri or remote_file:
                    link_type = "external_ignored"
                else:
                    link_type = "internal_unresolved"

                coords: List[float] = []
                if rect is not None:
                    try:
                        coords = [round(float(value), 3) for value in (rect.x0, rect.y0, rect.x1, rect.y1)]
                    except Exception:
                        try:
                            coords = [round(float(value), 3) for value in list(rect)[:4]]
                        except Exception:
                            coords = []

                record: Dict[str, Any] = {
                    "link_type": link_type,
                    "anchor_text": anchor_text,
                    "source_page": page_index + 1,
                    "resolution_version": _PDF_LINK_RESOLUTION_VERSION,
                }
                if target_page is not None:
                    record["target_page"] = target_page
                if uri:
                    record["uri"] = uri
                elif remote_file:
                    record["uri"] = remote_file
                if coords:
                    record["rect"] = coords
                records.append(record)
    finally:
        document.close()
    return records


def enrich_document_with_pdf_links(
    document_content: DocumentContent,
    pdf_path: Optional[str | Path] = None,
) -> Dict[str, int]:
    """Attach internal-link topology to Paddle-derived segments without extracting PDF body text."""
    summary = {"links": 0, "internal": 0, "external_ignored": 0, "anchors_created": 0}
    content_changed = False
    if not _env_bool("REPORT_LINK_RESOLUTION_ENABLED", True):
        return summary

    source_path = Path(pdf_path or document_content.file_path)
    if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
        return summary

    existing_records = [
        link
        for segment in document_content.segments
        for link in (
            (segment.structured_data or {}).get("pdf_links", [])
            if isinstance(segment.structured_data, dict)
            else []
        )
        if isinstance(link, dict)
    ]
    if existing_records and all(
        int(link.get("resolution_version") or 0) >= _PDF_LINK_RESOLUTION_VERSION
        for link in existing_records
    ):
        unique_existing = {_link_record_key(link): link for link in existing_records}
        summary["links"] = len(unique_existing)
        summary["internal"] = sum(1 for link in unique_existing.values() if link.get("link_type") == "internal")
        summary["external_ignored"] = sum(
            1 for link in unique_existing.values() if link.get("link_type") == "external_ignored"
        )
        return summary

    records = _extract_pdf_link_records(source_path)
    if not records:
        if existing_records:
            unique_existing = {_link_record_key(link): link for link in existing_records}
            summary["links"] = len(unique_existing)
            summary["internal"] = sum(
                1 for link in unique_existing.values() if link.get("link_type") == "internal"
            )
            summary["external_ignored"] = sum(
                1 for link in unique_existing.values() if link.get("link_type") == "external_ignored"
            )
        return summary

    if existing_records:
        repaired_segments: List[TextSegment] = []
        for segment in document_content.segments:
            data = dict(segment.structured_data or {})
            is_generated_anchor = (
                str(segment.segment_type or "").lower() == "link_anchor"
                and data.get("source") == "pymupdf_link_annotation"
            )
            if is_generated_anchor:
                content_changed = True
                continue
            if "pdf_links" in data:
                data.pop("pdf_links", None)
                content_changed = True
            segment.structured_data = data
            repaired_segments.append(segment)
        document_content.segments = repaired_segments

    anchor_counts: Dict[str, int] = {}
    page_anchor_counts: Dict[tuple[int, str], int] = {}
    for item in records:
        anchor_key = _normalise_link_text(item.get("anchor_text"))
        if anchor_key:
            anchor_counts[anchor_key] = anchor_counts.get(anchor_key, 0) + 1
            page_anchor_key = (int(item.get("source_page") or 1), anchor_key)
            page_anchor_counts[page_anchor_key] = page_anchor_counts.get(page_anchor_key, 0) + 1

    segments = document_content.segments
    page_segments: Dict[int, List[TextSegment]] = {}
    existing_ids = {str(segment.segment_id) for segment in segments}
    normalised_content = {id(segment): _normalise_link_text(segment.content) for segment in segments}
    for segment in segments:
        page_segments.setdefault(int(segment.page_number or 1), []).append(segment)

    anchor_sequence = 0
    used_duplicate_groups: Dict[tuple[int, str], set[tuple]] = {}
    for raw_record in records:
        record = dict(raw_record)
        anchor_key = _normalise_link_text(record.get("anchor_text"))
        if anchor_key and anchor_counts.get(anchor_key, 0) >= 5:
            record["navigation"] = True
        summary["links"] += 1
        link_type = str(record.get("link_type") or "")
        if link_type == "internal":
            summary["internal"] += 1
        elif link_type == "external_ignored":
            summary["external_ignored"] += 1

        source_page = int(record.get("source_page") or 1)
        candidates = page_segments.get(source_page, [])
        anchor_text = str(record.get("anchor_text") or "").strip()
        matched: Optional[TextSegment] = None
        if anchor_text and candidates:
            normalised_anchor = _normalise_link_text(anchor_text)
            page_anchor_key = (source_page, normalised_anchor)
            if page_anchor_counts.get(page_anchor_key, 0) > 1:
                used_groups = used_duplicate_groups.setdefault(page_anchor_key, set())
                for candidate in _duplicate_anchor_candidates(
                    normalised_anchor,
                    candidates,
                    normalised_content,
                ):
                    group = _link_attachment_group(candidate)
                    if group not in used_groups:
                        matched = candidate
                        used_groups.add(group)
                        break
            if matched is None:
                scored = [
                    (_anchor_match_score(normalised_anchor, normalised_content.get(id(segment), ""), segment), segment)
                    for segment in candidates
                ]
                best_score, best_segment = max(scored, key=lambda item: item[0])
                if best_score >= 0.58:
                    matched = best_segment

        attached_targets: List[TextSegment] = []
        if matched is not None:
            attached_targets.append(matched)
        elif (link_type == "external_ignored" or record.get("navigation")) and candidates:
            # Keep ignored URL/navigation metadata without creating retrievable link text.
            attached_targets.append(candidates[0])

        if attached_targets:
            for target in attached_targets:
                if _append_pdf_link(target, record):
                    content_changed = True
            continue

        if link_type != "internal" or record.get("navigation") or not anchor_key:
            continue

        anchor_sequence += 1
        base_id = f"{document_content.document_id}_p{source_page}_link_{anchor_sequence:04d}"
        segment_id = base_id
        suffix = 1
        while segment_id in existing_ids:
            suffix += 1
            segment_id = f"{base_id}_{suffix}"
        existing_ids.add(segment_id)
        page_position = max(
            [float(getattr(item, "position_y", 0.0) or 0.0) for item in candidates] or [0.0]
        ) + 0.01
        anchor_segment = TextSegment(
            segment_id=segment_id,
            content=anchor_text or f"Internal PDF link to page {record.get('target_page')}",
            page_number=source_page,
            position_y=page_position,
            segment_type="link_anchor",
            structured_data={"source": "pymupdf_link_annotation", "pdf_links": [dict(record)]},
        )
        segments.append(anchor_segment)
        content_changed = True
        normalised_content[id(anchor_segment)] = _normalise_link_text(anchor_segment.content)
        page_segments.setdefault(source_page, []).append(anchor_segment)
        summary["anchors_created"] += 1

    if content_changed:
        bump_document_content_revision(document_content)
    return summary


def _cleanup_path_tree(path: Path, *, label: str = "") -> None:
    """删除 PaddleOCR 过程目录。

    页批次队列需要临时 PDF 和 Markdown。合并完成或失败后删除这些过程文件，
    最终只保留 `<pdf_stem>_extracted.md`。
    """
    try:
        if path.exists():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"已删除 PaddleOCR 过程目录{f'({label})' if label else ''}: {path}")
    except Exception as exc:
        logger.warning(f"删除 PaddleOCR 过程目录失败{f'({label})' if label else ''}: {path} ({exc})")


class _SimpleHTMLTableParser(HTMLParser):
    """轻量级 HTML 表格解析器，用于解析 PaddleOCR-VL Markdown 中的表格。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._row: Optional[
            List[tuple[str, int, int, bool, str, str, str]]
        ] = None
        self._cell: Optional[List[str]] = None
        self._cell_rowspan = 1
        self._cell_colspan = 1
        self._in_cell = False
        self._active_rowspans: Dict[int, tuple[str, int]] = {}
        self.cells: List[Dict[str, Any]] = []
        self.row_metadata: List[Dict[str, Any]] = []
        self._row_index = 0
        self._cell_is_header = False
        self._cell_scope = ""
        self._cell_tag = ""
        self._section = ""
        self._in_caption = False
        self._caption_parts: List[str] = []
        self.caption = ""

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        tag = tag.lower()
        if tag in {"thead", "tbody", "tfoot"}:
            self._section = tag
        elif tag == "caption":
            self._in_caption = True
            self._caption_parts = []
        elif tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
            self._cell_rowspan = self._parse_span(attrs, "rowspan")
            self._cell_colspan = self._parse_span(attrs, "colspan")
            self._in_cell = True
            attrs_map = {
                str(name or "").strip().lower(): str(value or "").strip().lower()
                for name, value in attrs
            }
            self._cell_scope = attrs_map.get("scope", "")
            self._cell_tag = tag
            self._cell_is_header = tag == "th" or self._section == "thead"
        elif tag == "br" and self._in_cell and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._in_cell and self._cell is not None:
            self._cell.append(data)
        elif self._in_caption:
            self._caption_parts.append(data)

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            text = re.sub(r"\s+", " ", unescape("".join(self._cell or []))).strip()
            if self._row is not None:
                self._row.append(
                    (
                        text,
                        self._cell_rowspan,
                        self._cell_colspan,
                        self._cell_is_header,
                        self._section,
                        self._cell_scope,
                        self._cell_tag,
                    )
                )
            self._cell = None
            self._cell_rowspan = 1
            self._cell_colspan = 1
            self._in_cell = False
            self._cell_scope = ""
            self._cell_tag = ""
        elif tag == "tr":
            if self._row is not None:
                cells_start = len(self.cells)
                expanded_row = self._expand_row(self._row)
                if any(str(cell).strip() for cell in expanded_row):
                    self.rows.append(expanded_row)
                    sections = {
                        section
                        for *_prefix, section, _scope, _tag in self._row
                        if section
                    }
                    self.row_metadata.append(
                        {
                            "row_index": self._row_index,
                            "section": next(iter(sections)) if len(sections) == 1 else "",
                            "has_header_cells": any(cell[3] for cell in self._row),
                            "has_data_cells": any(not cell[3] for cell in self._row),
                        }
                    )
                    self._row_index += 1
                else:
                    del self.cells[cells_start:]
            self._row = None
        elif tag == "caption":
            self.caption = re.sub(
                r"\s+",
                " ",
                unescape("".join(self._caption_parts)),
            ).strip()
            self._caption_parts = []
            self._in_caption = False
        elif tag in {"thead", "tbody", "tfoot"}:
            self._section = ""

    @staticmethod
    def _parse_span(attrs: Sequence[tuple[str, Optional[str]]], name: str) -> int:
        for attr_name, raw_value in attrs:
            if str(attr_name).lower() != name:
                continue
            try:
                value = int(str(raw_value or "1").strip())
            except (TypeError, ValueError):
                return 1
            return min(value, 1000) if value > 0 else 1
        return 1

    def _expand_row(
        self,
        cells: Sequence[tuple[str, int, int, bool, str, str, str]],
    ) -> List[str]:
        occupied = {
            column: text
            for column, (text, _remaining_rows) in self._active_rowspans.items()
        }
        next_rowspans = {
            column: (text, remaining_rows - 1)
            for column, (text, remaining_rows) in self._active_rowspans.items()
            if remaining_rows > 1
        }

        column = 0
        for text, rowspan, colspan, is_header, section, scope, tag in cells:
            while any((column + offset) in occupied for offset in range(colspan)):
                column += 1

            self.cells.append({
                "row_index": self._row_index,
                "col_index": column,
                "text": text,
                "rowspan": rowspan,
                "colspan": colspan,
                "is_header": is_header,
                "section": section,
                "scope": scope,
                "tag": tag,
            })
            for offset in range(colspan):
                target_column = column + offset
                occupied[target_column] = text
                if rowspan > 1:
                    next_rowspans[target_column] = (text, rowspan - 1)
            column += colspan

        self._active_rowspans = next_rowspans
        if not occupied:
            return []
        return [occupied.get(column, "") for column in range(max(occupied) + 1)]


class ContentExtractor:
    """通过 PaddleOCR-VL v1.6 Redis 队列提取报告内容。

    主要环境变量：
        PADDLEOCR_PAGE_BATCH_SIZE: backend 拆分 PDF 的每批页数，默认 7。
        PADDLEOCR_VL_TIMEOUT: 解析等待超时时间，单位秒。
    """

    def __init__(self, config: ProcessingConfig | None = None):
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ContentExtractor")
        # 后台上传任务会在这里注入进度回调，用于 SSE 实时推送。
        self.progress_callback: Optional[Callable[..., None]] = None

    def _emit_progress(self, stage: str, message: str, progress: Optional[float] = None, **extra: Any) -> None:
        """Best-effort progress event for long OCR extraction."""
        cb = getattr(self, "progress_callback", None)
        if not cb:
            return
        try:
            cb(stage=stage, message=message, progress=progress, extra=extra or None)
        except Exception as exc:
            self.logger.debug(f"进度回调失败，已忽略: {exc}")

    def extract_pdf(self, file_path: str) -> DocumentContent:
        source_path = Path(file_path).resolve()
        if not source_path.exists():
            raise ContentExtractionError(f"文件不存在: {source_path}", file_path=str(source_path))

        start = time.perf_counter()
        paddle_lifecycle_job_id = ""
        try:
            self.logger.info(f"开始使用 PaddleOCR-VL v1.6 提取报告内容: {source_path}")
            self._emit_progress("ocr_start", "PaddleOCR-VL extraction started.", 10)
            page_analysis = None
            adaptive_started = time.perf_counter()
            if _env_bool("REPORT_ADAPTIVE_PAGE_ROUTING_ENABLED", True):
                try:
                    from .page_parser import analyze_pdf_pages

                    page_analysis = analyze_pdf_pages(source_path)
                    if bool(getattr(page_analysis, "available", False)):
                        route_counts = dict(getattr(page_analysis, "route_counts", {}) or {})
                        self.logger.info(
                            f"Adaptive PDF page analysis completed: file={source_path.name}, "
                            f"pages={getattr(page_analysis, 'total_pages', 0)}, routes={route_counts}"
                        )
                        self._emit_progress(
                            "page_routing",
                            f"Page routing ready: {route_counts}",
                            11,
                            route_counts=route_counts,
                        )
                    else:
                        self.logger.warning(
                            f"Adaptive page analysis unavailable; using full OCR: "
                            f"{getattr(page_analysis, 'error', '')}"
                        )
                except Exception as exc:
                    page_analysis = None
                    self.logger.warning(f"Adaptive page analysis failed; using full OCR: {exc}")

            page_analysis_seconds = time.perf_counter() - adaptive_started
            result = self._run_paddleocr_vl_page_batch_queue(
                source_path,
                page_analysis=page_analysis,
                release_after_document=False,
            )
            paddle_lifecycle_job_id = str(
                result.get("_paddle_lifecycle_job_id") or ""
            )
            stage_timings = dict(result.get("stage_timings") or {})
            stage_timings["page_analysis_seconds"] = round(page_analysis_seconds, 3)
            result["stage_timings"] = stage_timings
            markdown = str(result.get("markdown") or "").strip()
            if not markdown:
                raise ContentExtractionError(
                    "PaddleOCR-VL 返回的 Markdown 为空",
                    file_path=str(source_path),
                )

            segment_started = time.perf_counter()
            document_id = self._document_id(source_path)
            segments = self._segments_from_markdown(markdown, document_id)
            table_records = list(result.get("table_records") or [])
            if table_records:
                self._prefer_structured_table_records(
                    segments,
                    table_records,
                    document_id,
                )
            if page_analysis is not None and bool(getattr(page_analysis, "available", False)):
                self._enrich_segments_from_native_layout(segments, page_analysis)
            second_pass_started = time.perf_counter()
            second_pass_summary = self._run_table_second_pass(
                source_path,
                document_id,
                segments,
                page_analysis=page_analysis,
                release_after_document=False,
            )
            second_pass_job_id = str(
                second_pass_summary.pop("_paddle_lifecycle_job_id", "") or ""
            )
            if not paddle_lifecycle_job_id:
                paddle_lifecycle_job_id = second_pass_job_id
            second_pass_seconds = time.perf_counter() - second_pass_started
            result["table_second_pass"] = second_pass_summary
            self._stitch_continued_tables(segments)
            if not segments:
                segments = [
                    TextSegment(
                        segment_id=f"{document_id}_p1_s1",
                        content=markdown,
                        page_number=1,
                        position_y=0.0,
                        position_x=0.0,
                        segment_type="text",
                        structured_data={
                            "source": "paddleocr_vl_markdown_fallback",
                            "parser": "paddleocr-vl",
                            "pipeline_version": result.get("pipeline_version", "v1.6"),
                        },
                    )
                ]

            markdown = self._markdown_from_final_segments(segments, markdown)
            result["markdown"] = markdown

            document = DocumentContent(
                document_id=document_id,
                file_path=str(source_path),
                segments=segments,
                markdown_content=markdown,
                created_at=datetime.now(),
            )
            segment_seconds = max(
                0.0,
                time.perf_counter() - segment_started - second_pass_seconds,
            )
            link_started = time.perf_counter()
            link_summary = enrich_document_with_pdf_links(document, source_path)
            link_seconds = time.perf_counter() - link_started
            elapsed = time.perf_counter() - start
            stage_timings = dict(result.get("stage_timings") or {})
            stage_timings.update(
                {
                    "segment_build_seconds": round(segment_seconds, 3),
                    "table_second_pass_seconds": round(second_pass_seconds, 3),
                    "link_seconds": round(link_seconds, 3),
                    "extract_total_seconds": round(elapsed, 3),
                }
            )
            result["stage_timings"] = stage_timings

            task_key = str(result.get("_task_key") or "")
            if task_key:
                try:
                    client = self._redis_client()
                    redis_result = {
                        key: value
                        for key, value in result.items()
                        if key != "markdown" and not str(key).startswith("_")
                    }
                    self._redis_hash_set(
                        client,
                        task_key,
                        {
                            "segment_build_seconds": stage_timings["segment_build_seconds"],
                            "link_seconds": stage_timings["link_seconds"],
                            "extract_total_seconds": stage_timings["extract_total_seconds"],
                            "stage_timings": stage_timings,
                            "result_json": redis_result,
                        },
                    )
                except Exception as exc:
                    self.logger.warning(f"写入 PaddleOCR 提取耗时 metadata 失败: task={task_key}, error={exc}")

            batch_elapsed = (result.get("batch_timing") or {}).get("elapsed_seconds") or {}
            self.logger.info(
                f"PaddleOCR-VL 内容提取完成: file={source_path.name}, segments={len(segments)}, "
                f"elapsed={elapsed:.2f}s, parser_output={result.get('output_dir', '')}, "
                f"pdf_links={link_summary.get('internal', 0)}/{link_summary.get('links', 0)}, "
                f"timings={stage_timings}, batch_elapsed={batch_elapsed}"
            )
            visual_count = sum(1 for seg in segments if seg.segment_type in {"chart", "figure", "image_text", "chart_data"})
            self._emit_progress(
                "ocr_done",
                f"OCR extraction completed with {len(segments)} segments and {visual_count} visual assets.",
                45,
                segments=len(segments),
                visual_assets=visual_count,
            )
            return document

        except ContentExtractionError:
            raise
        except Exception as exc:
            self.logger.exception(f"PaddleOCR-VL 内容提取失败: {source_path}")
            raise ContentExtractionError(f"PaddleOCR-VL 内容提取失败: {exc}", file_path=str(source_path)) from exc

        finally:
            if paddle_lifecycle_job_id:
                self._release_paddle_after_document(paddle_lifecycle_job_id)

    def save_markdown(self, document_content: DocumentContent, output_path: str | None = None) -> str:
        """保存供检索和审计使用的最终 Markdown，不包含 OCR 中间产物。"""
        if output_path is None:
            pdf_path = Path(document_content.file_path)
            output_path = str(pdf_path.parent / f"{pdf_path.stem}_extracted.md")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        final_markdown = self._format_final_extracted_markdown(document_content)
        out.write_text(final_markdown, encoding="utf-8")
        _ensure_shared_file(out)
        return str(out)

    def _format_final_extracted_markdown(self, document_content: DocumentContent) -> str:
        """只输出文件名、页码、可恢复的段落 ID 和正文。"""
        pdf_path = Path(document_content.file_path)
        display_segments = [
            seg for seg in document_content.segments
            if getattr(seg, "segment_type", "text") not in {"table_row", "table_cell"}
            and str(getattr(seg, "content", "") or "").strip()
        ]
        lines: list[str] = [f"# {pdf_path.name}"]

        current_page: int | None = None
        page_text_counts: dict[int, int] = {}
        page_table_counts: dict[int, int] = {}

        def page_num(seg: TextSegment) -> int:
            try:
                return max(1, int(getattr(seg, "page_number", 1) or 1))
            except Exception:
                return 1

        display_segments.sort(key=lambda s: (page_num(s), float(getattr(s, "position_y", 0.0) or 0.0), float(getattr(s, "position_x", 0.0) or 0.0)))

        for seg in display_segments:
            page = page_num(seg)
            if page != current_page:
                if current_page is not None:
                    lines.append("")
                lines.append(f"## 第 {page} 页")
                current_page = page

            content = str(getattr(seg, "content", "") or "").strip()
            segment_type = str(getattr(seg, "segment_type", "text") or "text").lower()

            if segment_type in {"chart", "figure", "image_text", "chart_data"}:
                visual = dict(getattr(seg, "structured_data", None) or {})
                public = {k: visual.get(k) for k in (
                    "asset_id", "relative_path", "mime_type", "page_number", "bbox", "caption",
                    "summary", "ocr_text", "chart_data", "confidence", "parser_version"
                )}
                lines.extend([
                    "",
                    f"<!-- visual-asset: {json.dumps(public, ensure_ascii=False)} -->",
                    "",
                    content,
                    "",
                    "---",
                ])
            elif segment_type == "table":
                idx = page_table_counts.get(page, 0)
                page_table_counts[page] = idx + 1
                marker = f"P{page:03d}_T{idx:03d}"
                lines.extend(["", f"**{marker}**", "", content, "", "---"])
            else:
                idx = page_text_counts.get(page, 0)
                page_text_counts[page] = idx + 1
                marker = f"P{page:03d}_S{idx:03d}"
                lines.extend(["", f"**{marker}**", "", content, "", "---"])

        return "\n".join(lines).rstrip() + "\n"

    def _redis_client(self):
        try:
            import redis  # type: ignore
        except Exception as exc:
            raise ContentExtractionError(
                "PaddleOCR Redis 页批次模式需要安装 redis Python 包",
                file_path="",
            ) from exc

        redis_url = os.getenv("PADDLEOCR_TASK_QUEUE_URL", "redis://redis:6379/0").strip()
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=30, socket_connect_timeout=30)
        client.ping()
        return client

    def _redis_hash_set(self, client, key: str, mapping: Dict[str, Any]) -> None:
        safe: Dict[str, str] = {}
        for k, v in mapping.items():
            if isinstance(v, (dict, list)):
                safe[k] = json.dumps(v, ensure_ascii=False)
            else:
                safe[k] = "" if v is None else str(v)
        client.hset(key, mapping=safe)
        client.expire(key, int(os.getenv("PADDLEOCR_TASK_RESULT_TTL", "86400") or "86400"))

    def _acquire_paddleocr_lifecycle_lock(self, client):
        """Fence VLM wake+enqueue against idle-check+sleep across backends."""
        key = (
            os.getenv(
                "PADDLEOCR_VLM_LIFECYCLE_LOCK_KEY",
                "paddleocr:control:vlm-lifecycle",
            ).strip()
            or "paddleocr:control:vlm-lifecycle"
        )
        try:
            wake_timeout = max(
                5.0,
                float(os.getenv("PADDLEOCR_VLM_WAKE_TIMEOUT_SECONDS", "180") or "180"),
            )
            sleep_timeout = max(
                5.0,
                float(os.getenv("PADDLEOCR_VLM_SLEEP_TIMEOUT_SECONDS", "120") or "120"),
            )
            # Redis locks expire even while the holder is still working. Keep
            # this lease longer than either lifecycle API call plus an enqueue
            # margin so another producer/sleeper cannot enter mid-operation.
            # Wake may spend one full timeout in POST /wake_up and another in
            # the readiness polling loop (plus state/health probes).
            minimum_lock_timeout = 2.0 * max(wake_timeout, sleep_timeout) + 120.0
            lock_timeout = max(
                30.0,
                minimum_lock_timeout,
                float(
                    os.getenv(
                        "PADDLEOCR_VLM_LIFECYCLE_LOCK_TIMEOUT_SECONDS",
                        "360",
                    )
                    or "360"
                ),
            )
            blocking_timeout = max(
                5.0,
                lock_timeout,
                float(
                    os.getenv(
                        "PADDLEOCR_VLM_LIFECYCLE_LOCK_WAIT_SECONDS",
                        "360",
                    )
                    or "360"
                ),
            )
        except (TypeError, ValueError):
            lock_timeout = blocking_timeout = 360.0
        lock = client.lock(
            key,
            timeout=lock_timeout,
            blocking_timeout=blocking_timeout,
        )
        if not lock.acquire(blocking=True):
            raise ContentExtractionError(
                "Timed out waiting for the PaddleOCR VLM lifecycle lock.",
                file_path="",
            )
        return lock

    def _release_paddleocr_lifecycle_lock(self, lock) -> None:
        try:
            lock.release()
        except Exception as exc:
            self.logger.warning(f"Failed to release PaddleOCR VLM lifecycle lock: {exc}")

    def _submit_paddleocr_queue_entries(
        self,
        client,
        queue_name: str,
        entries: Sequence[Tuple[str, Dict[str, Any], Dict[str, Any]]],
    ) -> None:
        """Wake and enqueue as one lifecycle-fenced producer operation."""
        lifecycle_lock = self._acquire_paddleocr_lifecycle_lock(client)
        try:
            # This is the sole producer wake point. PDF splitting happens
            # before this lock and does not require the VLM to be resident.
            self._wake_paddleocr_vlm()
            for batch_key, batch_state, payload in entries:
                self._redis_hash_set(client, batch_key, batch_state)
                client.rpush(queue_name, json.dumps(payload, ensure_ascii=False))
        finally:
            self._release_paddleocr_lifecycle_lock(lifecycle_lock)

    @staticmethod
    def _paddleocr_vlm_control_base_url() -> str:
        raw = (
            os.getenv("PADDLEOCR_VLM_CONTROL_URL")
            or os.getenv("PADDLEOCR_VL_REC_SERVER_URL")
            or "http://paddleocr-vlm-server:8118"
        ).strip()
        base = raw.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base.rstrip("/")

    def _paddleocr_vlm_request(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
    ) -> Dict[str, Any]:
        url = f"{self._paddleocr_vlm_control_base_url()}/{path.lstrip('/')}"
        request = Request(url, method=method.upper())
        try:
            with urlopen(request, timeout=max(1.0, timeout)) as response:
                payload = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Paddle vLLM {method} {path} failed: HTTP {exc.code}, {body[:500]}") from exc
        if not payload:
            return {}
        try:
            data = json.loads(payload)
        except Exception:
            return {"body": payload}
        return data if isinstance(data, dict) else {"result": data}

    def _paddleocr_vlm_sleep_state(self, *, timeout: float) -> Optional[bool]:
        try:
            payload = self._paddleocr_vlm_request("GET", "/is_sleeping", timeout=timeout)
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        value = payload.get("is_sleeping")
        return value if isinstance(value, bool) else None

    def _wake_paddleocr_vlm(self) -> None:
        if not _env_bool("PADDLEOCR_VLM_SLEEP_ENABLED", False):
            return
        timeout = max(
            5.0,
            float(os.getenv("PADDLEOCR_VLM_WAKE_TIMEOUT_SECONDS", "180") or "180"),
        )
        state = self._paddleocr_vlm_sleep_state(timeout=min(10.0, timeout))
        if state is None:
            # Compatibility path for a server that has not enabled the internal
            # sleep routes yet. A healthy server is already usable.
            self._paddleocr_vlm_request("GET", "/health", timeout=min(10.0, timeout))
            self.logger.warning("Paddle vLLM sleep API is unavailable; continuing with an awake server")
            return
        if not state:
            return

        self.logger.info("Waking Paddle vLLM before OCR batch submission")
        self._paddleocr_vlm_request("POST", "/wake_up", timeout=timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._paddleocr_vlm_sleep_state(timeout=min(10.0, timeout))
            if state is False:
                self._paddleocr_vlm_request("GET", "/health", timeout=min(10.0, timeout))
                self.logger.info("Paddle vLLM wake completed")
                return
            time.sleep(0.5)
        raise ContentExtractionError(
            f"Paddle vLLM wake timed out after {timeout:.0f}s",
            file_path="",
        )

    def _request_paddle_worker_release(self, job_id: str) -> bool:
        if not _env_bool("PADDLEOCR_RELEASE_AFTER_DOCUMENT", False):
            return False
        try:
            client = self._redis_client()
            request_key = os.getenv(
                "PADDLEOCR_RELEASE_REQUEST_KEY",
                "paddleocr:control:release",
            ).strip()
            request_id = f"{job_id}:{uuid.uuid4().hex}"
            task_key = f"{os.getenv('PADDLEOCR_TASK_KEY_PREFIX', 'paddleocr:task').strip()}:{job_id}"
            pipe = client.pipeline()
            pipe.delete(request_key)
            pipe.hset(
                request_key,
                mapping={
                    "request_id": request_id,
                    "job_id": job_id,
                    "requested_at": datetime.now().isoformat(),
                },
            )
            pipe.expire(
                request_key,
                int(os.getenv("PADDLEOCR_TASK_RESULT_TTL", "86400") or "86400"),
            )
            pipe.execute()
            self._redis_hash_set(
                client,
                task_key,
                {
                    "worker_release_request_id": request_id,
                    "worker_release_requested_at": datetime.now().isoformat(),
                },
            )

            worker_ids = [
                item.strip()
                for item in os.getenv("PADDLEOCR_WORKER_IDS", "").split(",")
                if item.strip()
            ]
            if not worker_ids:
                return True

            ack_timeout = max(
                1.0,
                float(
                    os.getenv("PADDLEOCR_WORKER_RELEASE_ACK_TIMEOUT_SECONDS", "30")
                    or "30"
                ),
            )
            deadline = time.monotonic() + ack_timeout
            while time.monotonic() < deadline:
                state = client.hgetall(request_key) or {}
                acked = [
                    worker_id
                    for worker_id in worker_ids
                    if state.get(f"ack:{worker_id}") == request_id
                ]
                if len(acked) == len(worker_ids):
                    self._redis_hash_set(
                        client,
                        task_key,
                        {
                            "worker_release_completed_at": datetime.now().isoformat(),
                            "worker_release_acks": acked,
                        },
                    )
                    self.logger.info(
                        f"PaddleOCR worker pipelines released after job={job_id}: {acked}"
                    )
                    return True
                time.sleep(0.25)

            self.logger.warning(
                f"Timed out waiting for PaddleOCR worker release acknowledgements: "
                f"job={job_id}, workers={worker_ids}"
            )
            return False
        except Exception as exc:
            self.logger.warning(
                f"Failed to request PaddleOCR worker release: job={job_id}, error={exc}"
            )
            return False

    def _sleep_paddleocr_vlm(self, job_id: str) -> bool:
        if not _env_bool("PADDLEOCR_VLM_SLEEP_ENABLED", False):
            return False
        lifecycle_lock = None
        try:
            client = self._redis_client()
            lifecycle_lock = self._acquire_paddleocr_lifecycle_lock(client)
            queue_name = (
                os.getenv("PADDLEOCR_TASK_QUEUE_NAME", "paddleocr:parse").strip()
                or "paddleocr:parse"
            )
            processing_queue = (
                os.getenv(
                    "PADDLEOCR_PROCESSING_QUEUE_NAME",
                    f"{queue_name}:processing",
                ).strip()
                or f"{queue_name}:processing"
            )
            lease_key = (
                os.getenv(
                    "PADDLEOCR_PROCESSING_LEASE_KEY",
                    f"{processing_queue}:leases",
                ).strip()
                or f"{processing_queue}:leases"
            )
            pipe = client.pipeline()
            pipe.llen(queue_name)
            pipe.llen(processing_queue)
            pipe.zcount(lease_key, time.time(), "+inf")
            queued_count, processing_count, active_lease_count = [
                int(value or 0) for value in pipe.execute()
            ]
            if queued_count or processing_count or active_lease_count:
                self.logger.info(
                    "Keeping Paddle vLLM awake because OCR work remains active: "
                    f"job={job_id}, queued={queued_count}, "
                    f"processing={processing_count}, leases={active_lease_count}"
                )
                return False

            timeout = max(
                5.0,
                float(os.getenv("PADDLEOCR_VLM_SLEEP_TIMEOUT_SECONDS", "120") or "120"),
            )
            state = self._paddleocr_vlm_sleep_state(timeout=min(10.0, timeout))
            if state is None:
                self.logger.warning("Paddle vLLM sleep API is unavailable; model remains loaded")
                return False
            if state:
                return True

            level = int(os.getenv("PADDLEOCR_VLM_SLEEP_LEVEL", "1") or "1")
            level = 1 if level not in {1, 2} else level
            self.logger.info(f"Sleeping Paddle vLLM after OCR job={job_id}, level={level}")
            self._paddleocr_vlm_request(
                "POST",
                f"/sleep?level={level}",
                timeout=timeout,
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._paddleocr_vlm_sleep_state(timeout=min(10.0, timeout)) is True:
                    self.logger.info(f"Paddle vLLM sleep completed after OCR job={job_id}")
                    return True
                time.sleep(0.5)
            self.logger.warning(
                f"Paddle vLLM sleep did not complete within {timeout:.0f}s: job={job_id}"
            )
        except Exception as exc:
            self.logger.warning(f"Failed to sleep Paddle vLLM after job={job_id}: {exc}")
        finally:
            if lifecycle_lock is not None:
                self._release_paddleocr_lifecycle_lock(lifecycle_lock)
        return False

    def _release_paddle_after_document(self, job_id: str) -> None:
        workers_released = self._request_paddle_worker_release(job_id)
        # Do not sleep the shared VLM while a worker may still be inside a VLM
        # request. A missed acknowledgement keeps it awake as a safe fallback.
        if workers_released:
            self._sleep_paddleocr_vlm(job_id)

    def _remove_queued_batches_for_job(self, client, queue_name: str, job_id: str) -> int:
        """从 Redis 队列中移除同一 OCR job 的未开始 batch，避免失败后继续消费。"""
        try:
            items = client.lrange(queue_name, 0, -1) or []
            if not items:
                return 0
            targets = []
            for raw_item in items:
                try:
                    payload = json.loads(raw_item)
                    if str(payload.get("job_id") or "") == job_id:
                        targets.append(raw_item)
                        continue
                except Exception:
                    pass
            removed = 0
            if targets:
                pipe = client.pipeline()
                for raw_item in targets:
                    # LREM is atomic per item and never replaces the shared
                    # queue, so concurrent uploads cannot be lost here.
                    pipe.lrem(queue_name, 0, raw_item)
                results = pipe.execute()
                removed = sum(int(value or 0) for value in results)
                self.logger.warning(
                    f"已从 PaddleOCR 队列移除失败 job 的剩余 batch: job_id={job_id}, removed={removed}"
                )
            return removed
        except Exception as exc:
            self.logger.warning(f"清理失败 job 的 Redis 队列项失败: job_id={job_id}, error={exc}")
            return 0

    def _get_paddleocr_page_batch_size(self) -> int:
        """读取 PDF 拆分页数。

        注意：PDF 是在 backend 中拆分的，不是在 PaddleOCR worker 中拆分。
        因此 PADDLEOCR_PAGE_BATCH_SIZE 必须出现在 backend.environment 中。
        默认值为 7，配置只在 backend.environment 中设置。
        """
        raw = os.getenv("PADDLEOCR_PAGE_BATCH_SIZE", "7")
        try:
            value = int(str(raw).strip())
        except Exception:
            self.logger.warning(
                f"PADDLEOCR_PAGE_BATCH_SIZE={raw!r} 无法解析为整数，使用默认值 7"
            )
            value = 7
        if value < 1:
            self.logger.warning(
                f"PADDLEOCR_PAGE_BATCH_SIZE={raw!r} 小于 1，使用 1"
            )
            value = 1
        self.logger.info(
            f"PaddleOCR-VL PDF 拆分 batch size 生效: PADDLEOCR_PAGE_BATCH_SIZE={raw!r}, effective={value}"
        )
        return value

    def _split_pdf_for_page_batch_queue(
        self,
        source_path: Path,
        job_id: str,
        batch_size: int,
        page_numbers: Optional[Sequence[int]] = None,
        page_options: Optional[Dict[int, Dict[str, Any]]] = None,
        render_zoom: float = 1.0,
    ) -> tuple[list[dict], int, Path]:
        try:
            import fitz  # type: ignore
        except Exception as exc:
            raise ContentExtractionError(
                "页级 batch 队列需要 backend 安装 PyMuPDF",
                file_path=str(source_path),
            ) from exc

        work_root = Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs"))
        batch_dir = work_root / job_id / "batches"
        if batch_dir.exists():
            import shutil

            shutil.rmtree(batch_dir, ignore_errors=True)
        _ensure_shared_writable_dir(batch_dir)

        document = fitz.open(str(source_path))
        try:
            total_pages = len(document)
            if total_pages <= 0:
                raise ContentExtractionError("PDF 没有可解析页", file_path=str(source_path))

            units: list[dict] = []
            selected_ranges = _selected_page_batch_ranges(
                total_pages,
                batch_size,
                page_numbers,
                page_options,
            )
            for unit_index, (start_page, end_page) in enumerate(selected_ranges, 1):
                requested_zoom = max(1.0, float(render_zoom or 1.0))
                effective_zoom = requested_zoom
                page = None
                if requested_zoom > 1.0 and start_page == end_page:
                    page = document.load_page(start_page - 1)
                    try:
                        configured_render_pixels = max(
                            1,
                            int(
                                os.getenv(
                                    "REPORT_TABLE_SECOND_PASS_MAX_RENDER_PIXELS",
                                    "4014080",
                                )
                                or "4014080"
                            ),
                        )
                    except (TypeError, ValueError, OverflowError):
                        configured_render_pixels = 4_014_080
                    try:
                        prediction_render_pixels = max(
                            1,
                            int(
                                ((page_options or {}).get(start_page) or {}).get(
                                    "max_pixels",
                                    4_014_080,
                                )
                            ),
                        )
                    except (TypeError, ValueError, OverflowError):
                        prediction_render_pixels = 4_014_080
                    max_render_pixels = min(
                        configured_render_pixels,
                        prediction_render_pixels,
                        4_014_080,
                    )
                    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
                    effective_zoom = min(
                        requested_zoom,
                        max(1.0, math.sqrt(max_render_pixels / page_area)),
                    )
                raster_pass = effective_zoom > 1.0 and start_page == end_page
                suffix = ".png" if raster_pass else ".pdf"
                batch_path = batch_dir / f"pages_{start_page:04d}_{end_page:04d}{suffix}"
                # Preserve a real output suffix so image/PDF writers can infer
                # their format while the final rename remains atomic.
                tmp_path = batch_path.with_name(f"{batch_path.stem}.tmp{suffix}")
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

                if raster_pass:
                    # The table repair pass intentionally feeds Paddle a
                    # high-resolution page image.  Merely recording a zoom in
                    # metadata would leave the first-pass raster unchanged.
                    page = page or document.load_page(start_page - 1)
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(effective_zoom, effective_zoom),
                        alpha=False,
                    )
                    pixmap.save(str(tmp_path))
                else:
                    batch_document = fitz.open()
                    try:
                        batch_document.insert_pdf(
                            document,
                            from_page=start_page - 1,
                            to_page=end_page - 1,
                            # Link topology is read from the original PDF after OCR.
                            # Skipping link-object copying keeps temporary batch creation fast.
                            links=False,
                            annots=True,
                        )
                        batch_document.save(str(tmp_path), garbage=0, deflate=False, clean=False)
                    finally:
                        batch_document.close()

                # PyMuPDF closes the file after save; fsync before the atomic rename.
                with tmp_path.open("r+b") as f:
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                os.replace(tmp_path, batch_path)
                _ensure_shared_file(batch_path)
                ready_path = _write_shared_ready_marker(
                    batch_path,
                    payload={
                        "job_id": job_id,
                        "unit_index": unit_index,
                        "start_page": start_page,
                        "end_page": end_page,
                        "total_pages": total_pages,
                        "render_zoom": effective_zoom,
                        "requested_render_zoom": requested_zoom,
                    },
                    sync_parent=False,
                )
                units.append(
                    {
                        "unit_index": unit_index,
                        "batch_id": f"batch_{unit_index:04d}",
                        "start_page": start_page,
                        "end_page": end_page,
                        "input_path": str(batch_path),
                        "ready_path": str(ready_path),
                        "prediction_options": dict((page_options or {}).get(start_page, {})),
                        "render_zoom": effective_zoom,
                        "requested_render_zoom": requested_zoom,
                    }
                )

            # No Redis task is submitted until every batch is ready, so one final
            # directory fsync durably records all PDF and marker renames.
            if units:
                _fsync_parent_dir(Path(units[-1]["ready_path"]))
        finally:
            document.close()

        # 这里先在 backend 容器内做一次可见性校验，再入 Redis 队列。
        # 这不能替代 worker 侧等待，但能提前发现目录/权限/挂载异常。
        visibility_wait = float(os.getenv("PADDLEOCR_SPLIT_VISIBILITY_WAIT_SECONDS", "60") or "60")
        deadline = time.time() + max(0.0, visibility_wait)
        while True:
            missing = []
            for unit in units:
                p = Path(unit["input_path"])
                m = Path(unit["ready_path"])
                try:
                    if not (p.exists() and p.stat().st_size > 0 and m.exists() and m.stat().st_size > 0):
                        missing.append(str(p))
                except Exception:
                    missing.append(str(p))
            if not missing:
                break
            if time.time() >= deadline:
                raise ContentExtractionError(
                    f"PaddleOCR batch 文件写入后不可见，无法入队: missing={missing[:3]}",
                    file_path=str(source_path),
                )
            time.sleep(0.25)

        return units, total_pages, batch_dir

    def _run_paddleocr_vl_page_batch_queue(
        self,
        source_path: Path,
        *,
        page_analysis: Any = None,
        selected_page_numbers: Optional[Sequence[int]] = None,
        prediction_options_by_page: Optional[Dict[int, Dict[str, Any]]] = None,
        partial_result: bool = False,
        promote_visuals: bool = True,
        parse_pass: int = 1,
        render_zoom: float = 1.0,
        emit_progress: bool = True,
        release_after_document: bool = True,
    ) -> Dict[str, Any]:
        routes, _native_pages = _adaptive_analysis_maps(page_analysis)
        total_pages = int(getattr(page_analysis, "total_pages", 0) or 0) if page_analysis is not None else 0
        if selected_page_numbers is None and total_pages > 0 and len(routes) == total_pages and all(
            routes.get(page) == "native" for page in range(1, total_pages + 1)
        ):
            self.logger.info(f"Skipping PaddleOCR for all-native PDF: file={source_path.name}, pages={total_pages}")
            result = _native_only_result(page_analysis)
            if source_path.is_file():
                write_empty_visual_manifest(source_path, _native_manifest_pages(page_analysis))
            return result

        job_id = f"parse_{uuid.uuid4().hex}"
        completed = False
        try:
            if page_analysis is None and selected_page_numbers is None:
                if emit_progress:
                    # Preserve the historical call shape for subclasses and
                    # tests that override the active queue method.
                    result = self._run_paddleocr_vl_page_batch_queue_active(
                        source_path,
                        job_id,
                    )
                else:
                    result = self._run_paddleocr_vl_page_batch_queue_active(
                        source_path,
                        job_id,
                        emit_progress=False,
                    )
            else:
                result = self._run_paddleocr_vl_page_batch_queue_active(
                    source_path,
                    job_id,
                    page_analysis=page_analysis,
                    selected_page_numbers=selected_page_numbers,
                    prediction_options_by_page=prediction_options_by_page,
                    partial_result=partial_result,
                    promote_visuals=promote_visuals,
                    parse_pass=parse_pass,
                    render_zoom=render_zoom,
                    emit_progress=emit_progress,
                )
            completed = True
            if not release_after_document:
                result["_paddle_lifecycle_job_id"] = job_id
            return result
        except Exception:
            if not _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False):
                _cleanup_path_tree(
                    Path(
                        os.getenv(
                            "PADDLEOCR_OUTPUT_DIR",
                            "/workspace/uploads/paddleocr_vl_output",
                        )
                    )
                    / job_id,
                    label="failed OCR output",
                )
                _cleanup_path_tree(
                    Path(
                        os.getenv(
                            "PADDLEOCR_JOB_WORK_DIR",
                            "/workspace/uploads/paddleocr_vl_jobs",
                        )
                    )
                    / job_id,
                    label="failed OCR split workspace",
                )
            raise
        finally:
            # Failed attempts always release. Successful extraction may defer
            # release until its optional table repair pass has completed.
            if release_after_document or not completed:
                self._release_paddle_after_document(job_id)

    def _run_paddleocr_vl_page_batch_queue_active(
        self,
        source_path: Path,
        job_id: str,
        *,
        page_analysis: Any = None,
        selected_page_numbers: Optional[Sequence[int]] = None,
        prediction_options_by_page: Optional[Dict[int, Dict[str, Any]]] = None,
        partial_result: bool = False,
        promote_visuals: bool = True,
        parse_pass: int = 1,
        render_zoom: float = 1.0,
        emit_progress: bool = True,
    ) -> Dict[str, Any]:
        queue_run_started = time.perf_counter()

        def emit_queue_progress(
            stage: str,
            message: str,
            progress: Optional[float] = None,
            **extra: Any,
        ) -> None:
            if not emit_progress:
                return
            if partial_result:
                raw_progress = float(progress if progress is not None else 12.0)
                fraction = max(0.0, min(1.0, (raw_progress - 12.0) / 33.0))
                self._emit_progress(
                    "table_second_pass",
                    f"Table repair: {message}",
                    44.0 + 0.9 * fraction,
                    parse_pass=max(1, int(parse_pass or 1)),
                    **extra,
                )
                return
            self._emit_progress(stage, message, progress, **extra)

        client = self._redis_client()

        queue_name = (
            os.getenv("PADDLEOCR_TASK_QUEUE_NAME", "paddleocr:parse").strip()
            or "paddleocr:parse"
        )
        key_prefix = os.getenv("PADDLEOCR_TASK_KEY_PREFIX", "paddleocr:task").strip()
        timeout_env = (
            "REPORT_TABLE_SECOND_PASS_TIMEOUT_SECONDS"
            if partial_result
            else "PADDLEOCR_VL_TIMEOUT"
        )
        timeout_default = "1800" if partial_result else "14400"
        timeout = max(60, int(os.getenv(timeout_env, timeout_default) or timeout_default))
        poll_interval = float(os.getenv("PADDLEOCR_TASK_POLL_INTERVAL", "2.0") or "2.0")
        batch_size = self._get_paddleocr_page_batch_size()
        if partial_result:
            # A repair pass is intentionally page-isolated.  One pathological
            # table must not make a neighbouring candidate page fail or share a
            # token budget with it.
            batch_size = 1
        output_root = Path(os.getenv("PADDLEOCR_OUTPUT_DIR", "/workspace/uploads/paddleocr_vl_output"))

        task_key = f"{key_prefix}:{job_id}"
        output_dir = output_root / job_id
        _ensure_shared_writable_dir(output_root)
        _ensure_shared_writable_dir(output_dir)
        _ensure_shared_writable_dir(output_dir / "batches")

        page_routes, native_page_markdown = _adaptive_analysis_maps(page_analysis)
        page_prediction_options = _adaptive_prediction_options_by_page(page_analysis)
        analysis_total_pages = int(getattr(page_analysis, "total_pages", 0) or 0) if page_analysis is not None else 0
        analysis_is_complete = (
            analysis_total_pages > 0
            and set(page_routes) == set(range(1, analysis_total_pages + 1))
            and all(
                page_routes.get(page) != "native" or page in native_page_markdown
                for page in range(1, analysis_total_pages + 1)
            )
        )
        if not analysis_is_complete:
            # Never let a partial preflight influence the full-OCR fallback.
            # Retaining a few native routes here can over-count progress and
            # replace complete OCR pages with incomplete native content.
            page_routes = {}
            native_page_markdown = {}
            page_prediction_options = {}
        selected_ocr_pages: Optional[List[int]] = None
        if analysis_is_complete:
            selected_ocr_pages = [
                page
                for page in range(1, analysis_total_pages + 1)
                if page_routes.get(page) != "native"
            ]
        if selected_page_numbers is not None:
            selected_ocr_pages = sorted(
                {
                    int(page)
                    for page in selected_page_numbers
                    if int(page) >= 1
                }
            )
            if not selected_ocr_pages:
                raise ContentExtractionError(
                    "Selective PaddleOCR pass has no valid pages.",
                    file_path=str(source_path),
                )
            # A partial repair result contains only OCR output for the selected
            # pages.  It must never be merged with adaptive native content here.
            page_routes = {}
            native_page_markdown = {}
            page_prediction_options = {
                int(page): dict(options or {})
                for page, options in (prediction_options_by_page or {}).items()
                if int(page) in selected_ocr_pages
            }
            analysis_is_complete = False

        split_started = time.perf_counter()
        units, total_pages, batch_dir = self._split_pdf_for_page_batch_queue(
            source_path,
            job_id,
            batch_size,
            page_numbers=selected_ocr_pages,
            page_options=(
                page_prediction_options
                if analysis_is_complete or selected_page_numbers is not None
                else None
            ),
            render_zoom=render_zoom,
        )
        if analysis_is_complete and analysis_total_pages != total_pages:
            self.logger.warning(
                f"Adaptive page count changed during split; reverting to full OCR: "
                f"analysis={analysis_total_pages}, pdf={total_pages}"
            )
            page_routes = {}
            native_page_markdown = {}
            page_prediction_options = {}
            selected_ocr_pages = None
            units, total_pages, batch_dir = self._split_pdf_for_page_batch_queue(
                source_path,
                job_id,
                batch_size,
                render_zoom=render_zoom,
            )
        split_seconds = time.perf_counter() - split_started
        total_units = len(units)
        expected_ranges = [
            (int(unit["start_page"]), int(unit["end_page"]))
            for unit in units
        ]
        native_page_count = sum(1 for route in page_routes.values() if route == "native")
        ocr_page_count = sum(end - start + 1 for start, end in expected_ranges)
        if total_units == 0 and native_page_count == total_pages:
            result = _native_only_result(page_analysis)
            if source_path.is_file():
                write_empty_visual_manifest(source_path, _native_manifest_pages(page_analysis))
            result["stage_timings"] = {
                "split_seconds": round(split_seconds, 3),
                "queue_submit_seconds": 0.0,
                "ocr_queue_seconds": 0.0,
                "merge_seconds": 0.0,
                "queue_total_seconds": round(time.perf_counter() - queue_run_started, 3),
            }
            _cleanup_path_tree(
                Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs")) / job_id,
                label="all-native batch workspace",
            )
            return result

        self.logger.info(
            f"提交 PaddleOCR-VL 页级 batch 队列任务: job_id={job_id}, file={source_path.name}, "
            f"pages={total_pages}, units={total_units}, batch_size={batch_size}, queue={queue_name}, "
            f"native_pages={native_page_count}, ocr_pages={ocr_page_count}, "
            f"split_seconds={split_seconds:.3f}"
        )
        emit_queue_progress(
            "ocr_queued",
            f"PaddleOCR queued: {ocr_page_count}/{total_pages} pages in {total_units} batch(es); "
            f"{native_page_count} native page(s) bypassed OCR.",
            12,
            paddle_progress={
                "paddle_job_id": job_id,
                "total_pages": total_pages,
                "pages_done": native_page_count,
                "pages_success": native_page_count,
                "pages_failed": 0,
                "total_units": total_units,
                "units_done": 0,
                "units_success": 0,
                "units_failed": 0,
                "units_running": 0,
                "units_queued": total_units,
                "page_batch_size": batch_size,
                "parse_pass": max(1, int(parse_pass or 1)),
                "render_zoom": float(render_zoom or 1.0),
                "native_pages": native_page_count,
                "ocr_pages": ocr_page_count,
                "running_batches": [],
            },
            paddle_job_id=job_id,
            total_pages=total_pages,
            total_units=total_units,
            page_batch_size=batch_size,
        )

        self._redis_hash_set(
            client,
            task_key,
            {
                "status": "running",
                "stage": "batch_queued",
                "queue_granularity": "page-batch",
                "filename": source_path.name,
                "input_path": str(source_path),
                "batch_dir": str(batch_dir),
                "output_dir": str(output_dir),
                "created_at": datetime.now().isoformat(),
                "total_pages": total_pages,
                "total_units": total_units,
                "units_done": 0,
                "page_batch_size": batch_size,
                "native_pages": native_page_count,
                "ocr_pages": ocr_page_count,
                "split_seconds": round(split_seconds, 3),
            },
        )

        queue_submit_started = time.perf_counter()
        ocr_queue_started = queue_submit_started
        queue_entries: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        for unit in units:
            unit_index = int(unit["unit_index"])
            batch_key = f"{task_key}:batch:{unit_index:04d}"
            payload = {
                "task_type": "page_batch",
                "job_id": job_id,
                "filename": source_path.name,
                "unit_index": unit_index,
                "batch_id": unit["batch_id"],
                "total_units": total_units,
                "start_page": unit["start_page"],
                "end_page": unit["end_page"],
                "total_pages": total_pages,
                "input_path": unit["input_path"],
                "ready_path": unit.get("ready_path", ""),
                "prediction_options": dict(unit.get("prediction_options") or {}),
                "parse_pass": max(1, int(parse_pass or 1)),
                "render_zoom": float(unit.get("render_zoom") or render_zoom or 1.0),
                "requested_render_zoom": float(
                    unit.get("requested_render_zoom") or render_zoom or 1.0
                ),
                "created_at": datetime.now().isoformat(),
            }
            batch_state = {
                "status": "queued",
                "stage": "queued",
                "job_id": job_id,
                "filename": source_path.name,
                "unit_index": unit_index,
                "total_units": total_units,
                "start_page": unit["start_page"],
                "end_page": unit["end_page"],
                "input_path": unit["input_path"],
                "ready_path": unit.get("ready_path", ""),
                "prediction_options": dict(unit.get("prediction_options") or {}),
                "parse_pass": max(1, int(parse_pass or 1)),
                "render_zoom": float(unit.get("render_zoom") or render_zoom or 1.0),
                "requested_render_zoom": float(
                    unit.get("requested_render_zoom") or render_zoom or 1.0
                ),
                "created_at": datetime.now().isoformat(),
            }
            queue_entries.append((batch_key, batch_state, payload))

        self._submit_paddleocr_queue_entries(
            client,
            queue_name,
            queue_entries,
        )

        queue_submit_seconds = time.perf_counter() - queue_submit_started
        self._redis_hash_set(
            client,
            task_key,
            {
                "queue_submit_seconds": round(queue_submit_seconds, 3),
                "stage_timings": {
                    "split_seconds": round(split_seconds, 3),
                    "queue_submit_seconds": round(queue_submit_seconds, 3),
                },
            },
        )

        deadline = time.time() + timeout
        last_progress_signature = None
        # 即使 batch 状态没有变化，也要定时推送“仍在处理”的进度。
        # 否则单个 PaddleOCR batch 处理较久时，前端会看起来像卡住。
        progress_emit_interval = float(os.getenv("PADDLEOCR_PROGRESS_EMIT_INTERVAL", "5.0") or "5.0")
        last_progress_emit_ts = 0.0
        progress_log_interval = float(os.getenv("APP_PROGRESS_LOG_INTERVAL_SECONDS", "30") or "30")
        last_progress_log_ts = 0.0
        last_progress_log_bucket = -1
        batch_states: list[dict] = []
        while time.time() < deadline:
            batch_states = [client.hgetall(f"{task_key}:batch:{i:04d}") for i in range(1, total_units + 1)]
            statuses = [str(s.get("status", "queued")).lower() for s in batch_states]
            done_count = sum(1 for s in statuses if s in {"success", "completed"})
            failed_count = sum(1 for s in statuses if s == "failed")
            running_count = sum(1 for s in statuses if s in {"running", "predict", "processing"})
            queued_count = sum(1 for s in statuses if s in {"queued", "waiting", "waiting_model"})

            def _int_field(item: Dict[str, Any], name: str, default: int = 0) -> int:
                try:
                    return int(item.get(name) or default)
                except Exception:
                    return default

            ocr_pages_success = sum(
                max(0, _int_field(st, "end_page") - _int_field(st, "start_page") + 1)
                for st, status in zip(batch_states, statuses)
                if status in {"success", "completed"}
            )
            pages_failed = sum(
                max(0, _int_field(st, "end_page") - _int_field(st, "start_page") + 1)
                for st, status in zip(batch_states, statuses)
                if status == "failed"
            )
            pages_success = native_page_count + ocr_pages_success
            pages_done = pages_success + pages_failed
            running_batches = []
            for st, status in zip(batch_states, statuses):
                if status in {"running", "predict", "processing"}:
                    running_batches.append(
                        {
                            "unit_index": _int_field(st, "unit_index"),
                            "start_page": _int_field(st, "start_page"),
                            "end_page": _int_field(st, "end_page"),
                            "worker_id": st.get("worker_id") or "",
                        }
                    )
            running_signature = tuple(
                (b.get("unit_index"), b.get("start_page"), b.get("end_page"), b.get("worker_id"))
                for b in running_batches
            )
            progress_signature = (done_count, failed_count, running_count, queued_count, running_signature)

            now_ts = time.time()
            should_emit_progress = (
                progress_signature != last_progress_signature
                or (progress_emit_interval > 0 and now_ts - last_progress_emit_ts >= progress_emit_interval)
            )

            if should_emit_progress:
                running_pages_text = ", ".join(
                    f"p{b['start_page']}-{b['end_page']}" if b["start_page"] != b["end_page"] else f"p{b['start_page']}"
                    for b in running_batches[:3]
                ) or "-"
                completed_count = done_count + failed_count
                progress_bucket = min(10, int((completed_count * 10) / max(1, total_units)))
                should_log_progress = (
                    progress_bucket > last_progress_log_bucket
                    or progress_log_interval <= 0
                    or now_ts - last_progress_log_ts >= progress_log_interval
                )
                if should_log_progress:
                    self.logger.info(
                        f"PaddleOCR-VL progress job_id={job_id} completed={completed_count}/{total_units} "
                        f"failed={failed_count} running={running_count} queued={queued_count} "
                        f"pages={pages_done}/{total_pages} running_pages={running_pages_text}"
                    )
                    last_progress_log_bucket = progress_bucket
                    last_progress_log_ts = now_ts
                last_progress_signature = progress_signature
                last_progress_emit_ts = now_ts
                self._redis_hash_set(
                    client,
                    task_key,
                    {
                        "status": "running",
                        "stage": "batch_processing",
                        "units_done": done_count + failed_count,
                        "units_success": done_count,
                        "units_failed": failed_count,
                        "units_running": running_count,
                        "units_queued": queued_count,
                        "pages_done": pages_done,
                        "pages_success": pages_success,
                        "pages_failed": pages_failed,
                        "updated_at": datetime.now().isoformat(),
                    },
                )
                ocr_progress = 12 + (33 * ((done_count + failed_count) / max(1, total_units)))
                running_text = ""
                if running_batches:
                    shown = ", ".join(
                        f"p{b['start_page']}-{b['end_page']}" if b["start_page"] != b["end_page"] else f"p{b['start_page']}"
                        for b in running_batches[:3]
                    )
                    running_text = f" Running: {shown}."
                emit_queue_progress(
                    "ocr_batch_processing",
                    f"PaddleOCR progress: {done_count + failed_count}/{total_units} batches, {pages_done}/{total_pages} pages done.{running_text}",
                    ocr_progress,
                    paddle_progress={
                        "paddle_job_id": job_id,
                        "total_pages": total_pages,
                        "pages_done": pages_done,
                        "pages_success": pages_success,
                        "pages_failed": pages_failed,
                        "total_units": total_units,
                        "units_done": done_count + failed_count,
                        "units_success": done_count,
                        "units_failed": failed_count,
                        "units_running": running_count,
                        "units_queued": queued_count,
                        "page_batch_size": batch_size,
                        "running_batches": running_batches,
                        "updated_at": datetime.now().isoformat(),
                    },
                    paddle_job_id=job_id,
                    units_done=done_count + failed_count,
                    units_success=done_count,
                    units_failed=failed_count,
                    units_running=running_count,
                    units_queued=queued_count,
                    pages_done=pages_done,
                    pages_success=pages_success,
                    pages_failed=pages_failed,
                    total_pages=total_pages,
                    total_units=total_units,
                    running_batches=running_batches,
                )

            if failed_count and not partial_result:
                errors = [s.get("error", "unknown worker error") for s in batch_states if str(s.get("status", "")).lower() == "failed"]
                error_text = errors[0] if errors else "unknown worker error"
                ocr_queue_seconds = time.perf_counter() - ocr_queue_started
                batch_timing = _batch_timing_summary(batch_states)
                self._redis_hash_set(
                    client,
                    task_key,
                    {
                        "status": "failed",
                        "stage": "failed",
                        "cancel_requested": "1",
                        "failed_at": datetime.now().isoformat(),
                        "error": error_text,
                        "ocr_queue_seconds": round(ocr_queue_seconds, 3),
                        "batch_timing": batch_timing,
                    },
                )
                self._remove_queued_batches_for_job(client, queue_name, job_id)
                # 给当前 worker 一点时间退出/释放，避免 backend 立刻删除目录后 worker 又开始下一批。
                if running_count:
                    time.sleep(float(os.getenv("PADDLEOCR_FAILURE_CLEANUP_DELAY", "3.0") or "3.0"))
                if not _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False):
                    _cleanup_path_tree(output_dir, label="失败任务 worker batch 输出")
                    _cleanup_path_tree(Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs")) / job_id, label="失败任务拆页 PDF")
                raise ContentExtractionError(
                    f"PaddleOCR-VL 页级 batch 解析失败: job_id={job_id}, error={error_text}",
                    file_path=str(source_path),
                )

            if done_count + failed_count >= total_units:
                emit_queue_progress(
                    "ocr_merging",
                    "Merging OCR batch results.",
                    45,
                    paddle_job_id=job_id,
                    total_units=total_units,
                )
                ocr_queue_seconds = time.perf_counter() - ocr_queue_started
                batch_timing = _batch_timing_summary(batch_states)
                merge_started = time.perf_counter()
                try:
                    result = self._merge_paddleocr_page_batch_results(
                        client=client,
                        task_key=task_key,
                        job_id=job_id,
                        source_path=source_path,
                        batch_states=batch_states,
                        output_dir=output_dir,
                        total_pages=total_pages,
                        total_units=total_units,
                        batch_size=batch_size,
                        expected_ranges=expected_ranges,
                        native_page_markdown=native_page_markdown,
                        page_routes=page_routes,
                        partial_result=partial_result,
                        promote_visuals=promote_visuals,
                        parse_pass=parse_pass,
                        render_zoom=render_zoom,
                        emit_progress=emit_progress,
                    )
                except ContentExtractionError as exc:
                    self._redis_hash_set(
                        client,
                        task_key,
                        {
                            "status": "failed",
                            "stage": "merge_validation_failed",
                            "failed_at": datetime.now().isoformat(),
                            "error": str(exc),
                        },
                    )
                    if not _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False):
                        _cleanup_path_tree(output_dir, label="incomplete worker batch output")
                        _cleanup_path_tree(
                            Path(
                                os.getenv(
                                    "PADDLEOCR_JOB_WORK_DIR",
                                    "/workspace/uploads/paddleocr_vl_jobs",
                                )
                            )
                            / job_id,
                            label="incomplete batch PDFs",
                        )
                    raise
                merge_seconds = time.perf_counter() - merge_started
                queue_total_seconds = time.perf_counter() - queue_run_started
                stage_timings = {
                    "split_seconds": round(split_seconds, 3),
                    "queue_submit_seconds": round(queue_submit_seconds, 3),
                    "ocr_queue_seconds": round(ocr_queue_seconds, 3),
                    "merge_seconds": round(merge_seconds, 3),
                    "queue_total_seconds": round(queue_total_seconds, 3),
                }
                result["stage_timings"] = stage_timings
                result["batch_timing"] = batch_timing
                result["page_routes"] = page_routes
                result["page_prediction_options"] = page_prediction_options
                result["native_page_count"] = native_page_count
                result["ocr_page_count"] = ocr_page_count
                result["parse_pass"] = max(1, int(parse_pass or 1))
                result["render_zoom"] = float(render_zoom or 1.0)
                result["_task_key"] = task_key
                elapsed_stats = batch_timing["elapsed_seconds"]
                redis_result = {
                    key: value
                    for key, value in result.items()
                    if key != "markdown" and not str(key).startswith("_")
                }
                self._redis_hash_set(
                    client,
                    task_key,
                    {
                        "split_seconds": stage_timings["split_seconds"],
                        "queue_submit_seconds": stage_timings["queue_submit_seconds"],
                        "ocr_queue_seconds": stage_timings["ocr_queue_seconds"],
                        "merge_seconds": stage_timings["merge_seconds"],
                        "queue_total_seconds": stage_timings["queue_total_seconds"],
                        "batch_elapsed_avg_seconds": elapsed_stats["avg"],
                        "batch_elapsed_p50_seconds": elapsed_stats["p50"],
                        "batch_elapsed_p95_seconds": elapsed_stats["p95"],
                        "batch_elapsed_max_seconds": elapsed_stats["max"],
                        "stage_timings": stage_timings,
                        "batch_timing": batch_timing,
                        "result_json": redis_result,
                    },
                )
                self.logger.info(
                    f"PaddleOCR-VL internal timings: job_id={job_id}, stages={stage_timings}, "
                    f"batch_elapsed={elapsed_stats}"
                )
                return result

            time.sleep(max(0.25, poll_interval))

        ocr_queue_seconds = time.perf_counter() - ocr_queue_started
        batch_timing = _batch_timing_summary(batch_states)
        self._redis_hash_set(
            client,
            task_key,
            {
                "status": "failed",
                "stage": "timeout",
                "cancel_requested": "1",
                "failed_at": datetime.now().isoformat(),
                "error": f"timeout={timeout}s",
                "ocr_queue_seconds": round(ocr_queue_seconds, 3),
                "batch_timing": batch_timing,
            },
        )
        self._remove_queued_batches_for_job(client, queue_name, job_id)
        if not _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False):
            _cleanup_path_tree(output_dir, label="超时任务 worker batch 输出")
            _cleanup_path_tree(Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs")) / job_id, label="超时任务拆页 PDF")
        raise ContentExtractionError(
            f"PaddleOCR-VL 页级 batch 队列解析超时: job_id={job_id}, timeout={timeout}s",
            file_path=str(source_path),
        )

    def _merge_paddleocr_page_batch_results(
        self,
        *,
        client,
        task_key: str,
        job_id: str,
        source_path: Path,
        batch_states: list[dict],
        output_dir: Path,
        total_pages: int,
        total_units: int,
        batch_size: int,
        expected_ranges: Optional[Sequence[tuple[int, int]]] = None,
        native_page_markdown: Optional[Dict[int, str]] = None,
        page_routes: Optional[Dict[int, str]] = None,
        partial_result: bool = False,
        promote_visuals: bool = True,
        parse_pass: int = 1,
        render_zoom: float = 1.0,
        emit_progress: bool = True,
    ) -> Dict[str, Any]:
        ocr_page_markdown: Dict[int, str] = {}
        combined_page_markers: list[int] = []
        elapsed_total = 0.0
        effective_render_zooms: List[float] = []
        effective_render_zoom_by_page: Dict[int, float] = {}
        successful_ranges: List[Tuple[int, int]] = []
        failed_pages: List[int] = []
        planned_ranges = list(
            _page_batch_ranges(total_pages, batch_size)
            if expected_ranges is None
            else expected_ranges
        )
        if len(planned_ranges) != total_units:
            raise ContentExtractionError(
                f"PaddleOCR-VL batch plan mismatch: job_id={job_id}, "
                f"expected_units={len(planned_ranges)}, reported_units={total_units}",
                file_path=str(source_path),
            )

        # The optional repair pass is deliberately best-effort.  A worker can
        # report success before its batch markdown is durably visible, or it
        # can leave a malformed range/count/marker payload.  Validate each
        # purported success independently and demote only that unit to failed;
        # the normal first pass remains fail-closed in the strict loop below.
        if partial_result:
            validated_states: List[Dict[str, Any]] = []
            for idx, raw_state in enumerate(batch_states, 1):
                state = dict(raw_state)
                status = str(state.get("status", "")).lower()
                if status not in {"success", "completed"}:
                    validated_states.append(state)
                    continue
                expected_start, expected_end = planned_ranges[idx - 1]
                try:
                    raw_result = state.get("result_json")
                    if isinstance(raw_result, dict):
                        result = dict(raw_result)
                    else:
                        try:
                            result = json.loads(raw_result or "{}")
                        except Exception:
                            result = dict(state)

                    def _validated_int(name: str) -> int:
                        raw_value = result.get(name)
                        if raw_value is None or raw_value == "":
                            raw_value = state.get(name)
                        return int(raw_value)

                    actual_start = _validated_int("start_page")
                    actual_end = _validated_int("end_page")
                    result_count = _validated_int("result_count")
                    expected_count = expected_end - expected_start + 1
                    if (actual_start, actual_end) != (expected_start, expected_end):
                        raise ValueError(
                            f"range {actual_start}-{actual_end} != "
                            f"{expected_start}-{expected_end}"
                        )
                    if result_count != expected_count:
                        raise ValueError(
                            f"result_count {result_count} != {expected_count}"
                        )
                    md_path_value = str(
                        result.get("batch_markdown_path")
                        or state.get("batch_markdown_path")
                        or ""
                    ).strip()
                    md_path = Path(md_path_value) if md_path_value else None
                    if md_path is None or not md_path.is_file():
                        raise ValueError(f"batch markdown is missing: {md_path}")
                    md = md_path.read_text(encoding="utf-8", errors="ignore").strip()
                    expected_markers = list(range(expected_start, expected_end + 1))
                    markers = _paddle_markdown_page_markers(md)
                    if markers != expected_markers:
                        raise ValueError(
                            f"page markers {markers} != {expected_markers}"
                        )
                    page_bodies = _markdown_content_by_page(md)
                    empty_pages = [
                        page
                        for page in expected_markers
                        if not str(page_bodies.get(page) or "").strip()
                    ]
                    if empty_pages:
                        raise ValueError(
                            f"page bodies are empty: {empty_pages}"
                        )
                except Exception as exc:
                    state["status"] = "failed"
                    state["error"] = (
                        "optional repair success payload failed validation: "
                        f"{exc}"
                    )
                    logger.warning(
                        f"Skipping malformed optional table-repair batch: "
                        f"job_id={job_id}, unit={idx}, "
                        f"pages={expected_start}-{expected_end}, error={exc}"
                    )
                validated_states.append(state)
            batch_states = validated_states

        for idx, state in enumerate(batch_states, 1):
            status = str(state.get("status", "")).lower()
            if status in {"success", "completed"}:
                raw_result = state.get("result_json")
                if isinstance(raw_result, dict):
                    result = dict(raw_result)
                else:
                    try:
                        result = json.loads(raw_result or "{}")
                    except Exception:
                        result = dict(state)
                expected_start, expected_end = planned_ranges[idx - 1]
                expected_count = expected_end - expected_start + 1

                def _required_result_int(name: str) -> int:
                    raw_value = result.get(name)
                    if raw_value is None or raw_value == "":
                        raw_value = state.get(name)
                    try:
                        return int(raw_value)
                    except (TypeError, ValueError):
                        raise ContentExtractionError(
                            f"PaddleOCR-VL batch result missing {name}: "
                            f"job_id={job_id}, unit={idx}",
                            file_path=str(source_path),
                        )

                actual_start = _required_result_int("start_page")
                actual_end = _required_result_int("end_page")
                result_count = _required_result_int("result_count")
                if (actual_start, actual_end) != (expected_start, expected_end):
                    raise ContentExtractionError(
                        f"PaddleOCR-VL batch page range mismatch: job_id={job_id}, "
                        f"unit={idx}, expected={expected_start}-{expected_end}, "
                        f"returned={actual_start}-{actual_end}",
                        file_path=str(source_path),
                    )
                if result_count != expected_count:
                    raise ContentExtractionError(
                        f"PaddleOCR-VL batch page count mismatch: job_id={job_id}, "
                        f"unit={idx}, expected={expected_count}, returned={result_count}",
                        file_path=str(source_path),
                    )
                successful_ranges.append((expected_start, expected_end))

                md_path_value = str(
                    result.get("batch_markdown_path")
                    or state.get("batch_markdown_path")
                    or ""
                ).strip()
                md_path = Path(md_path_value) if md_path_value else None
                if md_path is None or not md_path.is_file():
                    raise ContentExtractionError(
                        f"PaddleOCR-VL batch 完成但 batch.md 不存在: job_id={job_id}, unit={idx}, path={md_path}",
                        file_path=str(source_path),
                    )
                try:
                    md = md_path.read_text(encoding="utf-8", errors="ignore").strip()
                except OSError as exc:
                    raise ContentExtractionError(
                        f"PaddleOCR-VL batch Markdown read failed: job_id={job_id}, "
                        f"unit={idx}, path={md_path}, error={exc}",
                        file_path=str(source_path),
                    ) from exc
                expected_markers = list(range(expected_start, expected_end + 1))
                page_markers = _paddle_markdown_page_markers(md)
                if page_markers != expected_markers:
                    raise ContentExtractionError(
                        f"PaddleOCR-VL batch Markdown pages are incomplete or out of order: "
                        f"job_id={job_id}, unit={idx}, expected={expected_markers}, "
                        f"returned={page_markers}",
                        file_path=str(source_path),
                    )
                ocr_page_markdown.update(_markdown_content_by_page(md))
                combined_page_markers.extend(page_markers)
                try:
                    elapsed_total += float(result.get("elapsed_seconds") or 0.0)
                except Exception:
                    pass
                try:
                    effective_zoom = max(
                        1.0,
                        float(result.get("render_zoom") or render_zoom or 1.0),
                    )
                    effective_render_zooms.append(effective_zoom)
                    for page in range(expected_start, expected_end + 1):
                        effective_render_zoom_by_page[page] = effective_zoom
                except (TypeError, ValueError, OverflowError):
                    pass
            elif status == "failed":
                if partial_result:
                    failed_start, failed_end = planned_ranges[idx - 1]
                    failed_pages.extend(range(failed_start, failed_end + 1))
                    continue
                error = state.get("error") or state.get("traceback") or "unknown worker error"
                raise ContentExtractionError(
                    f"PaddleOCR-VL batch failed: job_id={job_id}, unit={idx}, error={error}",
                    file_path=str(source_path),
                )
            else:
                raise ContentExtractionError(
                    f"PaddleOCR-VL batch 状态异常: job_id={job_id}, unit={idx}, status={status}",
                    file_path=str(source_path),
                )

        expected_ocr_markers = [
            page
            for start_page, end_page in successful_ranges
            for page in range(start_page, end_page + 1)
        ]
        if combined_page_markers != expected_ocr_markers:
            raise ContentExtractionError(
                f"PaddleOCR-VL merged OCR pages are incomplete or out of order: "
                f"job_id={job_id}, expected={expected_ocr_markers}, "
                f"returned={combined_page_markers}",
                file_path=str(source_path),
            )

        native_pages = {int(page): str(body or "").strip() for page, body in (native_page_markdown or {}).items()}
        routes = {int(page): str(route or "ocr").strip().lower() for page, route in (page_routes or {}).items()}
        combined_parts: list[str] = []
        missing_pages: list[int] = []
        output_pages = (
            sorted(set(expected_ocr_markers))
            if partial_result
            else list(range(1, total_pages + 1))
        )
        for page in output_pages:
            native_body = native_pages.get(page, "")
            ocr_body = ocr_page_markdown.get(page, "")
            route = routes.get(page, "ocr")
            if route == "native":
                body = native_body or ocr_body
            elif route == "hybrid":
                body = _merge_native_and_ocr_page(native_body, ocr_body)
            else:
                body = ocr_body or native_body
            if not body:
                if route == "native" and page in native_pages:
                    combined_parts.append(
                        f"<!-- Page {page} | adaptive parser route=native blank=true -->"
                    )
                    continue
                missing_pages.append(page)
                continue
            combined_parts.append(f"<!-- Page {page} | adaptive parser route={route} -->\n\n{body}")
        if missing_pages:
            raise ContentExtractionError(
                f"Adaptive PDF merge produced no content for pages: {missing_pages[:20]}",
                file_path=str(source_path),
            )

        markdown = "\n\n".join(combined_parts).strip()
        if not markdown:
            raise ContentExtractionError("PaddleOCR-VL 页级 batch 没有生成 Markdown", file_path=str(source_path))

        effective_pass = max(1, int(parse_pass or 1))
        if promote_visuals:
            if emit_progress:
                self._emit_progress(
                    "visual_assets",
                    "Persisting chart and image evidence.",
                    43,
                )
            visual_assets = promote_visual_assets(output_dir, source_path)
            visual_manifest = load_visual_manifest(source_path) or {}
            table_records = list(visual_manifest.get("tables") or [])
            for record in table_records:
                if isinstance(record, dict):
                    record["parse_pass"] = effective_pass
            markdown = append_visual_markers(markdown, visual_assets)
        else:
            # Selective table repair must not overwrite the complete first-pass
            # visual manifest.  Read only the structured table records produced
            # for the candidate pages.
            visual_assets = []
            table_records = collect_table_records(
                output_dir,
                parse_pass=effective_pass,
            )
            successful_page_set = set(expected_ocr_markers)
            successful_table_records: List[Dict[str, Any]] = []
            for record in table_records:
                if not isinstance(record, dict):
                    continue
                try:
                    record_page = int(record.get("page_number") or 0)
                except (TypeError, ValueError, OverflowError):
                    continue
                if record_page in successful_page_set:
                    successful_table_records.append(record)
            table_records = successful_table_records

        keep_process_output = _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False)
        combined_md_path = output_dir / "combined.md"
        result_markdown_path = ""
        if keep_process_output:
            _ensure_shared_writable_dir(output_dir)
            combined_md_path.write_text(markdown, encoding="utf-8")
            _ensure_shared_file(combined_md_path)
            result_markdown_path = str(combined_md_path)

        result = {
            "status": "success",
            "parser": "paddleocr-vl",
            "pipeline_version": "v1.6",
            "queue_granularity": "page-batch",
            "mode": "page-batch",
            "page_batch_size": batch_size,
            "total_pages": total_pages,
            "units_processed": total_units,
            "elapsed_worker_seconds_sum": elapsed_total,
            "visual_asset_count": len(visual_assets),
            "visual_assets": visual_assets,
            "table_records": table_records,
            "output_dir": str(output_dir) if keep_process_output else "",
            "result_markdown_path": result_markdown_path,
            "intermediate_output_removed": not keep_process_output,
            "partial_result": bool(partial_result),
            "parse_pass": effective_pass,
            "render_zoom": float(render_zoom or 1.0),
            "effective_render_zoom": (
                min(effective_render_zooms)
                if effective_render_zooms
                else float(render_zoom or 1.0)
            ),
            "effective_render_zoom_by_page": effective_render_zoom_by_page,
            "processed_pages": output_pages,
            "failed_pages": sorted(set(failed_pages)),
            "markdown": markdown,
        }
        self._redis_hash_set(
            client,
            task_key,
            {
                "status": result["status"],
                "stage": "completed",
                "completed_at": datetime.now().isoformat(),
                "result_json": {k: v for k, v in result.items() if k != "markdown"},
                "output_dir": result["output_dir"],
                "result_markdown_path": result_markdown_path,
                "intermediate_output_removed": str(not keep_process_output).lower(),
            },
        )

        if not keep_process_output:
            _cleanup_path_tree(output_dir, label="worker batch 输出")
            work_root = Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs"))
            _cleanup_path_tree(work_root / job_id, label="backend 拆页 PDF")

        return result

    def _markdown_from_final_segments(
        self,
        segments: Sequence[TextSegment],
        fallback: str = "",
    ) -> str:
        """Render the final canonical evidence without row/cell duplication.

        Accepted second-pass families live in ``segments``; retaining the raw
        first-pass Markdown would make direct-LLM consumers see stale tables.
        Preserve document order, include one full table segment per physical
        occurrence, and omit its derived row/cell indexing segments.
        """
        ordered = sorted(
            enumerate(segments),
            key=lambda item: (
                max(1, int(item[1].page_number or 1)),
                item[0],
            ),
        )
        parts: List[str] = []
        current_page: Optional[int] = None
        seen_segments: set[str] = set()
        for _index, segment in ordered:
            if segment.segment_type in {"table_row", "table_cell"}:
                continue
            segment_id = str(segment.segment_id or "").strip()
            if segment_id and segment_id in seen_segments:
                continue
            content = str(segment.content or "").strip()
            if not content:
                continue
            page = max(1, int(segment.page_number or 1))
            if page != current_page:
                parts.append(f"<!-- Page {page} | canonical evidence -->")
                current_page = page
            parts.append(content)
            if segment_id:
                seen_segments.add(segment_id)
        rendered = "\n\n".join(parts).strip()
        return rendered or str(fallback or "").strip()

    # ------------------------------------------------------------------
    # 从 PaddleOCR-VL Markdown 输出构造 TextSegment
    # ------------------------------------------------------------------

    def _segments_from_markdown(self, markdown: str, document_id: str) -> List[TextSegment]:
        blocks = self._split_markdown_blocks(markdown)
        segments: List[TextSegment] = []
        seq = 0
        table_index = 0
        current_page = 1
        current_heading = ""
        section_path: List[str] = []

        for block in blocks:
            marker = self._page_marker(block)
            if marker is not None:
                current_page = marker
                continue

            block = block.strip()
            if not block:
                continue

            visual = parse_visual_marker(block)
            if visual:
                # Preserve the durable asset in its manifest, but do not create a
                # dense-retrieval row for an empty crop placeholder.
                if not _visual_has_searchable_content(visual):
                    continue
                page = max(1, int(visual.get("page_number") or current_page))
                caption = str(visual.get("caption") or "").strip()
                summary = str(visual.get("summary") or "").strip()
                ocr_text = str(visual.get("ocr_text") or "").strip()
                chart_data = visual.get("chart_data")
                segment_type = "chart" if chart_data else "figure"
                content_parts = [part for part in (caption, summary, ocr_text) if part]
                if chart_data:
                    content_parts.append(json.dumps(chart_data, ensure_ascii=False, sort_keys=True))
                content = "\n".join(content_parts) or f"Visual evidence {visual['asset_id']}"
                seq += 1
                segments.append(TextSegment(
                    segment_id=f"{document_id}_p{page}_s{seq}",
                    content=content,
                    page_number=page,
                    position_y=float(seq),
                    position_x=0.0,
                    segment_type=segment_type,
                        structured_data={
                            **visual,
                            "source": "paddleocr_vl_visual_asset",
                            "parser": "paddleocr-vl",
                            "evidence_type": segment_type,
                            "section_path": list(section_path),
                        },
                ))
                continue

            if self._looks_like_markdown_table(block):
                table_index += 1
                table_id = f"{document_id}_table_{table_index:04d}"
                _table_rows, _table_cells, table_quality = self._parse_table_details(
                    block
                )
                table_header_model = dict(
                    table_quality.get("header_model") or {}
                )
                seq += 1
                segments.append(
                    TextSegment(
                        segment_id=f"{document_id}_p{current_page}_s{seq}",
                        content=block,
                        page_number=current_page,
                        position_y=float(seq),
                        position_x=0.0,
                        segment_type="table",
                        source_table_id=table_id,
                        structure_confidence=table_quality.get(
                            "structure_confidence"
                        ),
                        ocr_confidence=table_quality.get("ocr_confidence"),
                        parse_pass=1,
                        review_status=table_quality.get("review_status"),
                        structured_data={
                            "source": "paddleocr_vl_markdown",
                            "parser": "paddleocr-vl",
                            "table_id": table_id,
                            "table_title": current_heading,
                            "table_title_source": "section_heading",
                            "section_path": list(section_path),
                            "semantic_schema_version": 1,
                            "table_semantics_version": 2,
                            "header_source": table_header_model.get("source"),
                            "header_confirmed": bool(
                                table_header_model.get("confirmed")
                            ),
                            "header_row_indices": list(
                                table_header_model.get("header_row_indices")
                                or []
                            ),
                            "header_paths": list(
                                table_header_model.get("header_paths") or []
                            ),
                            "structure_confidence": table_quality.get(
                                "structure_confidence"
                            ),
                            "ocr_confidence": table_quality.get(
                                "ocr_confidence"
                            ),
                            "parse_pass": 1,
                            "review_status": table_quality.get("review_status"),
                            "quality_reasons": list(
                                table_quality.get("reasons") or []
                            ),
                            "quality_notes": list(
                                table_quality.get("notes") or []
                            ),
                            "conflicts": [],
                        },
                    )
                )
                table_segments = self._table_segments_from_markdown(
                    block,
                    document_id,
                    current_page,
                    table_id,
                    start_seq=seq,
                    table_title=current_heading,
                    table_title_source="section_heading",
                    section_path=section_path,
                )
                if table_segments:
                    segments.extend(table_segments)
                    seq += len(table_segments)
                continue

            block = _clean_non_table_markdown_block(block)
            if not block:
                continue
            heading_match = re.match(r"^(#{1,6})\s+", block)
            segment_type = "heading" if heading_match else "text"
            content = re.sub(r"^#{1,6}\s+", "", block).strip() if segment_type == "heading" else block
            if segment_type == "heading":
                current_heading = content
                level = len(heading_match.group(1)) if heading_match else 1
                section_path = section_path[: max(0, level - 1)] + [content]
            if (
                segment_type == "text"
                and len(content.strip()) < int(getattr(self.config, "min_text_length", 10) or 10)
                and not _is_meaningful_short_text(content)
            ):
                continue
            seq += 1
            segments.append(
                TextSegment(
                    segment_id=f"{document_id}_p{current_page}_s{seq}",
                    content=content,
                    page_number=current_page,
                    position_y=float(seq),
                    position_x=0.0,
                    segment_type=segment_type,
                    structured_data={
                        "source": "paddleocr_vl_markdown",
                        "parser": "paddleocr-vl",
                        "section_path": list(section_path),
                    },
                )
            )

        return segments

    def _split_markdown_blocks(self, markdown: str) -> List[str]:
        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        blocks: List[str] = []
        current: List[str] = []
        in_table = False
        in_html_table = False

        def flush() -> None:
            nonlocal current, in_table, in_html_table
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            in_table = False
            in_html_table = False

        for line in lines:
            stripped = line.strip()
            if self._page_marker(stripped) is not None:
                flush()
                blocks.append(stripped)
                continue

            lower = stripped.lower()
            if "<table" in lower:
                if current and not in_html_table:
                    flush()
                current.append(line)
                in_html_table = True
                if "</table" in lower:
                    flush()
                continue

            if in_html_table:
                current.append(line)
                if "</table" in lower:
                    flush()
                continue

            table_line = self._looks_like_table_line(stripped)
            if table_line:
                if current and not in_table:
                    flush()
                current.append(line)
                in_table = True
                continue

            if in_table:
                flush()

            if not stripped:
                flush()
                continue

            if re.match(r"^#{1,6}\s+", stripped):
                flush()
                current.append(line)
                flush()
                continue

            current.append(line)

        flush()
        return [b for b in blocks if b]

    def _looks_like_table_line(self, stripped: str) -> bool:
        if not stripped:
            return False
        if "<tr" in stripped.lower() or "<td" in stripped.lower() or "<th" in stripped.lower():
            return True
        if "|" not in stripped:
            return False
        return stripped.count("|") >= 2 or (stripped.startswith("|") and "|" in stripped[1:])

    def _looks_like_markdown_table(self, block: str) -> bool:
        if re.search(r"<\s*(table|tr|td|th)\b", block or "", flags=re.IGNORECASE):
            return bool(self._parse_html_table_rows(block))
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            return False
        pipe_lines = [ln for ln in lines if self._looks_like_table_line(ln.strip())]
        if len(pipe_lines) < 2:
            return False
        parsed = self._parse_markdown_table_rows("\n".join(pipe_lines))
        if len(parsed) < 2:
            return False
        widths = [len(row) for row in parsed if row]
        return bool(widths and max(widths) >= 2 and widths.count(widths[0]) >= 2)

    @staticmethod
    def _markdown_table_has_header_separator(table_text: str) -> bool:
        """Return whether a pipe table explicitly marks its first row as a header."""
        pipe_lines = [
            line.strip()
            for line in str(table_text or "").splitlines()
            if line.strip() and "|" in line
        ]
        if len(pipe_lines) < 2:
            return False
        header_cells = [
            cell.strip()
            for cell in pipe_lines[0].strip("|").split("|")
        ]
        separator_cells = [
            cell.strip()
            for cell in pipe_lines[1].strip("|").split("|")
        ]
        return (
            bool(header_cells)
            and len(separator_cells) == len(header_cells)
            and all(
                bool(cell)
                and bool(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")))
                for cell in separator_cells
            )
        )

    @staticmethod
    def _extract_table_years(text: object) -> List[int]:
        """Extract explicit FY/CY or four-digit years without scraping loose numbers."""
        raw = re.sub(r"\s+", " ", unescape(str(text or ""))).strip()
        if not raw:
            return []
        years: List[int] = []

        def append_year(value: object, *, short: bool = False) -> None:
            try:
                year = int(value)
            except (TypeError, ValueError):
                return
            if short:
                year += 2000
            if 1900 <= year <= 2100 and year not in years:
                years.append(year)

        occupied: List[Tuple[int, int]] = []
        # Compact slash ranges normally denote one reporting period, whose
        # reporting year is the period end. Keep `FY23 / FY24` as two distinct
        # years: the repeated prefix deliberately prevents this range match.
        period_pattern = re.compile(
            r"(?i)\b(?:"
            r"(?:(?:FY|CY)\s*['\u2019]?\s*)((?:19|20)?\d{2})"
            r"|((?:19|20)\d{2})"
            r")\s*/\s*((?:19|20)?\d{2})\b"
        )
        for match in period_pattern.finditer(raw):
            start_text = match.group(1) or match.group(2) or ""
            end_text = match.group(3) or ""
            try:
                start_year = (
                    2000 + int(start_text)
                    if len(start_text) == 2
                    else int(start_text)
                )
                if len(end_text) == 2:
                    end_year = (start_year // 100) * 100 + int(end_text)
                    if end_year < start_year:
                        end_year += 100
                else:
                    end_year = int(end_text)
            except (TypeError, ValueError):
                continue
            append_year(end_year)
            occupied.append(match.span())

        for match in re.finditer(
            r"(?i)\b(?:FY|CY)\s*['\u2019]?\s*((?:19|20)?\d{2})\b",
            raw,
        ):
            if any(start <= match.start() and match.end() <= end for start, end in occupied):
                continue
            value = match.group(1)
            append_year(value, short=len(value) == 2)
            occupied.append(match.span())
        for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})(?!\d)", raw):
            if any(start <= match.start() and match.end() <= end for start, end in occupied):
                continue
            append_year(match.group(1))
        return years

    def _build_table_header_model(
        self,
        rows: Sequence[Sequence[Any]],
        physical_cells: Sequence[Dict[str, Any]],
        *,
        is_html: bool,
        table_text: str,
        row_metadata: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build a conservative column-header tree while preserving source row indexes."""
        width = max((len(row) for row in rows), default=0)
        cells_by_row: Dict[int, List[Dict[str, Any]]] = {}
        for cell in physical_cells:
            try:
                row_index = int(cell.get("row_index"))
            except (TypeError, ValueError):
                continue
            cells_by_row.setdefault(row_index, []).append(cell)
        metadata_by_row = {
            int(item.get("row_index")): dict(item)
            for item in (row_metadata or [])
            if item.get("row_index") is not None
        }

        header_rows: List[int] = []
        source = "none"
        confirmed = False
        if rows and is_html:
            thead_rows = [
                index
                for index in range(len(rows))
                if str(metadata_by_row.get(index, {}).get("section") or "") == "thead"
            ]
            if thead_rows and thead_rows == list(range(0, max(thead_rows) + 1)):
                header_rows = thead_rows
                source = "html_thead"
                confirmed = True
            else:
                for index in range(len(rows)):
                    row_cells = [
                        cell
                        for cell in cells_by_row.get(index, [])
                        if str(cell.get("text") or "").strip()
                    ]
                    is_column_header_row = bool(row_cells) and all(
                        bool(cell.get("is_header"))
                        and str(cell.get("scope") or "").lower()
                        not in {"row", "rowgroup"}
                        for cell in row_cells
                    )
                    if not is_column_header_row:
                        break
                    header_rows.append(index)
                if header_rows:
                    source = "html_th"
                    confirmed = True
        elif rows and self._markdown_table_has_header_separator(table_text):
            header_rows = [0]
            source = "markdown_separator"
            confirmed = True

        header_set = set(header_rows)
        data_rows = [index for index in range(len(rows)) if index not in header_set]
        header_paths: List[List[str]] = []
        for column in range(width):
            path: List[str] = []
            for row_index in header_rows:
                raw_value = rows[row_index][column] if column < len(rows[row_index]) else ""
                value = re.sub(r"\s+", " ", unescape(str(raw_value or ""))).strip()
                if not value:
                    continue
                if not path or value.casefold() != path[-1].casefold():
                    path.append(value)
            header_paths.append(path)

        synthetic = not header_rows
        headers = [
            " > ".join(path) if path else f"Column {index + 1}"
            for index, path in enumerate(header_paths)
        ]
        return {
            "source": source,
            "confirmed": confirmed,
            "inferred": bool(header_rows and not confirmed),
            "header_row_indices": header_rows,
            "data_row_indices": data_rows,
            "header_paths": header_paths,
            "headers": headers,
            "synthetic_headers": synthetic,
        }

    @staticmethod
    def _clean_table_measurement_text(text: object) -> str:
        value = unescape(str(text or ""))
        value = value.replace("CO\u2082", "CO2").replace("co\u2082", "co2")
        value = value.replace("m\u00b3", "m3").replace("\uff05", "%")
        value = re.sub(r"_\{?2\}?", "2", value)
        value = re.sub(r"\\(?:mathrm|text)\s*\{([^{}]*)\}", r"\1", value)
        value = value.replace("$", " ").replace("{", " ").replace("}", " ")
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _table_scale_details(text: object) -> Optional[Tuple[str, float]]:
        value = ContentExtractor._clean_table_measurement_text(text).casefold()
        if not value:
            return None
        aliases = {
            "thousand": ("thousand", 1_000.0),
            "thousands": ("thousand", 1_000.0),
            "million": ("million", 1_000_000.0),
            "millions": ("million", 1_000_000.0),
            "billion": ("billion", 1_000_000_000.0),
            "billions": ("billion", 1_000_000_000.0),
        }
        if value in aliases:
            return aliases[value]
        match = re.search(
            r"\b(?:figures|amounts|values|data|results)\s+"
            r"(?:(?:are|shown|reported|expressed)\s+)?in\s+"
            r"(thousands?|millions?|billions?)\b",
            value,
        )
        return aliases.get(match.group(1)) if match else None

    def _extract_table_unit_specs(self, text: object) -> List[Dict[str, Any]]:
        """Extract high-confidence measurement units without selecting among conflicts."""
        raw = self._clean_table_measurement_text(text)
        if not raw:
            return []
        normalized = raw
        replacements = [
            (r"(?i)\bkg\s+(?:of\s+)?co2e\b", "kgCO2e"),
            (r"(?i)\bkt\s+(?:of\s+)?co2e\b", "ktCO2e"),
            (r"\bMt\s+(?:of\s+)?CO2e\b", "MtCO2e"),
            (r"(?i)\bt\s+(?:of\s+)?co2e\b", "tCO2e"),
            (
                r"(?i)\bkilograms?\s+(?:of\s+)?co2(?:e|\s+equivalents?)\b",
                "kgCO2e",
            ),
            (
                r"(?i)\bkilotonnes?\s+(?:of\s+)?co2(?:e|\s+equivalents?)\b",
                "ktCO2e",
            ),
            (
                r"(?i)\b(?:metric\s+)?(?:tonnes?|tons?)\s+(?:of\s+)?"
                r"co2(?:e|\s+equivalents?)\b",
                "tCO2e",
            ),
            (r"\bMT\s+CO2e\b", "tCO2e"),
            (r"(?i)\bmegawatt(?:-|\s*)hours?\b", "MWh"),
            (r"(?i)\bkilowatt(?:-|\s*)hours?\b", "kWh"),
            (r"(?i)\bgigawatt(?:-|\s*)hours?\b", "GWh"),
            (r"(?i)\bterawatt(?:-|\s*)hours?\b", "TWh"),
            (r"(?i)\bgigajoules?\b", "GJ"),
            (r"(?i)\bterajoules?\b", "TJ"),
            (r"(?i)\bpetajoules?\b", "PJ"),
            (r"(?i)\bcubic\s+met(?:er|re)s?\b", "m3"),
            (r"(?i)\bpercent(?:age)?\b", "%"),
        ]
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized)

        unit_atom_pattern = (
            r"kgCO2e|ktCO2e|MtCO2e|tCO2e|CO2e|"
            r"kWh|MWh|GWh|TWh|GJ|TJ|PJ|MMBtu|"
            r"m3|kL|mL|L|metric\s+tons?|tonnes?|tons?|kg|"
            r"USD|EUR|GBP|CNY|%"
        )
        scale_marker_pattern = (
            r"thousands?|millions?|billions?|mn|bn|(?:['\u2019]\s*)?000s?"
        )

        def normalized_scale_word(value: object) -> str:
            token = re.sub(
                r"[\s'\u2019]",
                "",
                str(value or "").casefold(),
            )
            if token.startswith("000"):
                return "thousand"
            if token in {"mn", "million", "millions"}:
                return "million"
            if token in {"bn", "billion", "billions"}:
                return "billion"
            return "thousand"

        # Normalize common accounting-style postfix/prefix scales into the
        # canonical `million USD` form consumed below. This prevents `USD
        # million`, `'000 tonnes`, or `GJ (000s)` from silently becoming x1.
        postfix_scale_pattern = re.compile(
            r"(?i)(?<![A-Za-z0-9])"
            rf"({unit_atom_pattern})\s*(?:\(\s*)?"
            rf"({scale_marker_pattern})(?:\s*\))?"
            r"(?![A-Za-z0-9])"
        )
        normalized = postfix_scale_pattern.sub(
            lambda match: (
                f"{normalized_scale_word(match.group(2))} {match.group(1)}"
            ),
            normalized,
        )
        symbolic_prefix_pattern = re.compile(
            r"(?i)(?<![A-Za-z0-9])"
            rf"((?:['\u2019]\s*)?000s?|mn|bn)\s+"
            rf"({unit_atom_pattern})(?![A-Za-z0-9])"
        )
        normalized = symbolic_prefix_pattern.sub(
            lambda match: (
                f"{normalized_scale_word(match.group(1))} {match.group(2)}"
            ),
            normalized,
        )
        unit_pattern = re.compile(
            r"(?i)(?<![A-Za-z0-9])"
            r"(?:(thousands?|millions?|billions?)\s+)?"
            rf"({unit_atom_pattern})(?![A-Za-z0-9])"
        )
        denominator_atom_pattern = (
            rf"{unit_atom_pattern}|employees?|FTEs?|revenue|sales|products?|units?"
        )
        compound_pattern = re.compile(
            r"(?i)(?<![A-Za-z0-9])"
            r"(?:(thousands?|millions?|billions?)\s+)?"
            rf"({unit_atom_pattern})\s*(?:/|\bper\b)\s*"
            rf"({denominator_atom_pattern})(?![A-Za-z0-9])"
        )
        canonical = {
            "kgco2e": "kgCO2e",
            "ktco2e": "ktCO2e",
            "mtco2e": "MtCO2e",
            "tco2e": "tCO2e",
            "co2e": "CO2e",
            "kwh": "kWh",
            "mwh": "MWh",
            "gwh": "GWh",
            "twh": "TWh",
            "gj": "GJ",
            "tj": "TJ",
            "pj": "PJ",
            "mmbtu": "MMBtu",
            "m3": "m3",
            "kl": "kL",
            "ml": "mL",
            "l": "L",
            "metric ton": "t",
            "metric tons": "t",
            "tonne": "t",
            "tonnes": "t",
            "ton": "t",
            "tons": "t",
            "kg": "kg",
            "usd": "USD",
            "eur": "EUR",
            "gbp": "GBP",
            "cny": "CNY",
            "%": "%",
        }
        denominator_canonical = {
            **canonical,
            "employee": "employee",
            "employees": "employee",
            "fte": "FTE",
            "ftes": "FTE",
            "revenue": "revenue",
            "sales": "revenue",
            "product": "product",
            "products": "product",
            "unit": "unit",
            "units": "unit",
        }
        scale_aliases = {
            "thousand": ("thousand", 1_000.0),
            "thousands": ("thousand", 1_000.0),
            "million": ("million", 1_000_000.0),
            "millions": ("million", 1_000_000.0),
            "billion": ("billion", 1_000_000_000.0),
            "billions": ("billion", 1_000_000_000.0),
        }
        specs: List[Dict[str, Any]] = []
        seen = set()
        compound_spans: List[Tuple[int, int]] = []
        for match in compound_pattern.finditer(normalized):
            numerator = canonical.get(str(match.group(2) or "").casefold())
            denominator = denominator_canonical.get(
                str(match.group(3) or "").casefold()
            )
            if not numerator or not denominator:
                continue
            scale_name, multiplier = scale_aliases.get(
                str(match.group(1) or "").casefold(),
                ("", 1.0),
            )
            base_unit = f"{numerator}/{denominator}"
            key = (base_unit, multiplier, bool(scale_name))
            if key not in seen:
                seen.add(key)
                specs.append(
                    {
                        "base_unit": base_unit,
                        "multiplier": multiplier,
                        "scale": scale_name or None,
                        "scale_explicit": bool(scale_name),
                        "source_text": raw,
                    }
                )
            compound_spans.append(match.span())

        connector_pattern = re.compile(
            r"(?i)(?<![A-Za-z0-9])"
            r"(?:(?:thousands?|millions?|billions?)\s+)?"
            rf"(?:{unit_atom_pattern})\s*(?:/|\bper\b)"
        )
        for match in connector_pattern.finditer(normalized):
            if any(
                start <= match.start() and match.end() <= end
                for start, end in compound_spans
            ):
                continue
            specs.append(
                {
                    "base_unit": None,
                    "multiplier": 1.0,
                    "scale": None,
                    "scale_explicit": False,
                    "source_text": raw,
                    "unsupported_compound": True,
                }
            )
        for match in unit_pattern.finditer(normalized):
            if any(
                start <= match.start() and match.end() <= end
                for start, end in compound_spans
            ):
                continue
            scale_name, multiplier = scale_aliases.get(
                str(match.group(1) or "").casefold(),
                ("", 1.0),
            )
            base_unit = canonical.get(str(match.group(2) or "").casefold())
            if not base_unit:
                continue
            key = (base_unit, multiplier, bool(scale_name))
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "base_unit": base_unit,
                    "multiplier": multiplier,
                    "scale": scale_name or None,
                    "scale_explicit": bool(scale_name),
                    "source_text": raw,
                }
            )
        scale_only = self._table_scale_details(raw)
        if scale_only and not any(spec.get("scale_explicit") for spec in specs):
            specs.append(
                {
                    "base_unit": None,
                    "multiplier": scale_only[1],
                    "scale": scale_only[0],
                    "scale_explicit": True,
                    "source_text": raw,
                }
            )
        return specs

    def _extract_explicit_table_unit_specs(
        self,
        text: object,
    ) -> List[Dict[str, Any]]:
        """Read table-wide units only from an explicit declaration."""
        raw = self._clean_table_measurement_text(text)
        if not raw:
            return []
        explicit = bool(
            re.search(
                r"(?i)\b(?:units?|uom|unit\s+of\s+measure)\s*[:=\-]",
                raw,
            )
            or re.search(
                r"(?i)\b(?:figures|amounts|values|data|results)\s+"
                r"(?:(?:are|shown|reported|expressed)\s+)?in\b",
                raw,
            )
        )
        if explicit:
            return self._extract_table_unit_specs(raw)

        parenthetical_specs = [
            spec
            for group in re.findall(r"[\(\[]([^\)\]]{1,120})[\)\]]", raw)
            for spec in self._extract_table_unit_specs(
                re.sub(r"(?i)^\s*in\s+", "", group).strip()
            )
        ]
        return parenthetical_specs

    @staticmethod
    def _coalesce_table_unit_specs(
        specs: Sequence[Dict[str, Any]],
        scope: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not specs:
            return None, None
        if any(spec.get("unsupported_compound") for spec in specs):
            return None, {
                "scope": scope,
                "candidates": [dict(spec) for spec in specs],
            }
        base_units = {
            str(spec.get("base_unit"))
            for spec in specs
            if spec.get("base_unit")
        }
        base_variants = {
            (
                str(spec.get("base_unit")),
                float(spec.get("multiplier") or 1.0),
            )
            for spec in specs
            if spec.get("base_unit")
        }
        scale_only_multipliers = {
            float(spec.get("multiplier") or 1.0)
            for spec in specs
            if not spec.get("base_unit") and spec.get("scale_explicit")
        }
        if (
            len(base_units) > 1
            or len(base_variants) > 1
            or len(scale_only_multipliers) > 1
        ):
            return None, {
                "scope": scope,
                "candidates": [dict(spec) for spec in specs],
            }
        base_unit = next(iter(base_units), None)
        base_multiplier = next(
            (variant[1] for variant in base_variants),
            1.0,
        )
        scale_only_multiplier = next(iter(scale_only_multipliers), None)
        if (
            scale_only_multiplier is not None
            and base_multiplier != 1.0
            and scale_only_multiplier != base_multiplier
        ):
            return None, {
                "scope": scope,
                "candidates": [dict(spec) for spec in specs],
            }
        multiplier = (
            scale_only_multiplier
            if scale_only_multiplier is not None
            else base_multiplier
        )
        scale_name = next(
            (
                str(spec.get("scale"))
                for spec in specs
                if spec.get("scale_explicit")
                and float(spec.get("multiplier") or 1.0) == multiplier
                and spec.get("scale")
            ),
            None,
        )
        sources = list(
            dict.fromkeys(
                str(spec.get("source_text") or "").strip()
                for spec in specs
                if str(spec.get("source_text") or "").strip()
            )
        )
        return {
            "base_unit": base_unit,
            "multiplier": multiplier,
            "scale": scale_name,
            "scale_explicit": bool(
                scale_only_multiplier is not None
                or any(spec.get("scale_explicit") for spec in specs)
            ),
            "scope": scope,
            "sources": sources,
        }, None

    @staticmethod
    def _render_table_unit(spec: Optional[Dict[str, Any]]) -> Optional[str]:
        if not spec or not spec.get("base_unit"):
            return None
        scale = str(spec.get("scale") or "").strip()
        return " ".join(
            value
            for value in (scale, str(spec.get("base_unit") or "").strip())
            if value
        )

    def _resolve_table_cell_unit(
        self,
        *,
        value_specs: Sequence[Dict[str, Any]],
        row_specs: Sequence[Dict[str, Any]],
        column_specs: Sequence[Dict[str, Any]],
        table_specs: Sequence[Dict[str, Any]],
        column_has_year: bool,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        column_scope = "year" if column_has_year else "column"
        value, value_ambiguity = self._coalesce_table_unit_specs(
            value_specs,
            "cell",
        )
        if value_ambiguity is not None:
            return None, value_ambiguity
        # An inline unit belongs to the value itself. Lower-scope ambiguity must
        # never erase that direct evidence or graft a table-level multiplier on it.
        if value and value.get("base_unit"):
            selected = dict(value)
            selected.setdefault("multiplier", 1.0)
            selected.setdefault("scale_explicit", False)
            return selected, None

        row, row_ambiguity = self._coalesce_table_unit_specs(row_specs, "row")
        if row_ambiguity is not None:
            return None, row_ambiguity
        column, column_ambiguity = self._coalesce_table_unit_specs(
            column_specs,
            column_scope,
        )

        row_base = row.get("base_unit") if row else None
        column_base = column.get("base_unit") if column else None
        if column_ambiguity is not None and not row_base:
            return None, column_ambiguity
        if column_ambiguity is not None:
            column = None
            column_base = None

        row_scale = (
            float(row.get("multiplier") or 1.0)
            if row and row.get("scale_explicit")
            else None
        )
        column_scale = (
            float(column.get("multiplier") or 1.0)
            if column and column.get("scale_explicit")
            else None
        )
        if row_base and column_base and row_base != column_base:
            return None, {
                "scope": "row_column",
                "candidates": [dict(row), dict(column)],
            }
        if (
            row_scale is not None
            and column_scale is not None
            and row_scale != column_scale
        ):
            return None, {
                "scope": "row_column",
                "candidates": [dict(row), dict(column)],
            }

        selected = dict(row or column or value or {})
        selected["base_unit"] = row_base or column_base
        if row_scale is not None:
            selected.update(
                multiplier=row_scale,
                scale=row.get("scale") if row else None,
                scale_explicit=True,
                scope="row",
            )
        elif column_scale is not None:
            selected.update(
                multiplier=column_scale,
                scale=column.get("scale") if column else None,
                scale_explicit=True,
                scope=column_scope,
            )
        elif value and value.get("scale_explicit"):
            selected.update(
                multiplier=float(value.get("multiplier") or 1.0),
                scale=value.get("scale"),
                scale_explicit=True,
                scope="cell",
            )

        table, table_ambiguity = self._coalesce_table_unit_specs(
            table_specs,
            "table",
        )
        selected_base = selected.get("base_unit")
        if not selected_base:
            if table_ambiguity is not None:
                return None, table_ambiguity
            if table:
                higher_scale = (
                    dict(selected) if selected.get("scale_explicit") else None
                )
                selected = dict(table)
                selected_base = selected.get("base_unit")
                if higher_scale is not None:
                    table_scale = (
                        float(table.get("multiplier") or 1.0)
                        if table.get("scale_explicit")
                        else None
                    )
                    higher_multiplier = float(
                        higher_scale.get("multiplier") or 1.0
                    )
                    if (
                        table_scale is not None
                        and table_scale != higher_multiplier
                    ):
                        return None, {
                            "scope": "higher_table",
                            "candidates": [higher_scale, dict(table)],
                        }
                    selected.update(
                        multiplier=higher_multiplier,
                        scale=higher_scale.get("scale"),
                        scale_explicit=True,
                        scope=higher_scale.get("scope") or "row",
                    )
        elif (
            not selected.get("scale_explicit")
            and table_ambiguity is None
            and table
            and table.get("scale_explicit")
            and (
                not table.get("base_unit")
                or table.get("base_unit") == selected_base
            )
        ):
            selected.update(
                multiplier=float(table.get("multiplier") or 1.0),
                scale=table.get("scale"),
                scale_explicit=True,
            )

        used_candidates = [
            candidate
            for candidate in (value, row, column, table)
            if candidate
            and (
                candidate is not table
                or not selected_base
                or not candidate.get("base_unit")
                or candidate.get("base_unit") == selected_base
            )
        ]
        selected["sources"] = list(
            dict.fromkeys(
                source
                for candidate in used_candidates
                for source in (candidate.get("sources") or [])
            )
        )
        if not selected or (
            not selected.get("base_unit") and not selected.get("scale_explicit")
        ):
            return None, None
        selected.setdefault("multiplier", 1.0)
        selected.setdefault("scale_explicit", False)
        return selected, None

    @staticmethod
    def _normalized_table_header_leaf(header_path: Sequence[str], fallback: str) -> str:
        value = str(header_path[-1] if header_path else fallback or "")
        value = re.sub(r"[^a-z0-9]+", " ", value.casefold())
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _table_header_common_prefix(
        first: Sequence[str],
        second: Sequence[str],
    ) -> int:
        count = 0
        for left, right in zip(first[:-1], second[:-1]):
            if str(left).strip().casefold() != str(right).strip().casefold():
                break
            count += 1
        return count

    @staticmethod
    def _table_cell_is_measurement_value(value: object) -> bool:
        text = ContentExtractor._clean_table_measurement_text(value)
        if not re.search(r"[-+]?\d", text):
            return False
        return not bool(
            re.fullmatch(
                r"(?i)(?:(?:FY|CY)\s*['\u2019]?\s*(?:19|20)?\d{2}|(?:19|20)\d{2})",
                text,
            )
        )

    def _table_segments_from_markdown(
        self,
        table_md: str,
        document_id: str,
        page: int,
        table_id: str,
        *,
        start_seq: int = 0,
        table_title: str = "",
        table_title_source: str = "",
        section_path: Optional[Sequence[str]] = None,
    ) -> List[TextSegment]:
        rows, physical_cells, quality = self._parse_table_details(table_md)
        if not rows:
            return []

        header_model = dict(quality.get("header_model") or {})
        headers = [str(value or "") for value in (header_model.get("headers") or [])]
        header_paths = [
            [str(value).strip() for value in path if str(value).strip()]
            for path in (header_model.get("header_paths") or [])
        ]
        data_row_indices = [
            int(index)
            for index in (header_model.get("data_row_indices") or [])
            if 0 <= int(index) < len(rows)
        ]
        width = max(
            [len(headers), *(len(row) for row in rows)]
            or [0]
        )
        if len(headers) < width:
            headers.extend(f"Column {index + 1}" for index in range(len(headers), width))
        if len(header_paths) < width:
            header_paths.extend([] for _ in range(width - len(header_paths)))
        physical_by_coord: Dict[Tuple[int, int], Dict[str, Any]] = {}
        physical_by_row: Dict[int, List[Dict[str, Any]]] = {}
        for cell in physical_cells:
            try:
                key = (int(cell.get("row_index")), int(cell.get("col_index")))
            except (TypeError, ValueError):
                continue
            physical_by_coord[key] = cell
            physical_by_row.setdefault(key[0], []).append(cell)

        unit_header_aliases = {
            "unit",
            "units",
            "uom",
            "unit of measure",
            "measurement unit",
            "\u5355\u4f4d",
            "\u8ba1\u91cf\u5355\u4f4d",
        }
        year_header_aliases = {
            "year",
            "fiscal year",
            "calendar year",
            "reporting year",
            "reporting period",
            "period",
            "\u5e74\u4efd",
            "\u62a5\u544a\u671f",
        }
        label_header_aliases = {
            "metric",
            "performance metric",
            "indicator",
            "line item",
            "description",
            "category",
            "topic",
            "name",
            "label",
            "scope",
            "sasb code",
            "gri code",
            "reference",
            "reference indices",
        }
        unit_columns = {
            index
            for index in range(width)
            if self._normalized_table_header_leaf(
                header_paths[index], headers[index]
            )
            in unit_header_aliases
        }
        year_columns = {
            index
            for index in range(width)
            if self._normalized_table_header_leaf(
                header_paths[index], headers[index]
            )
            in year_header_aliases
        }

        column_unit_specs: Dict[int, List[Dict[str, Any]]] = {}
        column_year_bindings: Dict[int, Dict[str, Any]] = {}
        column_year_ambiguities: Dict[int, List[int]] = {}
        for index in range(width):
            path = header_paths[index]
            if path:
                column_unit_specs[index] = self._extract_table_unit_specs(
                    " | ".join(path)
                )
                for label in reversed(path):
                    years = self._extract_table_years(label)
                    if len(years) == 1:
                        column_year_bindings[index] = {
                            "year": years[0],
                            "label": label,
                            "scope": "column_header",
                        }
                        break
                    if len(years) > 1:
                        column_year_ambiguities[index] = years
                        break

        caption = str(quality.get("caption") or "").strip()
        table_contexts = [
            value
            for value in (caption, table_title)
            if str(value or "").strip()
        ]
        table_unit_specs = [
            spec
            for context in table_contexts
            for spec in self._extract_explicit_table_unit_specs(context)
        ]
        table_scale = self._table_scale_details(table_md)
        if table_scale and not any(
            spec.get("scale_explicit") for spec in table_unit_specs
        ):
            table_unit_specs.append(
                {
                    "base_unit": None,
                    "multiplier": table_scale[1],
                    "scale": table_scale[0],
                    "scale_explicit": True,
                    "source_text": "table-level scale declaration",
                }
            )
        table_year_candidates = list(
            dict.fromkeys(
                year
                for context in table_contexts
                for year in self._extract_table_years(context)
            )
        )

        def scoped_entries_for_column(
            entries: Sequence[Tuple[int, Sequence[str], Any]],
            target_column: int,
        ) -> List[Any]:
            if not entries:
                return []
            if len(entries) == 1:
                _entry_column, entry_path, value = entries[0]
                normalized_entry_path = [
                    str(item).strip() for item in entry_path if str(item).strip()
                ]
                # A top-level Year/Unit column describes the whole row. A
                # declaration nested below a grouped header only applies to
                # sibling columns in that same branch.
                if (
                    len(normalized_entry_path) <= 1
                    or self._table_header_common_prefix(
                        header_paths[target_column],
                        normalized_entry_path,
                    )
                    > 0
                ):
                    return list(value) if isinstance(value, list) else [value]
                return []
            ranked = [
                (
                    self._table_header_common_prefix(
                        header_paths[target_column],
                        entry_path,
                    ),
                    entry_column,
                    value,
                )
                for entry_column, entry_path, value in entries
            ]
            best_score = max(item[0] for item in ranked)
            best = [item for item in ranked if item[0] == best_score]
            if best_score > 0 and len(best) == 1:
                value = best[0][2]
                return list(value) if isinstance(value, list) else [value]
            flattened: List[Any] = []
            for _score, _column, value in best:
                if isinstance(value, list):
                    flattened.extend(value)
                else:
                    flattened.append(value)
            return flattened

        segments: List[TextSegment] = []
        seq = start_seq
        common_quality = {
            "structure_confidence": quality["structure_confidence"],
            "ocr_confidence": quality["ocr_confidence"],
            "parse_pass": 1,
            "review_status": quality["review_status"],
            "quality_reasons": quality["reasons"],
            "quality_notes": quality.get("notes") or [],
            "conflicts": [],
            "semantic_schema_version": 1,
            "table_semantics_version": 2,
            "header_source": header_model.get("source"),
            "header_confirmed": bool(header_model.get("confirmed")),
            "header_row_indices": list(header_model.get("header_row_indices") or []),
        }

        for r_idx in data_row_indices:
            row = self._normalise_table_row(rows[r_idx], width=width)
            row_unit_entries: List[Tuple[int, Sequence[str], List[Dict[str, Any]]]] = []
            for unit_column in sorted(unit_columns):
                specs = self._extract_table_unit_specs(row[unit_column])
                if specs:
                    row_unit_entries.append(
                        (unit_column, header_paths[unit_column], specs)
                    )
            row_year_entries: List[Tuple[int, Sequence[str], Dict[str, Any]]] = []
            for year_column in sorted(year_columns):
                years = self._extract_table_years(row[year_column])
                if years:
                    row_year_entries.append(
                        (
                            year_column,
                            header_paths[year_column],
                            {
                                "years": years,
                                "label": row[year_column],
                                "scope": "row_year",
                            },
                        )
                    )

            seq += 1
            explicit_row_headers = [
                str(cell.get("text") or "").strip()
                for cell in sorted(
                    physical_by_row.get(r_idx, []),
                    key=lambda item: int(item.get("col_index") or 0),
                )
                if str(cell.get("text") or "").strip()
                and (
                    str(cell.get("scope") or "").lower() in {"row", "rowgroup"}
                    or (
                        bool(cell.get("is_header"))
                        and r_idx not in set(header_model.get("header_row_indices") or [])
                    )
                )
            ]
            row_header_path = list(dict.fromkeys(explicit_row_headers))
            row_header = (
                " > ".join(row_header_path)
                if row_header_path
                else self._infer_row_header(headers, row)
            )
            if not row_header_path and row_header:
                row_header_path = [row_header]
            row_text = self._format_table_row_context(headers, row, table_title=table_title, page=page)
            row_segment_id = f"{document_id}_p{page}_s{seq}"
            row_segment = TextSegment(
                segment_id=row_segment_id,
                content=row_text,
                page_number=page,
                position_y=float(seq),
                position_x=0.0,
                segment_type="table_row",
                source_table_id=table_id,
                row_header=row_header,
                structure_confidence=quality["structure_confidence"],
                ocr_confidence=quality["ocr_confidence"],
                parse_pass=1,
                review_status=quality["review_status"],
                structured_data={
                    "source": "paddleocr_vl_table_parser",
                    "parser": "paddleocr-vl",
                    "table_id": table_id,
                    "table_title": table_title,
                    "table_title_source": table_title_source,
                    "row_index": r_idx,
                    "row_header": row_header,
                    "row_header_path": row_header_path,
                    "row_text": row_text,
                    "column_headers": headers,
                    "header_paths": header_paths,
                    "section_path": list(section_path or []),
                    **common_quality,
                },
            )
            segments.append(row_segment)
            row_semantic_reasons: List[str] = []

            for c_idx, value in enumerate(row):
                if not str(value).strip():
                    continue
                seq += 1
                col_header = headers[c_idx] if c_idx < len(headers) else f"col_{c_idx + 1}"
                physical = physical_by_coord.get((r_idx, c_idx), {})
                header_path = list(header_paths[c_idx]) if c_idx < len(header_paths) else []
                header_leaf = self._normalized_table_header_leaf(
                    header_path,
                    col_header,
                )
                explicit_row_label_cell = (
                    str(physical.get("scope") or "").lower() in {"row", "rowgroup"}
                    or (
                        bool(physical.get("is_header"))
                        and r_idx
                        not in set(header_model.get("header_row_indices") or [])
                    )
                    or (
                        bool(row_header)
                        and str(value).strip().casefold()
                        == str(row_header).strip().casefold()
                    )
                )
                measurement_cell = (
                    c_idx not in unit_columns
                    and c_idx not in year_columns
                    and header_leaf not in label_header_aliases
                    and not explicit_row_label_cell
                    and self._table_cell_is_measurement_value(value)
                )
                semantic_reasons: List[str] = []

                year_binding: Optional[Dict[str, Any]] = None
                if measurement_cell:
                    year_scope_candidates: List[Dict[str, Any]] = []
                    ambiguous_year_scope = False
                    inline_years = self._extract_table_years(value)
                    if len(inline_years) > 1:
                        ambiguous_year_scope = True
                    elif inline_years:
                        year_scope_candidates.append(
                            {
                                "year": inline_years[0],
                                "label": str(value).strip(),
                                "scope": "cell",
                            }
                        )
                    if c_idx in column_year_ambiguities:
                        ambiguous_year_scope = True
                    row_year_candidates = scoped_entries_for_column(
                        row_year_entries,
                        c_idx,
                    )
                    row_years = list(
                        dict.fromkeys(
                            year
                            for candidate in row_year_candidates
                            if isinstance(candidate, dict)
                            for year in (candidate.get("years") or [])
                        )
                    )
                    column_year = column_year_bindings.get(c_idx)
                    if len(row_years) > 1:
                        ambiguous_year_scope = True
                    elif row_years:
                        source = next(
                            (
                                candidate
                                for candidate in row_year_candidates
                                if isinstance(candidate, dict)
                                and row_years[0] in (candidate.get("years") or [])
                            ),
                                {},
                            )
                        year_scope_candidates.append(
                            {
                                "year": row_years[0],
                                "label": source.get("label"),
                                "scope": "row_year",
                            }
                        )
                    if column_year:
                        year_scope_candidates.append(dict(column_year))
                    # A caption/title year is only a fallback. A common title
                    # such as "ESG Data 2023-2024" must not erase the exact
                    # FY23/FY24 binding already supplied by a column header.
                    if not year_scope_candidates:
                        if len(table_year_candidates) == 1:
                            year_scope_candidates.append(
                                {
                                    "year": table_year_candidates[0],
                                    "label": next(
                                        (
                                            context
                                            for context in table_contexts
                                            if table_year_candidates[0]
                                            in self._extract_table_years(context)
                                        ),
                                        str(table_year_candidates[0]),
                                    ),
                                    "scope": "table",
                                }
                            )
                        elif len(table_year_candidates) > 1:
                            ambiguous_year_scope = True

                    scoped_years = list(
                        dict.fromkeys(
                            int(candidate["year"])
                            for candidate in year_scope_candidates
                        )
                    )
                    if ambiguous_year_scope:
                        semantic_reasons.append("ambiguous_year_scope")
                    elif len(scoped_years) > 1:
                        semantic_reasons.append("conflicting_year_scope")
                    elif year_scope_candidates:
                        year_binding = dict(year_scope_candidates[0])

                row_specs = scoped_entries_for_column(row_unit_entries, c_idx)
                value_specs = (
                    self._extract_table_unit_specs(value)
                    if measurement_cell
                    else []
                )
                unit_spec: Optional[Dict[str, Any]] = None
                unit_ambiguity: Optional[Dict[str, Any]] = None
                if measurement_cell:
                    unit_spec, unit_ambiguity = self._resolve_table_cell_unit(
                        value_specs=value_specs,
                        row_specs=[
                            spec for spec in row_specs if isinstance(spec, dict)
                        ],
                        column_specs=column_unit_specs.get(c_idx, []),
                        table_specs=table_unit_specs,
                        column_has_year=year_binding is not None
                        and year_binding.get("scope") == "column_header",
                    )
                    if unit_ambiguity is not None:
                        semantic_reasons.append("ambiguous_unit_scope")

                semantic_reasons = list(dict.fromkeys(semantic_reasons))
                cell_quality_reasons = list(
                    dict.fromkeys(
                        [*(quality.get("reasons") or []), *semantic_reasons]
                    )
                )
                review_status = (
                    "needs_review"
                    if cell_quality_reasons
                    else quality["review_status"]
                )
                cell_content_parts = []
                if table_title:
                    cell_content_parts.append(f"[Table Title] {table_title}")
                if headers:
                    cell_content_parts.append(f"[Column Headers] {' | '.join(headers)}")
                if header_path:
                    cell_content_parts.append(
                        f"[Column Header Path] {' > '.join(header_path)}"
                    )
                cell_content_parts.append(f"[Row Context] {row_text}")
                cell_content_parts.append(f"{col_header}: {value}")
                cell_data: Dict[str, Any] = {
                    "source": "paddleocr_vl_table_parser",
                    "parser": "paddleocr-vl",
                    "table_id": table_id,
                    "table_title": table_title,
                    "table_title_source": table_title_source,
                    "row_index": r_idx,
                    "col_index": c_idx,
                    "row_header": row_header,
                    "row_header_path": row_header_path,
                    "col_header": col_header,
                    "value_text": value,
                    "column_headers": headers,
                    "header_path": header_path,
                    "section_path": list(section_path or []),
                    "row_text": row_text,
                    "row_segment_id": row_segment_id,
                    "rowspan": int(physical.get("rowspan") or 1),
                    "colspan": int(physical.get("colspan") or 1),
                    "bbox": physical.get("bbox"),
                    **common_quality,
                    "review_status": review_status,
                    "quality_reasons": cell_quality_reasons,
                }
                rendered_unit = self._render_table_unit(unit_spec)
                if year_binding is not None and not any(
                    reason in semantic_reasons
                    for reason in {"ambiguous_year_scope", "conflicting_year_scope"}
                ):
                    cell_data.update(
                        {
                            "year": int(year_binding["year"]),
                            "source_year_label": str(year_binding.get("label") or "").strip()
                            or None,
                            "year_scope": year_binding.get("scope"),
                        }
                    )
                if unit_spec is not None and unit_ambiguity is None:
                    cell_data.update(
                        {
                            "unit": rendered_unit,
                            "raw_unit": rendered_unit,
                            "unit_base": unit_spec.get("base_unit"),
                            "unit_multiplier": float(unit_spec.get("multiplier") or 1.0),
                            "unit_scope": unit_spec.get("scope"),
                            "unit_sources": list(unit_spec.get("sources") or []),
                        }
                    )
                elif unit_ambiguity is not None:
                    cell_data["unit_candidates"] = unit_ambiguity.get("candidates") or []
                    cell_data["unit_ambiguity_scope"] = unit_ambiguity.get("scope")
                segments.append(
                    TextSegment(
                        segment_id=f"{document_id}_p{page}_s{seq}",
                        content="\n".join(cell_content_parts),
                        page_number=page,
                        position_y=float(seq),
                        position_x=float(c_idx),
                        segment_type="table_cell",
                        source_table_id=table_id,
                        row_header=row_header,
                        col_header=col_header,
                        value_text=value,
                        unit=rendered_unit if unit_ambiguity is None else None,
                        structure_confidence=quality["structure_confidence"],
                        ocr_confidence=quality["ocr_confidence"],
                        header_path=header_path,
                        rowspan=int(physical.get("rowspan") or 1),
                        colspan=int(physical.get("colspan") or 1),
                        parse_pass=1,
                        review_status=review_status,
                        structured_data=cell_data,
                    )
                )
                row_semantic_reasons.extend(semantic_reasons)

            if row_semantic_reasons:
                row_data = dict(row_segment.structured_data or {})
                row_reasons = list(
                    dict.fromkeys(
                        [
                            *(row_data.get("quality_reasons") or []),
                            *row_semantic_reasons,
                        ]
                    )
                )
                row_data["quality_reasons"] = row_reasons
                row_data["review_status"] = "needs_review"
                row_segment.review_status = "needs_review"
                row_segment.structured_data = row_data
        return segments

    def _enrich_segments_from_native_layout(self, segments: List[TextSegment], analysis: Any) -> None:
        """Attach page geometry and reading order from the native PDF preflight."""
        profiles: Dict[int, Any] = {}
        for fallback_page, profile in enumerate(list(getattr(analysis, "pages", []) or []), 1):
            try:
                page = max(1, int(getattr(profile, "page_number", fallback_page) or fallback_page))
            except Exception:
                page = fallback_page
            profiles[page] = profile

        for page, profile in profiles.items():
            raw_blocks = list(getattr(profile, "native_blocks", []) or [])
            blocks: list[dict[str, Any]] = []
            for order, raw in enumerate(raw_blocks):
                if isinstance(raw, dict):
                    getter = raw.get
                else:
                    getter = lambda name, default=None, item=raw: getattr(item, name, default)
                text = str(getter("text", "") or "").strip()
                key = _normalise_page_text(text)
                # ``page_parser`` deliberately preserves both PDF-point and
                # normalized coordinates.  Retrieval/layout metadata always
                # uses normalized boxes, so prefer that representation and
                # only normalize the absolute fallback here.
                bbox = getter("normalized_bbox", None)
                bbox_is_normalized = bbox is not None
                if bbox is None:
                    bbox = getter("bbox", None)
                if not key or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                    continue
                try:
                    raw_bbox = [float(value) for value in bbox[:4]]
                    if bbox_is_normalized:
                        normalized_bbox = raw_bbox
                    else:
                        page_width = float(getattr(profile, "page_width", 0.0) or 0.0)
                        page_height = float(getattr(profile, "page_height", 0.0) or 0.0)
                        if page_width <= 0.0 or page_height <= 0.0:
                            continue
                        normalized_bbox = [
                            raw_bbox[0] / page_width,
                            raw_bbox[1] / page_height,
                            raw_bbox[2] / page_width,
                            raw_bbox[3] / page_height,
                        ]
                    normalized_bbox = [max(0.0, min(1.0, value)) for value in normalized_bbox]
                except Exception:
                    continue
                reading_order = getter("reading_order", None)
                try:
                    reading_order = order if reading_order is None else int(reading_order)
                except (TypeError, ValueError):
                    reading_order = order
                blocks.append(
                    {
                        "text": text,
                        "key": key,
                        "bbox": normalized_bbox,
                        "block_type": str(getter("block_type", "text") or "text"),
                        "reading_order": reading_order,
                    }
                )

            route = str(getattr(profile, "route", "ocr") or "ocr").strip().lower()
            page_width = float(getattr(profile, "page_width", 0.0) or 0.0)
            page_height = float(getattr(profile, "page_height", 0.0) or 0.0)
            page_segments = [segment for segment in segments if int(segment.page_number or 1) == page]
            for segment in page_segments:
                data = dict(segment.structured_data or {})
                data.update(
                    {
                        "parser_route": route,
                        "page_width": page_width or None,
                        "page_height": page_height or None,
                    }
                )
                segment.structured_data = data
                # Paddle's structured table/visual adapter has more precise
                # geometry than a flattened native text block.  Never replace
                # those boxes during the native-text enrichment pass.
                if segment.segment_type in {"table", "table_row", "table_cell", "chart", "figure", "link_anchor"}:
                    continue
                segment_key = _normalise_page_text(segment.content)
                if not segment_key:
                    continue
                best_score = 0.0
                best: Optional[dict[str, Any]] = None
                for block in blocks:
                    block_key = str(block["key"])
                    if segment_key == block_key:
                        score = 1.0
                    elif segment_key in block_key or block_key in segment_key:
                        score = min(len(segment_key), len(block_key)) / max(len(segment_key), len(block_key))
                    else:
                        score = SequenceMatcher(None, segment_key, block_key, autojunk=False).ratio()
                    if score > best_score:
                        best_score = score
                        best = block
                if best is None or best_score < 0.55:
                    continue
                bbox = list(best["bbox"])
                segment.position_x = bbox[0]
                segment.position_y = bbox[1]
                block_type = str(best["block_type"] or "text").lower()
                if segment.segment_type == "text" and any(
                    token in block_type for token in ("title", "heading", "section_header")
                ):
                    segment.segment_type = "heading"
                data = dict(segment.structured_data or {})
                data.update(
                    {
                        "bbox": bbox,
                        "block_type": block_type,
                        "reading_order": int(best["reading_order"]),
                        "native_layout_match": round(best_score, 4),
                    }
                )
                segment.structured_data = data

    def _enrich_table_segments_from_records(
        self,
        segments: List[TextSegment],
        records: List[Dict[str, Any]],
    ) -> Dict[str, set[Tuple[int, str]]]:
        """Bind Markdown tables to Paddle's structured records one-to-one.

        Paddle emits Markdown and JSON through separate serializers, so their
        ordering can diverge when a page contains nested figures or repeated
        tables.  Pairing merely by list index silently attaches the wrong
        geometry and confidence.  We instead require the same global page and
        score text identity and bbox IoU (when available).  A record is bound
        only when the table and record select each other as a unique best match
        with a non-zero safety margin.  Reading order is never identity evidence
        when either bbox is unavailable.
        """
        table_segments = [
            segment
            for segment in segments
            if segment.segment_type == "table" and segment.source_table_id
        ]
        by_page: Dict[int, List[TextSegment]] = {}
        for segment in table_segments:
            by_page.setdefault(int(segment.page_number), []).append(segment)
        record_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for record in records:
            try:
                page_number = max(1, int(record.get("page_number") or 1))
            except (TypeError, ValueError, OverflowError):
                continue
            record_by_page.setdefault(page_number, []).append(record)

        def record_key(record: Dict[str, Any], page: int) -> Tuple[int, str]:
            record_id = str(record.get("table_id") or "").strip()
            if not record_id:
                identity = json.dumps(
                    {
                        "html": hashlib.sha256(
                            str(record.get("pred_html") or "").encode("utf-8")
                        ).hexdigest(),
                        "bbox": record.get("bbox"),
                        "reading_order": record.get("reading_order"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                record_id = "anonymous:" + hashlib.sha256(
                    identity.encode("utf-8")
                ).hexdigest()[:20]
            return page, record_id

        binding_summary: Dict[str, set[Tuple[int, str]]] = {
            "matched_record_keys": set(),
            "ambiguous_record_keys": set(),
        }

        def safe_confidence(value: Any, fallback: float) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError, OverflowError):
                parsed = float(fallback)
            return max(0.0, min(1.0, parsed))

        def rows_similarity(first: Sequence[Sequence[Any]], second: Sequence[Sequence[Any]]) -> float:
            left = _normalised_table_text(first)
            right = _normalised_table_text(second)
            if not left or not right:
                return 0.0
            if left == right:
                return 1.0
            sequence_score = SequenceMatcher(None, left, right, autojunk=False).ratio()
            left_tokens = set(re.findall(r"[\w%+.-]+", left, flags=re.UNICODE))
            right_tokens = set(re.findall(r"[\w%+.-]+", right, flags=re.UNICODE))
            union = left_tokens | right_tokens
            token_score = len(left_tokens & right_tokens) / len(union) if union else 0.0
            return max(sequence_score, token_score)

        def record_sort_key(record: Dict[str, Any]) -> tuple:
            reading_order = record.get("reading_order")
            try:
                order_value = int(reading_order) if reading_order is not None else 1_000_000
            except (TypeError, ValueError, OverflowError):
                order_value = 1_000_000
            bbox = _normalized_bbox(
                record.get("bbox"),
                page_width=float(record.get("page_width") or 0.0),
                page_height=float(record.get("page_height") or 0.0),
            )
            return (order_value, bbox[1] if bbox else 1.0, bbox[0] if bbox else 1.0)

        def cell_bbox_map(
            record: Dict[str, Any],
            raw_cells: Sequence[Dict[str, Any]],
        ) -> Dict[tuple[int, int], List[float]]:
            raw_boxes = record.get("cell_box_list")
            if not isinstance(raw_boxes, list) or not raw_boxes or not raw_cells:
                return {}
            if len(raw_boxes) != len(raw_cells):
                # Paddle boxes are positional. Once the physical-cell counts
                # diverge (for example because an empty row was omitted), every
                # later zip entry can point at the wrong cell.
                return {}
            boxes: List[Optional[List[float]]] = []
            for raw_box in raw_boxes:
                if isinstance(raw_box, dict):
                    raw_box = raw_box.get("bbox") or raw_box.get("box") or raw_box.get("points")
                boxes.append(_bbox_values(raw_box))
            valid_boxes = [box for box in boxes if box is not None]
            if not valid_boxes:
                return {}

            table_bbox = _normalized_bbox(
                record.get("bbox"),
                page_width=float(record.get("page_width") or 0.0),
                page_height=float(record.get("page_height") or 0.0),
            )
            table_pixels = _bbox_values(record.get("bbox_pixels"))
            page_width = float(record.get("page_width") or 0.0)
            page_height = float(record.get("page_height") or 0.0)
            union = _bbox_union(valid_boxes)
            assert union is not None
            normalized_input = max(abs(item) for box in valid_boxes for item in box) <= 1.0001

            coordinate_space = "page"
            if normalized_input and table_bbox:
                margin = 0.01
                contained = (
                    union[0] >= table_bbox[0] - margin
                    and union[1] >= table_bbox[1] - margin
                    and union[2] <= table_bbox[2] + margin
                    and union[3] <= table_bbox[3] + margin
                )
                coordinate_space = "page" if contained else "table_crop"
            elif not normalized_input and table_pixels:
                margin = 2.0
                contained = (
                    union[0] >= table_pixels[0] - margin
                    and union[1] >= table_pixels[1] - margin
                    and union[2] <= table_pixels[2] + margin
                    and union[3] <= table_pixels[3] + margin
                )
                table_width = max(1.0, table_pixels[2] - table_pixels[0])
                table_height = max(1.0, table_pixels[3] - table_pixels[1])
                crop_sized = union[0] >= -margin and union[1] >= -margin and union[2] <= table_width + margin and union[3] <= table_height + margin
                coordinate_space = "page" if contained else ("table_crop" if crop_sized else "page")

            mapped: Dict[tuple[int, int], List[float]] = {}
            for cell, box in zip(raw_cells, boxes):
                if box is None:
                    continue
                if coordinate_space == "table_crop" and table_bbox:
                    if normalized_input:
                        relative = box
                    elif table_pixels:
                        width = max(1.0, table_pixels[2] - table_pixels[0])
                        height = max(1.0, table_pixels[3] - table_pixels[1])
                        relative = [box[0] / width, box[1] / height, box[2] / width, box[3] / height]
                    else:
                        continue
                    table_width = table_bbox[2] - table_bbox[0]
                    table_height = table_bbox[3] - table_bbox[1]
                    normalized = [
                        table_bbox[0] + relative[0] * table_width,
                        table_bbox[1] + relative[1] * table_height,
                        table_bbox[0] + relative[2] * table_width,
                        table_bbox[1] + relative[3] * table_height,
                    ]
                else:
                    normalized = _normalized_bbox(
                        box,
                        page_width=page_width,
                        page_height=page_height,
                    )
                    if normalized is None:
                        continue
                try:
                    key = (int(cell.get("row_index")), int(cell.get("col_index")))
                except (TypeError, ValueError, OverflowError):
                    continue
                mapped[key] = [round(max(0.0, min(1.0, value)), 6) for value in normalized]
            return mapped

        structure_threshold = float(os.getenv("REPORT_TABLE_STRUCTURE_CONFIDENCE_THRESHOLD", "0.80") or "0.80")
        ocr_threshold = float(os.getenv("REPORT_TABLE_OCR_CONFIDENCE_THRESHOLD", "0.75") or "0.75")
        for page, page_tables in by_page.items():
            page_tables = sorted(page_tables, key=lambda item: (item.position_y, item.position_x or 0.0))
            page_records = sorted(record_by_page.get(page, []), key=record_sort_key)
            if not page_records:
                continue

            parsed_tables = [self._parse_table_rows(segment.content) for segment in page_tables]
            parsed_records = [self._parse_table_rows(str(record.get("pred_html") or "")) for record in page_records]
            table_families = {
                str(table.source_table_id): [
                    segment
                    for segment in segments
                    if segment.source_table_id == table.source_table_id
                ]
                for table in page_tables
            }
            record_families: List[List[TextSegment]] = []
            for record_index, record in enumerate(page_records):
                raw_html = str(record.get("pred_html") or "")
                synthetic_table_id = f"record-match-p{page}-{record_index}"
                family = [
                    TextSegment(
                        segment_id=f"{synthetic_table_id}-table",
                        content=raw_html,
                        page_number=page,
                        position_y=0.0,
                        segment_type="table",
                        source_table_id=synthetic_table_id,
                    )
                ]
                family.extend(
                    self._table_segments_from_markdown(
                        raw_html,
                        synthetic_table_id,
                        page,
                        synthetic_table_id,
                    )
                )
                record_families.append(family)

            candidates: List[tuple[float, float, float, int, int]] = []
            plausible_record_indices: set[int] = set()
            plausible_edges: set[Tuple[int, int]] = set()
            for table_index, table_segment in enumerate(page_tables):
                table_bbox = _normalized_bbox((table_segment.structured_data or {}).get("bbox"))
                for record_index, record in enumerate(page_records):
                    record_bbox = _normalized_bbox(
                        record.get("bbox"),
                        page_width=float(record.get("page_width") or 0.0),
                        page_height=float(record.get("page_height") or 0.0),
                    )
                    text_score = rows_similarity(parsed_tables[table_index], parsed_records[record_index])
                    bbox_score = _bbox_iou(table_bbox, record_bbox)
                    span = max(len(page_tables), len(page_records), 1)
                    order_score = max(0.0, 1.0 - abs(table_index - record_index) / span)
                    if table_bbox is not None and record_bbox is not None:
                        score = 0.65 * text_score + 0.30 * bbox_score + 0.05 * order_score
                    else:
                        score = text_score
                    semantic_compatible = bool(
                        table_family := table_families.get(
                            str(table_segment.source_table_id or ""),
                            [],
                        )
                    ) and self._second_pass_table_identity_compatible(
                        table_family,
                        record_families[record_index],
                    )
                    # Keep a wider ambiguity graph than the acceptance graph.
                    # A record that visibly overlaps an existing page table but
                    # misses a strict threshold must not be reinterpreted as an
                    # unrelated JSON-only table and appended as a duplicate.
                    if (
                        semantic_compatible
                        or text_score >= 0.45
                        or (
                            table_bbox is not None
                            and record_bbox is not None
                            and bbox_score >= 0.10
                        )
                    ):
                        plausible_record_indices.add(record_index)
                        plausible_edges.add((table_index, record_index))
                    # Explicitly disjoint geometry always blocks binding, even
                    # on singleton pages.  The plausible edge above is retained
                    # so a text-identical but misplaced record is classified as
                    # ambiguous instead of being appended as a JSON-only table.
                    if (
                        table_bbox is not None
                        and record_bbox is not None
                        and bbox_score < 0.10
                    ):
                        continue
                    # A same-page singleton is not an identity signal. Keep a
                    # minimum content/geometry gate and require compatible
                    # header, year, unit and row-label axes before admitting an
                    # edge to the global candidate graph.
                    if score < 0.45 or (text_score < 0.35 and bbox_score < 0.20):
                        continue
                    if not semantic_compatible:
                        continue
                    candidates.append((score, text_score, bbox_score, table_index, record_index))

            try:
                match_margin = float(
                    os.getenv("REPORT_TABLE_SECOND_PASS_MATCH_MARGIN", "0.10")
                    or "0.10"
                )
            except (TypeError, ValueError, OverflowError):
                match_margin = 0.10
            match_margin = max(0.10, min(0.50, match_margin))

            edges_by_table: Dict[
                int, List[tuple[float, float, float, int, int]]
            ] = {}
            edges_by_record: Dict[
                int, List[tuple[float, float, float, int, int]]
            ] = {}
            for edge in candidates:
                edges_by_table.setdefault(edge[3], []).append(edge)
                edges_by_record.setdefault(edge[4], []).append(edge)

            def unique_best(
                edges: Sequence[tuple[float, float, float, int, int]],
            ) -> Optional[Tuple[int, int]]:
                ranked = sorted(edges, key=lambda item: item[0], reverse=True)
                if not ranked:
                    return None
                if (
                    len(ranked) > 1
                    and float(ranked[0][0]) - float(ranked[1][0])
                    < match_margin
                ):
                    return None
                return ranked[0][3], ranked[0][4]

            best_by_table = {
                table_index: unique_best(edges)
                for table_index, edges in edges_by_table.items()
            }
            best_by_record = {
                record_index: unique_best(edges)
                for record_index, edges in edges_by_record.items()
            }
            accepted_candidates = [
                edge
                for edge in candidates
                if best_by_table.get(edge[3]) == (edge[3], edge[4])
                and best_by_record.get(edge[4]) == (edge[3], edge[4])
            ]

            matches: List[tuple[TextSegment, Dict[str, Any], float, float, float]] = []
            matched_record_indices: set[int] = set()
            for score, text_score, bbox_score, table_index, record_index in sorted(
                accepted_candidates,
                reverse=True,
            ):
                matched_record_indices.add(record_index)
                binding_summary["matched_record_keys"].add(
                    record_key(page_records[record_index], page)
                )
                matches.append((page_tables[table_index], page_records[record_index], score, text_score, bbox_score))

            ambiguous_record_indices = (
                plausible_record_indices - matched_record_indices
            )
            for record_index in ambiguous_record_indices:
                binding_summary["ambiguous_record_keys"].add(
                    record_key(page_records[record_index], page)
                )

            # Preserve the ambiguity as actionable quality evidence without
            # attaching any record provenance to a table that was not safely
            # matched. This keeps the family eligible for a selective repair
            # pass while preventing a false table_record_id association.
            for table_index, record_index in plausible_edges:
                if record_index not in ambiguous_record_indices:
                    continue
                table_segment = page_tables[table_index]
                markdown_rows, _markdown_cells, markdown_quality = (
                    self._parse_table_details(table_segment.content)
                )
                raw_rows, _raw_cells, raw_quality = self._parse_table_details(
                    str(page_records[record_index].get("pred_html") or "")
                )
                markdown_header_rows = list(
                    (markdown_quality.get("header_model") or {}).get(
                        "header_row_indices"
                    )
                    or []
                )
                raw_header_rows = list(
                    (raw_quality.get("header_model") or {}).get(
                        "header_row_indices"
                    )
                    or []
                )
                row_shapes_match = [len(row) for row in raw_rows] == [
                    len(row) for row in markdown_rows
                ]
                row_coordinates_match = (
                    len(raw_rows) == len(markdown_rows)
                    and all(
                        _normalised_table_text([raw_row])
                        == _normalised_table_text([markdown_row])
                        for raw_row, markdown_row in zip(
                            raw_rows,
                            markdown_rows,
                        )
                    )
                )
                geometry_alignment_mismatch = not (
                    raw_rows
                    and markdown_rows
                    and raw_header_rows == markdown_header_rows
                    and row_shapes_match
                    and row_coordinates_match
                )
                for related_segment in table_families.get(
                    str(table_segment.source_table_id or ""),
                    [],
                ):
                    data = dict(related_segment.structured_data or {})
                    reasons = list(
                        dict.fromkeys(
                            [
                                *(data.get("quality_reasons") or []),
                                "structure_source_conflict",
                                "ambiguous_table_record_match",
                                *(
                                    ["cell_geometry_alignment_mismatch"]
                                    if geometry_alignment_mismatch
                                    else []
                                ),
                            ]
                        )
                    )
                    conflicts = [
                        dict(conflict)
                        for conflict in (
                            related_segment.conflicts
                            or data.get("conflicts")
                            or []
                        )
                        if isinstance(conflict, dict)
                    ]
                    conflict = {
                        "type": "ambiguous_table_record_match",
                        "record_id": page_records[record_index].get("table_id"),
                    }
                    conflict_key = json.dumps(
                        conflict,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    if all(
                        json.dumps(
                            existing,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        )
                        != conflict_key
                        for existing in conflicts
                    ):
                        conflicts.append(conflict)
                    if geometry_alignment_mismatch:
                        alignment_conflict = {
                            "type": "cell_geometry_alignment_mismatch",
                            "markdown_header_rows": markdown_header_rows,
                            "structured_header_rows": raw_header_rows,
                            "row_shapes_match": row_shapes_match,
                            "row_coordinates_match": row_coordinates_match,
                        }
                        alignment_key = json.dumps(
                            alignment_conflict,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        )
                        if all(
                            json.dumps(
                                existing,
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            )
                            != alignment_key
                            for existing in conflicts
                        ):
                            conflicts.append(alignment_conflict)
                    related_segment.review_status = "needs_review"
                    related_segment.conflicts = conflicts
                    data.update(
                        {
                            "review_status": "needs_review",
                            "quality_reasons": reasons,
                            "conflicts": conflicts,
                        }
                    )
                    related_segment.structured_data = data

            for table_segment, record, match_score, text_score, bbox_score in matches:
                raw_html = str(record.get("pred_html") or "")
                raw_rows, raw_cells, raw_quality = self._parse_table_details(raw_html)
                markdown_rows, _markdown_cells, markdown_quality = (
                    self._parse_table_details(table_segment.content)
                )
                structure_confidence = safe_confidence(record.get("structure_confidence"), raw_quality["structure_confidence"])
                raw_ocr_confidence = record.get("ocr_confidence")
                ocr_confidence = (
                    safe_confidence(raw_ocr_confidence, 0.0)
                    if raw_ocr_confidence is not None
                    else None
                )
                reasons = list(raw_quality["reasons"])
                quality_notes: List[str] = []
                if raw_ocr_confidence is None:
                    quality_notes.append("missing_ocr_confidence")
                if structure_confidence < structure_threshold:
                    reasons.append("low_structure_confidence")
                if ocr_confidence is not None and ocr_confidence < ocr_threshold:
                    reasons.append("low_ocr_confidence")
                if match_score < 0.55:
                    reasons.append("weak_table_record_match")

                conflicts: List[Dict[str, Any]] = []
                source_similarity = rows_similarity(raw_rows, markdown_rows)
                if raw_rows and markdown_rows and source_similarity < 0.82:
                    conflicts.append(
                        {
                            "type": "table_structure_mismatch",
                            "similarity": round(source_similarity, 4),
                            "first_pass_markdown": [self._normalise_table_row(row) for row in markdown_rows],
                            "paddle_structured_html": [self._normalise_table_row(row) for row in raw_rows],
                        }
                    )
                    reasons.append("structure_source_conflict")

                raw_header_rows = list(
                    (raw_quality.get("header_model") or {}).get(
                        "header_row_indices"
                    )
                    or []
                )
                markdown_header_rows = list(
                    (markdown_quality.get("header_model") or {}).get(
                        "header_row_indices"
                    )
                    or []
                )
                row_shapes_match = [len(row) for row in raw_rows] == [
                    len(row) for row in markdown_rows
                ]
                row_coordinates_match = (
                    len(raw_rows) == len(markdown_rows)
                    and all(
                        _normalised_table_text([raw_row])
                        == _normalised_table_text([markdown_row])
                        for raw_row, markdown_row in zip(
                            raw_rows,
                            markdown_rows,
                        )
                    )
                )
                coordinate_alignment_safe = bool(
                    raw_rows
                    and markdown_rows
                    and raw_header_rows == markdown_header_rows
                    and row_shapes_match
                    and row_coordinates_match
                )
                if not coordinate_alignment_safe:
                    conflicts.append(
                        {
                            "type": "cell_geometry_alignment_mismatch",
                            "markdown_header_rows": markdown_header_rows,
                            "structured_header_rows": raw_header_rows,
                            "row_shapes_match": row_shapes_match,
                            "row_coordinates_match": row_coordinates_match,
                        }
                    )
                    reasons.append("cell_geometry_alignment_mismatch")

                raw_box_list = record.get("cell_box_list")
                cell_box_count_matches = not (
                    isinstance(raw_box_list, list)
                    and raw_box_list
                    and len(raw_box_list) != len(raw_cells)
                )
                if not cell_box_count_matches:
                    conflicts.append(
                        {
                            "type": "cell_bbox_count_mismatch",
                            "cell_count": len(raw_cells),
                            "bbox_count": len(raw_box_list),
                        }
                    )
                    reasons.append("cell_bbox_count_mismatch")
                reasons = list(dict.fromkeys(reasons))
                review_status = (
                    "needs_review"
                    if reasons or conflicts
                    else ("unverified" if quality_notes else "verified")
                )
                table_bbox = _normalized_bbox(
                    record.get("bbox"),
                    page_width=float(record.get("page_width") or 0.0),
                    page_height=float(record.get("page_height") or 0.0),
                )
                boxes_by_cell = (
                    cell_bbox_map(record, raw_cells)
                    if coordinate_alignment_safe and cell_box_count_matches
                    else {}
                )
                try:
                    record_parse_pass = max(1, int(record.get("parse_pass") or 1))
                except (TypeError, ValueError, OverflowError):
                    record_parse_pass = 1
                row_boxes: Dict[int, List[float]] = {}
                for row_index in {key[0] for key in boxes_by_cell}:
                    union = _bbox_union([box for (candidate_row, _), box in boxes_by_cell.items() if candidate_row == row_index])
                    if union is not None:
                        row_boxes[row_index] = union

                related = [
                    segment
                    for segment in segments
                    if segment.source_table_id == table_segment.source_table_id
                ]
                raw_cell_by_coord: Dict[Tuple[int, int], Dict[str, Any]] = {}
                if coordinate_alignment_safe:
                    for raw_cell in raw_cells:
                        try:
                            raw_key = (
                                int(raw_cell.get("row_index")),
                                int(raw_cell.get("col_index")),
                            )
                        except (TypeError, ValueError, OverflowError):
                            continue
                        raw_cell_by_coord[raw_key] = raw_cell

                structured_html_sha256 = hashlib.sha256(raw_html.encode("utf-8")).hexdigest() if raw_html else None
                provenance = {
                    "table_record_id": record.get("table_id"),
                    "block_id": record.get("block_id"),
                    "block_type": record.get("block_type") or "table",
                    "reading_order": record.get("reading_order"),
                    "page_width": record.get("page_width"),
                    "page_height": record.get("page_height"),
                    "source_page_index": record.get("source_page_index"),
                    "source_json_path": record.get("source_json_path"),
                    "source_image_paths": list(record.get("source_image_paths") or []),
                    "asset_ids": list(record.get("asset_ids") or []),
                    "text_fingerprint": record.get("text_fingerprint"),
                    "table_match_score": round(match_score, 4),
                    "table_text_match_score": round(text_score, 4),
                    "table_bbox_iou": round(bbox_score, 4),
                }
                record_caption = str(record.get("caption") or "").strip()
                record_summary = str(record.get("summary") or "").strip()

                for related_segment in related:
                    related_segment.structure_confidence = structure_confidence
                    related_segment.ocr_confidence = ocr_confidence
                    related_segment.parse_pass = record_parse_pass
                    related_segment.conflicts = conflicts
                    data = dict(related_segment.structured_data or {})
                    existing_reasons = [
                        str(value).strip()
                        for value in (data.get("quality_reasons") or [])
                        if str(value).strip()
                    ]
                    merged_reasons = list(
                        dict.fromkeys([*reasons, *existing_reasons])
                    )
                    previous_review_status = str(
                        related_segment.review_status
                        or data.get("review_status")
                        or ""
                    ).strip().lower()
                    merged_review_status = (
                        "needs_review"
                        if merged_reasons
                        or conflicts
                        or previous_review_status == "needs_review"
                        else review_status
                    )
                    related_segment.review_status = merged_review_status
                    try:
                        row_index = int(data.get("row_index")) if data.get("row_index") is not None else None
                    except (TypeError, ValueError, OverflowError):
                        row_index = None
                    try:
                        col_index = int(data.get("col_index")) if data.get("col_index") is not None else None
                    except (TypeError, ValueError, OverflowError):
                        col_index = None
                    segment_bbox = None
                    if related_segment.segment_type == "table":
                        segment_bbox = table_bbox
                    elif (
                        coordinate_alignment_safe
                        and related_segment.segment_type == "table_row"
                        and row_index is not None
                    ):
                        segment_bbox = row_boxes.get(row_index)
                    elif (
                        coordinate_alignment_safe
                        and related_segment.segment_type == "table_cell"
                        and row_index is not None
                        and col_index is not None
                    ):
                        segment_bbox = boxes_by_cell.get((row_index, col_index))

                    data.update(
                        {
                            **provenance,
                            "structure_confidence": structure_confidence,
                            "ocr_confidence": ocr_confidence,
                            "parse_pass": record_parse_pass,
                            "review_status": merged_review_status,
                            "quality_reasons": merged_reasons,
                            "quality_notes": list(
                                dict.fromkeys(
                                    [
                                        *quality_notes,
                                        *(data.get("quality_notes") or []),
                                    ]
                                )
                            ),
                            "conflicts": conflicts,
                            "structured_html_sha256": structured_html_sha256,
                        }
                    )
                    if table_bbox is not None:
                        data["source_table_bbox"] = table_bbox
                    if segment_bbox is not None:
                        data["bbox"] = segment_bbox
                    if record_caption:
                        data["table_title"] = record_caption
                        data["table_title_source"] = "structured_record_caption"
                        data["caption"] = record_caption
                    if record_summary:
                        data["summary"] = record_summary
                    if related_segment.segment_type == "table":
                        data["structured_html"] = raw_html
                    if segment_bbox is not None:
                        related_segment.position_x = float(segment_bbox[0])
                        related_segment.position_y = float(segment_bbox[1])
                    if related_segment.segment_type == "table_cell":
                        cell = (
                            raw_cell_by_coord.get((row_index, col_index))
                            if row_index is not None and col_index is not None
                            else None
                        )
                        if cell:
                            related_segment.rowspan = int(cell.get("rowspan") or 1)
                            related_segment.colspan = int(cell.get("colspan") or 1)
                            data["rowspan"] = related_segment.rowspan
                            data["colspan"] = related_segment.colspan
                        year_fallback_blocked = any(
                            reason
                            in {
                                "ambiguous_year_scope",
                                "conflicting_year_scope",
                            }
                            for reason in merged_reasons
                        )
                        if data.get("year") is None and not year_fallback_blocked:
                            header_path = list(
                                data.get("header_path")
                                or related_segment.header_path
                                or []
                            )
                            header_labels = header_path or [
                                str(
                                    data.get("col_header")
                                    or related_segment.col_header
                                    or ""
                                )
                            ]
                            for header_label in reversed(header_labels):
                                years = self._extract_table_years(header_label)
                                if len(years) == 1:
                                    data["year"] = years[0]
                                    data["source_year_label"] = header_label
                                    data["year_scope"] = "column_header"
                                    break
                                if len(years) > 1:
                                    break
                        if not related_segment.unit and not data.get("unit"):
                            value_text = str(
                                data.get("value_text")
                                or related_segment.value_text
                                or ""
                            )
                            value_unit, ambiguity = self._coalesce_table_unit_specs(
                                self._extract_table_unit_specs(value_text),
                                "cell",
                            )
                            rendered_unit = self._render_table_unit(value_unit)
                            if rendered_unit and ambiguity is None:
                                related_segment.unit = rendered_unit
                                data.update(
                                    {
                                        "unit": rendered_unit,
                                        "raw_unit": rendered_unit,
                                        "unit_base": value_unit.get("base_unit"),
                                        "unit_multiplier": float(
                                            value_unit.get("multiplier") or 1.0
                                        ),
                                        "unit_scope": "cell",
                                        "unit_sources": list(
                                            value_unit.get("sources") or []
                                        ),
                                    }
                                )
                    related_segment.structured_data = data

        return binding_summary

    def _materialize_unmatched_table_records(
        self,
        segments: List[TextSegment],
        records: Sequence[Dict[str, Any]],
        document_id: str,
    ) -> int:
        """Create canonical table families when Markdown omitted a JSON table.

        Paddle can return a usable ``pred_html`` record while its Markdown
        projection is truncated or malformed.  Those records must become real
        table/row/cell segments instead of being silently discarded.
        """
        used_record_ids = {
            (
                int(segment.page_number or 1),
                str((segment.structured_data or {}).get("table_record_id") or "").strip(),
            )
            for segment in segments
            if (segment.structured_data or {}).get("table_record_id")
        }
        existing_fingerprints = {
            (
                int(segment.page_number or 1),
                str((segment.structured_data or {}).get("structured_html_sha256") or ""),
                tuple(
                    round(float(value), 4)
                    for value in (
                        _normalized_bbox(
                            (segment.structured_data or {}).get("bbox")
                        )
                        or []
                    )
                ),
                str(
                    (segment.structured_data or {}).get("reading_order")
                    if (segment.structured_data or {}).get("reading_order") is not None
                    else segment.position_y
                ),
            )
            for segment in segments
            if segment.segment_type == "table"
            and (segment.structured_data or {}).get("structured_html_sha256")
        }
        added = 0
        for ordinal, record in enumerate(records, 1):
            if not isinstance(record, dict):
                continue
            raw_html = str(record.get("pred_html") or "").strip()
            rows = self._parse_table_rows(raw_html)
            if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
                continue
            try:
                page = max(1, int(record.get("page_number") or 1))
            except (TypeError, ValueError, OverflowError):
                page = 1
            record["page_number"] = page
            record_id = str(record.get("table_id") or "").strip()
            fingerprint = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
            bbox = _normalized_bbox(
                record.get("bbox"),
                page_width=float(record.get("page_width") or 0.0),
                page_height=float(record.get("page_height") or 0.0),
            )
            reading_order_value = record.get("reading_order")
            record_fingerprint_key = (
                page,
                fingerprint,
                tuple(round(float(value), 4) for value in (bbox or [])),
                str(reading_order_value if reading_order_value is not None else ""),
            )
            if record_id and (page, record_id) in used_record_ids:
                continue
            if not record_id and record_fingerprint_key in existing_fingerprints:
                continue

            identity_basis = (
                f"record:{record_id}"
                if record_id
                else (
                    "anonymous:"
                    + json.dumps(
                        {
                            "html": fingerprint,
                            "bbox": [round(float(value), 6) for value in (bbox or [])],
                            "reading_order": reading_order_value,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                )
            )
            identity = hashlib.sha256(
                f"{page}\0{identity_basis}\0{fingerprint}".encode("utf-8")
            ).hexdigest()[:16]
            table_id = f"{document_id}_record_table_{identity}"
            table_segment_id = f"{document_id}_p{page}_record_{identity}"
            try:
                record_parse_pass = max(1, int(record.get("parse_pass") or 1))
            except (TypeError, ValueError, OverflowError):
                record_parse_pass = 1
            try:
                reading_order = float(reading_order_value)
            except (TypeError, ValueError, OverflowError):
                reading_order = float(len(segments) + added + 1)
            # Only a parser-provided caption is independent table identity.
            # A generated/general summary is useful context but is not strong
            # enough evidence for automatic cross-page stitching.
            table_title = str(record.get("caption") or "").strip()
            table_summary = str(record.get("summary") or "").strip()
            family = [
                TextSegment(
                    segment_id=table_segment_id,
                    content=raw_html,
                    page_number=page,
                    position_y=float(bbox[1]) if bbox is not None else reading_order,
                    position_x=float(bbox[0]) if bbox is not None else 0.0,
                    segment_type="table",
                    source_table_id=table_id,
                    parse_pass=record_parse_pass,
                    structured_data={
                        "source": "paddleocr_vl_structured_table_record",
                        "parser": "paddleocr-vl",
                        "table_id": table_id,
                        "table_title": table_title,
                        "table_title_source": (
                            "structured_record_caption" if table_title else ""
                        ),
                        "summary": table_summary or None,
                        "bbox": bbox,
                        "materialized_from_table_record": True,
                        "parse_pass": record_parse_pass,
                    },
                )
            ]
            family.extend(
                self._table_segments_from_markdown(
                    raw_html,
                    f"{document_id}_record_{identity}",
                    page,
                    table_id,
                    start_seq=100_000 + ordinal * 10_000,
                    table_title=table_title,
                    table_title_source=(
                        "structured_record_caption" if table_title else ""
                    ),
                )
            )
            self._enrich_table_segments_from_records(family, [record])
            segments.extend(family)
            if record_id:
                used_record_ids.add((page, record_id))
            else:
                existing_fingerprints.add(record_fingerprint_key)
            added += 1
        return added

    @staticmethod
    def _normalise_table_projection_cell(value: object) -> str:
        """Normalize a protected table cell without weakening numeric identity."""
        text = unicodedata.normalize("NFKC", unescape(str(value or "")))
        text = text.replace("\u00a0", " ").casefold().strip()
        return re.sub(r"\s+", " ", text)

    def _structured_table_preserves_projection(
        self,
        markdown_text: str,
        structured_text: str,
        *,
        trusted_markdown_row_indices: Optional[Sequence[int]] = None,
    ) -> bool:
        """Return whether structured HTML safely contains the Markdown projection.

        Coverage counts cannot establish table identity: an unrelated table can
        always have more rows or cells.  A canonical structured record must
        instead retain the confirmed Markdown header paths, every stable row
        label, and every trusted Markdown data row in the same order. Trusted
        cells are compared exactly after Unicode/case/whitespace normalization,
        so years, units and values cannot silently change while authorizing a
        replacement. Explicitly low-confidence rows retain their identity label
        but may have their values corrected.
        """
        markdown_rows, _markdown_cells, markdown_quality = self._parse_table_details(
            markdown_text
        )
        structured_rows, _structured_cells, structured_quality = (
            self._parse_table_details(structured_text)
        )
        markdown_header = dict(markdown_quality.get("header_model") or {})
        structured_header = dict(structured_quality.get("header_model") or {})
        if not (
            markdown_header.get("confirmed")
            and structured_header.get("confirmed")
        ):
            return False

        def normalized_paths(
            model: Dict[str, Any],
        ) -> Tuple[int, List[Tuple[int, Tuple[str, ...]]]]:
            raw_paths = list(model.get("header_paths") or [])
            paths: List[Tuple[int, Tuple[str, ...]]] = []
            for physical_column, path in enumerate(raw_paths):
                normalized = tuple(
                    value
                    for raw in path
                    if (value := self._normalise_table_projection_cell(raw))
                )
                if normalized:
                    paths.append((physical_column, normalized))
            return len(raw_paths), paths

        markdown_width, markdown_paths = normalized_paths(markdown_header)
        structured_width, structured_paths = normalized_paths(structured_header)
        if (
            not markdown_paths
            or not structured_paths
            or len(markdown_paths) != markdown_width
        ):
            return False

        # Map every old column to exactly one later new column.  Multiple
        # identical destinations are ambiguous and therefore fail closed.
        column_map: List[int] = []
        next_column = 0
        for _markdown_column, path in markdown_paths:
            matches = [
                physical_column
                for physical_column, candidate_path in structured_paths
                if physical_column >= next_column and candidate_path == path
            ]
            if len(matches) != 1:
                return False
            column_map.append(matches[0])
            next_column = matches[0] + 1

        markdown_data_indices = [
            int(index)
            for index in (markdown_header.get("data_row_indices") or [])
            if 0 <= int(index) < len(markdown_rows)
        ]
        structured_data_indices = [
            int(index)
            for index in (structured_header.get("data_row_indices") or [])
            if 0 <= int(index) < len(structured_rows)
        ]

        trusted_rows = (
            None
            if trusted_markdown_row_indices is None
            else {int(index) for index in trusted_markdown_row_indices}
        )
        projection_rows: List[
            Tuple[List[str], int, str, bool, Tuple[bool, ...]]
        ] = []
        markdown_headers = [
            str(value or "") for value in (markdown_header.get("headers") or [])
        ]

        def mutable_measurement_column(
            raw_value: object,
            column_index: int,
        ) -> bool:
            value = self._clean_table_measurement_text(raw_value).strip()
            if not self._table_cell_is_measurement_value(value):
                return False
            # A correction may change a measurement value, not a textual
            # category that happens to contain digits (for example tCO2e).
            if not re.match(
                r"^[~≈<>]?[\s\(]*[-+]?(?:\d[\d,.]*|\.\d+)",
                value,
            ):
                return False
            header_path = markdown_paths[column_index][1]
            header_text = " ".join(header_path)
            # Use a positive measurement allowlist. Numeric dimensions such as
            # Scope 1/2, quarter, tier, level, site or product code must never
            # become mutable merely because their cell begins with a digit.
            if self._extract_table_years(header_text):
                return True
            if re.search(
                r"\b(?:scope|quarter|period|tier|level|site|location|facility|"
                r"product|category|class|type|unit|code|reference|identifier|"
                r"id|name|description|boundary|country|region|gender|group)\b",
                header_text,
            ):
                return False
            if "%" in header_text or re.search(
                r"\b(?:measure(?:ment)?|value|amount|count|total|rate|"
                r"percentage|percent|share|ratio|quantity|volume|consumption|"
                r"emissions?|energy|water|waste|revenue|sales|cost|headcount|"
                r"score|intensity)\b",
                header_text,
            ):
                return True
            return any(
                spec.get("base_unit")
                for spec in self._extract_table_unit_specs(header_text)
            )

        for row_index in markdown_data_indices:
            raw_row = list(markdown_rows[row_index])
            row_is_trusted = trusted_rows is None or row_index in trusted_rows
            # No non-empty source row may disappear merely because it is hard
            # to project. Trusted malformed rows fail closed; low-confidence
            # rows must still supply a stable ordered label anchor.
            if len(raw_row) != markdown_width:
                return False
            row = [self._normalise_table_projection_cell(value) for value in raw_row]
            if not any(row):
                continue
            row_label = self._normalise_table_projection_cell(
                self._infer_row_header(markdown_headers, raw_row)
            )
            if not row_label or not re.search(r"[^\W\d_]", row_label, re.UNICODE):
                return False
            label_columns = [
                column_index
                for column_index, value in enumerate(row)
                if value == row_label
            ]
            if len(label_columns) != 1:
                return False
            label_column = label_columns[0]
            projection_rows.append(
                (
                    row,
                    column_map[label_column],
                    row_label,
                    row_is_trusted,
                    tuple(
                        mutable_measurement_column(raw_value, column_index)
                        for column_index, raw_value in enumerate(raw_row)
                    ),
                )
            )
        if not projection_rows:
            return False

        candidate_rows: List[List[str]] = []
        for row_index in structured_data_indices:
            raw_row = list(structured_rows[row_index])
            if len(raw_row) < structured_width:
                continue
            candidate_rows.append(
                [self._normalise_table_projection_cell(value) for value in raw_row]
            )

        next_row = 0
        for (
            projected,
            mapped_label_column,
            projected_label,
            is_trusted,
            mutable_columns,
        ) in projection_rows:
            matched_row: Optional[int] = None
            for candidate_index in range(next_row, len(candidate_rows)):
                candidate = candidate_rows[candidate_index]
                if candidate[mapped_label_column] != projected_label:
                    continue
                if not all(
                    not projected[column_index]
                    or (
                        not is_trusted
                        and mutable_columns[column_index]
                    )
                    or projected[column_index] == candidate[mapped_column]
                    for column_index, mapped_column in enumerate(column_map)
                ):
                    continue
                matched_row = candidate_index
                break
            if matched_row is None:
                return False
            next_row = matched_row + 1
        return True

    def _prefer_structured_table_records(
        self,
        segments: List[TextSegment],
        records: Sequence[Dict[str, Any]],
        document_id: str,
    ) -> int:
        """Use complete structured HTML as the canonical table representation.

        Markdown remains useful for surrounding narrative, but it can be a
        truncated projection of Paddle's structured JSON.  After matching it
        for section context, replace matched table families with families built
        from ``pred_html``.  Invalid records never displace usable Markdown.
        """
        valid_records: List[Dict[str, Any]] = []
        valid_record_keys: set[Tuple[int, str]] = set()
        record_by_key: Dict[Tuple[int, str], Dict[str, Any]] = {}
        record_fingerprint_by_key: Dict[Tuple[int, str], str] = {}
        duplicate_record_keys: set[Tuple[int, str]] = set()
        for raw_record in records:
            record = dict(raw_record) if isinstance(raw_record, dict) else None
            if not isinstance(record, dict):
                continue
            raw_html = str(record.get("pred_html") or "").strip()
            rows = self._parse_table_rows(raw_html)
            if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
                continue
            try:
                page = max(1, int(record.get("page_number") or 1))
            except (TypeError, ValueError, OverflowError):
                page = 1
            record["page_number"] = page
            record_id = str(record.get("table_id") or "").strip()
            if not record_id:
                bbox = _normalized_bbox(
                    record.get("bbox"),
                    page_width=float(record.get("page_width") or 0.0),
                    page_height=float(record.get("page_height") or 0.0),
                )
                occurrence = json.dumps(
                    {
                        "page": page,
                        "html": hashlib.sha256(raw_html.encode("utf-8")).hexdigest(),
                        "bbox": [round(float(value), 6) for value in (bbox or [])],
                        "reading_order": record.get("reading_order"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                record_id = "anonymous:" + hashlib.sha256(
                    occurrence.encode("utf-8")
                ).hexdigest()[:20]
                record["table_id"] = record_id
                record["table_record_source"] = "synthetic_occurrence"
            key = (page, record_id)
            record_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "html": raw_html,
                        "bbox": _normalized_bbox(
                            record.get("bbox"),
                            page_width=float(record.get("page_width") or 0.0),
                            page_height=float(record.get("page_height") or 0.0),
                        ),
                        "reading_order": record.get("reading_order"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            if key in record_fingerprint_by_key:
                if record_fingerprint_by_key[key] != record_fingerprint:
                    duplicate_record_keys.add(key)
                # Exact duplicates are redundant; conflicting duplicates are
                # removed below as an ambiguous identity. Neither case should
                # create an additional candidate-graph node.
                continue
            valid_record_keys.add(key)
            record_by_key[key] = record
            record_fingerprint_by_key[key] = record_fingerprint
            valid_records.append(record)
        if duplicate_record_keys:
            valid_records = [
                record
                for record in valid_records
                if (
                    int(record.get("page_number") or 1),
                    str(record.get("table_id") or "").strip(),
                )
                not in duplicate_record_keys
            ]
            for key in duplicate_record_keys:
                valid_record_keys.discard(key)
                record_by_key.pop(key, None)
        if not valid_records:
            return 0

        try:
            source_structure_threshold = float(
                os.getenv("REPORT_TABLE_STRUCTURE_CONFIDENCE_THRESHOLD", "0.80")
                or "0.80"
            )
            source_ocr_threshold = float(
                os.getenv("REPORT_TABLE_OCR_CONFIDENCE_THRESHOLD", "0.75")
                or "0.75"
            )
        except (TypeError, ValueError, OverflowError):
            source_structure_threshold, source_ocr_threshold = 0.80, 0.75
        trusted_rows_by_table: Dict[str, set[int]] = {}
        source_rows_by_table: Dict[str, set[int]] = {}
        for source_row in segments:
            if (
                source_row.segment_type != "table_row"
                or not source_row.source_table_id
            ):
                continue
            table_id = str(source_row.source_table_id)
            trusted_rows_by_table.setdefault(table_id, set())
            source_data = dict(source_row.structured_data or {})
            try:
                row_index = int(source_data.get("row_index"))
            except (TypeError, ValueError, OverflowError):
                continue
            source_rows_by_table.setdefault(table_id, set()).add(row_index)
            reasons = {
                str(value).strip()
                for value in (source_data.get("quality_reasons") or [])
                if str(value).strip()
            }
            review_status = str(
                source_row.review_status
                or source_data.get("review_status")
                or ""
            ).strip().lower()
            structure_confidence = (
                source_row.structure_confidence
                if source_row.structure_confidence is not None
                else source_data.get("structure_confidence")
            )
            ocr_confidence = (
                source_row.ocr_confidence
                if source_row.ocr_confidence is not None
                else source_data.get("ocr_confidence")
            )
            try:
                structure_is_trusted = (
                    structure_confidence is not None
                    and float(structure_confidence) >= source_structure_threshold
                )
                ocr_is_trusted = (
                    ocr_confidence is None
                    or float(ocr_confidence) >= source_ocr_threshold
                )
            except (TypeError, ValueError, OverflowError):
                structure_is_trusted = False
                ocr_is_trusted = False
            if (
                review_status != "needs_review"
                and not reasons
                and structure_is_trusted
                and ocr_is_trusted
            ):
                trusted_rows_by_table[table_id].add(row_index)

        binding_summary = self._enrich_table_segments_from_records(
            segments,
            valid_records,
        )
        ambiguous_record_keys = set(
            binding_summary.get("ambiguous_record_keys") or set()
        )
        context_by_record: Dict[Tuple[int, str], Dict[str, Any]] = {}
        replaced_table_ids: set[str] = set()
        matched_record_keys: set[Tuple[int, str]] = set()
        replaceable_record_keys: set[Tuple[int, str]] = set()
        for segment in segments:
            if segment.segment_type != "table" or not segment.source_table_id:
                continue
            data = dict(segment.structured_data or {})
            record_id = str(data.get("table_record_id") or "").strip()
            key = (int(segment.page_number or 1), record_id)
            if record_id and key in valid_record_keys:
                matched_record_keys.add(key)
                record = record_by_key[key]
                markdown_rows = self._parse_table_rows(segment.content)
                structured_rows, _structured_cells, structured_quality = (
                    self._parse_table_details(str(record.get("pred_html") or ""))
                )

                def _coverage(rows: Sequence[Sequence[Any]]) -> Tuple[int, int, int]:
                    nonempty = sum(
                        1
                        for row in rows
                        for value in row
                        if str(value or "").strip()
                    )
                    numeric = sum(
                        1
                        for row in rows
                        for value in row
                        if re.search(r"\d", str(value or ""))
                    )
                    return len(rows), nonempty, numeric

                markdown_coverage = _coverage(markdown_rows)
                structured_coverage = _coverage(structured_rows)
                no_data_loss = all(
                    structured >= markdown
                    for structured, markdown in zip(
                        structured_coverage,
                        markdown_coverage,
                    )
                )
                projection_preserved = self._structured_table_preserves_projection(
                    segment.content,
                    str(record.get("pred_html") or ""),
                    trusted_markdown_row_indices=trusted_rows_by_table.get(
                        str(segment.source_table_id)
                    ),
                )
                structured_reasons = {
                    str(value).strip()
                    for value in (structured_quality.get("reasons") or [])
                    if str(value).strip()
                }
                try:
                    raw_structure_confidence = record.get("structure_confidence")
                    structure_confidence = (
                        float(raw_structure_confidence)
                        if raw_structure_confidence is not None
                        else float(structured_quality.get("structure_confidence") or 0.0)
                    )
                except (TypeError, ValueError, OverflowError):
                    structure_confidence = 0.0
                try:
                    raw_ocr_confidence = record.get("ocr_confidence")
                    record_ocr_confidence = (
                        float(raw_ocr_confidence)
                        if raw_ocr_confidence is not None
                        else None
                    )
                except (TypeError, ValueError, OverflowError):
                    record_ocr_confidence = None
                try:
                    structure_threshold = float(
                        os.getenv("REPORT_TABLE_STRUCTURE_CONFIDENCE_THRESHOLD", "0.80")
                        or "0.80"
                    )
                except (TypeError, ValueError, OverflowError):
                    structure_threshold = 0.80
                source_table_id = str(segment.source_table_id)
                source_rows = source_rows_by_table.get(source_table_id, set())
                trusted_source_rows = trusted_rows_by_table.get(
                    source_table_id,
                    set(),
                )
                has_untrusted_source_rows = bool(
                    source_rows - trusted_source_rows
                )
                correction_source_is_trusted = (
                    not has_untrusted_source_rows
                    or (
                        record_ocr_confidence is not None
                        and math.isfinite(record_ocr_confidence)
                        and record_ocr_confidence >= source_ocr_threshold
                    )
                )
                may_replace_projection = (
                    no_data_loss
                    and projection_preserved
                    and correction_source_is_trusted
                    and not (
                        structured_reasons & _TABLE_SECOND_PASS_CRITICAL_REASONS
                    )
                    and structure_confidence >= structure_threshold
                )
                if not may_replace_projection:
                    # Keep the enriched Markdown family.  Its retained conflict
                    # remains actionable and can be repaired by the selective
                    # high-resolution pass; a weaker JSON projection must not
                    # delete first-pass cells merely because it parsed as 2x2.
                    continue
                replaceable_record_keys.add(key)
                replaced_table_ids.add(str(segment.source_table_id))
                inherited_conflicts: List[Dict[str, Any]] = []
                inherited_reasons: List[str] = []
                inherited_notes: List[str] = []
                inherited_statuses: List[str] = []
                for family_segment in segments:
                    if family_segment.source_table_id != segment.source_table_id:
                        continue
                    family_data = dict(family_segment.structured_data or {})
                    inherited_status = str(
                        family_segment.review_status
                        or family_data.get("review_status")
                        or ""
                    ).strip().lower()
                    if inherited_status and inherited_status not in inherited_statuses:
                        inherited_statuses.append(inherited_status)
                    for reason in family_data.get("quality_reasons") or []:
                        reason_text = str(reason or "").strip()
                        if reason_text and reason_text not in inherited_reasons:
                            inherited_reasons.append(reason_text)
                    for note in family_data.get("quality_notes") or []:
                        note_text = str(note or "").strip()
                        if note_text and note_text not in inherited_notes:
                            inherited_notes.append(note_text)
                    for conflict in [
                        *(family_segment.conflicts or []),
                        *(family_data.get("conflicts") or []),
                    ]:
                        if not isinstance(conflict, dict):
                            continue
                        conflict_key = json.dumps(
                            conflict,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        )
                        if any(
                            json.dumps(
                                existing,
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            )
                            == conflict_key
                            for existing in inherited_conflicts
                        ):
                            continue
                        inherited_conflicts.append(dict(conflict))
                context_by_record[key] = {
                    field: data.get(field)
                    for field in ("table_title", "table_title_source", "section_path")
                    if data.get(field) not in (None, "", [])
                }
                context_by_record[key].update(
                    {
                        "previous_source_table_id": str(segment.source_table_id),
                        "inherited_conflicts": inherited_conflicts,
                        "inherited_quality_reasons": inherited_reasons,
                        "inherited_quality_notes": inherited_notes,
                        "inherited_review_statuses": inherited_statuses,
                    }
                )

        canonical: List[TextSegment] = []
        records_to_materialize = [
            record
            for record in valid_records
            if (
                (
                    int(record.get("page_number") or 1),
                    str(record.get("table_id") or "").strip(),
                )
                not in ambiguous_record_keys
                and (
                    (
                        int(record.get("page_number") or 1),
                        str(record.get("table_id") or "").strip(),
                    )
                    not in matched_record_keys
                    or (
                        int(record.get("page_number") or 1),
                        str(record.get("table_id") or "").strip(),
                    )
                    in replaceable_record_keys
                )
            )
        ]
        added = self._materialize_unmatched_table_records(
            canonical,
            records_to_materialize,
            document_id,
        )
        for segment in canonical:
            data = dict(segment.structured_data or {})
            record_id = str(data.get("table_record_id") or "").strip()
            context = context_by_record.get((int(segment.page_number or 1), record_id), {})
            inherited_conflicts = list(context.get("inherited_conflicts") or [])
            merged_conflicts: List[Dict[str, Any]] = []
            seen_conflicts: set[str] = set()
            for conflict in [
                *(segment.conflicts or []),
                *(data.get("conflicts") or []),
                *inherited_conflicts,
            ]:
                if not isinstance(conflict, dict):
                    continue
                conflict_key = json.dumps(
                    conflict,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if conflict_key in seen_conflicts:
                    continue
                seen_conflicts.add(conflict_key)
                merged_conflicts.append(dict(conflict))

            merged_reasons = list(
                dict.fromkeys(
                    str(value).strip()
                    for value in [
                        *(data.get("quality_reasons") or []),
                        *(context.get("inherited_quality_reasons") or []),
                    ]
                    if str(value).strip()
                )
            )
            merged_notes = list(
                dict.fromkeys(
                    str(value).strip()
                    for value in [
                        *(data.get("quality_notes") or []),
                        *(context.get("inherited_quality_notes") or []),
                    ]
                    if str(value).strip()
                )
            )
            statuses = {
                str(value or "").strip().lower()
                for value in [
                    segment.review_status,
                    data.get("review_status"),
                    *(context.get("inherited_review_statuses") or []),
                ]
                if str(value or "").strip()
            }
            if merged_conflicts or merged_reasons or "needs_review" in statuses:
                review_status = "needs_review"
            elif merged_notes or "unverified" in statuses:
                review_status = "unverified"
            else:
                review_status = "verified"

            # A structured Paddle record caption is independent table identity;
            # never overwrite it with the nearest Markdown section heading.
            if not str(data.get("table_title") or "").strip():
                for field in ("table_title", "table_title_source"):
                    if context.get(field) not in (None, "", []):
                        data[field] = context[field]
            if context.get("section_path") not in (None, "", []):
                data["section_path"] = context["section_path"]
            if context:
                data["canonicalized_from_markdown"] = True
                data["previous_source_table_id"] = context.get(
                    "previous_source_table_id"
                )
            data["quality_reasons"] = merged_reasons
            data["quality_notes"] = merged_notes
            data["conflicts"] = merged_conflicts
            data["review_status"] = review_status
            segment.conflicts = merged_conflicts
            segment.review_status = review_status
            segment.structured_data = data
        canonical_by_previous: Dict[str, List[TextSegment]] = {}
        unmatched_canonical: List[TextSegment] = []
        for segment in canonical:
            previous_table_id = str(
                (segment.structured_data or {}).get("previous_source_table_id")
                or ""
            ).strip()
            if previous_table_id:
                canonical_by_previous.setdefault(previous_table_id, []).append(segment)
            else:
                unmatched_canonical.append(segment)

        # Replace a matched family at its original list position.  This keeps
        # section/page reading order stable instead of appending every rebuilt
        # table to the end of the document.
        rebuilt: List[TextSegment] = []
        inserted: set[str] = set()
        for segment in segments:
            table_id = str(segment.source_table_id or "")
            if table_id not in replaced_table_ids:
                rebuilt.append(segment)
                continue
            if table_id not in inserted:
                rebuilt.extend(canonical_by_previous.get(table_id, []))
                inserted.add(table_id)
        rebuilt.extend(unmatched_canonical)
        segments[:] = rebuilt
        return added

    def _table_family_quality_details(
        self,
        family: Sequence[TextSegment],
    ) -> Dict[str, Any]:
        """Return one comparable quality envelope for a physical table family."""
        table = next(
            (segment for segment in family if segment.segment_type == "table"),
            None,
        )
        reasons: set[str] = set()
        notes: set[str] = set()
        conflict_map: Dict[str, Dict[str, Any]] = {}
        statuses: List[str] = []
        structure_values: List[float] = []
        ocr_values: List[float] = []
        match_values: List[float] = []
        has_table_record = False

        for segment in family:
            data = dict(segment.structured_data or {})
            reasons.update(
                str(value).strip()
                for value in (data.get("quality_reasons") or [])
                if str(value).strip()
            )
            notes.update(
                str(value).strip()
                for value in (data.get("quality_notes") or [])
                if str(value).strip()
            )
            status = str(segment.review_status or data.get("review_status") or "").strip().lower()
            if status:
                statuses.append(status)
            for raw_conflict in [*(segment.conflicts or []), *(data.get("conflicts") or [])]:
                if not isinstance(raw_conflict, dict):
                    continue
                key = json.dumps(raw_conflict, ensure_ascii=False, sort_keys=True, default=str)
                conflict_map.setdefault(key, dict(raw_conflict))
            if data.get("table_record_id"):
                has_table_record = True
            for raw, target in (
                (segment.structure_confidence, structure_values),
                (segment.ocr_confidence, ocr_values),
                (data.get("table_match_score"), match_values),
            ):
                if raw is None:
                    continue
                try:
                    target.append(max(0.0, min(1.0, float(raw))))
                except (TypeError, ValueError, OverflowError):
                    pass

        rows = self._parse_table_rows(table.content if table is not None else "")
        if table is not None:
            raw_quality = self._parse_table_details(table.content)[2]
            reasons.update(str(value) for value in raw_quality.get("reasons") or [])
        if not has_table_record:
            reasons.add("missing_table_record")
        if "missing_ocr_confidence" in reasons:
            reasons.discard("missing_ocr_confidence")
            notes.add("missing_ocr_confidence")
        widths = [len(row) for row in rows if row]
        shape_consistency = 1.0 if widths and len(set(widths)) == 1 else 0.0
        nonempty_cells = sum(
            1 for row in rows for value in row if str(value or "").strip()
        )
        numeric_cells = sum(
            1 for row in rows for value in row if re.search(r"\d", str(value or ""))
        )
        actionable = sorted(reasons & _TABLE_SECOND_PASS_ACTIONABLE_REASONS)
        critical = sorted(reasons & _TABLE_SECOND_PASS_CRITICAL_REASONS)
        if "needs_review" in statuses:
            review_rank = 0
            review_status = "needs_review"
        elif statuses and all(status == "verified" for status in statuses):
            review_rank = 2
            review_status = "verified"
        else:
            review_rank = 1
            review_status = "unverified"
        if (
            review_status == "needs_review"
            and not (reasons & _TABLE_SECOND_PASS_ACTIONABLE_REASONS)
            and not (
                reasons
                and reasons.issubset(_TABLE_NON_ACTIONABLE_REVIEW_REASONS)
            )
            and notes != {"missing_ocr_confidence"}
        ):
            reasons.add("unexplained_needs_review")
            actionable = sorted(reasons & _TABLE_SECOND_PASS_ACTIONABLE_REASONS)

        return {
            "review_status": review_status,
            "review_rank": review_rank,
            "reasons": sorted(reasons),
            "notes": sorted(notes),
            "actionable_reasons": actionable,
            "critical_reasons": critical,
            "conflicts": list(conflict_map.values()),
            "conflict_count": len(conflict_map),
            "structure_confidence": min(structure_values) if structure_values else None,
            "ocr_confidence": min(ocr_values) if ocr_values else None,
            "table_match_score": min(match_values) if match_values else None,
            "has_table_record": has_table_record,
            "row_count": len(rows),
            "nonempty_cells": nonempty_cells,
            "numeric_cells": numeric_cells,
            "shape_consistency": shape_consistency,
        }

    def _table_family_quality_key(
        self,
        family: Sequence[TextSegment],
    ) -> Tuple[Any, ...]:
        """Higher tuples represent strictly safer, more complete table evidence."""
        details = self._table_family_quality_details(family)
        structure_confidence = details["structure_confidence"]
        ocr_confidence = details["ocr_confidence"]
        match_score = details["table_match_score"]
        return (
            -int(details["conflict_count"]),
            -len(details["critical_reasons"]),
            -len(details["actionable_reasons"]),
            int(details["review_rank"]),
            1 if details["has_table_record"] else 0,
            float(structure_confidence) if structure_confidence is not None else -1.0,
            1 if ocr_confidence is not None else 0,
            float(ocr_confidence) if ocr_confidence is not None else -1.0,
            float(match_score) if match_score is not None else -1.0,
            float(details["shape_consistency"]),
            int(details["nonempty_cells"]),
        )

    def _table_family_map(
        self,
        segments: Sequence[TextSegment],
    ) -> Dict[str, List[TextSegment]]:
        families: Dict[str, List[TextSegment]] = {}
        for segment in segments:
            table_id = str(segment.source_table_id or "").strip()
            if not table_id:
                continue
            families.setdefault(table_id, []).append(segment)
        return families

    def _select_table_second_pass_plan(
        self,
        segments: Sequence[TextSegment],
        *,
        page_analysis: Any = None,
        max_ratio: Optional[float] = None,
        render_zoom: Optional[float] = None,
    ) -> TableSecondPassPlan:
        """Select a bounded set of actionable physical tables for repair."""
        tables = [
            segment
            for segment in segments
            if segment.segment_type == "table" and segment.source_table_id
        ]
        families = self._table_family_map(segments)
        try:
            ratio = float(
                max_ratio
                if max_ratio is not None
                else os.getenv("REPORT_TABLE_SECOND_PASS_MAX_RATIO", "0.30")
            )
        except (TypeError, ValueError, OverflowError):
            ratio = 0.30
        ratio = max(0.0, min(1.0, ratio))
        try:
            requested_zoom = float(
                render_zoom
                if render_zoom is not None
                else os.getenv("REPORT_TABLE_SECOND_PASS_RENDER_ZOOM", "2.0")
            )
        except (TypeError, ValueError, OverflowError):
            requested_zoom = 2.0
        requested_zoom = max(1.0, min(4.0, requested_zoom))

        candidates: List[TableSecondPassCandidate] = []
        for table in tables:
            table_id = str(table.source_table_id)
            details = self._table_family_quality_details(families.get(table_id, [table]))
            actionable = tuple(details["actionable_reasons"])
            conflicts = int(details["conflict_count"])
            # An absent optional OCR score is a note, not a defect.  It must not
            # consume the repair budget by itself.
            if not actionable and conflicts <= 0:
                continue
            data = dict(table.structured_data or {})
            bbox_value = _normalized_bbox(data.get("bbox"))
            bbox = tuple(bbox_value) if bbox_value is not None else None
            reading_order: Optional[int]
            try:
                reading_order = (
                    int(data.get("reading_order"))
                    if data.get("reading_order") is not None
                    else None
                )
            except (TypeError, ValueError, OverflowError):
                reading_order = None
            critical_count = len(set(actionable) & _TABLE_SECOND_PASS_CRITICAL_REASONS)
            known_confidence_deficit = sum(
                1
                for reason in actionable
                if reason in {"low_structure_confidence", "low_ocr_confidence"}
            )
            weak_match = int("weak_table_record_match" in actionable or "missing_table_record" in actionable)
            rank_key = (
                -int(conflicts > 0),
                -critical_count,
                -known_confidence_deficit,
                -weak_match,
                int(table.page_number or 1),
                float(table.position_y or 0.0),
                table_id,
            )
            candidates.append(
                TableSecondPassCandidate(
                    source_table_id=table_id,
                    table_segment_id=table.segment_id,
                    page_number=max(1, int(table.page_number or 1)),
                    bbox=bbox,
                    reading_order=reading_order,
                    reasons=actionable,
                    conflict_count=conflicts,
                    rank_key=rank_key,
                )
            )

        candidates.sort(key=lambda item: item.rank_key)
        budget = (
            min(len(tables), int(math.ceil(len(tables) * ratio)))
            if ratio > 0.0 and tables
            else 0
        )
        selected = candidates[:budget]
        pages = tuple(sorted({candidate.page_number for candidate in selected}))

        adaptive_options = _adaptive_prediction_options_by_page(page_analysis)
        try:
            base_min = max(784, int(os.getenv("PADDLEOCR_VLM_MIN_PIXELS", "112896") or "112896"))
            base_max = max(base_min, int(os.getenv("PADDLEOCR_VLM_MAX_PIXELS", "1003520") or "1003520"))
            base_tokens = max(128, int(os.getenv("PADDLEOCR_VLM_MAX_NEW_TOKENS", "2048") or "2048"))
            second_tokens = max(
                base_tokens,
                int(os.getenv("REPORT_TABLE_SECOND_PASS_MAX_NEW_TOKENS", "4096") or "4096"),
            )
        except (TypeError, ValueError, OverflowError):
            base_min, base_max, base_tokens, second_tokens = 112896, 1003520, 2048, 4096
        area_scale = requested_zoom * requested_zoom
        scaled_min = max(784, min(4_014_080, int(math.ceil(base_min * area_scale))))
        scaled_max = max(
            scaled_min,
            min(4_014_080, int(math.ceil(base_max * area_scale))),
        )
        second_tokens = max(128, min(8192, second_tokens))
        prediction_options: Dict[int, Dict[str, Any]] = {}
        for page in pages:
            options = dict(adaptive_options.get(page, {}))
            options.update(
                {
                    "use_layout_detection": True,
                    "use_ocr_for_image_block": True,
                    "min_pixels": scaled_min,
                    "max_pixels": scaled_max,
                    "max_new_tokens": second_tokens,
                }
            )
            prediction_options[page] = options

        return TableSecondPassPlan(
            total_tables=len(tables),
            budget_tables=budget,
            candidates=tuple(candidates),
            selected_table_ids=tuple(candidate.source_table_id for candidate in selected),
            pages=pages,
            render_zoom=requested_zoom,
            prediction_options=prediction_options,
        )

    @staticmethod
    def _normalise_table_identity_text(value: object) -> str:
        text = unicodedata.normalize("NFKC", unescape(str(value or "")))
        text = text.replace("\u00a0", " ").casefold().strip()
        text = re.sub(
            r"\b(fy|cy)\s*['\u2019]?\s*(\d{2})\b",
            lambda match: f"{match.group(1)}20{match.group(2)}",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"[^\w%+./-]+", " ", text, flags=re.UNICODE)
        return re.sub(r"\s+", " ", text).strip()

    def _table_family_semantic_identity(
        self,
        family: Sequence[TextSegment],
    ) -> Dict[str, Any]:
        table = next(
            (segment for segment in family if segment.segment_type == "table"),
            None,
        )
        rows: List[List[str]] = []
        header_model: Dict[str, Any] = {}
        if table is not None:
            rows, _cells, quality = self._parse_table_details(table.content)
            header_model = dict(quality.get("header_model") or {})

        header_paths: List[str] = []
        for path in header_model.get("header_paths") or []:
            normalized = " > ".join(
                value
                for raw in path
                if (value := self._normalise_table_identity_text(raw))
            )
            if normalized:
                header_paths.append(normalized)

        def row_sort_key(segment: TextSegment) -> Tuple[int, float, str]:
            data = dict(segment.structured_data or {})
            try:
                row_index = int(data.get("row_index"))
            except (TypeError, ValueError, OverflowError):
                row_index = 1_000_000
            return row_index, float(segment.position_y or 0.0), str(segment.segment_id)

        row_labels: List[str] = []
        for segment in sorted(
            [item for item in family if item.segment_type == "table_row"],
            key=row_sort_key,
        ):
            data = dict(segment.structured_data or {})
            raw_path = data.get("row_header_path") or []
            if isinstance(raw_path, str):
                raw_path = [raw_path]
            normalized = " > ".join(
                value
                for raw in raw_path
                if (value := self._normalise_table_identity_text(raw))
            )
            if not normalized:
                normalized = self._normalise_table_identity_text(
                    segment.row_header or data.get("row_header")
                )
            if normalized:
                row_labels.append(normalized)

        if not row_labels and rows:
            headers = [str(value or "") for value in header_model.get("headers") or []]
            for row_index in header_model.get("data_row_indices") or []:
                try:
                    row = rows[int(row_index)]
                except (IndexError, TypeError, ValueError, OverflowError):
                    continue
                normalized = self._normalise_table_identity_text(
                    self._infer_row_header(headers, row)
                )
                if normalized:
                    row_labels.append(normalized)

        years: set[int] = set()
        for value in header_paths:
            years.update(self._extract_table_years(value))
        units: set[Tuple[str, float]] = set()
        for segment in family:
            data = dict(segment.structured_data or {})
            for value in (
                data.get("year"),
                data.get("source_year_label"),
                data.get("col_header"),
                *(data.get("header_path") or []),
            ):
                years.update(self._extract_table_years(value))

            base_unit = str(data.get("unit_base") or "").strip()
            if base_unit:
                try:
                    multiplier = float(data.get("unit_multiplier") or 1.0)
                except (TypeError, ValueError, OverflowError):
                    multiplier = 1.0
                units.add((base_unit.casefold(), multiplier))
                continue
            for raw_unit in (segment.unit, data.get("unit"), data.get("raw_unit")):
                for spec in self._extract_table_unit_specs(raw_unit):
                    if spec.get("base_unit"):
                        units.add(
                            (
                                str(spec.get("base_unit")).casefold(),
                                float(spec.get("multiplier") or 1.0),
                            )
                        )

        return {
            "header_paths": tuple(header_paths),
            "row_labels": tuple(row_labels),
            "years": frozenset(years),
            "units": frozenset(units),
        }

    @staticmethod
    def _table_identity_label_matches(first: str, second: str) -> bool:
        # Edit-distance similarity is unsafe for categorical table identity:
        # `direct`/`indirect`, `renewable`/`non-renewable`, and similar opposite
        # labels have deceptively high string similarity.  The normalizer already
        # handles Unicode, whitespace, case and FY/CY short years, so identity
        # axes must otherwise match exactly.
        return bool(first) and first == second

    def _table_identity_sequence_is_preserved(
        self,
        first: Sequence[str],
        second: Sequence[str],
    ) -> bool:
        if not first:
            return True
        if not second:
            return False
        next_index = 0
        for expected in first:
            match_index = next(
                (
                    index
                    for index in range(next_index, len(second))
                    if self._table_identity_label_matches(expected, second[index])
                ),
                None,
            )
            if match_index is None:
                return False
            next_index = match_index + 1
        return True

    def _second_pass_table_identity_compatible(
        self,
        first_family: Sequence[TextSegment],
        second_family: Sequence[TextSegment],
    ) -> bool:
        first = self._table_family_semantic_identity(first_family)
        second = self._table_family_semantic_identity(second_family)
        if not self._table_identity_sequence_is_preserved(
            first["header_paths"], second["header_paths"]
        ):
            return False
        if not self._table_identity_sequence_is_preserved(
            first["row_labels"], second["row_labels"]
        ):
            return False
        if first["years"] and not first["years"].issubset(second["years"]):
            return False
        if first["units"] and not first["units"].issubset(second["units"]):
            return False
        # At least one stable textual axis must identify the table.  Purely
        # numeric matrices without headers/row labels are never auto-replaced.
        return bool(first["header_paths"] or first["row_labels"])

    def _second_pass_table_match_score(
        self,
        first: TextSegment,
        second: TextSegment,
        *,
        first_order: int,
        second_order: int,
        page_span: int,
    ) -> Tuple[float, float, float]:
        first_rows = self._parse_table_rows(first.content)
        second_rows = self._parse_table_rows(second.content)
        left = _normalised_table_text(first_rows)
        right = _normalised_table_text(second_rows)
        if left and right:
            sequence_score = SequenceMatcher(None, left, right, autojunk=False).ratio()
            left_tokens = set(re.findall(r"[\w%+.-]+", left, flags=re.UNICODE))
            right_tokens = set(re.findall(r"[\w%+.-]+", right, flags=re.UNICODE))
            union = left_tokens | right_tokens
            token_score = len(left_tokens & right_tokens) / len(union) if union else 0.0
            text_score = max(sequence_score, token_score)
        else:
            text_score = 0.0
        first_bbox = _normalized_bbox((first.structured_data or {}).get("bbox"))
        second_bbox = _normalized_bbox((second.structured_data or {}).get("bbox"))
        bbox_score = _bbox_iou(first_bbox, second_bbox)
        order_score = max(0.0, 1.0 - abs(first_order - second_order) / max(1, page_span))
        if first_bbox is not None and second_bbox is not None:
            score = 0.55 * bbox_score + 0.35 * text_score + 0.10 * order_score
        else:
            # Reading order is not table identity when geometry is absent.
            score = text_score
        return score, text_score, bbox_score

    def _second_pass_family_is_complete(
        self,
        first_family: Sequence[TextSegment],
        second_family: Sequence[TextSegment],
    ) -> bool:
        first = self._table_family_quality_details(first_family)
        second = self._table_family_quality_details(second_family)
        if not second["has_table_record"] or int(second["row_count"]) < 2:
            return False
        if set(second["critical_reasons"]) - set(first["critical_reasons"]):
            return False
        if int(first["nonempty_cells"]) > 0 and int(second["nonempty_cells"]) < max(
            2,
            int(math.ceil(int(first["nonempty_cells"]) * 0.85)),
        ):
            return False
        if int(first["numeric_cells"]) > 0 and int(second["numeric_cells"]) < int(
            math.ceil(int(first["numeric_cells"]) * 0.80)
        ):
            return False
        for confidence_name in ("structure_confidence", "ocr_confidence"):
            first_confidence = first.get(confidence_name)
            second_confidence = second.get(confidence_name)
            if (
                first_confidence is not None
                and second_confidence is not None
                and float(second_confidence) + 0.05 < float(first_confidence)
            ):
                return False
        return True

    def _clone_second_pass_family(
        self,
        first_family: Sequence[TextSegment],
        second_family: Sequence[TextSegment],
        candidate: TableSecondPassCandidate,
        *,
        first_quality: Dict[str, Any],
        second_quality: Dict[str, Any],
        match_score: float,
        prediction_options: Dict[str, Any],
        requested_render_zoom: float,
        effective_render_zoom: float,
    ) -> List[TextSegment]:
        first_table = next(
            (segment for segment in first_family if segment.segment_type == "table"),
            first_family[0],
        )
        first_data = dict(first_table.structured_data or {})
        raw_segment_ids = [
            str(raw_segment.segment_id or "").strip()
            for raw_segment in second_family
        ]
        if (
            any(not segment_id for segment_id in raw_segment_ids)
            or len(set(raw_segment_ids)) != len(raw_segment_ids)
        ):
            # A family with missing/duplicate IDs cannot be rewritten without
            # risking broken row_segment_id references.
            return []
        id_map = {
            raw_segment_id: f"{candidate.table_segment_id}_sp2_{index:04d}"
            for index, raw_segment_id in enumerate(raw_segment_ids, 1)
        }
        provenance_fields = (
            "table_record_id",
            "table_record_source",
            "block_id",
            "block_type",
            "reading_order",
            "page_width",
            "page_height",
            "source_page_index",
            "source_json_path",
            "source_image_paths",
            "asset_ids",
            "text_fingerprint",
            "structured_html",
            "structured_html_sha256",
            "bbox",
            "table_match_score",
            "table_text_match_score",
            "table_bbox_iou",
        )
        first_pass_provenance = {
            field: first_data.get(field)
            for field in provenance_fields
            if first_data.get(field) not in (None, "", [])
        }
        cloned: List[TextSegment] = []
        for index, raw_segment in enumerate(second_family, 1):
            copier = getattr(raw_segment, "model_copy", None)
            segment = copier(deep=True) if callable(copier) else raw_segment.copy(deep=True)
            segment.segment_id = id_map[str(raw_segment.segment_id)]
            segment.source_table_id = candidate.source_table_id
            segment.parse_pass = 2
            data = dict(segment.structured_data or {})
            second_pass_provenance = {
                field: data.get(field)
                for field in provenance_fields
                if data.get(field) not in (None, "", [])
            }
            raw_row_segment_id = str(data.get("row_segment_id") or "").strip()
            if raw_row_segment_id:
                mapped_row_segment_id = id_map.get(raw_row_segment_id)
                if not mapped_row_segment_id:
                    return []
                data["row_segment_id"] = mapped_row_segment_id
            data.update(
                {
                    "table_id": candidate.source_table_id,
                    "parse_pass": 2,
                    "second_pass_replaced": True,
                    "previous_parse_pass": 1,
                    "second_pass_match_score": round(match_score, 4),
                }
            )
            if segment.segment_type == "table":
                data.update(
                    {
                        "first_pass_quality": first_quality,
                        "second_pass_quality": second_quality,
                        "quality_delta": {
                            "first_key": list(
                                self._table_family_quality_key(first_family)
                            ),
                            "second_key": list(
                                self._table_family_quality_key(second_family)
                            ),
                        },
                        "second_pass_prediction_options": dict(prediction_options),
                        "requested_render_zoom": requested_render_zoom,
                        "effective_render_zoom": effective_render_zoom,
                        "resolved_conflicts": list(
                            first_quality.get("conflicts") or []
                        ),
                        "second_pass_provenance": second_pass_provenance,
                        "first_pass_provenance": dict(first_pass_provenance),
                    }
                )
            else:
                for audit_field in (
                    "first_pass_quality",
                    "second_pass_quality",
                    "quality_delta",
                    "second_pass_prediction_options",
                    "requested_render_zoom",
                    "effective_render_zoom",
                    "resolved_conflicts",
                    "second_pass_provenance",
                    "first_pass_provenance",
                ):
                    data.pop(audit_field, None)
            # The accepted family is an embedded second-pass record: retain its
            # own block/hash/geometry fields, but never expose the temporary
            # worker JSON path as if it were a durable manifest record.  The
            # complete first-pass tuple remains available as one audit envelope.
            fingerprint = str(
                second_pass_provenance.get("structured_html_sha256") or ""
            )[:16]
            data["table_record_id"] = (
                f"embedded-pass2:{candidate.source_table_id}:{fingerprint or 'unknown'}"
            )
            data["table_record_source"] = "embedded_second_pass"
            data.pop("source_json_path", None)
            # Asset blobs describe the same physical table and are durable, so
            # preserve their first-pass links with an explicit generation tag.
            for field in ("source_image_paths", "asset_ids"):
                if (
                    segment.segment_type == "table"
                    and first_data.get(field) not in (None, "", [])
                ):
                    value = first_data[field]
                    data[field] = list(value) if isinstance(value, list) else value
                else:
                    data.pop(field, None)
            if data.get("source_image_paths") or data.get("asset_ids"):
                data["asset_provenance_parse_pass"] = 1
            for field in ("table_title", "section_path"):
                if first_data.get(field) not in (None, "", []):
                    value = first_data[field]
                    data[field] = list(value) if isinstance(value, list) else value
            segment.structured_data = data
            cloned.append(segment)
        cloned_ids = {segment.segment_id for segment in cloned}
        if len(cloned_ids) != len(cloned):
            return []
        if any(
            str((segment.structured_data or {}).get("row_segment_id") or "")
            not in cloned_ids
            for segment in cloned
            if (segment.structured_data or {}).get("row_segment_id")
        ):
            return []
        return cloned

    def _apply_table_second_pass(
        self,
        segments: List[TextSegment],
        second_pass_segments: Sequence[TextSegment],
        plan: TableSecondPassPlan,
        *,
        effective_render_zoom: Optional[float] = None,
        effective_render_zoom_by_page: Optional[Dict[int, float]] = None,
    ) -> Dict[str, Any]:
        """Atomically replace selected table families only when quality improves."""
        first_families = self._table_family_map(segments)
        second_families = self._table_family_map(second_pass_segments)
        first_tables = {
            str(segment.source_table_id): segment
            for segment in segments
            if segment.segment_type == "table" and segment.source_table_id
        }
        second_tables_by_page: Dict[int, List[TextSegment]] = {}
        for segment in second_pass_segments:
            if segment.segment_type == "table" and segment.source_table_id:
                second_tables_by_page.setdefault(int(segment.page_number), []).append(segment)
        candidate_map = {
            candidate.source_table_id: candidate
            for candidate in plan.candidates
            if candidate.source_table_id in set(plan.selected_table_ids)
        }
        # Build the identity graph from every first-pass table on each selected
        # page, including clean/unselected siblings.  Otherwise a repair target
        # can steal the second-pass result that uniquely belongs to a clean table.
        match_edges: List[Tuple[float, float, float, str, TextSegment]] = []
        selected_pages = {
            candidate.page_number for candidate in candidate_map.values()
        }
        for page_number in sorted(selected_pages):
            first_page_tables = sorted(
                [
                    table
                    for table in first_tables.values()
                    if int(table.page_number) == int(page_number)
                ],
                key=lambda item: (item.position_y, item.position_x or 0.0),
            )
            ordered_second_tables = sorted(
                second_tables_by_page.get(page_number, []),
                key=lambda item: (item.position_y, item.position_x or 0.0),
            )
            page_has_multiple_tables = (
                len(first_page_tables) > 1 or len(ordered_second_tables) > 1
            )
            page_span = max(
                len(first_page_tables),
                len(ordered_second_tables),
                1,
            )
            for first_order, first in enumerate(first_page_tables):
                first_id = str(first.source_table_id or "")
                first_family = first_families.get(first_id, [])
                if not first_id or not first_family:
                    continue
                for second_order, second in enumerate(ordered_second_tables):
                    second_id = str(second.source_table_id or "")
                    second_family = second_families.get(second_id, [])
                    if not second_id or not second_family:
                        continue
                    score, text_score, bbox_score = (
                        self._second_pass_table_match_score(
                            first,
                            second,
                            first_order=first_order,
                            second_order=second_order,
                            page_span=page_span,
                        )
                    )
                    first_bbox = _normalized_bbox(
                        (first.structured_data or {}).get("bbox")
                    )
                    second_bbox = _normalized_bbox(
                        (second.structured_data or {}).get("bbox")
                    )
                    if (
                        page_has_multiple_tables
                        and first_bbox is not None
                        and second_bbox is not None
                        and bbox_score < 0.10
                    ):
                        continue
                    # If either bbox is absent, ordinal position contributes no
                    # admission evidence.  Identity must come from table content
                    # and the semantic axes below.
                    if score < 0.45 or (
                        text_score < 0.35 and bbox_score < 0.20
                    ):
                        continue
                    if not self._second_pass_table_identity_compatible(
                        first_family,
                        second_family,
                    ):
                        continue
                    match_edges.append(
                        (score, text_score, bbox_score, first_id, second)
                    )

        try:
            match_margin = float(
                os.getenv("REPORT_TABLE_SECOND_PASS_MATCH_MARGIN", "0.10")
                or "0.10"
            )
        except (TypeError, ValueError, OverflowError):
            match_margin = 0.10
        # The safety margin cannot be disabled through configuration.  A zero
        # margin would let an arbitrary sort winner escape a tied candidate set.
        match_margin = max(0.10, min(0.50, match_margin))

        edges_by_first: Dict[
            str, List[Tuple[float, float, float, str, TextSegment]]
        ] = {}
        edges_by_second: Dict[
            str, List[Tuple[float, float, float, str, TextSegment]]
        ] = {}
        for edge in match_edges:
            edges_by_first.setdefault(edge[3], []).append(edge)
            edges_by_second.setdefault(
                str(edge[4].source_table_id or ""), []
            ).append(edge)

        def unique_best(
            edges: Sequence[Tuple[float, float, float, str, TextSegment]],
        ) -> Optional[Tuple[str, str]]:
            ranked = sorted(edges, key=lambda item: item[0], reverse=True)
            if not ranked:
                return None
            if (
                len(ranked) > 1
                and float(ranked[0][0]) - float(ranked[1][0])
                < match_margin
            ):
                return None
            return (
                ranked[0][3],
                str(ranked[0][4].source_table_id or ""),
            )

        best_by_first = {
            table_id: unique_best(edges)
            for table_id, edges in edges_by_first.items()
        }
        best_by_second = {
            table_id: unique_best(edges)
            for table_id, edges in edges_by_second.items()
        }
        match_candidates = [
            edge
            for edge in match_edges
            if edge[3] in candidate_map
            and best_by_first.get(edge[3])
            == (edge[3], str(edge[4].source_table_id or ""))
            and best_by_second.get(str(edge[4].source_table_id or ""))
            == (edge[3], str(edge[4].source_table_id or ""))
        ]

        used_first: set[str] = set()
        used_second: set[str] = set()
        matched_first: set[str] = set()
        replacements: Dict[str, List[TextSegment]] = {}
        rejected_not_improved = 0
        rejected_incomplete = 0
        for score, _text_score, _bbox_score, table_id, second_table in sorted(
            match_candidates,
            key=lambda item: item[0],
            reverse=True,
        ):
            second_id = str(second_table.source_table_id or "")
            if table_id in used_first or not second_id or second_id in used_second:
                continue
            candidate = candidate_map[table_id]
            first_family = first_families.get(table_id, [])
            second_family = second_families.get(second_id, [])
            if not first_family or not second_family:
                continue
            matched_first.add(table_id)
            if not self._second_pass_family_is_complete(first_family, second_family):
                rejected_incomplete += 1
                continue
            first_key = self._table_family_quality_key(first_family)
            second_key = self._table_family_quality_key(second_family)
            if second_key <= first_key:
                rejected_not_improved += 1
                continue
            first_quality = self._table_family_quality_details(first_family)
            second_quality = self._table_family_quality_details(second_family)
            replacement = self._clone_second_pass_family(
                first_family,
                second_family,
                candidate,
                first_quality=first_quality,
                second_quality=second_quality,
                match_score=score,
                prediction_options=plan.prediction_options.get(candidate.page_number, {}),
                requested_render_zoom=plan.render_zoom,
                effective_render_zoom=(
                    float(
                        (effective_render_zoom_by_page or {}).get(
                            candidate.page_number,
                            effective_render_zoom
                            if effective_render_zoom is not None
                            else plan.render_zoom,
                        )
                    )
                ),
            )
            if not replacement:
                rejected_incomplete += 1
                continue
            used_first.add(table_id)
            used_second.add(second_id)
            replacements[table_id] = replacement

        if replacements:
            rebuilt: List[TextSegment] = []
            inserted: set[str] = set()
            for segment in segments:
                table_id = str(segment.source_table_id or "")
                replacement = replacements.get(table_id)
                if replacement is None:
                    rebuilt.append(segment)
                    continue
                if table_id not in inserted:
                    rebuilt.extend(replacement)
                    inserted.add(table_id)
            segments[:] = rebuilt

        return {
            "selected_tables": len(plan.selected_table_ids),
            "selected_pages": list(plan.pages),
            "accepted_tables": len(replacements),
            "accepted_table_ids": sorted(replacements),
            "no_match_tables": max(0, len(plan.selected_table_ids) - len(matched_first)),
            "rejected_not_improved": rejected_not_improved,
            "rejected_incomplete": rejected_incomplete,
        }

    def _run_table_second_pass(
        self,
        source_path: Path,
        document_id: str,
        segments: List[TextSegment],
        *,
        page_analysis: Any = None,
        release_after_document: bool = True,
    ) -> Dict[str, Any]:
        """Best-effort high-resolution repair for a bounded table subset."""
        if not _env_bool("REPORT_TABLE_SECOND_PASS_ENABLED", False):
            return {"enabled": False, "accepted_tables": 0}
        plan = self._select_table_second_pass_plan(
            segments,
            page_analysis=page_analysis,
        )
        summary: Dict[str, Any] = {
            "enabled": True,
            "total_tables": plan.total_tables,
            "candidate_tables": len(plan.candidates),
            "budget_tables": plan.budget_tables,
            "selected_tables": len(plan.selected_table_ids),
            "selected_pages": list(plan.pages),
            "render_zoom": plan.render_zoom,
            "accepted_tables": 0,
        }
        if not plan.selected_table_ids or not plan.pages:
            return summary

        self._emit_progress(
            "table_second_pass",
            f"Rechecking {len(plan.selected_table_ids)} table(s) on {len(plan.pages)} page(s).",
            44,
            table_second_pass=summary,
        )
        started = time.perf_counter()
        try:
            result = self._run_paddleocr_vl_page_batch_queue(
                source_path,
                page_analysis=page_analysis,
                selected_page_numbers=plan.pages,
                prediction_options_by_page=plan.prediction_options,
                partial_result=True,
                promote_visuals=False,
                parse_pass=2,
                render_zoom=plan.render_zoom,
                emit_progress=True,
                release_after_document=release_after_document,
            )
            deferred_job_id = str(
                result.get("_paddle_lifecycle_job_id") or ""
            )
            if deferred_job_id:
                summary["_paddle_lifecycle_job_id"] = deferred_job_id
            second_markdown = str(result.get("markdown") or "").strip()
            if not second_markdown:
                raise ContentExtractionError(
                    "Table second pass returned empty Markdown.",
                    file_path=str(source_path),
                )
            second_segments = self._segments_from_markdown(
                second_markdown,
                f"{document_id}_sp2",
            )
            second_records = list(result.get("table_records") or [])
            if second_records:
                self._prefer_structured_table_records(
                    second_segments,
                    second_records,
                    f"{document_id}_sp2",
                )
            apply_stats = self._apply_table_second_pass(
                segments,
                second_segments,
                plan,
                effective_render_zoom=float(
                    result.get("effective_render_zoom") or plan.render_zoom
                ),
                effective_render_zoom_by_page={
                    int(page): float(zoom)
                    for page, zoom in (
                        result.get("effective_render_zoom_by_page") or {}
                    ).items()
                },
            )
            summary.update(apply_stats)
            summary["successful_pages"] = list(result.get("processed_pages") or plan.pages)
            summary["failed_pages"] = list(result.get("failed_pages") or [])
            summary["prediction_options"] = plan.prediction_options
        except Exception as exc:
            # The repair layer may improve evidence but must never discard an
            # otherwise usable first-pass report.
            self.logger.warning(
                f"Table second pass failed; preserving first-pass tables: "
                f"file={source_path.name}, pages={list(plan.pages)}, error={exc}"
            )
            summary["error"] = str(exc)
            summary["failed_pages"] = list(plan.pages)
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return summary

    def _stitch_continued_tables(self, segments: List[TextSegment]) -> None:
        """Join physical table fragments only when continuation evidence is complete.

        Repeated generic headers (for example ``Year | Value``) are not table
        identity.  A cross-page join therefore requires all of the following:

        * the normalized table title and section path agree;
        * the fragments are at the bottom/top page edges, or one fragment has
          an explicit continuation marker;
        * column counts, value types and any discoverable units are compatible.

        Metadata produced by some parsers does not contain titles or geometry.
        In that case this intentionally fails closed instead of guessing.
        """
        tables = sorted(
            [segment for segment in segments if segment.segment_type == "table" and segment.source_table_id],
            key=lambda item: (item.page_number, item.position_y),
        )

        continuation_token = (
            r"(?:continued|continuation|cont\.?|cont[\u2019']?d|"
            r"\u7eed\u8868|\u63a5\u4e0a\u9875|\u4e0b\u9875\u7eed)"
        )
        continuation_re = re.compile(
            rf"(?ix)(?:"
            rf"\(\s*{continuation_token}\s*\)|"
            rf"(?:[-\u2013\u2014,:]\s*|\s+){continuation_token}\s*$|"
            rf"^{continuation_token}(?:\s*[-\u2013\u2014:]|\s*$)|"
            rf"\bcontinued\s+(?:from|on)\s+(?:the\s+)?(?:previous|prior|next)?\s*page\b|"
            rf"\bcontinues?\s+on\s+(?:the\s+)?next\s+page\b)"
        )

        def table_data(table: TextSegment) -> Dict[str, Any]:
            return dict(table.structured_data or {})

        def normalize_context(value: Any) -> str:
            text = unescape(str(value or "")).replace("\u00a0", " ").strip().casefold()
            if not text:
                return ""
            # Strip the full continuation phrases recognized below before
            # comparing titles. Otherwise "Energy" and
            # "Energy - continued from previous page" can never match.
            text = continuation_re.sub(" ", text)
            text = re.sub(
                rf"(?ix)\(\s*{continuation_token}\s*\)",
                " ",
                text,
            )
            text = re.sub(
                rf"(?ix)(?:[-\u2013\u2014,:]\s*|\s+){continuation_token}\s*$",
                " ",
                text,
            )
            text = re.sub(
                rf"(?ix)^{continuation_token}\s*[-\u2013\u2014:]\s*",
                " ",
                text,
            )
            return re.sub(r"[^\w%]+", " ", text, flags=re.UNICODE).strip()

        def table_title(table: TextSegment) -> str:
            data = table_data(table)
            source = str(data.get("table_title_source") or "").strip()
            section_values = section_path(table)

            # An explicit structured caption remains independent evidence even
            # when its wording happens to equal the enclosing section heading.
            if source == "structured_record_caption":
                for key in ("table_title", "caption"):
                    if normalized := normalize_context(data.get(key)):
                        return normalized

            # A Markdown section heading is not a table title, but an attached
            # parser caption can still supply independent identity.
            if normalized_caption := normalize_context(data.get("caption")):
                return normalized_caption
            if source == "section_heading":
                return ""

            for key in ("table_title", "title"):
                normalized = normalize_context(data.get(key))
                if normalized:
                    # Legacy Markdown artifacts did not tag their title source;
                    # the nearest heading was copied into both fields. Treat
                    # that duplicate as missing independent title evidence.
                    if section_values and normalized == section_values[-1]:
                        continue
                    return normalized
            return ""

        def section_path(table: TextSegment) -> Tuple[str, ...]:
            raw_path = table_data(table).get("section_path")
            if isinstance(raw_path, str):
                raw_values: Sequence[Any] = [raw_path]
            elif isinstance(raw_path, (list, tuple)):
                raw_values = raw_path
            else:
                raw_values = []
            return tuple(
                normalized
                for value in raw_values
                if (normalized := normalize_context(value))
            )

        def has_continuation_cue(table: TextSegment) -> bool:
            data = table_data(table)
            if any(
                data.get(key) is True
                for key in (
                    "is_continuation",
                    "continued",
                    "continues_on_next_page",
                    "continued_from_previous_page",
                )
            ):
                return True
            cue_fields = [
                data.get("table_title"),
                data.get("caption"),
            ]
            if any(
                str(data.get(key) or "").strip()
                for key in ("continuation_label", "continuation_of")
            ):
                return True
            # A caption/marker normally precedes the first row.  Limit the raw
            # table scan so a later narrative cell containing "continued" does
            # not become identity evidence.
            cue_fields.append(str(table.content or "")[:240])
            return any(continuation_re.search(str(value or "").strip()) for value in cue_fields)

        def normalized_bbox(table: TextSegment) -> Optional[List[float]]:
            data = table_data(table)
            try:
                page_width = float(data.get("page_width") or 0.0)
                page_height = float(data.get("page_height") or 0.0)
            except (TypeError, ValueError, OverflowError):
                return None
            return _normalized_bbox(
                data.get("bbox"),
                page_width=page_width,
                page_height=page_height,
            )

        def has_edge_geometry(previous_table: TextSegment, current_table: TextSegment) -> bool:
            previous_bbox = normalized_bbox(previous_table)
            current_bbox = normalized_bbox(current_table)
            if previous_bbox is None or current_bbox is None:
                return False
            try:
                bottom_threshold = min(
                    0.95,
                    max(
                        0.50,
                        float(
                            os.getenv(
                                "REPORT_TABLE_CONTINUATION_BOTTOM_THRESHOLD",
                                "0.78",
                            )
                            or "0.78"
                        ),
                    ),
                )
                top_threshold = min(
                    0.50,
                    max(
                        0.05,
                        float(
                            os.getenv(
                                "REPORT_TABLE_CONTINUATION_TOP_THRESHOLD",
                                "0.22",
                            )
                            or "0.22"
                        ),
                    ),
                )
            except (TypeError, ValueError):
                bottom_threshold, top_threshold = 0.78, 0.22
            return previous_bbox[3] >= bottom_threshold and current_bbox[1] <= top_threshold

        def value_type(value: Any) -> Optional[str]:
            text = re.sub(r"\s+", " ", unescape(str(value or ""))).strip()
            if not text or text.casefold() in {"-", "--", "n/a", "na", "none", "null"}:
                return None
            if "%" in text or re.search(r"(?i)\bpercent(?:age)?\b", text):
                return "percentage"
            if re.search(r"[$\u00a3\u20ac\u00a5]", text):
                return "currency"
            if re.fullmatch(r"(?:FY|CY)?\s*['\u2019]?\d{2,4}", text, flags=re.IGNORECASE):
                return "year"
            numeric = re.sub(r"[(),]", "", text)
            if re.fullmatch(
                r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s*[A-Za-z0-9\u00b2\u00b3/_-]+)?",
                numeric,
            ):
                return "number"
            return "text"

        def column_type_signature(rows: List[List[str]]) -> Optional[Tuple[frozenset[str], ...]]:
            if len(rows) < 2:
                return None
            width = len(rows[0])
            if width < 2 or any(len(row) != width for row in rows):
                return None
            signature: List[frozenset[str]] = []
            evidenced_columns = 0
            for column in range(width):
                types = frozenset(
                    candidate
                    for row in rows[1:]
                    if (candidate := value_type(row[column])) is not None
                )
                if types:
                    evidenced_columns += 1
                signature.append(types)
            # One populated column is too little evidence to establish a table
            # schema, even when the repeated headers happen to be identical.
            return tuple(signature) if evidenced_columns >= 2 else None

        unit_aliases = {
            "percent": "%",
            "percentage": "%",
            "pct": "%",
            "ton": "t",
            "tons": "t",
            "tonne": "t",
            "tonnes": "t",
            "litre": "l",
            "litres": "l",
            "liter": "l",
            "liters": "l",
        }
        unit_atom = (
            r"tco2e|kgco2e|ktco2e|mtco2e|co2e|"
            r"kwh|mwh|gwh|twh|gj|tj|pj|mmbtu|m3|kl|ml|l|"
            r"kg|tonnes?|tons?|t|usd|eur|gbp|cny|%|"
            r"employees?|ftes?|revenue|units?"
        )
        unit_expression_re = re.compile(
            rf"(?i)(?<![A-Za-z0-9])"
            rf"(?:(thousand|million|billion)\s+)?({unit_atom})"
            rf"(?:\s*(?:/|per)\s*"
            rf"(?:(thousand|million|billion)\s+)?({unit_atom}))?"
            rf"(?![A-Za-z0-9])"
        )

        def canonical_unit(value: Any) -> str:
            text = re.sub(r"\s+", " ", unescape(str(value or ""))).strip().casefold()
            if not text:
                return ""
            text = text.replace("co\u2082", "co2").replace("m\u00b3", "m3")
            co2_suffix = r"co2(?:e|\s+equivalents?)"
            text = re.sub(
                rf"\bkilograms?\s+(?:of\s+)?{co2_suffix}\b",
                "kgco2e",
                text,
            )
            text = re.sub(
                rf"\bkilotonnes?\s+(?:of\s+)?{co2_suffix}\b",
                "ktco2e",
                text,
            )
            text = re.sub(
                rf"\b(?:metric\s+)?(?:tonnes?|tons?)\s+(?:of\s+)?{co2_suffix}\b",
                "tco2e",
                text,
            )
            text = re.sub(
                rf"\bkg\s+(?:of\s+)?{co2_suffix}\b",
                "kgco2e",
                text,
            )
            text = re.sub(
                rf"\bt\s+(?:of\s+)?{co2_suffix}\b",
                "tco2e",
                text,
            )
            text = re.sub(r"\bmetric\s+tonnes?\b", "t", text)
            text = re.sub(r"\bmetric\s+tons?\b", "t", text)
            text = re.sub(r"\bcubic\s+met(?:er|re)s?\b", "m3", text)
            match = unit_expression_re.search(text)
            if not match:
                return ""
            numerator_scale = str(match.group(1) or "").casefold()
            numerator = unit_aliases.get(
                str(match.group(2) or "").casefold(),
                str(match.group(2) or "").casefold(),
            )
            denominator_scale = str(match.group(3) or "").casefold()
            denominator = unit_aliases.get(
                str(match.group(4) or "").casefold(),
                str(match.group(4) or "").casefold(),
            )
            rendered = f"{numerator_scale}:{numerator}" if numerator_scale else numerator
            if denominator:
                rendered += (
                    f"/{denominator_scale}:{denominator}"
                    if denominator_scale
                    else f"/{denominator}"
                )
            return rendered

        def unit_signature(
            table: TextSegment,
            rows: List[List[str]],
        ) -> Tuple[Tuple[str, ...], ...]:
            headers = [normalize_context(value) for value in rows[0]] if rows else []
            column_units: List[set[str]] = [set() for _ in headers]
            unit_columns = {
                index
                for index, header in enumerate(headers)
                if header in {"unit", "units", "uom", "unit of measure", "\u5355\u4f4d"}
            }
            for row in rows[1:]:
                for index, value in enumerate(row):
                    if index >= len(column_units):
                        continue
                    if index in unit_columns or value_type(value) in {
                        "percentage",
                        "currency",
                        "number",
                    }:
                        if (unit := canonical_unit(value)):
                            column_units[index].add(unit)
            generic_unit_headers = {"unit", "units", "uom", "unit of measure", "\u5355\u4f4d"}
            for index, header in enumerate(rows[0] if rows else []):
                # A label saying merely "Unit" describes the column; it is not
                # evidence that a real measurement unit was extracted.
                if normalize_context(header) in generic_unit_headers:
                    continue
                if (unit := canonical_unit(header)):
                    column_units[index].add(unit)
            table_id = table.source_table_id
            for related in segments:
                if (
                    related.source_table_id != table_id
                    or related.page_number != table.page_number
                ):
                    continue
                for raw_unit in (
                    related.unit,
                    (related.structured_data or {}).get("unit"),
                    (related.structured_data or {}).get("cell_unit"),
                    (related.structured_data or {}).get("raw_unit"),
                ):
                    if (unit := canonical_unit(raw_unit)):
                        related_data = dict(related.structured_data or {})
                        raw_column_index = next(
                            (
                                related_data.get(key)
                                for key in (
                                    "col_index",
                                    "column_index",
                                    "column_idx",
                                    "col_number",
                                    "source_column_index",
                                )
                                if related_data.get(key) is not None
                            ),
                            None,
                        )
                        try:
                            column_index = (
                                int(raw_column_index)
                                if raw_column_index is not None
                                else None
                            )
                        except (TypeError, ValueError, OverflowError):
                            column_index = None
                        if column_index is None or not 0 <= column_index < len(column_units):
                            # An unbound unit cannot prove per-column
                            # compatibility, so fail closed by ignoring it.
                            continue
                        column_units[column_index].add(unit)
            return tuple(tuple(sorted(values)) for values in column_units)

        previous: Optional[TextSegment] = None
        for current in tables:
            if previous is None or current.page_number != previous.page_number + 1:
                previous = current
                continue
            previous_rows = self._parse_table_rows(previous.content)
            current_rows = self._parse_table_rows(current.content)
            if not previous_rows or not current_rows:
                previous = current
                continue
            previous_header = self._normalise_table_row(previous_rows[0])
            current_header = self._normalise_table_row(current_rows[0])
            if (
                len(previous_header) < 2
                or tuple(normalize_context(value) for value in previous_header)
                != tuple(normalize_context(value) for value in current_header)
            ):
                previous = current
                continue

            previous_title = table_title(previous)
            current_title = table_title(current)
            previous_section = section_path(previous)
            current_section = section_path(current)
            if (
                not previous_title
                or not current_title
                or previous_title != current_title
                or not previous_section
                or not current_section
                or previous_section != current_section
            ):
                previous = current
                continue

            previous_types = column_type_signature(previous_rows)
            current_types = column_type_signature(current_rows)
            if (
                previous_types is None
                or current_types is None
                or previous_types != current_types
            ):
                previous = current
                continue

            previous_units = unit_signature(previous, previous_rows)
            current_units = unit_signature(current, current_rows)
            if (
                not any(previous_units)
                or not any(current_units)
                or previous_units != current_units
            ):
                previous = current
                continue

            # Page-edge geometry is mandatory. A continuation marker is useful
            # corroboration, but cannot replace the missing physical evidence.
            if not has_edge_geometry(previous, current):
                previous = current
                continue
            old_id = current.source_table_id
            continued_id = previous.source_table_id
            for segment in segments:
                if (
                    segment.source_table_id != old_id
                    or segment.page_number != current.page_number
                ):
                    continue
                segment.source_table_id = continued_id
                data = dict(segment.structured_data or {})
                data.update({
                    "table_id": continued_id,
                    "continued_from_page": previous.page_number,
                    "continued_on_page": current.page_number,
                    "repeated_header_suppressed": True,
                })
                segment.structured_data = data
            root = min(
                (
                    table
                    for table in tables
                    if table.source_table_id == continued_id
                    and table.page_number <= previous.page_number
                ),
                key=lambda table: (table.page_number, table.position_y),
                default=previous,
            )
            previous_data = dict(root.structured_data or {})
            continuation_pages = list(previous_data.get("continuation_pages") or [root.page_number])
            if current.page_number not in continuation_pages:
                continuation_pages.append(current.page_number)
            previous_data["continuation_pages"] = continuation_pages
            root.structured_data = previous_data
            # Keep the last physical page as the next adjacency anchor while the
            # logical table id remains the first page's stable id.
            previous = current

    def _parse_table_details(self, table_text: str) -> tuple[List[List[str]], List[Dict[str, Any]], Dict[str, Any]]:
        is_html = bool(re.search(r"<\s*(table|tr|td|th)\b", table_text or "", flags=re.IGNORECASE))
        cells: List[Dict[str, Any]] = []
        row_metadata: List[Dict[str, Any]] = []
        caption = ""
        if is_html:
            parser = _SimpleHTMLTableParser()
            try:
                parser.feed(table_text)
                parser.close()
                rows = [self._normalise_table_row(row) for row in parser.rows if any(str(c).strip() for c in row)]
                cells = list(parser.cells)
                row_metadata = list(parser.row_metadata)
                caption = str(parser.caption or "").strip()
            except Exception:
                rows = []
        else:
            rows = self._parse_markdown_table_rows(table_text)
            markdown_has_header = self._markdown_table_has_header_separator(table_text)
            for r_idx, row in enumerate(rows):
                for c_idx, value in enumerate(row):
                    cells.append(
                        {
                            "row_index": r_idx,
                            "col_index": c_idx,
                            "text": value,
                            "rowspan": 1,
                            "colspan": 1,
                            "is_header": markdown_has_header and r_idx == 0,
                            "section": "",
                            "scope": "",
                            "tag": "",
                        }
                    )
                row_metadata.append(
                    {
                        "row_index": r_idx,
                        "section": "",
                        "has_header_cells": markdown_has_header and r_idx == 0,
                        "has_data_cells": not (markdown_has_header and r_idx == 0),
                    }
                )

        header_model = self._build_table_header_model(
            rows,
            cells,
            is_html=is_html,
            table_text=table_text,
            row_metadata=row_metadata,
        )

        reasons: List[str] = []
        notes: List[str] = []
        widths = [len(row) for row in rows if row]
        if widths and len(set(widths)) > 1:
            reasons.append("inconsistent_column_count")
        if not rows or not header_model.get("header_row_indices"):
            reasons.append("missing_header")
        elif header_model.get("inferred"):
            notes.append("inferred_header_structure")
        if is_html and table_text.lower().count("<table") != table_text.lower().count("</table"):
            reasons.append("malformed_html")
        if rows:
            year_columns = sum(
                1
                for path in (header_model.get("header_paths") or [])
                if any(self._extract_table_years(value) for value in path)
            )
            numeric_columns = max(
                (
                    sum(1 for value in rows[row_index] if re.search(r"\d", str(value)))
                    for row_index in (header_model.get("data_row_indices") or [])
                ),
                default=0,
            )
            if year_columns and numeric_columns and numeric_columns < year_columns:
                reasons.append("year_value_count_mismatch")

        if is_html and not reasons and not notes:
            structure_confidence = 0.9
        elif is_html and not reasons:
            structure_confidence = 0.82
        else:
            structure_confidence = 0.72 if is_html else 0.80
        # Markdown/HTML carries no calibrated per-cell OCR score.  Keep this as
        # unknown instead of manufacturing a perfect 1.0 confidence; a matched
        # Paddle table record may supply the real value later.
        ocr_confidence: Optional[float] = None
        structure_threshold = float(os.getenv("REPORT_TABLE_STRUCTURE_CONFIDENCE_THRESHOLD", "0.80") or "0.80")
        ocr_threshold = float(os.getenv("REPORT_TABLE_OCR_CONFIDENCE_THRESHOLD", "0.75") or "0.75")
        if structure_confidence < structure_threshold:
            reasons.append("low_structure_confidence")
        if ocr_confidence is not None and ocr_confidence < ocr_threshold:
            reasons.append("low_ocr_confidence")
        reasons = list(dict.fromkeys(reasons))
        notes = list(dict.fromkeys(notes))
        return rows, cells, {
            "structure_confidence": structure_confidence,
            "ocr_confidence": ocr_confidence,
            "review_status": (
                "needs_review"
                if reasons
                else ("verified" if is_html and not notes else "unverified")
            ),
            "reasons": reasons,
            "notes": notes,
            "caption": caption,
            "header_model": header_model,
        }

    def _parse_table_rows(self, table_text: str) -> List[List[str]]:
        html_rows = self._parse_html_table_rows(table_text)
        if html_rows:
            return html_rows
        return self._parse_markdown_table_rows(table_text)

    def _parse_html_table_rows(self, table_html: str) -> List[List[str]]:
        if not re.search(r"<\s*(table|tr|td|th)\b", table_html or "", flags=re.IGNORECASE):
            return []
        parser = _SimpleHTMLTableParser()
        try:
            parser.feed(table_html)
            parser.close()
        except Exception:
            return []
        return [self._normalise_table_row(row) for row in parser.rows if any(str(c).strip() for c in row)]

    def _parse_markdown_table_rows(self, table_md: str) -> List[List[str]]:
        rows: List[List[str]] = []
        for line in table_md.splitlines():
            stripped = line.strip()
            if not stripped or "|" not in stripped:
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not cells:
                continue
            if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c):
                continue
            rows.append(cells)
        return rows

    def _normalise_table_row(self, row: Sequence[Any], width: Optional[int] = None) -> List[str]:
        cells = [re.sub(r"\s+", " ", unescape(str(cell or ""))).strip() for cell in row]
        if width is not None and len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        return cells

    def _infer_row_header(self, headers: Sequence[str], row: Sequence[str]) -> str:
        header_names = [str(h or "").strip().lower() for h in headers]
        preferred_names = ("metric", "indicator", "disclosure", "topic", "code", "sasb code")
        for preferred in preferred_names:
            for idx, header in enumerate(header_names):
                if preferred in header and idx < len(row) and str(row[idx]).strip():
                    return str(row[idx]).strip()
        for value in row:
            if str(value).strip():
                return str(value).strip()
        return ""

    def _format_table_row_context(self, headers: Sequence[str], row: Sequence[str], *, table_title: str = "", page: int = 1) -> str:
        parts: List[str] = []
        if table_title:
            parts.append(f"[Table Title] {table_title}")
        if headers:
            parts.append(f"[Column Headers] {' | '.join(str(h or '').strip() for h in headers)}")
        pairs: List[str] = []
        for idx, value in enumerate(row):
            value_text = str(value or "").strip()
            if not value_text:
                continue
            header = str(headers[idx]).strip() if idx < len(headers) and str(headers[idx]).strip() else f"Column {idx + 1}"
            pairs.append(f"{header}: {value_text}")
        if pairs:
            parts.append(" | ".join(pairs))
        else:
            parts.append(" | ".join(str(x or "").strip() for x in row if str(x or "").strip()))
        parts.append(f"Page: {page}")
        return "\n".join(p for p in parts if p)

    def _page_marker(self, block: str) -> Optional[int]:
        patterns = [
            # 服务端页码标记格式：<!-- Page 12 | PaddleOCR-VL unit 12/116 part 1 -->
            # 只捕获紧跟在 Page 后面的页码，避免误取后面的 unit/part 数字。
            r"<!--\s*page\s+(\d+)\b",
            r"<!--\s*paddleocr-vl\s+page/part\s*:?.*?page\s*(\d+)\b",
            r"^\s*page\s*[:#-]?\s*(\d+)\s*$",
            r"^\s*第\s*(\d+)\s*页\s*$",
        ]
        for pattern in patterns:
            m = re.search(pattern, block, flags=re.IGNORECASE)
            if m:
                try:
                    return max(1, int(m.group(1)))
                except Exception:
                    return None
        return None

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------

    def _document_id(self, path: Path) -> str:
        digest = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:8]
        return f"doc_{self._safe_name(path.stem)}_{digest}"

    def _safe_name(self, text: str) -> str:
        value = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(text or "")).strip("_")
        return value or "document"
