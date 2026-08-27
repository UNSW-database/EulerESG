"""Build a company-level virtual corpus from persisted report artifacts."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np

from .file_manager import file_manager
from .models import DocumentContent, ReportContent, TextSegment
from .retrieval.metric_corpus import (
    MetricRetrievalCorpus,
    combine_metric_retrieval_corpora,
    metric_embeddings,
    namespace_metric_retrieval_corpus,
)


def _safe_year(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None


def _namespace_id(file_id: str, value: Any) -> str:
    text = str(value or "").strip()
    return f"{file_id}::{text}" if text else ""


def _copy_segment(
    segment: TextSegment,
    *,
    file_id: str,
    report_name: str,
    report_year: int | None,
) -> TextSegment:
    data = copy.deepcopy(segment.structured_data or {})
    original_segment_id = str(segment.segment_id or "")
    original_table_id = str(segment.source_table_id or data.get("table_id") or "")
    data.update(
        {
            "source_report_id": file_id,
            "source_report_name": report_name,
            "source_report_year": report_year,
            "source_segment_id": original_segment_id,
        }
    )
    if original_table_id:
        namespaced_table_id = _namespace_id(file_id, original_table_id)
        data["source_table_id"] = namespaced_table_id
        data["table_id"] = namespaced_table_id
    row_segment_id = str(data.get("row_segment_id") or "").strip()
    if row_segment_id:
        data["row_segment_id"] = _namespace_id(file_id, row_segment_id)

    return segment.model_copy(
        update={
            "segment_id": _namespace_id(file_id, original_segment_id),
            "source_table_id": (
                _namespace_id(file_id, original_table_id) if original_table_id else None
            ),
            "structured_data": data,
        }
    )


def build_company_report_content(
    company: Dict[str, Any],
    report_ids: Iterable[str] | None = None,
) -> Tuple[ReportContent, List[Dict[str, Any]]]:
    """Load and combine report artifacts without rerunning OCR or embeddings."""
    company_id = str(company.get("company_id") or "").strip()
    if not company_id:
        raise ValueError("Company ID is required")
    requested_ids = list(report_ids or company.get("report_ids") or [])
    if not requested_ids:
        raise ValueError("Company has no reports")

    all_segments: List[TextSegment] = []
    embedding_segments: List[TextSegment] = []
    embedding_rows: List[np.ndarray] = []
    source_reports: List[Dict[str, Any]] = []
    metric_corpora: List[MetricRetrievalCorpus] = []
    complete_metric_corpus = True
    content_revisions: List[int] = []

    for file_id in requested_ids:
        file_info = file_manager.metadata.get("files", {}).get(file_id)
        if not isinstance(file_info, dict):
            raise ValueError(f"Report metadata is missing for {file_id}")
        try:
            artifacts = file_manager.load_report_artifacts(
                file_id,
                include_metric_corpus=True,
            )
        except TypeError:
            # Compatibility with lightweight test/extension doubles that still
            # implement the historical one-argument loader.
            artifacts = file_manager.load_report_artifacts(file_id)
        if not artifacts:
            raise ValueError(f"Report artifacts are missing for {file_id}")
        try:
            content_revisions.append(
                max(1, int(artifacts.get("content_revision", 1) or 1))
            )
        except (TypeError, ValueError):
            content_revisions.append(1)

        report_name = str(file_info.get("original_name") or file_id)
        report_year = _safe_year(file_info.get("report_year"))
        source_reports.append(
            {
                "report_id": file_id,
                "report_name": report_name,
                "report_year": report_year,
                "page_count": file_info.get("page_count"),
            }
        )
        source_metric_corpus = artifacts.get("metric_retrieval_corpus")
        if isinstance(source_metric_corpus, MetricRetrievalCorpus):
            metric_corpora.append(
                namespace_metric_retrieval_corpus(
                    source_metric_corpus,
                    file_id,
                    source_report_name=report_name,
                    source_report_year=report_year,
                )
            )
        else:
            complete_metric_corpus = False

        copied_by_original_id: Dict[str, TextSegment] = {}
        for segment in artifacts.get("segments") or []:
            copied = _copy_segment(
                segment,
                file_id=file_id,
                report_name=report_name,
                report_year=report_year,
            )
            all_segments.append(copied)
            copied_by_original_id[str(segment.segment_id)] = copied

        matrix = np.asarray(artifacts.get("embedding_matrix"), dtype=np.float32)
        embedding_ids = list(artifacts.get("embedding_segment_ids") or [])
        if matrix.ndim != 2 or matrix.shape[0] != len(embedding_ids):
            raise ValueError(f"Embedding artifact shape is invalid for {file_id}")
        for index, original_id in enumerate(embedding_ids):
            copied = copied_by_original_id.get(str(original_id))
            if copied is None:
                continue
            embedding_segments.append(copied)
            embedding_rows.append(matrix[index])

    if not all_segments:
        raise ValueError("Company reports contain no text segments")
    if not embedding_rows:
        raise ValueError("Company reports contain no reusable embeddings")

    dimensions = {int(row.shape[0]) for row in embedding_rows}
    if len(dimensions) != 1:
        raise ValueError("Company report embeddings use incompatible dimensions")
    embedding_matrix = np.vstack(embedding_rows).astype(np.float32, copy=False)
    markdown = "\n\n".join(
        f"<!-- REPORT {item['report_id']}: {item['report_name']} -->"
        for item in source_reports
    )
    document = DocumentContent(
        document_id=company_id,
        file_path=f"company:{company_id}",
        segments=all_segments,
        content_revision=max(content_revisions or [1]),
        markdown_content=markdown,
        created_at=datetime.now(),
    )
    report = ReportContent(
        document_id=company_id,
        document_content=document,
        embeddings=[],
        created_at=datetime.now(),
    )
    object.__setattr__(
        report,
        "_semantic_retrieval_embedding_cache",
        (embedding_segments, embedding_matrix),
    )
    if complete_metric_corpus and len(metric_corpora) == len(requested_ids):
        combined_metric_corpus = combine_metric_retrieval_corpora(
            metric_corpora,
            document_id=company_id,
        )
        if (
            combined_metric_corpus is not None
            and metric_embeddings(combined_metric_corpus) is not None
            and combined_metric_corpus.source_segment_ids
            == [segment.segment_id for segment in all_segments]
        ):
            object.__setattr__(
                report,
                "_metric_retrieval_corpus",
                combined_metric_corpus,
            )
    object.__setattr__(report, "_company_source_reports", source_reports)
    return report, source_reports


__all__ = ["build_company_report_content"]
