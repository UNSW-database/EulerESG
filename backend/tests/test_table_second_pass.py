from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from esg_encoding.content_extractor import ContentExtractor
from esg_encoding.file_manager import FileManager
from esg_encoding.models import DocumentContent, ReportContent, TextSegment


TABLE_MARKDOWN = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |"""


def _set_semantic_fixture(family: list[TextSegment]) -> TextSegment:
    cell = next(
        segment
        for segment in family
        if segment.segment_type == "table_cell" and segment.value_text == "10"
    )
    cell.header_path = ["Energy", "Direct", "FY2024"]
    cell.unit = "million kWh"
    data = dict(cell.structured_data or {})
    data.update(
        {
            "header_path": ["Energy", "Direct", "FY2024"],
            "year": 2024,
            "source_year_label": "FY24",
            "year_scope": "column_header",
            "unit": "million kWh",
            "unit_base": "kWh",
            "unit_multiplier": 1_000_000.0,
            "unit_scope": "year",
            "quality_reasons": ["semantic_fixture_reason"],
        }
    )
    cell.structured_data = data
    return cell


def _assert_semantic_fixture(cell: TextSegment) -> None:
    data = cell.structured_data or {}
    assert cell.header_path == ["Energy", "Direct", "FY2024"]
    assert cell.unit == "million kWh"
    assert cell.review_status == "verified"
    assert data["header_path"] == ["Energy", "Direct", "FY2024"]
    assert data["year"] == 2024
    assert data["source_year_label"] == "FY24"
    assert data["year_scope"] == "column_header"
    assert data["unit"] == "million kWh"
    assert data["unit_base"] == "kWh"
    assert data["unit_multiplier"] == 1_000_000.0
    assert data["unit_scope"] == "year"
    assert "semantic_fixture_reason" in data["quality_reasons"]


def _table_family(
    extractor: ContentExtractor,
    table_id: str,
    *,
    page: int = 1,
    review_status: str = "verified",
    reasons: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    conflicts: tuple[dict, ...] = (),
    structure_confidence: float | None = 0.95,
    ocr_confidence: float | None = 0.94,
    has_record: bool = True,
    content: str = TABLE_MARKDOWN,
) -> list[TextSegment]:
    document_id = f"fixture_{table_id}"
    markdown = f"<!-- Page {page} | test fixture -->\n\n{content}"
    family = extractor._segments_from_markdown(markdown, document_id)
    family = [segment for segment in family if segment.source_table_id]
    assert family and any(segment.segment_type == "table" for segment in family)

    for index, segment in enumerate(family):
        segment.source_table_id = table_id
        segment.review_status = review_status
        segment.structure_confidence = structure_confidence
        segment.ocr_confidence = ocr_confidence
        segment.conflicts = list(conflicts) if index == 0 else []
        data = dict(segment.structured_data or {})
        data.update(
            {
                "table_id": table_id,
                "review_status": review_status,
                "quality_reasons": list(reasons),
                "quality_notes": list(notes),
                "conflicts": list(conflicts) if index == 0 else [],
                "bbox": [0.1, 0.1, 0.9, 0.5],
                "reading_order": 1,
                "table_match_score": 0.92,
            }
        )
        if has_record:
            data["table_record_id"] = f"record-{table_id}"
        else:
            data.pop("table_record_id", None)
        segment.structured_data = data
    return family


def _case_second_pass_candidates_exclude_clean_and_missing_ocr_note_only() -> None:
    extractor = ContentExtractor()
    clean = _table_family(extractor, "clean")
    missing_ocr_note = _table_family(
        extractor,
        "missing-ocr-note",
        review_status="unverified",
        notes=("missing_ocr_confidence",),
        ocr_confidence=None,
    )
    malformed = _table_family(
        extractor,
        "malformed",
        review_status="needs_review",
        reasons=("malformed_html",),
    )
    conflicted = _table_family(
        extractor,
        "conflicted",
        review_status="needs_review",
        conflicts=({"type": "table_structure_mismatch"},),
    )
    low_confidence = _table_family(
        extractor,
        "low-confidence",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.45,
    )

    plan = extractor._select_table_second_pass_plan(
        [*clean, *missing_ocr_note, *malformed, *conflicted, *low_confidence],
        max_ratio=1.0,
        render_zoom=2.0,
    )

    assert {candidate.source_table_id for candidate in plan.candidates} == {
        "malformed",
        "conflicted",
        "low-confidence",
    }
    assert set(plan.selected_table_ids) == {
        "malformed",
        "conflicted",
        "low-confidence",
    }
    assert "clean" not in plan.selected_table_ids
    assert "missing-ocr-note" not in plan.selected_table_ids


def _case_second_pass_budget_uses_ceil_ratio_and_deduplicates_pages() -> None:
    extractor = ContentExtractor()
    segments: list[TextSegment] = []
    for index in range(47):
        # Several selected physical tables intentionally share a page.  The
        # repair budget is table-based while OCR work is page-deduplicated.
        segments.extend(
            _table_family(
                extractor,
                f"table-{index:02d}",
                page=(index // 4) + 1,
                review_status="needs_review",
                reasons=("inconsistent_column_count",),
            )
        )

    plan = extractor._select_table_second_pass_plan(
        segments,
        max_ratio=0.30,
        render_zoom=2.0,
    )

    assert plan.total_tables == 47
    assert plan.budget_tables == 15
    assert len(plan.selected_table_ids) == 15
    assert len(plan.pages) < len(plan.selected_table_ids)
    assert plan.pages == tuple(sorted(set(plan.pages)))


def _case_second_pass_prediction_options_scale_and_clamp_pixels_and_tokens() -> None:
    extractor = ContentExtractor()
    family = _table_family(
        extractor,
        "scale-options",
        review_status="needs_review",
        reasons=("missing_header",),
    )

    with patch.dict(
        os.environ,
        {
            "PADDLEOCR_VLM_MIN_PIXELS": "1000000",
            "PADDLEOCR_VLM_MAX_PIXELS": "2000000",
            "PADDLEOCR_VLM_MAX_NEW_TOKENS": "2048",
            "REPORT_TABLE_SECOND_PASS_MAX_NEW_TOKENS": "12000",
        },
    ):
        plan = extractor._select_table_second_pass_plan(
            family,
            max_ratio=1.0,
            render_zoom=3.0,
        )

    options = plan.prediction_options[1]
    assert plan.render_zoom == 3.0
    assert options["use_layout_detection"] is True
    assert options["use_ocr_for_image_block"] is True
    assert options["min_pixels"] == 4_014_080
    assert options["max_pixels"] == 4_014_080
    assert options["max_new_tokens"] == 8192


def _case_selective_split_renders_a_real_high_resolution_png(tmp_path: Path) -> None:
    extractor = ContentExtractor()
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-test")
    observed: dict[str, object] = {}

    class FakePixmap:
        def save(self, path: str) -> None:
            Path(path).write_bytes(b"fake high-resolution png")

    class FakePage:
        rect = SimpleNamespace(width=1000.0, height=1000.0)

        def get_pixmap(self, *, matrix, alpha: bool):
            observed["matrix"] = matrix
            observed["alpha"] = alpha
            return FakePixmap()

    class FakeDocument:
        def __len__(self) -> int:
            return 1

        def load_page(self, index: int) -> FakePage:
            observed["page_index"] = index
            return FakePage()

        def close(self) -> None:
            observed["closed"] = True

    class FakeMatrix:
        def __init__(self, x: float, y: float) -> None:
            self.x = x
            self.y = y

    fake_fitz = SimpleNamespace(
        open=lambda _path: FakeDocument(),
        Matrix=FakeMatrix,
    )
    prediction_options = {1: {"max_pixels": 4_014_080, "max_new_tokens": 4096}}

    with patch.dict(sys.modules, {"fitz": fake_fitz}), patch.dict(
        os.environ,
        {
            "PADDLEOCR_JOB_WORK_DIR": str(tmp_path / "jobs"),
            "PADDLEOCR_SPLIT_VISIBILITY_WAIT_SECONDS": "0",
            "REPORT_TABLE_SECOND_PASS_MAX_RENDER_PIXELS": "4000000",
        },
    ):
        units, total_pages, _ = extractor._split_pdf_for_page_batch_queue(
            source_path,
            "repair-job",
            1,
            page_numbers=[1],
            page_options=prediction_options,
            render_zoom=2.5,
        )

    assert total_pages == 1
    assert len(units) == 1
    assert units[0]["input_path"].endswith(".png")
    assert Path(units[0]["input_path"]).read_bytes() == b"fake high-resolution png"
    # A requested 2.5x render of a 1000x1000 page would be 6.25M pixels;
    # the configured 4M ceiling safely reduces the effective zoom to 2x.
    assert units[0]["requested_render_zoom"] == 2.5
    assert units[0]["render_zoom"] == 2.0
    assert units[0]["prediction_options"] == prediction_options[1]
    assert observed["page_index"] == 0
    assert observed["alpha"] is False
    assert (observed["matrix"].x, observed["matrix"].y) == (2.0, 2.0)
    assert observed["closed"] is True

    ready_payload = json.loads(
        Path(units[0]["ready_path"]).read_text(encoding="utf-8")
    )
    assert ready_payload["requested_render_zoom"] == 2.5
    assert ready_payload["render_zoom"] == 2.0


def _case_improved_second_pass_replaces_the_complete_family_and_marks_pass_two() -> None:
    extractor = ContentExtractor()
    first = _table_family(
        extractor,
        "first-table",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        conflicts=({"type": "table_structure_mismatch"},),
        structure_confidence=0.40,
        ocr_confidence=0.50,
    )
    first_table = next(segment for segment in first if segment.segment_type == "table")
    first_table.structured_data["table_title"] = "Energy performance"
    first_table.structured_data["asset_ids"] = ["stable-asset"]
    first_table.structured_data["source_image_paths"] = ["/durable/table.png"]
    original_ids = {segment.segment_id for segment in first}
    plan = extractor._select_table_second_pass_plan(
        first,
        max_ratio=1.0,
        render_zoom=2.5,
    )

    improved = _table_family(
        extractor,
        "second-output-table",
        review_status="verified",
        structure_confidence=0.99,
        ocr_confidence=0.98,
    )
    _set_semantic_fixture(improved)
    stats = extractor._apply_table_second_pass(
        first,
        improved,
        plan,
        effective_render_zoom=2.25,
    )

    assert stats["accepted_tables"] == 1
    assert stats["accepted_table_ids"] == ["first-table"]
    assert len(first) == len(improved)
    assert all(segment.source_table_id == "first-table" for segment in first)
    assert all(segment.parse_pass == 2 for segment in first)
    assert not original_ids.intersection(segment.segment_id for segment in first)
    replacement_ids = {segment.segment_id for segment in first}
    for segment in first:
        data = segment.structured_data or {}
        assert data["parse_pass"] == 2
        assert data["second_pass_replaced"] is True
        assert data["previous_parse_pass"] == 1
        if segment.segment_type == "table":
            assert data["requested_render_zoom"] == 2.5
            assert data["effective_render_zoom"] == 2.25
            assert data["second_pass_prediction_options"] == plan.prediction_options[1]
            assert data["resolved_conflicts"] == [
                {"type": "table_structure_mismatch"}
            ]
        else:
            assert "first_pass_quality" not in data
            assert "second_pass_quality" not in data
            assert "first_pass_provenance" not in data
            assert "second_pass_provenance" not in data
        if data.get("row_segment_id"):
            assert data["row_segment_id"] in replacement_ids
    replaced_table = next(segment for segment in first if segment.segment_type == "table")
    assert replaced_table.structured_data["table_title"] == "Energy performance"
    assert replaced_table.structured_data["table_record_id"].startswith(
        "embedded-pass2:first-table:"
    )
    assert (
        replaced_table.structured_data["table_record_source"]
        == "embedded_second_pass"
    )
    assert replaced_table.structured_data["asset_ids"] == ["stable-asset"]
    assert replaced_table.structured_data["source_image_paths"] == ["/durable/table.png"]
    assert replaced_table.structured_data["asset_provenance_parse_pass"] == 1
    assert (
        replaced_table.structured_data["first_pass_provenance"]["table_record_id"]
        == "record-first-table"
    )
    assert (
        replaced_table.structured_data["second_pass_provenance"]["table_record_id"]
        == "record-second-output-table"
    )
    replacement_value = next(
        segment
        for segment in first
        if segment.segment_type == "table_cell" and segment.value_text == "10"
    )
    _assert_semantic_fixture(replacement_value)


def _case_table_semantics_survive_artifact_roundtrip(tmp_path: Path) -> None:
    extractor = ContentExtractor()
    family = _table_family(extractor, "semantic-roundtrip")
    _set_semantic_fixture(family)
    manager = FileManager(str(tmp_path / "uploads"))
    report = ReportContent(
        document_id="semantic-document",
        document_content=DocumentContent(
            document_id="semantic-document",
            file_path="semantic-report.pdf",
            segments=family,
            markdown_content=TABLE_MARKDOWN,
        ),
        embeddings=[],
    )

    manager.save_report_artifacts("semantic-roundtrip", report)
    loaded = manager.load_report_artifacts("semantic-roundtrip")

    assert loaded is not None
    loaded_value = next(
        segment
        for segment in loaded["segments"]
        if segment.segment_type == "table_cell" and segment.value_text == "10"
    )
    _assert_semantic_fixture(loaded_value)


def _case_equal_or_degraded_second_pass_never_replaces_first_pass() -> None:
    extractor = ContentExtractor()

    for case, second_conflicts, second_structure in (
        ("equal", (), 0.70),
        ("degraded", ({"type": "new_conflict"},), 0.30),
    ):
        first = _table_family(
            extractor,
            f"first-{case}",
            review_status="needs_review",
            reasons=("low_structure_confidence",),
            structure_confidence=0.70,
            ocr_confidence=0.80,
        )
        original_ids = [segment.segment_id for segment in first]
        plan = extractor._select_table_second_pass_plan(
            first,
            max_ratio=1.0,
            render_zoom=2.0,
        )
        second = _table_family(
            extractor,
            f"second-{case}",
            review_status="needs_review",
            reasons=("low_structure_confidence",),
            conflicts=second_conflicts,
            structure_confidence=second_structure,
            ocr_confidence=0.80,
        )

        stats = extractor._apply_table_second_pass(first, second, plan)

        assert stats["accepted_tables"] == 0
        if case == "equal":
            assert stats["rejected_not_improved"] == 1
            assert stats["rejected_incomplete"] == 0
        else:
            assert stats["rejected_not_improved"] == 0
            assert stats["rejected_incomplete"] == 1
        assert [segment.segment_id for segment in first] == original_ids
        assert all(segment.parse_pass == 1 for segment in first)
        assert not any(
            (segment.structured_data or {}).get("second_pass_replaced")
            for segment in first
        )


def _case_runner_passes_selective_pages_zoom_options_and_parse_pass_two(
    tmp_path: Path,
) -> None:
    extractor = ContentExtractor()
    first = _table_family(
        extractor,
        "runner-table",
        page=3,
        review_status="needs_review",
        reasons=("missing_header",),
    )
    returned_markdown = f"<!-- Page 3 | second pass -->\n\n{TABLE_MARKDOWN}"

    with patch.dict(
        os.environ,
        {
            "REPORT_TABLE_SECOND_PASS_ENABLED": "true",
            "REPORT_TABLE_SECOND_PASS_MAX_RATIO": "1.0",
            "REPORT_TABLE_SECOND_PASS_RENDER_ZOOM": "2.75",
        },
    ), patch.object(
        extractor,
        "_run_paddleocr_vl_page_batch_queue",
        return_value={
            "markdown": returned_markdown,
            "table_records": [],
            "processed_pages": [3],
            "effective_render_zoom": 2.5,
        },
    ) as queue_run, patch.object(
        extractor,
        "_apply_table_second_pass",
        return_value={"accepted_tables": 1, "accepted_table_ids": ["runner-table"]},
    ) as apply_pass:
        summary = extractor._run_table_second_pass(
            tmp_path / "report.pdf",
            "doc",
            first,
            release_after_document=False,
        )

    queue_kwargs = queue_run.call_args.kwargs
    assert queue_kwargs["selected_page_numbers"] == (3,)
    assert queue_kwargs["prediction_options_by_page"][3]["max_new_tokens"] >= 4096
    assert queue_kwargs["prediction_options_by_page"][3]["max_pixels"] > 1_003_520
    assert queue_kwargs["partial_result"] is True
    assert queue_kwargs["promote_visuals"] is False
    assert queue_kwargs["parse_pass"] == 2
    assert queue_kwargs["render_zoom"] == 2.75
    assert queue_kwargs["release_after_document"] is False
    assert apply_pass.call_args.kwargs["effective_render_zoom"] == 2.5
    assert summary["accepted_tables"] == 1
    assert summary["successful_pages"] == [3]


def _case_unrelated_singleton_never_replaces_candidate() -> None:
    extractor = ContentExtractor()
    first = _table_family(
        extractor,
        "energy-table",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.40,
    )
    second = _table_family(
        extractor,
        "unrelated-table",
        review_status="verified",
        structure_confidence=0.99,
        content="""| Board category | FY2024 |
