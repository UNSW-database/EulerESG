from __future__ import annotations

import numpy as np

from esg_encoding.models import DocumentContent, ReportContent, TextSegment
from esg_encoding.retrieval.metric_corpus import (
    MetricCorpusConfig,
    MetricRetrievalCorpus,
    attach_metric_embeddings,
    build_metric_retrieval_corpus,
    combine_metric_retrieval_corpora,
    metric_embeddings,
    metric_search_units,
    namespace_metric_retrieval_corpus,
    subset_metric_retrieval_corpus,
)


def _segment(
    segment_id: str,
    content: str,
    *,
    page: int = 1,
    kind: str = "text",
    table_id: str | None = None,
    row_header: str | None = None,
    col_header: str | None = None,
    value_text: str | None = None,
    unit: str | None = None,
    position_x: float | None = None,
    data: dict | None = None,
) -> TextSegment:
    return TextSegment(
        segment_id=segment_id,
        content=content,
        page_number=page,
        position_y=float(int("".join(ch for ch in segment_id if ch.isdigit()) or 1)),
        position_x=position_x,
        segment_type=kind,
        source_table_id=table_id,
        row_header=row_header,
        col_header=col_header,
        value_text=value_text,
        unit=unit,
        structured_data=data or {},
    )


def _document(segments: list[TextSegment], *, document_id: str = "report-1") -> DocumentContent:
    return DocumentContent(
        document_id=document_id,
        file_path=f"{document_id}.pdf",
        segments=segments,
        markdown_content="",
    )


def test_complete_paragraph_is_preserved_and_heading_is_context_only() -> None:
    heading = _segment(
        "s1",
        "Water Management",
        kind="heading",
        data={"section_path": ["Environment", "Water Management"]},
    )
    paragraph_text = (
        "The company measures total water withdrawn, consumed, and discharged "
        "for every operating site."
    )
    paragraph = _segment(
        "s2",
        paragraph_text,
        data={"section_path": ["Environment", "Water Management"]},
    )

    corpus = build_metric_retrieval_corpus(_document([heading, paragraph]))

    paragraph_block = next(
        block for block in corpus.evidence_blocks if block.block_type == "paragraph"
    )
    paragraph_view = next(
        view
        for view in corpus.retrieval_views
        if view.evidence_block_id == paragraph_block.block_id
    )
    assert paragraph_block.full_content == paragraph_text
    assert paragraph_view.content == paragraph_text
    assert paragraph_view.is_complete_block is True
    assert paragraph_view.start_offset == 0
    assert paragraph_view.end_offset == len(paragraph_text)
    assert "[Section] Environment > Water Management" in paragraph_view.index_text
    assert corpus.evidence_for_view(paragraph_view.view_id) == paragraph_block


def test_adjacent_list_items_form_one_complete_list_without_crossing_page() -> None:
    segments = [
        _segment("s1", "- Track Scope 1 emissions", kind="list_item"),
        _segment("s2", "- Track Scope 2 emissions", kind="list_item"),
        _segment("s3", "- Page two item", page=2, kind="list_item"),
    ]

    corpus = build_metric_retrieval_corpus(_document(segments))
    lists = [block for block in corpus.evidence_blocks if block.block_type == "list"]

    assert len(lists) == 2
    assert lists[0].full_content == (
        "- Track Scope 1 emissions\n- Track Scope 2 emissions"
    )
    assert lists[0].source_segment_ids == ["s1", "s2"]
    first_view = next(
        view for view in corpus.retrieval_views if view.evidence_block_id == lists[0].block_id
    )
    assert first_view.content == lists[0].full_content
    assert first_view.is_complete_block is True


def test_long_paragraph_uses_only_complete_sentence_windows() -> None:
    sentences = [
        "Scope one emissions were independently verified " + ("A" * 105) + ". ",
        "Scope two emissions used the market method " + ("B" * 105) + ". ",
        "The reporting boundary covers all subsidiaries " + ("C" * 105) + ".",
    ]
    full_text = "".join(sentences)
    corpus = build_metric_retrieval_corpus(
        _document([_segment("s1", full_text)]),
        MetricCorpusConfig(max_text_view_chars=256, sentence_overlap=1),
    )

    block = corpus.evidence_blocks[0]
    views = [view for view in corpus.retrieval_views if view.evidence_block_id == block.block_id]
    assert block.full_content == full_text
    assert len(views) == 3
    for view in views:
        assert view.start_offset is not None
        assert view.end_offset is not None
        assert full_text[view.start_offset : view.end_offset] == view.content
        assert view.content.rstrip().endswith(".")
        assert len(view.content) <= 256
        assert view.metadata["oversized_sentence"] is False
    for sentence in sentences:
        assert any(sentence.strip() in view.content for view in views)


