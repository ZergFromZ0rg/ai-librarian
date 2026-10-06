"""SQLite-backed store for document metadata.

Replaces the previous one-JSON-file-per-document layout. Extracted blocks,
semantic groups, and folder-ingest job state still live as files; only the
per-document metadata records moved into a single indexed table so that
duplicate lookups and library listings stay cheap as the library grows.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

# Column order is also the INSERT order and the dict-key order returned to
# callers. Every value the pipeline records about a document is a real column.
COLUMNS = (
    "document_id",
    # A source-backed document or a catalogue-only book with no file yet.
    "record_type",
    "filename",
    "file_type",
    "title",
    "stored_filename",
    "content_sha256",
    "uploaded_at",
    "updated_at",
    "indexed_at",
    "pages",
    "chunks",
    "retrieval_units",
    "indexing_status",
    "indexing_error",
    "embedding_model",
    "vector_dim",
    "pipeline_version",
    "index_schema_version",
    "extraction_notes",
    # When set, the PDF is referenced in place at INGEST_ROOT/source_path
    # instead of being copied into DOCUMENTS_DIR (browser folder imports).
    "source_path",
    # The named collection that discovered this in-place file (for example
    # Books or Work). Uploads may deliberately remain unfiled.
    "collection_id",
    # Pages of a scan that need OCR, recorded when that exceeds what may run
    # unasked; the reader approves it with `ocr_approved`.
    "ocr_pages",
    "ocr_approved",
    # "book" | "paper" | "document", guessed from the extracted text at index
    # time (see doc_kind.py); `kind_override` is the reader's correction.
    "kind",
    "kind_override",
    # Reader-set: 1/0 whether they own a copy (books only), NULL = not said.
    "owned",
    # When the reader marked it read; NULL = unread.
    "read_at",
    # Virtual shelves (see shelves.py). `shelf_suggested` is recomputed
    # automatically ("Books/Philosophy/Albert Camus"); `shelf` is the reader's
    # choice and always wins. `author` / `subject` are what the suggestion
    # was built from. Files on disk are never moved.
    "author",
    "subject",
    "shelf",
    "shelf_suggested",
    "metadata_edited",
    "source_document_id",
    "source_page",
    "source_quote",
    "note_text",
    "rating",
    "review",
    # Reader-managed catalogue metadata. Provider suggestions are applied only
    # after review; `metadata_source*` records the accepted provenance.
    "isbn_10",
    "isbn_13",
    "subtitle",
    "description",
    "publisher",
    "published_year",
    "page_count",
    "language",
    "genres_json",
    "acquisition_source",
    "reading_status",
    "started_at",
    "finished_at",
    "current_page",
    "reading_progress",
    "last_read_at",
    "metadata_source",
    "metadata_source_id",
    # What sort of working document this is (paper, report, manual, ...), see
    # doc_types.py. `doc_type` is the guess; `doc_type_override` is the reader's.
    "doc_type",
    "doc_type_override",
)
UPDATABLE_COLUMNS = frozenset(COLUMNS) - {"document_id"}

INT_COLUMNS = (
    "pages",
    "chunks",
    "retrieval_units",
    "vector_dim",
    "pipeline_version",
    "index_schema_version",
    "reading_progress",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id           TEXT PRIMARY KEY,
    record_type           TEXT NOT NULL DEFAULT 'source',
    filename              TEXT NOT NULL,
    file_type             TEXT NOT NULL DEFAULT 'pdf',
    title                 TEXT,
    stored_filename       TEXT NOT NULL,
    content_sha256        TEXT NOT NULL,
    uploaded_at           TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    indexed_at            TEXT,
    pages                 INTEGER NOT NULL DEFAULT 0,
    chunks                INTEGER NOT NULL DEFAULT 0,
    retrieval_units       INTEGER NOT NULL DEFAULT 0,
    indexing_status       TEXT NOT NULL DEFAULT 'queued',
    indexing_error        TEXT,
    embedding_model       TEXT,
    vector_dim            INTEGER NOT NULL DEFAULT 0,
    pipeline_version      INTEGER NOT NULL DEFAULT 0,
    index_schema_version  INTEGER NOT NULL DEFAULT 0,
    extraction_notes      TEXT,
    source_path           TEXT,
    collection_id         TEXT,
    ocr_pages             INTEGER,
    ocr_approved          INTEGER,
    kind                  TEXT,
    kind_override         TEXT,
    owned                 INTEGER,
    read_at               TEXT,
    author                TEXT,
    subject               TEXT,
    shelf                 TEXT,
    shelf_suggested       TEXT,
    metadata_edited       INTEGER,
    source_document_id    TEXT,
    source_page           INTEGER,
    source_quote          TEXT,
    note_text             TEXT,
    rating                INTEGER,
    review                TEXT,
    isbn_10               TEXT,
    isbn_13               TEXT,
    subtitle              TEXT,
    description           TEXT,
    publisher             TEXT,
    published_year        INTEGER,
    page_count            INTEGER,
    language              TEXT,
    genres_json           TEXT,
    acquisition_source    TEXT,
    reading_status        TEXT,
    started_at            TEXT,
    finished_at           TEXT,
    current_page          INTEGER,
    reading_progress      INTEGER NOT NULL DEFAULT 0,
    last_read_at          TEXT,
    metadata_source       TEXT,
    metadata_source_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_content_sha256 ON documents (content_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_uploaded_at ON documents (uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_indexing_status ON documents (indexing_status);

CREATE TABLE IF NOT EXISTS owned_books (
    book_id               TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    author                TEXT,
    notes                 TEXT,
    pdf_less              INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_owned_books_title ON owned_books (title);

CREATE TABLE IF NOT EXISTS vocabulary (
    word_id               TEXT PRIMARY KEY,
    word                  TEXT NOT NULL,
    normalized_word       TEXT NOT NULL,
    definition            TEXT NOT NULL,
    part_of_speech        TEXT,
    example               TEXT,
    document_id           TEXT,
    source_page           INTEGER,
    source_quote          TEXT,
    definition_source     TEXT NOT NULL DEFAULT 'manual',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vocabulary_word ON vocabulary (normalized_word);
CREATE INDEX IF NOT EXISTS idx_vocabulary_document ON vocabulary (document_id);
"""


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class MetadataStore:
    """Thread-safe SQLite store for document metadata records.

    One connection guarded by a re-entrant lock. The service runs a single
    process with a handful of worker threads, so serialising writes keeps the
    implementation simple without a meaningful throughput cost.
    """

    def __init__(self, db_path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(documents)")
        }
        for column, ddl in (
            ("record_type", "TEXT"),
            ("extraction_notes", "TEXT"),
            ("source_path", "TEXT"),
            ("collection_id", "TEXT"),
            ("ocr_pages", "INTEGER"),
            ("ocr_approved", "INTEGER"),
            ("kind", "TEXT"),
            ("kind_override", "TEXT"),
            ("owned", "INTEGER"),
            ("read_at", "TEXT"),
            ("author", "TEXT"),
            ("subject", "TEXT"),
            ("shelf", "TEXT"),
            ("shelf_suggested", "TEXT"),
            ("metadata_edited", "INTEGER"),
            ("source_document_id", "TEXT"),
            ("source_page", "INTEGER"),
            ("source_quote", "TEXT"),
            ("note_text", "TEXT"),
            ("rating", "INTEGER"),
            ("review", "TEXT"),
            ("isbn_10", "TEXT"),
            ("isbn_13", "TEXT"),
            ("subtitle", "TEXT"),
            ("description", "TEXT"),
            ("publisher", "TEXT"),
            ("published_year", "INTEGER"),
            ("page_count", "INTEGER"),
            ("language", "TEXT"),
            ("genres_json", "TEXT"),
            ("acquisition_source", "TEXT"),
            ("reading_status", "TEXT"),
            ("started_at", "TEXT"),
            ("finished_at", "TEXT"),
            ("current_page", "INTEGER"),
            ("reading_progress", "INTEGER NOT NULL DEFAULT 0"),
            ("last_read_at", "TEXT"),
            ("metadata_source", "TEXT"),
            ("metadata_source_id", "TEXT"),
            ("doc_type", "TEXT"),
            ("doc_type_override", "TEXT"),
        ):
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE documents ADD COLUMN {column} {ddl}"
                )
        # Indexes on migrated columns must come after the ALTERs: in SCHEMA
        # they would fail on a pre-existing table that lacks the column.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_collection_id ON documents (collection_id)"
        )
        self._conn.execute(
            "UPDATE documents SET record_type = 'source' WHERE record_type IS NULL"
        )
        # Compatibility bridge from the original Read/Unread mark. Keep
        # `read_at` for older clients while the richer lifecycle rolls out.
        self._conn.execute(
            "UPDATE documents SET reading_status = CASE "
            "WHEN read_at IS NOT NULL THEN 'read' ELSE 'to_read' END "
            "WHERE reading_status IS NULL"
        )
        self._conn.execute(
            "UPDATE documents SET finished_at = substr(read_at, 1, 10) "
            "WHERE finished_at IS NULL AND read_at IS NOT NULL"
        )
        # Release 1 kept books without files in a side table. Copy each one
        # into the main catalogue so it inherits covers, shelves, notes,
        # reviews, and reading state. The legacy rows remain as a rollback
        # bridge; INSERT OR IGNORE makes every startup idempotent.
        self._conn.execute(
            "INSERT OR IGNORE INTO documents ("
            "document_id, record_type, filename, file_type, title, stored_filename, "
            "content_sha256, uploaded_at, updated_at, pages, chunks, retrieval_units, "
            "indexing_status, vector_dim, pipeline_version, index_schema_version, "
            "kind, kind_override, owned, author, review, reading_status, shelf_suggested"
            ") SELECT book_id, 'standalone', title, 'book', title, '', "
            "'standalone:' || book_id, created_at, updated_at, 0, 0, 0, "
            "'catalogued', 0, 0, 0, 'book', 'book', 1, author, notes, 'to_read', 'Books' "
            "FROM owned_books"
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ reads
    def get(self, document_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return _row_to_dict(row)

    def find_by_hash(self, content_sha256: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE content_sha256 = ? "
                "ORDER BY uploaded_at LIMIT 1",
                (content_sha256,),
            ).fetchone()
        return _row_to_dict(row)

    def list_all(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM documents ORDER BY uploaded_at DESC"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    # ----------------------------------------------------------------- writes
    def create(self, metadata: dict) -> dict:
        metadata = {
            **metadata,
            "record_type": metadata.get("record_type") or "source",
            "reading_progress": metadata.get("reading_progress") or 0,
        }
        values = tuple(metadata.get(column) for column in COLUMNS)
        placeholders = ", ".join("?" for _ in COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO documents ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            row = self._conn.execute(
                "SELECT * FROM documents WHERE document_id = ?",
                (metadata["document_id"],),
            ).fetchone()
            self._conn.commit()
        return _row_to_dict(row)

    def update(self, document_id: str, changes: dict) -> dict:
        unknown = set(changes) - UPDATABLE_COLUMNS
        if unknown:
            raise ValueError(f"unknown document columns: {sorted(unknown)}")
        if not changes:
            existing = self.get(document_id)
            if existing is None:
                raise KeyError(document_id)
            return existing
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE documents SET {assignments} WHERE document_id = ?",
                (*changes.values(), document_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(document_id)
            row = self._conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            self._conn.commit()
        return _row_to_dict(row)

    def delete(self, document_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM documents WHERE document_id = ?", (document_id,)
            )
            # A standalone record may have originated in the Release 1 side
            # table. Remove that compatibility row as well so the idempotent
            # startup migration cannot resurrect a book the reader deleted.
            self._conn.execute(
                "DELETE FROM owned_books WHERE book_id = ?", (document_id,)
            )
            self._conn.execute(
                "UPDATE vocabulary SET document_id = NULL, source_page = NULL, "
                "source_quote = NULL WHERE document_id = ?", (document_id,)
            )
            self._conn.commit()

    # ------------------------------------------------------------ vocabulary
    def list_vocabulary(self, document_id: Optional[str] = None) -> List[dict]:
        with self._lock:
            if document_id:
                rows = self._conn.execute(
                    "SELECT * FROM vocabulary WHERE document_id = ? "
                    "ORDER BY normalized_word, created_at DESC", (document_id,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM vocabulary ORDER BY normalized_word, created_at DESC"
                ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def create_vocabulary(self, record: dict) -> dict:
        columns = tuple(record)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO vocabulary ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(record[column] for column in columns),
            )
            row = self._conn.execute(
                "SELECT * FROM vocabulary WHERE word_id = ?", (record["word_id"],)
            ).fetchone()
            self._conn.commit()
        return _row_to_dict(row)

    def update_vocabulary(self, word_id: str, changes: dict) -> dict:
        allowed = {"word", "normalized_word", "definition", "part_of_speech", "example", "document_id", "source_page", "source_quote", "definition_source", "updated_at"}
        if set(changes) - allowed:
            raise ValueError("unknown vocabulary columns")
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE vocabulary SET {', '.join(f'{column} = ?' for column in changes)} WHERE word_id = ?",
                (*changes.values(), word_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(word_id)
            row = self._conn.execute(
                "SELECT * FROM vocabulary WHERE word_id = ?", (word_id,)
            ).fetchone()
            self._conn.commit()
        return _row_to_dict(row)

    def delete_vocabulary(self, word_id: str) -> None:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM vocabulary WHERE word_id = ?", (word_id,))
            if cursor.rowcount == 0:
                raise KeyError(word_id)
            self._conn.commit()

    # --------------------------------------------------------- indexing queue
    def claim_for_indexing(self, now: str) -> Optional[dict]:
        """Atomically take the oldest ``queued`` document and mark it ``indexing``.

        The index worker calls this in a loop instead of draining an in-memory
        queue, so a restart that re-queues the whole library can never overflow
        a bounded queue and mark documents as failed.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT document_id FROM documents WHERE indexing_status = 'queued' "
                "ORDER BY updated_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            document_id = row["document_id"]
            self._conn.execute(
                "UPDATE documents SET indexing_status = 'indexing', "
                "indexing_error = NULL, updated_at = ? WHERE document_id = ?",
                (now, document_id),
            )
            self._conn.commit()
            claimed = self._conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return _row_to_dict(claimed)

    def count_indexing_backlog(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE indexing_status IN ('queued', 'indexing')"
            ).fetchone()
        return int(row[0]) if row else 0

    # -------------------------------------------------------------- migration
    def import_legacy(self, metadata_dir) -> int:
        """One-time import of ``<metadata_dir>/*.json`` records.

        Runs only while there are no source-backed records, so migrated
        catalogue-only books cannot prevent an older JSON library from being
        imported. The JSON files are left in place for rollback.
        """
        metadata_dir = Path(metadata_dir)
        if not metadata_dir.is_dir():
            return 0
        with self._lock:
            already_populated = self._conn.execute(
                "SELECT 1 FROM documents WHERE record_type != 'standalone' LIMIT 1"
            ).fetchone()
        if already_populated:
            return 0

        imported = 0
        for path in sorted(metadata_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            document_id = data.get("document_id")
            if not document_id:
                continue
            record = {column: data.get(column) for column in COLUMNS}
            record["record_type"] = record["record_type"] or "source"
            record["file_type"] = record["file_type"] or "pdf"
            record["filename"] = record["filename"] or document_id
            record["stored_filename"] = (
                record["stored_filename"] or f"{document_id}.pdf"
            )
            record["content_sha256"] = record["content_sha256"] or ""
            record["uploaded_at"] = record["uploaded_at"] or ""
            record["updated_at"] = (
                record["updated_at"] or record["uploaded_at"] or ""
            )
            record["indexing_status"] = record["indexing_status"] or "queued"
            for column in INT_COLUMNS:
                try:
                    record[column] = int(record[column] or 0)
                except (TypeError, ValueError):
                    record[column] = 0
            with self._lock:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO documents ({', '.join(COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in COLUMNS)})",
                    tuple(record[column] for column in COLUMNS),
                )
                self._conn.commit()
            imported += 1
        return imported


class OwnedBookStore:
    """Small registry for books the reader owns independently of files."""

    def __init__(self, connection):
        self._conn = connection
        self._lock = threading.RLock()

    def list_all(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM owned_books ORDER BY updated_at DESC, title COLLATE NOCASE"
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def create(self, record: dict) -> dict:
        columns = ("book_id", "title", "author", "notes", "pdf_less", "created_at", "updated_at")
        with self._lock:
            self._conn.execute(
                f"INSERT INTO owned_books ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(record.get(column) for column in columns),
            )
            row = self._conn.execute(
                "SELECT * FROM owned_books WHERE book_id = ?", (record["book_id"],)
            ).fetchone()
            self._conn.commit()
        return _row_to_dict(row)

    def update(self, book_id: str, changes: dict) -> dict:
        allowed = {"title", "author", "notes", "pdf_less", "updated_at"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unknown owned-book columns: {sorted(unknown)}")
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE owned_books SET {', '.join(f'{key} = ?' for key in changes)} WHERE book_id = ?",
                (*changes.values(), book_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(book_id)
            row = self._conn.execute(
                "SELECT * FROM owned_books WHERE book_id = ?", (book_id,)
            ).fetchone()
            self._conn.commit()
        return _row_to_dict(row)

    def delete(self, book_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM owned_books WHERE book_id = ?", (book_id,))
            self._conn.commit()
