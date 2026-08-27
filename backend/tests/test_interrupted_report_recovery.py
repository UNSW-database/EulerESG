import tempfile
import unittest
from unittest.mock import patch

from esg_encoding.file_manager import FileManager
from esg_encoding.services import report_service
from esg_encoding.services.report_service import _report_progress_metadata_updates


class InterruptedReportRecoveryTests(unittest.TestCase):
    def test_processing_rows_are_recoverable_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            manager.metadata = {"files": {
                "stuck": {
                    "file_type": "report", "status": "processing",
                    "processing_stage": "extracted", "processing_job_id": "old-job",
                },
                "done": {
                    "file_type": "report", "status": "processing",
                    "processing_stage": "completed", "processing_job_id": "done-job",
                },
                "partial": {
                    "file_type": "report", "status": "processing",
                    "processing_stage": "partial_success", "processing_job_id": "partial-job",
                    "processing_error": "assessment failed",
                },
                "legacy-partial": {
                    "file_type": "report", "status": "failed",
                    "processing_stage": "interrupted",
                    "interrupted_job_id": "legacy-job",
                    "processing_error": "Processing was interrupted by a backend restart.",
                    "processing_history": [{
                        "stage": "interrupted_recovery",
                        "previous_stage": "partial_success",
                    }],
                },
            }, "sessions": {}}

            result = manager.recover_interrupted_reports()

            self.assertEqual(result, {"completed": 3, "interrupted": 1})
            self.assertEqual(manager.metadata["files"]["stuck"]["status"], "failed")
            self.assertEqual(manager.metadata["files"]["stuck"]["processing_stage"], "interrupted")
            self.assertNotIn("processing_job_id", manager.metadata["files"]["stuck"])
            self.assertEqual(manager.metadata["files"]["done"]["status"], "processed")
            self.assertNotIn("interrupted_job_id", manager.metadata["files"]["done"])
            self.assertEqual(manager.metadata["files"]["partial"]["status"], "processed")
            self.assertEqual(manager.metadata["files"]["partial"]["processing_stage"], "partial_success")
            self.assertEqual(manager.metadata["files"]["partial"]["processing_error"], "assessment failed")
            self.assertNotIn("processing_job_id", manager.metadata["files"]["partial"])
            self.assertNotIn("interrupted_job_id", manager.metadata["files"]["partial"])
            self.assertEqual(manager.metadata["files"]["legacy-partial"]["status"], "processed")
            self.assertEqual(manager.metadata["files"]["legacy-partial"]["processing_stage"], "partial_success")
            self.assertNotIn("interrupted_job_id", manager.metadata["files"]["legacy-partial"])
            self.assertIn("completed with warnings", manager.metadata["files"]["legacy-partial"]["processing_error"])
            self.assertEqual(manager.recover_interrupted_reports(), {"completed": 0, "interrupted": 0})


