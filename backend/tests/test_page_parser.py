from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from esg_encoding.page_parser import (
    PageParserConfig,
    analyze_pdf_page,
    analyze_pdf_pages,
    render_native_markdown,
)


class FakeRect:
    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1


class FakePage:
    def __init__(
        self,
        *,
        blocks=None,
        images=None,
        drawings=None,
        rect=(0, 0, 600, 800),
        rotation=0,
    ):
        self.rect = FakeRect(*rect)
        self.rotation = rotation
        self._blocks = list(blocks or [])
        self._images = list(images or [])
        self._drawings = list(drawings or [])

    def get_text(self, mode, sort=False):  # noqa: ARG002
        if mode != "blocks":
            raise AssertionError(f"unexpected text mode: {mode}")
        return self._blocks

    def get_image_info(self, hashes=False, xrefs=True):  # noqa: ARG002
        return self._images

    def get_cdrawings(self):
        return self._drawings


class FakeDocument:
    def __init__(self, pages, *, failing_pages=None, needs_pass=False, password="secret"):
        self._pages = list(pages)
        self._failing_pages = set(failing_pages or [])
        self.page_count = len(self._pages)
        self.needs_pass = needs_pass
        self._password = password
        self.closed = False

    def authenticate(self, password):
        return password == self._password

    def load_page(self, page_index):
        if page_index in self._failing_pages:
            raise RuntimeError("damaged page")
        return self._pages[page_index]

    def close(self):
        self.closed = True


class FakeFitz:
    __name__ = "fake_fitz"
    VersionBind = "1.99-test"

    def __init__(self, document=None, error=None):
        self.document = document
        self.error = error

    def open(self, *args, **kwargs):  # noqa: ARG002
        if self.error:
            raise self.error
        return self.document


def text_block(x0, y0, x1, y1, text, number=0):
    return (x0, y0, x1, y1, text, number, 0)


def image_info(x0, y0, x1, y1, xref=1, width=1200, height=1600):
    return {
        "bbox": (x0, y0, x1, y1),
        "xref": xref,
        "width": width,
        "height": height,
    }


