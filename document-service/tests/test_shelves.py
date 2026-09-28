"""Pure logic of virtual shelves (shelves.py): no models, no service."""

import pytest

import shelves


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Albert Camus", "Albert Camus"),
        ("Camus, Albert", "Albert Camus"),
        ("ALBERT CAMUS", "Albert Camus"),
        ("Susan Blackmore, Richard Dawkins", "Susan Blackmore"),
        ("Carl Sagan; Ann Druyan", "Carl Sagan"),
        ("Simone de Beauvoir", "Simone de Beauvoir"),
        ("python-docx", None),
        ("Microsoft Office User", None),
        ("admin", None),
        ("scan0042", None),
        ("someone@example.com", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_author(raw, expected):
    assert shelves.normalize_author(raw) == expected


def test_author_from_filename_and_title_page():
    assert shelves.author_from_filename("The Meme Machine (Susan Blackmore, Richard Dawkins).pdf") == "Susan Blackmore"
    assert shelves.author_from_filename("The_Stranger_by_Albert_Camus.pdf") == "Albert Camus"
    assert shelves.author_from_filename("coral-reef-restoration-1.docx") is None
    assert shelves.author_from_filename("Annual report (2024).pdf") is None
    assert shelves.author_from_text(["THE PLAGUE\n\nby Albert Camus\n\nTranslated"]) == "Albert Camus"
    assert shelves.author_from_text(["stand by me"]) is None
    assert shelves.author_from_text(["by the river side"]) is None
    assert shelves.author_from_text(["BY Albert Camus"]) == "Albert Camus"


def test_classify_subject_needs_a_clear_winner():
    subjects = {"Philosophy": [1.0, 0.0, 0.0], "Physics": [0.0, 1.0, 0.0], "History": [0.0, 0.0, 1.0]}
    assert shelves.classify_subject([0.9, 0.1, 0.0], subjects) == "Philosophy"
    assert shelves.classify_subject([0.5, 0.5, 0.0], subjects) is None  # a tie
    assert shelves.classify_subject([0.1, 0.1, 0.1], subjects, min_score=0.9) is None  # too weak
    assert shelves.classify_subject(None, subjects) is None


def test_suggest_shelf_by_kind():
    assert shelves.suggest_shelf("book", "Philosophy", "Albert Camus") == "Books/Philosophy/Albert Camus"
    assert shelves.suggest_shelf("paper", "Physics", "Ann Author") == "Papers/Physics"  # authors: books only
    assert shelves.suggest_shelf("document", None, None) == "Documents"
    assert shelves.suggest_shelf(None, "History", None) == "Documents/History"
    assert shelves.suggest_shelf("note", "History", None) == "Notes"
    assert shelves.suggest_shelf("book", "Art/Music", "A/B Name") == "Books/Art-Music/A-B Name"


def test_normalize_shelf():
    assert shelves.normalize_shelf(" Books › Philosophy / Camus ") == "Books/Philosophy/Camus"
    assert shelves.normalize_shelf("Books//Philosophy/") == "Books/Philosophy"
    assert shelves.normalize_shelf("  ") is None
    assert shelves.normalize_shelf(None) is None
    with pytest.raises(ValueError):
        shelves.normalize_shelf("/".join("abcdefg"))


def test_on_shelf_and_moving_a_shelf():
    doc = {"shelf": None, "shelf_suggested": "Books/Philosophy/Albert Camus"}
    assert shelves.on_shelf(doc, "Books/Philosophy")
    assert not shelves.on_shelf(doc, "Books/Phil")  # whole names only
    assert shelves.on_shelf({"shelf": "Keep", "shelf_suggested": "Books"}, "Keep")  # the reader's choice wins
    assert shelves.moved_shelf("Books/Philosophy/Albert Camus", "Books/Philosophy", "Books/Existentialism") == (
        "Books/Existentialism/Albert Camus"
    )
    assert shelves.moved_shelf("Books/Philosophy", "Books/Philosophy", "Favourites") == "Favourites"
    assert shelves.moved_shelf("Books/Physics", "Books/Philosophy", "X") is None
