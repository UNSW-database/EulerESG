from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from esg_encoding import cross_analysis
from esg_encoding.content_revision import bump_document_content_revision
from esg_encoding.models import (
    DocumentContent,
    ProcessingConfig,
    ReportContent,
    TextSegment,
)
from esg_encoding.retrieval.hipporag.hooks import warm_hipporag_after_upload
from esg_encoding.retrieval.hipporag import patch as hipporag_patch
from esg_encoding.retrieval.hipporag.retriever import HippoRAGRetriever
from esg_encoding.retrieval.hipporag.settings import (
    HippoRAGSettings,
    resolve_hipporag_embedding_model_name,
    versioned_hipporag_cache_root,
)


def _report(document_id: str = "report-a") -> ReportContent:
    document = DocumentContent(
        document_id=document_id,
        file_path=f"{document_id}.pdf",
        segments=[
            TextSegment(
                segment_id="P1_S1",
                content="Scope 1 emissions were 10 tCO2e.",
                page_number=1,
                position_y=0,
            )
        ],
        markdown_content="",
    )
    return ReportContent(
        document_id=document_id,
        document_content=document,
        embeddings=[],
    )


def _identity_rerank(*, query, snippets, top_k):  # noqa: ARG001
    return [
        SimpleNamespace(idx=index, score=1.0 - index / 100)
        for index, _ in enumerate(snippets[:top_k])
    ]


class HippoRAGRetrieverContractTests(unittest.TestCase):
    def _ready_retriever(self, cache_root: Path):
        settings = replace(
            HippoRAGSettings(),
            cache_root=cache_root,
            top_k_docs=7,
            max_segment_ids_for_context=3,
            max_union_candidates=50,
        )
        retriever = HippoRAGRetriever(ProcessingConfig(), settings=settings)
        save_dir = retriever._save_dir("report-a")
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / ".ready").write_text("ready", encoding="utf-8")
        rag = Mock()
        rag.retrieve.return_value = {
            "retrieved_docs": [
                "__HIPPO_SEGMENT_IDS__: "
                + ",".join(f"P1_S{index}" for index in range(1, 11))
            ]
        }
        retriever._rag_cache["report-a"] = rag
        retriever.ensure_index = Mock(return_value=True)
        return retriever, rag

    def test_explicit_top_k_reaches_hipporag_and_widens_result_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever, rag = self._ready_retriever(Path(temp_dir))
            with patch.object(retriever, "_load_meta", return_value=Mock()):
                segment_ids = retriever.retrieve_segment_ids(
                    "report-a",
                    _report(),
                    "scope emissions",
                    top_k=5,
                )

        rag.retrieve.assert_called_once_with(
            queries=["scope emissions"],
            num_to_retrieve=5,
        )
        self.assertEqual(segment_ids, [f"P1_S{index}" for index in range(1, 6)])

    def test_omitted_top_k_preserves_chat_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever, rag = self._ready_retriever(Path(temp_dir))
            with patch.object(retriever, "_load_meta", return_value=Mock()):
                segment_ids = retriever.retrieve_segment_ids(
                    "report-a",
                    _report(),
                    "scope emissions",
                )

        rag.retrieve.assert_called_once_with(
            queries=["scope emissions"],
            num_to_retrieve=7,
        )
        self.assertEqual(segment_ids, ["P1_S1", "P1_S2", "P1_S3"])

    def test_upstream_failure_returns_empty_results_for_caller_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever, rag = self._ready_retriever(Path(temp_dir))
            rag.retrieve.side_effect = RuntimeError("HippoRAG unavailable")
            with patch.object(retriever, "_load_meta", return_value=Mock()):
                segment_ids = retriever.retrieve_segment_ids(
                    "report-a",
                    _report(),
                    "scope emissions",
                    top_k=5,
                )

        self.assertEqual(segment_ids, [])

    def test_query_solution_docs_are_parsed_into_segment_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever, rag = self._ready_retriever(Path(temp_dir))
            rag.retrieve.return_value = [
                SimpleNamespace(
                    docs=["__HIPPO_SEGMENT_IDS__: P3_S1,P3_S2"]
                )
            ]
            with patch.object(retriever, "_load_meta", return_value=Mock()):
                segment_ids = retriever.retrieve_segment_ids(
                    "report-a",
                    _report(),
                    "scope emissions",
                    top_k=5,
                )

        self.assertEqual(segment_ids, ["P3_S1", "P3_S2"])


