from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from esg_encoding.content_extractor import (
    ContentExtractor,
    _clean_non_table_markdown_block,
    _merge_native_and_ocr_page,
    _selected_page_batch_ranges,
)
from esg_encoding.exceptions import ContentExtractionError
from esg_encoding.page_parser import PageProfile, PdfAnalysis, analyze_pdf_pages
from esg_encoding.visual_assets import load_visual_manifest, promote_visual_assets


class _FakePage:
    def __init__(
        self,
        *,
        blocks: list[tuple] | None = None,
        images: list[dict] | None = None,
        drawings: int = 0,
        rotation: int = 0,
    ) -> None:
        self.rect = (0.0, 0.0, 100.0, 100.0)
        self.rotation = rotation
        self._blocks = list(blocks or [])
        self._images = list(images or [])
        self._drawings = drawings

    def get_text(self, kind: str, sort: bool = False):  # noqa: ARG002
        assert kind == "blocks"
        return list(self._blocks)

    def get_image_info(self, **_kwargs):
        return list(self._images)

    def get_cdrawings(self):
        return [{} for _ in range(self._drawings)]


class _FakeDocument:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = pages
        self.page_count = len(pages)
        self.closed = False

    def load_page(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        self.closed = True


class _FakeFitzModule:
    __name__ = "fake_fitz"
    VersionBind = "test"

    def __init__(self, pages: list[_FakePage]) -> None:
        self.document = _FakeDocument(pages)

    def open(self, _source: str) -> _FakeDocument:
        return self.document


def _text_block(text: str, bbox: tuple[float, float, float, float] = (5, 10, 95, 30)) -> tuple:
    return (*bbox, text, 0, 0)


def test_page_analyzer_routes_digital_scanned_and_mixed_pages() -> None:
    digital = _FakePage(
        blocks=[
            _text_block(
                "Employee engagement reached 82 percent in 2024 and remained stable."
            )
        ]
    )
    scanned = _FakePage(
        images=[{"xref": 2, "bbox": (0, 0, 100, 100), "width": 2000, "height": 2000}]
    )
    mixed = _FakePage(
        blocks=[
            _text_block(
                "Employee engagement reached 82 percent; the chart gives the yearly trend."
            )
        ],
        images=[{"xref": 3, "bbox": (20, 40, 80, 75), "width": 1200, "height": 700}],
    )
    backend = _FakeFitzModule([digital, scanned, mixed])

    analysis = analyze_pdf_pages("report.pdf", fitz_module=backend)

    assert analysis.available is True
    assert analysis.error is None
    assert analysis.total_pages == 3
    assert [page.page_number for page in analysis.pages] == [1, 2, 3]
    assert [page.content_kind for page in analysis.pages] == [
        "digital",
        "scanned",
        "hybrid",
    ]
    assert [page.route for page in analysis.pages] == ["native", "ocr", "hybrid"]
    assert analysis.route_counts == {"native": 1, "ocr": 1, "hybrid": 1}
    assert backend.document.closed is True


def test_selected_ocr_plan_excludes_native_pages_without_losing_global_ranges() -> None:
    # Pages 1 and 4 are native; only contiguous OCR/hybrid pages are batched.
    assert _selected_page_batch_ranges(6, 7, [2, 3, 5, 6]) == [(2, 3), (5, 6)]
    assert _selected_page_batch_ranges(6, 1, [2, 3, 5]) == [(2, 2), (3, 3), (5, 5)]


def test_all_native_analysis_skips_the_ocr_queue() -> None:
    analysis = PdfAnalysis(
        available=True,
        total_pages=2,
        pages=[
            PageProfile(
                page_number=1,
                route="native",
                content_kind="digital",
                page_width=100,
                page_height=100,
                rotation=0,
                native_markdown="Exact native text on page one.",
            ),
            PageProfile(
                page_number=2,
                route="native",
                content_kind="digital",
                page_width=100,
                page_height=100,
                rotation=0,
                native_markdown="Exact native text on page two.",
            ),
        ],
    )
    extractor = ContentExtractor()

    with patch.object(extractor, "_wake_paddleocr_vlm") as wake, patch.object(
        extractor, "_run_paddleocr_vl_page_batch_queue_active"
    ) as run_active:
        result = extractor._run_paddleocr_vl_page_batch_queue(
            Path("unused.pdf"), page_analysis=analysis
        )

    wake.assert_not_called()
    run_active.assert_not_called()
    assert result["mode"] == "native"
    assert result["native_page_count"] == 2
    assert result["ocr_page_count"] == 0
    assert result["markdown"].count("Exact native text") == 2


def test_native_and_ocr_duplicate_text_is_not_indexed_twice() -> None:
    native = "Employee engagement reached 82% in 2024."
    duplicate_ocr = "Employee engagement reached 82% in 2024."

    merged = _merge_native_and_ocr_page(native, duplicate_ocr)

    assert merged == native
    assert merged.count("82%") == 1
    assert "OCR structure supplement" not in merged

    table_supplement = """Employee engagement reached 82% in 2024.

| Metric | 2024 |
| --- | --- |
| Engagement | 82% |"""
    structured = _merge_native_and_ocr_page(native, table_supplement)
    assert structured.count("Employee engagement reached 82% in 2024.") == 1
    assert "| Engagement | 82% |" in structured
    assert "OCR structure supplement" not in structured


def test_blank_page_is_native_and_retains_an_all_native_page_marker() -> None:
    backend = _FakeFitzModule([_FakePage()])
    analysis = analyze_pdf_pages("blank.pdf", fitz_module=backend)

    assert analysis.available is True
    assert analysis.pages[0].content_kind == "blank"
    assert analysis.pages[0].route == "native"
    assert analysis.pages[0].native_markdown == ""

    extractor = ContentExtractor()
    with patch.object(extractor, "_wake_paddleocr_vlm") as wake, patch.object(
        extractor, "_run_paddleocr_vl_page_batch_queue_active"
    ) as run_active:
        result = extractor._run_paddleocr_vl_page_batch_queue(
            Path("blank.pdf"), page_analysis=analysis
        )

    wake.assert_not_called()
    run_active.assert_not_called()
    assert result["mode"] == "native"
    assert result["native_page_count"] == 1
    assert "<!-- Page 1 | adaptive parser route=native -->" in result["markdown"]


def test_native_layout_enrichment_normalizes_absolute_boxes_and_prefers_normalized_boxes() -> None:
    markdown = """<!-- Page 1 | adaptive parser route=native -->

Employee engagement reached 82 percent in 2024.

The normalized block must win over its absolute fallback."""
    extractor = ContentExtractor()
    segments = extractor._segments_from_markdown(markdown, "doc")
    analysis = PdfAnalysis(
        available=True,
        total_pages=1,
        pages=[
            PageProfile(
                page_number=1,
                route="native",
                content_kind="digital",
                page_width=100.0,
                page_height=200.0,
                rotation=0,
                native_blocks=[
                    {
                        "text": "Employee engagement reached 82 percent in 2024.",
                        "bbox": [10.0, 20.0, 60.0, 70.0],
                        "block_type": "text",
                        "reading_order": 0,
                    },
                    {
                        "text": "The normalized block must win over its absolute fallback.",
                        "bbox": [90.0, 180.0, 100.0, 200.0],
                        "normalized_bbox": [0.25, 0.4, 0.75, 0.5],
                        "block_type": "section_header",
                        "reading_order": 1,
                    },
                ],
            )
        ],
    )

    extractor._enrich_segments_from_native_layout(segments, analysis)

    by_content = {segment.content: segment for segment in segments}
    absolute = by_content["Employee engagement reached 82 percent in 2024."]
    normalized = by_content[
        "The normalized block must win over its absolute fallback."
    ]
    assert absolute.structured_data["bbox"] == [0.1, 0.1, 0.6, 0.35]
    assert absolute.position_x == 0.1
    assert absolute.position_y == 0.1
    assert absolute.structured_data["reading_order"] == 0
    assert normalized.structured_data["bbox"] == [0.25, 0.4, 0.75, 0.5]
    assert normalized.position_x == 0.25
    assert normalized.position_y == 0.4
    assert normalized.segment_type == "heading"


def test_incomplete_analysis_cannot_selectively_bypass_ocr(tmp_path: Path) -> None:
    analysis = PdfAnalysis(
        available=True,
        total_pages=2,
        pages=[
            PageProfile(
                page_number=1,
                route="native",
                content_kind="digital",
                page_width=100,
                page_height=100,
                rotation=0,
                native_markdown="Only the first page was profiled.",
            )
        ],
    )
    extractor = ContentExtractor()
    output_root = tmp_path / "outputs"
    work_root = tmp_path / "jobs"

    with patch.dict(
        os.environ,
        {
            "PADDLEOCR_OUTPUT_DIR": str(output_root),
            "PADDLEOCR_JOB_WORK_DIR": str(work_root),
        },
    ), patch.object(extractor, "_redis_client", return_value=object()), patch.object(
        extractor,
        "_split_pdf_for_page_batch_queue",
        side_effect=RuntimeError("stop-after-routing-plan"),
    ) as split:
        try:
            extractor._run_paddleocr_vl_page_batch_queue_active(
                tmp_path / "report.pdf",
                "incomplete-analysis",
                page_analysis=analysis,
            )
        except RuntimeError as exc:
            assert str(exc) == "stop-after-routing-plan"
        else:  # pragma: no cover - the sentinel must stop before Redis polling
            raise AssertionError("routing-plan sentinel was not raised")

    assert split.call_count == 1
    assert split.call_args.kwargs["page_numbers"] is None
    assert split.call_args.kwargs["page_options"] is None


def test_all_native_active_path_exposes_stage_timings(tmp_path: Path) -> None:
    analysis = PdfAnalysis(
        available=True,
        total_pages=2,
        pages=[
            PageProfile(
                page_number=1,
                route="native",
                content_kind="digital",
                page_width=100,
                page_height=100,
                rotation=0,
                native_markdown="Native page one.",
            ),
            PageProfile(
                page_number=2,
                route="native",
                content_kind="blank",
                page_width=100,
                page_height=100,
                rotation=0,
                native_markdown="",
            ),
        ],
    )
    extractor = ContentExtractor()
    batch_dir = tmp_path / "jobs" / "timed-native" / "batches"

    with patch.dict(
        os.environ,
        {
            "PADDLEOCR_OUTPUT_DIR": str(tmp_path / "outputs"),
            "PADDLEOCR_JOB_WORK_DIR": str(tmp_path / "jobs"),
        },
    ), patch.object(extractor, "_redis_client", return_value=object()), patch.object(
        extractor,
        "_split_pdf_for_page_batch_queue",
        return_value=([], 2, batch_dir),
    ) as split:
        result = extractor._run_paddleocr_vl_page_batch_queue_active(
            tmp_path / "report.pdf",
            "timed-native",
            page_analysis=analysis,
        )

    assert split.call_args.kwargs["page_numbers"] == []
    assert result["mode"] == "native"
    assert set(result["stage_timings"]) == {
        "split_seconds",
        "queue_submit_seconds",
        "ocr_queue_seconds",
        "merge_seconds",
        "queue_total_seconds",
    }
    assert all(value >= 0 for value in result["stage_timings"].values())


def _table_cell_boxes(y0: float, y1: float) -> list[list[float]]:
    middle = round((y0 + y1) / 2.0, 6)
    columns = (0.1, 0.4, 0.65, 0.9)
    return [
        [columns[column], row_start, columns[column + 1], row_end]
        for row_start, row_end in ((y0, middle), (middle, y1))
        for column in range(3)
    ]


def test_reverse_order_table_records_match_text_and_propagate_geometry_year_and_unit() -> None:
    markdown = """<!-- Page 1 | adaptive parser route=hybrid -->

| Metric | Unit | 2024 |
| --- | --- | --- |
| Employee engagement | % | 82% |

| Metric | Unit | 2023 |
| --- | --- | --- |
| Water withdrawal | m3 | 10 |"""
    extractor = ContentExtractor()
    segments = extractor._segments_from_markdown(markdown, "doc")
    engagement_html = (
        "<table><tr><th>Metric</th><th>Unit</th><th>2024</th></tr>"
        "<tr><td>Employee engagement</td><td>%</td><td>82%</td></tr></table>"
    )
    water_html = (
        "<table><tr><th>Metric</th><th>Unit</th><th>2023</th></tr>"
        "<tr><td>Water withdrawal</td><td>m3</td><td>10</td></tr></table>"
    )
    # Deliberately put Water first and give it the first reading order.  Index-
    # based binding would attach its record to the Engagement Markdown table.
    records = [
        {
            "page_number": 1,
            "table_id": "record-water",
            "block_id": "block-water",
            "block_type": "table",
            "reading_order": 1,
            "page_width": 1000,
            "page_height": 1000,
            "bbox": [0.1, 0.55, 0.9, 0.85],
            "cell_box_list": _table_cell_boxes(0.55, 0.85),
            "pred_html": water_html,
            "structure_confidence": 0.97,
            "ocr_confidence": 0.96,
        },
        {
            "page_number": 1,
            "table_id": "record-engagement",
            "block_id": "block-engagement",
            "block_type": "table",
            "reading_order": 2,
            "page_width": 1000,
            "page_height": 1000,
            "bbox": [0.1, 0.1, 0.9, 0.4],
            "cell_box_list": _table_cell_boxes(0.1, 0.4),
            "pred_html": engagement_html,
            "structure_confidence": 0.98,
            "ocr_confidence": 0.99,
        },
    ]

    extractor._enrich_table_segments_from_records(segments, records)

    tables = [segment for segment in segments if segment.segment_type == "table"]
    engagement_table = next(
        segment for segment in tables if "Employee engagement" in segment.content
    )
    water_table = next(segment for segment in tables if "Water withdrawal" in segment.content)
    assert engagement_table.structured_data["table_record_id"] == "record-engagement"
    assert water_table.structured_data["table_record_id"] == "record-water"
    assert engagement_table.structured_data["bbox"] == [0.1, 0.1, 0.9, 0.4]
    assert water_table.structured_data["bbox"] == [0.1, 0.55, 0.9, 0.85]
    assert {
        table.structured_data["table_record_id"] for table in tables
    } == {"record-engagement", "record-water"}

    engagement_value = next(
        segment
        for segment in segments
        if segment.source_table_id == engagement_table.source_table_id
        and segment.segment_type == "table_cell"
        and segment.col_header == "2024"
    )
    water_value = next(
        segment
        for segment in segments
        if segment.source_table_id == water_table.source_table_id
        and segment.segment_type == "table_cell"
        and segment.col_header == "2023"
    )
    assert engagement_value.structured_data["bbox"] == [0.65, 0.25, 0.9, 0.4]
    assert engagement_value.structured_data["year"] == 2024
    assert engagement_value.structured_data["unit"] == "%"
    assert engagement_value.unit == "%"
    assert water_value.structured_data["bbox"] == [0.65, 0.7, 0.9, 0.85]
    assert water_value.structured_data["year"] == 2023
    assert water_value.structured_data["unit"] == "m3"
    assert water_value.unit == "m3"


def test_missing_batch_markdown_path_raises_domain_error(tmp_path: Path) -> None:
    state = {
        "status": "success",
        "result_json": {
            "start_page": 1,
            "end_page": 1,
            "result_count": 1,
            "batch_markdown_path": "",
        },
    }
    extractor = ContentExtractor()

    try:
        extractor._merge_paddleocr_page_batch_results(
            client=object(),
            task_key="paddleocr:task:missing-path",
            job_id="missing-path",
            source_path=tmp_path / "source.pdf",
            batch_states=[state],
            output_dir=tmp_path / "output",
            total_pages=1,
            total_units=1,
            batch_size=1,
        )
    except ContentExtractionError as exc:
        assert "batch" in str(exc).casefold()
    except IsADirectoryError as exc:  # pragma: no cover - explicit regression guard
        raise AssertionError("an empty worker path was treated as the current directory") from exc
    else:
        raise AssertionError("missing batch Markdown path was accepted")


def test_empty_visual_is_not_converted_into_an_embedding_segment() -> None:
    empty_visual = {
        "asset_id": "va_empty000000000000000",
        "page_number": 1,
        "caption": "",
        "summary": "",
        "ocr_text": "",
        "chart_data": None,
        "confidence": 0.5,
    }
    meaningful_visual = {
        "asset_id": "va_chart000000000000000",
        "page_number": 1,
        "caption": "Employee engagement by year",
        "summary": "",
        "ocr_text": "",
        "chart_data": None,
        "confidence": 0.9,
    }
    markdown = "\n\n".join(
        [
            "<!-- Page 1 | adaptive parser route=hybrid -->",
            f"<!-- visual-asset: {json.dumps(empty_visual)} -->",
            f"<!-- visual-asset: {json.dumps(meaningful_visual)} -->",
        ]
    )

    segments = ContentExtractor()._segments_from_markdown(markdown, "doc")
    embedding_inputs = [segment.content for segment in segments]

    assert len(segments) == 1
    assert segments[0].segment_type == "figure"
    assert segments[0].structured_data["asset_id"] == meaningful_visual["asset_id"]
    assert all(empty_visual["asset_id"] not in text for text in embedding_inputs)
    assert all("Visual evidence" not in text for text in embedding_inputs)


def test_html_image_noise_is_removed_but_surrounding_disclosure_is_kept() -> None:
    image_only = (
        '<div style="text-align:center"><img src="imgs/cover.jpg" '
        'alt="Image" width="64%" /></div>'
    )
    with_disclosure = (
        '<div>Employee engagement reached 82% in 2024. '
        '<img src="imgs/chart.png" alt="Image" /></div>'
    )

    assert _clean_non_table_markdown_block(image_only) == ""
    assert _clean_non_table_markdown_block(with_disclosure) == (
        "Employee engagement reached 82% in 2024."
    )

    markdown = "\n\n".join(
        [
            "<!-- Page 1 | adaptive parser route=hybrid -->",
            "**P001_S000**",
            image_only,
            "Visual evidence va_0123456789abcdef0123",
            with_disclosure,
        ]
    )
    segments = ContentExtractor()._segments_from_markdown(markdown, "doc")
    contents = [segment.content for segment in segments]

    assert contents == ["Employee engagement reached 82% in 2024."]
    assert all("<img" not in content and "<div" not in content for content in contents)
    assert all("imgs/" not in content for content in contents)


def test_visual_manifest_uses_global_page_directory_not_batch_local_page_index(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "worker-output"
    page_dir = (
        output_root
        / "batches"
        / "batch_0002_pages_0008_0014"
        / "page_0010_part_03"
    )
    image_dir = page_dir / "imgs"
    image_dir.mkdir(parents=True)
    image_name = "img_in_chart_box_80_100_720_900.png"
    (image_dir / image_name).write_bytes(b"\x89PNG\r\n\x1a\n")
    payload = {
        # Paddle's page index is local to the split PDF and must not become page 3.
        "page_index": 2,
        "input_img_shape": [1000, 800, 3],
        "parsing_res_list": [
            {
                "block_id": "chart-1",
                "block_label": "chart",
                "block_order": 4,
                "block_bbox": [80, 100, 720, 900],
                "block_content": f'<img src="imgs/{image_name}" />',
                "caption": "Employee engagement by year",
            }
        ],
        "table_res_list": [
            {
                "block_id": "table-1",
                "block_bbox": [80, 100, 720, 900],
                "pred_html": "<table><tr><th>Year</th><th>Rate</th></tr>"
                "<tr><td>2024</td><td>82%</td></tr></table>",
                "rec_scores": [0.98, 0.97],
            }
        ],
    }
    (page_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    assets = promote_visual_assets(output_root, pdf_path)
    manifest = load_visual_manifest(pdf_path)

    assert len(assets) == 1
    assert assets[0]["page_number"] == 10
    assert assets[0]["source_page_index"] == 2
    assert assets[0]["batch_start_page"] == 8
    assert assets[0]["batch_end_page"] == 14
    assert assets[0]["bbox"] == [0.1, 0.1, 0.9, 0.9]
    assert manifest is not None
    assert manifest["version"] == 4
    assert manifest["pages"][0]["page_number"] == 10
    assert manifest["blocks"][0]["page_number"] == 10
    assert manifest["tables"][0]["page_number"] == 10
    assert manifest["tables"][0]["source_page_index"] == 2


class AdaptiveContentPipelineRegressionTests(unittest.TestCase):
    """Stdlib entry points keep this suite runnable in the backend image."""

    def test_page_analyzer_routes_digital_scanned_and_mixed_pages(self) -> None:
        test_page_analyzer_routes_digital_scanned_and_mixed_pages()

    def test_selected_ocr_plan_excludes_native_pages_without_losing_global_ranges(self) -> None:
        test_selected_ocr_plan_excludes_native_pages_without_losing_global_ranges()

    def test_all_native_analysis_skips_the_ocr_queue(self) -> None:
        test_all_native_analysis_skips_the_ocr_queue()

    def test_native_and_ocr_duplicate_text_is_not_indexed_twice(self) -> None:
        test_native_and_ocr_duplicate_text_is_not_indexed_twice()

    def test_blank_page_is_native_and_retains_an_all_native_page_marker(self) -> None:
        test_blank_page_is_native_and_retains_an_all_native_page_marker()

    def test_native_layout_enrichment_normalizes_absolute_boxes_and_prefers_normalized_boxes(self) -> None:
        test_native_layout_enrichment_normalizes_absolute_boxes_and_prefers_normalized_boxes()

    def test_incomplete_analysis_cannot_selectively_bypass_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_incomplete_analysis_cannot_selectively_bypass_ocr(Path(temp_dir))

    def test_all_native_active_path_exposes_stage_timings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_all_native_active_path_exposes_stage_timings(Path(temp_dir))

    def test_reverse_order_table_records_match_text_and_propagate_geometry_year_and_unit(self) -> None:
        test_reverse_order_table_records_match_text_and_propagate_geometry_year_and_unit()

    def test_missing_batch_markdown_path_raises_domain_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_missing_batch_markdown_path_raises_domain_error(Path(temp_dir))

    def test_empty_visual_is_not_converted_into_an_embedding_segment(self) -> None:
        test_empty_visual_is_not_converted_into_an_embedding_segment()

    def test_html_image_noise_is_removed_but_surrounding_disclosure_is_kept(self) -> None:
        test_html_image_noise_is_removed_but_surrounding_disclosure_is_kept()

    def test_visual_manifest_uses_global_page_directory_not_batch_local_page_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_visual_manifest_uses_global_page_directory_not_batch_local_page_index(
                Path(temp_dir)
            )


if __name__ == "__main__":
    unittest.main()
