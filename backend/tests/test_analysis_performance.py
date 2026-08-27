from __future__ import annotations

import os
import threading
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from esg_encoding.disclosure_inference import DisclosureInferenceEngine
from esg_encoding.content_embedder import ContentEmbedder
from esg_encoding.content_revision import bump_document_content_revision
from esg_encoding.models import (
    DocumentContent,
    MetricRetrievalResult,
    ProcessingConfig,
    ReportContent,
    RetrievalResult,
    SegmentEmbedding,
    TextSegment,
)
from esg_encoding.retrieval.keyword import KeywordRetriever
from esg_encoding.retrieval.reranker import _QwenReranker
from esg_encoding.retrieval.semantic import SemanticRetriever
from esg_encoding.retrieval.evidence_retriever import (
    iter_metric_collection_results,
    retrieve_metric_collection,
)
from esg_encoding.services.common import (
    _apply_assessment_year_selection,
    _compact_assessment_payload,
    _create_enhanced_knowledge_base,
    _prepare_metrics_for_retrieval,
)


def _report(segments: list[TextSegment]) -> ReportContent:
    document = DocumentContent(
        document_id="performance-test",
        file_path="performance-test.pdf",
        segments=segments,
        markdown_content="",
    )
    return ReportContent(
        document_id=document.document_id,
        document_content=document,
        embeddings=[],
    )


def _metric(index: int):
    return SimpleNamespace(
        metric_id=f"metric-{index}",
        metric_name=f"Metric {index}",
        metric_code=f"TC-X-{index}",
        sasb_category="Quantitative",
        sasb_topic="",
        sasb_type="Quantitative",
        unit="%",
        definition="",
    )


class QwenRerankerBatchTests(unittest.TestCase):
    def test_compute_score_honors_batch_and_text_limits(self):
        reranker = object.__new__(_QwenReranker)
        reranker.batch_size = 8
        reranker.max_instruction_chars = 9
        reranker.max_query_chars = 7
        reranker.max_passage_chars = 11
        batches: list[list[str]] = []

        def score_batch(formatted):
            values = list(formatted)
            batches.append(values)
            return [0.75] * len(values)

        reranker._score_formatted_batch = score_batch
        scores = reranker.compute_score(
            [["q" * 30, "d" * 40] for _ in range(18)],
            normalize=True,
            instruction="i" * 20,
        )

        self.assertEqual([len(batch) for batch in batches], [8, 8, 2])
        self.assertEqual(scores, [0.75] * 18)
        first = batches[0][0]
        self.assertIn("<Instruct>: " + "i" * 9, first)
        self.assertIn("<Query>: " + "q" * 7, first)
        self.assertTrue(first.endswith("<Document>: " + "d" * 11))

    def test_long_passages_reduce_microbatch_without_changing_score_order(self):
        reranker = object.__new__(_QwenReranker)
        reranker.batch_size = 8
        reranker.max_instruction_chars = 20
        reranker.max_query_chars = 20
        reranker.max_passage_chars = 40960
        reranker.max_padded_chars_per_batch = 200
        batch_sizes: list[int] = []

        def score_batch(formatted):
            values = list(formatted)
            batch_sizes.append(len(values))
            return [float(value.rsplit("IDX_", 1)[1]) for value in values]

        reranker._score_formatted_batch = score_batch
        docs = ["x" * 10 + "IDX_0", "x" * 10 + "IDX_1", "x" * 120 + "IDX_2", "x" * 10 + "IDX_3"]
        scores = reranker.compute_score(
            [["q", document] for document in docs],
            normalize=True,
            instruction="i",
        )

        self.assertEqual(batch_sizes, [2, 1, 1])
        self.assertEqual(scores, [0.0, 1.0, 2.0, 3.0])

    def test_unified_reranker_scores_every_retrieval_channel_once(self):
        channel_types = [
            "rrf:exact_code",
            "rrf:exact_alias",
            "rrf:bm25",
            "rrf:semantic",
            "rrf:linked_page+bm25",
        ]
        candidates = [
            RetrievalResult(
                segment_id=f"candidate-{index}",
                content=f"Candidate evidence {index}",
                page_number=index + 1,
                score=0.2,
                retrieval_type=retrieval_type,
                metric_id="metric-1",
            )
            for index, retrieval_type in enumerate(channel_types)
        ]
        seen_pairs = []

        def compute_score(pairs, normalize=True, instruction=None):
            seen_pairs.extend(pairs)
            return [0.1 * (index + 1) for index in range(len(pairs))]

        retriever = object.__new__(SemanticRetriever)
        retriever.config = SimpleNamespace(use_reranker=True)
        retriever.reranker = SimpleNamespace(compute_score=compute_score)
        retriever._reranker_initialized = True
        retriever._reranker_lock = threading.Lock()

        reranked = retriever.rerank_candidates(candidates, _metric(1))

        self.assertEqual(len(seen_pairs), len(channel_types))
        self.assertEqual(
            {pair[1].split("Retrieval channels: ", 1)[1].splitlines()[0] for pair in seen_pairs},
            set(channel_types),
        )
        self.assertNotIn("Expected unit", seen_pairs[0][0])
        self.assertNotIn("%", seen_pairs[0][0])
        self.assertTrue(all("qwen_unified_rerank" in item.retrieval_type for item in reranked))
        self.assertEqual(reranked[0].segment_id, "candidate-4")