class HippoRAGSettingsContractTests(unittest.TestCase):
    def test_enabled_setting_honors_environment_switch(self):
        with patch.dict(os.environ, {"HIPPO_ENABLED": "0"}, clear=False):
            self.assertFalse(HippoRAGSettings().enabled)
        with patch.dict(os.environ, {"HIPPO_ENABLED": "true"}, clear=False):
            self.assertTrue(HippoRAGSettings().enabled)

    def test_unknown_application_embedding_resolves_to_supported_fallback(self):
        settings = replace(
            HippoRAGSettings(),
            embedding_model_name="microsoft/harrier-oss-v1-0.6b",
            fallback_embedding_model_name="facebook/contriever",
        )

        self.assertEqual(
            resolve_hipporag_embedding_model_name(settings),
            "facebook/contriever",
        )
        self.assertIn(
            "facebook_contriever",
            versioned_hipporag_cache_root(settings).name,
        )

    def test_runtime_embedding_uses_preflight_local_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "models--facebook--contriever" / "snapshot"
            snapshot.mkdir(parents=True)
            retriever = HippoRAGRetriever(
                ProcessingConfig(),
                settings=replace(
                    HippoRAGSettings(),
                    embedding_model_name="facebook/contriever",
                ),
            )
            with patch(
                "esg_encoding.shared_embedding_model.prefer_local_model",
                return_value=SimpleNamespace(local_path=str(snapshot)),
            ):
                resolved = retriever._runtime_embedding_model_name()

        self.assertEqual(resolved, str(snapshot.resolve()))


class _FakeIndexRag:
    def __init__(self, save_dir: Path, marker: str, *, fail: bool = False):
        self.save_dir = save_dir
        self.marker = marker
        self.fail = fail

    def index(self, docs):
        if self.fail:
            raise RuntimeError("index failed")
        (self.save_dir / self.marker).write_text(
            "\n".join(docs),
            encoding="utf-8",
        )