| --- | --- |
| Independent directors | 99% |""",
    )
    for segment in first:
        segment.structured_data["bbox"] = [0.05, 0.05, 0.40, 0.30]
    for segment in second:
        segment.structured_data["bbox"] = [0.60, 0.60, 0.95, 0.90]
    plan = extractor._select_table_second_pass_plan(
        first,
        max_ratio=1.0,
        render_zoom=2.0,
    )

    stats = extractor._apply_table_second_pass(first, second, plan)

    assert stats["accepted_tables"] == 0
    assert next(
        segment for segment in first if segment.segment_type == "table"
    ).content == TABLE_MARKDOWN


def _case_duplicate_second_pass_segment_ids_are_rejected() -> None:
    extractor = ContentExtractor()
    first = _table_family(
        extractor,
        "duplicate-id-target",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.40,
    )
    plan = extractor._select_table_second_pass_plan(
        first,
        max_ratio=1.0,
        render_zoom=2.0,
    )
    second = _table_family(
        extractor,
        "duplicate-id-output",
        review_status="verified",
        structure_confidence=0.99,
    )
    second[1].segment_id = second[0].segment_id
    original_ids = [segment.segment_id for segment in first]

    stats = extractor._apply_table_second_pass(first, second, plan)

    assert stats["accepted_tables"] == 0
    assert stats["rejected_incomplete"] == 1
    assert [segment.segment_id for segment in first] == original_ids


def _case_bboxless_same_page_tables_cannot_cross_match_by_text() -> None:
    extractor = ContentExtractor()
    revenue_table = """| Metric | FY2024 |
