from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from esg_encoding.file_manager import FileManager
from esg_encoding.models import (
    DocumentContent,
    ESGMetric,
    MetricCategory,
    MetricSource,
    ProcessingConfig,
    ReportContent,
    SegmentEmbedding,
    TextSegment,
)
from esg_encoding.retrieval.keyword import KeywordRetriever
from esg_encoding.retrieval.semantic import SemanticRetriever
from esg_encoding.retrieval.metric_corpus import (
    attach_metric_embeddings,
    build_metric_retrieval_corpus,
    metric_embeddings,
)


def _report() -> ReportContent:
    segments = [
        TextSegment(
            segment_id="table",
            content=(
                "| Code | Metric | FY2024 |\n|---|---|---:|\n"
                "| IF-EU-130a.1 | Energy use | 120 GJ |"
            ),
            page_number=3,
            position_y=10.0,
            segment_type="table",
            source_table_id="energy-table",
            structured_data={"table_id": "energy-table"},
        ),
        TextSegment(
            segment_id="row",
            content="Code: IF-EU-130a.1 | Metric: Energy use | FY2024: 120 GJ",
            page_number=3,
            position_y=20.0,
            segment_type="table_row",
            source_table_id="energy-table",
            row_header="Energy use",
            structured_data={"table_id": "energy-table", "row_index": 2},
        ),
    ]
    document = DocumentContent(
        document_id="report",
        file_path="report.pdf",
        segments=segments,
        markdown_content="",
        content_revision=3,
    )
    report = ReportContent(
        document_id="report",
        document_content=document,
        embeddings=[
            SegmentEmbedding(segment_id="table", embedding=[1.0, 0.0]),
            SegmentEmbedding(segment_id="row", embedding=[0.0, 1.0]),
        ],
    )
    corpus = build_metric_retrieval_corpus(report)
    matrix = np.arange(
        len(corpus.retrieval_views) * 2,
        dtype=np.float32,
    ).reshape(len(corpus.retrieval_views), 2)
    attach_metric_embeddings(corpus, matrix, embedding_model="test-model")
    object.__setattr__(report, "_metric_retrieval_corpus", corpus)
    return report


class MetricRetrievalArtifactTests(unittest.TestCase):
    def test_exact_row_hit_returns_canonical_id_and_complete_table_evidence(self):
        report = _report()
        metric = ESGMetric(
            metric_id="energy",
            metric_name="Energy use",
            metric_code="IF-EU-130a.1",
            category=MetricCategory.ENVIRONMENTAL,
            source=MetricSource.SASB,
            keywords=["energy use"],
            unit="GJ",
        )

        results = KeywordRetriever(ProcessingConfig()).search_exact_code(
            report,
            metric,
        )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.segment_id, "row")
        self.assertNotEqual(result.retrieval_view_id, result.segment_id)
        self.assertIn("Code: IF-EU-130a.1", result.matched_content)
        self.assertIn("| IF-EU-130a.1 | Energy use | 120 GJ |", result.content)
        self.assertEqual(result.content, result.evidence_block_content)
        self.assertEqual(result.source_segment_ids, ["table", "row"])

    def test_dense_row_hit_returns_canonical_id_and_complete_table_evidence(self):
        report = _report()
        metric = ESGMetric(
            metric_id="energy",
            metric_name="Energy use",
            metric_code="IF-EU-130a.1",
            category=MetricCategory.ENVIRONMENTAL,
            source=MetricSource.SASB,
            keywords=["energy use"],
            unit="GJ",
        )
        retriever = object.__new__(SemanticRetriever)
        retriever.config = ProcessingConfig(
            similarity_threshold=0.0,
            embedding_model="test-model",
        )
        retriever.embedding_model = SimpleNamespace()
        retriever.reranker = None
        retriever._reranker_initialized = True
        retriever.reranker_top_k = 10
        retriever._reranker_lock = threading.Lock()

        with patch(
            "esg_encoding.retrieval.semantic.encode_query_texts",
            return_value=np.asarray([[0.0, 1.0]], dtype=np.float32),
        ):
            results = retriever.search_by_semantic(
                report,
                metric,
                apply_reranker=False,
            )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.segment_id, "row")
        self.assertIn("Code: IF-EU-130a.1", result.matched_content)
        self.assertIn("| IF-EU-130a.1 | Energy use | 120 GJ |", result.content)
        self.assertEqual(result.content, result.evidence_block_content)

    def test_optional_sidecar_roundtrip_preserves_revision_and_embeddings(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            report = _report()

            paths = manager.save_report_artifacts("report", report)
            loaded = manager.load_report_artifacts(
                "report",
                include_metric_corpus=True,
                expected_embedding_model="test-model",
            )

            self.assertIsNotNone(loaded)
            self.assertIn("metric_retrieval_manifest_path", paths)
            self.assertEqual(loaded["content_revision"], 3)
            corpus = loaded["metric_retrieval_corpus"]
            self.assertIsNotNone(corpus)
            self.assertEqual(corpus.source_segment_ids, ["table", "row"])
            embedded = metric_embeddings(corpus)
            self.assertIsNotNone(embedded)
            self.assertEqual(embedded[0].shape[0], len(corpus.retrieval_views))
            self.assertEqual(embedded[1], [view.view_id for view in corpus.retrieval_views])

    def test_corrupt_optional_sidecar_does_not_break_canonical_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            report = _report()
            paths = manager.save_report_artifacts("report", report)
            manifest_path = Path(paths["metric_retrieval_manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            embeddings_path = manager.embeddings_outputs / manifest["embeddings_file"]
            embeddings_path.write_bytes(b"not-an-npz")

            loaded = manager.load_report_artifacts(
                "report",
                include_metric_corpus=True,
            )

            self.assertIsNotNone(loaded)
            self.assertEqual([segment.segment_id for segment in loaded["segments"]], ["table", "row"])
            self.assertEqual(loaded["embedding_matrix"].shape, (2, 2))
            self.assertIsNone(loaded["metric_retrieval_corpus"])

    def test_delete_report_artifacts_removes_both_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            paths = manager.save_report_artifacts("report", _report())

            removed = manager.delete_report_artifacts("report")

            self.assertTrue(removed)
            self.assertFalse(any(Path(path).exists() for path in paths.values()))
            self.assertEqual(
                list(manager.embeddings_outputs.glob("report_metric_retrieval_*")),
                [],
            )

    def test_age_cleanup_removes_metric_sidecar_with_report(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            pdf_path = manager.processed_reports / "report.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            manager.metadata = {
                "files": {
                    "report": {
                        "file_id": "report",
                        "file_type": "report",
                        "file_path": str(pdf_path),
                        "upload_time": "2000-01-01T00:00:00",
                        "status": "processed",
                    }
                },
                "sessions": {},
            }
            manager._save_metadata()
            manager.save_report_artifacts("report", _report())

            manager.cleanup_old_files(days=30)

            self.assertFalse(pdf_path.exists())
            self.assertNotIn("report", manager.metadata["files"])
            self.assertEqual(
                list(manager.embeddings_outputs.glob("report_metric_retrieval_*")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
