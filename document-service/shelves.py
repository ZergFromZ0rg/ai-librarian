"""Virtual shelves: where a document would sit in, say, Books/Philosophy/Albert Camus.

Nothing on disk ever moves -- the library mount stays read-only. A shelf is a
path stored on the document record: ``shelf_suggested`` is recomputed from
what the document looks like, and ``shelf`` is the reader's own choice, which
always wins once set. The suggestion is built from three parts:

* the top level, from the document's kind (Books / Papers / Documents / Notes);
* a subject, by comparing the document's opening passages with a short list of
  subject descriptions using the embedding model the service already runs
  (no extra model, no LLM); left out when no subject is a clear match;
* for books, the author -- from the file's own metadata, its filename
  ("Title (Author).pdf", "Title by Author.pdf"), or a "by ..." line on its
  first page.

Everything here is pure (vectors and strings in, strings out) so it can be
unit-tested without models.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, List, Optional, Sequence

SEPARATOR = "/"
MAX_DEPTH = 6
MAX_PART_CHARS = 80

TOP_LEVEL = {"book": "Books", "paper": "Papers", "document": "Documents", "note": "Notes"}

# Top-level shelves for working documents, by document type (see doc_types.py).
# "other" keeps the original catch-all name so older shelves stay where they are.
DOCUMENT_TOP_LEVEL = {
    "paper": "Papers", "report": "Reports", "manual": "Manuals", "slides": "Slides",
    "data": "Data", "writing": "Writing", "legal": "Legal & Finance", "other": "Documents",
}
# Only these get a subject sub-shelf: the subject list describes topics of
# research and reference material, not invoices, decks or spreadsheets.
DOCUMENT_SUBJECT_TYPES = frozenset({"paper", "report", "manual", "other"})

# Label -> description embedded as a search query. Descriptions name the kind
# of material a shelf holds so a document's opening passages match them the
# way a passage matches a question.
SUBJECTS = {
    "Philosophy": "philosophy: existence, ethics, the absurd, meaning of life, metaphysics, logic, knowledge and truth",
    "Psychology": "psychology: the mind, behaviour, emotions, cognition, consciousness and mental health",
    "Religion & Spirituality": "religion and spirituality: God, faith, scripture, mysticism, prayer and belief",
    "Mathematics": "mathematics: numbers, proofs, theorems, geometry, algebra, calculus and equations",
    "Physics": "physics: forces, motion, energy, relativity, quantum mechanics, particles and gravitation",
    "Astronomy": "astronomy: stars, planets, galaxies, telescopes, the solar system and the universe",
    "Biology": "biology: living organisms, cells, genetics, evolution, ecology and marine life",
    "Chemistry": "chemistry: elements, molecules, reactions, compounds and the laboratory",
    "Computing": "computing: software, programming, algorithms, computers, data and networks",
    "Engineering & Energy": "engineering and energy: machines, construction, electricity, solar power and technology",
    "Medicine & Health": "medicine and health: disease, treatment, the body, nutrition and wellbeing",
    "History": "history: past events, eras, empires, wars, historical figures and chronology",
    "Politics & Society": "politics and society: government, power, law, social movements and communities",
    "Economics & Business": "economics and business: markets, money, companies, logistics, costs and management",
    "Work & Careers": "work and careers: employees, onboarding, workplace policies, teams and hiring",
    "Literature & Fiction": "literature and fiction: novels, stories, characters, poetry and literary criticism",
    "Art & Music": "art and music: painting, design, composers, performance and aesthetics",
    "Language": "language: linguistics, grammar, words, translation and writing",
    "Education": "education: teaching, learning, schools, students and study",
    "Food & Cooking": "food and cooking: recipes, baking, ingredients, kitchens and cuisine",
    "Travel & Places": "travel and places: journeys, countries, cities, geography and guides",
    "Nature & Environment": "nature and the environment: climate, ecosystems, conservation, oceans and wildlife",
    "Paranormal & Unexplained": "the paranormal and unexplained: aliens, abductions, UFOs, ghosts and the occult",
}

# A subject is only suggested when it clearly beats the runner-up; mixed or
# generic material stays one level up rather than being filed at random.
MIN_SUBJECT_SCORE = 0.45
MIN_SUBJECT_MARGIN = 0.015

# Author strings that are really the authoring tool or an account name.
_JUNK_AUTHORS = {
    "python-docx", "python-pptx", "openpyxl", "microsoft office user", "microsoft",
    "unknown", "anonymous", "administrator", "admin", "user", "author", "owner",
    "windows user", "calibre", "adobe", "acrobat", "pdf", "none", "n/a", "default",
}
_NAME_WORD = r"[A-Z][A-Za-z.'’\-]*"
_NAME = re.compile(rf"^{_NAME_WORD}(?: (?:[a-z]{{1,3}} )?{_NAME_WORD}){{1,4}}$")
# "Camus, Albert": a single word before the comma. Two or more words there
# ("Susan Blackmore, Richard Dawkins") is a list of authors instead.
_SURNAME_FIRST = re.compile(rf"^({_NAME_WORD}), ({_NAME_WORD}(?: {_NAME_WORD})*)$")
# Only "by" ignores case: the name itself must be capitalised, so a line like
# "by the river side" is not mistaken for an author.
_BY_LINE = re.compile(rf"^[ \t]*[Bb][Yy][ \t]+({_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{1,3}})[ \t]*$", re.MULTILINE)


def normalize_author(raw: Optional[str]) -> Optional[str]:
    """One clean author name ("Albert Camus") from a metadata or filename
    string, or None when it is missing, junk, or not name-shaped."""
    if not raw:
        return None
    text = " ".join(str(raw).replace("\x00", "").split()).strip(" .;,")
    if not text or len(text) > 120 or "@" in text or re.search(r"\d", text):
        return None
    if text.lower() in _JUNK_AUTHORS:
        return None
    # "Camus, Albert" (exactly one comma, surname first) -> "Albert Camus"
    match = _SURNAME_FIRST.match(text)
    if match and text.count(",") == 1:
        text = f"{match.group(2)} {match.group(1)}"
    else:
        # Several authors: keep the first.
        text = re.split(r"\s*(?:;|&|,|\band\b|\bwith\b)\s*", text)[0].strip()
    if text.isupper() or text.islower():
        text = text.title()
    return text if _NAME.match(text) else None


_FILE_EXTENSION = re.compile(r"\.(?:pdf|epub|mobi|azw3?|djvu|docx?|xlsx?|pptx?|txt|csv|md)$", re.IGNORECASE)
# Where a file was downloaded from, and the "(1)" a browser adds to a duplicate.
_DOWNLOAD_NOISE = re.compile(r"[\(\[]\s*(?:pdfdrive|z-?lib|libgen)[^)\]]*[\)\]]|\b(?:pdfdrive(?:\.com)?|z-library|libgen)\b", re.IGNORECASE)
_COPY_MARK = re.compile(r"\s*\(\s*\d{1,2}\s*\)\s*$")
_SPACED_DASH = re.compile(r"\s+[-–—]\s+")
_TRAILING_BRACKET = re.compile(r"\s*[\(\[]\s*(?:by\s+)?([^()\[\]]+?)\s*[\)\]]$", re.IGNORECASE)
_BY_AUTHOR = re.compile(r"\s+by\s+(.+)$", re.IGNORECASE)
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an|on|of)\s", re.IGNORECASE)
_LONE_SURNAME = re.compile(r"^[A-ZÀ-Þ][^\W\d_]{2,}(?:[-'’][^\W\d_]+)?$")
# Words that end a file name without being anyone's surname ("Make - Electronics").
_NOT_A_SURNAME = frozenset({
    "annotated", "book", "books", "collection", "complete", "cookbook", "copy", "datasheet", "draft",
    "edition", "electronics", "essays", "final", "guide", "handbook", "introduction", "lectures",
    "manual", "notes", "novel", "ocr", "part", "plays", "poems", "preview", "readings", "review",
    "sample", "scan", "scanned", "slides", "solutions", "stories", "summary", "textbook", "theory",
    "translated", "version", "vol", "volume",
})
_SMALL_WORDS = frozenset({"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "nor", "of", "on", "or", "the", "to", "vs", "with"})


def _author_candidate(text: str) -> Optional[str]:
    """``normalize_author``, but a lone capitalised surname ("Camus") passes
    too: that is how most file names in a hand-kept library credit an author.
    A lone surname must be the whole text (or two of them: "Merzbach and
    Boyer"), never the start of a list -- "Principles, Techniques, and Tools"
    is a subtitle, not an author."""
    name = normalize_author(text)
    if name:
        return name
    words = [word for word in re.split(r"\s+(?:and|&)\s+", str(text or "").strip(" .;,")) if word]
    if 1 <= len(words) <= 2 and all(
        _LONE_SURNAME.match(word) and word.lower() not in _NOT_A_SURNAME for word in words
    ):
        return words[0]
    return None


def _file_stem(filename: str) -> str:
    """A file name as readable text: no extension (however many), no download
    noise, and the separators people use for spaces and punctuation undone."""
    stem = (filename or "").strip()
    while True:
        shorter = _FILE_EXTENSION.sub("", stem)
        if shorter == stem:
            break
        stem = shorter
    hyphenated = " " not in stem.strip() and stem.count("-") >= 2  # "automation-and-robotics"
    stem = _DOWNLOAD_NOISE.sub(" ", stem)
    stem = _COPY_MARK.sub("", stem.strip())
    stem = re.sub(r"(?<=[A-Za-z])_s\b", "'s", stem)  # "Beginner_s Guide"
    stem = re.sub(r"_\s+", ": ", stem)  # "Psychology_ In and Out"
    stem = stem.replace("_", " ")
    stem = re.sub(r"(?<=\w)-\s+", ": ", stem)  # "Econometrics- A Modern Approach"
    if hyphenated:
        stem = stem.replace("-", " ")
    return " ".join(stem.split()).strip(" -–—.")


def _tidy_title(text: str) -> str:
    """Capitalise a title that came out of a file name in a single case
    ("automation and robotics"); one already in mixed case is left alone."""
    text = " ".join(text.split()).strip(" -–—.,:")
    if not text or text not in {text.lower(), text.upper()}:
        return text
    words = text.lower().split()
    return " ".join(
        word if (index and word in _SMALL_WORDS) else word[:1].upper() + word[1:]
        for index, word in enumerate(words)
    )


def _parse(filename: str) -> tuple:
    """(title, author, credit text) -- the credit text is the part of the file
    name that named the author(s), as written."""
    stem = _file_stem(filename)
    match = _TRAILING_BRACKET.search(stem)
    if match:
        name = normalize_author(match.group(1))
        if name:
            return _tidy_title(stem[: match.start()]), name, match.group(1)
    match = _BY_AUTHOR.search(stem)
    if match:
        name = normalize_author(match.group(1))
        if name:
            return _tidy_title(stem[: match.start()]), name, match.group(1)
    parts = [part.strip() for part in _SPACED_DASH.split(stem) if part.strip()]
    if len(parts) >= 2:
        # "Title - Author". The reverse ("Tolkien - The Silmarillion") reads the
        # same as "Computer Networks - A Systems Approach", so it is only
        # recognised when the file's own metadata names the author (see
        # title_from_filename).
        credited = None if _LEADING_ARTICLE.match(parts[-1]) else _author_candidate(parts[-1])
        if credited:
            return _tidy_title(" - ".join(parts[:-1])), credited, parts[-1]
    return _tidy_title(stem), None, None


def parse_filename(filename: str) -> tuple:
    """The (title, author) a file name spells out; author is None when it
    does not credit one.

    ``The Meme Machine (Susan Blackmore, Richard Dawkins).pdf``,
    ``The Stranger by Albert Camus.epub`` and ``A Happy Death - Camus.pdf`` (the
    commonest hand-kept style, a lone surname included) all give back the
    book's own title without the author tacked on. ``Title Surname.pdf`` with
    no separator can not be told apart from a longer title, so it is left
    whole."""
    title, author, _credit = _parse(filename)
    return title, author


def author_count(raw: Optional[str]) -> int:
    """How many people an author string names: "Susan Blackmore, Richard
    Dawkins" is 2, "Camus, Albert" is 1, "Stolz, J., Fugelsang, J. & Fernandes,
    M." is 3. 0 for nothing or a tool name."""
    text = " ".join(str(raw or "").replace("\x00", "").split()).strip(" .;,")
    if not text or text.lower() in _JUNK_AUTHORS:
        return 0
    if _SURNAME_FIRST.match(text) and text.count(",") == 1:
        return 1
    # Bare initials ("J.") belong to the name before them, not a person of their own.
    names = [part for part in re.split(r"\s*(?:;|&|,|\band\b)\s*", text) if re.search(r"[^\W\d_]{2,}", part.replace(".", ""))]
    return len(names)


def credit_count(filename: str) -> int:
    """How many authors a file name credits."""
    return author_count(_parse(filename)[2])


def author_from_filename(filename: str) -> Optional[str]:
    """``The Meme Machine (Susan Blackmore, Richard Dawkins).pdf`` ->
    "Susan Blackmore"; ``The Stranger by Albert Camus.epub`` -> "Albert Camus";
    ``A Happy Death - Camus.pdf`` -> "Camus"."""
    return parse_filename(filename)[1]


def _surname(name: str) -> str:
    words = re.sub(r"[.,]", " ", name or "").split()
    return words[-1].casefold() if words else ""


def title_from_filename(filename: str, author: Optional[str] = None) -> str:
    """A readable title for a file name. ``author`` is whatever else is known
    about who wrote it (the file's own metadata): a title that merely ends or
    starts with their name ("The Hobbit Tolkien", "Tolkien - The Silmarillion")
    loses it."""
    title, named = parse_filename(filename)
    if named or not author or not title:
        return title
    for name in (author, author.split()[-1].strip(".,")):
        if len(name) < 3:
            continue
        lead = re.match(rf"{re.escape(name)}\s+[-–—]\s+(.+)$", title, re.IGNORECASE)
        if lead:
            return lead.group(1).strip()
        if title.lower().endswith(" " + name.lower()):
            rest = re.sub(r"\s+by$", "", title[: -len(name)].rstrip(" -–—,:"), flags=re.IGNORECASE).strip()
            if re.search(r"[^\W\d_]", rest):
                return rest
    return title


def resolve_surname(surname: str, known_authors: Iterable[str]) -> str:
    """A lone surname ("Camus") as the full name the library already uses for
    it ("Albert Camus"), when that is unambiguous; else the surname as given."""
    counts: dict = {}
    for name in known_authors:
        if name and " " in name.strip() and _surname(name) == surname.casefold():
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: -item[1])
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return surname
    return ranked[0][0]


