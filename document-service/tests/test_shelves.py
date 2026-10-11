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


@pytest.mark.parametrize(
    "filename, title, author",
    [
        # The commonest hand-kept style: "Title - Author", a lone surname included.
        ("A Hapy Death - Camus.pdf", "A Hapy Death", "Camus"),
        ("Anarchist Cookbook - William Powell.pdf", "Anarchist Cookbook", "William Powell"),
        ("All Tomorrows - C.M. Kosemen .pdf", "All Tomorrows", "C.M. Kosemen"),
        ("A History of Mathematics - 3rd Edition - Merzbach and Boyer.pdf", "A History of Mathematics - 3rd Edition", "Merzbach"),
        ("The Meme Machine (Susan Blackmore, Richard Dawkins).pdf", "The Meme Machine", "Susan Blackmore"),
        ("Electrical Engineering 101 [by Darren Ashby].pdf", "Electrical Engineering 101", "Darren Ashby"),
        ("The Psycho-Cybernetics by Maxwell Maltz (1).pdf", "The Psycho-Cybernetics", "Maxwell Maltz"),
        ("The Structure of Magic Book 2 (Richard Bandler, John Grinder).pdf.pdf", "The Structure of Magic Book 2", "Richard Bandler"),
        ("Introductory Econometrics- A Modern Approach (Jeffrey Wooldridge).pdf", "Introductory Econometrics: A Modern Approach", "Jeffrey Wooldridge"),
        # No credit, or one that cannot be told from a subtitle: nothing is invented.
        ("Compilers - Principles, Techniques, and Tools (2006).pdf", "Compilers - Principles, Techniques, and Tools (2006)", None),
        ("Make - Electronics.pdf", "Make - Electronics", None),
        ("Computer Networks - A Systems Approach.pdf", "Computer Networks - A Systems Approach", None),
        ("Home Expedient Firearms - 9mm SMG.pdf", "Home Expedient Firearms - 9mm SMG", None),
        ("Fullstack GPT - V1.0.0 - August 6, 2023.pdf", "Fullstack GPT - V1.0.0 - August 6, 2023", None),
        ("0 The Hobbit Tolkien.pdf", "0 The Hobbit Tolkien", None),
        # Titles read cleanly out of the file name.
        ("Beginner_s Guide to Reading Schematics (3rd Edition) ( PDFDrive.com ).pdf", "Beginner's Guide to Reading Schematics (3rd Edition)", None),
        ("automation-and-robotics.pdf", "Automation and Robotics", None),
        ("555-Timer-Datasheet.pdf", "555 Timer Datasheet", None),
        ("Bayes_Book.pdf", "Bayes Book", None),
        ("OrganicChemistry-10th-Edition.pdf", "OrganicChemistry 10th Edition", None),
    ],
)
def test_parse_filename(filename, title, author):
    assert shelves.parse_filename(filename) == (title, author)


def test_title_drops_an_author_the_files_metadata_corroborates():
    # "Title Surname" and "Surname - Title" can not be split from the name alone...
    assert shelves.title_from_filename("0 The Hobbit Tolkien.pdf") == "0 The Hobbit Tolkien"
    assert shelves.title_from_filename("Tolkien - The Silmarillion.pdf") == "Tolkien - The Silmarillion"
    # ...but once the file's own metadata names the author, they can.
    assert shelves.title_from_filename("0 The Hobbit Tolkien.pdf", "J. R. R. Tolkien") == "0 The Hobbit"
    assert shelves.title_from_filename("Tolkien - The Silmarillion.pdf", "J. R. R. Tolkien") == "The Silmarillion"
    assert shelves.title_from_filename("code-charles-petzold.pdf", "Charles Petzold") == "Code"
    assert shelves.title_from_filename("Tolkien.pdf", "J. R. R. Tolkien") == "Tolkien"  # never an empty title
    assert shelves.title_from_filename("A Hapy Death - Camus.pdf", "Albert Camus") == "A Hapy Death"


def test_a_lone_surname_is_completed_from_the_library():
    known = ["Albert Camus"] * 3 + ["Camille Camus", "Isaac Asimov"]
    assert shelves.resolve_surname("Camus", known) == "Albert Camus"
    assert shelves.resolve_surname("Asimov", known) == "Isaac Asimov"
    assert shelves.resolve_surname("Hamsun", known) == "Hamsun"  # nobody else known
    assert shelves.resolve_surname("Gray", ["John Gray", "Alasdair Gray"]) == "Gray"  # a tie stays a surname
    assert shelves.resolve_surname("Camus", ["Camus"]) == "Camus"  # a surname is not a full name


def test_pick_author_prefers_the_file_name_over_a_translator_in_the_metadata():
    known = ["Arthur Schopenhauer"]
    # A name typed into the file name beats an editor, translator or institution...
    assert shelves.pick_author("Christopher Janaway", "The Wisdom of Life - Schopenhauer.pdf", known_authors=known) == "Arthur Schopenhauer"
    assert shelves.pick_author("Florida Memorial", "The Making of the Slave - Willie Lynch.pdf") == "Willie Lynch"
    # ...unless the metadata agrees on the surname, in which case its spelling is kept.
    assert shelves.pick_author("Albert Camus", "The Fall - Camus.epub") == "Albert Camus"
    assert shelves.pick_author("Mark P.O. Morford", "Classical Mythology (Mark Morford).pdf") == "Mark P.O. Morford"
    # Without one: the metadata, then the title page, then nothing.
    assert shelves.pick_author("J. R. R. Tolkien", "0 The Hobbit Tolkien.pdf") == "J. R. R. Tolkien"
    assert shelves.pick_author("python-docx", "notes.docx", ["by Ann Writer"]) == "Ann Writer"
    assert shelves.pick_author(None, "notes.docx", lambda: ["no byline here"]) is None


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