class RetrievalCacheTests(unittest.TestCase):
    @staticmethod
    def _semantic_retriever() -> SemanticRetriever:
        retriever = object.__new__(SemanticRetriever)
        retriever.config = ProcessingConfig(similarity_threshold=0.0)
        retriever.embedding_model = object()
        retriever.reranker = None
        retriever._reranker_initialized = True
        retriever.reranker_top_k = 10
        retriever._reranker_lock = threading.Lock()
        return retriever

    @staticmethod
    def _semantic_segment() -> TextSegment:
        return TextSegment(
            segment_id="semantic-segment",
            content="Total energy consumed was 10 GJ.",
            page_number=1,
            position_y=0,
        )

    def test_semantic_retrieval_accepts_legacy_embedding_lists(self):
        segment = self._semantic_segment()
        report = _report([segment])
        report.embeddings = [
            SegmentEmbedding(segment_id=segment.segment_id, embedding=[1.0, 0.0])
        ]

        with patch(
            "esg_encoding.retrieval.semantic.encode_query_texts",
            return_value=np.asarray([[1.0, 0.0]], dtype=np.float32),
        ):
            results = self._semantic_retriever().search_by_semantic(
                report,
                _metric(1),
                apply_reranker=False,
            )

        self.assertEqual(results[0].segment_id, segment.segment_id)
        cached_matrix = getattr(report, "_semantic_retrieval_embedding_cache")[1]
        self.assertEqual(cached_matrix.shape, (1, 2))
        self.assertEqual(cached_matrix.dtype, np.float32)

    def test_semantic_retrieval_reuses_native_numpy_matrix(self):
        segment = self._semantic_segment()
        report = _report([segment])
        native_matrix = np.asarray([[1.0, 0.0]], dtype=np.float32)
        object.__setattr__(report, "_embedding_matrix", native_matrix)
        object.__setattr__(report, "_embedding_segment_ids", [segment.segment_id])

        with patch(
            "esg_encoding.retrieval.semantic.encode_query_texts",
            return_value=np.asarray([[1.0, 0.0]], dtype=np.float32),
        ):
            results = self._semantic_retriever().search_by_semantic(
                report,
                _metric(1),
                apply_reranker=False,
            )

        self.assertEqual(results[0].segment_id, segment.segment_id)
        self.assertIs(getattr(report, "_semantic_retrieval_embedding_cache")[1], native_matrix)

    def test_semantic_retrieval_returns_empty_for_empty_numpy_matrix(self):
        report = _report([])
        object.__setattr__(report, "_embedding_matrix", np.empty((0, 2), dtype=np.float32))
        object.__setattr__(report, "_embedding_segment_ids", [])

        with patch(
            "esg_encoding.retrieval.semantic.encode_query_texts",
            return_value=np.asarray([[1.0, 0.0]], dtype=np.float32),
        ):
            results = self._semantic_retriever().search_by_semantic(
                report,
                _metric(1),
                apply_reranker=False,
            )

        self.assertEqual(results, [])

    def test_bm25_corpus_is_tokenized_once_per_report(self):
        segments = [
            TextSegment(
                segment_id=f"segment-{index}",
                content=f"Energy disclosure {index}",
                page_number=1,
                position_y=float(index),
            )
            for index in range(3)
        ]
        report = _report(segments)
        retriever = KeywordRetriever(ProcessingConfig())
        original = retriever._segment_tokens
        calls = 0

        def counted(segment):
            nonlocal calls
            calls += 1
            return original(segment)

        retriever._segment_tokens = counted
        first = retriever._get_bm25_corpus(report)
        second = retriever._get_bm25_corpus(report)

        self.assertEqual(calls, 3)
        self.assertIs(first[1], second[1])

        # In-place OCR/table correction keeps both list identity and length.
        # The content revision must still invalidate the tokenized corpus.
        segments[0].content = "Water disclosure changed in place"
        bump_document_content_revision(report)
        third = retriever._get_bm25_corpus(report)
        self.assertEqual(calls, 6)
        self.assertIsNot(second[1], third[1])
        self.assertIn("water", third[1][0])

    def test_same_document_id_and_revision_do_not_share_keyword_cache(self):
        first_report = _report(
            [TextSegment(segment_id="a", content="Energy", page_number=1, position_y=0)]
        )
        second_report = _report(
            [TextSegment(segment_id="b", content="Water", page_number=1, position_y=0)]
        )
        retriever = KeywordRetriever(ProcessingConfig())
        first = retriever._get_bm25_corpus(first_report)
        # Simulate a copied private cache on a newly loaded object with the
        # same stable document ID and revision.
        object.__setattr__(
            second_report,
            "_keyword_bm25_cache",
            getattr(first_report, "_keyword_bm25_cache"),
        )

        second = retriever._get_bm25_corpus(second_report)

        self.assertIsNot(first[1], second[1])
        self.assertEqual(second[0][0].segment_id, "b")
        self.assertIn("water", second[1][0])

    def test_disclosure_segment_cache_invalidates_after_in_place_edit(self):
        segment = TextSegment(
            segment_id="before",
            content="Original evidence",
            page_number=1,
            position_y=0,
        )
        report = _report([segment])
        engine = object.__new__(DisclosureInferenceEngine)
        first = engine._get_report_segment_cache(report)
        second = engine._get_report_segment_cache(report)
        self.assertIs(first, second)

        segment.segment_id = "after"
        segment.content = "Corrected evidence"
        bump_document_content_revision(report)
        third = engine._get_report_segment_cache(report)
        self.assertIsNot(second, third)
        self.assertNotIn("before", third["by_id"])
        self.assertIs(third["by_id"]["after"], segment)

    def test_metric_dense_queries_are_encoded_as_one_batch(self):
        retriever = self._semantic_retriever()
        segment = self._semantic_segment()
        report = _report([segment])
        object.__setattr__(
            report,
            "_embedding_matrix",
            np.asarray([[1.0, 0.0]], dtype=np.float32),
        )
        object.__setattr__(report, "_embedding_segment_ids", [segment.segment_id])
        pairs = [(_metric(index), None) for index in range(1, 4)]

        def encode(_model, texts, **_kwargs):
            return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

        with patch(
            "esg_encoding.retrieval.semantic.encode_query_texts",
            side_effect=encode,
        ) as mocked_encode:
            retriever.prepare_metric_queries(pairs)
            for metric, expansion in pairs:
                results = retriever.search_by_semantic(
                    report,
                    metric,
                    expansion,
                    apply_reranker=False,
                )
                self.assertEqual(results[0].segment_id, segment.segment_id)

        self.assertEqual(mocked_encode.call_count, 1)
        self.assertEqual(len(mocked_encode.call_args.args[1]), 3)

    def test_metric_results_are_reused_across_scopes_with_same_identity(self):
        report = _report([TextSegment(segment_id="s1", content="Energy", page_number=1, position_y=0)])
        collection = SimpleNamespace(metrics=[_metric(1)], semantic_expansions=[])
        expected = SimpleNamespace(metric_id="metric-1")
        calls = 0

        def fake_retrieve(_self, _report, _metric, _expansion=None):
            nonlocal calls
            calls += 1
            return expected

        with patch("esg_encoding.retrieval.evidence_retriever.enrich_document_with_pdf_links"), patch.object(
            __import__("esg_encoding.retrieval.dual_channel", fromlist=["DualChannelRetriever"]).DualChannelRetriever,
            "retrieve_for_metric",
            fake_retrieve,
        ):
            first = retrieve_metric_collection(report, collection, ProcessingConfig())
            second = retrieve_metric_collection(report, collection, ProcessingConfig())
        self.assertEqual(calls, 1)
        self.assertIs(first[0], second[0])

        report.document_content.segments[0].content = "Water changed in place"
        bump_document_content_revision(report)
        with patch("esg_encoding.retrieval.evidence_retriever.enrich_document_with_pdf_links"), patch.object(
            __import__("esg_encoding.retrieval.dual_channel", fromlist=["DualChannelRetriever"]).DualChannelRetriever,
            "retrieve_for_metric",
            fake_retrieve,
        ):
            third = retrieve_metric_collection(report, collection, ProcessingConfig())
        self.assertEqual(calls, 2)
        self.assertIs(third[0], expected)

    def test_metric_result_stream_retrieves_one_metric_per_iteration(self):
        report = _report(
            [
                TextSegment(
                    segment_id="s1",
                    content="Energy",
                    page_number=1,
                    position_y=0,
                )
            ]
        )
        metrics = [_metric(1), _metric(2)]
        collection = SimpleNamespace(metrics=metrics, semantic_expansions=[])
        retrieved_metric_ids: list[str] = []

        def fake_retrieve(_self, _report, metric, _expansion=None):
            retrieved_metric_ids.append(metric.metric_id)
            return MetricRetrievalResult(
                metric_id=metric.metric_id,
                metric_name=metric.metric_name,
                metric_code=metric.metric_code,
            )

        with patch(
            "esg_encoding.retrieval.evidence_retriever.enrich_document_with_pdf_links"
        ), patch.object(
            __import__(
                "esg_encoding.retrieval.dual_channel",
                fromlist=["DualChannelRetriever"],
            ).DualChannelRetriever,
            "retrieve_for_metric",
            fake_retrieve,
        ):
            stream = iter_metric_collection_results(
                report,
                collection,
                ProcessingConfig(use_metric_retrieval_corpus=False),
            )
            self.assertEqual(retrieved_metric_ids, [])
            self.assertEqual(stream.results, [])

            first = next(stream)
            self.assertEqual(retrieved_metric_ids, ["metric-1"])
            self.assertEqual([item.metric_id for item in stream.results], ["metric-1"])

            second = next(stream)
            self.assertEqual(retrieved_metric_ids, ["metric-1", "metric-2"])
            self.assertEqual(
                [item.metric_id for item in stream.results],
                ["metric-1", "metric-2"],
            )
            with self.assertRaises(StopIteration):
                next(stream)

        self.assertEqual(first.metric_id, "metric-1")
        self.assertEqual(second.metric_id, "metric-2")


