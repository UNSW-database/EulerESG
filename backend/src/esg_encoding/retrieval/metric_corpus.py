"""Structure-preserving retrieval corpus for report metric evidence.

The canonical :class:`~esg_encoding.models.TextSegment` objects remain the
source of truth.  This module builds a second, retrieval-only representation:

* evidence blocks preserve complete paragraphs, lists, tables, and visuals;
* retrieval views add section/table context without changing source evidence;
* long narrative blocks are split only at sentence boundaries; and
* table derivatives collapse into one table block with complete-row views.

Nothing in this module embeds, persists, or retrieves the corpus.  Keeping the
builder independent lets callers introduce the new corpus without changing the
legacy segment/embedding contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

from pydantic import BaseModel, Field

from ..models import DocumentContent, ReportContent, TextSegment


METRIC_CORPUS_SCHEMA_VERSION = "1.0"
METRIC_CHUNKER_VERSION = "structure-preserving-v1"

_TABLE_TYPES = {"table", "table_row", "table_cell"}
_LIST_TYPES = {
    "list",
    "list_group",
    "list_item",
    "bullet",
    "bullet_list",
    "ordered_list",
    "unordered_list",
}
_VISUAL_TYPES = {"chart", "figure", "image_text", "chart_data"}
_LIST_MARKER_RE = re.compile(
    r"^\s*(?:[-*+\u2022\u25cf\u25aa\u25e6\u2023]|"
    r"(?:\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+)[.)\u3001]|"
    r"[\u3400-\u9fff]{1,4}[.)\u3001])\s+"
)
_SENTENCE_END_RE = re.compile(
    r"(?:[.!?\u3002\uff01\uff1f;\uff1b]+[\"'\u2019\u201d)\]\uff09]*"
    r"(?:\s+|(?=[\u3400-\u9fff])|$)|\n+)"
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256_text(encoded)


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{_stable_digest(parts)[:24]}"


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


def _model_construct(model_type: Any, **data: Any) -> Any:
    if hasattr(model_type, "model_construct"):
        return model_type.model_construct(**data)
    return model_type.construct(**data)


def _model_copy(model: BaseModel, *, update: Dict[str, Any]) -> Any:
    if hasattr(model, "model_copy"):
        return model.model_copy(update=update)
    return model.copy(update=update)


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _segment_data(segment: TextSegment) -> Dict[str, Any]:
    value = getattr(segment, "structured_data", None)
    return dict(value) if isinstance(value, dict) else {}


def _source_report_id(segment: TextSegment) -> Optional[str]:
    value = _segment_data(segment).get("source_report_id")
    text = str(value or "").strip()
    return text or None


def _source_table_id(segment: TextSegment) -> Optional[str]:
    data = _segment_data(segment)
    value = (
        getattr(segment, "source_table_id", None)
        or data.get("source_table_id")
        or data.get("table_id")
    )
    text = str(value or "").strip()
    return text or None


def _section_path(segment: TextSegment) -> List[str]:
    value = _segment_data(segment).get("section_path")
    if isinstance(value, (list, tuple)):
        return _dedupe_strings(value)
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _page_number(segment: TextSegment) -> int:
    try:
        return max(1, int(getattr(segment, "page_number", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _segment_type(segment: TextSegment) -> str:
    return str(getattr(segment, "segment_type", "text") or "text").strip().lower()


def _is_list_content(content: str) -> bool:
    lines = [line for line in str(content or "").splitlines() if line.strip()]
    return bool(lines) and all(_LIST_MARKER_RE.match(line) for line in lines)


def _is_list_segment(segment: TextSegment) -> bool:
    return _segment_type(segment) in _LIST_TYPES or _is_list_content(segment.content)


def _contextual_index_text(
    content: str,
    *,
    section_path: Sequence[str],
    table_title: Optional[str] = None,
) -> str:
    parts: List[str] = []
    cleaned_path = _dedupe_strings(section_path)
    if cleaned_path:
        parts.append(f"[Section] {' > '.join(cleaned_path)}")
    title = str(table_title or "").strip()
    if title and (not cleaned_path or title.casefold() != cleaned_path[-1].casefold()):
        parts.append(f"[Table] {title}")
    body = str(content or "").strip()
    if body:
        parts.append(body)
    return "\n".join(parts)


class MetricCorpusConfig(BaseModel):
    """Deterministic settings for the retrieval-only corpus."""

    max_text_view_chars: int = Field(default=1200, ge=256)
    sentence_overlap: int = Field(default=1, ge=0, le=4)
    max_full_table_view_chars: int = Field(default=4000, ge=512)
    # Complete tables always remain in ``MetricEvidenceBlock.full_content``.
    # Row views are the default search unit; callers may opt into indexing the
    # whole small table when a corpus lacks reliable row derivatives.
    include_small_full_table_view: bool = False
    include_heading_views: bool = True


class MetricEvidenceBlock(BaseModel):
    """One complete, immutable evidence unit backed by canonical segments."""

    block_id: str
    block_type: str
    primary_segment_id: str
    source_segment_ids: List[str] = Field(min_length=1)
    full_content: str
    page_number: int = Field(ge=1)
    section_path: List[str] = Field(default_factory=list)
    source_report_id: Optional[str] = None
    source_table_id: Optional[str] = None
    content_hash: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MetricRetrievalView(BaseModel):
    """Searchable text that always resolves to one complete evidence block."""

    view_id: str
    evidence_block_id: str
    view_type: str
    primary_segment_id: str
    source_segment_ids: List[str] = Field(min_length=1)
    content: str
    index_text: str
    page_number: int = Field(ge=1)
    section_path: List[str] = Field(default_factory=list)
    source_report_id: Optional[str] = None
    source_table_id: Optional[str] = None
    row_index: Optional[int] = None
    column_indexes: List[int] = Field(default_factory=list)
    start_offset: Optional[int] = Field(default=None, ge=0)
    end_offset: Optional[int] = Field(default=None, ge=0)
    is_complete_block: bool = False
    content_hash: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MetricRetrievalCorpus(BaseModel):
    """Validated structure-first metric retrieval corpus."""

    schema_version: str = METRIC_CORPUS_SCHEMA_VERSION
    chunker_version: str = METRIC_CHUNKER_VERSION
    document_id: str
    content_revision: int = Field(default=1, ge=1)
    config: MetricCorpusConfig
    source_segment_ids: List[str]
    evidence_blocks: List[MetricEvidenceBlock]
    retrieval_views: List[MetricRetrievalView]
    segment_to_block_id: Dict[str, str]
    corpus_signature: str

    def __init__(self, **data: Any):
        # The runtime images used by this project have included both Pydantic
        # 1.x and 2.x.  Explicit post-init validation keeps the corpus contract
        # identical without binding this lightweight module to one decorator
        # API.  ``model_construct``/``construct`` remains available internally
        # for the unsigned signature calculation below.
        super().__init__(**data)
        self.validate_integrity()

    def validate_integrity(self) -> None:
        source_ids = list(self.source_segment_ids)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source segment IDs must be unique")

        block_ids = [block.block_id for block in self.evidence_blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("evidence block IDs must be unique")
        blocks_by_id = {block.block_id: block for block in self.evidence_blocks}

        mapped: Dict[str, str] = {}
        for block in self.evidence_blocks:
            if len(block.source_segment_ids) != len(set(block.source_segment_ids)):
                raise ValueError(f"duplicate source ID in evidence block {block.block_id}")
            if block.primary_segment_id not in block.source_segment_ids:
                raise ValueError(f"primary segment is not in evidence block {block.block_id}")
            if block.content_hash != _sha256_text(block.full_content):
                raise ValueError(f"content hash mismatch for evidence block {block.block_id}")
            for segment_id in block.source_segment_ids:
                if segment_id in mapped:
                    raise ValueError(f"source segment {segment_id} has multiple evidence parents")
                mapped[segment_id] = block.block_id

        if set(mapped) != set(source_ids):
            raise ValueError("every source segment must map to exactly one evidence block")
        if mapped != self.segment_to_block_id:
            raise ValueError("segment_to_block_id does not match evidence blocks")

        view_ids = [view.view_id for view in self.retrieval_views]
        if len(view_ids) != len(set(view_ids)):
            raise ValueError("retrieval view IDs must be unique")
        view_parent_ids: set[str] = set()
        for view in self.retrieval_views:
            parent = blocks_by_id.get(view.evidence_block_id)
            if parent is None:
                raise ValueError(f"retrieval view {view.view_id} has no evidence parent")
            view_parent_ids.add(parent.block_id)
            parent_sources = set(parent.source_segment_ids)
            if view.primary_segment_id not in parent_sources:
                raise ValueError(f"retrieval view {view.view_id} has an invalid primary segment")
            if not set(view.source_segment_ids).issubset(parent_sources):
                raise ValueError(f"retrieval view {view.view_id} escapes its evidence parent")
            if view.content_hash != _sha256_text(view.content):
                raise ValueError(f"content hash mismatch for retrieval view {view.view_id}")
            if view.start_offset is not None or view.end_offset is not None:
                if view.start_offset is None or view.end_offset is None:
                    raise ValueError(f"retrieval view {view.view_id} has incomplete offsets")
                if not 0 <= view.start_offset <= view.end_offset <= len(parent.full_content):
                    raise ValueError(f"retrieval view {view.view_id} has invalid offsets")
                if parent.full_content[view.start_offset : view.end_offset] != view.content:
                    raise ValueError(
                        f"retrieval view {view.view_id} offsets do not match its parent"
                    )

        for block in self.evidence_blocks:
            if block.full_content.strip() and block.block_id not in view_parent_ids:
                raise ValueError(f"non-empty evidence block {block.block_id} has no retrieval view")

        if self.corpus_signature != _corpus_signature_payload(self):
            raise ValueError("metric retrieval corpus signature mismatch")

    def evidence_for_view(self, view_id: str) -> MetricEvidenceBlock:
        """Resolve a view back to its complete evidence parent."""
        view = next((item for item in self.retrieval_views if item.view_id == view_id), None)
        if view is None:
            raise KeyError(view_id)
        block = next(
            (item for item in self.evidence_blocks if item.block_id == view.evidence_block_id),
            None,
        )
        if block is None:  # pragma: no cover - construction validation prevents this
            raise KeyError(view.evidence_block_id)
        return block


def _corpus_signature_payload(corpus: MetricRetrievalCorpus) -> str:
    payload = {
        "schema_version": corpus.schema_version,
        "chunker_version": corpus.chunker_version,
        "document_id": corpus.document_id,
        "content_revision": corpus.content_revision,
        "config": _model_dump(corpus.config),
        "source_segment_ids": corpus.source_segment_ids,
        "blocks": [
            {
                "block_id": block.block_id,
                "content_hash": block.content_hash,
                "source_segment_ids": block.source_segment_ids,
            }
            for block in corpus.evidence_blocks
        ],
        "views": [
            {
                "view_id": view.view_id,
                "evidence_block_id": view.evidence_block_id,
                "content_hash": view.content_hash,
                "index_text_hash": _sha256_text(view.index_text),
                "source_segment_ids": view.source_segment_ids,
                "start_offset": view.start_offset,
                "end_offset": view.end_offset,
            }
            for view in corpus.retrieval_views
        ],
    }
    return _stable_digest(payload)


class MetricRetrievalCorpusBuilder:
    """Build a lossless evidence layer plus compact retrieval views."""

    def __init__(self, config: Optional[MetricCorpusConfig] = None):
        self.config = config or MetricCorpusConfig()

    def build(
        self,
        content: Union[ReportContent, DocumentContent],
    ) -> MetricRetrievalCorpus:
        document = content.document_content if isinstance(content, ReportContent) else content
        segments = list(document.segments or [])
        source_ids = [str(segment.segment_id or "").strip() for segment in segments]
        if any(not segment_id for segment_id in source_ids):
            raise ValueError("every canonical segment must have a non-empty segment_id")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("canonical segment IDs must be unique")

        inferred_paths = self._infer_section_paths(segments)
        table_families = self._table_families(segments)
        table_key_by_segment = {
            segment.segment_id: key
            for key, family in table_families.items()
            for segment in family
        }

        blocks: List[MetricEvidenceBlock] = []
        views: List[MetricRetrievalView] = []
        processed: set[str] = set()
        index = 0
        while index < len(segments):
            segment = segments[index]
            segment_id = segment.segment_id
            if segment_id in processed:
                index += 1
                continue

            table_key = table_key_by_segment.get(segment_id)
            if table_key is not None:
                family = table_families[table_key]
                block, table_views = self._build_table_block(
                    document.document_id,
                    family,
                    inferred_paths,
                )
                blocks.append(block)
                views.extend(table_views)
                processed.update(block.source_segment_ids)
                index += 1
                continue

            if _is_list_segment(segment):
                family = [segment]
                cursor = index + 1
                path = inferred_paths[segment_id]
                while cursor < len(segments):
                    candidate = segments[cursor]
                    if (
                        candidate.segment_id in processed
                        or _segment_type(candidate) in _TABLE_TYPES
                    ):
                        break
                    if not _is_list_segment(candidate):
                        break
                    if (
                        _page_number(candidate) != _page_number(segment)
                        or _source_report_id(candidate) != _source_report_id(segment)
                        or inferred_paths[candidate.segment_id] != path
                    ):
                        break
                    family.append(candidate)
                    cursor += 1
                block, block_views = self._build_list_block(
                    document.document_id,
                    family,
                    path,
                )
                blocks.append(block)
                views.extend(block_views)
                processed.update(block.source_segment_ids)
                index = cursor
                continue

            block, block_views = self._build_single_segment_block(
                document.document_id,
                segment,
                inferred_paths[segment_id],
            )
            blocks.append(block)
            views.extend(block_views)
            processed.add(segment_id)
            index += 1

        segment_to_block = {
            segment_id: block.block_id
            for block in blocks
            for segment_id in block.source_segment_ids
        }
        corpus_data = {
            "schema_version": METRIC_CORPUS_SCHEMA_VERSION,
            "chunker_version": METRIC_CHUNKER_VERSION,
            "document_id": document.document_id,
            "content_revision": int(getattr(document, "content_revision", 1) or 1),
            "config": self.config,
            "source_segment_ids": source_ids,
            "evidence_blocks": blocks,
            "retrieval_views": views,
            "segment_to_block_id": segment_to_block,
            "corpus_signature": "",
        }
        if hasattr(MetricRetrievalCorpus, "model_construct"):
            unsigned = MetricRetrievalCorpus.model_construct(**corpus_data)
        else:
            unsigned = MetricRetrievalCorpus.construct(**corpus_data)
        corpus_data["corpus_signature"] = _corpus_signature_payload(unsigned)
        return MetricRetrievalCorpus(**corpus_data)

    @staticmethod
    def _infer_section_paths(segments: Sequence[TextSegment]) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        current_path: List[str] = []
        for segment in segments:
            explicit = _section_path(segment)
            kind = _segment_type(segment)
            content = str(segment.content or "").strip()
            if explicit:
                current_path = explicit
            elif kind == "heading" and content:
                current_path = [content]
            result[segment.segment_id] = list(explicit or current_path)
        return result

    @staticmethod
    def _table_families(
        segments: Sequence[TextSegment],
    ) -> Dict[Tuple[str, int, str], List[TextSegment]]:
        families: Dict[Tuple[str, int, str], List[TextSegment]] = defaultdict(list)
        for segment in segments:
            if _segment_type(segment) not in _TABLE_TYPES:
                continue
            table_id = _source_table_id(segment)
            if not table_id:
                data = _segment_data(segment)
                row_segment_id = str(data.get("row_segment_id") or "").strip()
                table_id = f"anonymous:{row_segment_id or segment.segment_id}"
            key = (_source_report_id(segment) or "", _page_number(segment), table_id)
            families[key].append(segment)
        return dict(families)

    def _build_table_block(
        self,
        document_id: str,
        family: Sequence[TextSegment],
        inferred_paths: Dict[str, List[str]],
    ) -> Tuple[MetricEvidenceBlock, List[MetricRetrievalView]]:
        ordered = list(family)
        tables = [segment for segment in ordered if _segment_type(segment) == "table"]
        rows = [segment for segment in ordered if _segment_type(segment) == "table_row"]
        cells = [segment for segment in ordered if _segment_type(segment) == "table_cell"]
        source_ids = _dedupe_strings(segment.segment_id for segment in ordered)
        primary = (tables or rows or cells)[0]
        section_path = next(
            (
                inferred_paths[segment.segment_id]
                for segment in [*tables, *rows, *cells]
                if inferred_paths.get(segment.segment_id)
            ),
            [],
        )
        table_title = next(
            (
                str(_segment_data(segment).get("table_title") or "").strip()
                for segment in [*tables, *rows, *cells]
                if str(_segment_data(segment).get("table_title") or "").strip()
            ),
            "",
        )

        full_table_contents = _dedupe_strings(segment.content for segment in tables)
        if full_table_contents:
            full_content = "\n\n".join(full_table_contents)
            synthesized = False
        elif rows:
            full_content = "\n".join(str(segment.content or "") for segment in rows)
            synthesized = True
        else:
            full_content = self._render_cells_as_table(cells, table_title)
            synthesized = True

        content_hash = _sha256_text(full_content)
        table_id = _source_table_id(primary)
        block_id = _stable_id(
            "meb",
            METRIC_CHUNKER_VERSION,
            document_id,
            _source_report_id(primary),
            _page_number(primary),
            "table",
            table_id,
            source_ids,
            content_hash,
        )
        block = MetricEvidenceBlock(
            block_id=block_id,
            block_type="table",
            primary_segment_id=primary.segment_id,
            source_segment_ids=source_ids,
            full_content=full_content,
            page_number=_page_number(primary),
            section_path=section_path,
            source_report_id=_source_report_id(primary),
            source_table_id=table_id,
            content_hash=content_hash,
            metadata={
                "table_title": table_title or None,
                "synthesized_full_table": synthesized,
                "full_table_segment_ids": [segment.segment_id for segment in tables],
                "row_segment_count": len(rows),
                "cell_segment_count": len(cells),
            },
        )

        views = self._table_row_views(block, rows, cells, table_title)
        if (
            self.config.include_small_full_table_view
            and full_content.strip()
            and len(full_content) <= self.config.max_full_table_view_chars
        ):
            views.insert(
                0,
                self._make_view(
                    block,
                    view_type="table_full",
                    primary_segment_id=block.primary_segment_id,
                    source_segment_ids=block.source_segment_ids,
                    content=full_content,
                    index_text=_contextual_index_text(
                        full_content,
                        section_path=section_path,
                        table_title=table_title,
                    ),
                    is_complete_block=True,
                ),
            )
        if not views and full_content.strip():
            views.append(
                self._make_view(
                    block,
                    view_type="table_full",
                    primary_segment_id=block.primary_segment_id,
                    source_segment_ids=block.source_segment_ids,
                    content=full_content,
                    index_text=_contextual_index_text(
                        full_content,
                        section_path=section_path,
                        table_title=table_title,
                    ),
                    is_complete_block=True,
                    metadata={
                        "oversized": (
                            len(full_content)
                            > self.config.max_full_table_view_chars
                        )
                    },
                )
            )
        return block, views

    def _table_row_views(
        self,
        block: MetricEvidenceBlock,
        rows: Sequence[TextSegment],
        cells: Sequence[TextSegment],
        table_title: str,
    ) -> List[MetricRetrievalView]:
        grouped: Dict[Tuple[str, Any], List[TextSegment]] = defaultdict(list)

        def row_key(segment: TextSegment) -> Tuple[str, Any]:
            data = _segment_data(segment)
            raw_index = data.get("row_index", data.get("row_idx"))
            try:
                return "index", int(raw_index)
            except (TypeError, ValueError):
                row_segment_id = str(data.get("row_segment_id") or "").strip()
                return "segment", row_segment_id or segment.segment_id

        for segment in [*rows, *cells]:
            grouped[row_key(segment)].append(segment)

        row_views: List[MetricRetrievalView] = []
        for key, family in grouped.items():
            row_segments = [item for item in family if _segment_type(item) == "table_row"]
            cell_segments = [item for item in family if _segment_type(item) == "table_cell"]
            primary = (row_segments or cell_segments)[0]
            source_ids = _dedupe_strings(item.segment_id for item in family)
            row_contents = _dedupe_strings(item.content for item in row_segments)
            row_content = (
                "\n".join(row_contents)
                if row_contents
                else self._render_cells_as_row(cell_segments, table_title)
            )
            if not row_content.strip():
                continue
            row_index = key[1] if key[0] == "index" else None
            column_indexes: List[int] = []
            for cell in cell_segments:
                data = _segment_data(cell)
                raw_column = data.get("col_index", data.get("column_index"))
                try:
                    column_indexes.append(int(raw_column))
                except (TypeError, ValueError):
                    continue
            row_views.append(
                self._make_view(
                    block,
                    view_type="table_row",
                    primary_segment_id=primary.segment_id,
                    source_segment_ids=source_ids,
                    content=row_content,
                    index_text=_contextual_index_text(
                        row_content,
                        section_path=block.section_path,
                        table_title=table_title,
                    ),
                    row_index=row_index,
                    column_indexes=sorted(set(column_indexes)),
                    metadata={
                        "row_header": next(
                            (
                                str(
                                    getattr(item, "row_header", None)
                                    or _segment_data(item).get("row_header")
                                    or ""
                                ).strip()
                                for item in family
                                if str(
                                    getattr(item, "row_header", None)
                                    or _segment_data(item).get("row_header")
                                    or ""
                                ).strip()
                            ),
                            None,
                        ),
                        "oversized": len(row_content) > self.config.max_text_view_chars,
                    },
                )
            )
        return row_views

    @staticmethod
    def _render_cells_as_table(cells: Sequence[TextSegment], table_title: str) -> str:
        grouped: Dict[Any, List[TextSegment]] = defaultdict(list)
        for cell in cells:
            data = _segment_data(cell)
            key = data.get("row_index", data.get("row_segment_id", cell.segment_id))
            grouped[key].append(cell)
        rendered = [
            MetricRetrievalCorpusBuilder._render_cells_as_row(row, table_title)
            for row in grouped.values()
        ]
        return "\n".join(item for item in rendered if item)

    @staticmethod
    def _render_cells_as_row(cells: Sequence[TextSegment], table_title: str) -> str:
        if not cells:
            return ""

        def column_key(cell: TextSegment) -> Tuple[int, float, str]:
            data = _segment_data(cell)
            raw = data.get("col_index", data.get("column_index"))
            try:
                column = int(raw)
            except (TypeError, ValueError):
                column = 10_000
            try:
                position = float(getattr(cell, "position_x", 0.0) or 0.0)
            except (TypeError, ValueError):
                position = 0.0
            return column, position, cell.segment_id

        ordered = sorted(cells, key=column_key)
        row_header = next(
            (
                str(
                    getattr(cell, "row_header", None)
                    or _segment_data(cell).get("row_header")
                    or ""
                ).strip()
                for cell in ordered
                if str(
                    getattr(cell, "row_header", None)
                    or _segment_data(cell).get("row_header")
                    or ""
                ).strip()
            ),
            "",
        )
        parts: List[str] = []
        if table_title:
            parts.append(f"[Table Title] {table_title}")
        if row_header:
            parts.append(f"[Row] {row_header}")
        for cell in ordered:
            data = _segment_data(cell)
            header_path = _dedupe_strings(
                data.get("header_path")
                if isinstance(data.get("header_path"), (list, tuple))
                else getattr(cell, "header_path", None) or []
            )
            col_header = str(
                getattr(cell, "col_header", None)
                or data.get("col_header")
                or (header_path[-1] if header_path else "Value")
            ).strip()
            value = str(
                getattr(cell, "value_text", None)
                or data.get("value_text")
                or cell.content
                or ""
            ).strip()
            unit = str(getattr(cell, "unit", None) or data.get("unit") or "").strip()
            year = data.get("year")
            label = " > ".join(header_path) or col_header
            rendered_value = value
            if unit and unit.casefold() not in value.casefold():
                rendered_value = f"{rendered_value} {unit}".strip()
            if year is not None and str(year) not in label and str(year) not in rendered_value:
                label = f"{label} ({year})"
            parts.append(f"{label}: {rendered_value}" if label else rendered_value)
        return "\n".join(_dedupe_strings(parts))

    def _build_list_block(
        self,
        document_id: str,
        family: Sequence[TextSegment],
        section_path: Sequence[str],
    ) -> Tuple[MetricEvidenceBlock, List[MetricRetrievalView]]:
        primary = family[0]
        source_ids = _dedupe_strings(segment.segment_id for segment in family)
        full_content = "\n".join(str(segment.content or "") for segment in family)
        block = self._make_block(
            document_id=document_id,
            block_type="list",
            primary=primary,
            source_ids=source_ids,
            full_content=full_content,
            section_path=section_path,
            metadata={"item_count": len(family)},
        )
        views = []
        if full_content.strip():
            views.append(
                self._make_view(
                    block,
                    view_type="list",
                    primary_segment_id=primary.segment_id,
                    source_segment_ids=source_ids,
                    content=full_content,
                    index_text=_contextual_index_text(
                        full_content,
                        section_path=section_path,
                    ),
                    is_complete_block=True,
                    start_offset=0,
                    end_offset=len(full_content),
                    metadata={"oversized": len(full_content) > self.config.max_text_view_chars},
                )
            )
        return block, views

    def _build_single_segment_block(
        self,
        document_id: str,
        segment: TextSegment,
        section_path: Sequence[str],
    ) -> Tuple[MetricEvidenceBlock, List[MetricRetrievalView]]:
        kind = _segment_type(segment)
        if kind == "heading":
            block_type = "heading"
        elif kind in _VISUAL_TYPES:
            block_type = "visual"
        elif kind in {"footnote", "caption", "index", "link_anchor"}:
            block_type = kind
        elif not str(segment.content or ""):
            block_type = "empty"
        else:
            block_type = "paragraph"
        full_content = str(segment.content or "")
        block = self._make_block(
            document_id=document_id,
            block_type=block_type,
            primary=segment,
            source_ids=[segment.segment_id],
            full_content=full_content,
            section_path=section_path,
            metadata={"canonical_segment_type": kind},
        )
        if not full_content.strip():
            return block, []
        if block_type == "heading" and not self.config.include_heading_views:
            # The heading still needs one view to satisfy strict parent coverage;
            # mark it contextual-only so a caller can omit it from dense indexing.
            return block, [
                self._make_view(
                    block,
                    view_type="heading_context",
                    primary_segment_id=segment.segment_id,
                    source_segment_ids=[segment.segment_id],
                    content=full_content,
                    index_text=_contextual_index_text(full_content, section_path=section_path),
                    is_complete_block=True,
                    start_offset=0,
                    end_offset=len(full_content),
                    metadata={"dense_index": False},
                )
            ]
        if block_type != "paragraph" or len(full_content) <= self.config.max_text_view_chars:
            return block, [
                self._make_view(
                    block,
                    view_type=block_type,
                    primary_segment_id=segment.segment_id,
                    source_segment_ids=[segment.segment_id],
                    content=full_content,
                    index_text=_contextual_index_text(full_content, section_path=section_path),
                    is_complete_block=True,
                    start_offset=0,
                    end_offset=len(full_content),
                )
            ]

        spans = self._sentence_view_spans(full_content)
        views = [
            self._make_view(
                block,
                view_type="paragraph_sentence_window",
                primary_segment_id=segment.segment_id,
                source_segment_ids=[segment.segment_id],
                content=full_content[start:end],
                index_text=_contextual_index_text(
                    full_content[start:end],
                    section_path=section_path,
                ),
                start_offset=start,
                end_offset=end,
                is_complete_block=start == 0 and end == len(full_content),
                metadata={
                    "oversized_sentence": end - start > self.config.max_text_view_chars,
                },
            )
            for start, end in spans
        ]
        return block, views

    def _make_block(
        self,
        *,
        document_id: str,
        block_type: str,
        primary: TextSegment,
        source_ids: Sequence[str],
        full_content: str,
        section_path: Sequence[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MetricEvidenceBlock:
        content_hash = _sha256_text(full_content)
        block_id = _stable_id(
            "meb",
            METRIC_CHUNKER_VERSION,
            document_id,
            _source_report_id(primary),
            _page_number(primary),
            block_type,
            list(source_ids),
            content_hash,
        )
        return MetricEvidenceBlock(
            block_id=block_id,
            block_type=block_type,
            primary_segment_id=primary.segment_id,
            source_segment_ids=list(source_ids),
            full_content=full_content,
            page_number=_page_number(primary),
            section_path=list(section_path),
            source_report_id=_source_report_id(primary),
            source_table_id=_source_table_id(primary),
            content_hash=content_hash,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _make_view(
        block: MetricEvidenceBlock,
        *,
        view_type: str,
        primary_segment_id: str,
        source_segment_ids: Sequence[str],
        content: str,
        index_text: str,
        row_index: Optional[int] = None,
        column_indexes: Optional[Sequence[int]] = None,
        start_offset: Optional[int] = None,
        end_offset: Optional[int] = None,
        is_complete_block: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MetricRetrievalView:
        content_hash = _sha256_text(content)
        source_ids = list(source_segment_ids)
        view_id = _stable_id(
            "mrv",
            METRIC_CHUNKER_VERSION,
            block.block_id,
            view_type,
            row_index,
            list(column_indexes or []),
            start_offset,
            end_offset,
            source_ids,
            content_hash,
            _sha256_text(index_text),
        )
        return MetricRetrievalView(
            view_id=view_id,
            evidence_block_id=block.block_id,
            view_type=view_type,
            primary_segment_id=primary_segment_id,
            source_segment_ids=source_ids,
            content=content,
            index_text=index_text,
            page_number=block.page_number,
            section_path=block.section_path,
            source_report_id=block.source_report_id,
            source_table_id=block.source_table_id,
            row_index=row_index,
            column_indexes=list(column_indexes or []),
            start_offset=start_offset,
            end_offset=end_offset,
            is_complete_block=is_complete_block,
            content_hash=content_hash,
            metadata=dict(metadata or {}),
        )

    def _sentence_view_spans(self, text: str) -> List[Tuple[int, int]]:
        sentence_spans: List[Tuple[int, int]] = []
        start = 0
        for match in _SENTENCE_END_RE.finditer(text):
            end = match.end()
            if end > start:
                sentence_spans.append((start, end))
            start = end
        if start < len(text):
            sentence_spans.append((start, len(text)))
        if not sentence_spans:
            return [(0, len(text))]

        windows: List[Tuple[int, int]] = []
        cursor = 0
        while cursor < len(sentence_spans):
            end_index = cursor
            window_start = sentence_spans[cursor][0]
            while end_index < len(sentence_spans):
                candidate_end = sentence_spans[end_index][1]
                if (
                    end_index > cursor
                    and candidate_end - window_start
                    > self.config.max_text_view_chars
                ):
                    break
                end_index += 1
                if candidate_end - window_start >= self.config.max_text_view_chars:
                    break
            if end_index == cursor:
                end_index = cursor + 1
            window_end = sentence_spans[end_index - 1][1]
            windows.append((window_start, window_end))
            if end_index >= len(sentence_spans):
                break
            next_cursor = end_index - min(self.config.sentence_overlap, end_index - cursor - 1)
            cursor = max(cursor + 1, next_cursor)
        return windows


def build_metric_retrieval_corpus(
    content: Union[ReportContent, DocumentContent],
    config: Optional[MetricCorpusConfig] = None,
) -> MetricRetrievalCorpus:
    """Build a structure-preserving metric retrieval corpus."""
    return MetricRetrievalCorpusBuilder(config).build(content)


def resolve_metric_retrieval_corpus(
    report_content: ReportContent,
    config: Optional[MetricCorpusConfig] = None,
) -> MetricRetrievalCorpus:
    """Return an attached valid corpus or deterministically rebuild it in memory."""
    canonical_ids = [
        str(segment.segment_id)
        for segment in report_content.document_content.segments or []
    ]
    attached = getattr(report_content, "_metric_retrieval_corpus", None)
    try:
        current_revision = max(
            1,
            int(
                getattr(
                    report_content.document_content,
                    "content_revision",
                    1,
                )
                or 1
            ),
        )
    except (TypeError, ValueError):
        current_revision = 1
    if (
        isinstance(attached, MetricRetrievalCorpus)
        and attached.source_segment_ids == canonical_ids
        and attached.content_revision == current_revision
    ):
        return attached
    corpus = build_metric_retrieval_corpus(report_content, config=config)
    object.__setattr__(report_content, "_metric_retrieval_corpus", corpus)
    return corpus


def attach_metric_embeddings(
    corpus: MetricRetrievalCorpus,
    matrix: np.ndarray,
    *,
    view_ids: Optional[Sequence[str]] = None,
    embedding_model: str = "",
    normalized: bool = True,
) -> MetricRetrievalCorpus:
    """Attach a validated float32 matrix while keeping it out of JSON models."""
    expected_ids = [view.view_id for view in corpus.retrieval_views]
    resolved_ids = [str(value) for value in (view_ids or expected_ids)]
    resolved_matrix = np.asarray(matrix, dtype=np.float32)
    if (
        resolved_matrix.ndim != 2
        or resolved_matrix.shape[0] != len(expected_ids)
        or resolved_ids != expected_ids
        or not np.isfinite(resolved_matrix).all()
    ):
        raise ValueError("Metric embedding rows must match retrieval views exactly")
    object.__setattr__(
        corpus,
        "_embedding_matrix",
        np.ascontiguousarray(resolved_matrix, dtype=np.float32),
    )
    object.__setattr__(corpus, "_embedding_view_ids", resolved_ids)
    object.__setattr__(corpus, "_embedding_model", str(embedding_model or ""))
    object.__setattr__(corpus, "_embeddings_normalized", bool(normalized))
    return corpus


def metric_embeddings(
    corpus: MetricRetrievalCorpus,
) -> Optional[Tuple[np.ndarray, List[str], str]]:
    """Return a validated attached metric matrix, or None when unavailable."""
    matrix = getattr(corpus, "_embedding_matrix", None)
    ids = [str(value) for value in (getattr(corpus, "_embedding_view_ids", None) or [])]
    expected_ids = [view.view_id for view in corpus.retrieval_views]
    if not isinstance(matrix, np.ndarray):
        return None
    matrix = np.asarray(matrix, dtype=np.float32)
    if (
        matrix.ndim != 2
        or matrix.shape[0] != len(expected_ids)
        or ids != expected_ids
        or not np.isfinite(matrix).all()
    ):
        return None
    return (
        np.ascontiguousarray(matrix, dtype=np.float32),
        ids,
        str(getattr(corpus, "_embedding_model", "") or ""),
    )


def metric_search_units(
    report_content: ReportContent,
    corpus: Optional[MetricRetrievalCorpus] = None,
) -> List[Any]:
    """Adapt retrieval views to the segment-shaped scoring contract.

    Internal ``segment_id`` values remain unique view IDs. Callers must map
    public results back through ``canonical_segment_id``.
    """
    corpus = corpus or resolve_metric_retrieval_corpus(report_content)
    canonical = {
        str(segment.segment_id): segment
        for segment in report_content.document_content.segments or []
    }
    blocks = {block.block_id: block for block in corpus.evidence_blocks}
    units: List[Any] = []
    for view in corpus.retrieval_views:
        segment = canonical.get(view.primary_segment_id)
        block = blocks.get(view.evidence_block_id)
        if segment is None or block is None:
            continue
        payload = _model_dump(segment)
        structured = dict(payload.get("structured_data") or {})
        structured.update(
            {
                "metric_evidence_block_id": block.block_id,
                "metric_retrieval_view_id": view.view_id,
                "metric_view_type": view.view_type,
                "section_path": list(view.section_path),
            }
        )
        if view.row_index is not None:
            structured["row_index"] = view.row_index
        payload.update(
            {
                "segment_id": view.view_id,
                "content": view.index_text,
                "page_number": view.page_number,
                "source_table_id": view.source_table_id,
                "structured_data": structured,
                "canonical_segment_id": view.primary_segment_id,
                "retrieval_view_id": view.view_id,
                "evidence_block_id": block.block_id,
                "source_segment_ids": list(block.source_segment_ids),
                "matched_content": view.content,
                "evidence_block_content": block.full_content,
                "matched_row_index": view.row_index,
                "matched_column_indexes": list(view.column_indexes),
                "view_type": view.view_type,
                "source_report_id": view.source_report_id,
            }
        )
        units.append(SimpleNamespace(**payload))
    return units


def subset_metric_retrieval_corpus(
    corpus: MetricRetrievalCorpus,
    allowed_segment_ids: Iterable[str],
) -> Optional[MetricRetrievalCorpus]:
    """Create a complete-block subset and slice attached embeddings by view ID."""
    allowed = {str(value) for value in allowed_segment_ids if str(value)}
    selected_views = [
        view
        for view in corpus.retrieval_views
        if view.primary_segment_id in allowed
        or bool(set(view.source_segment_ids) & allowed)
    ]
    if not selected_views:
        return None
    selected_block_ids = {view.evidence_block_id for view in selected_views}
    selected_blocks = [
        block
        for block in corpus.evidence_blocks
        if block.block_id in selected_block_ids
    ]
    selected_source_ids = [
        segment_id
        for segment_id in corpus.source_segment_ids
        if corpus.segment_to_block_id.get(segment_id) in selected_block_ids
    ]
    selected_mapping = {
        segment_id: corpus.segment_to_block_id[segment_id]
        for segment_id in selected_source_ids
    }
    data = {
        "schema_version": corpus.schema_version,
        "chunker_version": corpus.chunker_version,
        "document_id": corpus.document_id,
        "content_revision": corpus.content_revision,
        "config": corpus.config,
        "source_segment_ids": selected_source_ids,
        "evidence_blocks": selected_blocks,
        "retrieval_views": selected_views,
        "segment_to_block_id": selected_mapping,
        "corpus_signature": "",
    }
    unsigned = _model_construct(MetricRetrievalCorpus, **data)
    data["corpus_signature"] = _corpus_signature_payload(unsigned)
    subset = MetricRetrievalCorpus(**data)

    embedded = metric_embeddings(corpus)
    if embedded is not None:
        matrix, view_ids, model_name = embedded
        index_by_id = {view_id: index for index, view_id in enumerate(view_ids)}
        indexes = [index_by_id[view.view_id] for view in selected_views]
        attach_metric_embeddings(
            subset,
            matrix[indexes],
            embedding_model=model_name,
            normalized=bool(getattr(corpus, "_embeddings_normalized", True)),
        )
    return subset


def namespace_metric_retrieval_corpus(
    corpus: MetricRetrievalCorpus,
    namespace: str,
    *,
    source_report_name: Optional[str] = None,
    source_report_year: Optional[int] = None,
) -> MetricRetrievalCorpus:
    """Namespace one report corpus for collision-free company retrieval."""
    prefix = str(namespace or "").strip()
    if not prefix:
        raise ValueError("Metric corpus namespace is required")

    def namespaced(value: Optional[str]) -> Optional[str]:
        text = str(value or "").strip()
        return f"{prefix}::{text}" if text else None

    blocks: List[MetricEvidenceBlock] = []
    for block in corpus.evidence_blocks:
        metadata = dict(block.metadata or {})
        metadata.update(
            {
                "source_report_id": prefix,
                "source_report_name": source_report_name,
                "source_report_year": source_report_year,
            }
        )
        blocks.append(
            _model_copy(
                block,
                update={
                    "block_id": namespaced(block.block_id),
                    "primary_segment_id": namespaced(block.primary_segment_id),
                    "source_segment_ids": [
                        namespaced(value) for value in block.source_segment_ids
                    ],
                    "source_report_id": prefix,
                    "source_table_id": namespaced(block.source_table_id),
                    "metadata": metadata,
                }
            )
        )
    views: List[MetricRetrievalView] = []
    for view in corpus.retrieval_views:
        metadata = dict(view.metadata or {})
        metadata.update(
            {
                "source_report_id": prefix,
                "source_report_name": source_report_name,
                "source_report_year": source_report_year,
            }
        )
        views.append(
            _model_copy(
                view,
                update={
                    "view_id": namespaced(view.view_id),
                    "evidence_block_id": namespaced(view.evidence_block_id),
                    "primary_segment_id": namespaced(view.primary_segment_id),
                    "source_segment_ids": [
                        namespaced(value) for value in view.source_segment_ids
                    ],
                    "source_report_id": prefix,
                    "source_table_id": namespaced(view.source_table_id),
                    "metadata": metadata,
                }
            )
        )
    source_ids = [namespaced(value) for value in corpus.source_segment_ids]
    mapping = {
        namespaced(segment_id): namespaced(block_id)
        for segment_id, block_id in corpus.segment_to_block_id.items()
    }
    data = {
        "schema_version": corpus.schema_version,
        "chunker_version": corpus.chunker_version,
        "document_id": f"{prefix}::{corpus.document_id}",
        "content_revision": corpus.content_revision,
        "config": corpus.config,
        "source_segment_ids": source_ids,
        "evidence_blocks": blocks,
        "retrieval_views": views,
        "segment_to_block_id": mapping,
        "corpus_signature": "",
    }
    unsigned = _model_construct(MetricRetrievalCorpus, **data)
    data["corpus_signature"] = _corpus_signature_payload(unsigned)
    result = MetricRetrievalCorpus(**data)
    embedded = metric_embeddings(corpus)
    if embedded is not None:
        matrix, _ids, model_name = embedded
        attach_metric_embeddings(
            result,
            matrix,
            embedding_model=model_name,
            normalized=bool(getattr(corpus, "_embeddings_normalized", True)),
        )
    return result


def combine_metric_retrieval_corpora(
    corpora: Sequence[MetricRetrievalCorpus],
    *,
    document_id: str,
) -> Optional[MetricRetrievalCorpus]:
    """Combine already-namespaced corpora when their embedding contract matches."""
    values = list(corpora)
    if not values:
        return None
    first = values[0]
    config_dump = _model_dump(first.config)
    if any(
        corpus.schema_version != first.schema_version
        or corpus.chunker_version != first.chunker_version
        or _model_dump(corpus.config) != config_dump
        for corpus in values[1:]
    ):
        return None

    blocks = [block for corpus in values for block in corpus.evidence_blocks]
    views = [view for corpus in values for view in corpus.retrieval_views]
    source_ids = [
        segment_id for corpus in values for segment_id in corpus.source_segment_ids
    ]
    mapping = {
        segment_id: block_id
        for corpus in values
        for segment_id, block_id in corpus.segment_to_block_id.items()
    }
    data = {
        "schema_version": first.schema_version,
        "chunker_version": first.chunker_version,
        "document_id": str(document_id),
        "content_revision": max(corpus.content_revision for corpus in values),
        "config": first.config,
        "source_segment_ids": source_ids,
        "evidence_blocks": blocks,
        "retrieval_views": views,
        "segment_to_block_id": mapping,
        "corpus_signature": "",
    }
    unsigned = _model_construct(MetricRetrievalCorpus, **data)
    data["corpus_signature"] = _corpus_signature_payload(unsigned)
    combined = MetricRetrievalCorpus(**data)

    embedded_values = [metric_embeddings(corpus) for corpus in values]
    if any(value is None for value in embedded_values):
        return combined
    matrices = [value[0] for value in embedded_values if value is not None]
    models = {value[2] for value in embedded_values if value is not None}
    dimensions = {matrix.shape[1] for matrix in matrices}
    if len(models) != 1 or len(dimensions) != 1:
        return None
    attach_metric_embeddings(
        combined,
        np.vstack(matrices).astype(np.float32, copy=False),
        embedding_model=next(iter(models)),
        normalized=all(
            bool(getattr(corpus, "_embeddings_normalized", True))
            for corpus in values
        ),
    )
    return combined


__all__ = [
    "METRIC_CHUNKER_VERSION",
    "METRIC_CORPUS_SCHEMA_VERSION",
    "MetricCorpusConfig",
    "MetricEvidenceBlock",
    "MetricRetrievalCorpus",
    "MetricRetrievalCorpusBuilder",
    "MetricRetrievalView",
    "attach_metric_embeddings",
    "build_metric_retrieval_corpus",
    "combine_metric_retrieval_corpora",
    "metric_embeddings",
    "metric_search_units",
    "namespace_metric_retrieval_corpus",
    "resolve_metric_retrieval_corpus",
    "subset_metric_retrieval_corpus",
]