| --- | --- |
| Revenue | 100 |"""
    energy_table = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |"""
    first_a = _table_family(
        extractor,
        "first-a",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.40,
        content=revenue_table,
    )
    first_b = _table_family(
        extractor,
        "first-b",
        review_status="verified",
        structure_confidence=0.98,
        content=revenue_table,
    )
    second_a = _table_family(
        extractor,
        "second-a",
        review_status="verified",
        structure_confidence=0.99,
        content=energy_table,
    )
    second_b = _table_family(
        extractor,
        "second-b",
        review_status="verified",
        structure_confidence=0.99,
        content=revenue_table,
    )
    first = [*first_a, *first_b]
    second = [*second_a, *second_b]
    for segment in [*first, *second]:
        (segment.structured_data or {}).pop("bbox", None)
    plan = extractor._select_table_second_pass_plan(
        first,
        max_ratio=1.0,
        render_zoom=2.0,
    )

    stats = extractor._apply_table_second_pass(first, second, plan)

    assert stats["accepted_tables"] == 0
    assert stats["no_match_tables"] == 1
    preserved_a = next(
        segment
        for segment in first
        if segment.segment_type == "table"
        and segment.source_table_id == "first-a"
    )
    assert "Revenue" in preserved_a.content
    assert "Energy use" not in preserved_a.content
    assert preserved_a.parse_pass == 1
    assert not (preserved_a.structured_data or {}).get("second_pass_replaced")
    assert "second_pass_provenance" not in (preserved_a.structured_data or {})


def _case_bboxless_same_page_tables_match_unique_identity_across_order() -> None:
    extractor = ContentExtractor()
    revenue_table = """| Metric | FY2024 |
| --- | --- |
| Revenue | 100 |"""
    water_table = """| Metric | FY2024 |
| --- | --- |
| Water use | 20 |"""
    first_revenue = _table_family(
        extractor,
        "first-revenue",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.40,
        content=revenue_table,
    )
    first_water = _table_family(
        extractor,
        "first-water",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.40,
        content=water_table,
    )
    # Deliberately reverse second-pass order.  Without bbox, ordinal position
    # must not participate in table identity.
    second_water = _table_family(
        extractor,
        "second-water",
        review_status="verified",
        structure_confidence=0.99,
        content=water_table,
    )
    second_revenue = _table_family(
        extractor,
        "second-revenue",
        review_status="verified",
        structure_confidence=0.99,
        content=revenue_table,
    )
    first = [*first_revenue, *first_water]
    second = [*second_water, *second_revenue]
    for segment in [*first, *second]:
        (segment.structured_data or {}).pop("bbox", None)
    plan = extractor._select_table_second_pass_plan(
        first,
        max_ratio=1.0,
        render_zoom=2.0,
    )

    stats = extractor._apply_table_second_pass(first, second, plan)

    assert stats["accepted_tables"] == 2
    tables = {
        str(segment.source_table_id): segment
        for segment in first
        if segment.segment_type == "table"
    }
    assert "Revenue" in tables["first-revenue"].content
    assert "Water use" in tables["first-water"].content
    assert (
        tables["first-revenue"].structured_data["second_pass_provenance"][
            "table_record_id"
        ]
        == "record-second-revenue"
    )
    assert (
        tables["first-water"].structured_data["second_pass_provenance"][
            "table_record_id"
        ]
        == "record-second-water"
    )


