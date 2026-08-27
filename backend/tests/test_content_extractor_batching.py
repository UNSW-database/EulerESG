from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from esg_encoding.content_extractor import (
    ContentExtractor,
    _batch_timing_summary,
    _merge_native_and_ocr_page,
    _page_batch_ranges,
    _selected_page_batch_ranges,
)
from esg_encoding.exceptions import ContentExtractionError


class PageBatchRangeTests(unittest.TestCase):
    def test_116_pages_create_17_contiguous_batches(self):
        ranges = _page_batch_ranges(116, 7)

        self.assertEqual(len(ranges), 17)
        self.assertEqual(ranges[0], (1, 7))
        self.assertEqual(ranges[-1], (113, 116))
        pages = [page for start, end in ranges for page in range(start, end + 1)]
        self.assertEqual(pages, list(range(1, 117)))

    def test_selected_pages_are_batched_without_crossing_native_gaps(self):
        ranges = _selected_page_batch_ranges(15, 2, [1, 2, 5, 6, 7, 15])

        self.assertEqual(ranges, [(1, 2), (5, 6), (7, 7), (15, 15)])

    def test_hybrid_merge_prefers_native_text_and_keeps_ocr_structure(self):
        merged = _merge_native_and_ocr_page(
            "Revenue was 100% in FY24.",
            "<table><tr><td>FY24</td><td>100%</td></tr></table>",
        )

        self.assertIn("Revenue was 100% in FY24.", merged)
        self.assertIn("<table>", merged)
        self.assertEqual(merged.count("Revenue was 100% in FY24."), 1)
        self.assertNotIn("OCR structure supplement", merged)

    def test_batch_timing_summary_uses_worker_results(self):
        states = [
            {"result_json": json.dumps({"elapsed_seconds": value, "predict_seconds": value - 1})}
            for value in (3.0, 5.0, 7.0, 9.0)
        ]

        summary = _batch_timing_summary(states)

        self.assertEqual(summary["elapsed_seconds"]["count"], 4)
        self.assertEqual(summary["elapsed_seconds"]["avg"], 6.0)
        self.assertEqual(summary["elapsed_seconds"]["p50"], 6.0)
        self.assertEqual(summary["elapsed_seconds"]["max"], 9.0)
        self.assertEqual(summary["predict_seconds"]["avg"], 5.0)


