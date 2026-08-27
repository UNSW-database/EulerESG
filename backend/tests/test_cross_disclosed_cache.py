from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from esg_encoding.services import common, cross_analysis_service


class CrossDisclosedCacheLockTests(unittest.TestCase):
    def setUp(self) -> None:
        with common._cross_disclosed_locks_guard:
            common._cross_disclosed_locks.clear()

    def tearDown(self) -> None:
        with common._cross_disclosed_locks_guard:
            common._cross_disclosed_locks.clear()

    def test_lock_registry_reuses_one_lock_per_cache_key(self) -> None:
        first = common._cross_disclosed_lock_for("same-key")
        second = common._cross_disclosed_lock_for("same-key")
        other = common._cross_disclosed_lock_for("other-key")

        self.assertIs(first, second)
        self.assertIsNot(first, other)

    def test_disclosed_cache_endpoint_builds_then_reuses_cache(self) -> None:
        file_info = {"framework": "SASB"}
        records = [
            {
                "id": "report-a",
                "name": "Acme 2024",
                "primary_navigation": "Quantitative",
                "secondary_navigation": "Workforce",
                "topic": "Employee engagement",
                "sub_topic": "TC-SI-330a.2",
                "category": "Quantitative",
                "page": 10,
                "data": "87.0",
                "value": "87.0",
                "year": "2024",
                "unit": "%",
                "detail": "Employee engagement was disclosed.",
                "disclosure_status": "fully_disclosed",
                "metric_id": "TC-SI-330a.2",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            with (
                patch.object(
                    cross_analysis_service,
                    "_validate_cross_analysis_compatibility",
                    return_value=[],
                ),
                patch.object(
                    cross_analysis_service.file_manager,
                    "get_file_info",
                    return_value=file_info,
                ),
                patch.object(
                    cross_analysis_service,
                    "_cross_disclosed_cache_dir",
                    return_value=cache_dir,
                ),
                patch.object(
                    cross_analysis_service,
                    "_find_assessment_json_path",
                    return_value=None,
                ),
                patch.object(
                    cross_analysis_service,
                    "_build_disclosed_records_for_files",
                    return_value=(records, [], []),
                ) as build_records,
            ):
                first = asyncio.run(
                    cross_analysis_service.cross_analysis_disclosed_cache(
                        "report-b,report-a", user_id=7
                    )
                )
                second = asyncio.run(
                    cross_analysis_service.cross_analysis_disclosed_cache(
                        "report-a,report-b", user_id=7
                    )
                )

        self.assertEqual(first["file_ids"], ["report-a", "report-b"])
        self.assertFalse(first["from_cache"])
        self.assertEqual(first["records"], records)
        self.assertTrue(second["from_cache"])
        self.assertEqual(second["records"], records)
        build_records.assert_called_once_with(
            ["report-a", "report-b"], user_id=7, reports=[]
        )


if __name__ == "__main__":
    unittest.main()