class PageProfileTests(unittest.TestCase):
    def test_simple_born_digital_page_uses_native_text_and_coordinates(self):
        page = FakePage(
            blocks=[
                text_block(
                    60,
                    100,
                    540,
                    160,
                    "Employee engagement was 87% in 2025, compared with 84% in 2024.",
                    7,
                )
            ]
        )

        profile = analyze_pdf_page(page, 3)

        self.assertEqual(profile.page_number, 3)
        self.assertEqual(profile.content_kind, "digital")
        self.assertEqual(profile.classification, "digital")
        self.assertEqual(profile.route, "native")
        self.assertEqual(profile.page_size, {"width": 600.0, "height": 800.0})
        self.assertEqual(profile.native_blocks[0]["block_type"], "text")
        self.assertEqual(profile.native_blocks[0]["reading_order"], 0)
        self.assertEqual(profile.native_blocks[0]["bbox"], [60.0, 100.0, 540.0, 160.0])
        self.assertEqual(profile.native_blocks[0]["normalized_bbox"], [0.1, 0.125, 0.9, 0.2])
        self.assertIn("87%", profile.native_markdown)
        self.assertGreater(profile.text_area_ratio, 0)
        self.assertEqual(profile.image_area_ratio, 0)

    def test_image_only_page_is_scanned_and_requests_ocr(self):
        page = FakePage(images=[image_info(0, 0, 600, 800)])

        profile = analyze_pdf_page(page, 1)

        self.assertEqual(profile.content_kind, "scanned")
        self.assertEqual(profile.route, "ocr")
        self.assertEqual(profile.native_markdown, "")
        self.assertEqual(profile.image_area_ratio, 1.0)
        self.assertTrue(profile.complexity_hints["image_dominant"])
        self.assertTrue(profile.complexity_hints["needs_high_resolution_ocr"])

    def test_mixed_multicolumn_page_uses_hybrid_route(self):
        page = FakePage(
            blocks=[
                text_block(40, 100, 270, 250, "Left column sustainability narrative with enough native text.", 1),
                text_block(330, 105, 560, 260, "Right column governance narrative with enough native text.", 2),
            ],
            images=[image_info(60, 500, 540, 700)],
        )

        profile = analyze_pdf_page(page, 2)

        self.assertEqual(profile.content_kind, "hybrid")
        self.assertEqual(profile.route, "hybrid")
        self.assertEqual(profile.column_count, 2)
        self.assertEqual([block["column_index"] for block in profile.native_blocks], [1, 2])
        self.assertTrue(profile.complexity_hints["multi_column"])
        self.assertTrue(profile.complexity_hints["complex_layout"])

    def test_multicolumn_reading_order_finishes_left_column_before_right(self):
        page = FakePage(
            blocks=[
                text_block(40, 20, 560, 50, "Sustainability report section heading", 1),
                text_block(40, 100, 270, 150, "LEFT-FIRST narrative content", 2),
                text_block(330, 105, 560, 155, "RIGHT-FIRST narrative content", 3),
                text_block(40, 300, 270, 350, "LEFT-SECOND narrative content", 4),
                text_block(330, 305, 560, 355, "RIGHT-SECOND narrative content", 5),
            ]
        )

        profile = analyze_pdf_page(page, 4)

        self.assertEqual(profile.column_count, 2)
        markdown = profile.native_markdown
        self.assertLess(markdown.index("LEFT-FIRST"), markdown.index("LEFT-SECOND"))
        self.assertLess(markdown.index("LEFT-SECOND"), markdown.index("RIGHT-FIRST"))
        self.assertLess(markdown.index("RIGHT-FIRST"), markdown.index("RIGHT-SECOND"))

    def test_vector_table_and_rotated_pages_are_not_misrouted_as_simple_native(self):
        table_page = FakePage(
            blocks=[text_block(40, 100, 560, 400, "Metric 2024 2025 employee engagement 84% 87%", 1)],
            drawings=[{} for _ in range(8)],
        )
        rotated_page = FakePage(
            blocks=[text_block(40, 100, 560, 400, "This is a long born digital paragraph on a landscape page.", 1)],
            rotation=90,
        )

        table_profile = analyze_pdf_page(table_page, 1)
        rotated_profile = analyze_pdf_page(rotated_page, 2)

        self.assertEqual(table_profile.content_kind, "digital")
        self.assertEqual(table_profile.route, "hybrid")
        self.assertTrue(table_profile.complexity_hints["possible_table"])
        self.assertEqual(rotated_profile.route, "hybrid")
        self.assertEqual(rotated_profile.rotation, 90)
        self.assertTrue(rotated_profile.complexity_hints["needs_orientation"])

    def test_numeric_borderless_table_is_flagged_without_vector_drawings(self):
        page = FakePage(
            blocks=[
                text_block(
                    40,
                    100,
                    560,
                    400,
                    "Engagement 2023 81%\nEngagement 2024 84%\nEngagement 2025 87%",
                    1,
                )
            ]
        )

        profile = analyze_pdf_page(page, 1)

        self.assertTrue(profile.complexity_hints["possible_table"])
        self.assertEqual(profile.route, "hybrid")

    def test_image_overlap_is_not_double_counted(self):
        page = FakePage(
            images=[
                image_info(0, 0, 600, 800, xref=1),
                image_info(0, 0, 600, 800, xref=2),
            ]
        )

        profile = analyze_pdf_page(page, 1)

        self.assertEqual(profile.image_count, 2)
        self.assertEqual(profile.image_area_ratio, 1.0)

    def test_html_and_image_placeholders_are_removed_from_native_markdown(self):
        blocks = [
            {
                "reading_order": 0,
                "text": '<img src="logo.png"><div>Employee engagement 87%</div>',
                "usable": True,
            },
            {
                "reading_order": 1,
                "text": "![decorative](background.png)",
                "usable": True,
            },
        ]

        markdown = render_native_markdown(blocks)

        self.assertEqual(markdown, "Employee engagement 87%")
        self.assertNotIn("<img", markdown)
        self.assertNotIn("![", markdown)

    def test_invalid_page_number_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "1-based"):
            analyze_pdf_page(FakePage(), 0)

    def test_native_extraction_error_is_observable_and_falls_back_to_ocr(self):
        page = FakePage()
        page.get_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken text layer"))

        profile = analyze_pdf_page(page, 1)

        self.assertEqual(profile.route, "ocr")
        self.assertIn("native_text_extraction_failed", profile.warnings)