class ContentEmbedderMatrixTests(unittest.TestCase):
    def test_new_document_keeps_only_native_matrix(self):
        document = DocumentContent(
            document_id="matrix-report",
            file_path="matrix-report.pdf",
            segments=[
                TextSegment(segment_id="s1", content="One", page_number=1, position_y=0),
                TextSegment(segment_id="s2", content="Two", page_number=1, position_y=1),
            ],
            markdown_content="",
        )
        embedder = object.__new__(ContentEmbedder)
        embedder.config = ProcessingConfig()
        embedder.logger = Mock()
        embedder.model = SimpleNamespace(
            encode=lambda texts, **_kwargs: np.asarray(
                [[1.0, 0.0], [0.0, 1.0]][: len(texts)], dtype=np.float32
            )
        )

        report = embedder.embed_document(document)

        self.assertEqual(report.embeddings, [])
        matrix = getattr(report, "_embedding_matrix")
        self.assertEqual(matrix.shape, (2, 2))
        self.assertEqual(matrix.dtype, np.float32)
        self.assertEqual(getattr(report, "_embedding_segment_ids"), ["s1", "s2"])
        self.assertEqual(
            [item[0] for item in embedder.compute_similarity("query", report, top_k=2)],
            ["s1", "s2"],
        )

    def test_enhanced_chat_content_retains_native_report_matrix(self):
        segment = TextSegment(
            segment_id="report-segment",
            content="Report evidence",
            page_number=1,
            position_y=0,
        )
        report = _report([segment])
        matrix = np.asarray([[1.0, 0.0]], dtype=np.float32)
        object.__setattr__(report, "_embedding_matrix", matrix)
        object.__setattr__(report, "_embedding_segment_ids", [segment.segment_id])
        assessment = SimpleNamespace(
            report_id="report-1",
            total_metrics_analyzed=0,
            overall_compliance_score=0.0,
            disclosure_summary={},
            metric_analyses=[],
        )

        enhanced = _create_enhanced_knowledge_base(assessment, report)

        self.assertIs(getattr(enhanced, "_embedding_matrix"), matrix)
        self.assertEqual(
            getattr(enhanced, "_embedding_segment_ids"),
            [segment.segment_id],
        )


