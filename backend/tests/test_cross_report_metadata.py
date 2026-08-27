from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from esg_encoding import cross_report_metadata
from esg_encoding.cross_analysis_models import CrossAnalysisReport
from esg_encoding.services import common, cross_analysis_service


def _report(file_id: str, framework: str = "SASB") -> CrossAnalysisReport:
    return CrossAnalysisReport(
        file_id=file_id,
        display_name=file_id,
        short_name=file_id,
        confidence=0.5,
        filename=f"{file_id}.pdf",
        has_assessment=True,
        framework=framework,
        industry="Technology",
        semi_industry="Software",
    )


class CrossReportMetadataTests(unittest.TestCase):
    def test_uses_metadata_and_filename_without_semantic_cross_analysis(self) -> None:
        metadata = {
            "files": {
                "a": {
                    "file_id": "a",
                    "original_name": "Dell2024.pdf",
                    "company_name": "Dell",
                    "report_year": 2024,
                    "framework": "SASB",
                    "industry": "Technology",
                    "semi_industry": "Hardware",
                },
                "b": {
                    "file_id": "b",
                    "original_name": "Microsoft2025-Sustainability-Report.pdf",
                    "framework": "SASB",
                    "industry": "Technology",
                    "semi_industry": "Hardware",
                },
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            empty = Path(temp_dir)
            with (
                patch.object(cross_report_metadata.file_manager, "metadata", metadata),
                patch.object(cross_report_metadata, "_assessment_dirs", return_value=(empty,)),
                patch.object(cross_report_metadata.file_manager, "_save_metadata") as save,
                patch(
                    "builtins.__import__",
                    wraps=__import__,
                ) as import_module,
            ):
                reports = cross_report_metadata.get_reports_info(["a", "b"])

        self.assertEqual(reports[0].display_name, "Dell")
        self.assertEqual(reports[0].report_year, 2024)
        self.assertEqual(reports[0].short_name, "Dell 2024")
        self.assertEqual(reports[1].display_name, "Microsoft")
        self.assertEqual(reports[1].report_year, 2025)
        imported_names = [call.args[0] for call in import_module.call_args_list if call.args]
        self.assertNotIn("esg_encoding.cross_analysis", imported_names)
        save.assert_called_once()

    def test_reads_one_assessment_only_when_scope_metadata_is_missing(self) -> None:
        metadata = {"files": {"report-a": {"original_name": "Acme2023.pdf"}}}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            assessment = output_dir / "Acme_report-a_compliance.json"
            assessment.write_text(
                json.dumps(
                    {
                        "framework": "SASB",
                        "industry": "Technology & Communications",
                        "semi_industry": "Software & IT Services",
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(cross_report_metadata.file_manager, "metadata", metadata),
                patch.object(
                    cross_report_metadata,
                    "_assessment_dirs",
                    return_value=(output_dir,),
                ),
                patch.object(cross_report_metadata.file_manager, "_save_metadata"),
            ):
                report = cross_report_metadata.get_reports_info(["report-a"])[0]

        self.assertTrue(report.has_assessment)
        self.assertEqual(report.framework, "SASB")
        self.assertEqual(report.industry, "Technology & Communications")
        self.assertEqual(report.semi_industry, "Software & IT Services")

    def test_recovers_gri_sector_and_topic_from_manifest_and_filename(self) -> None:
        metadata = {
            "files": {
                "report-a": {
                    "original_name": "Acme2024.pdf",
                    "framework": "GRI",
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            assessment = (
                output_dir
                / "GRI_coal_sector_climate_change_report-a_compliance.json"
            )
            assessment.write_text(
                json.dumps({"framework": "GRI"}), encoding="utf-8"
            )
            (output_dir / "report-a_compliance_manifest.json").write_text(
                json.dumps({"outputs": [{"scope_key": "climate_change"}]}),
                encoding="utf-8",
            )
            with (
                patch.object(cross_report_metadata.file_manager, "metadata", metadata),
                patch.object(
                    cross_report_metadata,
                    "_assessment_dirs",
                    return_value=(output_dir,),
                ),
                patch.object(cross_report_metadata.file_manager, "_save_metadata"),
            ):
                report = cross_report_metadata.get_reports_info(["report-a"])[0]

        self.assertEqual(report.framework, "GRI")
        self.assertEqual(report.gri_sector, "coal_sector")
        self.assertEqual(report.gri_topic, "climate_change")

    def test_does_not_fsync_when_resolved_labels_are_unchanged(self) -> None:
        metadata = {
            "files": {
                "a": {
                    "file_id": "a",
                    "original_name": "Dell2024.pdf",
                    "company_name": "Dell",
                    "report_year": 2024,
                    "display_name": "Dell",
                    "short_name": "Dell 2024",
                    "display_confidence": 0.98,
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(cross_report_metadata.file_manager, "metadata", metadata),
                patch.object(
                    cross_report_metadata,
                    "_assessment_dirs",
                    return_value=(Path(temp_dir),),
                ),
                patch.object(cross_report_metadata.file_manager, "_save_metadata") as save,
            ):
                cross_report_metadata.get_reports_info(["a"])
        save.assert_not_called()


class CrossReportServiceResolutionTests(unittest.TestCase):
    def test_reports_endpoint_resolves_metadata_once(self) -> None:
        reports = [_report("a"), _report("b")]
        with patch.object(
            cross_analysis_service,
            "_validate_cross_analysis_compatibility",
            return_value=reports,
        ) as resolve:
            response = asyncio.run(cross_analysis_service.cross_analysis_reports("a,b"))

        self.assertEqual([report.file_id for report in response.reports], ["a", "b"])
        resolve.assert_called_once_with(["a", "b"])

    def test_validator_reuses_pre_resolved_reports(self) -> None:
        reports = [_report("a"), _report("b")]
        with patch.object(common, "get_reports_info") as resolve:
            actual = common._validate_cross_analysis_compatibility(
                ["a", "b"], reports=reports
            )
        self.assertEqual(actual, reports)
        resolve.assert_not_called()

    def test_disclosed_cache_reuses_validation_metadata_for_builder(self) -> None:
        reports = [_report("b"), _report("a")]
        with (
            patch.object(
                cross_analysis_service,
                "_validate_cross_analysis_compatibility",
                return_value=reports,
            ) as resolve,
            patch.object(
                cross_analysis_service,
                "_cross_analysis_disclosed_cache_sync",
                return_value={"records": []},
            ) as build,
        ):
            result = asyncio.run(
                cross_analysis_service.cross_analysis_disclosed_cache("b,a", user_id=4)
            )

        self.assertEqual(result, {"records": []})
        resolve.assert_called_once_with(["b", "a"])
        build.assert_called_once()
        self.assertEqual(build.call_args.args[0], ["b", "a"])
        self.assertEqual(build.call_args.args[1], 4)
        self.assertEqual(
            [report.file_id for report in build.call_args.args[2]], ["a", "b"]
        )


if __name__ == "__main__":
    unittest.main()
