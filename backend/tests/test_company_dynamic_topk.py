from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from esg_encoding.company_registry import CompanyRegistry, build_scope_config
from esg_encoding.company_reports import build_company_report_content
from esg_encoding.file_manager import file_manager
from esg_encoding.models import (
    DocumentContent,
    ProcessingConfig,
    ReportContent,
    RetrievalResult,
    TextSegment,
)
from esg_encoding.retrieval.dual_channel import DualChannelRetriever
from esg_encoding.retrieval.scoring import (
    compute_dynamic_top_k,
    compute_rerank_pool_k,
)


def _result(index: int, report_id: str = "report-a") -> RetrievalResult:
    return RetrievalResult(
        segment_id=f"segment-{index}",
        content=f"Metric evidence {index}: {index + 1}%",
        page_number=index + 1,
        score=max(0.1, 0.99 - index / 1000),
        retrieval_type="bm25+real_data_evidence",
        matched_keywords=["metric"],
        metric_id="metric-1",
        source_report_id=report_id,
    )


class DynamicTopKTests(unittest.TestCase):
    def test_expected_dynamic_windows(self):
        expected = {
            30: (30, 30),
            46: (46, 46),
            80: (50, 75),
            150: (53, 80),
            300: (57, 86),
            800: (63, 95),
        }
        with patch.dict(
            os.environ,
            {
                "REPORT_DYNAMIC_TOPK_MIN": "46",
                "REPORT_DYNAMIC_TOPK_LOG_FACTOR": "4",
                "REPORT_RERANK_POOL_MULTIPLIER": "1.5",
            },
            clear=False,
        ):
            for qualified, values in expected.items():
                target = compute_dynamic_top_k(qualified)
                pool = compute_rerank_pool_k(qualified, target)
                self.assertEqual((target, pool), values)

    def test_qwen_receives_only_computed_pool(self):
        config = ProcessingConfig()
        retriever = DualChannelRetriever(config)
        candidates = [
            _result(index, "report-a" if index % 2 == 0 else "report-b")
            for index in range(150)
        ]
        metric = SimpleNamespace(
            metric_id="metric-1",
            metric_name="Employee engagement as a percentage",
            metric_code="TC-SI-330a.2",
            unit="Percentage (%)",
            sasb_category="Quantitative",
            sasb_type="Sustainability Disclosure Topics & Metrics",
            sasb_topic="Workforce",
            definition="Employee engagement as a percentage",
            description="",
            keywords=[],
        )
        report = ReportContent(
            document_id="company-1",
            document_content=DocumentContent(
                document_id="company-1",
                file_path="company:company-1",
                segments=[],
                markdown_content="",
            ),
            embeddings=[],
        )
        profile = SimpleNamespace(anchor_terms=["employee engagement"])

        with (
            patch(
                "esg_encoding.retrieval.dual_channel.exact_metric_rerank",
                return_value=candidates,
            ),
            patch.object(
                retriever,
                "_prepare_unified_rerank_candidates",
                return_value=candidates,
            ),
            patch.object(
                retriever.semantic_retriever,
                "rerank_candidates",
                side_effect=lambda values, _metric: list(values),
            ) as rerank,
        ):
            output = retriever._combine_results(
                keyword_results=candidates,
                semantic_results=[],
                metric=metric,
                report_content=report,
                channel_results={"bm25": candidates},
                profile=profile,
            )

        reranked_candidates = rerank.call_args.args[0]
        self.assertEqual(len(reranked_candidates), 80)
        self.assertEqual(len(output), 53)
        self.assertEqual(
            retriever._dynamic_window_by_metric["metric-1"],
            {"qualified_total": 150, "rerank_pool_k": 80, "target_k": 53},
        )

    def test_balanced_pool_reserves_relevant_reports_without_noise_quota(self):
        candidates = [
            _result(index, "report-a") for index in range(60)
        ]
        candidates.extend(
            _result(100 + index, "report-b").model_copy(
                update={"score": 0.70 - index / 100}
            )
            for index in range(10)
        )
        candidates.extend(
            _result(200 + index, "report-noise").model_copy(
                update={
                    "score": 0.01,
                    "retrieval_type": "semantic",
                }
            )
            for index in range(20)
        )

        selected = DualChannelRetriever._select_balanced_rerank_pool(candidates, 30)
        report_ids = [item.source_report_id for item in selected]

        self.assertEqual(len(selected), 30)
        self.assertIn("report-b", report_ids)
        self.assertNotIn("report-noise", report_ids)

    def test_internal_link_target_is_namespaced_by_report(self):
        source = TextSegment(
            segment_id="report-a::source",
            content="SASB index",
            page_number=1,
            position_y=1,
            segment_type="table_row",
            structured_data={
                "source_report_id": "report-a",
                "pdf_links": [
                    {
                        "link_type": "internal",
                        "source_page": 1,
                        "target_page": 2,
                        "anchor_text": "By the numbers",
                    }
                ],
            },
        )
        target_a = TextSegment(
            segment_id="report-a::target",
            content="Employee engagement 87%",
            page_number=2,
            position_y=1,
            structured_data={"source_report_id": "report-a"},
        )
        target_b = TextSegment(
            segment_id="report-b::target",
            content="Unrelated 50%",
            page_number=2,
            position_y=1,
            structured_data={"source_report_id": "report-b"},
        )
        report = ReportContent(
            document_id="company-1",
            document_content=DocumentContent(
                document_id="company-1",
                file_path="company:company-1",
                segments=[source, target_a, target_b],
                markdown_content="",
            ),
            embeddings=[],
        )
        trigger = RetrievalResult(
            segment_id=source.segment_id,
            content=source.content,
            page_number=1,
            score=0.9,
            retrieval_type="exact_code",
            metric_id="metric-1",
            source_report_id="report-a",
        )
        targets = DualChannelRetriever(ProcessingConfig())._linked_page_targets(
            report, [trigger]
        )
        self.assertIn(("report-a", 2), targets)
        self.assertNotIn(("report-b", 2), targets)