class MetricPreparationTests(unittest.TestCase):
    def test_profiled_metrics_skip_unused_llm_expansion(self):
        metrics = SimpleNamespace(metrics=[_metric(1), _metric(2)])
        processor = SimpleNamespace(process_metric_collection=lambda value: self.fail("unexpected expansion"))
        with patch.dict(os.environ, {"REPORT_SKIP_PROFILED_METRIC_EXPANSION": "true"}):
            with patch("esg_encoding.services.common.find_metric_profile", return_value=object()):
                prepared = _prepare_metrics_for_retrieval(processor, metrics)
        self.assertIs(prepared, metrics)


class DisclosureConcurrencyTests(unittest.TestCase):
    def test_retrieval_and_disclosure_overlap_with_single_llm_worker(self):
        engine = object.__new__(DisclosureInferenceEngine)
        metrics = [_metric(1), _metric(2)]
        collection = SimpleNamespace(metrics=metrics)
        report = _report(
            [
                TextSegment(
                    segment_id="report-segment",
                    content="Report content",
                    page_number=1,
                    position_y=1,
                )
            ]
        )
        first_analysis_started = threading.Event()
        second_retrieval_requested = threading.Event()

        def fake_analysis(self, metric, retrieval_result, report_content):
            if metric.metric_id == "metric-1":
                first_analysis_started.set()
                if not second_retrieval_requested.wait(2.0):
                    raise AssertionError(
                        "Second retrieval did not overlap the first disclosure analysis"
                    )
            return self._not_disclosed_analysis_for_metric(metric, "test")

        def retrieval_stream():
            yield MetricRetrievalResult(
                metric_id=metrics[0].metric_id,
                metric_name=metrics[0].metric_name,
                metric_code=metrics[0].metric_code,
            )
            if not first_analysis_started.wait(2.0):
                raise AssertionError(
                    "Disclosure analysis did not start before the next retrieval"
                )
            second_retrieval_requested.set()
            yield MetricRetrievalResult(
                metric_id=metrics[1].metric_id,
                metric_name=metrics[1].metric_name,
                metric_code=metrics[1].metric_code,
            )

        engine._analyze_collection_metric = MethodType(fake_analysis, engine)
        with patch.dict(
            os.environ,
            {"REPORT_DISCLOSURE_LLM_CONCURRENCY": "1"},
        ):
            assessment = engine.analyze_compliance(
                retrieval_stream(),
                report,
                all_metrics=collection,
                framework="SASB",
            )

        self.assertTrue(second_retrieval_requested.is_set())
        self.assertEqual(
            [analysis.metric_id for analysis in assessment.metric_analyses],
            [metric.metric_id for metric in metrics],
        )

    def test_metric_analysis_is_parallel_and_result_order_is_stable(self):
        engine = object.__new__(DisclosureInferenceEngine)
        metrics = [_metric(index) for index in range(16)]
        collection = SimpleNamespace(metrics=metrics)
        report = _report(
            [
                TextSegment(
                    segment_id="report-segment",
                    content="Report content",
                    page_number=1,
                    position_y=1,
                )
            ]
        )
        worker_names: set[str] = set()
        worker_lock = threading.Lock()
        active_workers = 0
        max_active_workers = 0

        def fake_analysis(self, metric, retrieval_result, report_content):
            nonlocal active_workers, max_active_workers
            with worker_lock:
                worker_names.add(threading.current_thread().name)
                active_workers += 1
                max_active_workers = max(max_active_workers, active_workers)
            time.sleep(0.05)
            with worker_lock:
                active_workers -= 1
            return self._not_disclosed_analysis_for_metric(metric, "test")

        engine._analyze_collection_metric = MethodType(fake_analysis, engine)
        with patch.dict(os.environ, {"REPORT_DISCLOSURE_LLM_CONCURRENCY": "8"}):
            assessment = engine.analyze_compliance(
                [],
                report,
                all_metrics=collection,
                framework="SASB",
            )

        self.assertEqual(max_active_workers, 8)
        self.assertEqual(len(worker_names), 8)
        self.assertEqual(
            [analysis.metric_id for analysis in assessment.metric_analyses],
            [metric.metric_id for metric in metrics],
        )


