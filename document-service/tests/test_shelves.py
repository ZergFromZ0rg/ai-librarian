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
        ("All Tomorrows - C.M. Kosemen .pdf", "All Tomorrows", "C. M. Kosemen"),
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
    assert shelves.pick_author("Mark P.O. Morford", "Classical Mythology (Mark Morford).pdf") == "Mark P. O. Morford"
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


def test_author_count_does_not_count_roles_as_people():
    assert shelves.author_count("Jim Manis, ed.; Arthur Schopenhauer") == 2
    assert shelves.author_count("Jim Manis (ed.)") == 1
    assert shelves.author_count("Arthur Schopenhauer; T. Bailey Saunders, trans.") == 2
    assert shelves.author_count("Ann Smith; Bob Jones; Cy Lee, eds.") == 3
    assert shelves.author_count("Stolz, J., Fugelsang, J. & Fernandes, M.") == 3


# --- fixes 3, 4 and 5: surnames completed, spellings merged, authors in titles ---


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("E.M. Cioran", "E. M. Cioran"),
        ("E. M. Cioran", "E. M. Cioran"),
        ("J.R.R. Tolkien", "J. R. R. Tolkien"),
        ("C.M. Kosemen", "C. M. Kosemen"),
        ("Dr. Wasif Naeem", "Wasif Naeem"),
        ("Prof. Jane Doe", "Jane Doe"),
        ("Donald E. Westlake", "Donald E. Westlake"),
    ],
)
def test_one_author_is_one_spelling(raw, expected):
    assert shelves.normalize_author(raw) == expected


def test_authors_the_library_spells_two_ways_are_merged():
    names = ["E. M. Cioran"] * 3 + ["Emil Cioran", "Albert Camus", "Isaac Asimov", "Janet Asimov", "Camus"]
    assert shelves.canonical_authors(names) == {"Emil Cioran": "E. M. Cioran"}  # Janet is not Isaac; a surname alone is left
    assert shelves.canonical_authors(["Ann Lee", "Ann Lee"]) == {}
    # With no clear majority the fuller spelling wins.
    assert shelves.canonical_authors(["E. M. Cioran", "Emil M. Cioran"]) == {"E. M. Cioran": "Emil M. Cioran"}


def test_a_lone_surname_is_completed_from_the_opening_pages():
    assert shelves.complete_surname("Camus", ["THE STRANGER\n\nALBERT CAMUS\n\nTranslated by Matthew Ward"]) == "Albert Camus"
    assert shelves.complete_surname("Benatar", ["Better Never to Have Been\nDavid Benatar\nOxford University Press"]) == "David Benatar"
    assert shelves.complete_surname("Hamsun", ["Hunger by Knut Hamsun"]) == "Knut Hamsun"
    # Extraction can put the title and the author on one line: the title's words are not the name.
    assert shelves.complete_surname("Benatar", ["Better Never to Have Been David Benatar"]) == "David Benatar"
    assert shelves.complete_surname("Westlake", ["THE AX Donald E. Westlake"]) == "Donald E. Westlake"
    assert shelves.complete_surname("Tolkien", ["The Hobbit J. R. R. Tolkien"]) is None  # initials alone are not a completion
    assert shelves.complete_surname("Camus", ["ALBERT CAMUS", "A. Camus", "Albert Camus"]) == "Albert Camus"  # one person, spelled two ways
    assert shelves.complete_surname("Hamsun", ["Hunger\nby Hamsun"]) is None  # nothing beside it
    # Title and author run together by extraction: a word of the title is not a first name.
    assert shelves.complete_surname("Camus", ["A HAPPY DEATH CAMUS"], title="A Hapy Death") is None
    assert shelves.complete_surname("Camus", ["THE FALL ALBERT CAMUS"], title="The Fall") == "Albert Camus"
    assert shelves.complete_surname("Gray", ["John Gray, with Alasdair Gray"]) is None  # two people
    assert shelves.complete_surname("Camus", ["translated by Stuart Gilbert, Camus"]) is None
    assert shelves.complete_surname("Albert Camus", ["Albert Camus"]) is None  # already a full name