def test_one_oversized_sentence_is_never_cut_mid_sentence() -> None:
    full_text = "One indivisible sentence " + ("x" * 400) + "."
    corpus = build_metric_retrieval_corpus(
        _document([_segment("s1", full_text)]),
        MetricCorpusConfig(max_text_view_chars=256),
    )

    view = corpus.retrieval_views[0]
    assert view.content == full_text
    assert view.start_offset == 0
    assert view.end_offset == len(full_text)
    assert view.metadata["oversized_sentence"] is True


def test_table_parent_is_complete_and_derivatives_become_full_row_view() -> None:
    table_text = "| Metric | FY2023 | FY2024 |\n|---|---:|---:|\n| Energy | 100 GJ | 120 GJ |"
    common = {
        "table_title": "Energy performance",
        "section_path": ["Environment", "Energy"],
    }
    table = _segment(
        "s1",
        table_text,
        page=3,
        kind="table",
        table_id="table-1",
        data={**common, "table_id": "table-1"},
    )
    row = _segment(
        "s2",
        "[Table] Energy performance\nMetric: Energy | FY2023: 100 GJ | FY2024: 120 GJ",
        page=3,
        kind="table_row",
        table_id="table-1",
        row_header="Energy",
        data={**common, "table_id": "table-1", "row_index": 2},
    )
    cell_2023 = _segment(
        "s3",
        "FY2023: 100 GJ",
        page=3,
        kind="table_cell",
        table_id="table-1",
        row_header="Energy",
        col_header="FY2023",
        value_text="100",
        unit="GJ",
        position_x=1,
        data={
            **common,
            "table_id": "table-1",
            "row_index": 2,
            "col_index": 1,
            "year": 2023,
            "header_path": ["Energy use", "FY2023"],
        },
    )
    cell_2024 = _segment(
        "s4",
        "FY2024: 120 GJ",
        page=3,
        kind="table_cell",
        table_id="table-1",
        row_header="Energy",
        col_header="FY2024",
        value_text="120",
        unit="GJ",
        position_x=2,
        data={
            **common,
            "table_id": "table-1",
            "row_index": 2,
            "col_index": 2,
            "year": 2024,
            "header_path": ["Energy use", "FY2024"],
        },
    )

    corpus = build_metric_retrieval_corpus(
        _document([table, row, cell_2023, cell_2024])
    )

    assert len(corpus.evidence_blocks) == 1
    block = corpus.evidence_blocks[0]
    assert block.block_type == "table"
    assert block.full_content == table_text
    assert block.source_segment_ids == ["s1", "s2", "s3", "s4"]
    assert block.metadata["synthesized_full_table"] is False
    assert set(corpus.segment_to_block_id) == {"s1", "s2", "s3", "s4"}
    assert set(corpus.segment_to_block_id.values()) == {block.block_id}

    row_views = [view for view in corpus.retrieval_views if view.view_type == "table_row"]
    assert len(row_views) == 1
    row_view = row_views[0]
    assert row_view.row_index == 2
    assert row_view.column_indexes == [1, 2]
    assert set(row_view.source_segment_ids) == {"s2", "s3", "s4"}
    assert "100 GJ" in row_view.index_text
    assert "120 GJ" in row_view.index_text
    assert "[Section] Environment > Energy" in row_view.index_text
    assert corpus.evidence_for_view(row_view.view_id).full_content == table_text
    assert not any(view.view_type == "table_cell" for view in corpus.retrieval_views)


def test_large_table_keeps_complete_parent_but_indexes_rows_instead_of_full_table() -> None:
    table_text = "| Metric | Value |\n|---|---|\n" + "\n".join(
        f"| Metric {index} | {index} GJ |" for index in range(60)
    )
    table = _segment(
        "s1",
        table_text,
        kind="table",
        table_id="table-1",
        data={"table_id": "table-1"},
    )
    row = _segment(
        "s2",
        "Metric 1 | Value: 1 GJ",
        kind="table_row",
        table_id="table-1",
        data={"table_id": "table-1", "row_index": 1},
    )

    corpus = build_metric_retrieval_corpus(
        _document([table, row]),
        MetricCorpusConfig(max_full_table_view_chars=512),
    )

    assert corpus.evidence_blocks[0].full_content == table_text
    assert [view.view_type for view in corpus.retrieval_views] == ["table_row"]


def test_same_table_id_is_scoped_by_source_report_and_page() -> None:
    segments = [
        _segment(
            "a1",
            "| A | 1 |",
            page=1,
            kind="table",
            table_id="shared",
            data={"table_id": "shared", "source_report_id": "report-a"},
        ),
        _segment(
            "a2",
            "| A | 2 |",
            page=2,
            kind="table",
            table_id="shared",
            data={"table_id": "shared", "source_report_id": "report-a"},
        ),
        _segment(
            "b1",
            "| B | 1 |",
            page=1,
            kind="table",
            table_id="shared",
            data={"table_id": "shared", "source_report_id": "report-b"},
        ),
    ]

    corpus = build_metric_retrieval_corpus(_document(segments, document_id="company"))

    assert len(corpus.evidence_blocks) == 3
    assert {block.source_report_id for block in corpus.evidence_blocks} == {
        "report-a",
        "report-b",
    }
    assert {(block.source_report_id, block.page_number) for block in corpus.evidence_blocks} == {
        ("report-a", 1),
        ("report-a", 2),
        ("report-b", 1),
    }


