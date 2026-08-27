import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from esg_encoding.file_manager import FileManager
from esg_encoding.services import file_service


class FileMetadataDeletionTests(unittest.TestCase):
    def test_remove_file_metadata_is_persisted_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            manager.metadata = {
                "files": {"canonical-id": {"file_id": "canonical-id"}},
                "sessions": {},
            }
            manager._save_metadata()

            self.assertTrue(manager.remove_file_metadata("canonical-id"))
            self.assertFalse(manager.remove_file_metadata("canonical-id"))

            persisted = json.loads(Path(manager.metadata_file).read_text(encoding="utf-8"))
            self.assertNotIn("canonical-id", persisted["files"])
            self.assertEqual(list(Path(directory).glob(".file_metadata.json.*.tmp")), [])

    def test_concurrent_removal_has_one_winner_and_no_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            manager.metadata = {
                "files": {"canonical-id": {"file_id": "canonical-id"}},
                "sessions": {},
            }
            manager._save_metadata()
            barrier = threading.Barrier(2)
            results = []

            def remove():
                barrier.wait()
                results.append(manager.remove_file_metadata("canonical-id"))

            threads = [threading.Thread(target=remove) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(sorted(results), [False, True])

    def test_failed_persistence_restores_in_memory_record(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            manager.metadata = {
                "files": {"canonical-id": {"file_id": "canonical-id"}},
                "sessions": {},
            }

            with patch.object(manager, "_save_metadata", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    manager.remove_file_metadata("canonical-id")

            self.assertIn("canonical-id", manager.metadata["files"])


class FileDeletionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_alias_deletes_the_canonical_metadata_record(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            pdf_path = manager.processed_reports / "example-report.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            asset_dir = file_service.visual_asset_dir(pdf_path)
            asset_dir.mkdir(parents=True)
            (asset_dir / "crop.png").write_bytes(b"image")
            manager.metadata = {
                "files": {
                    "canonical-id": {
                        "file_id": "canonical-id",
                        "file_type": "report",
                        "file_path": str(pdf_path),
                        "safe_filename": pdf_path.name,
                        "original_name": "Example Report.pdf",
                        "user_id": 7,
                    }
                },
                "sessions": {},
            }
            manager._save_metadata()

            with (
                patch.object(file_service, "file_manager", manager),
                patch.object(file_service, "system_components", {}),
                patch.object(file_service, "_unlink_compliance_reports_dir_for_file_id"),
                patch.object(file_service, "_unlink_compliance_markdown_for_file_id"),
            ):
                result = await file_service.delete_file("Example Report", user_id=7)

            self.assertEqual(result["status"], "success")
            self.assertFalse(pdf_path.exists())
            self.assertFalse(asset_dir.exists())
            self.assertNotIn("canonical-id", manager.metadata["files"])


if __name__ == "__main__":
    unittest.main()
