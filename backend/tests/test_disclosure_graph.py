from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from esg_encoding.services import disclosure_graph_service as graph_service
from esg_encoding.services.disclosure_graph_service import (
    DisclosureGraphNotFound,
    build_company_disclosure_graph,
    build_company_graph_neighbors,
    build_report_disclosure_graph,
)


class _FakeFileManager:
    def __init__(self, root: Path, files: dict[str, dict]):
        self.compliance_outputs = root / "compliance"
        self.embeddings_outputs = root / "embeddings"
        self.compliance_outputs.mkdir(parents=True)
        self.embeddings_outputs.mkdir(parents=True)
        self.metadata = {"files": files}

    def get_file_info(self, file_id: str, user_id=None):
        value = self.metadata["files"].get(file_id)
        if not isinstance(value, dict):
            return None
        if user_id is not None and value.get("user_id") != user_id:
            return None
        return value


class _FakeCompanyRegistry:
    def __init__(self, company: dict):
        self.company = company

    def get_company(self, company_id: str, user_id=None):
        if company_id != self.company.get("company_id"):
            return None
        if user_id is not None and user_id != self.company.get("user_id"):
            return None
        return dict(self.company)


def _standard_scope():
    return {
        "framework": {
            "id": "sasb",
            "name": "SASB",
            "as_of": "Jan 2026",
            "source_url": "https://example.test/sasb",
        },
        "group": {"id": "technology", "label": "Technology"},
        "scope": {"id": "Example Scope", "label": "Example Scope"},
        "metrics": [
            {
                "code": "TC-EX-100a.1",
                "name": "(1) Technical employees",
                "topic": "Workforce",
                "category": "Quantitative",
                "type": "Disclosure Topics & Metrics",
                "unit": "%",
                "simple_definition": "Technical employee representation.",
                "definition": "Long technical definition one.",
            },
            {
                "code": "TC-EX-100a.1",
                "name": "(2) All other employees",
                "topic": "Workforce",
                "category": "Quantitative",
                "type": "Disclosure Topics & Metrics",
                "unit": "%",
                "simple_definition": "Other employee representation.",
                "definition": "Long technical definition two.",
            },
            {
                "code": "TC-EX-200a.1",
                "name": "Data security approach",
                "topic": "Data Security",
                "category": "Discussion and Analysis",
                "type": "Disclosure Topics & Metrics",
                "unit": None,
                "simple_definition": "How security is managed.",
                "definition": "Long technical definition three.",
            },
        ],
    }


def _assessment(report_id: str, first_status: str = "fully_disclosed"):
    return {
        "report_id": report_id,
        "framework": "SASB",
        "total_metrics_analyzed": 3,
        "overall_compliance_score": 0.5,
        "disclosure_summary": {
            "fully_disclosed": 1,
            "partially_disclosed": 1,
            "not_disclosed": 1,
        },
        "metric_analyses": [
            {
                "metric_id": "m1",
                "metric_code": "TC-EX-100a.1",
                "metric_name": "(1) Technical employees",
                "disclosure_status": first_status,
                "value": 25,
                "unit": "%",
                "value_status": "exact",
                "page": 4,
                "reasoning": "The technical employee value is explicit.",
                "year_values": [
                    {
                        "year": 2025,
                        "value": 25,
                        "unit": "%",
                        "page": 4,
                        "evidence_segment_id": "s1",
                    }
                ],
                "evidence_sources": [
                    {
                        "source_type": "report_page",
                        "data_page": 4,
                        "segment_id": "s1",
                    }
                ],
            },
            {
                "metric_id": "m2",
                "Code": "TC-EX-100a.1",
                "Metric": "(2) All other employees",
                "Disclosure Status": "partially_disclosed",
                "Value": "n/a",
                "Unit": "%",
                "Page": 4,
                "LLM Analysis": "The report uses a broader proxy category.",
                "evidence_sources": [
                    {
                        "source_type": "report_page",
                        "data_page": 4,
                        "segment_id": "s2",
                    }
                ],
            },
            {
                "metric_id": "m3",
                "metric_code": "TC-EX-200a.1",
                "metric_name": "Data security approach",
                "disclosure_status": "not_disclosed",
                "value": "n/a",
                "reasoning": "No direct disclosure.",
                "evidence_segments": ["s3"],
            },
        ],
    }


class DisclosureGraphProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.files = {
            "r1": {
                "file_id": "r1",
                "user_id": 7,
                "file_type": "report",
                "original_name": "Report 2025.pdf",
                "report_year": None,
                "status": "processed",
                "page_count": 20,
                "framework": "SASB",
                "semi_industry": "Example Scope",
            },
            "r2": {
                "file_id": "r2",
                "user_id": 7,
                "file_type": "report",
                "original_name": "Report 2024.pdf",
                "report_year": 2024,
                "status": "processed",
                "page_count": 18,
                "framework": "SASB",
                "semi_industry": "Example Scope",
            },
        }
        self.file_manager = _FakeFileManager(self.root, self.files)
        for report_id in self.files:
            self._write_report_artifacts(report_id)
            self._write_assessment(report_id, _assessment(report_id))
        self.company = {
            "company_id": "company-1",
            "company_name": "Example Company",
            "user_id": 7,
            "report_ids": ["r1", "r2"],
            "assessment_outputs": [
                {"scope_key": "Example Scope", "json_filename": "aggregate.json"}
            ],
            "status": "ready",
            "analysis_version": 2,
            "stale": False,
        }
        # Deliberately different aggregate content. The company graph must not
        # project this as the two reports' disclosure judgments.
        (self.file_manager.compliance_outputs / "aggregate.json").write_text(
            json.dumps({"metric_analyses": [_assessment("aggregate")["metric_analyses"][0]]}),
            encoding="utf-8",
        )
        self.patches = [
            patch.object(graph_service, "file_manager", self.file_manager),
            patch.object(
                graph_service,
                "company_registry",
                _FakeCompanyRegistry(self.company),
            ),
            patch.object(
                graph_service,
                "get_standard_metrics",
                side_effect=lambda *args, **kwargs: _standard_scope(),
            ),
        ]
        for active_patch in self.patches:
            active_patch.start()
        graph_service._read_json_snapshot.cache_clear()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        graph_service._read_json_snapshot.cache_clear()
        self.tempdir.cleanup()

    def _write_assessment(self, report_id: str, payload: dict):
        filename = f"Example Scope_{report_id}_compliance.json"
        (self.file_manager.compliance_outputs / filename).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        manifest = {
            "file_id": report_id,
            "framework": "SASB",
            "default_scope_key": "Example Scope",
            "outputs": [
                {"scope_key": "Example Scope", "json_filename": filename}
            ],
        }
        (self.file_manager.compliance_outputs / f"{report_id}_compliance_manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

    def _write_report_artifacts(self, report_id: str):
        segments_path = self.file_manager.embeddings_outputs / f"{report_id}_segments.json"
        corpus_path = self.file_manager.embeddings_outputs / f"{report_id}_corpus.json"
        segments_path.write_text(
            json.dumps(
                [
                    {"segment_id": "s1", "content": "cell one", "page_number": 4, "segment_type": "table_cell"},
                    {"segment_id": "s2", "content": "cell two", "page_number": 4, "segment_type": "table_cell"},
                    {"segment_id": "s3", "content": "candidate paragraph", "page_number": 9, "segment_type": "text"},
                ]
            ),
            encoding="utf-8",
        )
        corpus_path.write_text(
            json.dumps(
                {
                    "evidence_blocks": [
                        {
                            "block_id": "table-1",
                            "block_type": "table",
                            "primary_segment_id": "s1",
                            "source_segment_ids": ["s1", "s2"],
                            "full_content": "Complete table with both employee rows",
                            "page_number": 4,
                            "section_path": ["Workforce"],
                            "source_table_id": "T1",
                            "content_hash": "table-hash",
                        },
                        {
                            "block_id": "paragraph-1",
                            "block_type": "paragraph",
                            "primary_segment_id": "s3",
                            "source_segment_ids": ["s3"],
                            "full_content": "Complete candidate paragraph",
                            "page_number": 9,
                            "section_path": ["Security"],
                            "content_hash": "paragraph-hash",
                        },
                    ],
                    "segment_to_block_id": {
                        "s1": "table-1",
                        "s2": "table-1",
                        "s3": "paragraph-1",
                    },
                }
            ),
            encoding="utf-8",
        )
        self.files[report_id]["segments_path"] = str(segments_path)
        self.files[report_id]["metric_retrieval_corpus_path"] = str(corpus_path)

    def test_report_graph_preserves_same_code_submetrics_and_status_totals(self):
        graph = build_report_disclosure_graph(file_id="r1", user_id=7)

        disclosures = [node for node in graph.nodes if node.type == "disclosure"]
        shared_code_metrics = [
            node
            for node in graph.nodes
            if node.type == "metric" and node.properties.get("code") == "TC-EX-100a.1"
        ]
        self.assertEqual(len(disclosures), 3)
        self.assertEqual(len(shared_code_metrics), 2)
        self.assertEqual(len({node.id for node in shared_code_metrics}), 2)
        self.assertEqual(
            graph.stats.disclosure_statuses,
            {
                "fully_disclosed": 1,
                "not_disclosed": 1,
                "partially_disclosed": 1,
            },
        )
        self.assertNotIn("evidence", graph.stats.node_types)
        report = next(node for node in graph.nodes if node.type == "report")
        self.assertEqual(report.properties["report_year"], 2025)
        self.assertEqual(report.properties["overall_score"], 0.5)
        self.assertEqual(report.properties["total_metrics_analyzed"], 3)
        self.assertEqual(
            next(node for node in shared_code_metrics if "Technical" in node.label).properties["definition"],
            "Technical employee representation.",
        )
        dump = getattr(graph, "model_dump", graph.dict)
        public_payload = dump()
        self.assertIn("kind", public_payload["nodes"][0])
        self.assertNotIn("type", public_payload["nodes"][0])
        self.assertIn("kind", public_payload["edges"][0])
        self.assertTrue(
            all(node["group_id"] == "TC-EX-100a.1" for node in public_payload["nodes"] if node["kind"] == "metric" and node["properties"].get("code") == "TC-EX-100a.1")
        )

        repeated = build_report_disclosure_graph(file_id="r1", user_id=7)
        self.assertEqual(graph.graph_id, repeated.graph_id)
        self.assertEqual(graph.graph_revision, repeated.graph_revision)
        self.assertEqual(
            [node.id for node in graph.nodes],
            [node.id for node in repeated.nodes],
        )

    def test_evidence_expansion_uses_complete_blocks_and_marks_candidates(self):
        graph = build_report_disclosure_graph(
            file_id="r1",
            user_id=7,
            include_evidence=True,
            evidence_limit=8,
        )

        evidence_nodes = [node for node in graph.nodes if node.type == "evidence"]
        self.assertEqual(len(evidence_nodes), 2)
        table = next(node for node in evidence_nodes if node.properties.get("block_id") == "table-1")
        self.assertEqual(
            table.properties["content"],
            "Complete table with both employee rows",
        )
        self.assertIn("supported_by", graph.stats.edge_types)
        candidate_edges = [edge for edge in graph.edges if edge.type == "candidate_evidence"]
        self.assertTrue(candidate_edges)
        not_disclosed = next(
            node
            for node in graph.nodes
            if node.type == "disclosure" and node.properties["status"] == "not_disclosed"
        )
        self.assertTrue(any(edge.source == not_disclosed.id for edge in candidate_edges))

    def test_requested_scope_and_ownership_are_strict(self):
        with self.assertRaises(DisclosureGraphNotFound):
            build_report_disclosure_graph(
                file_id="r1",
                user_id=7,
                scope_key="Unknown Scope",
            )
        with self.assertRaises(DisclosureGraphNotFound):
            build_report_disclosure_graph(file_id="r1", user_id=99)

    def test_incomplete_or_inconsistent_assessments_are_not_projected(self):
        self.files["r1"]["status"] = "failed"
        with self.assertRaisesRegex(DisclosureGraphNotFound, "not complete"):
            build_report_disclosure_graph(file_id="r1", user_id=7)

        self.files["r1"]["status"] = "processed"
        inconsistent = _assessment("r1")
        inconsistent["total_metrics_analyzed"] = 4
        self._write_assessment("r1", inconsistent)
        graph_service._read_json_snapshot.cache_clear()
        with self.assertRaisesRegex(DisclosureGraphNotFound, "metric count"):
            build_report_disclosure_graph(file_id="r1", user_id=7)

        duplicated = _assessment("r1")
        duplicated["metric_analyses"].append(dict(duplicated["metric_analyses"][0]))
        duplicated["total_metrics_analyzed"] = 4
        duplicated["disclosure_summary"]["fully_disclosed"] = 2
        self._write_assessment("r1", duplicated)
        graph_service._read_json_snapshot.cache_clear()
        with self.assertRaisesRegex(DisclosureGraphNotFound, "duplicate metric"):
            build_report_disclosure_graph(file_id="r1", user_id=7)

    def test_company_graph_projects_each_report_assessment_not_aggregate(self):
        graph = build_company_disclosure_graph(
            company_id="company-1",
            user_id=7,
        )

        disclosures = [node for node in graph.nodes if node.type == "disclosure"]
        reports = [node for node in graph.nodes if node.type == "report"]
        self.assertEqual(len(reports), 2)
        self.assertEqual(len(disclosures), 6)
        self.assertEqual(sum(graph.stats.disclosure_statuses.values()), 6)
        self.assertEqual({node.properties["aggregation_scope"] for node in disclosures}, {"report"})
        for report in reports:
            report_disclosures = [
                edge
                for edge in graph.edges
                if edge.type == "has_disclosure" and edge.source == report.id
            ]
            self.assertEqual(len(report_disclosures), 3)

        filtered = build_company_disclosure_graph(
            company_id="company-1",
            user_id=7,
            selected_report_ids=["r2"],
        )
        self.assertEqual(filtered.stats.node_types["report"], 1)
        self.assertEqual(filtered.stats.node_types["disclosure"], 3)
        with self.assertRaises(DisclosureGraphNotFound):
            build_company_disclosure_graph(
                company_id="company-1",
                user_id=7,
                selected_report_ids=["not-owned"],
            )

    def test_six_reports_with_twenty_four_metrics_keep_144_disclosures(self):
        report_ids = [f"dell-{year}" for year in range(2019, 2025)]
        rows = []
        for metric_index in range(24):
            code = "TC-HW-000.A" if metric_index < 2 else f"TC-HW-{metric_index:03d}.A"
            status = (
                "fully_disclosed"
                if metric_index % 3 == 0
                else "partially_disclosed"
                if metric_index % 3 == 1
                else "not_disclosed"
            )
            rows.append(
                {
                    "metric_id": f"metric-{metric_index}",
                    "metric_code": code,
                    "metric_name": f"Dell metric item {metric_index}",
                    "disclosure_status": status,
                    "value": metric_index if status != "not_disclosed" else "n/a",
                    "unit": "%",
                    "reasoning": f"Assessment result {metric_index}",
                }
            )
        assessment = {
            "framework": "SASB",
            "total_metrics_analyzed": 24,
            "overall_compliance_score": 0.5,
            "disclosure_summary": {
                "fully_disclosed": 8,
                "partially_disclosed": 8,
                "not_disclosed": 8,
            },
            "metric_analyses": rows,
        }
        self.company["report_ids"] = report_ids
        for offset, report_id in enumerate(report_ids):
            self.files[report_id] = {
                "file_id": report_id,
                "user_id": 7,
                "file_type": "report",
                "original_name": f"Dell {2019 + offset}.pdf",
                "report_year": 2019 + offset,
                "status": "processed",
                "framework": "SASB",
                "semi_industry": "Example Scope",
            }
            self._write_assessment(report_id, {**assessment, "report_id": report_id})
        graph_service._read_json_snapshot.cache_clear()

        graph = build_company_disclosure_graph(company_id="company-1", user_id=7)
        disclosures = [node for node in graph.nodes if node.type == "disclosure"]
        self.assertEqual(len(disclosures), 144)
        self.assertEqual(sum(graph.stats.disclosure_statuses.values()), 144)
        for report_id in report_ids:
            report_node_id = f"report:{report_id}"
            self.assertEqual(
                len(
                    [
                        edge
                        for edge in graph.edges
                        if edge.type == "has_disclosure" and edge.source == report_node_id
                    ]
                ),
                24,
            )
        shared_code_metrics = [
            node
            for node in graph.nodes
            if node.type == "metric" and node.properties.get("code") == "TC-HW-000.A"
        ]
        self.assertEqual(len(shared_code_metrics), 2)

    def test_company_neighbors_expand_metric_to_report_evidence(self):
        graph = build_company_disclosure_graph(
            company_id="company-1",
            user_id=7,
        )
        metric = next(node for node in graph.nodes if node.type == "metric")
        neighborhood = build_company_graph_neighbors(
            company_id="company-1",
            user_id=7,
            node_id=metric.id,
            depth=2,
        )
        self.assertIn("disclosure", neighborhood.stats.node_types)
        self.assertIn("report", neighborhood.stats.node_types)
        self.assertIn("evidence", neighborhood.stats.node_types)
        evidence = [node for node in neighborhood.nodes if node.type == "evidence"]
        self.assertTrue(evidence)
        self.assertTrue(all(node.properties.get("block_id") == "table-1" for node in evidence))


if __name__ == "__main__":
    unittest.main()
