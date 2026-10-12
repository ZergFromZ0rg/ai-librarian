"""AI sort: check a book's details against the file, the online catalogue and a
local model, and say what can be fixed with confidence.

Nothing here touches the database or the network on its own -- ``plan_book``
takes the evidence and the answers and returns a plan -- so every rule can be
tested without a model. ``ask_local_model`` is the one call out, and it only
ever talks to the reader's own Ollama.

A model is a witness, not a judge. Its answers are only used when they can be
checked against the evidence the reader's own files and the catalogue supplied:

* a name it gives for the author must be written somewhere in that evidence;
* a catalogue book it says is the same work must be one it was shown, and its
  title must be close to ours;
* a new title must be close to the old one (it may fix a typo, not rewrite);
* two independent sources agreeing -- the model and the catalogue, or the model
  and the title page -- is what lets a change apply by itself. One source
  alone becomes a suggestion for the reader to accept or skip.

The reader's own edits are never touched, and everything applied can be undone.
"""

import difflib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import catalogue
import doc_kind
import shelves

# What a model's reply must look like. Ollama turns this into a grammar, so a
# small local model cannot answer in any other shape.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "author": {"type": ["string", "null"]},
        "same_work_as": {"type": ["integer", "null"]},
        "is_fiction": {"type": ["boolean", "null"]},
        "kind": {"type": ["string", "null"], "enum": ["book", "paper", "document", None]},
        "confidence": {"type": "number"},
        "quote": {"type": ["string", "null"]},
    },
    "required": ["title", "author", "same_work_as", "is_fiction", "kind", "confidence", "quote"],
}

SYSTEM_PROMPT = (
    "You check the catalogue record of one file in a person's own library. "
    "Use ONLY the evidence you are given and never invent facts: when the evidence "
    "does not support an answer, answer null. Reply with JSON only.\n"
    "- title: the work's real title, without the author, edition or file-name noise. "
    "Fix typos and capitalisation of the current title; do not replace it with a different title.\n"
    "- author: the full name of the person who WROTE the work (first author only), exactly as written in the evidence. "
    "Never a translator, editor, introducer, illustrator or publisher: a name that appears only after "
    "\"translated by\", \"edited by\", \"selected by\" or \"introduction by\" is not the author.\n"
    "- same_work_as: the number of the catalogue match that is the SAME work as this file, or null if none is. "
    "A different book with a similar title or a study guide is not the same work.\n"
    "- is_fiction: true for a novel, novella, short story, play or poetry; false for non-fiction, "
    "essays, philosophy, textbooks, memoir presented as fact; null if you cannot tell.\n"
    "- kind: \"book\" for a book of any length, \"paper\" for an academic paper, \"document\" for a manual, "
    "datasheet, report, form or notes; null if you cannot tell.\n"
    "- confidence: 0 to 1, how sure you are.\n"
    "- quote: 3 to 8 words copied EXACTLY from the evidence that name the author, or null."
)

OPENING_CHARS = 1100
EXCERPT_CHARS = 450
SIMILAR_TITLE = 0.72  # how close a corrected title must be to the current one
SAME_WORK_TITLE = 0.8  # how close a catalogue title must be to ours to be the same work
MODEL_CONFIDENCE = 0.7  # a fiction verdict is only kept above this


class AiError(RuntimeError):
    """The local model could not be reached or did not answer usably."""


# ------------------------------------------------------------ the model --
def ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434").strip().rstrip("/")


def list_local_models(timeout: float = 3.0) -> List[str]:
    """Names of the models in the reader's own Ollama; [] when it is not
    configured or not reachable. Embedding-only models are left out."""
    url = ollama_url()
    if not url:
        return []
    import httpx

    try:
        response = httpx.get(f"{url}/api/tags", timeout=timeout)
        response.raise_for_status()
        names = [entry.get("name") or entry.get("model") for entry in response.json().get("models") or []]
    except Exception:
        return []
    return [name for name in names if name and "embed" not in name.lower()]


def pick_model(preferred: Optional[str], available: List[str]) -> Optional[str]:
    """The model to use: the one asked for if it is installed, else the
    configured default (GENERATION_MODEL), else the first one Ollama lists.
    The first listed can be a small model, so set GENERATION_MODEL where the
    quality of the answers matters."""
    wanted = (preferred or "").removeprefix("ollama:")
    if wanted in available:
        return wanted
    configured = os.environ.get("GENERATION_MODEL", "").removeprefix("ollama:").strip()
    if configured in available:
        return configured
    return available[0] if available else None


