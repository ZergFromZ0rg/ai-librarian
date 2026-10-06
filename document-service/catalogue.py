"""Optional book-catalogue lookup (Open Library).

Only the explicit title/author/ISBN the reader typed ever leaves this process.
Responses are normalised into one provider-neutral candidate shape, cached in
memory for a while, and treated as untrusted plain data. Every failure surfaces
as CatalogueError so manual editing keeps working when the provider is down.
"""

import os
import re
import threading
import time
from typing import List, Optional

import httpx

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg?default=false"
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


def _candidates(params: dict, limit: int) -> List[dict]:
    cache_key = tuple(sorted(params.items()))
    cached = _cache_get(cache_key)
    if cached is None:
        found = [item for item in map(normalise, _fetch_json(params).get("docs") or []) if item]
        _cache_put(cache_key, found)
        cached = found
    return [dict(item) for item in cached[:limit]]


def search(query: str, limit: int = 8) -> List[dict]:
    query = " ".join(query.split())
    if len(query) < 2:
        return []
    return _candidates({"q": query, "limit": str(limit), "fields": FIELDS}, limit)


def by_isbn(isbn: str) -> List[dict]:
    """`isbn` is already checksum-validated by the caller."""
    found = _candidates({"q": f"isbn:{isbn}", "limit": "5", "fields": FIELDS}, 5)
    # A work lists every edition's ISBNs; the one searched for is the one the
    # reader holds, so it replaces whichever the work happened to list first.
    for item in found:
        item["isbn_13" if len(isbn) == 13 else "isbn_10"] = isbn
    return found


def fetch_cover(cover_id: str) -> bytes:
    """The raw bytes of a catalogue cover; the caller validates the image."""
    if not re.fullmatch(r"\d{1,12}", cover_id or ""):
        raise CatalogueError("That cover is not available.")
    response = _get(COVER_URL.format(cover_id=cover_id))
    # Covers are served from the Internet Archive through a couple of
    # redirects. Follow them by hand so none can lead anywhere else.
    for _ in range(3):
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        target = httpx.URL(response.headers.get("location", ""))
        host = target.host or ""
        if target.scheme != "https" or not (host == "archive.org" or host.endswith(".archive.org")):
            raise CatalogueError("That cover is not available.")
        response = _get(str(target))
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
        raise CatalogueError("That cover is not available.")
    if len(response.content) > MAX_COVER_BYTES:
        raise CatalogueError("That cover is too large.")
    return response.content
