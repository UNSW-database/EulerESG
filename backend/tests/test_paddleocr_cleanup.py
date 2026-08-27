import os
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from esg_encoding.paddleocr_cleanup import cleanup_stale_paddleocr_artifacts
from esg_encoding.visual_assets import promote_visual_assets


class PaddleOCRCleanupTests(unittest.TestCase):
    def test_cleanup_removes_only_stale_children(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            output = tmp_path / "paddleocr_vl_output"
            jobs = tmp_path / "paddleocr_vl_jobs"
            stale = output / "old-job"
            fresh = output / "active-job"
            stale.mkdir(parents=True)
            fresh.mkdir(parents=True)
            jobs.mkdir()
            (stale / "batch.md").write_text("old", encoding="utf-8")
            old = time.time() - 48 * 3600
            os.utime(stale, (old, old))

            with patch.dict(os.environ, {
                "PADDLEOCR_OUTPUT_DIR": str(output),
                "PADDLEOCR_JOB_WORK_DIR": str(jobs),
                "PADDLEOCR_STALE_ARTIFACT_TTL_HOURS": "24",
            }):
                result = cleanup_stale_paddleocr_artifacts()

            self.assertEqual(result, {"removed": 1, "failed": 0})
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())

    def test_layout_audit_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            output = tmp_path / "worker" / "page_0001"
            output.mkdir(parents=True)
            (output / "layout.json").write_text('{"type":"text","text":"hello"}', encoding="utf-8")
            pdf = tmp_path / "report.pdf"
            pdf.write_bytes(b"pdf")

            with patch.dict(os.environ, {"PADDLEOCR_KEEP_LAYOUT_AUDIT": "false"}):
                promote_visual_assets(tmp_path / "worker", pdf)

            destination = tmp_path / "report_visual_assets"
            self.assertFalse((destination / "layout_audit.json").exists())
            self.assertNotIn("layout_audit", (destination / "manifest.json").read_text(encoding="utf-8"))
