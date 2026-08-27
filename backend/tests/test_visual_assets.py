import json
import unittest
from pathlib import Path

from esg_encoding.models import ProcessingConfig
from esg_encoding.visual_assets import (
    append_visual_markers,
    normalize_bbox,
    promote_visual_assets,
    load_visual_manifest,
    safe_asset_path,
)


def test_normalize_bbox_supports_polygon_and_clamps():
    assert normalize_bbox([[10, 20], [110, 20], [110, 220]], 200, 400) == [0.05, 0.05, 0.55, 0.55]
    assert normalize_bbox([-1, 0, 4, 2]) == [0.0, 0.0, 1.0, 1.0]


def test_paddleocr_v16_layout_keeps_global_page_geometry_and_table_link(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PADDLEOCR_KEEP_LAYOUT_AUDIT", "true")
    worker = tmp_path / "worker"
    output = (
        worker / "job_a" / "batches" / "batch_0016_pages_0106_0112"
        / "page_0108_part_03"
    )
    images = output / "imgs"
    images.mkdir(parents=True)
    (images / "img_in_chart_box_10_20_110_220.jpg").write_bytes(b"not-a-real-png-but-stable")
    (images / "img_in_table_box_20_250_180_370.jpg").write_bytes(b"not-a-real-table-png")
    # Unreferenced copies and Paddle visualisation outputs are not evidence.
    (images / "chart-copy.png").write_bytes(b"not-a-real-png-but-stable")
    (output / "report_layout_det_res.png").write_bytes(b"diagnostic")
    table_html = "<table><tr><th>Metric</th><th>FY24</th></tr><tr><td>Energy</td><td>12</td></tr></table>"
    (output / "report_res.json").write_text(json.dumps({
        "input_path": "/workspace/uploads/job_a/batch_0016.pdf",
        # This is local to the seven-page batch and must not become page 3.
        "page_index": 2,
        "page_count": 7,
        "width": 200,
        "height": 400,
        "parsing_res_list": [
            {
                "block_label": "chart", "block_bbox": [10, 20, 110, 220],
                "block_id": 7, "block_order": 5,
                # v1.6 JSON omits the PIL image path; the saved crop filename
                # carries the label and bbox used by the adapter.
                "block_content": "Emissions",
                "chart_data": {"2024": 12}, "score": 0.91,
            },
            {
                "block_label": "figure_title", "block_bbox": [10, 222, 110, 240],
                "block_id": 8, "block_order": 6,
                "block_content": "Scope 1 emissions",
            },
            {
                "block_label": "table", "block_bbox": [20, 250, 180, 370],
                "block_id": 9, "block_order": 7, "block_content": table_html,
            },
        ],
        "table_res_list": [{
            "pred_html": table_html,
            "block_bbox": [20, 250, 180, 370],
            "structure_score": 0.93,
            "table_ocr_pred": {"rec_scores": [0.98, 0.96, 0.95, 0.94]},
        }],
    }), encoding="utf-8")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"pdf")

    assets = promote_visual_assets(worker, pdf)
    assert len(assets) == 2
    chart = next(asset for asset in assets if asset["block_type"] == "chart")
    table_asset = next(asset for asset in assets if asset["block_type"] == "table")
    assert chart["page_number"] == 108
    assert chart["source_page_index"] == 2
    assert chart["page_width"] == 200
    assert chart["page_height"] == 400
    assert chart["bbox"] == [0.05, 0.05, 0.55, 0.55]
    assert chart["reading_order"] == 5
    assert chart["caption"] == "Scope 1 emissions"
    assert chart["chart_data"] == {"2024": 12}
    assert chart["searchable"] is True
    assert chart["batch_id"] == "batch_0016"
    assert chart["source_json_path"].endswith("page_0108_part_03/report_res.json")
    resolved = safe_asset_path(pdf, chart["asset_id"])
    assert resolved and resolved[0].read_bytes() == b"not-a-real-png-but-stable"
    assert safe_asset_path(pdf, "../../report.pdf") is None
    manifest = json.loads((tmp_path / "report_visual_assets" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 4
    assert manifest["parser_schema"] == "paddleocr-vl-v1.6"
    assert "layout_records" not in manifest
    assert (tmp_path / "report_visual_assets" / manifest["layout_audit"]).is_file()
    assert manifest["pages"][0]["page_number"] == 108
    assert manifest["pages"][0]["source_page_index"] == 2
    assert manifest["blocks"][0]["block_type"] == "chart"
    assert len(manifest["tables"]) == 1
    table = manifest["tables"][0]
    assert table["page_number"] == 108
    assert table["bbox"] == [0.1, 0.625, 0.9, 0.925]
    assert table["reading_order"] == 7
    assert table["structure_confidence"] == 0.93
    assert table["ocr_confidence"] == 0.94
    assert table["asset_ids"] == [table_asset["asset_id"]]
    assert table_asset["table_ids"] == [table["table_id"]]


def test_real_v16_res_wrapper_assigns_layout_scores_and_footnotes_one_to_one(tmp_path: Path):
    worker = tmp_path / "worker"
    output = (
        worker / "job" / "batches" / "batch_0003_pages_0021_0024"
        / "page_0023_part_03"
    )
    images = output / "imgs"
    images.mkdir(parents=True)
    (images / "panel_a.png").write_bytes(b"first-visual")
    (images / "panel_b.png").write_bytes(b"second-visual")

    result = {
        "request_id": "paddle-serving-envelope",
        "res": {
            "input_path": "/workspace/worker/batch_0003.pdf",
            # Paddle reports a zero-based page within the split PDF. The page
            # directory remains the source of truth for the report-wide page.
            "page_index": 2,
            "page_count": 4,
            "layout_det_res": {
                "input_img_shape": [400, 200, 3],
                "boxes": [
                    {"label": "image", "coordinate": [10, 20, 90, 140], "score": 0.97},
                    {"label": "image", "coordinate": [110, 200, 190, 320], "score": 0.88},
                ],
            },
            "parsing_res_list": [
                {
                    "block_label": "image",
                    "block_bbox": [10, 20, 90, 140],
                    "block_id": 101,
                    "block_order": 17,
                    "block_content": "![](imgs/panel_a.png)",
                },
                {
                    "block_label": "vision_footnote",
                    "block_bbox": [10, 142, 90, 160],
                    "block_id": 102,
                    "block_order": 18,
                    "block_content": "Water withdrawals by year",
                },
                {
                    "block_label": "image",
                    "block_bbox": [110, 200, 190, 320],
                    "block_id": 103,
                    "block_order": 31,
                    "block_content": "![](imgs/panel_b.png)",
                },
                {
                    "block_label": "vision_footnote",
                    "block_bbox": [110, 322, 190, 340],
                    "block_id": 104,
                    "block_order": 32,
                    "block_content": "Waste recycled by year",
                },
            ],
        },
    }
    (output / "report_res.json").write_text(
        json.dumps(result), encoding="utf-8"
    )
    # Multiple result files for the same physical page must not create
    # duplicate page entries in the durable manifest.
    (output / "report_metadata.json").write_text(json.dumps({
        "res": {
            "input_path": "/workspace/worker/batch_0003.pdf",
            "page_index": 2,
            "page_count": 4,
            "layout_det_res": {"input_img_shape": [400, 200, 3], "boxes": []},
        },
    }), encoding="utf-8")
    pdf = tmp_path / "wrapped.pdf"
    pdf.write_bytes(b"pdf")

    assets = promote_visual_assets(worker, pdf)

    assert len(assets) == 2
    by_source = {Path(asset["source_image_path"]).name: asset for asset in assets}
    first = by_source["panel_a.png"]
    second = by_source["panel_b.png"]
    assert first["page_number"] == second["page_number"] == 23
    assert first["source_page_index"] == second["source_page_index"] == 2
    assert first["page_width"] == second["page_width"] == 200
    assert first["page_height"] == second["page_height"] == 400
    assert first["caption"] == "Water withdrawals by year"
    assert second["caption"] == "Waste recycled by year"
    assert first["confidence"] == 0.97
    assert second["confidence"] == 0.88
    # Canonical reading order follows parsing_res_list; Paddle's order is
    # retained separately on the manifest block for auditability.
    assert first["reading_order"] == 0
    assert second["reading_order"] == 2

    manifest = json.loads(
        (tmp_path / "wrapped_visual_assets" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["pages"]) == 1
    page = manifest["pages"][0]
    assert page["page_number"] == 23
    assert page["source_page_index"] == 2
    assert page["batch_page_count"] == 4
    assert page["batch_id"] == "batch_0003"
    assert page["batch_start_page"] == 21
    assert page["batch_end_page"] == 24
    visual_blocks = [block for block in manifest["blocks"] if block["block_type"] == "image"]
    assert [block["source_block_order"] for block in visual_blocks] == [17, 31]


def test_same_basename_on_different_pages_never_cross_links(tmp_path: Path):
    worker = tmp_path / "worker"
    for page, caption, bbox in (
        (8, "Water use", [10, 10, 90, 90]),
        (9, "Waste generated", [20, 20, 180, 180]),
    ):
        output = worker / "job" / "batches" / "batch_0002_pages_0008_0009" / f"page_{page:04d}_part_0{page - 7}"
        images = output / "imgs"
        images.mkdir(parents=True)
        # Identical bytes are stored once, but both page occurrences survive.
        (images / "chart.png").write_bytes(b"same-chart-bytes")
        (output / "result.json").write_text(json.dumps({
            "page_index": page - 8, "width": 200, "height": 200,
            "parsing_res_list": [{
                "block_label": "chart", "block_bbox": bbox, "block_order": 3,
                "block_content": f"![{caption}](imgs/chart.png)",
            }],
        }), encoding="utf-8")
    pdf = tmp_path / "two-pages.pdf"
    pdf.write_bytes(b"pdf")

    assets = promote_visual_assets(worker, pdf)

    assert len(assets) == 2
    assert len({asset["asset_id"] for asset in assets}) == 2
    assert len({asset["relative_path"] for asset in assets}) == 1
    by_page = {asset["page_number"]: asset for asset in assets}
    assert by_page[8]["ocr_text"] == "Water use"
    assert by_page[8]["bbox"] == [0.05, 0.05, 0.45, 0.45]
    assert by_page[9]["ocr_text"] == "Waste generated"
    assert by_page[9]["bbox"] == [0.1, 0.1, 0.9, 0.9]


def test_decorative_diagnostics_and_html_placeholders_are_filtered(tmp_path: Path):
    worker = tmp_path / "worker"
    output = worker / "page_0001_part_01"
    output.mkdir(parents=True)
    for name in ("logo.png", "placeholder.png", "document_layout_order_res.png"):
        (output / name).write_bytes(name.encode())
    (output / "result.json").write_text(json.dumps({
        "page_index": 0, "width": 100, "height": 100,
        "parsing_res_list": [
            {
                "block_label": "header_image", "block_bbox": [0, 0, 20, 10],
                "block_content": "![](logo.png)",
            },
            {
                "block_label": "text", "block_bbox": [0, 10, 100, 30],
                "block_content": '<div><img src="placeholder.png"></div>',
            },
        ],
    }), encoding="utf-8")
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"pdf")

    assert promote_visual_assets(worker, pdf) == []
    manifest = json.loads((tmp_path / "empty_visual_assets" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["assets"] == []


def test_image_label_does_not_override_logo_and_repeated_page_decoration_filters(tmp_path: Path):
    worker = tmp_path / "worker"
    for page in (1, 2, 3):
        output = (
            worker / "job" / "batches" / "batch_0001_pages_0001_0003"
            / f"page_{page:04d}_part_{page:02d}"
        )
        images = output / "imgs"
        images.mkdir(parents=True)
        # A repeated, thin top-of-page image is a decorative band even when
        # Paddle labels it as a regular image and extracts meaningful alt text.
        (images / "top_band.png").write_bytes(b"same-decoration-on-every-page")
        blocks = [{
            "block_label": "image",
            "block_bbox": [0, 0, 200, 20],
            "block_content": "![Company navigation band](imgs/top_band.png)",
        }]
        if page == 1:
            # Filename-level logo filtering must also win over a generic image
            # layout label.
            (images / "company_logo.png").write_bytes(b"logo")
            blocks.append({
                "block_label": "image",
                "block_bbox": [5, 25, 35, 45],
                "block_content": "![Company logo](imgs/company_logo.png)",
            })
        (output / "result.json").write_text(json.dumps({
            "res": {
                "page_index": page - 1,
                "page_count": 3,
                "width": 200,
                "height": 200,
                "parsing_res_list": blocks,
            },
        }), encoding="utf-8")
    pdf = tmp_path / "decorations.pdf"
    pdf.write_bytes(b"pdf")

    assert promote_visual_assets(worker, pdf) == []
    manifest = json.loads(
        (tmp_path / "decorations_visual_assets" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["assets"] == []
    assert [page["page_number"] for page in manifest["pages"]] == [1, 2, 3]


def test_layout_audit_is_omitted_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PADDLEOCR_KEEP_LAYOUT_AUDIT", raising=False)
    output = tmp_path / "worker" / "page_0001"
    output.mkdir(parents=True)
    (output / "layout.json").write_text('{"type":"text","text":"hello"}', encoding="utf-8")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"pdf")

    promote_visual_assets(tmp_path / "worker", pdf)

    destination = tmp_path / "report_visual_assets"
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert "layout_audit" not in manifest
    assert not (destination / "layout_audit.json").exists()


def test_manifest_cache_reuses_and_invalidates_by_mtime(tmp_path: Path):
    pdf = tmp_path / "cached.pdf"
    pdf.write_bytes(b"pdf")
    root = tmp_path / "cached_visual_assets"
    root.mkdir()
    path = root / "manifest.json"
    path.write_text('{"version":2,"assets":[]}', encoding="utf-8")
    first = load_visual_manifest(pdf)
    second = load_visual_manifest(pdf)
    assert first is second
    path.write_text('{"version":2,"assets":[{"asset_id":"new"}]}', encoding="utf-8")
    third = load_visual_manifest(pdf)
    assert third is not first
    assert third["assets"][0]["asset_id"] == "new"


def test_visual_marker_creates_searchable_segment(tmp_path: Path):
    from esg_encoding.content_extractor import ContentExtractor

    asset = {
        "asset_id": "va_0123456789abcdef0123", "relative_path": "x.png",
        "mime_type": "image/png", "page_number": 3, "bbox": [0.1, 0.2, 0.8, 0.9],
        "caption": "Scope 1 emissions", "summary": "Emissions fell year over year",
        "ocr_text": "2023 14; 2024 12", "chart_data": {"2023": 14, "2024": 12},
        "confidence": 0.9, "parser_version": "paddleocr-vl-v1.6",
    }
    markdown = append_visual_markers("<!-- Page 1 -->\nText", [asset])
    segments = ContentExtractor(ProcessingConfig())._segments_from_markdown(markdown, "doc_test")
    visual = next(segment for segment in segments if segment.segment_type == "chart")
    assert visual.page_number == 3
    assert "Scope 1 emissions" in visual.content
    assert visual.structured_data["asset_id"] == asset["asset_id"]


def test_empty_visual_asset_does_not_create_embedding_marker():
    empty_asset = {
        "asset_id": "va_0123456789abcdef0123",
        "relative_path": "blob.png",
        "mime_type": "image/png",
        "page_number": 4,
        "block_type": "figure",
        "caption": "",
        "summary": "",
        "ocr_text": "<div><img src='placeholder.png'></div>",
        "chart_data": None,
        "searchable": False,
    }

    assert append_visual_markers("Report text", [empty_asset]) == "Report text"


def test_generic_visual_placeholders_do_not_create_embedding_markers():
    for placeholder in ("Figure", "Figure 12", "image", "img_04.png"):
        asset = {
            "asset_id": "va_0123456789abcdef0123",
            "relative_path": "blob.png",
            "mime_type": "image/png",
            "page_number": 4,
            "block_type": "image",
            "caption": placeholder,
            "summary": "",
            "ocr_text": placeholder,
            "chart_data": None,
        }

        assert append_visual_markers("Report text", [asset]) == "Report text"


def test_visual_promotion_does_not_follow_image_symlinks(tmp_path: Path):
    worker = tmp_path / "worker"
    output = worker / "page_0001_part_01"
    images = output / "imgs"
    images.mkdir(parents=True)
    external = tmp_path / "outside-chart.png"
    external.write_bytes(b"must-not-be-copied")
    linked = images / "linked-chart.png"
    try:
        linked.symlink_to(external)
    except (NotImplementedError, OSError):
        raise unittest.SkipTest("Creating symlinks is unavailable for this Windows test account")
    (output / "result.json").write_text(json.dumps({
        "page_index": 0,
        "width": 100,
        "height": 100,
        "parsing_res_list": [{
            "block_label": "chart",
            "block_bbox": [10, 10, 90, 90],
            "block_content": "![Energy use](imgs/linked-chart.png)",
        }],
    }), encoding="utf-8")
    pdf = tmp_path / "symlink.pdf"
    pdf.write_bytes(b"pdf")

    assert promote_visual_assets(worker, pdf) == []
    manifest = json.loads(
        (tmp_path / "symlink_visual_assets" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["assets"] == []