class CompanyCorpusTests(unittest.TestCase):
    def test_virtual_corpus_namespaces_segments_tables_and_embeddings(self):
        segment_a = TextSegment(
            segment_id="segment-1",
            content="FY2024 87%",
            page_number=2,
            position_y=1,
            segment_type="table_cell",
            source_table_id="table-1",
            structured_data={"table_id": "table-1", "row_index": 1},
        )
        segment_b = segment_a.model_copy(update={"content": "FY2023 84%"})
        artifacts = {
            "report-a": {
                "segments": [segment_a],
                "embedding_matrix": np.asarray([[1.0, 0.0]], dtype=np.float32),
                "embedding_segment_ids": ["segment-1"],
            },
            "report-b": {
                "segments": [segment_b],
                "embedding_matrix": np.asarray([[0.0, 1.0]], dtype=np.float32),
                "embedding_segment_ids": ["segment-1"],
            },
        }
        original_files = file_manager.metadata.setdefault("files", {})
        saved = dict(original_files)
        try:
            original_files.update(
                {
                    "report-a": {
                        "original_name": "Acme-2024.pdf",
                        "report_year": 2024,
                        "page_count": 10,
                    },
                    "report-b": {
                        "original_name": "Acme-2023.pdf",
                        "report_year": 2023,
                        "page_count": 9,
                    },
                }
            )
            with patch.object(
                file_manager,
                "load_report_artifacts",
                side_effect=lambda file_id: artifacts[file_id],
            ):
                report, sources = build_company_report_content(
                    {
                        "company_id": "company-1",
                        "report_ids": ["report-a", "report-b"],
                    }
                )
        finally:
            file_manager.metadata["files"] = saved

        ids = [segment.segment_id for segment in report.document_content.segments]
        table_ids = [segment.source_table_id for segment in report.document_content.segments]
        self.assertEqual(ids, ["report-a::segment-1", "report-b::segment-1"])
        self.assertEqual(table_ids, ["report-a::table-1", "report-b::table-1"])
        self.assertEqual([item["report_year"] for item in sources], [2024, 2023])
        cache_segments, cache_matrix = getattr(
            report, "_semantic_retrieval_embedding_cache"
        )
        self.assertEqual(len(cache_segments), 2)
        self.assertEqual(cache_matrix.shape, (2, 2))

    def test_registry_enforces_scope_and_report_limit(self):
        scope = build_scope_config(
            framework="SASB",
            industry="Technology & Communications",
            semi_industry="Software & IT Services",
            gri_sector=None,
            gri_topic=None,
            scope_slugs=["Software & IT Services"],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = CompanyRegistry(Path(temp_dir) / "companies.json")
            company = registry.create_company(
                user_id=7,
                company_name="Acme",
                scope_config=scope,
            )
            registry.validate_upload(
                company_id=company["company_id"],
                user_id=7,
                scope_config=scope,
                file_hashes=["hash-a", "hash-b"],
            )
            registry.create_batch(
                company_id=company["company_id"],
                user_id=7,
                upload_mode="multi",
                file_ids=[f"report-{index}" for index in range(7)],
                report_years={},
            )
            with self.assertRaisesRegex(ValueError, "at most 8"):
                registry.validate_upload(
                    company_id=company["company_id"],
                    user_id=7,
                    scope_config=scope,
                    file_hashes=["hash-c", "hash-d"],
                )
            mismatched = dict(scope)
            mismatched["scope_slugs"] = ["Hardware"]
            with self.assertRaisesRegex(ValueError, "same framework"):
                registry.validate_upload(
                    company_id=company["company_id"],
                    user_id=7,
                    scope_config=mismatched,
                    file_hashes=["hash-c"],
                )

    def test_batch_creation_rechecks_duplicate_hash_atomically(self):
        scope = build_scope_config(
            framework="SASB",
            industry="Technology & Communications",
            semi_industry="Software & IT Services",
            gri_sector=None,
            gri_topic=None,
            scope_slugs=["Software & IT Services"],
        )
        files = file_manager.metadata.setdefault("files", {})
        saved = dict(files)
        try:
            files.update(
                {
                    "report-a": {"file_hash": "same-hash"},
                    "report-b": {"file_hash": "same-hash"},
                }
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                registry = CompanyRegistry(Path(temp_dir) / "companies.json")
                company = registry.create_company(
                    user_id=7,
                    company_name="Acme",
                    scope_config=scope,
                )
                registry.create_batch(
                    company_id=company["company_id"],
                    user_id=7,
                    upload_mode="single",
                    file_ids=["report-a"],
                    report_years={"report-a": 2024},
                )
                with self.assertRaisesRegex(ValueError, "already contains"):
                    registry.create_batch(
                        company_id=company["company_id"],
                        user_id=7,
                        upload_mode="single",
                        file_ids=["report-b"],
                        report_years={"report-b": 2023},
                    )
        finally:
            file_manager.metadata["files"] = saved

if __name__ == "__main__":
    unittest.main()
