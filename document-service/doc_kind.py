"""Guess what a file is -- a book, a paper, or a working document -- and, for a
book, what form it takes (novel, short story, collection, non-fiction).

The Library holds books; papers and documents are tracked separately in the
Documents library. Page count is the prior, not the verdict:

  Evidence, strongest first                                   -> kind
  ------------------------------------------------------------------------
  an ISBN or a copyright page; several "Chapter N" headings   -> book
  a DOI / arXiv id, or an abstract with references, keywords
    or a contact address; three or more authors on a short
    text that is not fiction                                  -> paper
  manual / legal / report vocabulary on a short text          -> document
  more than 150 pages                                         -> book
  50 to 150 pages: fiction, or a credited author              -> book
                   otherwise                                  -> document
  under 50 pages: fiction                                     -> book (a short story)
                  otherwise                                   -> document

and, for a book (the reader can correct every one of these):

  not fiction                                                 -> non-fiction
  fiction by several authors (credited, or a different byline
    on each story), or titled "Stories", "Plays", "Asimov's..." -> collection
  fiction of 150 pages or less                                -> short story
  fiction of more than 150 pages                              -> novel

"Fiction" is told from the text: narrative prose is thick with pronouns and
past tense ("she said", "he was"), which essays, textbooks and manuals are
not. A genre from the catalogue or a hint in the title ("A True Story",
"Plays") settles the borderline cases. It only ever reads text already
extracted during indexing.
"""

import re
from dataclasses import dataclass
from typing import Iterable, Optional

import doc_types

KINDS = ("book", "paper", "document")
FORMS = ("novel", "short_story", "collection", "nonfiction")
FORM_LABELS = {"novel": "Novel", "short_story": "Short story", "collection": "Collection", "nonfiction": "Non-fiction"}

# Bump when the rules above change: the startup backfill re-reads every
# record that was classified under an older version.
RULES_VERSION = 2

SHORT_STORY_MAX_PAGES = 150  # "150 pages or less is a short story"
SHORT_WORK_PAGES = 50  # under this a file is a document unless it reads as fiction
MIN_PROSE_WORDS = 3000  # about ten pages: less text than this says nothing about style
FICTION_SCORE = 8.0  # narrative score that reads as fiction with nothing else to go on
FICTION_SCORE_SUPPORTED = 6.0  # ...when the subject says literature
FICTION_SCORE_HINTED = 3.0  # ...when the title says "Stories" or "Plays"
THREE_AUTHORS = 3