def ask_local_model(model: str, user: str, *, timeout: float = 180.0, system: str = SYSTEM_PROMPT) -> dict:
    """One structured question to the local model. Raises AiError."""
    import httpx

    payload = {
        "model": model,
        "stream": False,
        "format": ANSWER_SCHEMA,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": 0, "num_ctx": 3072, "num_predict": 160},
    }
    try:
        response = httpx.post(f"{ollama_url()}/api/chat", json=payload, timeout=timeout)
        if response.status_code == 400:  # an Ollama too old for schema output: plain JSON mode
            payload["format"] = "json"
            response = httpx.post(f"{ollama_url()}/api/chat", json=payload, timeout=timeout)
        response.raise_for_status()
        content = (response.json().get("message") or {}).get("content") or ""
    except Exception as exc:
        raise AiError(f"{model}: {exc.__class__.__name__}: {str(exc)[:200]}") from exc
    return parse_json(content)


def parse_json(text: str) -> dict:
    """The JSON object in a model's reply, tolerating code fences and chatter."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise AiError("the model did not answer in JSON")
        try:
            value = json.loads(match.group(0))
        except ValueError as exc:
            raise AiError("the model's JSON could not be read") from exc
    if not isinstance(value, dict):
        raise AiError("the model's answer was not an object")
    return value


# --------------------------------------------------------- the evidence --
@dataclass
class Evidence:
    """Everything known about one file, as plain data."""

    filename: str
    title: str
    author: Optional[str] = None
    embedded_author: Optional[str] = None
    opening: str = ""  # the first pages: title page, copyright page
    excerpt: str = ""  # a stretch of the body
    pages: Optional[int] = None
    words: int = 0
    kind: Optional[str] = None
    form: Optional[str] = None
    weak: bool = False  # the kind was decided by length alone
    fiction_in_doubt: bool = False
    title_untouched: bool = True  # the title is still the guess from the file name
    candidates: List[dict] = field(default_factory=list)  # catalogue matches, best first


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def similarity(a: str, b: str) -> float:
    a, b = _fold(a), _fold(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"[ \t]+", " ", re.sub(r"[#*_>`]+", "", text or ""))
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:limit]


def clip_opening(page_texts: Iterable[str]) -> str:
    return _clip("\n".join(text or "" for text in list(page_texts)[:6]), OPENING_CHARS)


def clip_excerpt(page_texts: Iterable[str]) -> str:
    pages = [text or "" for text in page_texts]
    body = pages[6:12] or pages[2:6]
    return _clip("\n".join(body), EXCERPT_CHARS)


def build_prompt(ev: Evidence) -> str:
    """The user message: the evidence, labelled, with the catalogue matches numbered."""
    lines = [
        f"File name: {ev.filename}",
        f"Current title: {ev.title}",
        f"Current author: {ev.author or '(none)'}",
    ]
    if ev.embedded_author:
        lines.append(f"Author in the file's own properties: {ev.embedded_author}")
    lines.append(f"Length: about {ev.pages or 'unknown'} pages; currently filed as {ev.kind or 'unknown'}.")
    if ev.opening:
        lines += ["", "--- Opening pages (title page, copyright page) ---", ev.opening]
    if ev.excerpt:
        lines += ["", "--- A passage from the text ---", ev.excerpt]
    if ev.candidates:
        lines += ["", "--- Catalogue matches (Open Library / Google Books) ---"]
        for number, candidate in enumerate(ev.candidates, 1):
            year = f", {candidate['published_year']}" if candidate.get("published_year") else ""
            lines.append(f"{number}. {candidate.get('title')} - {candidate.get('author') or 'unknown author'}{year}")
    else:
        lines += ["", "(No catalogue matches were looked up.)"]
    return "\n".join(lines)


def evidence_blob(ev: Evidence) -> str:
    """Every word a model's claim may be checked against, before the catalogue."""
    return _fold(" ".join([ev.filename, ev.title, ev.author or "", ev.embedded_author or "", ev.opening, ev.excerpt]))


# ---------------------------------------------------------- the answer --
@dataclass
class Answer:
    title: Optional[str] = None
    author: Optional[str] = None
    same_work_as: Optional[int] = None  # 1-based number of a catalogue match
    is_fiction: Optional[bool] = None
    kind: Optional[str] = None
    confidence: float = 0.0
    quote: Optional[str] = None