def test_a_title_that_is_a_body_of_work_names_its_author():
    known = ["Arthur Schopenhauer", "Albert Camus", "Camus"]
    assert shelves.author_in_title("Essays of Arthur Schopenhauer", known) == "Arthur Schopenhauer"
    assert shelves.author_in_title("Collected Works of Arthur Schopenhauer", known) == "Arthur Schopenhauer"
    assert shelves.author_in_title("Albert Camus's Notebooks", known) == "Albert Camus"
    assert shelves.author_in_title("Life of Albert Camus", known) is None  # about them, not by them
    assert shelves.author_in_title("Essays of Somebody Else", known) is None
    assert shelves.pick_author(None, "Essays of Arthur Schopenhauer.pdf", known_authors=known) == "Arthur Schopenhauer"


def test_pick_author_completes_a_lone_surname_from_the_title_page_when_the_library_does_not_know_it():
    pages = ["Better Never to Have Been\nDavid Benatar\nOxford University Press"]
    assert shelves.pick_author(None, "Better Never to Have Been - Benatar.pdf", lambda: pages) == "David Benatar"
    # The library's own spelling comes first.
    assert shelves.pick_author(None, "Better Never - Benatar.pdf", lambda: pages, ["Daniel Benatar"]) == "Daniel Benatar"
    # And nothing beside the surname: it stays a surname.
    assert shelves.pick_author(None, "Hunger - Hamsun.pdf", lambda: ["Hunger"]) == "Hamsun"


def test_a_title_that_names_its_author_as_part_of_itself_keeps_the_name():
    # Found on the live library: this became "Essays of".
    assert shelves.title_from_filename("Essays of Arthur Schopenhauer.pdf", "Arthur Schopenhauer") == "Essays of Arthur Schopenhauer"
    assert shelves.title_from_filename("Collected Works of Arthur Schopenhauer.epub", "Arthur Schopenhauer") == "Collected Works of Arthur Schopenhauer"
    assert shelves.title_from_filename("Life of Albert Camus.pdf", "Albert Camus") == "Life of Albert Camus"
    # An author glued on at the end of an ordinary title still comes off.
    assert shelves.title_from_filename("0 The Hobbit Tolkien.pdf", "J. R. R. Tolkien") == "0 The Hobbit"
    assert shelves.title_from_filename("Code Charles Petzold.pdf", "Charles Petzold") == "Code"


def test_a_completed_surname_needs_a_given_name_not_only_initials():
    assert shelves.complete_surname("Meerloo", ["The Rape of the Mind\nM. Meerloo\nPublished by Harper"]) is None
    assert shelves.complete_surname("Meerloo", ["Joost A. M. Meerloo"]) == "Joost A. M. Meerloo"
    assert shelves.complete_surname("Tolkien", ["The Hobbit J. R. R. Tolkien"]) is None  # initials alone: left as the surname
    assert shelves.complete_surname("Westlake", ["THE AX Donald E. Westlake"]) == "Donald E. Westlake"


def test_two_spellings_are_one_person_only_when_the_given_names_agree():
    assert shelves.same_person("J. R. R. Tolkien", "John Ronald Reuel Tolkien")
    assert shelves.same_person("E. M. Cioran", "Emil Cioran")
    assert shelves.same_person("Valery Vodovozov", "Velery Vodovozov")  # a typo on a cover
    assert not shelves.same_person("John Smith", "James Smith")
    assert not shelves.same_person("Mark Morford", "Michael Morford")
    assert not shelves.same_person("Camus", "Albert Camus")  # a lone surname says nothing about the given name


def test_a_name_takes_the_spelling_the_library_already_uses_for_that_person():
    known = ["J. R. R. Tolkien", "J. R. R. Tolkien", "John Ronald Reuel Tolkien", "Albert Camus"]
    assert shelves.spelling_in_use("John R. R. Tolkien", known) == "J. R. R. Tolkien"  # the one used most
    assert shelves.spelling_in_use("James Camus", known) is None  # not the same given name
    assert shelves.spelling_in_use("Jorge Luis Borges", known) is None
    assert shelves.spelling_in_use("John Ronald Reuel Tolkien", ["J. R. R. Tolkien"]) == "J. R. R. Tolkien"  # a tie is the library's own