def _case_second_pass_near_tie_fails_unique_match_margin() -> None:
    extractor = ContentExtractor()
    target = _table_family(
        extractor,
        "target",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.40,
    )
    clean_sibling = _table_family(
        extractor,
        "clean-sibling",
        review_status="verified",
        structure_confidence=0.98,
    )
    second_a = _table_family(
        extractor,
        "second-a",
        review_status="verified",
        structure_confidence=0.99,
    )
    second_b = _table_family(
        extractor,
        "second-b",
        review_status="verified",
        structure_confidence=0.99,
    )
    first = [*target, *clean_sibling]
    second = [*second_a, *second_b]
    for segment in [*first, *second]:
        (segment.structured_data or {}).pop("bbox", None)
    plan = extractor._select_table_second_pass_plan(
        first,
        max_ratio=1.0,
        render_zoom=2.0,
    )
    score_matrix = {
        ("target", "second-a"): 0.90,
        ("target", "second-b"): 0.85,
        ("clean-sibling", "second-a"): 0.60,
        ("clean-sibling", "second-b"): 0.90,
    }

    def match_score(first_table, second_table, **_kwargs):
        score = score_matrix[
            (str(first_table.source_table_id), str(second_table.source_table_id))
        ]
        return score, score, 0.0

    with patch.dict(
        os.environ,
        # A zero override must not disable the mandatory uniqueness margin.
        {"REPORT_TABLE_SECOND_PASS_MATCH_MARGIN": "0"},
    ), patch.object(
        extractor,
        "_second_pass_table_match_score",
        side_effect=match_score,
    ):
        stats = extractor._apply_table_second_pass(first, second, plan)

    assert stats["accepted_tables"] == 0
    assert stats["no_match_tables"] == 1
    preserved = next(
        segment
        for segment in first
        if segment.segment_type == "table"
        and segment.source_table_id == "target"
    )
    assert preserved.parse_pass == 1
    assert not (preserved.structured_data or {}).get("second_pass_replaced")


def _case_second_pass_requires_compatible_semantic_axes() -> None:
    compatible_first = """| Metric | Unit | FY2024 |
| --- | --- | --- |
| Energy use | kWh | 10 |"""
    incompatible_second_tables = {
        "header": """| Category | Unit | FY2024 |
| --- | --- | --- |
| Energy use | kWh | 10 |""",
        "year": """| Metric | Unit | FY2023 |
| --- | --- | --- |
| Energy use | kWh | 10 |""",
        "unit": """| Metric | Unit | FY2024 |
| --- | --- | --- |
| Energy use | tCO2e | 10 |""",
        "row_label": """| Metric | Unit | FY2024 |
| --- | --- | --- |
| Water use | kWh | 10 |""",
    }
    for case, second_content in incompatible_second_tables.items():
        extractor = ContentExtractor()
        first = _table_family(
            extractor,
            f"first-{case}",
            review_status="needs_review",
            reasons=("low_structure_confidence",),
            structure_confidence=0.40,
            content=compatible_first,
        )
        second = _table_family(
            extractor,
            f"second-{case}",
            review_status="verified",
            structure_confidence=0.99,
            content=second_content,
        )
        for segment in [*first, *second]:
            (segment.structured_data or {}).pop("bbox", None)
        plan = extractor._select_table_second_pass_plan(
            first,
            max_ratio=1.0,
            render_zoom=2.0,
        )

        stats = extractor._apply_table_second_pass(first, second, plan)

        assert stats["accepted_tables"] == 0, case
        assert all(segment.parse_pass == 1 for segment in first), case

    for first_label, second_label in (
        ("Renewable", "Non-renewable"),
        ("Hazardous", "Non-hazardous"),
        ("Direct", "Indirect"),
        ("Technical", "Non-technical"),
    ):
        extractor = ContentExtractor()
        first = _table_family(
            extractor,
            f"first-{first_label}",
            review_status="needs_review",
            reasons=("low_structure_confidence",),
            structure_confidence=0.40,
            content=(
                "| Category | FY2024 |\n| --- | --- |\n"
                f"| {first_label} | 10 |"
            ),
        )
        second = _table_family(
            extractor,
            f"second-{second_label}",
            review_status="verified",
            structure_confidence=0.99,
            content=(
                "| Category | FY2024 |\n| --- | --- |\n"
                f"| {second_label} | 10 |"
            ),
        )
        for segment in [*first, *second]:
            (segment.structured_data or {}).pop("bbox", None)
        plan = extractor._select_table_second_pass_plan(
            first,
            max_ratio=1.0,
            render_zoom=2.0,
        )

        stats = extractor._apply_table_second_pass(first, second, plan)

        assert stats["accepted_tables"] == 0, (first_label, second_label)

    # Numeric text is deliberately excluded from identity.  A high-resolution
    # pass must still be able to correct a value when every semantic axis agrees.
    extractor = ContentExtractor()
    first = _table_family(
        extractor,
        "first-value-fix",
        review_status="needs_review",
        reasons=("low_structure_confidence",),
        structure_confidence=0.40,
        content=compatible_first,
    )
    second = _table_family(
        extractor,
        "second-value-fix",
        review_status="verified",
        structure_confidence=0.99,
        content=compatible_first.replace("| 10 |", "| 11 |"),
    )
    for segment in [*first, *second]:
        (segment.structured_data or {}).pop("bbox", None)
    plan = extractor._select_table_second_pass_plan(
        first,
        max_ratio=1.0,
        render_zoom=2.0,
    )

    stats = extractor._apply_table_second_pass(first, second, plan)

    assert stats["accepted_tables"] == 1
    repaired = next(
        segment for segment in first if segment.segment_type == "table"
    )
    assert "| 11 |" in repaired.content


def _case_json_only_record_materializes_a_table_family() -> None:
    extractor = ContentExtractor()
    segments = [
        TextSegment(
            segment_id="text-only",
            content="OCR narrative without a Markdown table projection.",
            page_number=4,
            position_y=0.0,
            segment_type="text",
        )
    ]
    record = {
        "table_id": "json-table-1",
        "page_number": 4,
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr></table>"
        ),
        "structure_confidence": 0.96,
        "ocr_confidence": 0.95,
        "parse_pass": 2,
        "bbox": [0.1, 0.2, 0.9, 0.6],
    }

    added = extractor._materialize_unmatched_table_records(
        segments,
        [record],
        "doc-sp2",
    )

    family = [segment for segment in segments if segment.source_table_id]
    assert added == 1
    assert {segment.segment_type for segment in family} >= {
        "table",
        "table_row",
        "table_cell",
    }
    assert all(segment.parse_pass == 2 for segment in family)
    assert all(
        (segment.structured_data or {}).get("table_record_id") == "json-table-1"
        for segment in family
    )