def test_ids_and_signature_are_stable_and_invalid_parent_mapping_is_rejected() -> None:
    document = _document(
        [
            _segment("s1", "Governance", kind="heading"),
            _segment("s2", "The board reviews climate risk every quarter."),
        ]
    )
    first = build_metric_retrieval_corpus(document)
    second = build_metric_retrieval_corpus(document)

    assert first.corpus_signature == second.corpus_signature
    assert [block.block_id for block in first.evidence_blocks] == [
        block.block_id for block in second.evidence_blocks
    ]
    assert [view.view_id for view in first.retrieval_views] == [
        view.view_id for view in second.retrieval_views
    ]

    invalid = (
        first.model_dump(mode="json")
        if hasattr(first, "model_dump")
        else first.dict()
    )
    invalid["retrieval_views"][0]["evidence_block_id"] = "missing-parent"
    try:
        MetricRetrievalCorpus(**invalid)
    except ValueError as error:
        assert "has no evidence parent" in str(error)
    else:  # pragma: no cover - defensive assertion for both pytest/unittest imports
        raise AssertionError("Invalid retrieval parent mapping was accepted")


def test_search_unit_keeps_row_match_and_resolves_to_complete_table() -> None:
    table_text = "| Metric | FY2024 |\n|---|---:|\n| Energy use | 120 GJ |"
    table = _segment(
        "table",
        table_text,
        kind="table",
        table_id="energy-table",
        data={"table_id": "energy-table"},
    )
    row = _segment(
        "row",
        "Metric: Energy use | FY2024: 120 GJ",
        kind="table_row",
        table_id="energy-table",
        row_header="Energy use",
        data={"table_id": "energy-table", "row_index": 2},
    )
    document = _document([table, row])
    report = ReportContent(
        document_id=document.document_id,
        document_content=document,
        embeddings=[],
    )
    corpus = build_metric_retrieval_corpus(report)
    row_view = next(view for view in corpus.retrieval_views if view.view_type == "table_row")

    units = metric_search_units(report, corpus)
    unit = next(item for item in units if item.segment_id == row_view.view_id)

    assert unit.segment_id == row_view.view_id
    assert unit.canonical_segment_id == "row"
    assert unit.matched_content == row_view.content
    assert unit.evidence_block_content == table_text
    assert unit.source_segment_ids == ["table", "row"]
    assert unit.matched_row_index == 2


def test_subset_namespace_and_combine_preserve_embedding_row_contract() -> None:
    first_document = _document(
        [
            _segment("a", "Water withdrawn was 10 m3."),
            _segment("b", "Energy consumed was 20 GJ."),
        ],
        document_id="first",
    )
    first = build_metric_retrieval_corpus(first_document)
    first_matrix = np.arange(
        len(first.retrieval_views) * 3,
        dtype=np.float32,
    ).reshape(len(first.retrieval_views), 3)
    attach_metric_embeddings(first, first_matrix, embedding_model="test-model")

    subset = subset_metric_retrieval_corpus(first, {"b"})
    assert subset is not None
    assert subset.source_segment_ids == ["b"]
    subset_embeddings = metric_embeddings(subset)
    assert subset_embeddings is not None
    assert subset_embeddings[0].shape == (1, 3)
    assert subset_embeddings[1] == [subset.retrieval_views[0].view_id]

    namespaced_first = namespace_metric_retrieval_corpus(first, "report-a")
    second = build_metric_retrieval_corpus(
        _document([_segment("a", "Water withdrawn was 11 m3.")], document_id="second")
    )
    attach_metric_embeddings(
        second,
        np.ones((len(second.retrieval_views), 3), dtype=np.float32),
        embedding_model="test-model",
    )
    namespaced_second = namespace_metric_retrieval_corpus(second, "report-b")
    combined = combine_metric_retrieval_corpora(
        [namespaced_first, namespaced_second],
        document_id="company",
    )

    assert combined is not None
    assert combined.source_segment_ids == ["report-a::a", "report-a::b", "report-b::a"]
    assert all(view.view_id.startswith(("report-a::", "report-b::")) for view in combined.retrieval_views)
    combined_embeddings = metric_embeddings(combined)
    assert combined_embeddings is not None
    assert combined_embeddings[0].shape == (3, 3)
    assert combined_embeddings[1] == [view.view_id for view in combined.retrieval_views]
