"""Guess what kind of document a PDF is: a book (textbooks included), a paper,
or anything else.

The library shows an owned/unowned mark on books only, so it needs to tell a
book from a paper without asking. The signals, strongest first:

- An ISBN in the front or back matter. Published books and textbooks almost
  always print one on the copyright page; papers essentially never do.
- A DOI, arXiv id, or an abstract followed by references in a short document:
  the shape of a paper.
- Length and structure: a long document with several "Chapter N" headings
  is a book; anything very long is most likely one too.

It only ever reads text already extracted during indexing, and the reader can
override the guess from the UI.
"""

import re
from typing import Iterable

KINDS = ("book", "paper", "document")

_ISBN = re.compile(r"\bISBN(?:-1[03])?[:\s]*(?:97[89][-\s]?)?\d[\d\-\s]{7,15}[\dXx]\b", re.IGNORECASE)
_DOI = re.compile(r"\b(?:doi\.org/|doi:\s*)10\.\d{4,9}/\S+", re.IGNORECASE)
_ARXIV = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}", re.IGNORECASE)
_ABSTRACT = re.compile(r"^\W*abstract\b", re.IGNORECASE | re.MULTILINE)
_REFERENCES = re.compile(r"^\W*(?:references|bibliography|works cited)\W*$", re.IGNORECASE | re.MULTILINE)
_CHAPTER = re.compile(r"^\W*chapter\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE | re.MULTILINE)

# Front and back matter: where copyright pages live.
_FRONT_PAGES = 15
_BACK_PAGES = 4


def detect_kind(page_texts: Iterable[str], page_count: int) -> str:
    """`page_texts` in page order; `page_count` is the PDF's real page count
    (which can exceed the texts given if some pages had no text layer)."""
    pages = [text or "" for text in page_texts]
    edges = pages[:_FRONT_PAGES] + pages[-_BACK_PAGES:]
    if any(_ISBN.search(text) for text in edges):
        return "book"

    full = "\n".join(pages)
    chapters = len(_CHAPTER.findall(full))
    if page_count >= 60 and chapters >= 3:
        return "book"

    first = "\n".join(pages[:2])
    looks_like_paper = bool(_DOI.search(first) or _ARXIV.search(first)) or (
        bool(_ABSTRACT.search(first)) and bool(_REFERENCES.search(full))
    )
    if looks_like_paper and page_count <= 80:
        return "paper"

    if page_count >= 150:
        return "book"
    return "document"