class PdfAnalysisTests(unittest.TestCase):
    def test_document_analysis_exposes_routes_and_closes_document(self):
        digital = FakePage(
            blocks=[text_block(40, 100, 560, 200, "A sufficiently long digital disclosure paragraph for extraction.")]
        )
        scanned = FakePage(images=[image_info(0, 0, 600, 800)])
        document = FakeDocument([digital, scanned])

        analysis = analyze_pdf_pages("report.pdf", fitz_module=FakeFitz(document))

        self.assertTrue(analysis.available)
        self.assertIsNone(analysis.error)
        self.assertEqual(analysis.total_pages, 2)
        self.assertEqual(analysis.route_counts, {"native": 1, "ocr": 1, "hybrid": 0})
        self.assertEqual(analysis.backend, "fake_fitz")
        self.assertTrue(document.closed)
        payload = analysis.to_dict()
        self.assertEqual(payload["pages"][0]["page_number"], 1)
        self.assertEqual(payload["route_counts"]["ocr"], 1)

    def test_one_bad_page_degrades_to_ocr_without_losing_other_pages(self):
        digital = FakePage(
            blocks=[text_block(40, 100, 560, 200, "A sufficiently long digital disclosure paragraph for extraction.")]
        )
        document = FakeDocument([digital, digital], failing_pages={1})

        analysis = analyze_pdf_pages("report.pdf", fitz_module=FakeFitz(document))

        self.assertTrue(analysis.available)
        self.assertIsNone(analysis.error)
        self.assertEqual(len(analysis.pages), 2)
        self.assertEqual(analysis.pages[0].route, "native")
        self.assertEqual(analysis.pages[1].route, "ocr")
        self.assertIn("damaged page", analysis.pages[1].error)
        self.assertEqual(analysis.warnings, ["native_page_analysis_failed:1"])
        self.assertTrue(document.closed)

    def test_missing_pymupdf_is_a_structured_unavailable_result(self):
        with patch(
            "esg_encoding.page_parser.importlib.import_module",
            side_effect=ModuleNotFoundError("not installed"),
        ):
            analysis = analyze_pdf_pages("report.pdf")

        self.assertFalse(analysis.available)
        self.assertEqual(analysis.total_pages, 0)
        self.assertEqual(analysis.pages, [])
        self.assertIn("pdf_backend_unavailable", analysis.error)
        self.assertEqual(analysis.route_counts, {"native": 0, "ocr": 0, "hybrid": 0})

    def test_bad_and_password_protected_pdfs_return_errors_and_close(self):
        failed = analyze_pdf_pages(
            "bad.pdf",
            fitz_module=FakeFitz(error=ValueError("not a PDF")),
        )
        self.assertTrue(failed.available)
        self.assertIn("pdf_open_failed", failed.error)

        document = FakeDocument([], needs_pass=True)
        encrypted = analyze_pdf_pages(
            "encrypted.pdf",
            fitz_module=FakeFitz(document),
            password="wrong",
        )
        self.assertTrue(encrypted.available)
        self.assertEqual(encrypted.error, "pdf_password_required")
        self.assertTrue(document.closed)

    def test_thresholds_can_be_tuned_without_changing_parser_code(self):
        page = FakePage(blocks=[text_block(10, 10, 500, 100, "Short digital title")])

        conservative = analyze_pdf_page(page, 1)
        tuned = analyze_pdf_page(
            page,
            1,
            config=PageParserConfig(min_digital_chars=10, min_any_text_chars=4),
        )

        self.assertNotEqual(conservative.route, "native")
        self.assertEqual(tuned.route, "native")


if __name__ == "__main__":
    unittest.main()
