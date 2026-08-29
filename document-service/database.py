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
)
UPDATABLE_COLUMNS = frozenset(COLUMNS) - {"document_id"}

INT_COLUMNS = (
    "pages",
    "chunks",
    "retrieval_units",
    "vector_dim",
    "pipeline_version",
    "index_schema_version",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id           TEXT PRIMARY KEY,
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
    index_schema_version  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documents_content_sha256 ON documents (content_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_uploaded_at ON documents (uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_indexing_status ON documents (indexing_status);
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
            self._conn.commit()

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
        values = tuple(metadata.get(column) for column in COLUMNS)
        placeholders = ", ".join("?" for _ in COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO documents ({', '.join(COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            self._conn.commit()
        return self.get(metadata["document_id"])

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
            self._conn.commit()
        return self.get(document_id)

    def delete(self, document_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM documents WHERE document_id = ?", (document_id,)
            )
            self._conn.commit()

    # -------------------------------------------------------------- migration
    def import_legacy(self, metadata_dir) -> int:
        """One-time import of ``<metadata_dir>/*.json`` records.

        Runs only while the table is empty, so it is a safe no-op on every
        start after the first. The JSON files are left in place for rollback.
        """
        metadata_dir = Path(metadata_dir)
        if not metadata_dir.is_dir():
            return 0
        with self._lock:
            already_populated = self._conn.execute(
                "SELECT 1 FROM documents LIMIT 1"
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
