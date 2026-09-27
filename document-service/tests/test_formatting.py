"""Passage cleanup: no blank gaps, no extractor markup, tight lists."""

from chunking import (
    Block,
    bind_structural_context,
    collapse_blank_lines,
    join_blocks,
    parse_typed_blocks,
    tidy_markdown,
)


def test_collapse_blank_lines():
    assert collapse_blank_lines("a\n\n\n\nb\n \n\t\n\u00a0\nc") == "a\n\nb\n\nc"
    assert collapse_blank_lines("line one   \nline two\u200b") == "line one\nline two"
    assert collapse_blank_lines("") == ""


def test_figure_labels_are_dropped_and_prose_kept():
    labels = "Text.\n\n<!-- Start of picture text -->\nO C A<br>P<br>S R<br><!-- End of picture text -->\n\nFIG. 4.13"
    assert tidy_markdown(labels) == "Text.\n\nFIG. 4.13"
    prose = (
        "<!-- Start of picture text -->\nThe quick brown fox jumps over<br>"
        "the lazy dog in the morning sun<br><!-- End of picture text -->"
    )
    assert tidy_markdown(prose) == "The quick brown fox jumps over\nthe lazy dog in the morning sun"


def test_line_breaks_inside_table_cells_do_not_split_rows():
    table = "|_A_|_B_<br>_A_|\n|---|---|\n|T|T|"
    assert tidy_markdown(table) == "|_A_|_B_ _A_|\n|---|---|\n|T|T|"
    assert tidy_markdown("one<br/>two") == "one\ntwo"


def test_dropped_equation_gaps_leave_no_blank_runs():
    blocks = parse_typed_blocks([{"page": 1, "text": "So we allow:\n\n\n\n\nand equally:\n\n\n\nNote that"}])
    assert [block.text for block in blocks] == ["So we allow:", "and equally:", "Note that"]


def _block(index, kind, text):
    return Block(f"b{index}", kind, text, 1, 1)


def test_consecutive_list_items_join_tightly():
    blocks = [
        _block(0, "paragraph", "Further reading:"),
        _block(1, "paragraph", "- Euler, 1748."),
        _block(2, "paragraph", "- Gauss, 1801."),
        _block(3, "paragraph", "1. Then prose."),
        _block(4, "paragraph", "After."),
    ]
    assert join_blocks(blocks) == "Further reading:\n\n- Euler, 1748.\n- Gauss, 1801.\n1. Then prose.\n\nAfter."


def test_a_heading_right_above_a_table_stays_with_it():
    blocks = [
        _block(0, "paragraph", "Intro prose."),
        _block(1, "heading", "## 2026"),
        _block(2, "table", "| a | b |\n| --- | --- |\n| 1 | 2 |"),
    ]
    units = bind_structural_context(blocks)
    assert [unit.text for unit in units] == ["Intro prose.", "## 2026\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"]


def _hit(filename, text, page=2, **extra):
    return {"score": 0.5, "payload": {"filename": filename, "page": page, "text": text, "document_id": "d", **extra}}


def test_format_hits_tidies_old_passages_and_labels_locations():
    import app

    [pdf] = app.format_hits([_hit("book.pdf", "First para\n\n\n\nsecond para that runs on", lead_in="tail\n\n\nend")])
    assert pdf["text"] == "First para\n\nsecond para that runs on …"
    assert pdf["lead_in"] == "tail\n\nend"
    assert pdf["location"] == "p. 2" and pdf["file_type"] == "pdf"

    [slide] = app.format_hits([_hit("deck.pptx", "# Kickoff\n\nGoals for the quarter", page=1)])
    assert slide["text"] == "# Kickoff\n\nGoals for the quarter"  # no run-on ellipsis on a slide
    assert slide["location"] == "slide 1"

    [table] = app.format_hits([_hit("notes.docx", "| a | b |\n| --- | --- |\n| 1 | 2 |")])
    assert not table["text"].endswith("…")