def pick_author(embedded: Optional[str], filename: str, first_pages=None, known_authors: Iterable[str] = ()) -> Optional[str]:
    """The best author available.

    A name in the file name wins: someone typed it on purpose, while a file's
    own metadata is often a translator, an editor or an institution ("Florida
    Memorial"). The metadata still supplies the fuller spelling when it agrees
    on the surname, and a lone surname is completed from the full name the
    library already uses for it. With no author in the file name: the file's
    metadata, then a "by ..." line on the title page (a callable, so pages
    are only read when needed)."""
    named = author_from_filename(filename)
    metadata = normalize_author(embedded)
    if named:
        if metadata and _surname(metadata) == _surname(named):
            return metadata
        return named if " " in named else resolve_surname(named, known_authors)
    if metadata:
        return metadata
    pages = first_pages() if callable(first_pages) else first_pages
    return author_from_text(pages or [])


def author_from_text(first_pages: Iterable[str]) -> Optional[str]:
    """A ``by Albert Camus`` line on a title page."""
    for text in first_pages:
        match = _BY_LINE.search(text or "")
        if match:
            return normalize_author(match.group(1))
    return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def mean_vector(vectors: List[Sequence[float]]) -> Optional[List[float]]:
    if not vectors:
        return None
    width = len(vectors[0])
    return [sum(vector[i] for vector in vectors) / len(vectors) for i in range(width)]