def _case_record_summary_is_not_promoted_to_table_caption() -> None:
    extractor = ContentExtractor()
    segments: list[TextSegment] = []
    record = {
        "table_id": "summary-only-table",
        "page_number": 4,
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr></table>"
        ),
        "summary": "A generic two-column metrics table.",
        "structure_confidence": 0.96,
        "parse_pass": 2,
        "bbox": [0.1, 0.2, 0.9, 0.6],
    }

    extractor._materialize_unmatched_table_records(
        segments,
        [record],
        "doc-sp2",
    )

    table = next(segment for segment in segments if segment.segment_type == "table")
    data = table.structured_data or {}
    assert data.get("table_title") in (None, "")
    assert data.get("table_title_source") in (None, "")
    assert data.get("summary") == "A generic two-column metrics table."


def _case_identical_anonymous_records_keep_distinct_table_families() -> None:
    extractor = ContentExtractor()
    raw_html = (
        "<table><tr><th>Metric</th><th>FY2024</th></tr>"
        "<tr><td>Energy use</td><td>10</td></tr></table>"
    )
    records = [
        {
            "page_number": 4,
            "pred_html": raw_html,
            "bbox": [0.1, 0.1, 0.4, 0.3],
            "reading_order": 1,
            "structure_confidence": 0.96,
            "parse_pass": 2,
        },
        {
            "page_number": 4,
            "pred_html": raw_html,
            "bbox": [0.6, 0.6, 0.9, 0.8],
            "reading_order": 2,
            "structure_confidence": 0.96,
            "parse_pass": 2,
        },
    ]
    segments: list[TextSegment] = []

    added = extractor._materialize_unmatched_table_records(
        segments,
        records,
        "doc-sp2",
    )

    tables = [segment for segment in segments if segment.segment_type == "table"]
    assert added == 2
    assert len(tables) == 2
    assert len({table.source_table_id for table in tables}) == 2
    assert len({segment.segment_id for segment in segments}) == len(segments)


def _case_structured_record_replaces_truncated_markdown_family() -> None:
    extractor = ContentExtractor()
    segments = extractor._segments_from_markdown(
        "<!-- Page 5 | first pass -->\n\n" + TABLE_MARKDOWN,
        "doc",
    )
    record = {
        "table_id": "complete-json-table",
        "page_number": 5,
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr>"
            "<tr><td>Water use</td><td>20</td></tr></table>"
        ),
        "structure_confidence": 0.98,
        "ocr_confidence": 0.97,
        "parse_pass": 2,
    }

    added = extractor._prefer_structured_table_records(
        segments,
        [record],
        "doc-sp2",
    )

    table_rows = [segment for segment in segments if segment.segment_type == "table_row"]
    cell_values = {
        str(segment.value_text)
        for segment in segments
        if segment.segment_type == "table_cell"
    }
    assert added == 1
    assert len(table_rows) == 2
    assert "20" in cell_values
    assert all(segment.parse_pass == 2 for segment in segments if segment.source_table_id)
    canonical_table = next(
        segment for segment in segments if segment.segment_type == "table"
    )
    canonical_data = canonical_table.structured_data or {}
    # Canonicalising the complete JSON table must not erase the fact that its
    # Markdown projection disagreed.  The retained conflict is what schedules
    # this table for a selective second pass.
    assert canonical_table.review_status == "needs_review"
    assert "structure_source_conflict" in canonical_data["quality_reasons"]
    assert any(
        conflict.get("type") == "table_structure_mismatch"
        for conflict in canonical_table.conflicts
    )
    plan = extractor._select_table_second_pass_plan(
        segments,
        max_ratio=1.0,
        render_zoom=2.0,
    )
    assert plan.selected_table_ids == (canonical_table.source_table_id,)
    final_markdown = extractor._markdown_from_final_segments(segments)
    assert "Page 5" in final_markdown
    assert final_markdown.count("Water use") == 1


def _case_less_complete_structured_record_never_deletes_markdown_rows() -> None:
    extractor = ContentExtractor()
    complete_markdown = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |
| Water use | 20 |"""
    segments = extractor._segments_from_markdown(
        "<!-- Page 6 | first pass -->\n\n" + complete_markdown,
        "doc",
    )
    record = {
        "table_id": "short-json-table",
        "page_number": 6,
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr></table>"
        ),
        "structure_confidence": 0.99,
        "ocr_confidence": 0.98,
        "parse_pass": 1,
    }

    added = extractor._prefer_structured_table_records(
        segments,
        [record],
        "doc",
    )

    table = next(segment for segment in segments if segment.segment_type == "table")
    assert added == 0
    assert "Water use" in table.content
    assert not (table.structured_data or {}).get("canonicalized_from_markdown")
    assert table.review_status == "needs_review"
    assert "structure_source_conflict" in (
        table.structured_data or {}
    )["quality_reasons"]


def _case_structured_record_preserves_noncontiguous_ordered_projection() -> None:
    extractor = ContentExtractor()
    markdown = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |
| Water use | 20 |"""
    segments = extractor._segments_from_markdown(
        "<!-- Page 7 | first pass -->\n\n" + markdown,
        "doc",
    )
    table = next(segment for segment in segments if segment.segment_type == "table")
    table.structured_data["bbox"] = [0.1, 0.1, 0.9, 0.6]
    record = {
        "table_id": "ordered-superset",
        "page_number": 7,
        "bbox": [0.1, 0.1, 0.9, 0.6],
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr>"
            "<tr><td>GHG emissions</td><td>30</td></tr>"
            "<tr><td>Water use</td><td>20</td></tr>"
            "<tr><td>Waste</td><td>40</td></tr></table>"
        ),
        "structure_confidence": 0.99,
        "ocr_confidence": 0.98,
        "parse_pass": 1,
    }

    added = extractor._prefer_structured_table_records(
        segments,
        [record],
        "doc",
    )

    assert added == 1
    canonical = next(
        segment for segment in segments if segment.segment_type == "table"
    )
    assert "GHG emissions" in canonical.content
    assert "Water use" in canonical.content
    assert (canonical.structured_data or {}).get("canonicalized_from_markdown") is True


