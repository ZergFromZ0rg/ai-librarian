"""Optional book-catalogue lookup (Open Library, with Google Books alongside).

Only a title/author/ISBN query ever leaves this process: what the reader typed,
or, for a book they asked to match, its cleaned-up file name.
Several providers are searched together, their hits are scored against the
query (so a typo like "Hapy" still finds "Happy"), de-duplicated, and merged
into one ranked list.
Responses are normalised into one provider-neutral candidate shape, cached in
memory for a while, and treated as untrusted plain data. Every failure surfaces
as CatalogueError so manual editing keeps working when the provider is down.
"""

import difflib
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import httpx

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg?default=false"
GOOGLE_URL = "https://www.googleapis.com/books/v1/volumes"
GOOGLE_COVER_URL = "https://books.google.com/books/content?id={volume}&printsec=frontcover&img=1&zoom=1"
GOOGLE_COVER_HOSTS = ("google.com", "googleusercontent.com")
USER_AGENT = os.environ.get("CATALOGUE_USER_AGENT", "AILibrarian/1.0 (self-hosted personal library)")
TIMEOUT_SECONDS = 8.0
CACHE_SECONDS = 3600
CACHE_LIMIT = 256
MAX_COVER_BYTES = 3 * 1024 * 1024
FIELDS = (
    "key,title,subtitle,author_name,first_publish_year,publisher,isbn,"
    "number_of_pages_median,language,subject,cover_i"
)
# Open Library reports ISO 639-2/B; the book editor stores two-letter codes
# where they exist, which is what readers expect to see ("en").
LANGUAGES = {
    "eng": "en", "fre": "fr", "ger": "de", "spa": "es", "ita": "it", "por": "pt",
    "rus": "ru", "jpn": "ja", "chi": "zh", "dut": "nl", "swe": "sv", "pol": "pl",
    "lat": "la", "grc": "el", "gre": "el", "ara": "ar", "heb": "he", "kor": "ko",
}


class CatalogueError(Exception):
    """The catalogue could not answer; the message is safe to show a reader."""


_cache: dict = {}
_cache_lock = threading.Lock()