class AssessmentYearSelectionTests(unittest.TestCase):
    def _payload(self):
        return {
            "metric_analyses": [
                {
                    "metric_id": "employee-engagement",
                    "disclosure_status": "fully_disclosed",
                    "value": 87,
                    "page": 108,
                    "context": "FY2024: 87%",
                    "year_values": [
                        {"year": 2022, "value": 81, "unit": "%", "page": 106, "context": "FY2022: 81%"},
                        {"year": 2023, "value": 84, "unit": "%", "page": 107, "context": "FY2023: 84%"},
                        {"year": 2024, "value": 87, "unit": "%", "page": 108, "context": "FY2024: 87%"},
                    ],
                }
            ]
        }

    def test_requested_year_is_projected_without_removing_other_years(self):
        payload = _apply_assessment_year_selection(self._payload(), 2023)
        metric = payload["metric_analyses"][0]
        self.assertEqual(metric["value"], 84)
        self.assertEqual(metric["page"], 107)
        self.assertEqual(metric["selected_year"], 2023)
        self.assertEqual(metric["year_selection_status"], "selected")
        self.assertEqual(len(metric["year_values"]), 3)

    def test_missing_requested_year_does_not_fall_back_to_latest(self):
        payload = _apply_assessment_year_selection(self._payload(), 2021)
        metric = payload["metric_analyses"][0]
        self.assertEqual(metric["value"], "n/a")
        self.assertIsNone(metric["page"])
        self.assertEqual(metric["selected_year"], 2021)
        self.assertEqual(metric["year_selection_status"], "not_available")