class PaddleDocumentLifecycleTests(unittest.TestCase):
    def test_queue_wrapper_defers_wake_to_fenced_submit_and_releases_in_finally(self):
        extractor = ContentExtractor()
        calls: list[str] = []

        def run_active(source_path, job_id):  # noqa: ARG001
            calls.append("run")
            self.assertTrue(job_id.startswith("parse_"))
            return {"status": "success"}

        with patch.object(
            extractor,
            "_wake_paddleocr_vlm",
            side_effect=lambda: calls.append("wake"),
        ), patch.object(
            extractor,
            "_run_paddleocr_vl_page_batch_queue_active",
            side_effect=run_active,
        ), patch.object(
            extractor,
            "_release_paddle_after_document",
            side_effect=lambda job_id: calls.append("release"),
        ):
            result = extractor._run_paddleocr_vl_page_batch_queue(Path("report.pdf"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, ["run", "release"])

    def test_queue_wrapper_can_defer_release_until_report_repairs_finish(self):
        extractor = ContentExtractor()
        calls: list[str] = []

        def run_active(source_path, job_id):  # noqa: ARG001
            calls.append("run")
            return {"status": "success"}

        with patch.object(
            extractor,
            "_wake_paddleocr_vlm",
            side_effect=lambda: calls.append("wake"),
        ), patch.object(
            extractor,
            "_run_paddleocr_vl_page_batch_queue_active",
            side_effect=run_active,
        ), patch.object(
            extractor,
            "_release_paddle_after_document",
            side_effect=lambda job_id: calls.append(f"release:{job_id}"),
        ) as release:
            result = extractor._run_paddleocr_vl_page_batch_queue(
                Path("report.pdf"),
                release_after_document=False,
            )

        self.assertEqual(calls, ["run"])
        release.assert_not_called()
        self.assertTrue(result["_paddle_lifecycle_job_id"].startswith("parse_"))

    def test_queue_wrapper_releases_after_ocr_failure(self):
        extractor = ContentExtractor()

        with patch.object(extractor, "_wake_paddleocr_vlm"), patch.object(
            extractor,
            "_run_paddleocr_vl_page_batch_queue_active",
            side_effect=ContentExtractionError("failed", file_path="report.pdf"),
        ), patch.object(extractor, "_release_paddle_after_document") as release:
            with self.assertRaises(ContentExtractionError):
                extractor._run_paddleocr_vl_page_batch_queue(Path("report.pdf"))

        release.assert_called_once()

    def test_vllm_sleep_requires_all_worker_release_acknowledgements(self):
        extractor = ContentExtractor()
        with patch.object(
            extractor,
            "_request_paddle_worker_release",
            return_value=False,
        ), patch.object(extractor, "_sleep_paddleocr_vlm") as sleep:
            extractor._release_paddle_after_document("job-1")
        sleep.assert_not_called()

        with patch.object(
            extractor,
            "_request_paddle_worker_release",
            return_value=True,
        ), patch.object(extractor, "_sleep_paddleocr_vlm") as sleep:
            extractor._release_paddle_after_document("job-2")
        sleep.assert_called_once_with("job-2")

    def test_vllm_does_not_sleep_while_a_worker_holds_a_processing_lease(self):
        extractor = ContentExtractor()
        client = MagicMock()
        lifecycle_lock = MagicMock()
        lifecycle_lock.acquire.return_value = True
        client.lock.return_value = lifecycle_lock
        pipeline = MagicMock()
        client.pipeline.return_value = pipeline
        pipeline.llen.return_value = pipeline
        pipeline.zcount.return_value = pipeline
        pipeline.execute.return_value = [0, 1, 1]

        with patch.dict(
            os.environ,
            {"PADDLEOCR_VLM_SLEEP_ENABLED": "true"},
        ), patch.object(
            extractor,
            "_redis_client",
            return_value=client,
        ), patch.object(extractor, "_paddleocr_vlm_sleep_state") as sleep_state:
            slept = extractor._sleep_paddleocr_vlm("job-active")

        self.assertFalse(slept)
        sleep_state.assert_not_called()
        pipeline.llen.assert_any_call("paddleocr:parse")
        pipeline.llen.assert_any_call("paddleocr:parse:processing")
        pipeline.zcount.assert_called_once()
        lifecycle_lock.acquire.assert_called_once_with(blocking=True)
        lifecycle_lock.release.assert_called_once_with()

    def test_wake_and_enqueue_share_one_lifecycle_lock(self):
        extractor = ContentExtractor()
        client = MagicMock()
        lifecycle_lock = MagicMock()
        events: list[str] = []
        lifecycle_lock.acquire.side_effect = lambda **_kwargs: events.append("acquire") or True
        lifecycle_lock.release.side_effect = lambda: events.append("release")
        client.lock.return_value = lifecycle_lock
        client.rpush.side_effect = lambda *_args, **_kwargs: events.append("rpush")
        entries = [("batch-key", {"status": "queued"}, {"job_id": "job-1"})]

        with patch.object(
            extractor,
            "_wake_paddleocr_vlm",
            side_effect=lambda: events.append("wake"),
        ), patch.object(
            extractor,
            "_redis_hash_set",
            side_effect=lambda *_args, **_kwargs: events.append("hash"),
        ):
            extractor._submit_paddleocr_queue_entries(
                client,
                "paddleocr:parse",
                entries,
            )

        self.assertEqual(events, ["acquire", "wake", "hash", "rpush", "release"])

    def test_lifecycle_lock_ttl_covers_configured_api_timeout(self):
        extractor = ContentExtractor()
        client = MagicMock()
        lifecycle_lock = MagicMock()
        lifecycle_lock.acquire.return_value = True
        client.lock.return_value = lifecycle_lock

        with patch.dict(
            os.environ,
            {
                "PADDLEOCR_VLM_WAKE_TIMEOUT_SECONDS": "600",
                "PADDLEOCR_VLM_SLEEP_TIMEOUT_SECONDS": "120",
                "PADDLEOCR_VLM_LIFECYCLE_LOCK_TIMEOUT_SECONDS": "30",
            },
        ):
            lock = extractor._acquire_paddleocr_lifecycle_lock(client)

        self.assertIs(lock, lifecycle_lock)
        self.assertGreaterEqual(client.lock.call_args.kwargs["timeout"], 1320)
        self.assertGreaterEqual(
            client.lock.call_args.kwargs["blocking_timeout"],
            client.lock.call_args.kwargs["timeout"],
        )


class PyMuPdfSplitTests(unittest.TestCase):
    def test_15_page_pdf_is_split_losslessly_with_ready_markers(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.pdf"
            source = fitz.open()
            for page_number in range(1, 16):
                page = source.new_page()
                page.insert_text((72, 72), f"PAGE-{page_number:02d}")
            source.save(source_path)
            source.close()

            with patch.dict(os.environ, {"PADDLEOCR_JOB_WORK_DIR": str(root / "jobs")}):
                units, total_pages, batch_dir = ContentExtractor()._split_pdf_for_page_batch_queue(
                    source_path,
                    "split_test",
                    7,
                )

            self.assertEqual(total_pages, 15)
            self.assertEqual(
                [(unit["start_page"], unit["end_page"]) for unit in units],
                [(1, 7), (8, 14), (15, 15)],
            )
            self.assertEqual(list(batch_dir.glob("*.tmp.pdf")), [])

            copied_labels: list[str] = []
            for unit, expected_count in zip(units, (7, 7, 1)):
                batch_path = Path(unit["input_path"])
                ready_path = Path(unit["ready_path"])
                self.assertTrue(batch_path.is_file())
                self.assertTrue(ready_path.is_file())

                marker = json.loads(ready_path.read_text(encoding="utf-8"))
                self.assertEqual(marker["start_page"], unit["start_page"])
                self.assertEqual(marker["end_page"], unit["end_page"])

                batch = fitz.open(batch_path)
                try:
                    self.assertEqual(len(batch), expected_count)
                    copied_labels.extend(page.get_text("text").strip() for page in batch)
                finally:
                    batch.close()

            self.assertEqual(copied_labels, [f"PAGE-{page:02d}" for page in range(1, 16)])

    def test_selected_scan_pages_are_the_only_pages_copied(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "mixed.pdf"
            source = fitz.open()
            for page_number in range(1, 7):
                page = source.new_page()
                page.insert_text((72, 72), f"PAGE-{page_number:02d}")
            source.save(source_path)
            source.close()

            with patch.dict(os.environ, {"PADDLEOCR_JOB_WORK_DIR": str(root / "jobs")}):
                units, total_pages, _ = ContentExtractor()._split_pdf_for_page_batch_queue(
                    source_path,
                    "adaptive_split",
                    2,
                    page_numbers=[2, 3, 6],
                )

            self.assertEqual(total_pages, 6)
            self.assertEqual(
                [(unit["start_page"], unit["end_page"]) for unit in units],
                [(2, 3), (6, 6)],
            )
            copied_labels: list[str] = []
            for unit in units:
                batch = fitz.open(unit["input_path"])
                try:
                    copied_labels.extend(page.get_text("text").strip() for page in batch)
                finally:
                    batch.close()
            self.assertEqual(copied_labels, ["PAGE-02", "PAGE-03", "PAGE-06"])


class PageBatchMergeValidationTests(unittest.TestCase):
    def test_success_state_with_missing_page_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_markdown = root / "batch.md"
            batch_markdown.write_text(
                "\n".join(
                    f"<!-- Page {page} | PaddleOCR-VL batch 1/1 part {page} -->\npage-{page}"
                    for page in range(1, 7)
                ),
                encoding="utf-8",
            )
            state = {
                "status": "success",
                "result_json": json.dumps(
                    {
                        "start_page": 1,
                        "end_page": 7,
                        "result_count": 7,
                        "batch_markdown_path": str(batch_markdown),
                    }
                ),
            }

            with self.assertRaisesRegex(
                ContentExtractionError,
                "incomplete or out of order",
            ):
                ContentExtractor()._merge_paddleocr_page_batch_results(
                    client=object(),
                    task_key="paddleocr:task:test",
                    job_id="test",
                    source_path=root / "source.pdf",
                    batch_states=[state],
                    output_dir=root / "output",
                    total_pages=7,
                    total_units=1,
                    batch_size=7,
                )


if __name__ == "__main__":
    unittest.main()
