from __future__ import annotations

from types import SimpleNamespace
import unittest

from esg_encoding.content_extractor import (
    _adaptive_prediction_options_by_page,
    _selected_page_batch_ranges,
)


class AdaptivePaddleOptionTests(unittest.TestCase):
    def test_adjacent_pages_with_different_hints_are_split_into_separate_batches(self) -> None:
        analysis = SimpleNamespace(
            available=True,
            pages=[
                SimpleNamespace(page_number=1, complexity_hints={}),
                SimpleNamespace(
                    page_number=2,
                    complexity_hints={"needs_orientation": True},
                ),
                SimpleNamespace(
                    page_number=3,
                    complexity_hints={"needs_orientation": True},
                ),
                SimpleNamespace(
                    page_number=4,
                    complexity_hints={"needs_high_resolution_ocr": True},
                ),
            ],
        )

        options_by_page = _adaptive_prediction_options_by_page(analysis)
        ranges = _selected_page_batch_ranges(
            4,
            7,
            [1, 2, 3, 4],
            options_by_page,
        )

        self.assertEqual(ranges, [(1, 1), (2, 3), (4, 4)])
        self.assertFalse(options_by_page[1]["use_doc_orientation_classify"])
        self.assertTrue(options_by_page[2]["use_doc_orientation_classify"])
        self.assertEqual(options_by_page[2], options_by_page[3])
        self.assertNotEqual(options_by_page[3], options_by_page[4])
        self.assertEqual(options_by_page[4]["min_pixels"], 200704)
        self.assertEqual(options_by_page[4]["max_pixels"], 1605632)


if __name__ == "__main__":
    unittest.main()
