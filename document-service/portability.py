"""Portable export/import archive for the reader's own data.

The archive holds only what the reader created or corrected: catalogue
metadata, reading state, ratings and reviews, notes, vocabulary, and custom
covers. Source files, search indexes, models, thumbnails that can be
regenerated, and job state are deliberately left out. Every file is plain
JSON, Markdown or JPEG, so the data can be recovered without this app.
"""

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
FORMAT_NAME = "ai-librarian-export"

# Everything the reader owns about a book. Pipeline state (pages, chunks,
# indexing status, shelves' suggestions, ...) is regenerated and never exported.
BOOK_FIELDS = (
    "document_id", "record_type", "filename", "file_type", "content_sha256", "source_path",
    "collection_id", "uploaded_at", "title", "subtitle", "author", "subject", "shelf",
    "kind_override", "owned", "description", "publisher", "published_year", "page_count",
    "language", "isbn_10", "isbn_13", "acquisition_source", "reading_status", "started_at",
    "finished_at", "current_page", "reading_progress", "last_read_at", "rating", "review",
    "metadata_source", "metadata_source_id",
)
VOCABULARY_FIELDS = (
    "word_id", "word", "definition", "part_of_speech", "example", "document_id",
    "source_page", "source_quote", "definition_source", "created_at", "updated_at",
)

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 50_000
MAX_RATIO = 200

README = """AI Librarian export
===================

books.json       Every book's catalogue details, reading state, rating and review.
notes.json       Your notes with the book and page each one is linked to.
notes/<id>.md    The same notes as plain Markdown, one file per note.
vocabulary.json  Your word list with definitions and source passages.
covers/<id>.jpg  Covers you chose yourself (not covers taken from the books).
manifest.json    Format version, counts and a SHA-256 checksum for every file.

Source books and documents are NOT included; they stay where they are.
To restore, scan your library first, then import this file: books are matched
by the SHA-256 of their content, so renamed or moved files are still found.
"""


class ArchiveError(ValueError):
    """The archive is unusable; the message is safe to show a reader."""


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def book_record(doc: dict) -> dict:
    record = {key: doc.get(key) for key in BOOK_FIELDS}
    try:
        genres = json.loads(doc.get("genres_json") or "[]")
    except ValueError:
        genres = []
    record["genres"] = genres if isinstance(genres, list) else []
    return record


def note_record(doc: dict, text: str) -> dict:
    return {
        "note_id": doc["document_id"],
        "text": text,
        "source_document_id": doc.get("source_document_id"),
        "source_page": doc.get("source_page"),
        "source_quote": doc.get("source_quote"),
        "created_at": doc.get("uploaded_at"),
        "updated_at": doc.get("updated_at"),
    }


def build_archive(
    destination: Path,
    *,
    books: Iterable[dict],
    notes: Iterable[dict],
    vocabulary: Iterable[dict],
    covers: Iterable[tuple],
    app_version: str,
    created_at: str,
    timezone_name: str,
) -> dict:
    """Write the archive to `destination` and return its manifest.

    `books` and `notes` are already-shaped records (see book_record and
    note_record); `covers` yields (document_id, path) pairs. The file is
    written entry by entry, so the library is never held in memory at once.
    """
    checksums: Dict[str, str] = {}
    counts = {"books": 0, "notes": 0, "vocabulary": 0, "covers": 0}

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        def add(name: str, data: bytes) -> None:
            archive.writestr(name, data)
            checksums[name] = hashlib.sha256(data).hexdigest()

        books = list(books)
        notes = list(notes)
        vocabulary = [{key: item.get(key) for key in VOCABULARY_FIELDS} for item in vocabulary]
        add("books.json", _json_bytes(books))
        add("notes.json", _json_bytes(notes))
        add("vocabulary.json", _json_bytes(vocabulary))
        for note in notes:
            add(f"notes/{note['note_id']}.md", (note["text"] + "\n").encode("utf-8"))
        for document_id, path in covers:
            try:
                data = Path(path).read_bytes()
            except OSError:
                continue
            add(f"covers/{document_id}.jpg", data)
            counts["covers"] += 1
        counts.update(books=len(books), notes=len(notes), vocabulary=len(vocabulary))
        add("README.txt", README.encode("utf-8"))
        manifest = {
            "format": FORMAT_NAME,
            "schema_version": SCHEMA_VERSION,
            "app_version": app_version,
            "created_at": created_at,
            "timezone": timezone_name,
            "includes_source_files": False,
            "counts": counts,
            "checksums": checksums,
        }
        archive.writestr("manifest.json", _json_bytes(manifest))
    return manifest


