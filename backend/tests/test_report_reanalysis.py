from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
import numpy as np

from esg_encoding.api.routers import reports as reports_router
from esg_encoding.models import TextSegment
from esg_encoding.services import report_jobs, report_service


class _InlineExecutor:
    """Executor double that preserves the real background entry point."""

    def __init__(self) -> None:
        self.submissions: list[tuple[object, tuple, dict]] = []

    def submit(self, fn, *args, **kwargs) -> Future:
        self.submissions.append((fn, args, kwargs))
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # pragma: no cover - mirrors Executor semantics
            future.set_exception(exc)
        return future


def _valid_artifacts() -> dict:
    segments = [
        TextSegment(
            segment_id="segment-1",
            content="Scope 1 emissions were 10 tCO2e.",
            page_number=3,
            position_y=10.0,
        ),
        TextSegment(
            segment_id="segment-2",
            content="Scope 2 emissions were 12 tCO2e.",
            page_number=3,
            position_y=20.0,
        ),
    ]
    return {
        "segments": segments,
        "embedding_matrix": np.ascontiguousarray(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32
        ),
        "embedding_segment_ids": ["segment-1", "segment-2"],
        "segments_path": "segments.json",
        "embeddings_path": "embeddings.npz",
    }


def _pipeline_spies() -> tuple[SimpleNamespace, SimpleNamespace]:
    embedder = SimpleNamespace(
        embed_document=Mock(name="embed_document"),
        _generate_embeddings=Mock(name="generate_embeddings"),
    )
    encoder = SimpleNamespace(
        encode_pdf=Mock(name="encode_pdf"),
        extractor=SimpleNamespace(extract_pdf=Mock(name="extract_pdf")),
        embedder=embedder,
    )
    standalone_embedder = SimpleNamespace(
        embed_document=Mock(name="standalone_embed_document"),
        _generate_embeddings=Mock(name="standalone_generate_embeddings"),
    )
    return encoder, standalone_embedder


class ReportReanalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pdf_path = Path(self.temp_dir.name) / "existing-report.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4\n")
        self.file_info = {
            "file_id": "report-1",
            "file_type": "report",
            "file_path": str(self.pdf_path),
            "safe_filename": self.pdf_path.name,
            "original_name": "Existing Report.pdf",
            "user_id": 7,
            "status": "processed",
            "framework": "SASB",
            "industry": "Technology & Communications",
            "semi_industry": "Software & IT Services",
            "scope_slugs": ["Software & IT Services"],
        }
        with report_jobs._lock:
            report_jobs._jobs.clear()

    def tearDown(self) -> None:
        with report_jobs._lock:
            report_jobs._jobs.clear()
        self.temp_dir.cleanup()

    async def test_foreign_or_unknown_report_is_not_disclosed(self):
        with (
            patch.object(report_service.file_manager, "get_file_info", return_value=None),
            patch.object(report_service.file_manager, "load_report_artifacts") as load_artifacts,
            patch.object(report_service, "get_report_job_executor") as get_executor,
        ):
            with self.assertRaises(HTTPException) as raised:
                await report_service.reanalyze_report("foreign-report", user_id=99)

        self.assertIn(raised.exception.status_code, {403, 404})
        load_artifacts.assert_not_called()
        get_executor.assert_not_called()

    async def test_missing_persisted_artifacts_returns_conflict(self):
        with (
            patch.object(
                report_service.file_manager,
                "get_file_info",
                return_value=dict(self.file_info),
            ),
            patch.object(
                report_service.file_manager,
                "load_report_artifacts",
                return_value=None,
            ),
            patch.object(report_service, "get_report_job_executor") as get_executor,
        ):
            with self.assertRaises(HTTPException) as raised:
                await report_service.reanalyze_report("report-1", user_id=7)

        self.assertEqual(raised.exception.status_code, 409)
        get_executor.assert_not_called()

    async def test_embedding_row_or_id_mismatch_returns_conflict(self):
        invalid_artifacts = _valid_artifacts()
        invalid_artifacts["embedding_matrix"] = np.ones((1, 3), dtype=np.float32)

        with (
            patch.object(
                report_service.file_manager,
                "get_file_info",
                return_value=dict(self.file_info),
            ),
            patch.object(
                report_service.file_manager,
                "load_report_artifacts",
                return_value=invalid_artifacts,
            ),
            patch.object(report_service, "get_report_job_executor") as get_executor,
        ):
            with self.assertRaises(HTTPException) as raised:
                await report_service.reanalyze_report("report-1", user_id=7)

        self.assertEqual(raised.exception.status_code, 409)
        get_executor.assert_not_called()

    async def test_reprocess_rejects_an_active_reanalysis_job(self):
        active = report_jobs.create_report_job(
            file_id="report-1",
            filename="Existing Report.pdf",
            user_id=7,
        )
        file_info = {
            **self.file_info,
            "reanalysis_job_id": active["job_id"],
        }

        with (
            patch.object(
                report_service.file_manager,
                "get_file_info",
                return_value=file_info,
            ),
            patch.object(report_service, "get_report_job_executor") as get_executor,
        ):
            with self.assertRaises(HTTPException) as raised:
                await report_service.reprocess_report("report-1", user_id=7)

        self.assertEqual(raised.exception.status_code, 409)
        get_executor.assert_not_called()

    async def test_accepted_reanalysis_reaches_success_without_ocr_or_embedding(self):
        executor = _InlineExecutor()
        encoder, standalone_embedder = _pipeline_spies()
        result = {
            "status": "success",
            "message": "Assessment completed from persisted artifacts.",
            "file_id": "report-1",
            "report_id": "report-1",
        }

        with (
            patch.object(
                report_service.file_manager,
                "get_file_info",
                return_value=dict(self.file_info),
            ),
            patch.object(
                report_service.file_manager,
                "load_report_artifacts",
                return_value=_valid_artifacts(),
            ),
            patch.object(report_service, "get_report_job_executor", return_value=executor),
            patch.object(report_service, "_sync_reanalyze_report_body", return_value=result) as body,
            patch.object(report_service, "_patch_file_metadata"),
            patch.dict(
                report_service.system_components,
                {
                    "report_encoder": encoder,
                    "content_embedder": standalone_embedder,
                },
                clear=False,
            ),
        ):
            accepted = await report_service.reanalyze_report("report-1", user_id=7)

        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["file_id"], "report-1")
        self.assertEqual(
            accepted["processing_status_url"],
            f"/api/report-jobs/{accepted['job_id']}",
        )
        self.assertEqual(
            accepted["events_url"],
            f"/api/report-jobs/{accepted['job_id']}/events",
        )
        self.assertEqual(len(executor.submissions), 1)
        self.assertIs(executor.submissions[0][0], report_service._run_report_reanalysis_job)
        body.assert_called_once()

        job = report_jobs.snapshot_report_job(accepted["job_id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "success")
        self.assertEqual(job["stage"], "completed")
        self.assertEqual(job["result"], result)
        self._assert_no_extraction_or_embedding(encoder, standalone_embedder)

    async def test_accepted_reanalysis_reaches_failed_terminal_state_without_ocr(self):
        executor = _InlineExecutor()
        encoder, standalone_embedder = _pipeline_spies()

        with (
            patch.object(
                report_service.file_manager,
                "get_file_info",
                return_value=dict(self.file_info),
            ),
            patch.object(
                report_service.file_manager,
                "load_report_artifacts",
                return_value=_valid_artifacts(),
            ),
            patch.object(report_service, "get_report_job_executor", return_value=executor),
            patch.object(
                report_service,
                "_sync_reanalyze_report_body",
                side_effect=RuntimeError("assessment failed"),
            ),
            patch.object(report_service, "_patch_file_metadata"),
            patch.dict(
                report_service.system_components,
                {
                    "report_encoder": encoder,
                    "content_embedder": standalone_embedder,
                },
                clear=False,
            ),
        ):
            accepted = await report_service.reanalyze_report("report-1", user_id=7)

        job = report_jobs.snapshot_report_job(accepted["job_id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["stage"], "failed")
        self.assertIsNone(job["result"])
        self._assert_no_extraction_or_embedding(encoder, standalone_embedder)

    def test_superseded_reanalysis_cannot_be_marked_successful(self) -> None:
        job = report_jobs.create_report_job(
            file_id="report-1",
            filename="Existing Report.pdf",
            user_id=7,
        )
        result = {
            "status": "success",
            "message": "Assessment completed from persisted artifacts.",
            "file_id": "report-1",
        }

        with (
            patch.object(
                report_service,
                "_sync_reanalyze_report_body",
                return_value=result,
            ),
            patch.object(
                report_service,
                "_patch_file_metadata",
                side_effect=[True, False, False],
            ),
        ):
            report_service._run_report_reanalysis_job(
                job["job_id"],
                dict(self.file_info),
            )

        snapshot = report_jobs.snapshot_report_job(job["job_id"])
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["stage"], "failed")
        self.assertIsNone(snapshot["result"])

    @staticmethod
    def _assert_no_extraction_or_embedding(encoder, standalone_embedder) -> None:
        encoder.encode_pdf.assert_not_called()
        encoder.extractor.extract_pdf.assert_not_called()
        encoder.embedder.embed_document.assert_not_called()
        encoder.embedder._generate_embeddings.assert_not_called()
        standalone_embedder.embed_document.assert_not_called()
        standalone_embedder._generate_embeddings.assert_not_called()

    def test_reanalysis_route_is_registered_as_post(self):
        matches = [
            route
            for route in reports_router.router.routes
            if getattr(route, "path", None) == "/api/reports/{file_id}/reanalyze"
        ]

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].methods, {"POST"})


if __name__ == "__main__":
    unittest.main()