def _case_structured_record_can_correct_explicit_low_confidence_row() -> None:
    extractor = ContentExtractor()
    markdown = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |"""
    segments = extractor._segments_from_markdown(
        "<!-- Page 9 | first pass -->\n\n" + markdown,
        "doc-low-confidence",
    )
    table = next(segment for segment in segments if segment.segment_type == "table")
    table.structured_data["bbox"] = [0.1, 0.1, 0.9, 0.6]
    source_row = next(
        segment for segment in segments if segment.segment_type == "table_row"
    )
    source_row.review_status = "needs_review"
    source_row.ocr_confidence = 0.30
    source_row_data = dict(source_row.structured_data or {})
    source_row_data.update(
        {
            "review_status": "needs_review",
            "ocr_confidence": 0.30,
            "quality_reasons": ["low_ocr_confidence"],
        }
    )
    source_row.structured_data = source_row_data
    record = {
        "table_id": "low-confidence-correction",
        "page_number": 9,
        "bbox": [0.1, 0.1, 0.9, 0.6],
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>11</td></tr>"
            "<tr><td>Water use</td><td>20</td></tr></table>"
        ),
        "structure_confidence": 0.99,
        "ocr_confidence": 0.98,
        "parse_pass": 1,
    }

    added = extractor._prefer_structured_table_records(
        segments,
        [record],
        "doc-low-confidence",
    )

    assert added == 1
    energy_values = {
        str(segment.value_text)
        for segment in segments
        if segment.segment_type == "table_cell"
        and segment.row_header == "Energy use"
    }
    assert "11" in energy_values
    assert "10" not in energy_values


def _case_structured_record_low_confidence_corrections_remain_bounded() -> None:
    extractor = ContentExtractor()

    # A non-empty source row cannot disappear merely because it contains only
    # a stable label and no measurement value.
    notes_markdown = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |
| Notes | |"""
    notes_replacement = (
        "<table><tr><th>Metric</th><th>FY2024</th></tr>"
        "<tr><td>Energy use</td><td>10</td></tr>"
        "<tr><td>Water use</td><td>20</td></tr></table>"
    )
    assert not extractor._structured_table_preserves_projection(
        notes_markdown,
        notes_replacement,
        trusted_markdown_row_indices=set(),
    )

    # Pure numeric dimensions are immutable. Scope 1 is not a measurement
    # value merely because its cell begins with a digit.
    assert not extractor._structured_table_preserves_projection(
        """| Metric | Scope | FY2024 |
| --- | --- | --- |
| Emissions | 1 | 10 |""",
        (
            "<table><tr><th>Metric</th><th>Scope</th><th>FY2024</th></tr>"
            "<tr><td>Emissions</td><td>2</td><td>20</td></tr></table>"
        ),
        trusted_markdown_row_indices=set(),
    )
    assert not extractor._structured_table_preserves_projection(
        """| Metric | Period number | FY2024 |
| --- | --- | --- |
| Energy use | 1 | 10 |""",
        (
            "<table><tr><th>Metric</th><th>Period number</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>2</td><td>20</td></tr></table>"
        ),
        trusted_markdown_row_indices=set(),
    )

    # Low OCR confidence may change a numeric measurement, but it may not
    # rewrite stable Region/category dimensions under the same row label.
    dimension_markdown = """| Metric | Region | FY2024 |
| --- | --- | --- |
| Energy use | US | 10 |
| Energy use | EU | 20 |"""
    segments = extractor._segments_from_markdown(
        "<!-- Page 14 | first pass -->\n\n" + dimension_markdown,
        "doc-low-dimension",
    )
    table = next(segment for segment in segments if segment.segment_type == "table")
    table.structured_data["bbox"] = [0.1, 0.1, 0.9, 0.6]
    for source_row in [
        segment for segment in segments if segment.segment_type == "table_row"
    ]:
        source_row.review_status = "needs_review"
        source_row.ocr_confidence = 0.30
        data = dict(source_row.structured_data or {})
        data.update(
            {
                "review_status": "needs_review",
                "ocr_confidence": 0.30,
                "quality_reasons": ["low_ocr_confidence"],
            }
        )
        source_row.structured_data = data
    dimension_record = {
        "table_id": "unsafe-dimension-correction",
        "page_number": 14,
        "bbox": [0.1, 0.1, 0.9, 0.6],
        "pred_html": (
            "<table><tr><th>Metric</th><th>Region</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>Asia</td><td>30</td></tr>"
            "<tr><td>Energy use</td><td>Africa</td><td>40</td></tr></table>"
        ),
        "structure_confidence": 0.99,
        "ocr_confidence": 0.98,
    }

    added = extractor._prefer_structured_table_records(
        segments,
        [dimension_record],
        "doc-low-dimension",
    )

    preserved = next(segment for segment in segments if segment.segment_type == "table")
    assert added == 0
    assert "US" in preserved.content
    assert "EU" in preserved.content
    assert "Asia" not in preserved.content

    # A low-confidence source row can only be corrected by a sufficiently
    # reliable OCR record.
    low_ocr_segments = extractor._segments_from_markdown(
        "<!-- Page 15 | first pass -->\n\n" + TABLE_MARKDOWN,
        "doc-low-source-record",
    )
    low_ocr_table = next(
        segment for segment in low_ocr_segments if segment.segment_type == "table"
    )
    low_ocr_table.structured_data["bbox"] = [0.1, 0.1, 0.9, 0.6]
    low_ocr_row = next(
        segment
        for segment in low_ocr_segments
        if segment.segment_type == "table_row"
    )
    low_ocr_row.review_status = "needs_review"
    low_ocr_row.ocr_confidence = 0.30
    low_ocr_row_data = dict(low_ocr_row.structured_data or {})
    low_ocr_row_data.update(
        {
            "review_status": "needs_review",
            "ocr_confidence": 0.30,
            "quality_reasons": ["low_ocr_confidence"],
        }
    )
    low_ocr_row.structured_data = low_ocr_row_data
    low_ocr_record = {
        "table_id": "untrusted-correction-record",
        "page_number": 15,
        "bbox": [0.1, 0.1, 0.9, 0.6],
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>11</td></tr></table>"
        ),
        "structure_confidence": 0.99,
        "ocr_confidence": 0.10,
    }

    low_ocr_added = extractor._prefer_structured_table_records(
        low_ocr_segments,
        [low_ocr_record],
        "doc-low-source-record",
    )

    low_ocr_preserved = next(
        segment
        for segment in low_ocr_segments
        if segment.segment_type == "table"
    )
    assert low_ocr_added == 0
    assert "Energy use | 10" in low_ocr_preserved.content


def _case_structured_record_rejects_non_preserving_supersets() -> None:
    markdown = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |
| Water use | 20 |"""
    unsafe_records = {
        "missing_old_row": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr>"
            "<tr><td>Waste</td><td>30</td></tr>"
            "<tr><td>GHG emissions</td><td>40</td></tr></table>"
        ),
        "reordered_old_rows": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Water use</td><td>20</td></tr>"
            "<tr><td>Energy use</td><td>10</td></tr>"
            "<tr><td>Waste</td><td>30</td></tr></table>"
        ),
        "changed_header_year": (
            "<table><tr><th>Metric</th><th>FY2023</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr>"
            "<tr><td>Water use</td><td>20</td></tr>"
            "<tr><td>Waste</td><td>30</td></tr></table>"
        ),
        "changed_protected_value": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>11</td></tr>"
            "<tr><td>Water use</td><td>20</td></tr>"
            "<tr><td>Waste</td><td>30</td></tr></table>"
        ),
        "leading_blank_header_column": (
            "<table><tr><th></th><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td><td>999</td></tr>"
            "<tr><td>Water use</td><td>20</td><td>888</td></tr>"
            "<tr><td>Waste</td><td>30</td><td>777</td></tr></table>"
        ),
    }
    for case, pred_html in unsafe_records.items():
        extractor = ContentExtractor()
        segments = extractor._segments_from_markdown(
            "<!-- Page 8 | first pass -->\n\n" + markdown,
            f"doc-{case}",
        )
        table = next(
            segment for segment in segments if segment.segment_type == "table"
        )
        table.structured_data["bbox"] = [0.1, 0.1, 0.9, 0.6]
        record = {
            "table_id": f"unsafe-{case}",
            "page_number": 8,
            "bbox": [0.1, 0.1, 0.9, 0.6],
            "pred_html": pred_html,
            "structure_confidence": 0.99,
            "ocr_confidence": 0.98,
            "parse_pass": 1,
        }

        added = extractor._prefer_structured_table_records(
            segments,
            [record],
            f"doc-{case}",
        )

        preserved = next(
            segment for segment in segments if segment.segment_type == "table"
        )
        assert added == 0, case
        assert "Energy use | 10" in preserved.content, case
        assert "Water use | 20" in preserved.content, case
        assert not (preserved.structured_data or {}).get(
            "canonicalized_from_markdown"
        ), case


def _case_bboxless_structured_records_bind_by_unique_identity_not_order() -> None:
    extractor = ContentExtractor()
    revenue_table = """| Metric | FY2024 |
