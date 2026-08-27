from __future__ import annotations

import unittest

from esg_encoding.content_extractor import ContentExtractor
from esg_encoding.models import TextSegment


class HtmlTableSpanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = ContentExtractor()

    def _table_family(self, table: str, table_id: str) -> list[TextSegment]:
        parent = TextSegment(
            segment_id=f"{table_id}-parent",
            content=table,
            page_number=1,
            position_y=0.1,
            position_x=0.1,
            segment_type="table",
            source_table_id=table_id,
            structured_data={
                "table_id": table_id,
                "bbox": [0.1, 0.1, 0.9, 0.9],
            },
        )
        return [
            parent,
            *self.extractor._table_segments_from_markdown(
                table,
                document_id="doc",
                page=1,
                table_id=table_id,
            ),
        ]

    def test_rowspan_keeps_dell_year_columns_aligned(self):
        table = """
        <table>
          <tr>
            <th>Performance metric</th><th>Unit</th><th>FY22</th>
            <th>FY23</th><th>FY24</th><th>Notes</th>
          </tr>
          <tr>
            <td>Overall</td><td rowspan="4">%</td><td>33.9%</td>
            <td>34.8%</td><td>35.0%</td><td></td>
          </tr>
          <tr>
            <td>People leader roles</td><td>28.2%</td><td>29.2%</td>
            <td>29.1%</td><td></td>
          </tr>
          <tr>
            <td>Technical roles</td><td>22.8%</td><td>24.5%</td>
            <td>25.0%</td><td></td>
          </tr>
          <tr>
            <td>Non-technical roles</td><td>47.7%</td><td>48.1%</td>
            <td>47.5%</td><td></td>
          </tr>
        </table>
        """

        rows = self.extractor._parse_html_table_rows(table)

        self.assertTrue(all(len(row) == 6 for row in rows))
        self.assertEqual(
            rows[3],
            ["Technical roles", "%", "22.8%", "24.5%", "25.0%", ""],
        )

        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="dell",
            page=86,
            table_id="dell-p86-table-1",
        )
        technical_cells = {
            segment.col_header: segment.value_text
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.row_header == "Technical roles"
        }
        self.assertEqual(
            technical_cells,
            {
                "Performance metric": "Technical roles",
                "Unit": "%",
                "FY22": "22.8%",
                "FY23": "24.5%",
                "FY24": "25.0%",
            },
        )

    def test_colspan_and_rowspan_expand_to_a_rectangular_grid(self):
        table = """
        <table>
          <tr><th>Group</th><th>Unit</th><th>FY22</th><th>FY23</th><th>FY24</th></tr>
          <tr><td colspan="5">U.S. race and ethnicity representation</td></tr>
          <tr><td>Asian</td><td rowspan="2">%</td><td>10%</td><td>11%</td><td>12%</td></tr>
          <tr><td>Black</td><td>8%</td><td>9%</td><td>10%</td></tr>
        </table>
        """

        rows = self.extractor._parse_html_table_rows(table)

        self.assertEqual(
            rows[1],
            ["U.S. race and ethnicity representation"] * 5,
        )
        self.assertEqual(rows[3], ["Black", "%", "8%", "9%", "10%"])

        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )
        section = next(
            segment for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "U.S. race and ethnicity representation"
        )
        self.assertEqual(section.colspan, 5)
        self.assertEqual(section.rowspan, 1)
        self.assertEqual(section.review_status, "verified")

    def test_thead_builds_three_level_header_paths_and_keeps_source_rows(self):
        table = """
        <table>
          <thead>
            <tr><th rowspan="3">Facility</th><th colspan="4">Emissions</th></tr>
            <tr><th colspan="2">Direct</th><th colspan="2">Indirect</th></tr>
            <tr><th>FY23</th><th>FY24</th><th>FY23</th><th>FY24</th></tr>
          </thead>
          <tbody>
            <tr><th scope="row">Plant A</th><td>1</td><td>2</td><td>3</td><td>4</td></tr>
          </tbody>
        </table>
        """

        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )

        rows = [segment for segment in segments if segment.segment_type == "table_row"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].structured_data["row_index"], 3)
        self.assertEqual(rows[0].row_header, "Plant A")
        value_cells = {
            segment.value_text: segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text in {"1", "2", "3", "4"}
        }
        self.assertEqual(
            value_cells["1"].header_path,
            ["Emissions", "Direct", "FY23"],
        )
        self.assertEqual(
            value_cells["2"].header_path,
            ["Emissions", "Direct", "FY24"],
        )
        self.assertEqual(
            value_cells["4"].header_path,
            ["Emissions", "Indirect", "FY24"],
        )
        self.assertEqual(value_cells["2"].col_header, "Emissions > Direct > FY24")
        self.assertEqual(value_cells["2"].structured_data["year"], 2024)

    def test_consecutive_th_rows_without_thead_build_multilevel_headers(self):
        table = """
        <table>
          <tr><th rowspan="2">Metric</th><th colspan="2">Energy</th></tr>
          <tr><th>FY23</th><th>FY24</th></tr>
          <tr><td>Consumption</td><td>1</td><td>2</td></tr>
        </table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-th-header",
        )
        rows = [
            segment for segment in segments if segment.segment_type == "table_row"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].structured_data["row_index"], 2)
        values = {
            segment.value_text: segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text in {"1", "2"}
        }
        self.assertEqual(values["1"].header_path, ["Energy", "FY23"])
        self.assertEqual(values["2"].header_path, ["Energy", "FY24"])

    def test_all_td_group_header_candidates_are_preserved_for_review(self):
        table = """
        <table>
          <tr><td colspan="2"></td><td colspan="2">Facilities</td></tr>
          <tr><td colspan="2">Category</td><td>Direct</td><td>Sub-tier</td></tr>
          <tr><td colspan="4">Environmental</td></tr>
          <tr><td>Air emissions</td><td>M</td><td>4</td><td>1</td></tr>
        </table>
        """

        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )

        rows = [segment for segment in segments if segment.segment_type == "table_row"]
        self.assertEqual(
            [segment.structured_data["row_index"] for segment in rows],
            [0, 1, 2, 3],
        )
        direct = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "4"
        )
        self.assertEqual(direct.header_path, [])
        self.assertEqual(direct.col_header, "Column 3")
        self.assertEqual(direct.review_status, "needs_review")
        self.assertIn("missing_header", direct.structured_data["quality_reasons"])

    def test_ambiguous_all_td_table_preserves_first_row_as_data(self):
        table = """
        <table>
          <tr><td>North</td><td>Open</td></tr>
          <tr><td>South</td><td>Closed</td></tr>
        </table>
        """

        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )

        rows = [segment for segment in segments if segment.segment_type == "table_row"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].structured_data["row_index"], 0)
        north = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "North"
        )
        self.assertEqual(north.col_header, "Column 1")
        self.assertEqual(north.header_path, [])
        self.assertEqual(north.review_status, "needs_review")
        self.assertIn("missing_header", north.structured_data["quality_reasons"])

    def test_markdown_without_complete_separator_preserves_first_row(self):
        cases = [
            "| Metric | FY24 |\n| Energy | 10 |",
            "| Metric | FY24 |\n| --- | |\n| Energy | 10 |",
            "| Metric | FY24 |\n| --- |\n| Energy | 10 |",
        ]
        for index, table in enumerate(cases):
            with self.subTest(index=index):
                segments = self.extractor._table_segments_from_markdown(
                    table,
                    document_id="doc",
                    page=1,
                    table_id=f"table-{index}",
                )
                rows = [
                    segment
                    for segment in segments
                    if segment.segment_type == "table_row"
                ]
                self.assertEqual(rows[0].structured_data["row_index"], 0)
                metric = next(
                    segment
                    for segment in segments
                    if segment.segment_type == "table_cell"
                    and segment.value_text == "Metric"
                )
                self.assertEqual(metric.col_header, "Column 1")
                self.assertEqual(metric.review_status, "needs_review")
                self.assertIn(
                    "missing_header",
                    metric.structured_data["quality_reasons"],
                )

    def test_complete_markdown_separator_keeps_explicit_header_contract(self):
        table = (
            "| Metric | Unit | FY24 |\n"
            "|---|---|---|\n"
            "| Energy | GJ | 10 |"
        )
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-markdown-header",
        )
        rows = [
            segment for segment in segments if segment.segment_type == "table_row"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].structured_data["row_index"], 1)
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertEqual(value.header_path, ["FY24"])
        self.assertEqual(value.structured_data["year"], 2024)
        self.assertEqual(value.unit, "GJ")

    def test_row_oriented_fy_cy_and_unit_scope_bind_to_value_cells(self):
        table = """
        <table>
          <thead><tr><th>Year</th><th>Unit of measure</th><th>Value</th></tr></thead>
          <tbody>
            <tr><td>FY22</td><td>GJ</td><td>10</td></tr>
            <tr><td>CY'24</td><td>GJ</td><td>12</td></tr>
          </tbody>
        </table>
        """

        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )
        values = {
            segment.value_text: segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.col_header == "Value"
        }

        self.assertEqual(values["10"].structured_data["year"], 2022)
        self.assertEqual(values["10"].structured_data["source_year_label"], "FY22")
        self.assertEqual(values["10"].unit, "GJ")
        self.assertEqual(values["10"].structured_data["unit_scope"], "row")
        self.assertEqual(values["12"].structured_data["year"], 2024)
        self.assertEqual(values["12"].unit, "GJ")
        unit_declaration = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.col_header == "Unit of measure"
            and segment.value_text == "GJ"
        )
        self.assertIsNone(unit_declaration.unit)

    def test_scaled_units_are_scoped_to_their_header_branches(self):
        table = """
        <table>
          <thead>
            <tr>
              <th rowspan="2">Metric</th>
              <th colspan="2">Energy (million kWh)</th>
              <th colspan="2">Emissions (thousand tCO2e)</th>
            </tr>
            <tr><th>FY23</th><th>FY24</th><th>FY23</th><th>FY24</th></tr>
          </thead>
          <tbody><tr><th scope="row">Operations</th><td>1</td><td>2</td><td>3</td><td>4</td></tr></tbody>
        </table>
        """

        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )
        energy = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "2"
        )
        emissions = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "4"
        )

        self.assertEqual(energy.unit, "million kWh")
        self.assertEqual(energy.structured_data["unit_base"], "kWh")
        self.assertEqual(energy.structured_data["unit_multiplier"], 1_000_000.0)
        self.assertEqual(energy.structured_data["unit_scope"], "year")
        self.assertEqual(energy.structured_data["year"], 2024)
        self.assertEqual(energy.structured_data["year_scope"], "column_header")
        self.assertEqual(energy.structured_data["source_year_label"], "FY24")
        self.assertEqual(energy.value_text, "2")
        self.assertEqual(emissions.unit, "thousand tCO2e")
        self.assertEqual(emissions.structured_data["unit_multiplier"], 1_000.0)
        self.assertEqual(emissions.structured_data["year"], 2024)

    def test_single_scoped_unit_and_year_do_not_leak_across_branches(self):
        table = """
        <table>
          <thead>
            <tr><th rowspan="2">Metric</th><th>Energy</th><th colspan="3">Emissions</th></tr>
            <tr><th>Value</th><th>Unit</th><th>Year</th><th>Value</th></tr>
          </thead>
          <tbody><tr><th scope="row">Operations</th><td>10</td><td>tCO2e</td><td>FY24</td><td>2</td></tr></tbody>
        </table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-scoped",
        )
        energy = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        emissions = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "2"
        )

        self.assertIsNone(energy.unit)
        self.assertNotIn("year", energy.structured_data)
        self.assertEqual(emissions.unit, "tCO2e")
        self.assertEqual(emissions.structured_data["year"], 2024)

    def test_unit_priority_and_multiplier_compatibility(self):
        inline = """
        <table><thead><tr><th>Metric</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Energy</td><td>10 GJ</td></tr></tbody></table>
        """
        inline_segments = self.extractor._table_segments_from_markdown(
            inline,
            document_id="doc",
            page=1,
            table_id="table-inline",
            table_title="Units: kWh and tCO2e",
        )
        inline_value = next(
            segment
            for segment in inline_segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10 GJ"
        )
        self.assertEqual(inline_value.unit, "GJ")
        self.assertNotIn(
            "ambiguous_unit_scope",
            inline_value.structured_data["quality_reasons"],
        )

        row_unit = """
        <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Energy</td><td>GJ</td><td>10</td></tr></tbody></table>
        """
        row_segments = self.extractor._table_segments_from_markdown(
            row_unit,
            document_id="doc",
            page=1,
            table_id="table-row-unit",
            table_title="Results (million kWh)",
        )
        row_value = next(
            segment
            for segment in row_segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertEqual(row_value.unit, "GJ")
        self.assertEqual(row_value.structured_data["unit_multiplier"], 1.0)

        scale_only_segments = self.extractor._table_segments_from_markdown(
            row_unit,
            document_id="doc",
            page=1,
            table_id="table-scale-only",
            table_title="Values in millions",
        )
        scale_only_value = next(
            segment
            for segment in scale_only_segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertEqual(scale_only_value.unit, "million GJ")
        self.assertEqual(
            scale_only_value.structured_data["unit_multiplier"],
            1_000_000.0,
        )

    def test_caption_provides_table_level_scaled_unit(self):
        table = """
        <table>
          <caption>Energy consumption — Values in million kWh</caption>
          <thead><tr><th>Metric</th><th>FY24</th></tr></thead>
          <tbody><tr><th scope="row">Operations</th><td>2</td></tr></tbody>
        </table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-caption-unit",
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "2"
        )

        self.assertEqual(value.unit, "million kWh")
        self.assertEqual(value.structured_data["unit_base"], "kWh")
        self.assertEqual(value.structured_data["unit_multiplier"], 1_000_000.0)
        self.assertEqual(value.structured_data["unit_scope"], "table")

    def test_ambiguous_caption_units_fail_closed(self):
        table = """
        <table>
          <caption>Results — Units: kWh and tCO2e</caption>
          <thead><tr><th>Metric</th><th>FY24</th></tr></thead>
          <tbody><tr><th scope="row">Operations</th><td>2</td></tr></tbody>
        </table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-caption-ambiguous",
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "2"
        )

        self.assertIsNone(value.unit)
        self.assertIn(
            "ambiguous_unit_scope",
            value.structured_data["quality_reasons"],
        )
        self.assertEqual(value.review_status, "needs_review")

    def test_same_unit_with_conflicting_multiplier_fails_closed(self):
        table = """
        <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Energy</td><td>GJ and million GJ</td><td>10</td></tr></tbody></table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-scale-conflict",
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertIsNone(value.unit)
        self.assertIn(
            "ambiguous_unit_scope",
            value.structured_data["quality_reasons"],
        )

    def test_row_and_year_scope_multiplier_conflict_fails_closed(self):
        table = """
        <table>
          <thead>
            <tr><th rowspan="2">Metric</th><th colspan="2">Energy (million GJ)</th></tr>
            <tr><th>Unit</th><th>FY24</th></tr>
          </thead>
          <tbody><tr><th scope="row">Consumption</th><td>thousand GJ</td><td>10</td></tr></tbody>
        </table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-cross-scope-conflict",
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertIsNone(value.unit)
        self.assertIn(
            "ambiguous_unit_scope",
            value.structured_data["quality_reasons"],
        )

    def test_inline_year_conflict_does_not_bind_and_row_label_is_not_a_value(self):
        table = """
        <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
        <tbody><tr><th scope="row">Scope 1 emissions</th><td>tCO2e</td><td>FY23 10</td></tr></tbody></table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-year-conflict",
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "FY23 10"
        )
        row_label = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "Scope 1 emissions"
        )
        self.assertNotIn("year", value.structured_data)
        self.assertIn(
            "conflicting_year_scope",
            value.structured_data["quality_reasons"],
        )
        self.assertIsNone(row_label.unit)
        self.assertNotIn("year", row_label.structured_data)

    def test_structured_record_enrichment_preserves_year_conflict(self):
        table = """
        <table><thead><tr><th>Metric</th><th>Year</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Energy</td><td>FY23</td><td>10</td></tr></tbody></table>
        """
        family = self._table_family(table, "table-enrichment-year")
        value = next(
            segment
            for segment in family
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertNotIn("year", value.structured_data)

        self.extractor._enrich_table_segments_from_records(
            family,
            [
                {
                    "pred_html": table,
                    "page_number": 1,
                    "bbox": [0.1, 0.1, 0.9, 0.9],
                    "structure_confidence": 0.98,
                    "ocr_confidence": 0.98,
                }
            ],
        )

        self.assertNotIn("year", value.structured_data)
        self.assertIn(
            "conflicting_year_scope",
            value.structured_data["quality_reasons"],
        )

    def test_header_depth_mismatch_does_not_rebind_cell_geometry(self):
        markdown = (
            "| Metric | FY23 | FY24 |\n"
            "|---|---|---|\n"
            "| Energy | 1 | 2 |"
        )
        structured = """
        <table>
          <thead>
            <tr><th rowspan="2">Metric</th><th colspan="2">Year</th></tr>
            <tr><th>FY23</th><th>FY24</th></tr>
          </thead>
          <tbody><tr><td>Energy</td><td>1</td><td>2</td></tr></tbody>
        </table>
        """
        family = self._table_family(markdown, "table-header-mismatch")
        value = next(
            segment
            for segment in family
            if segment.segment_type == "table_cell"
            and segment.value_text == "1"
        )
        original_position = (value.position_x, value.position_y)

        self.extractor._enrich_table_segments_from_records(
            family,
            [
                {
                    "pred_html": structured,
                    "page_number": 1,
                    "bbox": [0.1, 0.1, 0.9, 0.9],
                    "structure_confidence": 0.98,
                    "ocr_confidence": 0.98,
                    "cell_box_list": [
                        [0.1, 0.1 + index * 0.05, 0.2, 0.14 + index * 0.05]
                        for index in range(7)
                    ],
                }
            ],
        )

        self.assertEqual((value.position_x, value.position_y), original_position)
        self.assertIsNone(value.structured_data.get("bbox"))
        self.assertEqual(value.review_status, "needs_review")
        self.assertIn(
            "cell_geometry_alignment_mismatch",
            value.structured_data["quality_reasons"],
        )

    def test_cell_bbox_count_mismatch_fails_closed(self):
        table = """
        <table>
          <thead><tr><th>Metric</th><th>FY24</th></tr></thead>
          <tbody>
            <tr><td></td><td></td></tr>
            <tr><td>Energy</td><td>1</td></tr>
          </tbody>
        </table>
        """
        family = self._table_family(table, "table-box-count")
        value = next(
            segment
            for segment in family
            if segment.segment_type == "table_cell"
            and segment.value_text == "1"
        )
        original_position = (value.position_x, value.position_y)

        self.extractor._enrich_table_segments_from_records(
            family,
            [
                {
                    "pred_html": table,
                    "page_number": 1,
                    "bbox": [0.1, 0.1, 0.9, 0.9],
                    "structure_confidence": 0.98,
                    "ocr_confidence": 0.98,
                    "cell_box_list": [
                        [0.1, 0.1 + index * 0.05, 0.2, 0.14 + index * 0.05]
                        for index in range(6)
                    ],
                }
            ],
        )

        self.assertEqual((value.position_x, value.position_y), original_position)
        self.assertIsNone(value.structured_data.get("bbox"))
        self.assertIn(
            "cell_bbox_count_mismatch",
            value.structured_data["quality_reasons"],
        )

    def test_conflicting_year_scopes_fail_closed(self):
        table = """
        <table>
          <thead><tr><th>Metric</th><th>Year</th><th>FY24</th></tr></thead>
          <tbody><tr><td>Energy</td><td>FY23</td><td>10</td></tr></tbody>
        </table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )

        self.assertNotIn("year", value.structured_data)
        self.assertIsNone(value.unit)
        self.assertEqual(value.review_status, "needs_review")
        self.assertIn(
            "conflicting_year_scope",
            value.structured_data["quality_reasons"],
        )

    def test_multiple_years_in_one_scope_are_ambiguous(self):
        table = """
        <table><thead><tr><th>Metric</th><th>FY23 / FY24</th></tr></thead>
        <tbody><tr><td>Energy</td><td>10</td></tr></tbody></table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-year-ambiguity",
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertNotIn("year", value.structured_data)
        self.assertNotIn("source_year_label", value.structured_data)
        self.assertEqual(value.review_status, "needs_review")
        self.assertIn(
            "ambiguous_year_scope",
            value.structured_data["quality_reasons"],
        )

    def test_multi_year_caption_does_not_override_exact_column_years(self):
        table = """
        <table>
          <caption>ESG Data 2023-2024</caption>
          <thead><tr><th>Metric</th><th>FY23</th><th>FY24</th></tr></thead>
          <tbody><tr><th scope="row">Energy</th><td>9</td><td>10</td></tr></tbody>
        </table>
        """
        segments = self.extractor._table_segments_from_markdown(
            table,
            document_id="doc",
            page=1,
            table_id="table-caption-years",
        )
        values = {
            segment.value_text: segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text in {"9", "10"}
        }

        self.assertEqual(values["9"].structured_data["year"], 2023)
        self.assertEqual(values["10"].structured_data["year"], 2024)
        self.assertNotIn(
            "ambiguous_year_scope",
            values["9"].structured_data["quality_reasons"],
        )
        self.assertNotIn(
            "ambiguous_year_scope",
            values["10"].structured_data["quality_reasons"],
        )

    def test_ambiguous_and_false_unit_scopes_fail_closed(self):
        ambiguous = """
        <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Energy</td><td>GJ and MWh</td><td>10</td></tr></tbody></table>
        """
        segments = self.extractor._table_segments_from_markdown(
            ambiguous, document_id="doc", page=1, table_id="table-1"
        )
        value = next(
            segment
            for segment in segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "10"
        )
        self.assertIsNone(value.unit)
        self.assertIn(
            "ambiguous_unit_scope",
            value.structured_data["quality_reasons"],
        )

        compound = """
        <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Intensity</td><td>tCO2e/MWh</td><td>5</td></tr></tbody></table>
        """
        compound_segments = self.extractor._table_segments_from_markdown(
            compound, document_id="doc", page=1, table_id="table-compound"
        )
        compound_value = next(
            segment
            for segment in compound_segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "5"
        )
        self.assertEqual(compound_value.unit, "tCO2e/MWh")

        for index, (raw_unit, expected_unit) in enumerate(
            [
                ("kg CO2e/MWh", "kgCO2e/MWh"),
                ("kg of CO2e/MWh", "kgCO2e/MWh"),
                ("t CO2e", "tCO2e"),
                ("kt CO2e", "ktCO2e"),
                ("Mt CO2e", "MtCO2e"),
            ]
        ):
            with self.subTest(raw_unit=raw_unit):
                variant = f"""
                <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
                <tbody><tr><td>Intensity</td><td>{raw_unit}</td><td>6</td></tr></tbody></table>
                """
                variant_segments = self.extractor._table_segments_from_markdown(
                    variant,
                    document_id="doc",
                    page=1,
                    table_id=f"table-compound-{index}",
                )
                variant_value = next(
                    segment
                    for segment in variant_segments
                    if segment.segment_type == "table_cell"
                    and segment.value_text == "6"
                )
                self.assertEqual(variant_value.unit, expected_unit)

        unsupported = """
        <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Intensity</td><td>tCO2e per million revenue</td><td>7</td></tr></tbody></table>
        """
        unsupported_segments = self.extractor._table_segments_from_markdown(
            unsupported,
            document_id="doc",
            page=1,
            table_id="table-unsupported-compound",
        )
        unsupported_value = next(
            segment
            for segment in unsupported_segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "7"
        )
        self.assertIsNone(unsupported_value.unit)
        self.assertIn(
            "ambiguous_unit_scope",
            unsupported_value.structured_data["quality_reasons"],
        )

        community = """
        <table><thead><tr><th>Metric</th><th>Community</th><th>FY24</th></tr></thead>
        <tbody><tr><td>Investment</td><td>North</td><td>20</td></tr></tbody></table>
        """
        community_segments = self.extractor._table_segments_from_markdown(
            community, document_id="doc", page=1, table_id="table-2"
        )
        community_value = next(
            segment
            for segment in community_segments
            if segment.segment_type == "table_cell"
            and segment.value_text == "20"
        )
        self.assertIsNone(community_value.unit)

    def test_year_normalization_rejects_loose_two_digit_numbers(self):
        cases = {
            "FY22": [2022],
            "FY 2023": [2023],
            "CY'24": [2024],
            "CY 2025": [2025],
            "FY23/24": [2024],
            "FY2023/24": [2024],
            "2023/24": [2024],
            "FY23 / FY24": [2023, 2024],
            "2026": [2026],
            "24 months": [],
            "Q4 24": [],
            "GRI 305-1": [],
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    self.extractor._extract_table_years(value),
                    expected,
                )

    def test_postfix_and_accounting_unit_scales_are_not_silently_x1(self):
        cases = [
            ("USD million", "million USD", "USD", 1_000_000.0),
            ("'000 tonnes", "thousand t", "t", 1_000.0),
            ("GJ (000s)", "thousand GJ", "GJ", 1_000.0),
        ]
        for index, (raw_unit, rendered, base, multiplier) in enumerate(cases):
            with self.subTest(raw_unit=raw_unit):
                table = f"""
                <table><thead><tr><th>Metric</th><th>Unit</th><th>FY24</th></tr></thead>
                <tbody><tr><td>Result</td><td>{raw_unit}</td><td>5</td></tr></tbody></table>
                """
                segments = self.extractor._table_segments_from_markdown(
                    table,
                    document_id="doc",
                    page=1,
                    table_id=f"table-postfix-scale-{index}",
                )
                value = next(
                    segment
                    for segment in segments
                    if segment.segment_type == "table_cell"
                    and segment.value_text == "5"
                )
                self.assertEqual(value.unit, rendered)
                self.assertEqual(value.structured_data["unit_base"], base)
                self.assertEqual(
                    value.structured_data["unit_multiplier"],
                    multiplier,
                )
                self.assertNotIn(
                    "ambiguous_unit_scope",
                    value.structured_data["quality_reasons"],
                )

    def test_malformed_table_is_marked_for_review(self):
        table = "<table><tr><th>Metric</th><th>FY24</th></tr><tr><td>Energy</td><td>10</td></tr>"
        segments = self.extractor._table_segments_from_markdown(
            table, document_id="doc", page=1, table_id="table-1"
        )
        self.assertTrue(segments)
        self.assertTrue(all(segment.review_status == "needs_review" for segment in segments))

    def test_adjacent_identical_headers_are_stitched_with_reliable_context(self):
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| A | GJ | 1 |",
            page_number=1,
            position_y=0.80,
            segment_type="table",
            source_table_id="table-1",
            structured_data={
                "table_title": "Energy consumption",
                "section_path": ["Environment", "Energy"],
                "bbox": [0.08, 0.80, 0.92, 0.98],
            },
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| B | GJ | 2 |",
            page_number=2,
            position_y=0.05,
            segment_type="table",
            source_table_id="table-2",
            structured_data={
                "table_title": "Energy consumption",
                "section_path": ["Environment", "Energy"],
                "bbox": [0.08, 0.05, 0.92, 0.24],
            },
        )
        cell = TextSegment(
            segment_id="c2", content="B", page_number=2, position_y=2,
            segment_type="table_cell", source_table_id="table-2",
        )
        self.extractor._stitch_continued_tables([first, second, cell])
        self.assertEqual(second.source_table_id, "table-1")
        self.assertEqual(cell.source_table_id, "table-1")
        self.assertEqual(cell.structured_data["continued_from_page"], 1)

    def test_same_headers_with_different_titles_are_not_stitched(self):
        common = {
            "section_path": ["Environment", "Performance"],
        }
        first = TextSegment(
            segment_id="t1",
            content="| Year | Unit | Value |\n|---|---|---|\n| FY24 | GJ | 10 |",
            page_number=1,
            position_y=0.82,
            segment_type="table",
            source_table_id="energy-table",
            structured_data={
                **common,
                "table_title": "Energy use",
                "bbox": [0.1, 0.82, 0.9, 0.98],
            },
        )
        second = TextSegment(
            segment_id="t2",
            content="| Year | Unit | Value |\n|---|---|---|\n| FY24 | GJ | 20 |",
            page_number=2,
            position_y=0.04,
            segment_type="table",
            source_table_id="water-table",
            structured_data={
                **common,
                "table_title": "Water use",
                "bbox": [0.1, 0.04, 0.9, 0.20],
            },
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "water-table")
        self.assertNotIn("continued_from_page", second.structured_data)

    def test_explicit_continuation_cue_does_not_replace_missing_geometry(self):
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| A | GJ | 1 |",
            page_number=1,
            position_y=10,
            segment_type="table",
            source_table_id="table-1",
            structured_data={
                "table_title": "Energy consumption",
                "section_path": ["Environment", "Energy"],
            },
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| B | GJ | 2 |",
            page_number=2,
            position_y=20,
            segment_type="table",
            source_table_id="table-2",
            structured_data={
                "table_title": "Energy consumption (continued)",
                "section_path": ["Environment", "Energy"],
            },
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-2")
        self.assertNotIn("continued_from_page", second.structured_data)

    def test_full_continuation_phrase_is_normalized_with_edge_geometry(self):
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| A | GJ | 1 |",
            page_number=1,
            position_y=0.80,
            segment_type="table",
            source_table_id="table-1",
            structured_data={
                "table_title": "Energy consumption",
                "section_path": ["Environment", "Energy"],
                "bbox": [0.08, 0.80, 0.92, 0.98],
            },
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| B | GJ | 2 |",
            page_number=2,
            position_y=0.05,
            segment_type="table",
            source_table_id="table-2",
            structured_data={
                "table_title": "Energy consumption - continued from previous page",
                "section_path": ["Environment", "Energy"],
                "bbox": [0.08, 0.05, 0.92, 0.24],
            },
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-1")
        self.assertEqual(second.structured_data["continued_from_page"], 1)

    def test_missing_edge_or_continuation_evidence_fails_closed(self):
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| A | GJ | 1 |",
            page_number=1,
            position_y=10,
            segment_type="table",
            source_table_id="table-1",
            structured_data={
                "table_title": "Energy consumption",
                "section_path": ["Environment", "Energy"],
            },
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| B | GJ | 2 |",
            page_number=2,
            position_y=20,
            segment_type="table",
            source_table_id="table-2",
            structured_data={
                "table_title": "Energy consumption",
                "section_path": ["Environment", "Energy"],
            },
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-2")

    def test_incompatible_units_are_not_stitched(self):
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| Energy | GJ | 1 |",
            page_number=1,
            position_y=0.82,
            segment_type="table",
            source_table_id="table-1",
            structured_data={
                "table_title": "Environmental performance",
                "section_path": ["Environment", "Performance"],
                "bbox": [0.1, 0.82, 0.9, 0.98],
            },
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| Water | m3 | 2 |",
            page_number=2,
            position_y=0.04,
            segment_type="table",
            source_table_id="table-2",
            structured_data={
                "table_title": "Environmental performance",
                "section_path": ["Environment", "Performance"],
                "bbox": [0.1, 0.04, 0.9, 0.20],
            },
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-2")

    def test_generic_unit_header_without_real_units_is_not_stitched(self):
        common = {
            "table_title": "Operating metrics",
            "section_path": ["Operations", "Metrics"],
        }
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| A | - | 1 |",
            page_number=1,
            position_y=0.82,
            segment_type="table",
            source_table_id="table-1",
            structured_data={**common, "bbox": [0.1, 0.82, 0.9, 0.98]},
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| B | - | 2 |",
            page_number=2,
            position_y=0.04,
            segment_type="table",
            source_table_id="table-2",
            structured_data={**common, "bbox": [0.1, 0.04, 0.9, 0.20]},
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-2")

    def test_structured_cell_units_are_bound_to_their_column(self):
        common = {
            "table_title": "Energy metrics",
            "section_path": ["Environment", "Energy"],
        }
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| A | - | 1 |",
            page_number=1,
            position_y=0.82,
            segment_type="table",
            source_table_id="table-1",
            structured_data={**common, "bbox": [0.1, 0.82, 0.9, 0.98]},
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| B | - | 2 |",
            page_number=2,
            position_y=0.04,
            segment_type="table",
            source_table_id="table-2",
            structured_data={**common, "bbox": [0.1, 0.04, 0.9, 0.20]},
        )
        first_unit = TextSegment(
            segment_id="u1",
            content="GJ",
            page_number=1,
            position_y=0.90,
            segment_type="table_cell",
            source_table_id="table-1",
            unit="GJ",
            structured_data={"col_index": 1},
        )
        second_unit = TextSegment(
            segment_id="u2",
            content="GJ",
            page_number=2,
            position_y=0.12,
            segment_type="table_cell",
            source_table_id="table-2",
            unit="GJ",
            structured_data={"col_index": 1},
        )

        self.extractor._stitch_continued_tables(
            [first, second, first_unit, second_unit]
        )

        self.assertEqual(second.source_table_id, "table-1")
        self.assertEqual(second_unit.source_table_id, "table-1")

    def test_units_must_match_the_same_columns(self):
        common = {
            "table_title": "Energy conversion",
            "section_path": ["Environment", "Energy"],
        }
        first = TextSegment(
            segment_id="t1",
            content="| Input (GJ) | Output (MWh) |\n|---|---|\n| 10 | 2 |",
            page_number=1,
            position_y=0.82,
            segment_type="table",
            source_table_id="table-1",
            structured_data={**common, "bbox": [0.1, 0.82, 0.9, 0.98]},
        )
        second = TextSegment(
            segment_id="t2",
            content="| Input (MWh) | Output (GJ) |\n|---|---|\n| 3 | 11 |",
            page_number=2,
            position_y=0.04,
            segment_type="table",
            source_table_id="table-2",
            structured_data={**common, "bbox": [0.1, 0.04, 0.9, 0.20]},
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-2")

    def test_structured_caption_may_equal_section_heading(self):
        common = {
            "table_title": "Energy",
            "table_title_source": "structured_record_caption",
            "section_path": ["Environment", "Energy"],
        }
        first = TextSegment(
            segment_id="t1",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| A | GJ | 1 |",
            page_number=1,
            position_y=0.82,
            segment_type="table",
            source_table_id="table-1",
            structured_data={**common, "bbox": [0.1, 0.82, 0.9, 0.98]},
        )
        second = TextSegment(
            segment_id="t2",
            content="| Metric | Unit | FY24 |\n|---|---|---|\n| B | GJ | 2 |",
            page_number=2,
            position_y=0.04,
            segment_type="table",
            source_table_id="table-2",
            structured_data={**common, "bbox": [0.1, 0.04, 0.9, 0.20]},
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-1")

    def test_compound_or_scaled_unit_differences_are_not_stitched(self):
        cases = [
            ("tCO2e/MWh", "tCO2e/GJ"),
            ("million t", "t"),
            ("kg CO2e", "kg"),
            ("tonnes CO2e", "tonnes"),
            ("kg of CO2e", "kg"),
            ("t of CO2e", "t"),
        ]
        for index, (first_unit, second_unit) in enumerate(cases, start=1):
            with self.subTest(first_unit=first_unit, second_unit=second_unit):
                first = TextSegment(
                    segment_id=f"t1-{index}",
                    content=(
                        "| Metric | Unit | FY24 |\n|---|---|---|\n"
                        f"| Emissions | {first_unit} | 1 |"
                    ),
                    page_number=1,
                    position_y=0.82,
                    segment_type="table",
                    source_table_id=f"table-1-{index}",
                    structured_data={
                        "table_title": "Emissions intensity",
                        "section_path": ["Environment", "Emissions"],
                        "bbox": [0.1, 0.82, 0.9, 0.98],
                    },
                )
                second = TextSegment(
                    segment_id=f"t2-{index}",
                    content=(
                        "| Metric | Unit | FY24 |\n|---|---|---|\n"
                        f"| Emissions | {second_unit} | 2 |"
                    ),
                    page_number=2,
                    position_y=0.04,
                    segment_type="table",
                    source_table_id=f"table-2-{index}",
                    structured_data={
                        "table_title": "Emissions intensity",
                        "section_path": ["Environment", "Emissions"],
                        "bbox": [0.1, 0.04, 0.9, 0.20],
                    },
                )

                self.extractor._stitch_continued_tables([first, second])

                self.assertEqual(second.source_table_id, f"table-2-{index}")

    def test_section_heading_is_not_accepted_as_independent_table_title(self):
        first = TextSegment(
            segment_id="t1",
            content="| Year | Unit | Value |\n|---|---|---|\n| FY24 | GJ | 1 |",
            page_number=1,
            position_y=0.82,
            segment_type="table",
            source_table_id="table-1",
            structured_data={
                "table_title": "Energy",
                "table_title_source": "section_heading",
                "section_path": ["Environment", "Energy"],
                "bbox": [0.1, 0.82, 0.9, 0.98],
            },
        )
        second = TextSegment(
            segment_id="t2",
            content="| Year | Unit | Value |\n|---|---|---|\n| FY25 | GJ | 2 |",
            page_number=2,
            position_y=0.04,
            segment_type="table",
            source_table_id="table-2",
            structured_data={
                "table_title": "Energy",
                "table_title_source": "section_heading",
                "section_path": ["Environment", "Energy"],
                "bbox": [0.1, 0.04, 0.9, 0.20],
            },
        )

        self.extractor._stitch_continued_tables([first, second])

        self.assertEqual(second.source_table_id, "table-2")


if __name__ == "__main__":
    unittest.main()