def _offline() -> bool:
    return os.environ.get("OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        _cache.pop(key, None)
    return None


def _cache_put(key, value) -> None:
    with _cache_lock:
        if len(_cache) >= CACHE_LIMIT:
            _cache.pop(min(_cache, key=lambda item: _cache[item][0]))
        _cache[key] = (time.monotonic(), value)


def _get(url: str, params: Optional[dict] = None) -> httpx.Response:
    """GET with a short timeout and one retry on transient failure."""
    if _offline():
        raise CatalogueError("Catalogue lookup is disabled because OFFLINE mode is on.")
    last: Optional[Exception] = None
    for attempt in range(2):
        try:
            response = httpx.get(
                url, params=params, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT},
                follow_redirects=False,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise httpx.HTTPError(f"status {response.status_code}")
            return response
        except httpx.HTTPError as exc:
            last = exc
            if attempt == 0:
                time.sleep(0.4)
    raise CatalogueError("Open Library could not be reached. You can still fill the details in by hand.") from last


def _fetch_json(params: dict) -> dict:
    response = _get(SEARCH_URL, params)
    if response.status_code != 200:
        raise CatalogueError("Open Library returned an unexpected answer.")
    try:
        data = response.json()
    except ValueError as exc:
        raise CatalogueError("Open Library returned an unreadable answer.") from exc
    return data if isinstance(data, dict) else {}


def _text(value, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _isbn_pair(values) -> tuple:
    isbn_13 = isbn_10 = None
    for raw in values if isinstance(values, list) else []:
        digits = re.sub(r"[-\s]", "", str(raw)).upper()
        if isbn_13 is None and re.fullmatch(r"97[89]\d{10}", digits):
            isbn_13 = digits
        elif isbn_10 is None and re.fullmatch(r"\d{9}[\dX]", digits):
            isbn_10 = digits
    return isbn_13, isbn_10


_NOISE = re.compile(r"reviewed|bestseller|accessible book|protected daisy|large type|in library|overdrive|nyt:|award:", re.I)


def _useful_subject(value) -> Optional[str]:
    """A subject worth showing as a genre. Open Library subjects include
    catalogue tags, award markers and comma lists; the genre field is a
    comma-separated list, so anything containing a comma would be split."""
    subject = _text(value, 60)
    if not subject or any(char in subject for char in ",:=()") or _NOISE.search(subject):
        return None
    return subject


def normalise(doc: dict) -> Optional[dict]:
    """One Open Library search hit -> the internal candidate shape."""
    title = _text(doc.get("title"), 300)
    key = _text(doc.get("key"), 64)
    if not title or not key:
        return None
    authors = [name for name in (_text(item, 120) for item in doc.get("author_name") or []) if name]
    publishers = [name for name in (_text(item, 300) for item in doc.get("publisher") or []) if name]
    # A work lists every edition's language; only trust it when there is one.
    codes = [code for code in doc.get("language") or [] if isinstance(code, str)]
    languages = [LANGUAGES.get(codes[0], codes[0])] if len(codes) == 1 else []
    subjects: List[str] = []
    seen = set()
    for item in doc.get("subject") or []:
        subject = _useful_subject(item)
        normalised = subject.casefold().replace("-", " ") if subject else None
        if subject and normalised not in seen:
            seen.add(normalised)
            subjects.append(subject)
        if len(subjects) == 5:
            break
    isbn_13, isbn_10 = _isbn_pair(doc.get("isbn"))
    year = doc.get("first_publish_year")
    pages = doc.get("number_of_pages_median")
    cover = doc.get("cover_i")
    return {
        "provider": "openlibrary",
        "id": key,
        "title": title,
        "subtitle": _text(doc.get("subtitle"), 300),
        "author": ", ".join(authors[:3]) or None,
        "published_year": year if isinstance(year, int) and 1000 <= year <= 2100 else None,
        "publisher": publishers[0] if publishers else None,
        "page_count": pages if isinstance(pages, int) and 1 <= pages <= 100_000 else None,
        "language": languages[0] if languages and re.fullmatch(r"[a-z]{2,3}", languages[0]) else None,
        "genres": subjects,
        "isbn_13": isbn_13,
        "isbn_10": isbn_10,
        "cover_id": str(cover) if isinstance(cover, int) and cover > 0 else None,
    }


def _providers() -> List[str]:
    chosen = [name.strip().lower() for name in os.environ.get("CATALOGUE_PROVIDERS", "openlibrary,googlebooks").split(",")]
    return [name for name in ("openlibrary", "googlebooks") if name in chosen] or ["openlibrary"]


def _fetch_google(params: dict) -> dict:
    key = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
    response = _get(GOOGLE_URL, {**params, **({"key": key} if key else {})})
    if response.status_code != 200:
        raise CatalogueError("Google Books returned an unexpected answer.")
    try:
        data = response.json()
    except ValueError as exc:
        raise CatalogueError("Google Books returned an unreadable answer.") from exc
    return data if isinstance(data, dict) else {}


def normalise_google(item: dict) -> Optional[dict]:
    """One Google Books volume -> the internal candidate shape."""
    info = item.get("volumeInfo") if isinstance(item, dict) else None
    volume = item.get("id") if isinstance(item, dict) else None
    if not isinstance(info, dict) or not isinstance(volume, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", volume):
        return None
    title = _text(info.get("title"), 300)
    if not title:
        return None
    authors = [name for name in (_text(a, 120) for a in info.get("authors") or []) if name]
    isbn_13 = isbn_10 = None
    for ident in info.get("industryIdentifiers") or []:
        if not isinstance(ident, dict):
            continue
        pair = _isbn_pair([ident.get("identifier")])
        isbn_13, isbn_10 = isbn_13 or pair[0], isbn_10 or pair[1]
    year_match = re.match(r"(\d{4})", str(info.get("publishedDate") or ""))
    year = int(year_match.group(1)) if year_match else None
    genres: List[str] = []
    for category in info.get("categories") or []:
        for part in str(category).split("/"):
            genre = _useful_subject(part)
            if genre and genre.casefold() not in {g.casefold() for g in genres}:
                genres.append(genre)
    pages = info.get("pageCount")
    language = info.get("language")
    return {
        "provider": "googlebooks",
        "id": f"gb:{volume}",
        "title": title,
        "subtitle": _text(info.get("subtitle"), 300),
        "author": ", ".join(authors[:3]) or None,
        "published_year": year if year and 1000 <= year <= 2100 else None,
        "publisher": _text(info.get("publisher"), 300),
        "page_count": pages if isinstance(pages, int) and 1 <= pages <= 100_000 else None,
        "language": language if isinstance(language, str) and re.fullmatch(r"[a-z]{2,3}", language) else None,
        "genres": genres[:5],
        "isbn_13": isbn_13,
        "isbn_10": isbn_10,
        "description": _text(info.get("description"), 2000),
        "cover_id": f"g:{volume}" if isinstance(info.get("imageLinks"), dict) and info["imageLinks"] else None,
    }


# ----------------------------------------------------- query clean-up --
# Words that file names and download sites add around the real title.
_FILE_NOISE = re.compile(
    r"\b(?:z-?lib(?:rary)?(?:\.org)?|libgen(?:\.\w+)?|pdf-?drive|annas?-?archive|ebook|e-book|epub|mobi|pdf|"
    r"retail|scan(?:ned)?|ocr|vintage|\d+(?:st|nd|rd|th) ed(?:ition)?|edition|revised|illustrated|unabridged|"
    r"hardcover|paperback|www\.\S+|\S+\.(?:com|org|net))\b", re.I,
)
_STOPWORDS = frozenset("a an the of and or in on to for by with at from is as".split())
_WEAK_TITLE = re.compile(r"\b(?:commentary|study guide|summar(?:y|ies)|bright summaries|analysis|workbook|companion|notes on|cliffs?notes|sparknotes|critical)\b", re.I)


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", _fold(text).replace("'", "").replace("’", ""))


def clean_query(raw: str) -> str:
    """A file name or messy query reduced to the words that name the book."""
    text = re.sub(r"\.(?:pdf|epub|mobi|azw3?|djvu|docx?|txt)$", "", (raw or "").strip(), flags=re.I)
    text = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", lambda m: " " if _FILE_NOISE.search(m.group(0)) or re.search(r"\b(?:19|20)\d\d\b", m.group(0)) else m.group(0)[1:-1], text)
    text = re.sub(r"(?<=[A-Za-z])[_.]+(?=[A-Za-z0-9])|[_]+", " ", text)
    text = _FILE_NOISE.sub(" ", text)
    text = re.sub(r"\s*[-–—]\s*", " - ", text)
    text = re.sub(r"[^\w\s'’:&,.\-]", " ", text)
    return " ".join(text.split()).strip(" -.,:")


def query_variants(raw: str) -> List[dict]:
    """Plausible {title, author} readings of a query: "Author - Title" in
    either order, or a trailing/leading surname ("A Happy Death Camus")."""
    text = clean_query(raw)
    out: List[dict] = []
    parts = [p.strip() for p in text.split(" - ") if p.strip()]
    if len(parts) == 2:
        out += [{"title": parts[1], "author": parts[0]}, {"title": parts[0], "author": parts[1]}]
    elif "," in text and len(text.split(",")) == 2 and len(text.split(",")[0].split()) <= 2 and len(text.split()) >= 3:
        title, author = (p.strip() for p in text.split(","))
        out.append({"title": title, "author": author})
    words = text.replace(" - ", " ").split()
    if len(words) >= 3:
        out += [{"title": " ".join(words[:-1]), "author": words[-1]}, {"title": " ".join(words[1:]), "author": words[0]}]
    return out[:4]


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _main_title(title: str) -> str:
    """The title without a subtitle or translator credit ("A Happy Death.
    Translated from the French by ..." -> "A Happy Death")."""
    return re.split(r"\.\s|:\s|\s\(|\s-\s| by ", title or "", maxsplit=1)[0]


def score(candidate: dict, query: str) -> float:
    """How well a candidate answers the query, 0..1. Typo-tolerant: a query
    word counts if it nearly matches a word in the title or authors, so the
    right book outranks a longer title that merely shares one word."""
    wanted = [t for t in _tokens(clean_query(query)) if t not in _STOPWORDS]
    if not wanted:
        return 0.0
    full = [t for t in _tokens(candidate.get("title") or "") if t not in _STOPWORDS]
    title_tokens = [t for t in _tokens(_main_title(candidate.get("title") or "")) if t not in _STOPWORDS] or full
    author_tokens = _tokens(candidate.get("author") or "")
    matched = sum(1 for w in wanted if any(_similar(w, t) for t in full + author_tokens))
    coverage = matched / len(wanted)
    in_title = sum(1 for t in title_tokens if any(_similar(t, w) for w in wanted))
    precision = in_title / len(title_tokens) if title_tokens else 0.0
    ascii_query = all(ord(c) < 128 for c in clean_query(query))
    language = candidate.get("language")
    result = 0.6 * coverage + 0.2 * precision
    # The title is exactly what was asked for, and the author (if named) agrees.
    if title_tokens and sorted(title_tokens) == sorted(w for w in wanted if any(_similar(w, t) for t in title_tokens)) and in_title == len(wanted):
        result += 0.1
    if any(_similar(w, t) for w in wanted for t in author_tokens if w not in title_tokens):
        result += 0.1
    result += 0.03 * bool(candidate.get("cover_id")) + 0.03 * bool(candidate.get("isbn_13") or candidate.get("isbn_10"))
    result += 0.04 * (1 if (not ascii_query or language in (None, "en")) else 0)
    if _WEAK_TITLE.search(candidate.get("title") or "") and not _WEAK_TITLE.search(query):
        result -= 0.25
    return max(0.0, min(1.0, round(result, 4)))


def _merge_key(candidate: dict) -> tuple:
    title = " ".join(t for t in _tokens(re.split(r"[.:(]", candidate.get("title") or "")[0]) if t not in _STOPWORDS)
    surname = (_tokens((candidate.get("author") or "").split(",")[0]) or [""])[-1]
    return title, surname


def merge(candidates: List[dict], query: str) -> List[dict]:
    """Score, collapse duplicates across providers (filling the gaps in the best
    one from the rest), and sort best first."""
    groups: dict = {}
    for item in candidates:
        item = {**item, "score": score(item, query)}
        groups.setdefault(_merge_key(item), []).append(item)
    merged = []
    for group in groups.values():
        group.sort(key=lambda c: c["score"], reverse=True)
        best = dict(group[0])
        for other in group[1:]:
            for field, value in other.items():
                if best.get(field) in (None, "", []) and value not in (None, "", []):
                    best[field] = value
        best["sources"] = sorted({c["provider"] for c in group})
        merged.append(best)
    merged.sort(key=lambda c: (c["score"], bool(c.get("cover_id")), bool(c.get("isbn_13"))), reverse=True)
    return merged


# ------------------------------------------------------------ searching --
def _candidates(params: dict, limit: int) -> List[dict]:
    cache_key = tuple(sorted(params.items()))
    cached = _cache_get(cache_key)
    if cached is None:
        found = [item for item in map(normalise, _fetch_json(params).get("docs") or []) if item]
        _cache_put(cache_key, found)
        cached = found
    return [dict(item) for item in cached[:limit]]


def _google_candidates(params: dict, limit: int) -> List[dict]:
    cache_key = ("google",) + tuple(sorted(params.items()))
    cached = _cache_get(cache_key)
    if cached is None:
        found = [item for item in map(normalise_google, _fetch_google(params).get("items") or []) if item]
        _cache_put(cache_key, found)
        cached = found
    return [dict(item) for item in cached[:limit]]


def _ol(query: Optional[str] = None, title: Optional[str] = None, author: Optional[str] = None, limit: int = 10) -> List[dict]:
    params = {"limit": str(limit), "fields": FIELDS}
    if query:
        params["q"] = query
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    return _candidates(params, limit)


def _gb(query: str, limit: int = 10) -> List[dict]:
    return _google_candidates({"q": query, "maxResults": str(limit), "printType": "books"}, limit)


def _run(jobs: List) -> tuple:
    """Run provider calls concurrently; returns (candidates, last_error). A
    provider being down only matters when nothing was found anywhere."""
    found: List[dict] = []
    error: Optional[CatalogueError] = None
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        for future in [pool.submit(job) for job in jobs]:
            try:
                found += future.result()
            except CatalogueError as exc:
                error = exc
    return found, error


GOOD_ENOUGH = 0.75


def search(query: str, limit: int = 8) -> List[dict]:
    """Search every enabled provider for a title, author, or messy file name."""
    query = " ".join(query.split())
    if len(query) < 2:
        return []
    cleaned = clean_query(query) or query
    providers = _providers()
    jobs = []
    if "openlibrary" in providers:
        jobs.append(lambda: _ol(query=cleaned))
    if "googlebooks" in providers:
        jobs.append(lambda: _gb(cleaned))
    found, error = _run(jobs)
    ranked = merge(found, query)
    if ranked and ranked[0]["score"] >= GOOD_ENOUGH:
        return ranked[:limit]

    # Weak or empty: try the readings "Title Author" / "Author Title" as
    # fielded searches, then the query with each word left out in turn (which
    # steps over a typo that exact search cannot).
    more = []
    for variant in query_variants(query):
        if "openlibrary" in providers:
            more.append(lambda v=variant: _ol(title=v["title"], author=v["author"], limit=6))
        if "googlebooks" in providers:
            more.append(lambda v=variant: _gb(f'intitle:"{v["title"]}" inauthor:"{v["author"]}"', 6))
    words = cleaned.split()
    if 2 < len(words) <= 7:
        for skip in range(len(words)):
            reduced = " ".join(words[:skip] + words[skip + 1:])
            if "openlibrary" in providers:
                more.append(lambda r=reduced: _ol(query=r, limit=6))
    extra, extra_error = _run(more) if more else ([], None)
    ranked = merge(found + extra, query)
    if not ranked and (error or extra_error):
        raise error or extra_error
    return [item for item in ranked if item["score"] >= 0.3][:limit] if ranked else []


def best_match(query: str) -> Optional[dict]:
    """The single most likely book for a file name, or None when nothing is
    convincing. Used to suggest details; a person still approves them."""
    results = search(query, 3)
    if not results or results[0]["score"] < 0.8:
        return None
    if len(results) > 1 and results[0]["score"] - results[1]["score"] < 0.05 and results[0]["title"] != results[1]["title"]:
        return None
    return results[0]


def by_isbn(isbn: str) -> List[dict]:
    """`isbn` is already checksum-validated by the caller."""
    providers = _providers()
    jobs = []
    if "openlibrary" in providers:
        jobs.append(lambda: _candidates({"q": f"isbn:{isbn}", "limit": "5", "fields": FIELDS}, 5))
    if "googlebooks" in providers:
        jobs.append(lambda: _google_candidates({"q": f"isbn:{isbn}", "maxResults": "5", "printType": "books"}, 5))
    found, error = _run(jobs)
    if not found and error:
        raise error
    ranked = merge(found, isbn)
    # A work lists every edition's ISBNs; the one searched for is the one the
    # reader holds, so it replaces whichever the work happened to list first.
    for item in ranked:
        item["isbn_13" if len(isbn) == 13 else "isbn_10"] = isbn
    return ranked[:5]


def _cover_allowed(host: str, cover_id: str) -> bool:
    suffixes = GOOGLE_COVER_HOSTS if cover_id.startswith("g:") else ("archive.org",)
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def fetch_cover(cover_id: str) -> bytes:
    """The raw bytes of a catalogue cover; the caller validates the image."""
    if re.fullmatch(r"g:[A-Za-z0-9_-]{6,20}", cover_id or ""):
        url = GOOGLE_COVER_URL.format(volume=cover_id[2:])
    elif re.fullmatch(r"\d{1,12}", cover_id or ""):
        url = COVER_URL.format(cover_id=cover_id)
    else:
        raise CatalogueError("That cover is not available.")
    response = _get(url)
    # Covers are served through a couple of redirects. Follow them by hand so
    # none can lead anywhere but the provider's own image hosts.
    for _ in range(3):
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        target = httpx.URL(response.headers.get("location", ""))
        if target.scheme != "https" or not _cover_allowed(target.host or "", cover_id):
            raise CatalogueError("That cover is not available.")
        response = _get(str(target))
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
        raise CatalogueError("That cover is not available.")
    if len(response.content) > MAX_COVER_BYTES:
        raise CatalogueError("That cover is too large.")
    return response.content
