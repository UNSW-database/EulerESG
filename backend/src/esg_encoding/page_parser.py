"""Lightweight, page-level PDF profiling and native text extraction.

This module deliberately keeps PyMuPDF optional.  Importing it never imports a
PDF backend; :func:`analyze_pdf_pages` loads PyMuPDF lazily and returns a
structured ``unavailable`` result when it is not installed.  The resulting
profiles are intended to drive an adaptive document pipeline:

* simple born-digital pages can use their exact PDF text and coordinates;
* image-only pages can be sent to OCR; and
* mixed or structurally complex pages can combine native text with layout OCR.

The classifier is intentionally conservative.  A false ``hybrid`` decision is
slower, while a false ``native`` decision can silently lose table or chart
structure.  All coordinates are retained in PDF points and also exposed as
normalized ``[x0, y0, x1, y1]`` boxes for downstream matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Optional, Sequence


_SPACE_RE = re.compile(r"[\t\f\v ]+")
_HTML_IMAGE_RE = re.compile(r"<\s*img\b[^>]*?/?>", re.IGNORECASE)
_HTML_DIV_TAG_RE = re.compile(r"<\s*/?\s*div\b[^>]*>", re.IGNORECASE)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_PAGE_NUMBER_RE = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?$", re.IGNORECASE)
_NUMBER_TOKEN_RE = re.compile(r"(?<![\w.])[-+]?(?:\d[\d,]*)(?:\.\d+)?%?(?!\w)")


@dataclass(frozen=True)
class PageParserConfig:
    """Thresholds for the inexpensive page classifier.

    Ratios are relative to the visible page area.  They are deliberately
    exposed so the application can tune them against a real PDF benchmark
    without changing parser code.
    """

    min_digital_chars: int = 24
    min_any_text_chars: int = 8
    min_native_quality: float = 0.55
    scanned_image_ratio: float = 0.30
    hybrid_image_ratio: float = 0.12
    large_image_ratio: float = 0.45
    dense_block_count: int = 24
    table_drawing_count: int = 6
    chart_drawing_count: int = 80
    aligned_table_row_count: int = 3
    header_footer_ratio: float = 0.075
    min_column_chars: int = 18
    skew_degrees_threshold: float = 1.5
    low_contrast_threshold: float = 0.18
    raster_quality_dpi: int = 36
    heading_size_ratio: float = 1.25


@dataclass
class PageProfile:
    """JSON-friendly analysis of one 1-based PDF page."""

    page_number: int
    route: str
    content_kind: str
    page_width: float
    page_height: float
    rotation: int
    native_blocks: list[dict[str, Any]] = field(default_factory=list)
    native_paragraphs: list[dict[str, Any]] = field(default_factory=list)
    native_markdown: str = ""
    native_char_count: int = 0
    native_word_count: int = 0
    native_quality: float = 0.0
    text_area_ratio: float = 0.0
    image_area_ratio: float = 0.0
    image_count: int = 0
    image_regions: list[dict[str, Any]] = field(default_factory=list)
    column_count: int = 1
    drawing_count: int = 0
    complexity_hints: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def classification(self) -> str:
        """Backward-friendly alias for ``content_kind``."""

        return self.content_kind

    @property
    def page_size(self) -> dict[str, float]:
        return {"width": self.page_width, "height": self.page_height}

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "route": self.route,
            "content_kind": self.content_kind,
            "classification": self.content_kind,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "page_size": self.page_size,
            "rotation": self.rotation,
            "native_blocks": self.native_blocks,
            "native_paragraphs": self.native_paragraphs,
            "native_markdown": self.native_markdown,
            "native_char_count": self.native_char_count,
            "native_word_count": self.native_word_count,
            "native_quality": self.native_quality,
            "text_area_ratio": self.text_area_ratio,
            "image_area_ratio": self.image_area_ratio,
            "image_count": self.image_count,
            "image_regions": self.image_regions,
            "column_count": self.column_count,
            "drawing_count": self.drawing_count,
            "complexity_hints": self.complexity_hints,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass
class PdfAnalysis:
    """Document result returned even when PDF support is unavailable."""

    available: bool
    total_pages: int = 0
    pages: list[PageProfile] = field(default_factory=list)
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    backend: Optional[str] = None
    backend_version: Optional[str] = None
    source: Optional[str] = None

    @property
    def route_counts(self) -> dict[str, int]:
        counts = {"native": 0, "ocr": 0, "hybrid": 0}
        for page in self.pages:
            counts[page.route] = counts.get(page.route, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "error": self.error,
            "total_pages": self.total_pages,
            "pages": [page.to_dict() for page in self.pages],
            "route_counts": self.route_counts,
            "warnings": self.warnings,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "source": self.source,
        }


def _round(value: float) -> float:
    return round(float(value), 6)


def _rect_values(value: Any) -> Optional[tuple[float, float, float, float]]:
    if value is None:
        return None
    try:
        if all(hasattr(value, attr) for attr in ("x0", "y0", "x1", "y1")):
            raw = (value.x0, value.y0, value.x1, value.y1)
        else:
            raw = tuple(value)[:4]
        if len(raw) < 4:
            return None
        x0, y0, x1, y1 = (float(item) for item in raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(item) for item in (x0, y0, x1, y1)):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


def _clip_rect(
    value: Any,
    page_bounds: tuple[float, float, float, float],
) -> Optional[tuple[float, float, float, float]]:
    rect = _rect_values(value)
    if rect is None:
        return None
    px0, py0, px1, py1 = page_bounds
    x0 = min(max(rect[0], px0), px1)
    y0 = min(max(rect[1], py0), py1)
    x1 = min(max(rect[2], px0), px1)
    y1 = min(max(rect[3], py0), py1)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _normalise_rect(
    rect: tuple[float, float, float, float],
    page_bounds: tuple[float, float, float, float],
) -> list[float]:
    px0, py0, px1, py1 = page_bounds
    width = max(px1 - px0, 1e-9)
    height = max(py1 - py0, 1e-9)
    return [
        _round((rect[0] - px0) / width),
        _round((rect[1] - py0) / height),
        _round((rect[2] - px0) / width),
        _round((rect[3] - py0) / height),
    ]


def _union_area(rectangles: Iterable[tuple[float, float, float, float]]) -> float:
    """Return the exact union area of axis-aligned rectangles."""

    rects = [rect for rect in rectangles if rect[2] > rect[0] and rect[3] > rect[1]]
    x_values = sorted({value for rect in rects for value in (rect[0], rect[2])})
    area = 0.0
    for left, right in zip(x_values, x_values[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (rect[1], rect[3])
            for rect in rects
            if rect[0] < right and rect[2] > left
        )
        if not intervals:
            continue
        covered = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                covered += end - start
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        covered += end - start
        area += (right - left) * covered
    return area


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "").replace("\u200b", "")
    text = _HTML_IMAGE_RE.sub(" ", text)
    text = _MARKDOWN_IMAGE_RE.sub(" ", text)
    text = _HTML_DIV_TAG_RE.sub(" ", text)
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _SPACE_RE.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _text_from_block(block: Mapping[str, Any]) -> str:
    direct = block.get("text")
    if direct:
        return _clean_text(direct)
    lines: list[str] = []
    for line in block.get("lines") or []:
        if not isinstance(line, Mapping):
            continue
        spans = line.get("spans") or []
        line_text = "".join(
            str(span.get("text") or "")
            for span in spans
            if isinstance(span, Mapping)
        )
        if line_text:
            lines.append(line_text)
    return _clean_text("\n".join(lines))


def _meaningful_character_count(text: str) -> int:
    return sum(character.isalnum() for character in text)


def _word_count(text: str) -> int:
    # Each CJK character carries more signal than a whitespace-delimited token.
    cjk_count = sum("\u3400" <= char <= "\u9fff" for char in text)
    latin_tokens = re.findall(r"[A-Za-z0-9]+(?:[._%/-][A-Za-z0-9]+)*", text)
    return cjk_count + len(latin_tokens)


def _native_quality(text: str) -> float:
    non_space = [char for char in text if not char.isspace()]
    if not non_space:
        return 0.0
    useful = sum(
        char.isalnum() or char in "%$€£¥°±.,:;!?()[]{}&/+-_'\""
        for char in non_space
    )
    replacement_penalty = text.count("\ufffd") + text.count("�")
    return max(0.0, min(1.0, (useful - replacement_penalty * 2) / len(non_space)))


def _page_geometry(page: Any) -> tuple[tuple[float, float, float, float], float, float]:
    # PyMuPDF's ``page.rect`` reflects page rotation, while extracted text and
    # image coordinates use the unrotated page coordinate system.  Prefer the
    # crop/media boxes so normalization remains valid on 90/270-degree pages.
    rect = _rect_values(getattr(page, "cropbox", None))
    if rect is None:
        rect = _rect_values(getattr(page, "mediabox", None))
    if rect is None:
        rect = _rect_values(getattr(page, "rect", None))
    if rect is None:
        rect = (0.0, 0.0, 612.0, 792.0)
    width = max(rect[2] - rect[0], 1.0)
    height = max(rect[3] - rect[1], 1.0)
    return rect, width, height


def _get_text_blocks(page: Any, warnings: Optional[list[str]] = None) -> Sequence[Any]:
    getter = getattr(page, "get_text", None)
    if not callable(getter):
        if warnings is not None:
            warnings.append("native_text_api_unavailable")
        return []
    try:
        return getter("blocks", sort=True) or []
    except TypeError:
        try:
            return getter("blocks") or []
        except Exception:
            if warnings is not None:
                warnings.append("native_text_extraction_failed")
            return []
    except Exception:
        if warnings is not None:
            warnings.append("native_text_extraction_failed")
        return []


def _extract_native_blocks(
    page: Any,
    page_number: int,
    page_bounds: tuple[float, float, float, float],
    config: PageParserConfig,
    warnings: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    raw_blocks = _get_text_blocks(page, warnings)
    blocks: list[dict[str, Any]] = []
    page_height = page_bounds[3] - page_bounds[1]
    for source_order, raw in enumerate(raw_blocks):
        block_number = source_order
        block_type: Any = 0
        if isinstance(raw, Mapping):
            block_type = raw.get("type", raw.get("block_type", 0))
            block_number = raw.get("number", raw.get("block_no", source_order))
            raw_bbox = raw.get("bbox") or raw.get("box")
            text = _text_from_block(raw)
        else:
            try:
                values = tuple(raw)
            except TypeError:
                continue
            if len(values) < 5:
                continue
            raw_bbox = values[:4]
            text = _clean_text(values[4])
            if len(values) >= 6:
                block_number = values[5]
            if len(values) >= 7:
                block_type = values[6]

        if block_type not in (0, "0", "text", "Text") or not text:
            continue
        bbox = _clip_rect(raw_bbox, page_bounds)
        if bbox is None:
            continue
        char_count = _meaningful_character_count(text)
        if char_count == 0:
            continue
        normalized = _normalise_rect(bbox, page_bounds)
        margin = max(0.0, min(config.header_footer_ratio, 0.25))
        is_margin = normalized[3] <= margin or normalized[1] >= 1.0 - margin
        is_page_number = bool(_PAGE_NUMBER_RE.fullmatch(text.replace("\n", " ").strip()))
        blocks.append(
            {
                "block_id": f"p{page_number}:b{block_number}",
                "page_number": page_number,
                "block_type": "text",
                "source_order": source_order,
                "reading_order": 0,
                "text": text,
                "bbox": [_round(value) for value in bbox],
                "normalized_bbox": normalized,
                "char_count": char_count,
                "word_count": _word_count(text),
                "line_count": max(1, len(text.splitlines())),
                "text_quality": _round(_native_quality(text)),
                "column_index": 0,
                "is_header_footer": is_margin,
                "usable": not (is_margin and is_page_number),
            }
        )

    # PyMuPDF's sorted blocks are normally top-to-bottom.  Explicit sorting is
    # still needed for injected/test backends and makes reading_order stable.
    blocks.sort(
        key=lambda item: (
            item["normalized_bbox"][1],
            item["normalized_bbox"][0],
            item["source_order"],
        )
    )
    for reading_order, block in enumerate(blocks):
        block["reading_order"] = reading_order
    return blocks


def _rect_iou(first: Sequence[float], second: Sequence[float]) -> float:
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if intersection <= 0.0:
        return 0.0
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _native_style_records(
    page: Any,
    page_bounds: tuple[float, float, float, float],
) -> tuple[list[dict[str, Any]], float]:
    """Read span styles and line directions without making them mandatory."""
    getter = getattr(page, "get_text", None)
    if not callable(getter):
        return [], 0.0
    try:
        payload = getter("dict", sort=True) or {}
    except TypeError:
        try:
            payload = getter("dict") or {}
        except Exception:
            return [], 0.0
    except Exception:
        return [], 0.0
    if not isinstance(payload, Mapping):
        return [], 0.0

    records: list[dict[str, Any]] = []
    skew_angles: list[float] = []
    for block in payload.get("blocks", []) or []:
        if not isinstance(block, Mapping) or block.get("type", 0) not in (0, "0", "text"):
            continue
        spans: list[Mapping[str, Any]] = []
        for line in block.get("lines", []) or []:
            if not isinstance(line, Mapping):
                continue
            direction = line.get("dir")
            if isinstance(direction, (list, tuple)) and len(direction) >= 2:
                try:
                    angle = math.degrees(math.atan2(float(direction[1]), float(direction[0])))
                    # Measure deviation from the nearest horizontal/vertical axis.
                    deviation = ((angle + 45.0) % 90.0) - 45.0
                    if abs(deviation) <= 20.0:
                        skew_angles.append(deviation)
                except (TypeError, ValueError, OverflowError):
                    pass
            spans.extend(span for span in (line.get("spans", []) or []) if isinstance(span, Mapping))
        bbox = _clip_rect(block.get("bbox"), page_bounds)
        if bbox is None or not spans:
            continue
        sizes = []
        bold = False
        for span in spans:
            try:
                size = float(span.get("size") or 0.0)
            except (TypeError, ValueError, OverflowError):
                size = 0.0
            if size > 0.0:
                sizes.append(size)
            font = str(span.get("font") or "").casefold()
            flags = _safe_int(span.get("flags")) or 0
            bold = bold or "bold" in font or bool(flags & 16)
        if sizes:
            records.append(
                {
                    "bbox": bbox,
                    "font_size": max(sizes),
                    "bold": bold,
                }
            )
    skew = 0.0
    if skew_angles:
        ordered = sorted(skew_angles)
        skew = ordered[len(ordered) // 2]
    return records, _round(skew)


def _apply_native_block_styles(
    blocks: list[dict[str, Any]],
    styles: Sequence[Mapping[str, Any]],
    config: PageParserConfig,
) -> None:
    if not blocks or not styles:
        return
    base_sizes = sorted(float(style.get("font_size") or 0.0) for style in styles if float(style.get("font_size") or 0.0) > 0.0)
    if not base_sizes:
        return
    median_size = base_sizes[len(base_sizes) // 2]
    for block in blocks:
        best = max(styles, key=lambda style: _rect_iou(block["bbox"], style.get("bbox") or (0, 0, 0, 0)))
        overlap = _rect_iou(block["bbox"], best.get("bbox") or (0, 0, 0, 0))
        if overlap <= 0.0:
            continue
        size = float(best.get("font_size") or 0.0)
        bold = bool(best.get("bold"))
        heading = (
            len(str(block.get("text") or "")) <= 180
            and size >= median_size * max(1.05, config.heading_size_ratio)
            and (bold or size >= median_size * 1.45)
        )
        block["font_size"] = _round(size)
        block["font_bold"] = bold
        if heading:
            block["block_type"] = "heading"


def _raster_contrast(page: Any, dpi: int) -> Optional[float]:
    """Return a cheap 0..1 luminance range for likely scanned pages."""
    renderer = getattr(page, "get_pixmap", None)
    if not callable(renderer):
        return None
    try:
        pixmap = renderer(dpi=max(18, min(72, int(dpi))), alpha=False)
        samples = bytes(getattr(pixmap, "samples", b"") or b"")
        channels = max(1, int(getattr(pixmap, "n", 1) or 1))
    except Exception:
        return None
    if not samples:
        return None
    pixels = len(samples) // channels
    stride = max(1, pixels // 4096)
    luminance: list[int] = []
    for pixel in range(0, pixels, stride):
        offset = pixel * channels
        sample = samples[offset:offset + min(3, channels)]
        if not sample:
            continue
        luminance.append(sum(sample) // len(sample))
    if len(luminance) < 16:
        return None
    luminance.sort()
    low = luminance[int((len(luminance) - 1) * 0.10)]
    high = luminance[int((len(luminance) - 1) * 0.90)]
    return _round(max(0.0, min(1.0, (high - low) / 255.0)))


def _fallback_image_info(page: Any) -> list[dict[str, Any]]:
    getter = getattr(page, "get_images", None)
    rect_getter = getattr(page, "get_image_rects", None)
    if not callable(getter) or not callable(rect_getter):
        return []
    try:
        images = getter(full=True) or []
    except TypeError:
        try:
            images = getter() or []
        except Exception:
            return []
    except Exception:
        return []

    records: list[dict[str, Any]] = []
    for image in images:
        try:
            xref = int(image[0])
        except (TypeError, ValueError, IndexError):
            continue
        try:
            rects = rect_getter(xref) or []
        except Exception:
            rects = []
        for rect in rects:
            records.append(
                {
                    "xref": xref,
                    "bbox": rect,
                    "width": image[2] if len(image) > 2 else None,
                    "height": image[3] if len(image) > 3 else None,
                }
            )
    return records


def _extract_image_regions(
    page: Any,
    page_bounds: tuple[float, float, float, float],
    warnings: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    getter = getattr(page, "get_image_info", None)
    raw_images: Sequence[Any] = []
    if callable(getter):
        try:
            raw_images = getter(hashes=False, xrefs=True) or []
        except TypeError:
            try:
                raw_images = getter() or []
            except Exception:
                if warnings is not None:
                    warnings.append("native_image_analysis_failed")
                raw_images = []
        except Exception:
            if warnings is not None:
                warnings.append("native_image_analysis_failed")
            raw_images = []
    if not raw_images:
        raw_images = _fallback_image_info(page)

    regions: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    page_area = max(
        (page_bounds[2] - page_bounds[0]) * (page_bounds[3] - page_bounds[1]),
        1.0,
    )
    for source_order, raw in enumerate(raw_images):
        if isinstance(raw, Mapping):
            raw_bbox = raw.get("bbox") or raw.get("rect")
            xref = raw.get("xref")
            pixel_width = raw.get("width")
            pixel_height = raw.get("height")
        else:
            continue
        bbox = _clip_rect(raw_bbox, page_bounds)
        if bbox is None:
            continue
        key = tuple(_round(value) for value in bbox) + (xref,)
        if key in seen:
            continue
        seen.add(key)
        area_ratio = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / page_area
        regions.append(
            {
                "image_id": f"image-{source_order}",
                "xref": xref,
                "bbox": [_round(value) for value in bbox],
                "normalized_bbox": _normalise_rect(bbox, page_bounds),
                "pixel_width": _safe_int(pixel_width),
                "pixel_height": _safe_int(pixel_height),
                "area_ratio": _round(area_ratio),
            }
        )
    return regions


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _count_drawings(page: Any) -> int:
    for method_name in ("get_cdrawings", "get_drawings"):
        getter = getattr(page, method_name, None)
        if not callable(getter):
            continue
        try:
            return len(getter() or [])
        except Exception:
            continue
    return 0


def _vertical_overlap(left: Sequence[float], right: Sequence[float]) -> float:
    overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    smaller = min(left[3] - left[1], right[3] - right[1])
    return overlap / smaller if smaller > 0 else 0.0


def _estimate_columns(
    blocks: list[dict[str, Any]],
    config: PageParserConfig,
) -> int:
    body = [
        block
        for block in blocks
        if not block["is_header_footer"]
        and block["char_count"] >= 3
        and block["normalized_bbox"][2] - block["normalized_bbox"][0] <= 0.72
    ]
    left = [block for block in body if block["normalized_bbox"][2] <= 0.62]
    right = [block for block in body if block["normalized_bbox"][0] >= 0.38]
    left_chars = sum(block["char_count"] for block in left)
    right_chars = sum(block["char_count"] for block in right)
    if left_chars < config.min_column_chars or right_chars < config.min_column_chars:
        return 1
    has_parallel_content = any(
        _vertical_overlap(l_block["normalized_bbox"], r_block["normalized_bbox"]) >= 0.15
        for l_block in left
        for r_block in right
    )
    return 2 if has_parallel_content else 1


def _assign_columns(blocks: list[dict[str, Any]], column_count: int) -> None:
    if column_count < 2:
        return
    for block in blocks:
        x0, _, x1, _ = block["normalized_bbox"]
        if block["is_header_footer"]:
            block["column_index"] = 0
        elif x1 <= 0.62:
            block["column_index"] = 1
        elif x0 >= 0.38:
            block["column_index"] = 2
        else:
            block["column_index"] = 0


def _assign_reading_order(blocks: list[dict[str, Any]], column_count: int) -> None:
    """Assign column-aware order while respecting full-width section breaks."""

    if column_count < 2:
        ordered = sorted(
            blocks,
            key=lambda block: (
                block["normalized_bbox"][1],
                block["normalized_bbox"][0],
                block["source_order"],
            ),
        )
    else:
        spanning = sorted(
            (block for block in blocks if block["column_index"] == 0),
            key=lambda block: (
                block["normalized_bbox"][1],
                block["normalized_bbox"][0],
            ),
        )
        column_blocks = [block for block in blocks if block["column_index"] in (1, 2)]
        emitted: set[int] = set()
        ordered = []

        def append_column_band(before_y: float) -> None:
            band = [
                block
                for block in column_blocks
                if block["source_order"] not in emitted
                and block["normalized_bbox"][1] < before_y
            ]
            band.sort(
                key=lambda block: (
                    block["column_index"],
                    block["normalized_bbox"][1],
                    block["normalized_bbox"][0],
                    block["source_order"],
                )
            )
            ordered.extend(band)
            emitted.update(block["source_order"] for block in band)

        for span in spanning:
            append_column_band(span["normalized_bbox"][1])
            ordered.append(span)
        append_column_band(float("inf"))

    for reading_order, block in enumerate(ordered):
        block["reading_order"] = reading_order


def _aligned_table_rows(blocks: list[dict[str, Any]]) -> int:
    candidates = [block for block in blocks if not block["is_header_footer"]]
    rows = 0
    for index, block in enumerate(candidates):
        center_y = (block["normalized_bbox"][1] + block["normalized_bbox"][3]) / 2
        peers = [
            other
            for other in candidates[index + 1 :]
            if abs(
                center_y
                - (other["normalized_bbox"][1] + other["normalized_bbox"][3]) / 2
            )
            <= 0.012
            and (
                other["normalized_bbox"][0] >= block["normalized_bbox"][2]
                or block["normalized_bbox"][0] >= other["normalized_bbox"][2]
            )
        ]
        if len(peers) >= 2:
            rows += 1
    return rows


def _looks_like_borderless_table(blocks: list[dict[str, Any]]) -> bool:
    """Detect numeric row patterns when a table has no vector borders."""

    numeric_rows = 0
    for block in blocks:
        for line in str(block.get("text") or "").splitlines():
            if len(line) <= 180 and len(_NUMBER_TOKEN_RE.findall(line)) >= 2:
                numeric_rows += 1
                if numeric_rows >= 3:
                    return True
    return False


def render_native_markdown(
    native_blocks: Sequence[Mapping[str, Any]],
    *,
    include_noise: bool = False,
) -> str:
    """Render ordered native blocks without reintroducing image/HTML noise."""

    ordered = sorted(
        native_blocks,
        key=lambda block: int(block.get("reading_order", 0) or 0),
    )
    paragraphs: list[str] = []
    for block in ordered:
        if not include_noise and not bool(block.get("usable", True)):
            continue
        text = _clean_text(block.get("text"))
        if text:
            if str(block.get("block_type") or "").casefold() in {"heading", "title", "section_header"}:
                paragraphs.append(f"## {text}")
            else:
                paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _native_paragraphs(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "paragraph_id": f"native-{block['block_id']}",
            "page_number": block["page_number"],
            "block_ids": [block["block_id"]],
            "reading_order": block["reading_order"],
            "text": block["text"],
            "bbox": block["bbox"],
            "normalized_bbox": block["normalized_bbox"],
            "column_index": block["column_index"],
            "is_header_footer": block["is_header_footer"],
            "usable": block["usable"],
        }
        for block in blocks
    ]


def analyze_pdf_page(
    page: Any,
    page_number: int,
    *,
    config: Optional[PageParserConfig] = None,
) -> PageProfile:
    """Profile an already-open PDF page.

    ``page_number`` is always 1-based.  Supplying a page object keeps this
    function usable in tests and by callers that already own a PDF document.
    Individual extraction failures become warnings and conservatively select
    OCR rather than aborting the whole document.
    """

    if page_number < 1:
        raise ValueError("page_number must be 1-based and greater than zero")
    parser_config = config or PageParserConfig()
    page_bounds, page_width, page_height = _page_geometry(page)
    warnings: list[str] = []
    try:
        rotation = int(getattr(page, "rotation", 0) or 0) % 360
    except (TypeError, ValueError, OverflowError):
        rotation = 0
        warnings.append("invalid_page_rotation")

    native_blocks = _extract_native_blocks(
        page,
        page_number,
        page_bounds,
        parser_config,
        warnings,
    )
    style_records, skew_angle = _native_style_records(page, page_bounds)
    _apply_native_block_styles(native_blocks, style_records, parser_config)
    image_regions = _extract_image_regions(page, page_bounds, warnings)
    drawing_count = _count_drawings(page)
    column_count = _estimate_columns(native_blocks, parser_config)
    _assign_columns(native_blocks, column_count)
    _assign_reading_order(native_blocks, column_count)

    all_text = "\n".join(block["text"] for block in native_blocks)
    char_count = _meaningful_character_count(all_text)
    word_count = _word_count(all_text)
    quality = _native_quality(all_text)
    page_area = max(page_width * page_height, 1.0)
    text_rects = [tuple(block["bbox"]) for block in native_blocks]
    image_rects = [tuple(region["bbox"]) for region in image_regions]
    text_area_ratio = min(1.0, _union_area(text_rects) / page_area)
    image_area_ratio = min(1.0, _union_area(image_rects) / page_area)

    native_failed = any(
        warning in {"native_text_extraction_failed", "native_text_api_unavailable"}
        for warning in warnings
    )
    blank_page = (
        char_count == 0
        and image_area_ratio < 0.01
        and drawing_count == 0
        and not native_failed
    )
    has_native_text = (
        char_count >= parser_config.min_digital_chars
        and quality >= parser_config.min_native_quality
    )
    sparse_native_text = 0 < char_count < parser_config.min_digital_chars
    poor_native_text = char_count > 0 and quality < parser_config.min_native_quality

    if blank_page:
        content_kind = "blank"
    elif has_native_text and image_area_ratio < parser_config.hybrid_image_ratio:
        content_kind = "digital"
    elif (
        char_count < parser_config.min_any_text_chars
        and (image_area_ratio >= parser_config.scanned_image_ratio or not has_native_text)
    ):
        content_kind = "scanned"
    else:
        content_kind = "hybrid"

    compact_numeric_block = any(
        len(str(block.get("text") or "")) <= 300
        and len(_NUMBER_TOKEN_RE.findall(str(block.get("text") or ""))) >= 3
        for block in native_blocks
    )
    possible_table = (
        _aligned_table_rows(native_blocks) >= parser_config.aligned_table_row_count
        or _looks_like_borderless_table(native_blocks)
        or (
            drawing_count >= parser_config.table_drawing_count
            and compact_numeric_block
        )
    )
    visual_heavy = image_area_ratio >= parser_config.hybrid_image_ratio
    image_dominant = image_area_ratio >= parser_config.large_image_ratio
    raster_contrast = (
        _raster_contrast(page, parser_config.raster_quality_dpi)
        if not blank_page and (not has_native_text or image_dominant)
        else None
    )
    low_contrast = raster_contrast is not None and raster_contrast < parser_config.low_contrast_threshold
    skewed = abs(skew_angle) >= parser_config.skew_degrees_threshold
    multi_column = column_count > 1
    rotated = rotation != 0
    dense_layout = len(native_blocks) >= parser_config.dense_block_count
    possible_chart = (
        drawing_count >= parser_config.chart_drawing_count
        or any(
            parser_config.hybrid_image_ratio <= region["area_ratio"] < 0.90
            for region in image_regions
        )
    ) and char_count >= parser_config.min_any_text_chars
    # Native text already carries coordinates and the reader below handles
    # columns/dense pages. OCR is reserved for structures native extraction
    # cannot faithfully represent: tables, charts and geometric correction.
    complex_layout = any((possible_table, possible_chart, rotated, skewed))

    if content_kind == "blank":
        # A confirmed blank page is a successful parse result, not a failed OCR
        # page.  Preserve its page marker without submitting it to a worker that
        # intentionally rejects empty Markdown.
        route = "native"
    elif content_kind == "scanned":
        route = "ocr"
    elif content_kind == "hybrid" or complex_layout or visual_heavy:
        route = "hybrid"
    else:
        route = "native"

    hints = {
        "blank_page": blank_page,
        "sparse_native_text": sparse_native_text,
        "poor_native_text": poor_native_text,
        "image_dominant": image_dominant,
        "visual_heavy": visual_heavy,
        "multi_column": multi_column,
        "possible_table": possible_table,
        "possible_chart": possible_chart,
        "dense_layout": dense_layout,
        "rotated": rotated,
        "skewed": skewed,
        "skew_angle": skew_angle,
        "low_contrast": low_contrast,
        "raster_contrast": raster_contrast,
        "complex_layout": complex_layout,
        "needs_orientation": rotated,
        "needs_unwarping": skewed,
        "needs_high_resolution_ocr": content_kind == "scanned" and (image_dominant or low_contrast),
    }
    paragraphs = _native_paragraphs(native_blocks)
    return PageProfile(
        page_number=page_number,
        route=route,
        content_kind=content_kind,
        page_width=_round(page_width),
        page_height=_round(page_height),
        rotation=rotation,
        native_blocks=native_blocks,
        native_paragraphs=paragraphs,
        native_markdown=render_native_markdown(native_blocks),
        native_char_count=char_count,
        native_word_count=word_count,
        native_quality=_round(quality),
        text_area_ratio=_round(text_area_ratio),
        image_area_ratio=_round(image_area_ratio),
        image_count=len(image_regions),
        image_regions=image_regions,
        column_count=column_count,
        drawing_count=drawing_count,
        complexity_hints=hints,
        warnings=warnings,
    )


def _load_pdf_backend() -> tuple[Optional[Any], Optional[str]]:
    errors: list[str] = []
    for module_name in ("pymupdf", "fitz"):
        try:
            module = importlib.import_module(module_name)
        except (ImportError, ModuleNotFoundError) as exc:
            errors.append(f"{module_name}: {exc}")
            continue
        if callable(getattr(module, "open", None)):
            return module, None
        errors.append(f"{module_name}: module has no open()")
    return None, "; ".join(errors) or "PyMuPDF is unavailable"


def _backend_version(module: Any) -> Optional[str]:
    for attribute in ("VersionBind", "__version__", "version"):
        value = getattr(module, attribute, None)
        if value:
            if isinstance(value, (tuple, list)):
                return ".".join(str(item) for item in value)
            return str(value)
    return None


def _source_label(source: Any) -> str:
    if isinstance(source, (str, Path)):
        return str(source)
    if isinstance(source, (bytes, bytearray, memoryview)):
        return "<bytes>"
    return f"<{type(source).__name__}>"


def _open_document(module: Any, source: Any) -> Any:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return module.open(stream=bytes(source), filetype="pdf")
    return module.open(str(source))


def _document_page_count(document: Any) -> int:
    raw_count = getattr(document, "page_count", None)
    if raw_count is None:
        raw_count = len(document)
    return max(0, int(raw_count))


def _load_page(document: Any, page_index: int) -> Any:
    loader = getattr(document, "load_page", None)
    if callable(loader):
        return loader(page_index)
    return document[page_index]


def _failed_page(page_number: int, error: Exception) -> PageProfile:
    message = f"{type(error).__name__}: {error}"
    return PageProfile(
        page_number=page_number,
        route="ocr",
        content_kind="scanned",
        page_width=0.0,
        page_height=0.0,
        rotation=0,
        complexity_hints={
            "blank_page": False,
            "sparse_native_text": False,
            "poor_native_text": True,
            "image_dominant": False,
            "visual_heavy": False,
            "multi_column": False,
            "possible_table": False,
            "possible_chart": False,
            "dense_layout": False,
            "rotated": False,
            "skewed": False,
            "skew_angle": 0.0,
            "low_contrast": False,
            "raster_contrast": None,
            "complex_layout": False,
            "needs_orientation": False,
            "needs_unwarping": False,
            "needs_high_resolution_ocr": False,
        },
        warnings=["native_page_analysis_failed"],
        error=message,
    )


def analyze_pdf_pages(
    source: str | Path | bytes | bytearray | memoryview,
    *,
    config: Optional[PageParserConfig] = None,
    password: Optional[str] = None,
    fitz_module: Optional[Any] = None,
) -> PdfAnalysis:
    """Analyze every page without making PyMuPDF a hard dependency.

    ``fitz_module`` is an explicit injection point for alternative adapters and
    deterministic tests.  A missing backend, unreadable/encrypted PDF, or a
    page-level exception is represented in the return value; callers do not
    need an import-time ``try/except`` just to retain their OCR fallback.
    """

    source_label = _source_label(source)
    module = fitz_module
    load_error: Optional[str] = None
    if module is None:
        module, load_error = _load_pdf_backend()
    if module is None:
        return PdfAnalysis(
            available=False,
            error=f"pdf_backend_unavailable: {load_error}",
            warnings=["native_pdf_analysis_skipped"],
            source=source_label,
        )

    backend_name = getattr(module, "__name__", type(module).__name__)
    try:
        document = _open_document(module, source)
    except Exception as exc:
        return PdfAnalysis(
            available=True,
            error=f"pdf_open_failed: {type(exc).__name__}: {exc}",
            backend=backend_name,
            backend_version=_backend_version(module),
            source=source_label,
        )

    try:
        needs_password = bool(
            getattr(document, "needs_pass", False)
            or getattr(document, "is_encrypted", False)
        )
        if needs_password:
            authenticator = getattr(document, "authenticate", None)
            authenticated = bool(password and callable(authenticator) and authenticator(password))
            if not authenticated:
                return PdfAnalysis(
                    available=True,
                    error="pdf_password_required",
                    backend=backend_name,
                    backend_version=_backend_version(module),
                    source=source_label,
                )

        total_pages = _document_page_count(document)
        profiles: list[PageProfile] = []
        for page_index in range(total_pages):
            try:
                page = _load_page(document, page_index)
                profile = analyze_pdf_page(
                    page,
                    page_index + 1,
                    config=config,
                )
            except Exception as exc:  # one corrupt page must not lose the report
                profile = _failed_page(page_index + 1, exc)
            profiles.append(profile)
        failed_count = sum(profile.error is not None for profile in profiles)
        warnings = []
        if failed_count:
            warnings.append(f"native_page_analysis_failed:{failed_count}")
        return PdfAnalysis(
            available=True,
            total_pages=total_pages,
            pages=profiles,
            warnings=warnings,
            backend=backend_name,
            backend_version=_backend_version(module),
            source=source_label,
        )
    except Exception as exc:
        return PdfAnalysis(
            available=True,
            error=f"pdf_analysis_failed: {type(exc).__name__}: {exc}",
            backend=backend_name,
            backend_version=_backend_version(module),
            source=source_label,
        )
    finally:
        closer = getattr(document, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass


__all__ = [
    "PageParserConfig",
    "PageProfile",
    "PdfAnalysis",
    "analyze_pdf_page",
    "analyze_pdf_pages",
    "render_native_markdown",
]