_ISBN = re.compile(r"\bISBN(?:-1[03])?[:\s]*(?:97[89][-\s]?)?\d[\d\-\s]{7,15}[\dXx]\b", re.IGNORECASE)
_DOI = re.compile(r"\b(?:doi\.org/|doi:\s*)10\.\d{4,9}/\S+", re.IGNORECASE)
_ARXIV = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}", re.IGNORECASE)
_ABSTRACT = re.compile(r"^\W*abstract\b", re.IGNORECASE | re.MULTILINE)
_REFERENCES = re.compile(r"^\W*(?:references|bibliography|works cited)\W*$", re.IGNORECASE | re.MULTILINE)
_KEYWORDS = re.compile(r"^\W*(?:keywords?|index terms)\b", re.IGNORECASE | re.MULTILINE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_CHAPTER = re.compile(r"^\W*chapter\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE | re.MULTILINE)
# A copyright page: the notice, the rights line, and a publisher or printing.
_COPYRIGHT = re.compile(r"(?:©|\(c\)|\bcopyright\b)", re.IGNORECASE)
_RIGHTS = re.compile(r"\ball rights reserved\b", re.IGNORECASE)
_IMPRINT = re.compile(r"\b(?:published by|first published|first edition|printed in|publisher|press|publishing|edition)\b", re.IGNORECASE)

_FICTION_TITLE = re.compile(
    r"\b(?:novel|novella|stories|story|tales?|plays?|poems?|poetry|fiction|fantasy|fables?|anthology|saga|mystery|thriller|horror)\b",
    re.IGNORECASE,
)
_NONFICTION_TITLE = re.compile(
    r"\b(?:true story|memoirs?|biography|autobiography|history of|introduction to|guide|handbook|manual|textbook|how to|essays?)\b",
    re.IGNORECASE,
)
_COLLECTION_TITLE = re.compile(r"\b(?:stories|tales|plays|poems|anthology|collected|selected|omnibus)\b", re.IGNORECASE)
_FICTION_GENRE = re.compile(r"(?<![a-z-])(?:fiction|novels?|stories|short stories|fantasy|mystery|thriller|horror|poetry|drama|plays)\b", re.IGNORECASE)
_NONFICTION_GENRE = re.compile(r"\bnon-?fiction\b", re.IGNORECASE)

# "The Witch, by Jane Doe" on a contents or title page. Credits that are not
# the author of the text ("Translated by", "Cover design by") do not count.
_BYLINE_NAME = r"[A-Z][A-Za-z.'’\-]+(?:[ \t]+(?:[a-z]{1,3}[ \t]+)?[A-Z][A-Za-z.'’\-]+){1,3}"
_BYLINE = re.compile(rf"^(?P<before>[^\n]{{0,80}}?)\b[Bb][Yy]\b[ \t]+(?P<name>{_BYLINE_NAME})[ \t*_.]*$", re.MULTILINE)
_NOT_AN_AUTHOR_CREDIT = re.compile(
    r"translat|illustrat|edit|cover|design|introduc|forewor|afterwor|photograph|compiled|narrat|read\b|"
    r"typeset|prefac|artwork|art\b|drawn|published|©|copyright",
    re.IGNORECASE,
)
ANTHOLOGY_BYLINES = 3  # this many different authors in the front matter: a collection
_BYLINE_PAGES = 80

_NARRATIVE = frozenset("he she him her his hers they them their said asked replied was were had would could".split())
_FIRST_PERSON = frozenset("i me my mine we our us".split())
_WORD = re.compile(r"[A-Za-z']+")

# Front and back matter: where copyright pages live.
_FRONT_PAGES = 15
_BACK_PAGES = 4


@dataclass(frozen=True)
class Verdict:
    kind: str
    prose_score: Optional[float]  # narrative score, None when there was too little text to judge
    authors: int = 0  # people credited: in the metadata, the file name, or a byline per story


def prose_score(page_texts: Iterable[str]) -> Optional[float]:
    """How much the text reads as narrative fiction: the share of words that
    are third-person pronouns and past-tense narration, plus half the share of
    first-person ones (a narrator's "I"). Novels score 8 and up; essays,
    textbooks and manuals score under 5. None when there is too little text.

    Front matter is skipped and a long book is sampled from its opening, so a
    copyright page or a bibliography does not tilt it."""
    pages = [text or "" for text in page_texts]
    body = pages[3:83] if len(pages) > 8 else pages
    words = [word.lower() for word in _WORD.findall("\n".join(body))]
    if len(words) < MIN_PROSE_WORDS:
        return None
    narrative = sum(1 for word in words if word in _NARRATIVE)
    first_person = sum(1 for word in words if word in _FIRST_PERSON)
    return round(100.0 * (narrative + 0.5 * first_person) / len(words), 2)


def genres_say_fiction(genres: Iterable[str]) -> Optional[bool]:
    """True/False when the catalogue's genres settle it, None when they do not."""
    labels = [str(genre) for genre in genres or [] if genre]
    if any(_NONFICTION_GENRE.search(label) for label in labels):
        return False
    if any(_FICTION_GENRE.search(label) for label in labels):
        return True
    return None


def is_fiction(score: Optional[float], title: str = "", subject: Optional[str] = None, genres: Iterable[str] = ()) -> Optional[bool]:
    """Whether a book is fiction; None when there is nothing to go on (a scan
    with no text, a catalogue entry). A catalogue genre decides; then a title
    that says what it is; then the text, with a lower bar when the subject
    agrees."""
    by_genre = genres_say_fiction(genres)
    if by_genre is not None:
        return by_genre
    if _NONFICTION_TITLE.search(title or ""):
        return False
    if score is None:
        return None
    if _FICTION_TITLE.search(title or "") and score >= FICTION_SCORE_HINTED:
        return True
    if subject == "Literature & Fiction" and score >= FICTION_SCORE_SUPPORTED:
        return True
    return score >= FICTION_SCORE


def distinct_bylines(page_texts: Iterable[str], author: str = "") -> int:
    """How many different authors the front matter credits ("... by Jane Doe"),
    not counting the book's own author or credits such as a translator."""
    pages = [text or "" for text in page_texts][:_BYLINE_PAGES]
    surname = (author or "").replace(".", " ").split()[-1].lower() if (author or "").strip(" .") else ""
    names = set()
    for match in _BYLINE.finditer("\n".join(pages)):
        if _NOT_AN_AUTHOR_CREDIT.search(match.group("before")):
            continue
        name = " ".join(match.group("name").split()).strip(" .").lower()
        if surname and name.split()[-1] == surname:
            continue
        names.add(name)
    return len(names)


def book_form(pages: Optional[int], fiction: Optional[bool], authors: int = 0, title: str = "", author: str = "") -> Optional[str]:
    """Novel, short story, collection or non-fiction, for a book; None while
    it is unknown whether it is fiction, or how long a work of fiction is."""
    if fiction is None:
        return None
    if not fiction:
        return "nonfiction"
    surname = (author or "").replace(".", " ").split()[-1] if (author or "").strip(" .") else ""
    # "Asimov's Magical Worlds of Fantasy": an editor's name on a book of other people's stories.
    edited = bool(surname) and re.search(rf"\b{re.escape(surname)}['’]s\b", title or "", re.IGNORECASE) is not None
    if authors >= 2 or edited or _COLLECTION_TITLE.search(title or ""):
        return "collection"
    if not pages:
        return None
    return "novel" if pages > SHORT_STORY_MAX_PAGES else "short_story"


def _has_copyright_page(edges: list) -> bool:
    return any(
        _COPYRIGHT.search(text) and _RIGHTS.search(text) and _IMPRINT.search(text) for text in edges
    )


def classify(
    page_texts: Iterable[str],
    page_count: int,
    *,
    file_type: Optional[str] = None,
    author: str = "",
    authors: int = 0,
    title: str = "",
    subject: Optional[str] = None,
    genres: Iterable[str] = (),
) -> Verdict:
    """`page_texts` in page order; `page_count` is the PDF's real page count
    (which can exceed the texts given if some pages had no text layer).
    `author` is the named author when one is known (file name, file properties
    or title page) and `authors` how many the metadata or file name credit."""
    pages = [text or "" for text in page_texts]
    score = prose_score(pages)
    if file_type == "epub":
        return _book(pages, score, authors, author, title, subject, genres)
    if file_type in {"excel", "csv", "powerpoint"}:
        return Verdict("document", score, authors)

    edges = pages[:_FRONT_PAGES] + pages[-_BACK_PAGES:]
    if any(_ISBN.search(text) for text in edges) or _has_copyright_page(edges):
        return _book(pages, score, authors, author, title, subject, genres)

    full = "\n".join(pages)
    if page_count >= 60 and len(_CHAPTER.findall(full)) >= 3:
        return _book(pages, score, authors, author, title, subject, genres)

    fiction = bool(is_fiction(score, title, subject, genres))
    first = "\n".join(pages[:2])
    if page_count <= SHORT_STORY_MAX_PAGES and not fiction:
        paper = bool(_DOI.search(first) or _ARXIV.search(first)) or (
            bool(_ABSTRACT.search(first))
            and (bool(_REFERENCES.search(full)) or bool(_KEYWORDS.search(first)) or bool(_EMAIL.search(first)))
        )
        if paper or (authors >= THREE_AUTHORS and page_count <= 80):
            return Verdict("paper", score, authors)

    if page_count <= SHORT_STORY_MAX_PAGES and not fiction and doc_types.reads_as_working_document(pages[:3], page_count):
        return Verdict("document", score, authors)

    if page_count > SHORT_STORY_MAX_PAGES or (page_count >= SHORT_WORK_PAGES and (fiction or author)) or (
        page_count < SHORT_WORK_PAGES and fiction
    ):
        return _book(pages, score, authors, author, title, subject, genres)
    return Verdict("document", score, authors)


def _book(pages, score, authors, author, title, subject, genres) -> Verdict:
    """A book verdict, carrying how many authors the text credits so a
    collection can be told from a novel later."""
    if is_fiction(score, title, subject, genres):
        authors = max(authors, distinct_bylines(pages, author))
    return Verdict("book", score, authors)


def detect_kind(page_texts: Iterable[str], page_count: int, **context) -> str:
    return classify(page_texts, page_count, **context).kind