class ReportProgressMetadataTests(unittest.TestCase):
    def tearDown(self):
        report_service._progress_metadata_last_flush.clear()

    def test_progress_metadata_writes_are_debounced_but_memory_stays_current(self):
        metadata = {"files": {"report-1": {}}}
        with (
            patch.object(report_service.file_manager, "metadata", metadata),
            patch.object(report_service.file_manager, "_save_metadata") as save_metadata,
            patch.object(report_service.time, "monotonic", side_effect=[100.0, 105.0, 113.0]),
            patch.dict(
                report_service.os.environ,
                {"REPORT_PROGRESS_METADATA_FLUSH_SECONDS": "12"},
            ),
        ):
            report_service._patch_progress_file_metadata(
                "report-1", processing_progress=10
            )
            report_service._patch_progress_file_metadata(
                "report-1", processing_progress=20
            )
            self.assertEqual(metadata["files"]["report-1"]["processing_progress"], 20)
            report_service._patch_progress_file_metadata(
                "report-1", processing_progress=30
            )

        self.assertEqual(save_metadata.call_count, 2)
        self.assertEqual(metadata["files"]["report-1"]["processing_progress"], 30)

    def test_terminal_progress_forces_metadata_write(self):
        metadata = {"files": {"report-1": {}}}
        with (
            patch.object(report_service.file_manager, "metadata", metadata),
            patch.object(report_service.file_manager, "_save_metadata") as save_metadata,
            patch.object(report_service.time, "monotonic", side_effect=[100.0, 101.0]),
        ):
            report_service._patch_progress_file_metadata(
                "report-1", processing_progress=10
            )
            report_service._patch_progress_file_metadata(
                "report-1",
                force=True,
                status="processed",
                processing_progress=100,
            )

        self.assertEqual(save_metadata.call_count, 2)
        self.assertEqual(metadata["files"]["report-1"]["status"], "processed")

    def test_stale_job_progress_cannot_overwrite_a_new_or_terminal_job(self):
        metadata = {
            "files": {
                "report-1": {
                    "status": "processing",
                    "processing_job_id": "new-job",
                    "processing_progress": 40,
                }
            }
        }
        with (
            patch.object(report_service.file_manager, "metadata", metadata),
            patch.object(report_service.file_manager, "_save_metadata") as save_metadata,
        ):
            report_service._patch_progress_file_metadata(
                "report-1",
                expected_job_id="old-job",
                processing_job_id="old-job",
                processing_progress=90,
            )
            metadata["files"]["report-1"].update(
                status="processed",
                processing_job_id=None,
                processing_progress=100,
            )
            report_service._patch_progress_file_metadata(
                "report-1",
                force=True,
                expected_job_id="old-job",
                status="processing",
                processing_job_id="old-job",
                processing_progress=95,
            )

        save_metadata.assert_not_called()
        self.assertEqual(metadata["files"]["report-1"]["status"], "processed")
        self.assertIsNone(metadata["files"]["report-1"]["processing_job_id"])
        self.assertEqual(metadata["files"]["report-1"]["processing_progress"], 100)

    def test_stale_direct_terminal_patch_cannot_clear_a_new_job_token(self):
        metadata = {
            "files": {
                "report-1": {
                    "status": "processing",
                    "processing_job_id": "new-job",
                    "processing_progress": 25,
                }
            }
        }
        with (
            patch.object(report_service.file_manager, "metadata", metadata),
            patch.object(report_service.file_manager, "_save_metadata") as save_metadata,
        ):
            patched = report_service._patch_file_metadata(
                "report-1",
                expected_job_id="old-job",
                status="failed",
                processing_job_id=None,
                processing_progress=100,
            )

        self.assertFalse(patched)
        save_metadata.assert_not_called()
        self.assertEqual(metadata["files"]["report-1"]["status"], "processing")
        self.assertEqual(
            metadata["files"]["report-1"]["processing_job_id"], "new-job"
        )

    def test_stale_finalizer_cannot_move_or_patch_a_new_job(self):
        metadata = {
            "files": {
                "report-1": {
                    "status": "processing",
                    "processing_job_id": "new-job",
                }
            }
        }
        with (
            patch.object(report_service.file_manager, "metadata", metadata),
            patch.object(report_service.file_manager, "move_report_file") as move_report,
            patch.object(report_service.file_manager, "_save_metadata") as save_metadata,
        ):
            finalized = report_service._finalize_report_file_metadata(
                "report-1",
                destination_status="failed",
                expected_job_id="old-job",
                status="failed",
                processing_job_id=None,
            )

        self.assertFalse(finalized)
        move_report.assert_not_called()
        save_metadata.assert_not_called()
        self.assertEqual(metadata["files"]["report-1"]["status"], "processing")

    def test_failed_file_move_cannot_be_reported_as_finalized(self):
        metadata = {
            "files": {
                "report-1": {
                    "status": "processing",
                    "processing_job_id": "job-1",
                }
            }
        }
        with (
            patch.object(report_service.file_manager, "metadata", metadata),
            patch.object(
                report_service.file_manager,
                "move_report_file",
                return_value=False,
            ) as move_report,
            patch.object(report_service.file_manager, "_save_metadata") as save_metadata,
        ):
            finalized = report_service._finalize_report_file_metadata(
                "report-1",
                destination_status="processed",
                expected_job_id="job-1",
                status="processed",
                processing_job_id=None,
            )

        self.assertFalse(finalized)
        move_report.assert_called_once_with("report-1", "processed")
        save_metadata.assert_not_called()
        self.assertEqual(metadata["files"]["report-1"]["status"], "processing")

    def test_terminal_progress_does_not_restore_processing_state(self):
        completed = _report_progress_metadata_updates(
            stage="completed",
            message="done",
            progress=100,
            job_id="job-1",
            extra=None,
        )
        partial = _report_progress_metadata_updates(
            stage="partial_success",
            message="warning",
            progress=100,
            job_id="job-2",
            extra={"error": "assessment failed"},
        )
        failed = _report_progress_metadata_updates(
            stage="failed",
            message="failed",
            progress=100,
            job_id="job-3",
            extra={"error": "pipeline failed"},
        )

        self.assertEqual(completed["status"], "processed")
        self.assertIsNone(completed["processing_job_id"])
        self.assertIsNone(completed["processing_error"])
        self.assertEqual(partial["status"], "processed")
        self.assertEqual(partial["processing_stage"], "partial_success")
        self.assertEqual(partial["processing_error"], "assessment failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["processing_error"], "pipeline failed")

    def test_non_terminal_progress_remains_processing(self):
        updates = _report_progress_metadata_updates(
            stage="ocr_batch_processing",
            message="working",
            progress=42,
            job_id="job-active",
            extra={"pages_done": 10},
        )

        self.assertEqual(updates["status"], "processing")
        self.assertEqual(updates["processing_job_id"], "job-active")
        self.assertEqual(updates["processing_progress"], 42)
        self.assertEqual(updates["processing_pages_done"], 10)
        self.assertNotIn("processing_error", updates)