def parse_answer(raw: dict, candidate_count: int = 0) -> Answer:
    """A model's reply as an Answer, every field checked: wrong types and
    out-of-range numbers become "no answer" instead of an error."""

    def text(key: str, limit: int = 200) -> Optional[str]:
        value = raw.get(key)
        value = " ".join(value.split()) if isinstance(value, str) else ""
        return value[:limit] or None

    same = raw.get("same_work_as")
    same = same if isinstance(same, int) and not isinstance(same, bool) and 1 <= same <= candidate_count else None
    fiction = raw.get("is_fiction")
    kind = raw.get("kind")
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    return Answer(
        title=text("title"),
        author=text("author"),
        same_work_as=same,
        is_fiction=fiction if isinstance(fiction, bool) else None,
        kind=kind if kind in doc_kind.KINDS else None,
        confidence=max(0.0, min(1.0, confidence)),
        quote=text("quote", 160),
    )


# A name credited with one of these just before it is not the work's author.
_CONTRIBUTOR_CREDIT = re.compile(
    r"(?:translat\w*|edit\w*|selected|compiled|introduc\w*|foreword|afterword|preface|illustrat\w*|"
    r"adapted|retold|narrat\w*|read by|notes|commentary|revised|annotated)\W+(?:\w+\W+){0,6}(?:by|and|with)?\W*$",
    re.IGNORECASE,
)


def contributor_only(name: str, text: str) -> bool:
    """True when every place the name is printed in `text` is a credit as
    translator, editor or the like ("Selected and translated by T. Bailey Saunders"):
    such a person did not write the work. A name that is not printed there at all
    is not known to be a contributor."""
    surname = _surname(name)
    if not surname or not text:
        return False
    found = False
    for match in re.finditer(rf"\b{re.escape(surname)}\b", _plain(text), re.IGNORECASE):
        found = True
        before = _plain(text)[max(0, match.start() - 70):match.start()]
        if not _CONTRIBUTOR_CREDIT.search(before):
            return False
    return found


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def candidate_author(candidate: dict, ev: "Evidence") -> Optional[str]:
    """The author to take from a catalogue entry that lists several people
    ("T. Bailey Saunders, Arthur Schopenhauer"): the one whose surname is in the
    title, the file name or the author we already have, and who is not credited
    only as a translator or editor on the pages. A single name is returned as is."""
    raw = candidate.get("author") or ""
    parts = [part for part in re.split(r"\s*(?:;|&|,|\band\b)\s*", raw) if part.strip()]
    names = [name for name in (shelves.normalize_author(part) for part in parts) if name and " " in name]
    if not names:
        single = shelves.normalize_author(raw)
        names = [single] if single and " " in single else []
    if not names:
        return None
    known = set(_fold(" ".join([ev.title, ev.filename, ev.author or "", ev.embedded_author or ""])).split())
    ranked = sorted(names, key=lambda name: (_surname(name) not in known, contributor_only(name, ev.opening)))
    best = ranked[0]
    return None if contributor_only(best, ev.opening) and _surname(best) not in known else best


def author_is_written_in(name: str, blob: str) -> bool:
    """Every real word of the name (initials aside) appears in the folded evidence."""
    tokens = [t for t in _fold(name).split() if len(t) > 1]
    words = set(blob.split())
    return bool(tokens) and all(token in words for token in tokens)


def quote_is_written_in(quote: Optional[str], blob: str) -> bool:
    folded = _fold(quote or "")
    return len(folded) >= 6 and folded in blob


# ---------------------------------------------- confirming without a model --
def strict_match(ev: Evidence) -> Optional[dict]:
    """The one catalogue match that is convincing without anyone confirming it:
    a high score with a clear lead, a title that is ours with a typo at most,
    and an author that does not contradict what we know."""
    ranked = ev.candidates
    if not ranked or ranked[0].get("score", 0) < 0.8:
        return None
    best = ranked[0]
    if len(ranked) > 1 and best["score"] - ranked[1].get("score", 0) < 0.05 and best.get("title") != ranked[1].get("title"):
        return None
    if similarity(catalogue._main_title(best.get("title") or ""), ev.title) < SAME_WORK_TITLE:
        return None
    if ev.author:
        surname = _surname(ev.author)
        if surname and surname not in _fold(best.get("author") or "").split():
            return None
    return best


def _surname(name: str) -> str:
    words = _fold(name).split()
    return words[-1] if words else ""


# ------------------------------------------------------------- the plan --
@dataclass
class Change:
    """One field of one book: what it is, what it would become, and how sure we are."""

    field: str
    before: Optional[str]
    after: Optional[str]
    source: str  # "title page", "catalogue", "model", "library", "file name"
    reason: str
    auto: bool  # applies by itself; otherwise it is a suggestion


@dataclass
class Plan:
    changes: List[Change] = field(default_factory=list)
    match: Optional[dict] = None  # the confirmed catalogue match, to fill the empty details from
    ai: dict = field(default_factory=dict)  # a verdict to remember: {"fiction": bool, "kind": str, ...}
    notes: List[str] = field(default_factory=list)

    @property
    def auto(self) -> List[Change]:
        return [c for c in self.changes if c.auto]

    @property
    def suggestions(self) -> List[Change]:
        return [c for c in self.changes if not c.auto]