def classify_subject(
    document_vector: Optional[Sequence[float]],
    subject_vectors: dict,
    min_score: float = MIN_SUBJECT_SCORE,
    min_margin: float = MIN_SUBJECT_MARGIN,
) -> Optional[str]:
    """The subject whose description best matches the document, if it is a
    clear winner; None for a weak or ambiguous match."""
    if not document_vector or not subject_vectors:
        return None
    scored = sorted(
        ((_cosine(document_vector, vector), label) for label, vector in subject_vectors.items()),
        reverse=True,
    )
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else -1.0
    if best_score < min_score or best_score - runner_up < min_margin:
        return None
    return best


def clean_part(part: str) -> str:
    part = " ".join(str(part).replace(SEPARATOR, "-").replace("›", "-").split())
    return part.strip(" .")[:MAX_PART_CHARS].strip()


def suggest_shelf(kind: Optional[str], subject: Optional[str], author: Optional[str]) -> str:
    parts = [TOP_LEVEL.get(kind or "", "Documents")]
    if subject and kind != "note":
        parts.append(subject)
    if author and kind == "book":
        parts.append(author)
    return SEPARATOR.join(clean_part(part) for part in parts if clean_part(part))


def suggest_document_shelf(doc_type: Optional[str], subject: Optional[str]) -> str:
    """Where a working document of `doc_type` sits, e.g. Papers/Physics."""
    parts = [DOCUMENT_TOP_LEVEL.get(doc_type or "", "Documents")]
    if subject and doc_type in DOCUMENT_SUBJECT_TYPES:
        parts.append(subject)
    return SEPARATOR.join(clean_part(part) for part in parts if clean_part(part))


def normalize_shelf(path: Optional[str]) -> Optional[str]:
    """A reader-typed shelf ("Books › Philosophy / Camus ") in canonical form,
    or None for an empty one. Raises ValueError when it is too deep."""
    if path is None:
        return None
    parts = [clean_part(part) for part in re.split(r"[/›>]", str(path))]
    parts = [part for part in parts if part]
    if not parts:
        return None
    if len(parts) > MAX_DEPTH:
        raise ValueError(f"a shelf can be at most {MAX_DEPTH} levels deep")
    return SEPARATOR.join(parts)


def effective_shelf(doc: dict) -> Optional[str]:
    return doc.get("shelf") or doc.get("shelf_suggested")


def on_shelf(doc: dict, shelf: str) -> bool:
    """Whether the document sits on ``shelf`` or anywhere below it."""
    current = effective_shelf(doc) or ""
    return current == shelf or current.startswith(shelf + SEPARATOR)


def moved_shelf(current: str, source: str, target: str) -> Optional[str]:
    """Where a document on ``current`` lands when shelf ``source`` is renamed
    or moved to ``target`` (sub-shelves come along); None if unaffected."""
    if current == source:
        return target
    if current.startswith(source + SEPARATOR):
        return target + current[len(source):]
    return None
