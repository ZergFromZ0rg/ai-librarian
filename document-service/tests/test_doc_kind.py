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
        (150, True, 0, "", "", "novella"),  # "150 pages or less" is not a novel
        (50, True, 0, "", "", "novella"),
        (49, True, 0, "", "", "short_story"),
        (56, True, 0, "A Hapy Death", "Albert Camus", "novella"),
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


# --- fix 1 and 2: two signals, a grey zone, a language guard, estimated length --

PRESENT_TENSE = "She says that he is late and they are not asking her why it is so, but he does not say. " * 40
SPANISH = "El hombre dijo que ella estaba cansada y que los dos habian llegado tarde a la casa de su madre en la ciudad. " * 60


def test_the_tense_of_the_verbs_is_a_second_signal():
    fiction = doc_kind.text_profile(_pages(FICTION))
    essay = doc_kind.text_profile(_pages(ESSAY))
    present = doc_kind.text_profile(_pages(PRESENT_TENSE))
    assert fiction.past > 80
    assert essay.past is None or essay.past < 40
    assert present.score > 8 and present.past < 20  # lots of pronouns, but in the present
    assert doc_kind.is_fiction(present.score, past=present.past) is False
    assert doc_kind.is_fiction(fiction.score, past=fiction.past) is True
    # Without a tense reading the narrative score alone decides, as before.
    assert doc_kind.is_fiction(9.0) is True and doc_kind.is_fiction(7.0) is False


@pytest.mark.parametrize(
    "score, past, expected",
    [
        (13.0, 93, False),  # The Stranger
        (8.1, 51, True),  # The Ax: first person, only half past tense
        (8.7, 60, True),  # The Fall
        (7.3, 48, True),  # How to Talk to Anyone: a self-help book that says "you" and "he said"
        (7.2, 23, False),  # The Temptation to Exist: aphorisms in the present
        (5.5, 36, True),  # The Rape of the Mind
        (4.5, 12, False),  # Schopenhauer
        (None, None, True),  # a scan, or another language
    ],
)
def test_fiction_in_doubt_marks_the_grey_zone_for_a_model(score, past, expected):
    assert doc_kind.fiction_in_doubt(score, past) is expected


def test_the_borderline_novels_of_the_real_library_are_still_fiction_and_the_essays_are_not():
    assert doc_kind.is_fiction(8.09, past=51) is True  # The Ax
    assert doc_kind.is_fiction(8.67, past=60) is True  # The Fall
    assert doc_kind.is_fiction(7.31, past=48) is False  # How to Talk to Anyone
    assert doc_kind.is_fiction(7.25, past=23) is False  # The Temptation to Exist
    assert doc_kind.is_fiction(6.12, past=54) is False  # Personal Writings
    assert doc_kind.is_fiction(8.33, past=76) is True  # The Fellowship of the Ring


def test_text_in_another_language_is_not_judged_at_all():
    profile = doc_kind.text_profile(_pages(SPANISH))
    assert (profile.english, profile.score, profile.past) == (False, None, None)
    assert doc_kind.is_fiction(profile.score, "Pedro Paramo") is None  # unknown, not "non-fiction"
    assert doc_kind.classify(_pages(SPANISH), 99, author="Juan Rulfo", file_type="epub").kind == "book"


def test_length_is_the_printed_page_count_else_an_estimate_from_the_words():
    assert doc_kind.length_pages(300, 90_000, 77) == 300  # the catalogue knows
    assert doc_kind.length_pages(None, 36_000, 77) == 124  # The Stranger: 77 epub pages, 123 in print
    assert doc_kind.length_pages(None, 500, 4) == 4  # too little text to estimate from
    assert doc_kind.length_pages(None, 0, None) is None


def test_a_model_breaks_ties_only_where_the_length_prior_decided():
    # 112 pages, an author but nothing else: the prior says book; a model that read it says document.
    assert doc_kind.detect_kind(_pages(ESSAY), 112, author="A. Name") == "book"
    assert doc_kind.detect_kind(_pages(ESSAY), 112, author="A. Name", ai={"kind": "document"}) == "document"
    # An anonymous 30-page text is a document by default; a model may say it is a book.
    assert doc_kind.detect_kind(_pages(ESSAY), 30, ai={"kind": "book"}) == "book"
    # Evidence in the text is not overruled: a copyright page is a copyright page.
    pages = ["Title", "Copyright © 2001 Jane Doe. All rights reserved. Published by Example Press."]
    assert doc_kind.detect_kind(pages, 30, ai={"kind": "document"}) == "book"
    assert doc_kind.detect_kind(["Title\nAbstract. We study.\nKeywords: a", "x"], 9, ai={"kind": "book"}) == "paper"
    # A model's fiction verdict stands in for the text's, and a genre still outranks it.
    assert doc_kind.is_fiction(4.0, "Untitled", ai=True) is True
    assert doc_kind.is_fiction(11.0, "Untitled", ai=False) is False
    assert doc_kind.is_fiction(11.0, "Untitled", genres=["Nonfiction"], ai=True) is False


def test_the_verdict_says_whether_the_length_prior_alone_decided():
    assert doc_kind.classify(_pages(ESSAY), 112, author="A. Name").weak is True
    assert doc_kind.classify(_pages(ESSAY), 30).weak is True
    assert doc_kind.classify(_pages(FICTION), 112).weak is False  # the text says fiction
    assert doc_kind.classify(_pages(ESSAY), 300).weak is False
