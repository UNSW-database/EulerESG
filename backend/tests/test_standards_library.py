from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from esg_encoding.services import standards_library_service as standards_service
from esg_encoding.services.standards_library_service import (
    StandardsDataError,
    StandardsScopeNotFound,
    get_standard_metrics,
    get_standards_catalog,
)


class StandardsLibraryBundledDataTests(unittest.TestCase):
    def test_catalog_reports_the_real_bundled_scope_counts(self) -> None:
        catalog = get_standards_catalog()
        frameworks = {
            framework["id"]: framework for framework in catalog["frameworks"]
        }

        self.assertEqual(
            {key: frameworks[key]["scope_count"] for key in frameworks},
            {
                "sasb": 77,
                "gri": 98,
                "cdp": 8,
                "aasb": 0,
            },
        )
        self.assertNotIn("tcfd", frameworks)
        self.assertEqual(
            [(group["id"], len(group["scopes"])) for group in frameworks["gri"]["groups"]],
            [
                ("agriculture_aquaculture_and_fishing_sectors", 27),
                ("coal_sector", 22),
                ("mining_sector", 25),
                ("oil_and_gas_sector", 24),
            ],
        )
        self.assertTrue(frameworks["sasb"]["available"])
        self.assertFalse(frameworks["aasb"]["available"])

    def test_sasb_catalog_preserves_all_industry_groups_and_unique_industries(self) -> None:
        catalog = get_standards_catalog()
        sasb = next(
            framework for framework in catalog["frameworks"] if framework["id"] == "sasb"
        )

        expected_groups = {
            "consumer_goods": ("Consumer Goods", 7),
            "extractives_minerals_processing": (
                "Extractives & Minerals Processing",
                8,
            ),
            "financials": ("Financials", 7),
            "food_beverage": ("Food & Beverage", 8),
            "health_care": ("Health Care", 6),
            "infrastructure": ("Infrastructure", 8),
            "renewable_resources_alternative_energy": (
                "Renewable Resources & Alternative Energy",
                6,
            ),
            "resource_transformation": ("Resource Transformation", 5),
            "services": ("Services", 7),
            "technology_communications": ("Technology & Communications", 6),
            "transportation": ("Transportation", 9),
        }
        actual_groups = {
            group["id"]: (group["label"], len(group["scopes"]))
            for group in sasb["groups"]
        }
        leaf_ids = [
            scope["id"]
            for group in sasb["groups"]
            for scope in group["scopes"]
        ]

        self.assertEqual(actual_groups, expected_groups)
        self.assertEqual(len(sasb["groups"]), 11)
        self.assertEqual(len(leaf_ids), 77)
        self.assertEqual(len(set(leaf_ids)), 77)
        self.assertEqual(sasb["scope_count"], 77)

        technology = next(
            group
            for group in sasb["groups"]
            if group["id"] == "technology_communications"
        )
        self.assertIn(
            {"id": "Hardware", "label": "Hardware"},
            technology["scopes"],
        )

    def test_real_sasb_scope_resolves_its_group_when_group_id_is_omitted(self) -> None:
        result = get_standard_metrics("SASB", "Hardware")

        self.assertEqual(result["framework"]["id"], "sasb")
        self.assertEqual(
            result["group"],
            {
                "id": "technology_communications",
                "label": "Technology & Communications",
            },
        )
        self.assertEqual(result["scope"], {"id": "Hardware", "label": "Hardware"})
        self.assertEqual(result["total_metrics"], 24)
        self.assertEqual(
            result["metrics"][0]["id"],
            "sasb:technology_communications:Hardware:1",
        )
        self.assertEqual(result["metrics"][0]["code"], "TC-HW-230a.1")
        self.assertEqual(
            set(result["metrics"][0]),
            {
                "id",
                "code",
                "name",
                "topic",
                "category",
                "type",
                "unit",
                "standard",
                "definition",
                "simple_definition",
            },
        )

    def test_real_sasb_scope_accepts_its_explicit_group(self) -> None:
        result = get_standard_metrics(
            "sasb",
            "Hardware",
            group_id="technology_communications",
        )

        self.assertEqual(
            result["group"],
            {
                "id": "technology_communications",
                "label": "Technology & Communications",
            },
        )
        self.assertEqual(result["scope"]["id"], "Hardware")
        self.assertEqual(result["total_metrics"], 24)

    def test_real_sasb_long_definition_reaches_its_final_sentence(self) -> None:
        result = get_standard_metrics(
            "sasb",
            "Solar Technology & Project Developers",
        )
        metric = next(
            item for item in result["metrics"] if item["code"] == "RR-ST-140a.2"
        )

        self.assertGreater(len(metric["definition"]), 4_000)
        self.assertTrue(
            metric["definition"].endswith(
                "and why the entity chose these practices despite lifecycle trade-offs."
            )
        )

    def test_real_sasb_scope_rejects_an_explicit_wrong_group(self) -> None:
        with self.assertRaises(StandardsScopeNotFound):
            get_standard_metrics(
                "sasb",
                "Hardware",
                group_id="consumer_goods",
            )

    def test_aasb_is_listed_but_local_metrics_are_not_fabricated(self) -> None:
        catalog = get_standards_catalog()
        aasb = next(
            framework for framework in catalog["frameworks"] if framework["id"] == "aasb"
        )

        self.assertEqual(aasb["groups"], [])
        self.assertEqual(aasb["scope_count"], 0)
        self.assertFalse(aasb["available"])
        with self.assertRaisesRegex(
            StandardsScopeNotFound, "Local metric data is not available for AASB"
        ):
            get_standard_metrics("aasb", "anything")


class StandardsLibraryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        standards_service._get_standards_catalog_cached.cache_clear()
        standards_service._read_json_cached.cache_clear()
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.data_root = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        standards_service._get_standards_catalog_cached.cache_clear()
        standards_service._read_json_cached.cache_clear()
        self._temporary_directory.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_sasb_scope(self, rows: object) -> None:
        directory = self.data_root / "sasb_metrics"
        self._write_json(
            directory / "manifest.json",
            {"semi_industry_to_file": {"Example Industry": "Example.json"}},
        )
        self._write_json(directory / "Example.json", rows)

    def test_repeated_catalog_calls_reuse_the_cached_build_and_manifest_json(self) -> None:
        self._write_sasb_scope([{"Metric": "Cached metric"}])

        first = get_standards_catalog(self.data_root)
        catalog_after_first = standards_service._get_standards_catalog_cached.cache_info()
        json_after_first = standards_service._read_json_cached.cache_info()

        second = get_standards_catalog(self.data_root)
        catalog_after_second = standards_service._get_standards_catalog_cached.cache_info()
        json_after_second = standards_service._read_json_cached.cache_info()

        self.assertEqual(second, first)
        self.assertEqual(catalog_after_first.misses, 1)
        self.assertEqual(catalog_after_second.misses, catalog_after_first.misses)
        self.assertEqual(catalog_after_second.hits, catalog_after_first.hits + 1)
        self.assertEqual(json_after_first.misses, 1)
        self.assertEqual(json_after_second.misses, json_after_first.misses)

        first["frameworks"].clear()
        third = get_standards_catalog(self.data_root)
        self.assertEqual(third, second)
        self.assertIsNot(third, second)

    def test_concurrent_cold_catalog_requests_build_one_cached_snapshot(self) -> None:
        self._write_sasb_scope([{"Metric": "Concurrent metric"}])
        worker_count = 8
        start_barrier = Barrier(worker_count)

        def load_catalog(_: int) -> dict:
            start_barrier.wait(timeout=5)
            return get_standards_catalog(self.data_root)

        original_builder = standards_service._build_standards_catalog
        with patch.object(
            standards_service,
            "_build_standards_catalog",
            wraps=original_builder,
        ) as build_catalog:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                catalogs = list(executor.map(load_catalog, range(worker_count)))

        self.assertEqual(build_catalog.call_count, 1)
        self.assertTrue(all(catalog == catalogs[0] for catalog in catalogs[1:]))
        self.assertEqual(len({id(catalog) for catalog in catalogs}), worker_count)

    def test_manifest_change_invalidates_the_cached_catalog(self) -> None:
        self._write_sasb_scope([{"Metric": "First metric"}])
        first = get_standards_catalog(self.data_root)
        first_sasb = next(
            framework for framework in first["frameworks"] if framework["id"] == "sasb"
        )
        cache_after_first = standards_service._get_standards_catalog_cached.cache_info()

        directory = self.data_root / "sasb_metrics"
        self._write_json(directory / "Second.json", [{"Metric": "Second metric"}])
        self._write_json(
            directory / "manifest.json",
            {
                "semi_industry_to_file": {
                    "Example Industry": "Example.json",
                    "Second Industry": "Second.json",
                }
            },
        )

        second = get_standards_catalog(self.data_root)
        second_sasb = next(
            framework for framework in second["frameworks"] if framework["id"] == "sasb"
        )
        cache_after_second = standards_service._get_standards_catalog_cached.cache_info()

        self.assertEqual(first_sasb["scope_count"], 1)
        self.assertEqual(second_sasb["scope_count"], 2)
        self.assertEqual(
            {scope["id"] for scope in second_sasb["groups"][0]["scopes"]},
            {"Example Industry", "Second Industry"},
        )
        self.assertEqual(cache_after_second.misses, cache_after_first.misses + 1)

    def test_directory_membership_change_invalidates_the_cached_catalog(self) -> None:
        directory = self.data_root / "cdp_metrics"
        self._write_json(directory / "climate.json", [])
        signature_before = standards_service._catalog_signature(self.data_root)
        first = get_standards_catalog(self.data_root)
        cache_after_first = standards_service._get_standards_catalog_cached.cache_info()

        self._write_json(directory / "water.json", [])
        signature_after = standards_service._catalog_signature(self.data_root)
        second = get_standards_catalog(self.data_root)
        cache_after_second = standards_service._get_standards_catalog_cached.cache_info()

        first_cdp = next(
            framework for framework in first["frameworks"] if framework["id"] == "cdp"
        )
        second_cdp = next(
            framework for framework in second["frameworks"] if framework["id"] == "cdp"
        )
        self.assertNotEqual(signature_after, signature_before)
        self.assertEqual(first_cdp["scope_count"], 1)
        self.assertEqual(second_cdp["scope_count"], 2)
        self.assertEqual(cache_after_second.misses, cache_after_first.misses + 1)

    def test_metric_json_change_is_reloaded_without_rebuilding_the_catalog(self) -> None:
        self._write_sasb_scope([{"Metric": "Initial metric"}])
        first = get_standard_metrics(
            "sasb",
            "Example Industry",
            data_root=self.data_root,
        )
        unchanged = get_standard_metrics(
            "sasb",
            "Example Industry",
            data_root=self.data_root,
        )
        catalog_before_update = standards_service._get_standards_catalog_cached.cache_info()
        json_before_update = standards_service._read_json_cached.cache_info()

        metric_path = self.data_root / "sasb_metrics" / "Example.json"
        metric_signature_before = standards_service._path_signature(metric_path)
        self._write_json(
            metric_path,
            [
                {"Metric": "Updated metric with longer content"},
                {"Metric": "Second updated metric"},
            ],
        )
        metric_signature_after = standards_service._path_signature(metric_path)
        updated = get_standard_metrics(
            "sasb",
            "Example Industry",
            data_root=self.data_root,
        )
        catalog_after_update = standards_service._get_standards_catalog_cached.cache_info()
        json_after_update = standards_service._read_json_cached.cache_info()

        self.assertEqual(first, unchanged)
        self.assertNotEqual(metric_signature_after, metric_signature_before)
        self.assertEqual(updated["total_metrics"], 2)
        self.assertEqual(
            [metric["name"] for metric in updated["metrics"]],
            ["Updated metric with longer content", "Second updated metric"],
        )
        self.assertEqual(
            catalog_after_update.misses,
            catalog_before_update.misses,
        )
        self.assertEqual(json_after_update.misses, json_before_update.misses + 1)

    def test_legacy_sasb_manifest_without_groups_uses_the_single_group_fallback(self) -> None:
        self._write_sasb_scope([{"Metric": "Legacy metric", "Code": "LEGACY-1"}])

        catalog = get_standards_catalog(self.data_root)
        sasb = next(
            framework for framework in catalog["frameworks"] if framework["id"] == "sasb"
        )
        result = get_standard_metrics(
            "sasb",
            "Example Industry",
            data_root=self.data_root,
        )

        self.assertEqual(
            sasb["groups"],
            [
                {
                    "id": "industries",
                    "label": "Industries",
                    "scopes": [
                        {"id": "Example Industry", "label": "Example Industry"}
                    ],
                }
            ],
        )
        self.assertEqual(result["group"], {"id": "industries", "label": "Industries"})
        self.assertEqual(result["total_metrics"], 1)
        self.assertEqual(result["metrics"][0]["id"], "sasb:industries:Example Industry:1")

    def test_metric_rows_are_normalized_across_supported_key_styles(self) -> None:
        self._write_sasb_scope(
            [
                {
                    "Metric": "  Uppercase metric  ",
                    "Code": float("nan"),
                    "Topic": " Topic A ",
                    "Category": " Quantitative ",
                    "Type": " Disclosure ",
                    "Unit": " % ",
                    "Standard": " Standard A ",
                    "Definition": " Full definition ",
                    "Simple Definition": " Simple definition ",
                },
                {
                    "metric": "lowercase metric",
                    "code": " code-2 ",
                    "topic": "topic b",
                    "category": "qualitative",
                    "type": "activity metric",
                    "unit": "number",
                    "standard": "standard b",
                    "definition": "definition b",
                    "simple_definition": "simple b",
                },
                {},
            ]
        )

        result = get_standard_metrics(
            " sasb ",
            "Example Industry",
            data_root=self.data_root,
        )

        self.assertEqual(result["total_metrics"], 3)
        self.assertEqual(
            result["metrics"][0],
            {
                "id": "sasb:industries:Example Industry:1",
                "code": None,
                "name": "Uppercase metric",
                "topic": "Topic A",
                "category": "Quantitative",
                "type": "Disclosure",
                "unit": "%",
                "standard": "Standard A",
                "definition": "Full definition",
                "simple_definition": "Simple definition",
            },
        )
        self.assertEqual(result["metrics"][1]["code"], "code-2")
        self.assertEqual(result["metrics"][1]["name"], "lowercase metric")
        self.assertEqual(result["metrics"][2]["name"], "Metric 3")
        self.assertIsNone(result["metrics"][2]["definition"])

    def test_manifest_path_traversal_is_rejected_even_when_target_exists(self) -> None:
        directory = self.data_root / "sasb_metrics"
        self._write_json(self.data_root / "outside.json", [])
        self._write_json(
            directory / "manifest.json",
            {"semi_industry_to_file": {"Unsafe": "../outside.json"}},
        )

        with self.assertRaisesRegex(StandardsDataError, "unsafe file reference"):
            get_standards_catalog(self.data_root)

    def test_malformed_manifest_json_has_a_controlled_data_error(self) -> None:
        path = self.data_root / "sasb_metrics" / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"semi_industry_to_file":', encoding="utf-8")

        with self.assertRaisesRegex(StandardsDataError, "manifest.json"):
            get_standards_catalog(self.data_root)

    def test_malformed_metric_json_has_a_controlled_data_error(self) -> None:
        directory = self.data_root / "sasb_metrics"
        self._write_json(
            directory / "manifest.json",
            {"semi_industry_to_file": {"Example Industry": "Example.json"}},
        )
        (directory / "Example.json").write_text("[not valid json", encoding="utf-8")

        with self.assertRaisesRegex(StandardsDataError, "Example.json"):
            get_standard_metrics(
                "sasb",
                "Example Industry",
                data_root=self.data_root,
            )

    def test_metric_file_must_be_an_array_of_objects(self) -> None:
        for payload in ({"Metric": "wrong container"}, ["wrong row"]):
            with self.subTest(payload=payload):
                self._write_sasb_scope(payload)
                with self.assertRaises(StandardsDataError):
                    get_standard_metrics(
                        "sasb",
                        "Example Industry",
                        data_root=self.data_root,
                    )


if __name__ == "__main__":
    unittest.main()
