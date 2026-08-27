from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from esg_encoding.content_extractor import enrich_document_with_pdf_links
from esg_encoding.disclosure_inference import DisclosureInferenceEngine
from esg_encoding.exceptions import DisclosureAnalysisError
from esg_encoding.models import (
    DocumentContent,
    MetricRetrievalResult,
    ProcessingConfig,
    ReportContent,
    RetrievalResult,
    SegmentEmbedding,
    TextSegment,
)
from esg_encoding.retrieval.dual_channel import DualChannelRetriever
from esg_encoding.retrieval.keyword import KeywordRetriever
from esg_encoding.retrieval.metric_profile import (
    build_metric_retrieval_profile,
    build_profile_index,
    find_metric_profile,
    load_all_metric_profiles,
    normalize_metric_text,
)
from esg_encoding.retrieval.scoring import (
    _metric_evidence_quality_adjustment,
    _qualitative_relevance_adjustment,
    _segment_structure_bonus,
    _topic_relevance_adjustment,
)
from esg_encoding.retrieval.semantic import SemanticRetriever


def _table_segment(
    segment_id: str,
    segment_type: str,
    content: str,
    *,
    page: int = 1,
    table_id: str = "table-1",
    row_index: int = 1,
    col_index: int | None = None,
    row_header: str | None = None,
    col_header: str | None = None,
    value_text: str | None = None,
    links: list[dict] | None = None,
) -> TextSegment:
    data = {
        "table_id": table_id,
        "row_index": row_index,
    }
    if col_index is not None:
        data["col_index"] = col_index
    if row_header is not None:
        data["row_header"] = row_header
    if col_header is not None:
        data["col_header"] = col_header
    if value_text is not None:
        data["value_text"] = value_text
    if links:
        data["pdf_links"] = links
    return TextSegment(
        segment_id=segment_id,
        content=content,
        page_number=page,
        position_y=float(row_index * 10 + (col_index or 0)),
        position_x=float(col_index) if col_index is not None else None,
        segment_type=segment_type,
        source_table_id=table_id,
        row_header=row_header,
        col_header=col_header,
        value_text=value_text,
        structured_data=data,
    )


def _report(segments: list[TextSegment]) -> ReportContent:
    document = DocumentContent(
        document_id="test-document",
        file_path="test.pdf",
        segments=segments,
        markdown_content="",
    )
    return ReportContent(document_id="test-document", document_content=document, embeddings=[])


def _metric(metric_id: str, code: str, name: str, unit: str = ""):
    return SimpleNamespace(
        metric_id=metric_id,
        metric_code=code,
        metric_name=name,
        unit=unit,
        definition="",
        description="",
        keywords=[],
        sasb_category="Quantitative",
        sasb_type="Quantitative",
        sasb_topic="",
        source="SASB",
    )


class PdfLinkTests(unittest.TestCase):
    def test_internal_link_metadata_is_attached_and_external_is_ignored(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "links.pdf"
            pdf = fitz.open()
            source_page = pdf.new_page()
            pdf.new_page()
            source_page = pdf[0]
            source_page.insert_text((72, 72), "Energy details")
            source_page.insert_text((72, 110), "External details")
            source_page.insert_link(
                {"kind": fitz.LINK_GOTO, "from": source_page.search_for("Energy details")[0], "page": 1}
            )
            source_page.insert_link(
                {"kind": fitz.LINK_URI, "from": source_page.search_for("External details")[0], "uri": "https://example.com/data"}
            )
            pdf.save(pdf_path)
            pdf.close()

            internal = TextSegment(
                segment_id="p1-internal",
                content="Energy details",
                page_number=1,
                position_y=1,
                segment_type="text",
            )
            external = TextSegment(
                segment_id="p1-external",
                content="External details",
                page_number=1,
                position_y=2,
                segment_type="text",
            )
            target = TextSegment(
                segment_id="p2-data",
                content="Total energy consumed: 996 million kWh",
                page_number=2,
                position_y=1,
                segment_type="text",
            )
            document = DocumentContent(
                document_id="link-test",
                file_path=str(pdf_path),
                segments=[internal, external, target],
                markdown_content="",
            )
            previous = os.environ.get("REPORT_LINK_RESOLUTION_ENABLED")
            os.environ["REPORT_LINK_RESOLUTION_ENABLED"] = "true"
            try:
                summary = enrich_document_with_pdf_links(document)
            finally:
                if previous is None:
                    os.environ.pop("REPORT_LINK_RESOLUTION_ENABLED", None)
                else:
                    os.environ["REPORT_LINK_RESOLUTION_ENABLED"] = previous

            self.assertEqual(summary["internal"], 1)
            self.assertEqual(summary["external_ignored"], 1)
            self.assertEqual(document.content_revision, 2)
            internal_links = internal.structured_data["pdf_links"]
            self.assertEqual(internal_links[0]["target_page"], 2)
            self.assertEqual(internal_links[0]["link_type"], "internal")
            external_links = external.structured_data["pdf_links"]
            self.assertEqual(external_links[0]["link_type"], "external_ignored")

            # Current-version link metadata is idempotent and must not churn
            # cache revisions on every retrieval call.
            enrich_document_with_pdf_links(document)
            self.assertEqual(document.content_revision, 2)

    def test_duplicate_link_anchors_are_attached_to_distinct_table_rows(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "duplicate-links.pdf"
            pdf = fitz.open()
            source_page = pdf.new_page()
            pdf.new_page()
            pdf.new_page()
            source_page = pdf[0]
            anchor = "Inclusive workforce - Accountability"
            source_page.insert_text((72, 72), anchor)
            source_page.insert_text((72, 110), anchor)
            matches = source_page.search_for(anchor)
            source_page.insert_link(
                {"kind": fitz.LINK_GOTO, "from": matches[0], "page": 1}
            )
            source_page.insert_link(
                {"kind": fitz.LINK_GOTO, "from": matches[1], "page": 2}
            )
            pdf.save(pdf_path)
            pdf.close()

            first = _table_segment(
                "employee-row",
                "table_row",
                (
                    "Employee engagement | Inclusive workforce - Accountability | "
                    "Employee engagement as a percentage: 87% | SASB TC-SI-330a.2"
                ),
                row_index=6,
            )
            second = _table_segment(
                "pay-equity-row",
                "table_row",
                "Pay equity | Inclusive workforce - Accountability | GRI 405-2",
                row_index=11,
                links=[
                    {
                        "link_type": "internal",
                        "anchor_text": anchor,
                        "source_page": 1,
                        "target_page": 2,
                    }
                ],
            )
            document = DocumentContent(
                document_id="duplicate-link-test",
                file_path=str(pdf_path),
                segments=[first, second],
                markdown_content="",
            )

            enrich_document_with_pdf_links(document)

            first_links = (first.structured_data or {}).get("pdf_links") or []
            second_links = (second.structured_data or {}).get("pdf_links") or []
            self.assertEqual([item["target_page"] for item in first_links], [2])
            self.assertEqual([item["target_page"] for item in second_links], [3])
            self.assertTrue(all(item["resolution_version"] == 2 for item in first_links + second_links))

    def test_linked_page_channel_prefers_internal_target_page(self):
        code = "TC-X-100a.1"
        source = _table_segment(
            "source-row",
            "table_row",
            f"Energy use | Reference indices: SASB {code}",
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": "Energy details",
                    "source_page": 1,
                    "target_page": 2,
                }
            ],
        )
        target = TextSegment(
            segment_id="target-data",
            content="Total energy consumed was 996 million kWh in FY2024.",
            page_number=2,
            position_y=1,
            segment_type="text",
        )
        report = _report([source, target])
        config = ProcessingConfig(top_k=10)
        object.__setattr__(config, "use_keyword_retrieval", True)
        object.__setattr__(config, "use_semantic_retrieval", False)
        result = DualChannelRetriever(config).retrieve_for_metric(
            report,
            _metric(code, code, "Total energy consumed", "kWh"),
        )
        linked = [item for item in result.combined_results if "linked_page" in item.retrieval_type]
        self.assertTrue(linked)
        self.assertEqual(result.combined_results[0].segment_id, "target-data")
        self.assertEqual(linked[0].page_number, 2)
        self.assertEqual(linked[0].link_source_page, 1)
        self.assertEqual(linked[0].link_target_page, 2)
        self.assertEqual(linked[0].link_anchor_text, "Energy details")
        self.assertEqual(linked[0].link_source_segment_id, "source-row")

        engine = object.__new__(DisclosureInferenceEngine)
        paired, source_context, source_segment_id = engine._build_linked_evidence_context(
            report,
            linked[0],
            target.content,
            max_chars=2400,
        )
        self.assertEqual(source_segment_id, "source-row")
        self.assertIn("Energy details", source_context)
        self.assertIn("[Internal PDF Link Source Context]", paired)
        self.assertIn(f"SASB {code}", paired)
        self.assertIn("[Internal PDF Link Target Context]", paired)
        self.assertIn("996 million kWh", paired)

        sources = engine._build_evidence_sources(
            [
                {
                    "segment_id": linked[0].segment_id,
                    "page_number": linked[0].page_number,
                    "score": linked[0].score,
                    "link_source_page": linked[0].link_source_page,
                    "link_target_page": linked[0].link_target_page,
                    "link_anchor_text": linked[0].link_anchor_text,
                    "link_source_segment_id": source_segment_id,
                    "link_source_context": source_context,
                }
            ]
        )
        self.assertEqual(sources[0]["anchor_text"], "Energy details")
        self.assertIn("Energy details", sources[0]["source_context"])

    def test_linked_page_channel_follows_five_page_continuation_table(self):
        code = "TC-SI-330a.3"
        source = _table_segment(
            "index-row",
            "table_row",
            f"Employees | Reference indices: SASB {code}",
            page=108,
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": (
                        "By the numbers - Global female representation, "
                        "U.S. race/ethnicity representation"
                    ),
                    "source_page": 108,
                    "target_page": 86,
                }
            ],
        )
        linked_tables = [
            TextSegment(
                segment_id="p86-gender",
                content="Global female representation | Overall | FY24 35.0%",
                page_number=86,
                position_y=1,
                segment_type="table",
            ),
            TextSegment(
                segment_id="p87-race-overall",
                content="U.S. race/ethnicity representation | Overall | Asian | FY24 16.4%",
                page_number=87,
                position_y=1,
                segment_type="table",
            ),
            TextSegment(
                segment_id="p88-race-leaders",
                content=(
                    "U.S. race/ethnicity representation (continued) | "
                    "People leader roles | Black or African American | FY24 3.8%"
                ),
                page_number=88,
                position_y=1,
                segment_type="table",
            ),
            TextSegment(
                segment_id="p89-race-technical",
                content=(
                    "U.S. race/ethnicity representation (continued) | "
                    "Technical | Hispanic or Latino | FY24 8.2%"
                ),
                page_number=89,
                position_y=1,
                segment_type="table",
            ),
            TextSegment(
                segment_id="p90-race-nontechnical",
                content=(
                    "U.S. race/ethnicity representation (continued) | "
                    "Non-technical roles | Hispanic or Latino | FY24 11.0%"
                ),
                page_number=90,
                position_y=1,
                segment_type="table",
            ),
        ]
        report = _report([source, *linked_tables])
        config = ProcessingConfig(top_k=10)
        object.__setattr__(config, "use_keyword_retrieval", True)
        object.__setattr__(config, "use_semantic_retrieval", False)

        previous = os.environ.get("REPORT_LINK_MAX_TARGET_PAGES_PER_METRIC")
        os.environ["REPORT_LINK_MAX_TARGET_PAGES_PER_METRIC"] = "5"
        try:
            retriever = DualChannelRetriever(config)
            cases = {
                "Percentage of (1) gender": 86,
                "Percentage of (2) diversity group representation for (a) executive management": 88,
                "Percentage of (2) diversity group representation for (b) non-executive management": 88,
                "Percentage of (2) diversity group representation for (c) technical employees": 89,
                "Percentage of (2) diversity group representation for (d) all other employees": 90,
            }
            results = {
                name: retriever.retrieve_for_metric(
                    report,
                    _metric(f"metric-{index}", code, name, "Percentage (%)"),
                )
                for index, name in enumerate(cases)
            }
        finally:
            if previous is None:
                os.environ.pop("REPORT_LINK_MAX_TARGET_PAGES_PER_METRIC", None)
            else:
                os.environ["REPORT_LINK_MAX_TARGET_PAGES_PER_METRIC"] = previous

        result = results[
            "Percentage of (2) diversity group representation for (c) technical employees"
        ]
        linked = [item for item in result.keyword_results if "linked_page" in item.retrieval_type]
        self.assertEqual({item.page_number for item in linked}, {86, 87, 88, 89, 90})
        self.assertEqual(linked[0].page_number, 89)
        technical = next(item for item in linked if item.page_number == 89)
        self.assertEqual(technical.link_source_page, 108)
        self.assertEqual(technical.link_target_page, 86)
        self.assertIn(89, {item.page_number for item in result.combined_results})
        for name, expected_page in cases.items():
            metric_linked = [
                item for item in results[name].keyword_results
                if "linked_page" in item.retrieval_type
            ]
            combined_linked = [
                item for item in results[name].combined_results
                if "linked_page" in item.retrieval_type
            ]
            self.assertTrue(metric_linked, name)
            self.assertTrue(combined_linked, name)
            self.assertEqual(metric_linked[0].page_number, expected_page, name)
            self.assertEqual(combined_linked[0].page_number, expected_page, name)

    def test_linked_page_pool_is_not_truncated_before_dynamic_rerank(self):
        code = "TC-SI-330a.3"
        source = _table_segment(
            "index-row",
            "table_row",
            f"Employees | Reference indices: SASB {code}",
            page=108,
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": "Global female representation",
                    "source_page": 108,
                    "target_page": 86,
                }
            ],
        )
        target_segments = [
            _table_segment(
                "technical-row",
                "table_row",
                "Global female representation - Technical roles | FY24 25.0%",
                page=86,
                table_id="gender-table",
                row_index=1,
                row_header="Technical roles",
            )
        ]
        for page in range(86, 94):
            for index in range(8):
                target_segments.append(
                    TextSegment(
                        segment_id=f"p{page}-candidate-{index}",
                        content=(
                            "Gender representation for technical employees "
                            f"supporting value {index + 1}%"
                        ),
                        page_number=page,
                        position_y=float(index + 2),
                        segment_type="text",
                    )
                )

        report = _report([source, *target_segments])
        config = ProcessingConfig(top_k=10)
        object.__setattr__(config, "use_keyword_retrieval", True)
        object.__setattr__(config, "use_semantic_retrieval", False)
        retriever = DualChannelRetriever(config)
        metric = _metric(
            "TC-SI-330a.3.03",
            code,
            "Percentage of (1) gender representation for (c) technical employees",
            "Percentage (%)",
        )
        profile = build_metric_retrieval_profile(metric)
        trigger = RetrievalResult(
            segment_id="index-row",
            content=source.content,
            page_number=108,
            score=0.97,
            retrieval_type="exact_code",
            metric_id=metric.metric_id,
        )

        linked = retriever._search_linked_pages(
            report,
            metric,
            profile,
            [trigger],
        )

        self.assertEqual(len(linked), 65)
        self.assertIn("technical-row", {item.segment_id for item in linked})
        self.assertEqual({item.page_number for item in linked}, set(range(86, 94)))

    def test_linked_page_context_includes_target_and_next_seven_pages_only(self):
        code = "TC-X-200a.1"
        source = _table_segment(
            "context-index-row",
            "table_row",
            f"Metric reference | SASB {code}",
            page=40,
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": "Metric details",
                    "source_page": 40,
                    "target_page": 20,
                }
            ],
        )
        page_segments = [
            TextSegment(
                segment_id=f"page-{page}",
                content=f"Page {page} metric context",
                page_number=page,
                position_y=1,
                segment_type="text",
            )
            for page in range(11, 30)
        ]
        report = _report([source, *page_segments])
        trigger = RetrievalResult(
            segment_id=source.segment_id,
            content=source.content,
            page_number=source.page_number,
            score=1.0,
            retrieval_type="exact_code",
            metric_id=code,
        )
        config = ProcessingConfig(top_k=10)

        previous = os.environ.get("REPORT_LINK_FORWARD_PAGE_COUNT")
        os.environ["REPORT_LINK_FORWARD_PAGE_COUNT"] = "7"
        try:
            targets = DualChannelRetriever(config)._linked_page_targets(report, [trigger])
        finally:
            if previous is None:
                os.environ.pop("REPORT_LINK_FORWARD_PAGE_COUNT", None)
            else:
                os.environ["REPORT_LINK_FORWARD_PAGE_COUNT"] = previous

        self.assertEqual(set(targets), set(range(20, 28)))
        self.assertEqual(targets[20]["context_offset"], 0)
        self.assertEqual(targets[27]["context_offset"], 7)
        self.assertNotIn(19, targets)
        self.assertNotIn(28, targets)