| --- | --- |
| Revenue | 100 |"""
    water_table = """| Metric | FY2024 |
| --- | --- |
| Water use | 20 |"""
    segments = extractor._segments_from_markdown(
        "<!-- Page 10 | first pass -->\n\n"
        + revenue_table
        + "\n\n"
        + water_table,
        "doc-record-order",
    )
    records = [
        {
            "table_id": "water-record",
            "page_number": 10,
            "reading_order": 1,
            "pred_html": (
                "<table><tr><th>Metric</th><th>FY2024</th></tr>"
                "<tr><td>Water use</td><td>20</td></tr></table>"
            ),
            "structure_confidence": 0.99,
            "ocr_confidence": 0.98,
        },
        {
            "table_id": "revenue-record",
            "page_number": 10,
            "reading_order": 2,
            "pred_html": (
                "<table><tr><th>Metric</th><th>FY2024</th></tr>"
                "<tr><td>Revenue</td><td>100</td></tr></table>"
            ),
            "structure_confidence": 0.99,
            "ocr_confidence": 0.98,
        },
    ]

    added = extractor._prefer_structured_table_records(
        segments,
        records,
        "doc-record-order",
    )

    tables = [segment for segment in segments if segment.segment_type == "table"]
    assert added == 2
    assert len(tables) == 2
    record_by_row = {
        "Revenue": next(
            (table.structured_data or {}).get("table_record_id")
            for table in tables
            if "Revenue" in table.content
        ),
        "Water use": next(
            (table.structured_data or {}).get("table_record_id")
            for table in tables
            if "Water use" in table.content
        ),
    }
    assert record_by_row == {
        "Revenue": "revenue-record",
        "Water use": "water-record",
    }


def _case_ambiguous_structured_records_do_not_bind_or_materialize_duplicates() -> None:
    extractor = ContentExtractor()
    duplicated_table = """| Metric | FY2024 |
| --- | --- |
| Energy use | 10 |"""
    segments = extractor._segments_from_markdown(
        "<!-- Page 11 | first pass -->\n\n"
        + duplicated_table
        + "\n\n"
        + duplicated_table,
        "doc-record-ambiguous",
    )
    raw_html = (
        "<table><tr><th>Metric</th><th>FY2024</th></tr>"
        "<tr><td>Energy use</td><td>10</td></tr></table>"
    )
    records = [
        {
            "table_id": "ambiguous-record-a",
            "page_number": 11,
            "reading_order": 1,
            "pred_html": raw_html,
            "structure_confidence": 0.99,
            "ocr_confidence": 0.98,
        },
        {
            "table_id": "ambiguous-record-b",
            "page_number": 11,
            "reading_order": 2,
            "pred_html": raw_html,
            "structure_confidence": 0.99,
            "ocr_confidence": 0.98,
        },
        {
            "table_id": "json-only-headcount",
            "page_number": 11,
            "reading_order": 3,
            "pred_html": (
                "<table><tr><th>Region</th><th>Headcount</th></tr>"
                "<tr><td>Asia</td><td>50 employees</td></tr></table>"
            ),
            "structure_confidence": 0.99,
            "ocr_confidence": 0.98,
        },
    ]

    added = extractor._prefer_structured_table_records(
        segments,
        records,
        "doc-record-ambiguous",
    )

    tables = [segment for segment in segments if segment.segment_type == "table"]
    assert added == 1
    assert len(tables) == 3
    original_tables = [
        table for table in tables if "Energy use" in table.content
    ]
    assert len(original_tables) == 2
    assert all(
        not (table.structured_data or {}).get("table_record_id")
        for table in original_tables
    )
    materialized = [
        table
        for table in tables
        if (table.structured_data or {}).get("materialized_from_table_record")
    ]
    assert len(materialized) == 1
    assert (materialized[0].structured_data or {}).get(
        "table_record_id"
    ) == "json-only-headcount"


def _case_disjoint_bbox_structured_record_never_binds_singleton() -> None:
    extractor = ContentExtractor()
    segments = extractor._segments_from_markdown(
        "<!-- Page 12 | first pass -->\n\n" + TABLE_MARKDOWN,
        "doc-record-disjoint-single",
    )
    table = next(segment for segment in segments if segment.segment_type == "table")
    table.structured_data["bbox"] = [0.05, 0.05, 0.40, 0.30]
    record = {
        "table_id": "disjoint-single-record",
        "page_number": 12,
        "bbox": [0.60, 0.60, 0.95, 0.90],
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Energy use</td><td>10</td></tr></table>"
        ),
        "structure_confidence": 0.99,
        "ocr_confidence": 0.98,
    }

    added = extractor._prefer_structured_table_records(
        segments,
        [record],
        "doc-record-disjoint-single",
    )

    tables = [segment for segment in segments if segment.segment_type == "table"]
    assert added == 0
    assert len(tables) == 1
    assert not (tables[0].structured_data or {}).get("table_record_id")
    assert tables[0].review_status == "needs_review"


def _case_disjoint_bbox_record_does_not_duplicate_on_multi_table_page() -> None:
    extractor = ContentExtractor()
    revenue_table = """| Metric | FY2024 |
| --- | --- |
| Revenue | 100 |"""
    water_table = """| Metric | FY2024 |
