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


# --- the rules in the module docstring, one test per line of the table -------

import pytest  # noqa: E402

import doc_kind  # noqa: E402

FICTION = "She said that he was late and they had not asked her why it was so, but he would not say. " * 40
ESSAY = "The will is the thing in itself and representation is its appearance, which we must examine with care. " * 40


def _pages(text, count=8):
    return [text] * count


def test_fiction_is_told_from_narrative_prose_not_from_length():
    assert doc_kind.prose_score(_pages(FICTION)) > 30
    assert doc_kind.prose_score(_pages(ESSAY)) < 5
    assert doc_kind.prose_score(["too short to say"]) is None


def test_a_short_novel_without_an_isbn_is_a_book():
    # 56 pages, no copyright page: the old rule called this a document.
    assert doc_kind.detect_kind(_pages(FICTION), 56) == "book"
    assert doc_kind.detect_kind(_pages(FICTION), 56, author="Albert Camus") == "book"


def test_between_50_and_150_pages_a_book_needs_fiction_or_a_credited_author():
    assert doc_kind.detect_kind(_pages(ESSAY), 112, author="Albert Camus") == "book"  # a short essay book
    assert doc_kind.detect_kind(_pages(ESSAY), 112) == "document"  # an anonymous file of that length is not


def test_under_50_pages_only_fiction_is_a_book():
    assert doc_kind.detect_kind(_pages(FICTION), 30) == "book"  # a short story
    assert doc_kind.detect_kind(_pages(ESSAY), 30, author="Gilles Deleuze") == "document"  # an essay, even credited
    assert doc_kind.detect_kind(["Meeting notes"], 2, author="Boris") == "document"


def test_more_than_150_pages_is_a_book():
    assert doc_kind.detect_kind(_pages(ESSAY), 151) == "book"
    assert doc_kind.detect_kind(_pages(ESSAY), 150) == "document"  # not yet: 150 is "or less"


def test_a_copyright_page_marks_a_book_at_any_length():
    pages = ["Title", "Copyright © 2001 Jane Doe. All rights reserved. Published by Example Press.", "Contents"]
    assert doc_kind.detect_kind(pages, 12) == "book"
    assert doc_kind.detect_kind(["Copyright 2001 Jane Doe", "Page two"], 12) == "document"  # a notice alone is not enough


def test_a_datasheet_is_a_document_even_when_credited():
    pages = ["Datasheet\nPart number NE555\nRevision history", "Specification\nTroubleshooting", "..."]
    assert doc_kind.detect_kind(pages, 34, author="Texas Instruments") == "document"


def test_a_paper_has_an_abstract_with_references_keywords_or_a_contact_address():
    assert doc_kind.detect_kind(["Title\nAbstract. We propose.\nsatoshi@example.org", "x"], 9) == "paper"
    assert doc_kind.detect_kind(["Title\nAbstract\nWe study.\nKeywords: a, b", "x"], 9) == "paper"
    assert doc_kind.detect_kind(["Abstract\nWe study.", "x"], 9) == "document"  # nothing else says paper


def test_three_authors_on_a_short_text_that_is_not_fiction_make_a_paper():
    assert doc_kind.detect_kind(_pages(ESSAY), 40, authors=3) == "paper"
    assert doc_kind.detect_kind(_pages(ESSAY), 40, authors=2) == "document"
    assert doc_kind.detect_kind(_pages(ESSAY), 120, authors=3, author="A. Name") == "book"  # a long one is a book
    # Studies in Pessimism: 79 pages, credited to an editor, a translator and the author.
    assert doc_kind.detect_kind(_pages(ESSAY), 79, authors=3, author="Arthur Schopenhauer") == "book"
    assert doc_kind.detect_kind(_pages(FICTION), 40, authors=3) == "book"  # an anthology stays a book


def test_file_types_settle_some_kinds_outright():
    assert doc_kind.detect_kind(["x"], 1, file_type="epub") == "book"
    for office in ("excel", "csv", "powerpoint"):
        assert doc_kind.detect_kind(_pages(FICTION), 400, file_type=office) == "document"


@pytest.mark.parametrize(
    "pages, fiction, authors, title, author, expected",
    [
        (320, False, 0, "", "", "nonfiction"),
        (320, True, 0, "The Plague", "Albert Camus", "novel"),
        (151, True, 0, "", "", "novel"),
        (150, True, 0, "", "", "short_story"),  # "150 pages or less"
        (56, True, 0, "A Hapy Death", "Albert Camus", "short_story"),
        (320, True, 3, "Witches", "Isaac Asimov", "collection"),  # several authors
        (320, True, 0, "Asimov's Magical Worlds of Fantasy", "Isaac Asimov", "collection"),  # an editor's name
        (59, True, 0, "4 Plays", "Camus", "collection"),
        (None, True, 0, "A Novel Of Unknown Length", "", None),
        (None, True, 2, "", "", "collection"),  # a collection needs no page count
        (320, None, 0, "", "", None),  # nothing to judge: no label rather than a wrong one
    ],
)
def test_book_form(pages, fiction, authors, title, author, expected):
    assert doc_kind.book_form(pages, fiction, authors, title, author) == expected


def test_fiction_is_decided_by_genre_then_title_then_text():
    assert doc_kind.is_fiction(2.0, "Anything", genres=["Fantasy fiction"]) is True
    assert doc_kind.is_fiction(12.0, "Anything", genres=["Nonfiction"]) is False
    assert doc_kind.is_fiction(10.3, "Communion: A True Story") is False  # reads like a novel; says it is not
    assert doc_kind.is_fiction(4.0, "4 Plays") is True  # a hint lowers the bar
    assert doc_kind.is_fiction(6.5, "Untitled", subject="Literature & Fiction") is True
    assert doc_kind.is_fiction(6.5, "Untitled", subject="Philosophy") is False
    assert doc_kind.is_fiction(8.1, "Untitled") is True
    assert doc_kind.is_fiction(None, "Untitled") is None  # a scan: unknown, not "no"


def test_bylines_count_other_authors_but_not_the_translator_the_designer_or_the_author():
    front = [
        "Translated by Stuart Gilbert\nCover design by Helen Yentus",
        "THE WITCH by Jane Doe\nTHE GHOST by John Roe\nTHE ELF by Mary Poe\nEdited by Isaac Asimov",
        "A NOVEL by Albert Camus",
    ]
    assert doc_kind.distinct_bylines(front, author="Albert Camus") == 3
    assert doc_kind.distinct_bylines(front[:1] + front[2:], author="Albert Camus") == 0