class HippoRAGIndexLifecycleTests(unittest.TestCase):
    def _retriever(self, cache_root: Path) -> HippoRAGRetriever:
        settings = replace(
            HippoRAGSettings(),
            cache_root=cache_root,
            min_chars_per_segment=1,
            pack_segments=False,
        )
        return HippoRAGRetriever(ProcessingConfig(), settings=settings)

    def test_same_report_reuses_index_and_revised_content_builds_clean(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever = self._retriever(Path(temp_dir))
            report = _report()
            created = []

            def create(save_dir):
                marker = "first.marker" if not created else "second.marker"
                rag = _FakeIndexRag(save_dir, marker)
                created.append(rag)
                return rag

            with patch.object(retriever, "_create_rag", side_effect=create):
                self.assertTrue(retriever.ensure_index("report-a", report))
                self.assertTrue(retriever.ensure_index("report-a", report))
                self.assertEqual(len(created), 1)

                report.document_content.segments[0].content = (
                    "Scope 1 emissions were 11 tCO2e."
                )
                bump_document_content_revision(report)
                self.assertTrue(retriever.ensure_index("report-a", report))

            save_dir = retriever._save_dir("report-a")
            self.assertEqual(len(created), 2)
            self.assertFalse((save_dir / "first.marker").exists())
            self.assertTrue((save_dir / "second.marker").exists())
            self.assertFalse(list(Path(temp_dir).glob("*.stale-*")))

    def test_failed_rebuild_restores_previous_ready_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever = self._retriever(Path(temp_dir))
            report = _report()
            save_dir = retriever._save_dir("report-a")

            with patch.object(
                retriever,
                "_create_rag",
                return_value=_FakeIndexRag(save_dir, "stable.marker"),
            ):
                self.assertTrue(retriever.ensure_index("report-a", report))

            report.document_content.segments[0].content = (
                "Scope 1 emissions were 99 tCO2e."
            )
            bump_document_content_revision(report)
            with patch.object(
                retriever,
                "_create_rag",
                return_value=_FakeIndexRag(save_dir, "broken.marker", fail=True),
            ):
                self.assertFalse(retriever.ensure_index("report-a", report))

            self.assertTrue((save_dir / ".ready").exists())
            self.assertTrue((save_dir / "stable.marker").exists())
            self.assertFalse((save_dir / "broken.marker").exists())

    def test_file_id_cannot_escape_cache_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            retriever = self._retriever(Path(temp_dir))
            with self.assertRaises(ValueError):
                retriever._save_dir("../outside")


class CrossAnalysisHippoRAGContractTests(unittest.TestCase):
    @staticmethod
    def _artifacts():
        segments = [
            {
                "segment_id": "P1_S1",
                "content": "Scope 1 emissions were 10 tCO2e.",
                "page_number": 1,
            },
            {
                "segment_id": "P1_S2",
                "content": "The emissions reduction target covers 2030.",
                "page_number": 2,
            },
        ]
        return cross_analysis.ReportArtifacts(
            file_id="report-a",
            segments=segments,
            segment_by_id={segment["segment_id"]: segment for segment in segments},
            segment_ids=[segment["segment_id"] for segment in segments],
            embeddings=np.asarray([[1.0, 0.0], [0.8, 0.2]], dtype=np.float32),
        )

    def _patch_pipeline(self, hippo, report_content):
        return (
            patch.object(cross_analysis, "load_artifacts", return_value=self._artifacts()),
            patch.object(
                cross_analysis,
                "_compute_vector_sims",
                return_value=np.asarray([0.9, 0.7], dtype=np.float32),
            ),
            patch.object(cross_analysis, "_keyword_recall", return_value=([], {})),
            patch.object(cross_analysis, "_get_hippo", return_value=hippo),
            patch.object(
                cross_analysis,
                "_get_report_content",
                return_value=report_content,
            ),
            patch.object(
                cross_analysis,
                "rerank",
                create=True,
                side_effect=_identity_rerank,
            ),
            patch.dict(
                cross_analysis.os.environ,
                {
                    "CROSS_VEC_TOPN": "1",
                    "CROSS_HIPPO_ENABLED": "1",
                    "CROSS_HIPPO_TOPN": "5",
                    "CROSS_HIPPO_MIN_VEC": "0",
                    "CROSS_HIPPO_MIN_KW": "0",
                },
                clear=False,
            ),
        )

    def test_cross_analysis_passes_file_report_query_and_top_k(self):
        hippo = Mock()
        hippo.retrieve_segment_ids.return_value = ["P1_S2"]
        report_content = _report()
        patches = self._patch_pipeline(hippo, report_content)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            results = cross_analysis.topn_segments(
                "report-a",
                ["scope emissions"],
                query_text="scope emissions",
                top_n=3,
            )

        hippo.retrieve_segment_ids.assert_called_once_with(
            "report-a",
            report_content,
            query="scope emissions",
            top_k=5,
        )
        self.assertIn("P1_S2", [segment["segment_id"] for segment, _ in results])

    def test_cross_analysis_keeps_vector_results_when_hipporag_fails(self):
        hippo = Mock()
        hippo.retrieve_segment_ids.side_effect = RuntimeError("HippoRAG unavailable")
        report_content = _report()
        patches = self._patch_pipeline(hippo, report_content)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            results = cross_analysis.topn_segments(
                "report-a",
                ["scope emissions"],
                query_text="scope emissions",
                top_n=3,
            )

        self.assertEqual(
            [segment["segment_id"] for segment, _ in results],
            ["P1_S1"],
        )

    def test_injected_retriever_is_reused(self):
        previous = cross_analysis._hippo
        shared = Mock()
        try:
            cross_analysis.set_hipporag_retriever(shared)
            self.assertIs(cross_analysis._get_hippo(), shared)
        finally:
            cross_analysis.set_hipporag_retriever(previous)

    def test_cross_rerank_adapter_uses_shared_segment_reranker(self):
        settings = replace(HippoRAGSettings(), rerank_enabled=True)
        with patch.object(
            cross_analysis,
            "_effective_hippo_settings",
            return_value=settings,
        ), patch.object(
            cross_analysis,
            "rerank_segment_ids",
            return_value=[("1", 0.9), ("0", 0.7)],
        ) as rerank_mock:
            ranked = cross_analysis.rerank(
                query="emissions",
                snippets=["first", "second"],
                top_k=2,
            )

        self.assertEqual([item.idx for item in ranked], [1, 0])
        kwargs = rerank_mock.call_args.kwargs
        self.assertEqual(kwargs["query"], "emissions")
        self.assertEqual(kwargs["get_passage"]("1"), "second")


class HippoRAGWarmHookContractTests(unittest.TestCase):
    def test_enable_patch_attaches_canonical_and_legacy_attributes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                HippoRAGSettings(),
                cache_root=Path(temp_dir),
                enabled=False,
            )
            chatbot = SimpleNamespace(
                _search_relevant_content=lambda query: [query]
            )
            with patch.object(
                hipporag_patch,
                "HippoRAGSettings",
                return_value=settings,
            ):
                hipporag_patch.enable_hipporag(chatbot, ProcessingConfig())

        self.assertIs(chatbot._hipporag_retriever, chatbot._hippo_retriever)
        self.assertIs(chatbot._hipporag_settings, chatbot._hippo_settings)
        self.assertEqual(
            chatbot._hipporag_cache_root,
            chatbot._hippo_cache_root,
        )

    def test_warm_hook_accepts_canonical_and_legacy_attribute_names(self):
        report_content = _report()
        for prefix in ("_hipporag", "_hippo"):
            with self.subTest(prefix=prefix):
                retriever = Mock()
                chatbot = SimpleNamespace(
                    **{
                        f"{prefix}_retriever": retriever,
                        f"{prefix}_settings": SimpleNamespace(enabled=True),
                    }
                )

                warm_hipporag_after_upload(chatbot, report_content)

                retriever.schedule_index.assert_called_once_with(
                    file_id="report-a",
                    report_content=report_content,
                )

    def test_warm_hook_never_fails_the_upload(self):
        report_content = _report()
        retriever = Mock()
        retriever.schedule_index.side_effect = RuntimeError("index failed")
        chatbot = SimpleNamespace(
            _hipporag_retriever=retriever,
            _hipporag_settings=SimpleNamespace(enabled=True),
        )

        warm_hipporag_after_upload(chatbot, report_content)

        retriever.schedule_index.assert_called_once()


if __name__ == "__main__":
    unittest.main()