| --- | --- |
| Water use | 20 |"""
    segments = extractor._segments_from_markdown(
        "<!-- Page 13 | first pass -->\n\n"
        + revenue_table
        + "\n\n"
        + water_table,
        "doc-record-disjoint-multi",
    )
    tables = [segment for segment in segments if segment.segment_type == "table"]
    assert len(tables) == 2
    for table in tables:
        if "Revenue" in table.content:
            table.structured_data["bbox"] = [0.05, 0.05, 0.40, 0.25]
        else:
            table.structured_data["bbox"] = [0.55, 0.05, 0.95, 0.25]
    record = {
        "table_id": "disjoint-revenue-record",
        "page_number": 13,
        "bbox": [0.05, 0.65, 0.40, 0.90],
        "pred_html": (
            "<table><tr><th>Metric</th><th>FY2024</th></tr>"
            "<tr><td>Revenue</td><td>100</td></tr></table>"
        ),
        "structure_confidence": 0.99,
        "ocr_confidence": 0.98,
    }

    added = extractor._prefer_structured_table_records(
        segments,
        [record],
        "doc-record-disjoint-multi",
    )

    tables = [segment for segment in segments if segment.segment_type == "table"]
    assert added == 0
    assert len(tables) == 2
    assert all(
        not (table.structured_data or {}).get("table_record_id")
        for table in tables
    )


def _case_partial_merge_keeps_successful_pages(tmp_path: Path) -> None:
    extractor = ContentExtractor()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    page_one = tmp_path / "page-one.md"
    page_one.write_text(
        "<!-- Page 1 | PaddleOCR-VL batch 1/2 part 1 -->\n\n"
        + TABLE_MARKDOWN,
        encoding="utf-8",
    )
    page_three = tmp_path / "page-three-marker-only.md"
    page_three.write_text(
        "<!-- Page 3 | PaddleOCR-VL batch 3/3 part 1 -->",
        encoding="utf-8",
    )
    states = [
        {
            "status": "success",
            "result_json": json.dumps(
                {
                    "start_page": 1,
                    "end_page": 1,
                    "result_count": 1,
                    "batch_markdown_path": str(page_one),
                    "render_zoom": 2.0,
                }
            ),
        },
        {
            "status": "failed",
            "start_page": "2",
            "end_page": "2",
            "error": "page two failed",
        },
        {
            "status": "success",
            "result_json": json.dumps(
                {
                    "start_page": 3,
                    "end_page": 3,
                    "result_count": 1,
                    "batch_markdown_path": str(page_three),
                }
            ),
        },
    ]

    with patch.object(extractor, "_redis_hash_set"), patch(
        "esg_encoding.content_extractor.collect_table_records",
        return_value=[
            {"table_id": "success-record", "page_number": 1, "pred_html": "ok"},
            {"table_id": "failed-record", "page_number": 2, "pred_html": "stale"},
            {"table_id": "malformed-success-record", "page_number": 3, "pred_html": "stale"},
        ],
    ):
        result = extractor._merge_paddleocr_page_batch_results(
            client=object(),
            task_key="task",
            job_id="repair",
            source_path=tmp_path / "source.pdf",
            batch_states=states,
            output_dir=output_dir,
            total_pages=3,
            total_units=3,
            batch_size=1,
            expected_ranges=[(1, 1), (2, 2), (3, 3)],
            partial_result=True,
            promote_visuals=False,
            parse_pass=2,
            render_zoom=2.5,
            emit_progress=False,
        )

    assert result["processed_pages"] == [1]
    assert result["failed_pages"] == [2, 3]
    assert result["effective_render_zoom_by_page"] == {1: 2.0}
    assert [record["table_id"] for record in result["table_records"]] == [
        "success-record"
    ]
    assert "Page 1" in result["markdown"]


class TableSecondPassTests(unittest.TestCase):
    def test_candidates_exclude_clean_and_missing_ocr_note_only(self) -> None:
        _case_second_pass_candidates_exclude_clean_and_missing_ocr_note_only()

    def test_budget_uses_ceil_ratio_and_deduplicates_pages(self) -> None:
        _case_second_pass_budget_uses_ceil_ratio_and_deduplicates_pages()

    def test_prediction_options_scale_and_clamp_pixels_and_tokens(self) -> None:
        _case_second_pass_prediction_options_scale_and_clamp_pixels_and_tokens()

    def test_selective_split_renders_a_real_high_resolution_png(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _case_selective_split_renders_a_real_high_resolution_png(Path(directory))

    def test_improved_result_replaces_complete_family_and_marks_pass_two(self) -> None:
        _case_improved_second_pass_replaces_the_complete_family_and_marks_pass_two()

    def test_table_semantics_survive_artifact_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _case_table_semantics_survive_artifact_roundtrip(Path(directory))

    def test_equal_or_degraded_result_never_replaces_first_pass(self) -> None:
        _case_equal_or_degraded_second_pass_never_replaces_first_pass()

    def test_runner_passes_pages_zoom_options_and_parse_pass_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _case_runner_passes_selective_pages_zoom_options_and_parse_pass_two(
                Path(directory)
            )

    def test_unrelated_singleton_never_replaces_candidate(self) -> None:
        _case_unrelated_singleton_never_replaces_candidate()

    def test_duplicate_second_pass_segment_ids_are_rejected(self) -> None:
        _case_duplicate_second_pass_segment_ids_are_rejected()

    def test_bboxless_same_page_tables_cannot_cross_match_by_text(self) -> None:
        _case_bboxless_same_page_tables_cannot_cross_match_by_text()

    def test_bboxless_same_page_tables_match_unique_identity_across_order(self) -> None:
        _case_bboxless_same_page_tables_match_unique_identity_across_order()

    def test_second_pass_near_tie_fails_unique_match_margin(self) -> None:
        _case_second_pass_near_tie_fails_unique_match_margin()

    def test_second_pass_requires_compatible_semantic_axes(self) -> None:
        _case_second_pass_requires_compatible_semantic_axes()

    def test_json_only_record_materializes_a_table_family(self) -> None:
        _case_json_only_record_materializes_a_table_family()

    def test_record_summary_is_not_promoted_to_table_caption(self) -> None:
        _case_record_summary_is_not_promoted_to_table_caption()

    def test_identical_anonymous_records_keep_distinct_table_families(self) -> None:
        _case_identical_anonymous_records_keep_distinct_table_families()

    def test_structured_record_replaces_truncated_markdown_family(self) -> None:
        _case_structured_record_replaces_truncated_markdown_family()

    def test_less_complete_structured_record_never_deletes_markdown_rows(self) -> None:
        _case_less_complete_structured_record_never_deletes_markdown_rows()

    def test_structured_record_preserves_noncontiguous_ordered_projection(self) -> None:
        _case_structured_record_preserves_noncontiguous_ordered_projection()

    def test_structured_record_can_correct_explicit_low_confidence_row(self) -> None:
        _case_structured_record_can_correct_explicit_low_confidence_row()

    def test_structured_record_low_confidence_corrections_remain_bounded(self) -> None:
        _case_structured_record_low_confidence_corrections_remain_bounded()

    def test_structured_record_rejects_non_preserving_supersets(self) -> None:
        _case_structured_record_rejects_non_preserving_supersets()

    def test_bboxless_structured_records_bind_by_unique_identity_not_order(self) -> None:
        _case_bboxless_structured_records_bind_by_unique_identity_not_order()

    def test_ambiguous_structured_records_do_not_bind_or_materialize_duplicates(self) -> None:
        _case_ambiguous_structured_records_do_not_bind_or_materialize_duplicates()

    def test_disjoint_bbox_structured_record_never_binds_singleton(self) -> None:
        _case_disjoint_bbox_structured_record_never_binds_singleton()

    def test_disjoint_bbox_record_does_not_duplicate_on_multi_table_page(self) -> None:
        _case_disjoint_bbox_record_does_not_duplicate_on_multi_table_page()

    def test_partial_merge_keeps_successful_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _case_partial_merge_keeps_successful_pages(Path(directory))


if __name__ == "__main__":
    unittest.main()