class RetrievalNoiseControlTests(unittest.TestCase):
    def _metric_with_topic(self):
        metric = _metric(
            "employee-engagement",
            "TC-X-900a.1",
            "Employee engagement as a percentage",
            "Percentage (%)",
        )
        metric.sasb_topic = "Recruiting & Managing a Global, Diverse & Skilled Workforce"
        return metric

    @staticmethod
    def _qualitative_metric():
        metric = _metric(
            "data-security-risk-management",
            "TC-X-230a.2",
            "Description of approach to identifying and addressing data security risks",
        )
        metric.sasb_category = "Qualitative"
        metric.sasb_type = "Discussion and Analysis"
        metric.keywords = ["data security", "risk management", "governance"]
        return metric

    def test_qualitative_adjustment_is_backward_compatible_without_segment(self):
        metric = self._qualitative_metric()
        score = _qualitative_relevance_adjustment(
            metric,
            "Our data security risk management policy defines governance and oversight.",
            ["data security", "risk management"],
            "paragraph_cluster",
        )
        self.assertIsInstance(score, float)
        self.assertGreater(score, 0.0)

    def test_qualitative_adjustment_uses_segment_review_metadata(self):
        metric = self._qualitative_metric()
        content = "Our data security risk management policy defines governance and oversight."
        anchors = ["data security", "risk management"]
        verified = TextSegment(
            segment_id="verified",
            content=content,
            page_number=1,
            position_y=1,
            segment_type="paragraph_cluster",
            review_status=" VERIFIED ",
        )
        needs_review = TextSegment(
            segment_id="needs-review",
            content=content,
            page_number=2,
            position_y=1,
            segment_type="paragraph_cluster",
            structured_data={"review_status": "needs_review"},
        )

        verified_score = _qualitative_relevance_adjustment(
            metric,
            verified.content,
            anchors,
            verified.segment_type,
            segment=verified,
        )
        needs_review_score = _qualitative_relevance_adjustment(
            metric,
            needs_review.content,
            anchors,
            needs_review.segment_type,
            segment=needs_review,
        )

        self.assertAlmostEqual(verified_score - needs_review_score, 0.16)

    def test_bm25_qualitative_retrieval_runs_for_reviewed_segments(self):
        metric = self._qualitative_metric()
        profile = build_metric_retrieval_profile(metric)
        content = "Our data security risk management policy defines governance and oversight."
        report = _report(
            [
                TextSegment(
                    segment_id="needs-review",
                    content=content,
                    page_number=1,
                    position_y=1,
                    segment_type="paragraph_cluster",
                    review_status="needs_review",
                ),
                TextSegment(
                    segment_id="verified",
                    content=content,
                    page_number=2,
                    position_y=1,
                    segment_type="paragraph_cluster",
                    review_status="verified",
                ),
            ]
        )

        results = KeywordRetriever(ProcessingConfig()).search_bm25(report, metric, profile)

        self.assertEqual(
            {result.segment_id for result in results},
            {"verified", "needs-review"},
        )
        self.assertTrue(all(isinstance(result.score, float) for result in results))

    def test_topic_unit_and_percentage_tokens_are_not_exact_aliases(self):
        metric = self._metric_with_topic()
        profile = build_metric_retrieval_profile(metric)
        aliases = {normalize_metric_text(value) for value in profile.aliases}

        self.assertIn("employee engagement as a percentage", aliases)
        self.assertNotIn(normalize_metric_text(metric.sasb_topic), aliases)
        self.assertNotIn(normalize_metric_text(metric.unit), aliases)
        self.assertTrue({"%", "percent", "percentage"}.isdisjoint(aliases))

        report = _report(
            [
                TextSegment(
                    segment_id="topic-only",
                    content=metric.sasb_topic,
                    page_number=1,
                    position_y=1,
                ),
                TextSegment(
                    segment_id="unit-only",
                    content="Percentage (%) percent %",
                    page_number=1,
                    position_y=2,
                ),
                TextSegment(
                    segment_id="identity",
                    content="Employee engagement as a percentage: 87%",
                    page_number=1,
                    position_y=3,
                ),
            ]
        )
        results = KeywordRetriever(ProcessingConfig()).search_exact_alias(
            report,
            metric,
            profile,
        )
        self.assertEqual([item.segment_id for item in results], ["identity"])

    def test_expected_unit_is_removed_from_dense_and_qwen_ranking_queries(self):
        metric = _metric(
            "TC-SI-330a.2",
            "TC-SI-330a.2",
            "Employee engagement as a percentage",
            "Percentage (%)",
        )
        profile = build_metric_retrieval_profile(metric)
        semantic = object.__new__(SemanticRetriever)

        self.assertNotIn("expected unit", profile.dense_query.lower())
        self.assertNotIn(
            "expected unit",
            semantic._build_rerank_instruction(metric).lower(),
        )

    def test_topic_is_bounded_bonus_but_unit_does_not_change_score(self):
        metric = self._metric_with_topic()
        segment = TextSegment(
            segment_id="topic-data",
            content=(
                "Recruiting & Managing a Global, Diverse & Skilled Workforce. "
                "Employee engagement result: 87%."
            ),
            page_number=1,
            position_y=1,
            segment_type="text",
        )
        self.assertGreater(_topic_relevance_adjustment(metric, segment.content), 0)
        self.assertEqual(
            _segment_structure_bonus(segment, expected_unit="Percentage (%)"),
            _segment_structure_bonus(segment, expected_unit="Metric tonnes"),
        )
        other_unit = self._metric_with_topic()
        other_unit.unit = "Metric tonnes"
        self.assertEqual(
            _metric_evidence_quality_adjustment(metric, segment, ["employee engagement"]),
            _metric_evidence_quality_adjustment(other_unit, segment, ["employee engagement"]),
        )

    def test_bm25_topic_only_content_does_not_qualify(self):
        metric = self._metric_with_topic()
        profile = build_metric_retrieval_profile(metric)
        report = _report(
            [
                TextSegment(
                    segment_id="topic-only",
                    content=metric.sasb_topic,
                    page_number=1,
                    position_y=1,
                ),
                TextSegment(
                    segment_id="metric-evidence",
                    content="Employee engagement survey result was 87 in FY2024.",
                    page_number=2,
                    position_y=1,
                ),
            ]
        )
        results = KeywordRetriever(ProcessingConfig()).search_bm25(report, metric, profile)
        result_ids = {item.segment_id for item in results}
        self.assertIn("metric-evidence", result_ids)
        self.assertNotIn("topic-only", result_ids)

    def test_code_index_is_limited_to_ten_and_real_linked_data_is_reserved(self):
        metric = self._metric_with_topic()
        profile = build_metric_retrieval_profile(metric)
        code_rows = [
            TextSegment(
                segment_id=f"index-{index}",
                content=f"Reporting frameworks index | SASB {metric.metric_code} | page {index + 10}",
                page_number=index + 1,
                position_y=1,
                segment_type="table_row",
            )
            for index in range(14)
        ]
        data_row = _table_segment(
            "data-row",
            "table_row",
            f"Employee engagement | SASB {metric.metric_code} | FY2024 87%",
            page=20,
            row_index=2,
        )
        value_cell = _table_segment(
            "data-value",
            "table_cell",
            "87%",
            page=20,
            row_index=2,
            col_index=2,
            col_header="FY2024",
            value_text="87%",
        )
        linked_data = TextSegment(
            segment_id="linked-data",
            content="Employee engagement as a percentage: 87%",
            page_number=30,
            position_y=1,
            segment_type="text",
        )
        report = _report([*code_rows, data_row, value_cell, linked_data])
        candidates = [
            RetrievalResult(
                segment_id=segment.segment_id,
                content=segment.content,
                page_number=segment.page_number,
                score=0.99 - index * 0.01,
                retrieval_type="rrf:exact_code",
                metric_id=metric.metric_id,
            )
            for index, segment in enumerate(code_rows)
        ]
        candidates.extend(
            [
                RetrievalResult(
                    segment_id=data_row.segment_id,
                    content=data_row.content,
                    page_number=data_row.page_number,
                    score=0.70,
                    retrieval_type="rrf:exact_code",
                    metric_id=metric.metric_id,
                ),
                RetrievalResult(
                    segment_id=linked_data.segment_id,
                    content=linked_data.content,
                    page_number=linked_data.page_number,
                    score=0.68,
                    retrieval_type="rrf:linked_page+bm25",
                    metric_id=metric.metric_id,
                    link_source_page=1,
                    link_target_page=30,
                ),
            ]
        )
        prepared = DualChannelRetriever(ProcessingConfig())._prepare_unified_rerank_candidates(
            candidates,
            report,
            profile,
            limit=46,
        )

        self.assertEqual(
            sum("code_index_evidence" in item.retrieval_type for item in prepared),
            10,
        )
        self.assertEqual(prepared[0].segment_id, "linked-data")
        self.assertIn("real_data_evidence", prepared[0].retrieval_type)
        self.assertIn("data-row", {item.segment_id for item in prepared})

    def test_exact_code_row_is_the_only_link_trigger_when_bm25_hits_adjacent_metric(self):
        code = "TC-SI-330a.2"
        metric = _metric(
            "employee-engagement",
            code,
            "Employee engagement as a percentage",
            "Percentage (%)",
        )
        engagement_row = _table_segment(
            "engagement-row",
            "table_row",
            f"Employee engagement | FY2024: 87% | SASB {code}",
            page=108,
            row_index=6,
        )
        engagement_value = _table_segment(
            "engagement-value",
            "table_cell",
            "Employee engagement as a percentage: 87%",
            page=108,
            row_index=6,
            col_index=1,
            col_header="FY2024",
            value_text="Employee engagement as a percentage: 87%",
        )
        adjacent_row = _table_segment(
            "employees-row",
            "table_row",
            "Employees | Global female representation | SASB TC-SI-330a.3",
            page=108,
            row_index=9,
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": "Global female representation",
                    "source_page": 108,
                    "target_page": 86,
                }
            ],
        )
        report = _report([engagement_row, engagement_value, adjacent_row])
        exact_result = RetrievalResult(
            segment_id=engagement_row.segment_id,
            content=engagement_row.content,
            page_number=engagement_row.page_number,
            score=1.0,
            retrieval_type="exact_code+table_row_context",
            metric_id=metric.metric_id,
        )
        adjacent_bm25 = RetrievalResult(
            segment_id=adjacent_row.segment_id,
            content=adjacent_row.content,
            page_number=adjacent_row.page_number,
            score=0.9,
            retrieval_type="bm25",
            metric_id=metric.metric_id,
        )
        config = ProcessingConfig(top_k=10)
        object.__setattr__(config, "use_semantic_retrieval", False)
        object.__setattr__(config, "use_reranker", False)
        retriever = DualChannelRetriever(config)
        retriever.keyword_retriever.search_exact_code = lambda *args, **kwargs: [exact_result]
        retriever.keyword_retriever.search_exact_alias = lambda *args, **kwargs: []
        retriever.keyword_retriever.search_bm25 = lambda *args, **kwargs: [adjacent_bm25]
        captured_triggers = []

        def fake_linked_search(
            target_report,
            target_metric,
            profile,
            trigger_results,
            semantic_expansion=None,
        ):
            captured_triggers.extend(item.segment_id for item in trigger_results)
            return []

        retriever._search_linked_pages = fake_linked_search
        with patch.dict(
            os.environ,
            {"REPORT_EXACT_CODE_DATA_SHORT_CIRCUIT": "false"},
        ):
            result = retriever.retrieve_for_metric(report, metric)

        self.assertEqual(captured_triggers, [engagement_row.segment_id])
        self.assertNotIn(adjacent_row.segment_id, captured_triggers)
        self.assertEqual(result.combined_results[0].segment_id, engagement_row.segment_id)

    def test_exact_code_cell_does_not_join_same_row_number_on_next_page(self):
        code = "TC-SI-330a.2"
        metric = _metric(
            "employee-engagement",
            code,
            "Employee engagement as a percentage",
            "Percentage (%)",
        )
        engagement_row = _table_segment(
            "p108-engagement-row",
            "table_row",
            f"Employee engagement | FY2024: 87% | SASB {code}",
            page=108,
            table_id="continued-index",
            row_index=6,
        )
        next_page_row = _table_segment(
            "p109-ewaste-row",
            "table_row",
            "E-waste recycled | FY2024: 91,000 metric tons | SASB TC-HW-410a.4",
            page=109,
            table_id="continued-index",
            row_index=6,
        )
        code_cell = _table_segment(
            "p108-engagement-code",
            "table_cell",
            f"Reference indices: SASB {code}",
            page=108,
            table_id="continued-index",
            row_index=6,
            col_index=2,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        report = _report([engagement_row, next_page_row, code_cell])

        results = KeywordRetriever(ProcessingConfig()).search_exact_code(
            report,
            metric,
        )

        self.assertEqual(
            {item.segment_id for item in results},
            {engagement_row.segment_id},
        )
        self.assertNotIn(
            next_page_row.segment_id,
            {item.segment_id for item in results},
        )

    def test_verified_unique_exact_code_row_skips_qwen_rerank(self):
        code = "TC-SI-330a.2"
        metric_name = "Employee engagement as a percentage"
        metric = _metric("employee-engagement", code, metric_name, "Percentage (%)")
        row = _table_segment(
            "engagement-row",
            "table_row",
            (
                "Employee engagement | FY2024: Inclusive workforce - Accountability | "
                f"Employee engagement as a percentage: 87% | SASB {code}"
            ),
            page=108,
            row_index=6,
        )
        value_cell = _table_segment(
            "engagement-value",
            "table_cell",
            "FY2024: Employee engagement as a percentage: 87%",
            page=108,
            row_index=6,
            col_index=1,
            col_header="FY2024",
            value_text="Employee engagement as a percentage: 87%",
        )
        code_cell = _table_segment(
            "engagement-code",
            "table_cell",
            f"Reference indices: SASB {code}",
            page=108,
            row_index=6,
            col_index=2,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        linked_noise = TextSegment(
            segment_id="linked-diversity-noise",
            content="Global female representation and workforce diversity: 35%",
            page_number=86,
            position_y=1,
            segment_type="text",
        )
        report = _report([row, value_cell, code_cell, linked_noise])
        config = ProcessingConfig(top_k=10)
        object.__setattr__(config, "use_semantic_retrieval", False)
        object.__setattr__(config, "use_reranker", True)
        retriever = DualChannelRetriever(config)
        retriever.keyword_retriever.search_exact_alias = Mock(
            side_effect=AssertionError("exact-alias search must be skipped")
        )
        retriever.keyword_retriever.search_bm25 = Mock(
            side_effect=AssertionError("BM25 search must be skipped")
        )
        retriever._search_linked_pages = Mock(
            side_effect=AssertionError("linked-page search must be skipped")
        )
        rerank = Mock(
            side_effect=AssertionError("verified exact-Code data must skip Qwen")
        )
        retriever.semantic_retriever.rerank_candidates = rerank

        result = retriever.retrieve_for_metric(report, metric)

        self.assertEqual(result.combined_results[0].segment_id, row.segment_id)
        self.assertIn(
            "protected_exact_code_data",
            result.combined_results[0].retrieval_type,
        )
        self.assertIn(
            "pre_rerank_exact_code_data",
            result.combined_results[0].retrieval_type,
        )
        self.assertEqual(result.rerank_pool_k, 0)
        self.assertEqual(result.target_k, len(result.combined_results))
        self.assertEqual(result.qualified_total, len(result.combined_results))
        rerank.assert_not_called()

        llm_create = Mock(
            side_effect=AssertionError("protected exact-code data must bypass the LLM")
        )
        engine = object.__new__(DisclosureInferenceEngine)
        engine.config = config
        engine.llm_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=llm_create))
        )

        analysis = engine._analyze_single_metric(result, report, metric)

        self.assertEqual(analysis.disclosure_status.value, "fully_disclosed")
        self.assertEqual(analysis.value, 87)
        self.assertEqual(analysis.selected_year, 2024)
        self.assertEqual(analysis.page, 108)
        self.assertIn(row.segment_id, analysis.evidence_segments)
        self.assertIn(value_cell.segment_id, analysis.evidence_segments)
        llm_create.assert_not_called()

    def test_shared_code_exact_row_still_calls_qwen_rerank(self):
        code = "TC-SI-330a.3"
        metric = _metric(
            "TC-SI-330a.3.03",
            code,
            "Percentage of (1) gender representation for (c) technical employees",
            "Percentage (%)",
        )
        row = _table_segment(
            "technical-row",
            "table_row",
            f"Global female representation - Technical roles | FY2024: 25% | SASB {code}",
            page=86,
            row_index=3,
            row_header="Global female representation - Technical roles",
        )
        value_cell = _table_segment(
            "technical-value",
            "table_cell",
            "FY2024: 25%",
            page=86,
            row_index=3,
            col_index=1,
            row_header="Global female representation - Technical roles",
            col_header="FY2024",
            value_text="25%",
        )
        code_cell = _table_segment(
            "technical-code",
            "table_cell",
            f"SASB {code}",
            page=86,
            row_index=3,
            col_index=2,
            row_header="Global female representation - Technical roles",
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        report = _report([row, value_cell, code_cell])
        config = ProcessingConfig(top_k=10)
        object.__setattr__(config, "use_metric_retrieval_corpus", False)
        object.__setattr__(config, "use_semantic_retrieval", False)
        object.__setattr__(config, "use_reranker", True)
        retriever = DualChannelRetriever(config)
        retriever.keyword_retriever.search_exact_alias = lambda *args, **kwargs: []
        retriever.keyword_retriever.search_bm25 = lambda *args, **kwargs: []
        rerank = Mock(side_effect=lambda candidates, *_args, **_kwargs: list(candidates))
        retriever.semantic_retriever.rerank_candidates = rerank

        result = retriever.retrieve_for_metric(report, metric)

        rerank.assert_called_once()
        self.assertGreater(result.rerank_pool_k, 0)
        self.assertFalse(
            any(
                "pre_rerank_exact_code_data" in item.retrieval_type
                for item in result.combined_results
            )
        )

    def test_reviewed_or_conflicted_exact_row_still_calls_qwen_rerank(self):
        code = "TC-SI-330a.2"
        metric = _metric(
            "employee-engagement",
            code,
            "Employee engagement as a percentage",
            "Percentage (%)",
        )
        for field, value in (
            ("review_status", "needs_review"),
            ("conflicts", [{"field": "value"}]),
        ):
            with self.subTest(field=field):
                row = _table_segment(
                    f"engagement-row-{field}",
                    "table_row",
                    f"Employee engagement as a percentage | FY2024: 87% | SASB {code}",
                    page=108,
                    row_index=6,
                    row_header="Employee engagement as a percentage",
                )
                value_cell = _table_segment(
                    f"engagement-value-{field}",
                    "table_cell",
                    "FY2024: 87%",
                    page=108,
                    row_index=6,
                    col_index=1,
                    row_header="Employee engagement as a percentage",
                    col_header="FY2024",
                    value_text="87%",
                )
                value_cell.structured_data[field] = value
                code_cell = _table_segment(
                    f"engagement-code-{field}",
                    "table_cell",
                    f"SASB {code}",
                    page=108,
                    row_index=6,
                    col_index=2,
                    row_header="Employee engagement as a percentage",
                    col_header="Reference indices",
                    value_text=f"SASB {code}",
                )
                report = _report([row, value_cell, code_cell])
                config = ProcessingConfig(top_k=10)
                object.__setattr__(config, "use_metric_retrieval_corpus", False)
                object.__setattr__(config, "use_semantic_retrieval", False)
                object.__setattr__(config, "use_reranker", True)
                retriever = DualChannelRetriever(config)
                retriever.keyword_retriever.search_exact_alias = lambda *args, **kwargs: []
                retriever.keyword_retriever.search_bm25 = lambda *args, **kwargs: []
                rerank = Mock(
                    side_effect=lambda candidates, *_args, **_kwargs: list(candidates)
                )
                retriever.semantic_retriever.rerank_candidates = rerank

                result = retriever.retrieve_for_metric(report, metric)

                rerank.assert_called_once()
                self.assertFalse(
                    any(
                        "pre_rerank_exact_code_data" in item.retrieval_type
                        for item in result.combined_results
                    )
                )

    def test_linked_pages_run_normal_semantic_retrieval_before_link_attention(self):
        metric = self._metric_with_topic()
        profile = build_metric_retrieval_profile(metric)
        source = _table_segment(
            "source-index",
            "table_row",
            f"Reporting frameworks index | SASB {metric.metric_code}",
            page=1,
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": "Employee details",
                    "source_page": 1,
                    "target_page": 2,
                }
            ],
        )
        target = TextSegment(
            segment_id="semantic-target",
            content="Survey result and workforce accountability details: 87.",
            page_number=2,
            position_y=1,
            segment_type="text",
        )
        report = _report([source, target])
        report.embeddings = [
            SegmentEmbedding(segment_id=target.segment_id, embedding=[0.1, 0.2])
        ]
        config = ProcessingConfig(top_k=10)
        object.__setattr__(config, "use_semantic_retrieval", True)
        object.__setattr__(config, "use_reranker", False)
        retriever = DualChannelRetriever(config)
        calls = []

        def fake_semantic(target_report, target_metric, semantic_expansion=None, apply_reranker=True):
            calls.append((target_report, target_metric, apply_reranker))
            return [
                RetrievalResult(
                    segment_id=target.segment_id,
                    content=target.content,
                    page_number=target.page_number,
                    score=0.75,
                    retrieval_type="semantic",
                    metric_id=metric.metric_id,
                )
            ]

        retriever.semantic_retriever.search_by_semantic = fake_semantic
        trigger = RetrievalResult(
            segment_id=source.segment_id,
            content=source.content,
            page_number=source.page_number,
            score=1.0,
            retrieval_type="exact_code",
            metric_id=metric.metric_id,
        )
        linked = retriever._search_linked_pages(
            report,
            metric,
            profile,
            [trigger],
        )

        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][2])
        self.assertEqual(
            [segment.segment_id for segment in calls[0][0].document_content.segments],
            [target.segment_id],
        )
        self.assertEqual(linked[0].segment_id, target.segment_id)
        self.assertIn("linked_page", linked[0].retrieval_type)
        self.assertIn("semantic", linked[0].retrieval_type)

    def test_linked_second_pass_is_the_only_unified_rerank_pool(self):
        metric = self._metric_with_topic()
        source = _table_segment(
            "source-index",
            "table_row",
            f"Reporting frameworks index | SASB {metric.metric_code}",
            page=1,
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": "Employee engagement details",
                    "source_page": 1,
                    "target_page": 20,
                }
            ],
        )
        preceding = TextSegment(
            segment_id="preceding-page",
            content="Employee engagement as a percentage: 66%",
            page_number=19,
            position_y=1,
        )
        target = TextSegment(
            segment_id="target-page",
            content="Employee engagement as a percentage: 87%",
            page_number=20,
            position_y=1,
        )
        continuation = TextSegment(
            segment_id="continuation-page",
            content="Employee engagement survey methodology and accountability.",
            page_number=27,
            position_y=1,
        )
        after_window = TextSegment(
            segment_id="after-window",
            content="Employee engagement as a percentage: 91%",
            page_number=28,
            position_y=1,
        )
        global_match = TextSegment(
            segment_id="global-match",
            content="Employee engagement as a percentage: 99%",
            page_number=50,
            position_y=1,
        )
        report = _report(
            [source, preceding, target, continuation, after_window, global_match]
        )
        report.embeddings = [
            SegmentEmbedding(segment_id=segment.segment_id, embedding=[0.1, 0.2])
            for segment in (preceding, target, continuation, after_window, global_match)
        ]
        config = ProcessingConfig(top_k=46)
        object.__setattr__(config, "use_keyword_retrieval", True)
        object.__setattr__(config, "use_semantic_retrieval", True)
        object.__setattr__(config, "use_reranker", True)
        retriever = DualChannelRetriever(config)
        semantic_scopes = []
        rerank_pools = []

        def fake_semantic(target_report, target_metric, semantic_expansion=None, apply_reranker=True):
            scoped_segments = list(target_report.document_content.segments)
            semantic_scopes.append(scoped_segments)
            return [
                RetrievalResult(
                    segment_id=segment.segment_id,
                    content=segment.content,
                    page_number=segment.page_number,
                    score=0.75,
                    retrieval_type="semantic",
                    metric_id=metric.metric_id,
                )
                for segment in scoped_segments
                if segment.segment_id != source.segment_id
            ]

        def fake_rerank(candidates, target_metric, semantic_expansion=None):
            values = list(candidates)
            rerank_pools.append(values)
            return values

        retriever.semantic_retriever.search_by_semantic = fake_semantic
        retriever.semantic_retriever.rerank_candidates = fake_rerank
        with patch.dict(
            os.environ,
            {"REPORT_LINK_FORWARD_PAGE_COUNT": "7"},
            clear=False,
        ):
            result = retriever.retrieve_for_metric(report, metric)

        self.assertEqual(len(semantic_scopes), 1)
        self.assertEqual(
            {segment.page_number for segment in semantic_scopes[0]},
            {20, 27},
        )
        self.assertEqual(len(rerank_pools), 1)
        self.assertTrue(rerank_pools[0])
        self.assertTrue(
            all("linked_page" in item.retrieval_type for item in rerank_pools[0])
        )
        self.assertTrue(
            {item.page_number for item in rerank_pools[0]}.issubset({20, 27})
        )
        excluded_ids = {"preceding-page", "after-window", "global-match", "source-index"}
        self.assertTrue(excluded_ids.isdisjoint({item.segment_id for item in rerank_pools[0]}))
        self.assertEqual(result.semantic_results, [])
        self.assertTrue(
            all("linked_page" in item.retrieval_type for item in result.combined_results)
        )

    def test_unavailable_links_use_whole_report_and_retain_code_row(self):
        metric = self._metric_with_topic()
        link_cases = {
            "external": {
                "link_type": "external_ignored",
                "anchor_text": "Employee engagement details",
                "source_page": 1,
                "uri": "https://example.com/report-data",
            },
            "outside_report": {
                "link_type": "internal",
                "anchor_text": "Employee engagement details",
                "source_page": 1,
                "target_page": 999,
            },
        }

        for case_name, link in link_cases.items():
            with self.subTest(case=case_name):
                source = _table_segment(
                    f"source-index-{case_name}",
                    "table_row",
                    f"Reporting frameworks index | SASB {metric.metric_code}",
                    page=1,
                    links=[link],
                )
                global_data = TextSegment(
                    segment_id=f"global-data-{case_name}",
                    content="Employee engagement as a percentage: 87%",
                    page_number=50,
                    position_y=1,
                )
                report = _report([source, global_data])
                report.embeddings = [
                    SegmentEmbedding(
                        segment_id=global_data.segment_id,
                        embedding=[0.1, 0.2],
                    )
                ]
                config = ProcessingConfig(top_k=46)
                object.__setattr__(config, "use_keyword_retrieval", True)
                object.__setattr__(config, "use_semantic_retrieval", True)
                object.__setattr__(config, "use_reranker", True)
                retriever = DualChannelRetriever(config)
                semantic_scopes = []

                def fake_semantic(target_report, target_metric, semantic_expansion=None, apply_reranker=True):
                    semantic_scopes.append(
                        [segment.segment_id for segment in target_report.document_content.segments]
                    )
                    return [
                        RetrievalResult(
                            segment_id=global_data.segment_id,
                            content=global_data.content,
                            page_number=global_data.page_number,
                            score=0.80,
                            retrieval_type="semantic",
                            metric_id=metric.metric_id,
                        )
                    ]

                def fake_rerank(candidates, target_metric, semantic_expansion=None):
                    # Simulate Qwen dropping the navigation row. The fallback
                    # retention step must restore it after whole-report ranking.
                    return [
                        item for item in candidates
                        if item.segment_id != source.segment_id
                    ]

                retriever.semantic_retriever.search_by_semantic = fake_semantic
                retriever.semantic_retriever.rerank_candidates = fake_rerank
                result = retriever.retrieve_for_metric(report, metric)

                self.assertEqual(
                    semantic_scopes,
                    [[source.segment_id, global_data.segment_id]],
                )
                by_id = {item.segment_id: item for item in result.combined_results}
                self.assertIn(global_data.segment_id, by_id)
                self.assertIn(source.segment_id, by_id)
                self.assertIn(
                    "link_fallback_code_context",
                    by_id[source.segment_id].retrieval_type,
                )
                self.assertNotIn("linked_page", by_id[source.segment_id].retrieval_type)


class DirectDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.engine = object.__new__(DisclosureInferenceEngine)

    def _analyze_all_other_employee_category(
        self,
        evidence_segments: list[TextSegment],
    ):
        code = "TC-SI-330a.3"
        metric_name = (
            "Percentage of (2) diversity group representation for "
            "(d) all other employees"
        )
        metric = _metric(f"{code}.08", code, metric_name, "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id=metric.metric_id,
            metric_name=metric_name,
            metric_code=code,
            combined_results=[
                RetrievalResult(
                    segment_id=segment.segment_id,
                    content=segment.content,
                    page_number=segment.page_number,
                    score=0.99 - (index * 0.01),
                    retrieval_type="rrf:linked_page_category",
                    metric_id=metric.metric_id,
                )
                for index, segment in enumerate(evidence_segments)
            ],
            total_matches=len(evidence_segments),
        )
        response_payload = {
            "metric_hit": True,
            "disclosure_status": "fully_disclosed",
            "has_disclosure": True,
            "disclosure_quality": "high",
            "value_status": "ambiguous",
            "value": None,
            "raw_value": None,
            "raw_unit": "%",
            "reasoning": "The report provides the requested employee representation distribution.",
            "page": 90,
            "evidence_segment_id": evidence_segments[0].segment_id,
            "evidence_quote": "Non-technical roles: Asian 9.7%; Hispanic or Latino 11.0%.",
            "specific_data_found": "FY2024 race/ethnicity distribution",
            "year_values": [],
            "derived_calculation": None,
            "improvement_suggestions": [],
        }
        llm_create = Mock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(response_payload))
                    )
                ]
            )
        )
        self.engine.config = ProcessingConfig()
        self.engine.llm_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=llm_create))
        )

        analysis = self.engine._analyze_single_metric(
            retrieval,
            _report(evidence_segments),
            metric,
        )
        return analysis, llm_create

    def test_all_other_non_technical_proxy_downgrades_llm_full_to_partial(self):
        proxy = TextSegment(
            segment_id="nontechnical-distribution",
            content=(
                "U.S. race/ethnicity representation | Non-technical roles | "
                "FY2024: Asian 9.7%; Hispanic or Latino 11.0%"
            ),
            page_number=90,
            position_y=2,
            segment_type="table",
        )
        # A SASB index row can repeat the exact framework label but does not
        # prove that the linked report category has the same employee boundary.
        source_index = TextSegment(
            segment_id="sasb-index-all-other",
            content=(
                "TC-SI-330a.3 | Percentage of diversity group representation "
                "for all other employees | Page 90"
            ),
            page_number=82,
            position_y=1,
            segment_type="table_row",
        )

        analysis, llm_create = self._analyze_all_other_employee_category(
            [proxy, source_index]
        )

        self.assertEqual(analysis.disclosure_status.value, "partially_disclosed")
        self.assertEqual(analysis.value, "n/a")
        self.assertEqual(analysis.page, 90)
        self.assertIn("category boundary is not exact", analysis.reasoning)
        self.assertTrue(analysis.improvement_suggestions)
        llm_create.assert_called_once()

    def test_explicit_non_technical_all_other_equivalence_preserves_llm_full(self):
        explicit_equivalence = TextSegment(
            segment_id="defined-all-other-distribution",
            content=(
                "For this report, Non-technical roles is the label for all other "
                "employees and means employees not classified as executive "
                "management, non-executive management, or technical employees. "
                "FY2024: Asian 9.7%; Hispanic or Latino 11.0%."
            ),
            page_number=90,
            position_y=1,
            segment_type="table",
        )

        analysis, llm_create = self._analyze_all_other_employee_category(
            [explicit_equivalence]
        )

        self.assertEqual(analysis.disclosure_status.value, "fully_disclosed")
        self.assertEqual(analysis.value, "n/a")
        llm_create.assert_called_once()

    def test_reviewed_or_conflicted_row_cannot_take_direct_disclosure_shortcut(self):
        code = "TC-SI-330a.2"
        metric = _metric(
            "employee-engagement",
            code,
            "Employee engagement as a percentage",
            "Percentage (%)",
        )
        for field, value in (
            ("review_status", "needs_review"),
            ("conflicts", [{"field": "value"}]),
        ):
            with self.subTest(field=field):
                row = _table_segment(
                    f"direct-row-{field}",
                    "table_row",
                    f"Employee engagement as a percentage | FY2024: 87% | SASB {code}",
                    page=108,
                    row_index=6,
                    row_header="Employee engagement as a percentage",
                )
                value_cell = _table_segment(
                    f"direct-value-{field}",
                    "table_cell",
                    "FY2024: 87%",
                    page=108,
                    row_index=6,
                    col_index=1,
                    row_header="Employee engagement as a percentage",
                    col_header="FY2024",
                    value_text="87%",
                )
                value_cell.structured_data[field] = value
                code_cell = _table_segment(
                    f"direct-code-{field}",
                    "table_cell",
                    f"SASB {code}",
                    page=108,
                    row_index=6,
                    col_index=2,
                    row_header="Employee engagement as a percentage",
                    col_header="Reference indices",
                    value_text=f"SASB {code}",
                )
                report = _report([row, value_cell, code_cell])
                retrieval = MetricRetrievalResult(
                    metric_id=metric.metric_id,
                    metric_name=metric.metric_name,
                    metric_code=code,
                    combined_results=[],
                )

                analysis = self.engine._direct_code_data_disclosure_analysis(
                    retrieval,
                    report,
                    metric,
                    [{"segment_id": row.segment_id, "page_number": 108, "score": 1.0}],
                    [row.segment_id],
                )

                self.assertIsNone(analysis)

    def test_structured_year_label_and_scaled_unit_override_visible_header(self):
        row = _table_segment(
            "energy-row",
            "table_row",
            "Total energy consumed | visible header says FY2023 | value 2",
            row_index=7,
            row_header="Total energy consumed",
        )
        value = _table_segment(
            "energy-value",
            "table_cell",
            "2",
            row_index=7,
            col_index=2,
            row_header="Total energy consumed",
            col_header="FY2023",
            value_text="2",
        )
        value.structured_data.update(
            {
                "year": 2024,
                "source_year_label": "FY24",
                "unit": "million kWh",
            }
        )
        report = _report([row, value])

        candidates = self.engine._real_data_candidates_for_row(
            report,
            row,
            code_candidates=[],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["value"], 2)
        self.assertEqual(candidate["year"], 2024)
        self.assertEqual(candidate["source_year_label"], "FY24")
        self.assertEqual(candidate["unit"], "million kWh")

        metric = _metric(
            "energy-test",
            "TEST-ENERGY-1",
            "Total energy consumed",
            "GJ",
        )
        year_values = self.engine._metric_year_values_from_candidates(
            candidates,
            metric,
        )
        self.assertEqual(len(year_values), 1)
        self.assertEqual(year_values[0]["year"], 2024)
        self.assertEqual(year_values[0]["source_year_label"], "FY24")
        self.assertEqual(year_values[0]["raw_value"], 2)
        self.assertEqual(year_values[0]["raw_unit"], "million kWh")
        self.assertEqual(year_values[0]["value"], 7200)
        self.assertEqual(year_values[0]["unit"], "GJ")

    def test_semantic_scope_conflicts_are_not_reinferred_downstream(self):
        metric = _metric(
            "energy-test",
            "TEST-ENERGY-1",
            "Total energy consumed",
            "GJ",
        )
        for reason in (
            "conflicting_year_scope",
            "ambiguous_year_scope",
            "ambiguous_unit_scope",
        ):
            with self.subTest(reason=reason):
                row = _table_segment(
                    f"energy-row-{reason}",
                    "table_row",
                    "Total energy consumed | FY24 | 10 GJ",
                    row_index=7,
                    row_header="Total energy consumed",
                )
                value = _table_segment(
                    f"energy-value-{reason}",
                    "table_cell",
                    "10 GJ",
                    row_index=7,
                    col_index=2,
                    row_header="Total energy consumed",
                    col_header="FY24",
                    value_text="10 GJ",
                )
                for segment in (row, value):
                    segment.structured_data["quality_reasons"] = [reason]
                if reason == "ambiguous_unit_scope":
                    value.structured_data.update(
                        {
                            "year": 2024,
                            "source_year_label": "FY24",
                        }
                    )
                else:
                    value.structured_data["unit"] = "GJ"
                report = _report([row, value])

                candidates = self.engine._real_data_candidates_for_row(
                    report,
                    row,
                    code_candidates=[],
                )

                self.assertEqual(len(candidates), 1)
                candidate = candidates[0]
                self.assertIn(
                    reason,
                    candidate["blocking_semantic_quality_reasons"],
                )
                if reason in {
                    "conflicting_year_scope",
                    "ambiguous_year_scope",
                }:
                    self.assertIsNone(candidate["year"])
                else:
                    self.assertIsNone(candidate["unit"])
                self.assertIsNone(
                    self.engine._select_metric_numeric_candidate(
                        report,
                        row,
                        metric,
                        code_candidates=[],
                    )
                )
                self.assertEqual(
                    self.engine._metric_year_values_from_candidates(
                        candidates,
                        metric,
                    ),
                    [],
                )
                _context, latest_numeric, _unit, _description = (
                    self.engine._build_table_row_aggregation_context(
                        report,
                        row,
                    )
                )
                self.assertIsNone(latest_numeric)

    def _direct(self, code: str, value_text: str | None, metric_name: str = "Unrelated label", unit: str = ""):
        row = _table_segment(
            "row",
            "table_row",
            f"Metric row | FY2024: {value_text or ''} | Reference indices: SASB {code}",
        )
        code_cell = _table_segment(
            "code-cell",
            "table_cell",
            f"Reference indices: SASB {code}",
            col_index=2,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        segments = [row, code_cell]
        if value_text is not None:
            segments.append(
                _table_segment(
                    "data-cell",
                    "table_cell",
                    f"FY2024: {value_text}",
                    col_index=1,
                    col_header="FY2024",
                    value_text=value_text,
                )
            )
        report = _report(segments)
        metric = _metric(code, code, metric_name, unit)
        retrieval = MetricRetrievalResult(
            metric_id=code,
            metric_name=metric_name,
            metric_code=code,
            combined_results=[],
        )
        metadata = [
            {
                "segment_id": "row",
                "page_number": 1,
                "score": 1.0,
                "retrieval_type": "exact_code",
            }
        ]
        return self.engine._direct_code_data_disclosure_analysis(
            retrieval,
            report,
            metric,
            metadata,
            ["row"],
        ), report, row, metric

    def test_unique_real_data_cell_triggers_without_name_or_unit_match(self):
        analysis, _, _, _ = self._direct("TC-X-100a.1", "Reported result: 42")
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.value, 42)

    def test_code_suffix_is_not_a_value(self):
        analysis, _, _, _ = self._direct("TC-HW-410a.3", None)
        self.assertIsNone(analysis)

    def test_exact_code_and_metric_label_count_as_disclosure_without_parsed_value(self):
        code = "TC-SI-330a.2"
        metric_name = "Employee engagement as a percentage"
        row = _table_segment(
            "row",
            "table_row",
            f"{metric_name} | SASB {code}",
        )
        report = _report([row])
        metric = _metric(code, code, metric_name, "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id=code,
            metric_name=metric_name,
            metric_code=code,
            combined_results=[],
        )

        analysis = self.engine._direct_code_label_disclosure_analysis(
            retrieval,
            report,
            metric,
            [{"segment_id": "row", "page_number": 1, "score": 1.0}],
            ["row"],
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.disclosure_status.value, "fully_disclosed")
        self.assertIsNone(analysis.value)

    def test_bare_exact_code_still_does_not_count_as_labelled_disclosure(self):
        code = "TC-SI-330a.2"
        metric_name = "Employee engagement as a percentage"
        row = _table_segment("row", "table_row", f"SASB {code} | See page 82")
        report = _report([row])
        metric = _metric(code, code, metric_name, "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id=code,
            metric_name=metric_name,
            metric_code=code,
            combined_results=[],
        )

        analysis = self.engine._direct_code_label_disclosure_analysis(
            retrieval,
            report,
            metric,
            [{"segment_id": "row", "page_number": 1, "score": 1.0}],
            ["row"],
        )

        self.assertIsNone(analysis)

    def test_plain_text_number_does_not_trigger_direct_disclosure(self):
        code = "TC-HW-410a.3"
        segment = TextSegment(
            segment_id="plain-text",
            content=f"SASB {code}. See page 82 for details.",
            page_number=1,
            position_y=1,
            segment_type="text",
        )
        report = _report([segment])
        retrieval = MetricRetrievalResult(
            metric_id=code,
            metric_name="ENERGY STAR products",
            metric_code=code,
            combined_results=[],
        )
        analysis = self.engine._direct_code_data_disclosure_analysis(
            retrieval,
            report,
            _metric(code, code, "ENERGY STAR products", "%"),
            [{"segment_id": "plain-text", "page_number": 1, "score": 1.0}],
            ["plain-text"],
        )
        self.assertIsNone(analysis)

    def test_internal_link_page_number_is_not_a_data_value(self):
        code = "TC-SI-130a.1"
        row = _table_segment(
            "row",
            "table_row",
            f"Energy use | See page 82 | Reference indices: SASB {code}",
        )
        link_cell = _table_segment(
            "link-cell",
            "table_cell",
            "FY2024: See page 82",
            col_index=1,
            col_header="FY2024",
            value_text="See page 82",
            links=[
                {
                    "link_type": "internal",
                    "anchor_text": "See page 82",
                    "source_page": 1,
                    "target_page": 82,
                }
            ],
        )
        code_cell = _table_segment(
            "code-cell",
            "table_cell",
            f"Reference indices: SASB {code}",
            col_index=2,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        report = _report([row, link_cell, code_cell])
        retrieval = MetricRetrievalResult(
            metric_id=code,
            metric_name="Total energy consumed",
            metric_code=code,
            combined_results=[],
        )
        analysis = self.engine._direct_code_data_disclosure_analysis(
            retrieval,
            report,
            _metric(code, code, "Total energy consumed", "GJ"),
            [{"segment_id": "row", "page_number": 1, "score": 1.0}],
            ["row"],
        )
        self.assertIsNone(analysis)

    def test_long_energy_star_cell_extracts_68_7(self):
        value = (
            "ENERGY STAR Product Finder and percentage of eligible products, by revenue, "
            "meeting ENERGY STAR registration or equivalent: 68.7%"
        )
        analysis, _, _, _ = self._direct("TC-HW-410a.3", value, unit="Percentage (%)")
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.value, 68.7)

    def test_employee_engagement_index_row_extracts_real_fy2024_cell(self):
        code = "TC-SI-330a.2"
        row = _table_segment(
            "engagement-row",
            "table_row",
            (
                "Employee engagement | FY2024: Inclusive workforce - Accountability | "
                f"Employee engagement as a percentage: 87% | SASB {code}"
            ),
            page=108,
            row_index=6,
        )
        value_cell = _table_segment(
            "engagement-value",
            "table_cell",
            "FY2024: Employee engagement as a percentage: 87%",
            page=108,
            row_index=6,
            col_index=1,
            col_header="FY2024",
            value_text=(
                "Inclusive workforce - Accountability\n"
                "Employee engagement as a percentage: 87%"
            ),
        )
        code_cell = _table_segment(
            "engagement-code",
            "table_cell",
            f"Reference indices: SASB {code}",
            page=108,
            row_index=6,
            col_index=2,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        report = _report([row, value_cell, code_cell])
        metric_name = "Employee engagement as a percentage"
        metric = _metric("employee-engagement", code, metric_name, "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id="employee-engagement",
            metric_name=metric_name,
            metric_code=code,
            combined_results=[],
        )

        analysis = self.engine._direct_code_data_disclosure_analysis(
            retrieval,
            report,
            metric,
            [
                {
                    "segment_id": "engagement-row",
                    "page_number": 108,
                    "score": 1.0,
                    "retrieval_type": "exact_code+table_row_context",
                }
            ],
            ["engagement-row"],
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.value, 87)
        self.assertEqual(analysis.page, 108)
        self.assertIn("Employee engagement as a percentage: 87%", analysis.context)

    def test_employee_engagement_exact_code_row_bypasses_false_llm_rejection(self):
        code = "TC-SI-330a.2"
        metric_name = "Employee engagement as a percentage"
        row = _table_segment(
            "engagement-row",
            "table_row",
            (
                "[Table Title] Reporting frameworks index\n"
                "[Column Headers] Key performance indicator | FY2024 | Reference indices\n"
                "Key performance indicator: Employee engagement | "
                "FY2024: Inclusive workforce - Accountability\\n"
                f"Employee engagement as a percentage: 87% | SASB {code}"
            ),
            page=108,
            row_index=6,
            row_header="Employee engagement",
        )
        value_cell = _table_segment(
            "engagement-value",
            "table_cell",
            "FY2024: Employee engagement as a percentage: 87%",
            page=108,
            row_index=6,
            col_index=1,
            row_header="Employee engagement",
            col_header="FY2024",
            value_text=(
                "Inclusive workforce - Accountability\\n"
                "Employee engagement as a percentage: 87%"
            ),
        )
        code_cell = _table_segment(
            "engagement-code",
            "table_cell",
            f"Reference indices: SASB {code}",
            page=108,
            row_index=6,
            col_index=2,
            row_header="Employee engagement",
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        report = _report([row, value_cell, code_cell])
        metric = _metric("employee-engagement", code, metric_name, "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id=metric.metric_id,
            metric_name=metric_name,
            metric_code=code,
            combined_results=[
                RetrievalResult(
                    segment_id=row.segment_id,
                    content=row.content,
                    page_number=108,
                    score=1.0,
                    retrieval_type=(
                        "exact_code+table_row_context+"
                        "real_data_evidence+protected_exact_code_data"
                    ),
                    metric_id=metric.metric_id,
                )
            ],
            total_matches=1,
            target_k=1,
        )
        llm_create = Mock(
            side_effect=AssertionError("clear same-code data must bypass the LLM")
        )
        self.engine.config = ProcessingConfig()
        self.engine.llm_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=llm_create))
        )

        analysis = self.engine._analyze_single_metric(retrieval, report, metric)

        self.assertEqual(analysis.disclosure_status.value, "fully_disclosed")
        self.assertEqual(analysis.value, 87)
        self.assertEqual(analysis.selected_year, 2024)
        self.assertEqual(analysis.page, 108)
        self.assertIn("Employee engagement as a percentage: 87%", analysis.context)
        self.assertIn(value_cell.segment_id, analysis.evidence_segments)
        llm_create.assert_not_called()

    def test_direct_disclosure_does_not_mix_same_row_number_from_next_page(self):
        code = "TC-SI-330a.2"
        metric_name = "Employee engagement as a percentage"
        engagement_row = _table_segment(
            "p108-engagement-row",
            "table_row",
            f"Employee engagement | FY2024: 87% | SASB {code}",
            page=108,
            table_id="continued-index",
            row_index=6,
        )
        engagement_value = _table_segment(
            "p108-engagement-value",
            "table_cell",
            "Employee engagement as a percentage: 87%",
            page=108,
            table_id="continued-index",
            row_index=6,
            col_index=1,
            col_header="FY2024",
            value_text="Employee engagement as a percentage: 87%",
        )
        engagement_code = _table_segment(
            "p108-engagement-code",
            "table_cell",
            f"Reference indices: SASB {code}",
            page=108,
            table_id="continued-index",
            row_index=6,
            col_index=2,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        next_page_row = _table_segment(
            "p109-ewaste-row",
            "table_row",
            "E-waste recycled | FY2024: 91,000 metric tons | SASB TC-HW-410a.4",
            page=109,
            table_id="continued-index",
            row_index=6,
        )
        next_page_value = _table_segment(
            "p109-ewaste-value",
            "table_cell",
            "E-waste recycled: 91,000 metric tons",
            page=109,
            table_id="continued-index",
            row_index=6,
            col_index=1,
            col_header="FY2024",
            value_text="91,000 metric tons",
        )
        report = _report([
            engagement_row,
            engagement_value,
            engagement_code,
            next_page_row,
            next_page_value,
        ])
        metric = _metric(
            "employee-engagement",
            code,
            metric_name,
            "Percentage (%)",
        )
        retrieval = MetricRetrievalResult(
            metric_id=metric.metric_id,
            metric_name=metric_name,
            metric_code=code,
            combined_results=[],
        )

        analysis = self.engine._direct_code_data_disclosure_analysis(
            retrieval,
            report,
            metric,
            [{
                "segment_id": engagement_row.segment_id,
                "page_number": 108,
                "score": 1.0,
                "retrieval_type": "exact_code+table_row_context",
            }],
            [engagement_row.segment_id],
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.value, 87)
        self.assertEqual(analysis.page, 108)
        self.assertNotIn("91,000", analysis.context)

    def test_direct_code_row_retains_all_years_and_projects_latest(self):
        code = "TC-SI-330a.2"
        row = _table_segment(
            "engagement-row",
            "table_row",
            (
                "Employee engagement | FY2022: 81% | FY2023: 84% | FY2024: 87% | "
                f"SASB {code}"
            ),
            page=108,
            row_index=6,
        )
        cells = [
            _table_segment(
                f"engagement-{year}",
                "table_cell",
                f"FY{year}: {value}%",
                page=108,
                row_index=6,
                col_index=index,
                col_header=f"FY{year}",
                value_text=f"{value}%",
            )
            for index, (year, value) in enumerate(
                [(2022, 81), (2023, 84), (2024, 87)],
                start=1,
            )
        ]
        code_cell = _table_segment(
            "engagement-code",
            "table_cell",
            f"Reference indices: SASB {code}",
            page=108,
            row_index=6,
            col_index=4,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        report = _report([row, *cells, code_cell])
        metric = _metric("employee-engagement", code, "Employee engagement as a percentage", "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id="employee-engagement",
            metric_name=metric.metric_name,
            metric_code=code,
            combined_results=[],
        )

        analysis = self.engine._direct_code_data_disclosure_analysis(
            retrieval,
            report,
            metric,
            [{"segment_id": "engagement-row", "page_number": 108, "score": 1.0}],
            ["engagement-row"],
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.value, 87)
        self.assertEqual(analysis.selected_year, 2024)
        self.assertEqual(
            [(item["year"], item["value"]) for item in analysis.year_values],
            [(2022, 81), (2023, 84), (2024, 87)],
        )

    def test_shared_code_profile_rejects_sibling_component_value(self):
        code = "TC-SI-330a.3"
        metric_name = "Percentage of (1) gender representation for (c) technical employees"
        metric = _metric(f"{code}.03", code, metric_name, "Percentage (%)")
        profile = find_metric_profile(metric)
        self.assertIsNotNone(profile)
        # The runtime safety rule must not depend on optional JSON flags.
        profile_without_guard_flags = replace(
            profile,
            direct_disclosure_rules={},
            value_selection_rules={},
        )
        retrieval = MetricRetrievalResult(
            metric_id=f"{code}.03",
            metric_name=metric_name,
            metric_code=code,
            combined_results=[],
        )

        def analyze(label: str, value: str):
            row = _table_segment(
                "shared-row",
                "table_row",
                f"{label} | FY2024: {value} | SASB {code}",
            )
            value_cell = _table_segment(
                "shared-value",
                "table_cell",
                f"{label} | FY2024: {value}",
                col_index=1,
                row_header=label,
                col_header="FY2024",
                value_text=value,
            )
            code_cell = _table_segment(
                "shared-code",
                "table_cell",
                f"SASB {code}",
                col_index=2,
                row_header=label,
                col_header="Reference indices",
                value_text=f"SASB {code}",
            )
            return self.engine._direct_code_data_disclosure_analysis(
                retrieval,
                _report([row, value_cell, code_cell]),
                metric,
                [{"segment_id": "shared-row", "page_number": 86, "score": 1.0}],
                ["shared-row"],
                metric_profile=profile_without_guard_flags,
            )

        self.assertIsNone(
            analyze("Global female representation - People leader roles", "29.1%")
        )
        analysis = analyze("Global female representation - Technical roles", "25.0%")
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.value, 25)

    def test_deterministic_candidate_does_not_override_llm_selected_value(self):
        code = "TC-SI-330a.3"
        metric_name = "Percentage of (1) gender representation for (c) technical employees"
        label = "Global female representation - Technical roles"
        row = _table_segment(
            "technical-row",
            "table_row",
            f"{label} | FY2024: 25.0% | SASB {code}",
            page=86,
        )
        value_cell = _table_segment(
            "technical-value",
            "table_cell",
            f"{label} | FY2024: 25.0%",
            page=86,
            col_index=1,
            row_header=label,
            col_header="FY2024",
            value_text="25.0%",
        )
        code_cell = _table_segment(
            "technical-code",
            "table_cell",
            f"SASB {code}",
            page=86,
            col_index=2,
            row_header=label,
            col_header="Reference indices",
            value_text=f"SASB {code}",
        )
        report = _report([row, value_cell, code_cell])
        metric = _metric(f"{code}.03", code, metric_name, "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id=f"{code}.03",
            metric_name=metric_name,
            metric_code=code,
            combined_results=[
                RetrievalResult(
                    segment_id="technical-row",
                    content=row.content,
                    page_number=86,
                    score=0.99,
                    retrieval_type="rrf:exact_code",
                    metric_id=f"{code}.03",
                )
            ],
            total_matches=1,
        )
        response_payload = {
            "metric_hit": True,
            "disclosure_status": "fully_disclosed",
            "has_disclosure": True,
            "disclosure_quality": "high",
            "value_status": "reported",
            "value": 24.5,
            "raw_value": 24.5,
            "raw_unit": "%",
            "reasoning": "The current technical-employee component is explicitly reported.",
            "page": 86,
            "evidence_segment_id": "technical-row",
            "evidence_quote": "Technical roles: 24.5%",
            "specific_data_found": "24.5%",
            "derived_calculation": None,
            "improvement_suggestions": [],
        }
        fake_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(response_payload))
                )
            ]
        )
        self.engine.config = ProcessingConfig()
        self.engine.llm_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: fake_response)
            )
        )

        with patch.object(
            self.engine,
            "_direct_code_data_disclosure_analysis",
            return_value=None,
        ):
            analysis = self.engine._analyze_single_metric(retrieval, report, metric)

        self.assertEqual(analysis.value, 24.5)
        self.assertEqual(analysis.context, "Technical roles: 24.5%")

    def test_invalid_llm_json_raises_analysis_error_instead_of_not_disclosed(self):
        metric = _metric("metric-1", "TEST-1", "Reported metric", "Percentage (%)")
        segment = TextSegment(
            segment_id="metric-evidence",
            content="Reported metric: 42%",
            page_number=1,
            position_y=1,
            segment_type="text",
        )
        report = _report([segment])
        retrieval = MetricRetrievalResult(
            metric_id=metric.metric_id,
            metric_name=metric.metric_name,
            metric_code=metric.metric_code,
            combined_results=[
                RetrievalResult(
                    segment_id=segment.segment_id,
                    content=segment.content,
                    page_number=1,
                    score=0.99,
                    retrieval_type="rrf:semantic",
                    metric_id=metric.metric_id,
                )
            ],
            total_matches=1,
        )
        self.engine.config = ProcessingConfig()
        self.engine.llm_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="not valid json")
                            )
                        ]
                    )
                )
            )
        )

        with self.assertRaises(DisclosureAnalysisError) as raised:
            self.engine._analyze_collection_metric(metric, retrieval, report)

        self.assertEqual(raised.exception.error_type, "invalid_llm_json")
        self.assertEqual(raised.exception.metric_id, "metric-1")

    def test_llm_timeout_raises_analysis_error_instead_of_not_disclosed(self):
        metric = _metric("metric-1", "TEST-1", "Reported metric", "Percentage (%)")
        segment = TextSegment(
            segment_id="metric-evidence",
            content="Reported metric: 42%",
            page_number=1,
            position_y=1,
            segment_type="text",
        )
        report = _report([segment])
        retrieval = MetricRetrievalResult(
            metric_id=metric.metric_id,
            metric_name=metric.metric_name,
            metric_code=metric.metric_code,
            combined_results=[
                RetrievalResult(
                    segment_id=segment.segment_id,
                    content=segment.content,
                    page_number=1,
                    score=0.99,
                    retrieval_type="rrf:semantic",
                    metric_id=metric.metric_id,
                )
            ],
            total_matches=1,
        )

        def timeout(**_):
            raise TimeoutError("upstream timeout")

        self.engine.config = ProcessingConfig()
        self.engine.llm_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=timeout)
            )
        )

        with self.assertRaises(DisclosureAnalysisError) as raised:
            self.engine._analyze_collection_metric(metric, retrieval, report)

        self.assertEqual(raised.exception.error_type, "llm_request_failed")
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)

    def test_no_retrieved_evidence_remains_not_disclosed(self):
        metric = _metric("metric-1", "TEST-1", "Missing metric", "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id=metric.metric_id,
            metric_name=metric.metric_name,
            metric_code=metric.metric_code,
            combined_results=[],
            total_matches=0,
        )

        analysis = self.engine._analyze_collection_metric(
            metric,
            retrieval,
            _report([]),
        )

        self.assertEqual(analysis.disclosure_status.value, "not_disclosed")

    def test_llm_year_values_are_validated_and_short_fy_years_are_supported(self):
        metric = _metric(
            "employee-engagement",
            "TC-SI-330a.2",
            "Employee engagement as a percentage",
            "Percentage (%)",
        )
        values = self.engine._normalise_llm_year_values(
            {
                "year_values": [
                    {
                        "year": 2022,
                        "value": 81,
                        "raw_value": 81,
                        "unit": "%",
                        "page": 106,
                        "evidence_segment_id": "fy22",
                        "evidence_quote": "FY22 employee engagement: 81%",
                    },
                    {
                        "year": 2024,
                        "value": 87,
                        "raw_value": 87,
                        "unit": "%",
                        "page": 108,
                        "evidence_segment_id": "fy24",
                        "evidence_quote": "FY24 employee engagement: 87%",
                    },
                ]
            },
            metric,
            [
                {"segment_id": "fy22", "page_number": 106},
                {"segment_id": "fy24", "page_number": 108},
            ],
        )

        self.assertEqual([(item["year"], item["value"]) for item in values], [(2022, 81), (2024, 87)])
        self.assertEqual(
            self.engine._extract_years_from_text("FY22 | FY 2023 | CY'24"),
            [2022, 2023, 2024],
        )

    def test_same_year_value_merges_sources_before_conflict_detection(self):
        metadata = [
            {
                "segment_id": "report-a::value",
                "page_number": 4,
                "source_report_id": "report-a",
                "source_report_name": "Acme 2024.pdf",
                "source_report_year": 2024,
            },
            {
                "segment_id": "report-b::value",
                "page_number": 9,
                "source_report_id": "report-b",
                "source_report_name": "Acme ESG 2024.pdf",
                "source_report_year": 2024,
            },
        ]
        first = self.engine._attach_year_value_sources(
            [{
                "year": 2024,
                "value": 87,
                "unit": "%",
                "evidence_segment_id": "report-a::value",
            }],
            metadata,
        )
        second = self.engine._attach_year_value_sources(
            [{
                "year": 2024,
                "value": 87,
                "unit": "Percentage (%)",
                "evidence_segment_id": "report-b::value",
            }],
            metadata,
        )

        merged = self.engine._merge_metric_year_values(first, second)

        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["sources"]), 2)
        self.assertFalse(self.engine._year_has_conflicting_values(merged, 2024))
        conflicting = self.engine._merge_metric_year_values(
            merged,
            [{"year": 2024, "value": 84, "unit": "%"}],
        )
        self.assertTrue(
            self.engine._year_has_conflicting_values(conflicting, 2024)
        )

    def test_evidence_chunking_keeps_table_rows_together(self):
        segments = ["A" * 80, "B" * 80, "C" * 80]
        metadata = [
            {
                "segment_id": "a",
                "source_table_id": "table-1",
                "page_number": 1,
                "row_index": 2,
            },
            {
                "segment_id": "b",
                "source_table_id": "table-1",
                "page_number": 1,
                "row_index": 2,
            },
            {
                "segment_id": "c",
                "source_table_id": "table-1",
                "page_number": 2,
                "row_index": 2,
            },
        ]

        chunks = self.engine._evidence_chunks(segments, metadata, token_budget=30)

        self.assertEqual(len(chunks), 2)
        self.assertEqual([item[1]["segment_id"] for item in chunks[0]], ["a", "b"])
        self.assertEqual([item[1]["segment_id"] for item in chunks[1]], ["c"])

    def test_ocr_separator_variation_matches_full_code_only(self):
        canonical_code = "TC-HW-410a.3"
        ocr_code = "TC HW\u2011410a . 3"
        row = _table_segment(
            "row",
            "table_row",
            f"ENERGY STAR | FY2024: 68.7% | Reference indices: SASB {ocr_code}",
        )
        value_cell = _table_segment(
            "value-cell",
            "table_cell",
            "FY2024: 68.7%",
            col_index=1,
            col_header="FY2024",
            value_text="68.7%",
        )
        code_cell = _table_segment(
            "code-cell",
            "table_cell",
            f"Reference indices: SASB {ocr_code}",
            col_index=2,
            col_header="Reference indices",
            value_text=f"SASB {ocr_code}",
        )
        report = _report([row, value_cell, code_cell])
        metric = _metric(canonical_code, canonical_code, "ENERGY STAR products", "%")
        retrieval = MetricRetrievalResult(
            metric_id=canonical_code,
            metric_name=metric.metric_name,
            metric_code=canonical_code,
            combined_results=[],
        )

        analysis = self.engine._direct_code_data_disclosure_analysis(
            retrieval,
            report,
            metric,
            [{"segment_id": "row", "page_number": 1, "score": 1.0}],
            ["row"],
        )

        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.value, 68.7)
        self.assertFalse(self.engine._contains_metric_code("SASB TC-HW-410a.30", [canonical_code]))

    def test_mixed_weight_and_percentage_skips_direct_and_selects_submetrics(self):
        value = "Weight of end-of-life products and e-waste recovered: 91,000 tons\\nPercentage recycled: 90%"
        analysis, report, row, _ = self._direct("TC-HW-410a.4", value)
        self.assertIsNone(analysis)
        weight = _metric(
            "TC-HW-410a.4.01",
            "TC-HW-410a.4",
            "Weight of end-of-life products and e-waste recovered",
            "Metric tonnes (t)",
        )
        percentage = _metric(
            "TC-HW-410a.4.02",
            "TC-HW-410a.4",
            "Percentage of end-of-life products and e-waste recycled",
            "Percentage (%)",
        )
        weight_candidate = self.engine._select_metric_numeric_candidate(report, row, weight, ["TC-HW-410a.4"])
        percent_candidate = self.engine._select_metric_numeric_candidate(report, row, percentage, ["TC-HW-410a.4"])
        self.assertEqual(weight_candidate["value"], 91000)
        self.assertEqual(percent_candidate["value"], 90)

        metadata = [{"segment_id": "row", "page_number": 1, "score": 1.0}]
        selected_weight = self.engine._select_metric_candidate_from_exact_code_evidence(
            report, metadata, weight, ["TC-HW-410a.4"]
        )
        selected_percent = self.engine._select_metric_candidate_from_exact_code_evidence(
            report, metadata, percentage, ["TC-HW-410a.4"]
        )
        self.assertEqual(selected_weight["value"], 91000)
        self.assertEqual(selected_percent["value"], 90)

    def test_ambiguous_distribution_is_not_replaced_by_one_table_value(self):
        category = _table_segment(
            "technical-category",
            "table_row",
            "U.S. race/ethnicity representation | Technical",
            page=89,
            row_index=1,
        )
        race_row = _table_segment(
            "technical-hispanic-row",
            "table_row",
            "Hispanic or Latino | FY23: 7.8% | FY24: 8.2%",
            page=89,
            row_index=2,
        )
        fy23 = _table_segment(
            "technical-hispanic-fy23",
            "table_cell",
            "FY23: 7.8%",
            page=89,
            row_index=2,
            col_index=2,
            col_header="FY23",
            value_text="7.8%",
        )
        fy24 = _table_segment(
            "technical-hispanic-fy24",
            "table_cell",
            "FY24: 8.2%",
            page=89,
            row_index=2,
            col_index=3,
            col_header="FY24",
            value_text="8.2%",
        )
        report = _report([category, race_row, fy23, fy24])
        metric_name = (
            "Percentage of (2) diversity group representation for "
            "(c) technical employees"
        )
        metric = _metric("technical", "TC-SI-330a.3", metric_name, "Percentage (%)")
        retrieval = MetricRetrievalResult(
            metric_id="technical",
            metric_name=metric_name,
            metric_code="TC-SI-330a.3",
            combined_results=[
                RetrievalResult(
                    segment_id="technical-hispanic-row",
                    content=race_row.content,
                    page_number=89,
                    score=0.95,
                    retrieval_type="rrf:linked_page_category",
                    metric_id="technical",
                ),
                RetrievalResult(
                    segment_id="technical-category",
                    content=category.content,
                    page_number=89,
                    score=0.90,
                    retrieval_type="rrf:linked_page_category",
                    metric_id="technical",
                ),
            ],
            total_matches=2,
        )

        response_payload = {
            "metric_hit": True,
            "disclosure_status": "partially_disclosed",
            "has_disclosure": True,
            "disclosure_quality": "medium",
            "value_status": "ambiguous",
            # Even an inconsistent model response must not force one group value
            # into a metric whose evidence is a complete distribution.
            "value": 8.2,
            "raw_value": 8.2,
            "raw_unit": "%",
            "reasoning": "The technical employee race distribution is disclosed for the U.S. workforce.",
            "page": 89,
            "evidence_segment_id": "technical-hispanic-row",
            "evidence_quote": "Technical: Asian 28.4%; Black 4.1%; Hispanic 8.2%; White 54.6%.",
            "specific_data_found": "FY2024 technical employee race/ethnicity distribution",
            "derived_calculation": None,
            "improvement_suggestions": [],
        }
        fake_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(response_payload))
                )
            ]
        )
        self.engine.config = ProcessingConfig()
        self.engine.llm_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_: fake_response)
            )
        )

        analysis = self.engine._analyze_single_metric(retrieval, report, metric)

        self.assertEqual(analysis.disclosure_status.value, "partially_disclosed")
        self.assertEqual(analysis.value, "n/a")
        self.assertEqual(analysis.page, 89)
        self.assertIn("Asian 28.4%", analysis.context)

    def test_strict_derived_calculation_requires_same_boundary(self):
        metric = _metric("ratio", "RATIO-1", "Renewable energy percentage", "%")
        metric.definition = "The percentage shall be calculated as renewable energy divided by total energy."
        payload = {
            "derived_calculation": {
                "operation": "ratio_percent",
                "formula": "renewable / total * 100",
                "operands": [
                    {"name": "renewable", "value": 50, "unit": "MWh", "year": 2024, "boundary": "global operations", "segment_id": "a"},
                    {"name": "total", "value": 100, "unit": "MWh", "year": 2024, "boundary": "global operations", "segment_id": "b"},
                ],
            }
        }
        metadata = [{"segment_id": "a"}, {"segment_id": "b"}]
        derived = self.engine._validated_derived_calculation(payload, metric, metadata)
        self.assertEqual(derived["result"], 50)
        payload["derived_calculation"]["operands"][1]["boundary"] = "US operations"
        self.assertIsNone(self.engine._validated_derived_calculation(payload, metric, metadata))

    def test_derived_sum_converts_compatible_units(self):
        metric = _metric("mass", "MASS-1", "Total recovered mass", "kg")
        metric.definition = "The total shall be calculated as the sum of both recovered mass streams."
        payload = {
            "derived_calculation": {
                "operation": "sum",
                "formula": "stream A + stream B",
                "operands": [
                    {
                        "name": "stream A",
                        "value": 1000,
                        "unit": "kg",
                        "year": 2024,
                        "boundary": "global operations",
                        "segment_id": "a",
                    },
                    {
                        "name": "stream B",
                        "value": 1,
                        "unit": "t",
                        "year": 2024,
                        "boundary": "global operations",
                        "segment_id": "b",
                    },
                ],
            }
        }
        metadata = [{"segment_id": "a"}, {"segment_id": "b"}]
        derived = self.engine._validated_derived_calculation(payload, metric, metadata)
        self.assertEqual(derived["result"], 2000)
        self.assertEqual(derived["result_unit"], "kg")
        payload["derived_calculation"]["operands"][1]["unit"] = "MWh"
        self.assertIsNone(self.engine._validated_derived_calculation(payload, metric, metadata))


