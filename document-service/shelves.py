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


def author_from_filename(filename: str) -> Optional[str]:
    """``The Meme Machine (Susan Blackmore, Richard Dawkins).pdf`` ->
    "Susan Blackmore"; ``The Stranger by Albert Camus.epub`` -> "Albert Camus"."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "").replace("_", " ")
    match = re.search(r"\(([^()]+)\)\s*$", stem)
    if match:
        return normalize_author(match.group(1))
    match = re.search(r"\bby\s+(.+)$", stem, re.IGNORECASE)
    if match:
        return normalize_author(match.group(1))
    return None


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