_ALLOWED_NAME = re.compile(
    r"^(?:manifest\.json|README\.txt|books\.json|notes\.json|vocabulary\.json"
    r"|notes/[A-Za-z0-9_-]{1,64}\.md|covers/[A-Za-z0-9_-]{1,64}\.jpg)$"
)


class Archive:
    """A validated, open export archive. Use as a context manager."""

    def __init__(self, source) -> None:
        try:
            self._zip = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ArchiveError("This is not a valid AI Librarian export (it is not a ZIP file).") from exc
        try:
            self._validate()
        except Exception:
            self._zip.close()
            raise

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, *exc) -> None:
        self._zip.close()

    def close(self) -> None:
        self._zip.close()

    def _validate(self) -> None:
        infos = self._zip.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ArchiveError("This archive contains too many files.")
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise ArchiveError("This archive is too large once unpacked.")
        for info in infos:
            if info.is_dir():
                continue
            if not _ALLOWED_NAME.fullmatch(info.filename):
                raise ArchiveError(f"The archive contains an unexpected file: {info.filename[:80]!r}.")
            if info.file_size > MAX_ENTRY_BYTES:
                raise ArchiveError(f"{info.filename} is too large.")
            if info.file_size > 1_000_000 and info.file_size > MAX_RATIO * max(info.compress_size, 1):
                raise ArchiveError(f"{info.filename} looks like a decompression bomb.")
        names = {info.filename for info in infos}
        if "manifest.json" not in names:
            raise ArchiveError("This archive has no manifest, so it is not an AI Librarian export.")
        manifest = self._json("manifest.json")
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME:
            raise ArchiveError("This archive has no manifest, so it is not an AI Librarian export.")
        version = manifest.get("schema_version")
        if not isinstance(version, int) or version > SCHEMA_VERSION or version < 1:
            raise ArchiveError(
                f"This export uses format version {version!r}; this app understands up to {SCHEMA_VERSION}. "
                "Update AI Librarian to import it."
            )
        checksums = manifest.get("checksums")
        if not isinstance(checksums, dict):
            raise ArchiveError("The manifest is damaged.")
        for name in names - {"manifest.json"}:
            if name not in checksums:
                raise ArchiveError(f"{name} is not listed in the manifest.")
        for name, expected in checksums.items():
            if name not in names:
                raise ArchiveError(f"{name} is listed in the manifest but missing from the archive.")
            if hashlib.sha256(self._read(name)).hexdigest() != expected:
                raise ArchiveError(f"{name} does not match its checksum; the archive is damaged.")
        self.manifest = manifest
        self._names = names

    def _read(self, name: str) -> bytes:
        # Read through a bounded stream rather than trusting the declared size.
        with self._zip.open(name) as handle:
            data = handle.read(MAX_ENTRY_BYTES + 1)
        if len(data) > MAX_ENTRY_BYTES:
            raise ArchiveError(f"{name} is too large.")
        return data

    def _json(self, name: str):
        try:
            return json.loads(self._read(name).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ArchiveError(f"{name} is not valid JSON.") from exc

    def _list(self, name: str) -> List[dict]:
        if name not in self._names:
            return []
        data = self._json(name)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ArchiveError(f"{name} is not in the expected format.")
        return data

    def books(self) -> List[dict]:
        return self._list("books.json")

    def notes(self) -> List[dict]:
        return self._list("notes.json")

    def vocabulary(self) -> List[dict]:
        return self._list("vocabulary.json")

    def cover(self, document_id: str) -> Optional[bytes]:
        name = f"covers/{document_id}.jpg"
        return self._read(name) if name in self._names else None