class MetricProfileTests(unittest.TestCase):
    def test_all_generated_profiles_expose_executable_extraction_rules(self):
        profiles = load_all_metric_profiles()
        self.assertEqual(len(profiles), 2162)
        for profile in profiles:
            self.assertTrue(profile.direct_disclosure_rules, profile.metric_id)
            self.assertTrue(profile.year_rules, profile.metric_id)
            self.assertTrue(profile.value_selection_rules, profile.metric_id)
            self.assertEqual(
                set(profile.reject_values_from),
                {
                    "metric_code",
                    "reference_index",
                    "page_number",
                    "row_or_column_number",
                    "standalone_year",
                },
                profile.metric_id,
            )

    def test_extraction_rules_are_loaded_and_enter_the_final_prompt(self):
        metric = _metric(
            "TC-SI-330a.3.03",
            "TC-SI-330a.3",
            "Percentage of (1) gender representation for (c) technical employees",
            "Percentage (%)",
        )
        profile = find_metric_profile(metric)
        self.assertIsNotNone(profile)
        self.assertTrue(
            profile.direct_disclosure_rules["requires_component_label_when_code_shared"]
        )
        self.assertTrue(profile.year_rules["extract_all_reported_years"])
        self.assertIn("standalone_year", profile.reject_values_from)
        self.assertTrue(
            profile.value_selection_rules["match_current_component_before_sibling_values"]
        )
        diversity_profile = find_metric_profile(
            _metric(
                "TC-SI-330a.3.07",
                "TC-SI-330a.3",
                "Percentage of (2) diversity group representation for (c) technical employees",
                "Percentage (%)",
            )
        )
        self.assertEqual(diversity_profile.output_shape, "breakdown")
        self.assertEqual(diversity_profile.variable_dimensions, ["group"])
        self.assertTrue(diversity_profile.requires_dimension_labels)

        engine = object.__new__(DisclosureInferenceEngine)
        prompt = engine._build_analysis_prompt(
            metric.metric_name,
            metric.metric_id,
            [],
            metric_code=metric.metric_code,
            metric_unit=metric.unit,
            metric_profile=profile,
        )
        self.assertIn('"requires_component_label_when_code_shared": true', prompt)
        self.assertIn('"do_not_treat_standalone_year_as_value": true', prompt)
        self.assertIn('"match_current_component_before_sibling_values": true', prompt)

        mentions = engine._numeric_mentions_from_cell_text(
            "TC-SI-330a.3 | page 82 | FY2024 | 25.0%",
            ["TC-SI-330a.3"],
            profile.reject_values_from,
        )
        self.assertEqual([item["value"] for item in mentions], [25])

    def test_software_it_representation_profiles_cover_all_split_metrics(self):
        backend_root = Path(__file__).resolve().parents[1]
        metrics_path = backend_root / "data" / "sasb_metrics" / "Software_and_it_services.json"
        profiles_dir = backend_root / "data" / "sasb_metric_profiles"
        profiles_path = profiles_dir / "Software_and_it_services.profiles.json"
        metric_code = "TC-SI-330a.3"

        metrics = [
            item
            for item in json.loads(metrics_path.read_text(encoding="utf-8"))
            if item.get("Code") == metric_code
        ]
        profiles = [
            item
            for item in json.loads(profiles_path.read_text(encoding="utf-8"))["profiles"]
            if item.get("metric_code") == metric_code
        ]

        expected_names = [item["Metric"] for item in metrics]
        self.assertEqual(len(expected_names), 8)
        self.assertEqual([item["metric"] for item in profiles], expected_names)
        self.assertEqual(
            [item["metric_id"] for item in profiles],
            [f"{metric_code}.{index:02d}" for index in range(1, 9)],
        )
        self.assertNotIn("Percentage of (1) gender", expected_names)

        for profile in (
            item
            for item in profiles
            if "all other employees" in item["metric"].lower()
        ):
            self.assertFalse(
                any(
                    "non-technical" in str(alias).lower()
                    for alias in profile.get("aliases", [])
                ),
                "Non-technical roles is a retrieval proxy, not an exact identity alias",
            )
            self.assertTrue(
                any(
                    "non-technical" in str(term).lower()
                    for term in profile.get("bm25_terms", [])
                )
            )
            self.assertTrue(
                any(
                    "non-technical" in str(term).lower()
                    for term in profile.get("anchor_terms", [])
                )
            )

        load_all_metric_profiles.cache_clear()
        build_profile_index.cache_clear()
        try:
            for index, item in enumerate(metrics):
                metric = _metric(
                    f"split-representation-{index}",
                    metric_code,
                    item["Metric"],
                    item["Unit"],
                )
                profile = find_metric_profile(metric, str(profiles_dir))
                self.assertIsNotNone(profile, item["Metric"])
                self.assertEqual(profile.metric_name, item["Metric"])
        finally:
            load_all_metric_profiles.cache_clear()
            build_profile_index.cache_clear()

    def test_duplicate_code_profiles_are_disambiguated(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "Test.profiles.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "profiles": [
                            {
                                "metric_id": "TC-X-1.01",
                                "metric_code": "TC-X-1",
                                "metric": "Recovered weight",
                                "unit": "Metric tonnes (t)",
                                "aliases": ["Recovered weight"],
                            },
                            {
                                "metric_id": "TC-X-1.02",
                                "metric_code": "TC-X-1",
                                "metric": "Recycled percentage",
                                "unit": "Percentage (%)",
                                "aliases": ["Recycled percentage"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            load_all_metric_profiles.cache_clear()
            build_profile_index.cache_clear()
            try:
                weight = find_metric_profile(
                    _metric("TC-X-1.01", "TC-X-1", "Recovered weight", "Metric tonnes (t)"),
                    tmp,
                )
                percentage = find_metric_profile(
                    _metric("TC-X-1.02", "TC-X-1", "Recycled percentage", "Percentage (%)"),
                    tmp,
                )
                ambiguous = find_metric_profile(_metric("unknown", "TC-X-1", "", ""), tmp)
            finally:
                load_all_metric_profiles.cache_clear()
                build_profile_index.cache_clear()
            self.assertEqual(weight.metric_id, "TC-X-1.01")
            self.assertEqual(percentage.metric_id, "TC-X-1.02")
            self.assertIsNone(ambiguous)


if __name__ == "__main__":
    unittest.main()
