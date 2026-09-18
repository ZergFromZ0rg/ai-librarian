import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_kind import detect_kind  # noqa: E402


def test_isbn_on_the_copyright_page_marks_a_book():
    pages = ["Linear Algebra Done Right", "Copyright 2015\nISBN 978-3-319-11079-0", "Contents"]
    assert detect_kind(pages, 3) == "book"


def test_isbn_in_the_back_matter_counts_too():
    pages = ["text"] * 40 + ["Colophon. ISBN: 0-306-40615-2"]
    assert detect_kind(pages, 41) == "book"


def test_long_document_with_chapters_is_a_book():
    pages = [f"Chapter {n}\n\nBody text." if n % 10 == 0 else "Body text." for n in range(80)]
    assert detect_kind(pages, 80) == "book"


def test_abstract_and_references_mark_a_paper():
    pages = ["Attention Is All You Need\n\nAbstract\nWe propose...", "Method", "References\n[1] ..."]
    assert detect_kind(pages, 11) == "paper"


def test_doi_on_the_first_page_marks_a_paper():
    assert detect_kind(["A Study\nhttps://doi.org/10.1145/3292500.3330701", "..."], 9) == "paper"


def test_very_long_untitled_scan_is_treated_as_a_book():
    assert detect_kind(["x"] * 10, 300) == "book"


def test_short_notes_are_a_plain_document():
    assert detect_kind(["Week one: Camus.", "Week two: retrieval."], 2) == "document"


def test_a_long_paper_with_isbn_like_numbers_elsewhere_is_not_misread():
    pages = ["Abstract\nWe study...", "Results 978 3 319"] + ["body"] * 10 + ["References\n..."]
    assert detect_kind(pages, 13) == "paper"