def plan_book(ev: Evidence, answer: Optional[Answer] = None, *, use_catalogue: bool = True) -> Plan:
    """Decide what to change for one book. `answer` is the local model's reply
    (None when no model was available)."""
    plan = Plan()
    blob = evidence_blob(ev)
    match, match_sources = _confirmed_match(ev, answer, plan) if use_catalogue else (None, set())
    plan.match = match

    _plan_author(ev, answer, match, match_sources, blob, plan)
    _plan_title(ev, answer, match, match_sources, blob, plan)
    _plan_verdicts(ev, answer, plan)
    return plan


def _confirmed_match(ev: Evidence, answer: Optional[Answer], plan: Plan):
    """The catalogue match that is the same work, and who says so."""
    if not ev.candidates:
        return None, set()
    if answer is not None:
        if answer.same_work_as is None:
            plan.notes.append("the model found no catalogue match that is the same work")
            return None, set()
        candidate = ev.candidates[answer.same_work_as - 1]
        if similarity(catalogue._main_title(candidate.get("title") or ""), ev.title) >= SAME_WORK_TITLE or answer.confidence >= 0.85:
            return candidate, {"model"} | ({"rules"} if strict_match(ev) is candidate else set())
        plan.notes.append(f"the model's match “{candidate.get('title')}” is too different from this title")
        return None, set()
    strict = strict_match(ev)
    return (strict, {"rules"}) if strict else (None, set())


def _plan_author(ev, answer, match, match_sources, blob, plan: Plan) -> None:
    current = ev.author
    lone = bool(current) and " " not in current.strip()
    if current and not lone:
        return  # a full name is left alone; a model's disagreement is not a reason to change it
    options: dict = {}  # full name -> the sources that give or confirm it

    def offer(name: Optional[str], sources: set) -> None:
        name = shelves.normalize_author(name)
        if not name or " " not in name:
            return
        if lone and _surname(name) != _surname(current):
            return
        options.setdefault(name, set()).update(sources)

    if match and match.get("author"):
        # The catalogue gives the name; whoever confirmed it is the same work (the
        # model, or the rules' own strict check) is a second source for it.
        offer(candidate_author(match, ev), {"catalogue"} | match_sources)
    quote_ok = False
    if answer and answer.author and author_is_written_in(answer.author, blob) and not contributor_only(answer.author, ev.opening):
        offer(answer.author, {"model"})
        quote_ok = quote_is_written_in(answer.quote, blob)
    if not options:
        return
    # Several full names for one surname: the one the most sources give.
    name, sources = max(options.items(), key=lambda item: (len(item[1]), len(item[0])))
    corroborated = len(sources) >= 2 or ("model" in sources and quote_ok)
    plan.changes.append(Change(
        "author", current, name, " + ".join(sorted(sources)),
        "completes the surname" if lone else "no author was known",
        auto=corroborated,
    ))


def _plan_title(ev, answer, match, match_sources, blob, plan: Plan) -> None:
    if not ev.title_untouched:
        return
    wanted = None
    source = ""
    if match and match.get("title"):
        wanted, source = catalogue._main_title(match["title"]).strip(), "catalogue"
    elif answer and answer.title:
        wanted, source = answer.title.strip(), "model"
    if not wanted or wanted == ev.title or similarity(wanted, ev.title) < SIMILAR_TITLE:
        return
    if source == "model" and not all(token in blob.split() for token in _fold(wanted).split()[:4]):
        return  # a title the evidence does not even contain
    agreed = source == "catalogue" and bool(match_sources)
    plan.changes.append(Change("title", ev.title, wanted, source, "fixes the spelling or capitalisation", auto=agreed))


def _plan_verdicts(ev, answer, plan: Plan) -> None:
    if not answer:
        return
    if answer.is_fiction is not None and answer.confidence >= MODEL_CONFIDENCE and (ev.fiction_in_doubt or ev.form is None):
        plan.ai["fiction"] = answer.is_fiction
        plan.changes.append(Change(
            "fiction", None, "fiction" if answer.is_fiction else "non-fiction", "model",
            "the text alone could not tell", auto=True,
        ))
    if answer.kind and answer.kind != ev.kind and ev.weak and answer.confidence >= MODEL_CONFIDENCE:
        plan.changes.append(Change(
            "kind", ev.kind, answer.kind, "model",
            "only the page count had decided it", auto=False,
        ))