class CompactAssessmentPayloadTests(unittest.TestCase):
    def test_compact_view_keeps_ui_fields_and_drops_export_duplicates(self):
        payload = {
            "report_id": "report-1",
            "framework": "SASB",
            "sasb_metric_rows": [{"large": "duplicate export row" * 100}],
            "metric_analyses": [
                {
                    "metric_id": "employee-engagement",
                    "metric_name": "Employee engagement",
                    "metric_code": "TC-SI-330a.2",
                    "disclosure_status": "fully_disclosed",
                    "reasoning": "Disclosed",
                    "value": 87,
                    "page": 108,
                    "context": "Employee engagement: 87%",
                    "simple_definition": "Report the percentage of employees who are engaged.",
                    "definition": "Percentage of employees who are engaged.",
                    "evidence_segments": ["large-segment" * 100],
                    "year_values": [{"year": 2024, "value": 87}],
                    "evidence_sources": [
                        {
                            "asset_id": "asset-1",
                            "caption": "Engagement chart",
                            "confidence": 0.9,
                            "source_type": "linked_page",
                            "data_page": 108,
                            "link_source_page": 102,
                            "target_page": 107,
                            "segment_id": "segment-108",
                            "source_report_id": "source-report-1",
                            "source_report_name": "Source report.pdf",
                            "source_report_year": 2024,
                            "private_debug_payload": "drop-me",
                        },
                        {
                            "review_status": "needs_review",
                            "structure_confidence": 0.8,
                            "conflicts": [{"large": "secret"}, {"large": "secret-2"}],
                        },
                    ],
                    "Value": 87,
                    "Context": "Employee engagement: 87%",
                }
            ],
        }

        compact = _compact_assessment_payload(payload)

        self.assertEqual(compact["response_view"], "compact")
        self.assertNotIn("sasb_metric_rows", compact)
        metric = compact["metric_analyses"][0]
        self.assertEqual(metric["value"], 87)
        self.assertEqual(metric["page"], 108)
        self.assertEqual(metric["context"], "Employee engagement: 87%")
        self.assertEqual(
            metric["simple_definition"],
            "Report the percentage of employees who are engaged.",
        )
        self.assertNotIn("evidence_segments", metric)
        self.assertNotIn("year_values", metric)
        self.assertNotIn("Value", metric)
        self.assertNotIn("private_debug_payload", metric["evidence_sources"][0])
        self.assertEqual(metric["evidence_sources"][0]["data_page"], 108)
        self.assertEqual(metric["evidence_sources"][0]["link_source_page"], 102)
        self.assertEqual(metric["evidence_sources"][0]["target_page"], 107)
        self.assertEqual(metric["evidence_sources"][0]["segment_id"], "segment-108")
        self.assertEqual(metric["evidence_sources"][0]["source_report_id"], "source-report-1")
        self.assertEqual(len(metric["evidence_sources"][1]["conflicts"]), 2)


if __name__ == "__main__":
    unittest.main()
