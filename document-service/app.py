import asyncio
import collections
import concurrent.futures
import contextvars
import hashlib
import json
import logging
import logging.handlers
import os
import queue
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pymupdf
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import Headers, MutableHeaders

import generation
from chunking import build_semantic_groups, normalize_for_embedding, parse_typed_blocks
from conversations import ConversationStore
from database import MetadataStore
from embeddings import DEFAULT_MODEL as EMBEDDING_MODEL, embed_texts
from extraction import (
    assess_scanned,
    assess_text_layer,
    boilerplate_page_indices,
    describe_skipped_pages,
    extract_pages,
    garbled_page_indices,
)
from reranker import DEFAULT_MODEL as RERANK_MODEL, model_info, rerank
from vector_store import (
    FUSION_METHOD,
    INDEX_SCHEMA_VERSION,
    delete_document as delete_document_vectors,
    healthcheck as vector_healthcheck,
    search_vectors,
    upsert_chunks,
)


def _configure_logging() -> None:
    """Give the ``ai_librarian`` logger tree a formatted stderr handler.

    Under ``uvicorn app:app`` uvicorn configures only its own loggers, so without
    this the service's ``logger.info`` breadcrumbs (pages skipped, re-queue
    counts, recovered glyph maps) are dropped and only unformatted WARNING+
    reaches the container logs. ``propagate = False`` keeps uvicorn's own output
    untouched and avoids double lines.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    service_logger = logging.getLogger("ai_librarian")
    service_logger.handlers.clear()
    service_logger.addHandler(handler)
    service_logger.setLevel(getattr(logging, level, logging.INFO))
    service_logger.propagate = False


def _apply_offline_env() -> None:
    """``OFFLINE=1`` -> tell huggingface libraries never to reach the network.

    With the models already cached (a prebaked image or a ``warm_models.py``
    run), this skips the etag checks that would otherwise stall every model
    load on an air-gapped host.
    """
    if os.environ.get("OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


_apply_offline_env()
_configure_logging()
logger = logging.getLogger("ai_librarian")

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
DOCUMENTS_DIR = DATA_DIR / "documents"
METADATA_DIR = DATA_DIR / "metadata"
EXTRACTED_DIR = DATA_DIR / "extracted"
CHUNKS_DIR = DATA_DIR / "chunks"
JOBS_DIR = DATA_DIR / "jobs"
LOGS_DIR = DATA_DIR / "logs"
INGEST_ROOT = Path(os.environ.get("INGEST_ROOT", str(DATA_DIR / "inbox"))).resolve()

SEARCH_LOG_ENABLED = os.environ.get("SEARCH_LOG", "on").strip().lower() not in {
    "off", "0", "false", "no", ""
}
SEARCH_LOG_MAX_BYTES = int(os.environ.get("SEARCH_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
SEARCH_LOG_BACKUPS = int(os.environ.get("SEARCH_LOG_BACKUPS", "3"))

# When set, every endpoint except the health checks requires
# ``Authorization: Bearer <APP_TOKEN>``. Empty (the default) leaves the API open,
# which is the intended posture for a loopback / SSH-tunnel deployment.
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
_AUTH_EXEMPT_PATHS = {"/health", "/health/live", "/health/ready"}
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))
INGEST_QUEUE_SIZE = int(os.environ.get("INGEST_QUEUE_SIZE", "10"))
# The index worker pulls ``queued`` documents from the database rather than
# draining an in-memory backlog. This is only a fallback re-check interval in
# case a wake-up signal is ever missed.
INDEX_POLL_SECONDS = float(os.environ.get("INDEX_POLL_SECONDS", "30"))
RERANK_MAX_WORKERS = int(os.environ.get("RERANK_MAX_WORKERS", "2"))
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "15"))
# How many fused candidates the cross-encoder actually scores. Dense+sparse
# fusion is only a coarse filter; the reranker is what picks the winning
# passage, so it needs a wide enough pool. `rerank_k` from the request acts as
# a floor on top of this. The default ms-marco-MiniLM scores ~100 in ~1-2s on
# CPU; raise it further as the library grows (fusion over hundreds of documents
# is a coarse net), or drop it to ~25 with a heavier RERANK_MODEL.
RERANK_CANDIDATES = int(os.environ.get("RERANK_CANDIDATES", "100"))
# What text the cross-encoder scores against the query: "group" = the whole
# parent passage (default), "matched" = just the specific retrieval unit that
# won (a paragraph, an equation). "group" gives the reranker enough context to
# separate near-duplicates in dense survey prose; "matched" tends to saturate a
# 0..1 model's score on short fragments and bare headings. A request may override.
RERANK_PASSAGE = os.environ.get("RERANK_PASSAGE", "group").strip().lower()


def _parse_min_score(raw: str):
    """Reranker cutoff: a float, or None when disabled ('', 'off', 'none')."""
    raw = (raw or "").strip()
    if raw.lower() in {"", "off", "none", "disabled"}:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring unparseable RERANK_MIN_SCORE=%r; gate disabled", raw)
        return None


# Hard floor on the reranker score: hits below it are dropped, so a query with no
# real answer in the library comes back empty. -2.0 works with the default
# ms-marco-MiniLM: on the reference library every unanswerable probe scores below
# -3.5 while genuine answers (with bge-base retrieval feeding the reranker) score
# above -2. bge-reranker-base cannot be gated at all -- it scored a geometry
# sentence 0.93 for "chess en passant". Re-fit with `eval/harness.py calibrate`
# after changing RERANK_MODEL; set "off" to disable.
RERANK_MIN_SCORE = _parse_min_score(os.environ.get("RERANK_MIN_SCORE", "-2.0"))
# Soft signal instead: when the top reranked hit scores below this, the response
# is flagged `low_confidence` and the UI shows a "nothing clearly matched"
# banner without hiding anything. 0.0 suits MiniLM logits; raise it (~0.3) for a
# 0..1 model. `off` disables the flag.
RERANK_LOWCONF_SCORE = _parse_min_score(os.environ.get("RERANK_LOWCONF_SCORE", "0.0"))
# Ask mode: keep any single document from taking more than this many of the
# passages sent to the model, so a large library answers with breadth instead of
# ten near-identical paragraphs from one chapter. 0 disables the cap.
ASK_MAX_PER_DOC = int(os.environ.get("ASK_MAX_PER_DOC", "3"))
# Ask mode: drop a passage whose word overlap with an already-kept passage
# exceeds this (near-duplicate removal). 0/1 disables.
ASK_DEDUP_JACCARD = float(os.environ.get("ASK_DEDUP_JACCARD", "0.6"))
CHUNK_TARGET_TOKENS = int(os.environ.get("CHUNK_TARGET_TOKENS", "180"))
CHUNK_SOFT_MAX_TOKENS = int(os.environ.get("CHUNK_SOFT_MAX_TOKENS", "220"))
CHUNK_HARD_MAX_TOKENS = int(os.environ.get("CHUNK_HARD_MAX_TOKENS", "240"))
CHUNK_OVERLAP_TOKENS = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "32"))
DOC_ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")
PIPELINE_VERSION = 9

for directory in (
    DOCUMENTS_DIR, METADATA_DIR, EXTRACTED_DIR, CHUNKS_DIR, JOBS_DIR, LOGS_DIR, INGEST_ROOT
):
    directory.mkdir(parents=True, exist_ok=True)


search_logger = logging.getLogger("ai_librarian.search")


def _configure_search_log() -> None:
    """One JSON line per query at ``DATA_DIR/logs/search.jsonl`` (rotated).

    Local-only diagnostics for tuning retrieval: the query, the candidate
    budget, and each returned hit's page and dense/rerank scores — enough to
    reproduce and explain a result after the fact. Never leaves the server.
    Disable with ``SEARCH_LOG=off``.
    """
    for handler in list(search_logger.handlers):
        handler.close()
    search_logger.handlers.clear()
    search_logger.propagate = False
    search_logger.setLevel(logging.INFO)
    if not SEARCH_LOG_ENABLED:
        search_logger.addHandler(logging.NullHandler())
        return
    handler = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "search.jsonl",
        maxBytes=SEARCH_LOG_MAX_BYTES,
        backupCount=SEARCH_LOG_BACKUPS,
        encoding="utf-8",
        delay=True,  # do not create the file until the first query
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    search_logger.addHandler(handler)


_configure_search_log()


def _rounded(value, places: int = 4):
    return round(value, places) if isinstance(value, (int, float)) else None


def _hit_log_fields(hit: dict) -> dict:
    payload = hit.get("payload") or {}
    return {
        "document_id": payload.get("document_id"),
        "filename": payload.get("filename"),
        "page": payload.get("page"),
        "group_id": payload.get("group_id"),
        "retrieval_kind": payload.get("retrieval_kind"),
        "dense_score": _rounded(hit.get("score")),
        "rerank_score": _rounded(hit.get("rerank_score")),
    }


def record_search(
    request: "SearchRequest",
    hits: List[dict],
    candidate_count: int,
    latency_ms: float,
    rerank_min_score: Optional[float] = None,
    rerank_dropped: int = 0,
) -> None:
    """Append one search-log line. Never raises into the request path."""
    if not SEARCH_LOG_ENABLED:
        return
    try:
        filters = {
            key: value
            for key, value in {
                "document_id": request.document_id,
                "filename": request.filename,
            }.items()
            if value
        }
        entry = {
            "ts": utc_now(),
            "query": request.query,
            "top_k": request.top_k,
            "rerank": request.rerank,
            "rerank_k": request.rerank_k if request.rerank else None,
            "rerank_min_score": rerank_min_score,
            "rerank_passage": (request.rerank_passage or RERANK_PASSAGE) if request.rerank else None,
            "rerank_dropped": rerank_dropped or None,
            "fusion": request.fusion or FUSION_METHOD,
            "dense_weight": request.dense_weight,
            "filters": filters or None,
            "candidate_count": candidate_count,
            "result_count": len(hits),
            "latency_ms": round(latency_ms, 1),
            "hits": [_hit_log_fields(hit) for hit in hits],
        }
        search_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        logger.exception("Could not write search-log entry")


def record_ask(
    request: "AskRequest",
    hits: List[dict],
    candidate_count: int,
    latency_ms: float,
    rerank_dropped: int = 0,
    model: Optional[str] = None,
) -> None:
    """Append one ask-log line (same JSONL file as search). Never raises."""
    if not SEARCH_LOG_ENABLED:
        return
    try:
        entry = {
            "ts": utc_now(),
            "mode": "ask",
            "ask_mode": request.mode,
            "query": request.question,
            "model": model,
            "history_turns": len(request.history),
            "filters": {
                key: value
                for key, value in {
                    "document_id": request.document_id,
                    "filename": request.filename,
                }.items()
                if value
            }
            or None,
            "candidate_count": candidate_count,
            "result_count": len(hits),
            "rerank_dropped": rerank_dropped or None,
            "latency_ms": round(latency_ms, 1),
            "hits": [_hit_log_fields(hit) for hit in hits],
        }
        search_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        logger.exception("Could not write ask-log entry")


STORE = MetadataStore(DATA_DIR / "library.db")
_imported = STORE.import_legacy(METADATA_DIR)
if _imported:
    logger.info("Imported %d legacy metadata records into %s", _imported, DATA_DIR / "library.db")

# Ask-mode conversations (its own DB file, see conversations.py).
CONVERSATIONS = ConversationStore(DATA_DIR / "conversations.db")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=50)
    document_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{12}$")
    filename: Optional[str] = Field(default=None, max_length=255)
    rerank: bool = False
    # A floor on how many fused candidates the cross-encoder scores; the server's
    # RERANK_CANDIDATES applies on top of it. Left at the default, the pool is
    # RERANK_CANDIDATES.
    rerank_k: int = Field(default=20, ge=1, le=200)
    # Overrides RERANK_MIN_SCORE for this request. Only used when rerank=True.
    # A hit is kept when its reranker score is >= this value.
    rerank_min_score: Optional[float] = Field(default=None, ge=-100, le=100)
    # Overrides RERANK_PASSAGE for this request: "group" or "matched".
    rerank_passage: Optional[str] = Field(default=None, pattern=r"^(group|matched)$")
    # Override how the dense and sparse lists are merged (FUSION_METHOD) and, for
    # "rsf", how much weight the semantic list gets (FUSION_DENSE_WEIGHT).
    fusion: Optional[str] = Field(default=None, pattern=r"^(rrf|dbsf|rsf)$")
    dense_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_text_chars: int = Field(default=20_000, ge=100, le=50_000)


class ConversationBody(BaseModel):
    # The client owns the turn objects (role, content, sources, …); the server
    # stores them verbatim and never interprets them beyond the size cap.
    messages: list = Field(default_factory=list)
    model: Optional[str] = Field(default=None, max_length=120)
    title: Optional[str] = Field(default=None, max_length=200)


class IngestFolderRequest(BaseModel):
    folder: Optional[str] = Field(default=None, max_length=1_024)


class AskTurn(BaseModel):
    role: str = Field(pattern=r"^(user|assistant)$")
    content: str = Field(min_length=1, max_length=20_000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    # Prior turns of this conversation, oldest first. The client keeps the
    # history; the server is stateless and re-retrieves from the new question.
    history: List[AskTurn] = Field(default_factory=list, max_length=20)
    document_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{12}$")
    filename: Optional[str] = Field(default=None, max_length=255)
    top_k: int = Field(default=10, ge=1, le=20)
    # "provider:model" from GET /ask/models; unknown or absent -> the server default.
    model: Optional[str] = Field(default=None, max_length=120)
    # "quick" = one grounded pass; "thorough" = map-reduce over a wider pool.
    mode: str = Field(default="quick", pattern=r"^(quick|thorough)$")
    # Per-request cloud API keys the browser holds (it never persists them
    # server-side). {"anthropic"|"openai"|"google": "<key>"}. Never logged.
    provider_keys: Optional[dict] = None


@dataclass(frozen=True)
class IndexTask:
    document_id: str
    ingest_job_id: Optional[str] = None
    ingest_file_index: Optional[int] = None


# A coalescing wake-up channel for the index worker: a single slot is enough
# because the worker always drains every ``queued`` document once woken.
INDEX_SIGNAL = queue.Queue(maxsize=1)
INGEST_QUEUE = queue.Queue(maxsize=INGEST_QUEUE_SIZE)
SHUTDOWN = threading.Event()
# document_id -> (ingest_job_id, ingest_file_index) for documents that came in
# through a folder-ingest job, so the worker can report progress back to it.
INGEST_LINKS = {}
INGEST_LINKS_LOCK = threading.Lock()
JOB_STATUS = {}
JOB_STATUS_LOCK = threading.RLock()
DOCUMENT_LOCKS = {}
DOCUMENT_LOCKS_LOCK = threading.Lock()
WORKER_THREADS = []
RERANK_EXECUTOR = None


def signal_index_worker() -> None:
    try:
        INDEX_SIGNAL.put_nowait(True)
    except queue.Full:
        pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_id() -> str:
    return uuid.uuid4().hex[:12]


def title_from_filename(filename: str) -> str:
    name = Path(filename).stem
    return name.replace("_", " ").replace("-", " ").strip().title()


def atomic_write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_doc_id(doc_id: str) -> str:
    if not DOC_ID_PATTERN.fullmatch(doc_id):
        raise HTTPException(status_code=404, detail="document not found")
    return doc_id


def read_metadata(doc_id: str) -> dict:
    validate_doc_id(doc_id)
    metadata = STORE.get(doc_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="document not found")
    return metadata


def update_metadata(doc_id: str, **changes) -> dict:
    changes["updated_at"] = utc_now()
    try:
        return STORE.update(doc_id, changes)
    except KeyError as exc:
        raise FileNotFoundError(f"metadata for {doc_id} does not exist") from exc


def list_metadata() -> List[dict]:
    return STORE.list_all()


def find_duplicate(content_sha256: str) -> Optional[dict]:
    return STORE.find_by_hash(content_sha256)


def get_document_lock(doc_id: str) -> threading.Lock:
    with DOCUMENT_LOCKS_LOCK:
        return DOCUMENT_LOCKS.setdefault(doc_id, threading.Lock())


def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def save_job(job: dict) -> None:
    job["updated_at"] = utc_now()
    atomic_write_json(job_path(job["job_id"]), job)


def load_job(job_id: str) -> Optional[dict]:
    if not DOC_ID_PATTERN.fullmatch(job_id):
        return None
    path = job_path(job_id)
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read job file %s", path)
        return None


def refresh_ingest_job(job_id: str) -> None:
    with JOB_STATUS_LOCK:
        job = JOB_STATUS.get(job_id)
        if not job:
            return
        statuses = [entry["status"] for entry in job["files"]]
        terminal = {"indexed", "duplicate", "error"}
        if not statuses:
            job["state"] = "done"
        elif all(status in terminal for status in statuses):
            errors = sum(status == "error" for status in statuses)
            if errors == len(statuses):
                job["state"] = "error"
            elif errors:
                job["state"] = "partial"
            else:
                job["state"] = "done"
        elif any(status == "indexing" for status in statuses):
            job["state"] = "indexing"
        save_job(job)


_TERMINAL_INGEST_STATUSES = {"indexed", "duplicate", "error"}


def update_ingest_entry(document_id: str, status: str, error: Optional[str] = None) -> None:
    with INGEST_LINKS_LOCK:
        link = INGEST_LINKS.get(document_id)
        if link and status in _TERMINAL_INGEST_STATUSES:
            INGEST_LINKS.pop(document_id, None)
    if not link:
        return
    job_id, file_index = link
    with JOB_STATUS_LOCK:
        job = JOB_STATUS.get(job_id)
        if not job or file_index >= len(job["files"]):
            return
        entry = job["files"][file_index]
        entry["status"] = status
        if error:
            entry["error"] = error
        else:
            entry.pop("error", None)
        save_job(job)
    refresh_ingest_job(job_id)


def enqueue_index(task: IndexTask) -> None:
    """Mark a document ``queued`` and wake the worker.

    The worker owns the backlog (it pulls ``queued`` rows from the database), so
    this never blocks and never fails a document for lack of queue space. The
    folder-ingest progress link is registered before the status flips to
    ``queued`` so the worker cannot claim the document before it is trackable.
    """
    if task.ingest_job_id is not None and task.ingest_file_index is not None:
        with INGEST_LINKS_LOCK:
            INGEST_LINKS[task.document_id] = (task.ingest_job_id, task.ingest_file_index)
    update_metadata(task.document_id, indexing_status="queued", indexing_error=None)
    signal_index_worker()


def _index_one_document(metadata: dict) -> None:
    """Embed and upsert one already-claimed (``indexing``) document."""
    doc_id = metadata["document_id"]
    with get_document_lock(doc_id):
        try:
            if metadata.get("pipeline_version") != PIPELINE_VERSION:
                # Queued before an extraction-pipeline upgrade: rebuild its
                # blocks and chunks from the stored PDF before embedding.
                rebuild_document_artifacts(metadata)
                update_metadata(doc_id, indexing_status="indexing", indexing_error=None)
            update_ingest_entry(doc_id, "indexing")
            chunks = read_json(CHUNKS_DIR / f"{doc_id}.json")
            delete_document_vectors(doc_id)
            vector_dim = 0
            for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
                batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
                vectors = embed_texts([chunk.get("embedding_text") or chunk["text"] for chunk in batch])
                indexed_batch = [{**chunk, "embedding": vector} for chunk, vector in zip(batch, vectors)]
                if indexed_batch:
                    vector_dim = len(indexed_batch[0]["embedding"])
                    upsert_chunks(indexed_batch)
            update_metadata(
                doc_id,
                indexing_status="indexed",
                indexing_error=None,
                indexed_at=utc_now(),
                embedding_model=EMBEDDING_MODEL,
                vector_dim=vector_dim,
                index_schema_version=INDEX_SCHEMA_VERSION,
            )
            update_ingest_entry(doc_id, "indexed")
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            logger.exception("Indexing failed for document %s", doc_id)
            try:
                delete_document_vectors(doc_id)
            except Exception:
                logger.exception("Could not clean partial vectors for %s", doc_id)
            try:
                update_metadata(doc_id, indexing_status="error", indexing_error=message)
            except Exception:
                logger.exception("Could not persist indexing error for %s", doc_id)
            update_ingest_entry(doc_id, "error", message)


def index_worker() -> None:
    """Drain every ``queued`` document whenever woken.

    Pulling work from the database (rather than a bounded in-memory queue) means
    a restart that re-queues the entire library cannot overflow anything or
    mark documents as failed for lack of space.
    """
    while not SHUTDOWN.is_set():
        try:
            INDEX_SIGNAL.get(timeout=INDEX_POLL_SECONDS)
        except queue.Empty:
            pass
        while not SHUTDOWN.is_set():
            try:
                claimed = STORE.claim_for_indexing(utc_now())
            except Exception:
                logger.exception("Could not claim the next document for indexing")
                break
            if claimed is None:
                break
            _index_one_document(claimed)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_document_artifacts(
    stored_path: Path, filename: str, doc_id: str
) -> tuple[int, List[dict], int, Optional[str]]:
    pages = extract_pages(stored_path)
    if not any(page.get("text", "").strip() for page in pages):
        raise ValueError("the PDF contains no extractable text; scanned PDFs require OCR")
    fatal_reason = assess_scanned(pages) or assess_text_layer(pages)
    if fatal_reason:
        raise ValueError(fatal_reason)

    total_pages = len(pages)
    garbled = set(garbled_page_indices(pages))
    extraction_notes = None
    if garbled:
        # A minority of the pages are OCR garbage. Drop just those and index
        # the rest rather than refusing the whole document.
        pages = [page for index, page in enumerate(pages) if index not in garbled]
        extraction_notes = describe_skipped_pages(len(garbled), total_pages)
        logger.info("Skipped %d corrupt page(s) of %s", len(garbled), filename)

    # Contents / index / bibliography pages: keyword-dense, no retrievable prose.
    boilerplate = set(boilerplate_page_indices(pages))
    if boilerplate and len(boilerplate) <= len(pages) * 0.4:
        pages = [page for index, page in enumerate(pages) if index not in boilerplate]
        logger.info(
            "Skipped %d front/back-matter page(s) of %s", len(boilerplate), filename
        )
    elif boilerplate:
        logger.warning(
            "Boilerplate detection flagged %d/%d pages of %s; ignoring as unreliable",
            len(boilerplate),
            len(pages),
            filename,
        )

    blocks = parse_typed_blocks(pages)
    groups = build_semantic_groups(
        blocks,
        target_tokens=CHUNK_TARGET_TOKENS,
        soft_max_tokens=CHUNK_SOFT_MAX_TOKENS,
        hard_max_tokens=CHUNK_HARD_MAX_TOKENS,
        overlap_tokens=CHUNK_OVERLAP_TOKENS,
    )
    chunks = []
    for group_index, group in enumerate(groups):
        group_id = uuid.uuid4().hex[:12]
        for retrieval_index, retrieval_unit in enumerate(group["retrieval_units"]):
            retrieval_text = retrieval_unit["text"]
            chunks.append(
                {
                    "chunk_id": uuid.uuid4().hex[:12],
                    "group_id": group_id,
                    "document_id": doc_id,
                    "filename": filename,
                    "page": group["page"],
                    "page_end": group["page_end"],
                    "chunk_index": len(chunks),
                    "group_index": group_index,
                    "retrieval_index": retrieval_index,
                    "retrieval_kind": retrieval_unit["kind"],
                    "text": group["text"],
                    "lead_in": group.get("lead_in", ""),
                    "retrieval_text": retrieval_text,
                    "embedding_text": normalize_for_embedding(retrieval_text),
                    "token_count": retrieval_unit["token_count"],
                    "group_token_count": group["token_count"],
                    "block_types": group["block_types"],
                    "protected_type": group["protected_type"],
                    "format": "markdown",
                }
            )
    if not chunks:
        raise ValueError("the PDF produced no searchable text chunks")

    atomic_write_json(
        EXTRACTED_DIR / f"{doc_id}.json",
        {
            "format": "typed-markdown",
            "pages": pages,
            "blocks": [block.as_dict() for block in blocks],
        },
    )
    atomic_write_json(CHUNKS_DIR / f"{doc_id}.json", chunks)
    return total_pages, chunks, len(groups), extraction_notes


def rebuild_document_artifacts(metadata: dict) -> dict:
    doc_id = metadata["document_id"]
    stored_path = DOCUMENTS_DIR / metadata["stored_filename"]
    if not stored_path.exists():
        raise FileNotFoundError("stored PDF is missing; upload the document again")
    page_count, chunks, group_count, extraction_notes = build_document_artifacts(
        stored_path, metadata["filename"], doc_id
    )
    return update_metadata(
        doc_id,
        pages=page_count,
        chunks=group_count,
        retrieval_units=len(chunks),
        pipeline_version=PIPELINE_VERSION,
        index_schema_version=0,
        indexing_status="queued",
        indexing_error=None,
        extraction_notes=extraction_notes,
    )


def create_document(stored_path: Path, filename: str, content_sha256: str) -> dict:
    doc_id = stored_path.stem
    try:
        page_count, chunks, group_count, extraction_notes = build_document_artifacts(
            stored_path, filename, doc_id
        )

        now = utc_now()
        metadata = {
            "document_id": doc_id,
            "filename": filename,
            "file_type": "pdf",
            "title": title_from_filename(filename),
            "stored_filename": stored_path.name,
            "content_sha256": content_sha256,
            "uploaded_at": now,
            "updated_at": now,
            "pages": page_count,
            "chunks": group_count,
            "retrieval_units": len(chunks),
            "extraction_notes": extraction_notes,
            # "pending" until enqueue_index flips it to "queued": the index
            # worker only claims "queued" rows, so a folder-ingest job always
            # has its progress link registered before the worker can pick it up.
            "indexing_status": "pending",
            "indexing_error": None,
            "embedding_model": EMBEDDING_MODEL,
            "vector_dim": 0,
            "pipeline_version": PIPELINE_VERSION,
            "index_schema_version": 0,
        }
        return STORE.create(metadata)
    except Exception:
        stored_path.unlink(missing_ok=True)
        (EXTRACTED_DIR / f"{doc_id}.json").unlink(missing_ok=True)
        (CHUNKS_DIR / f"{doc_id}.json").unlink(missing_ok=True)
        STORE.delete(doc_id)
        raise


def copy_folder_document(source: Path) -> tuple[dict, bool]:
    content_sha256 = hash_file(source)
    duplicate = find_duplicate(content_sha256)
    if duplicate:
        return duplicate, True

    doc_id = generate_id()
    stored_path = DOCUMENTS_DIR / f"{doc_id}.pdf"
    with source.open("rb") as source_handle, stored_path.open("wb") as destination_handle:
        shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
    return create_document(stored_path, source.name, content_sha256), False


def resolve_ingest_folder(requested: Optional[str]) -> Path:
    candidate = INGEST_ROOT if not requested else Path(requested)
    if not candidate.is_absolute():
        candidate = INGEST_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail="ingest folder does not exist") from exc
    if resolved != INGEST_ROOT and INGEST_ROOT not in resolved.parents:
        raise HTTPException(status_code=403, detail="folder must be inside the configured ingest root")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="ingest path is not a folder")
    return resolved


def ingest_worker() -> None:
    while True:
        item = INGEST_QUEUE.get()
        try:
            if item is None:
                return
            job_id, folder = item
            with JOB_STATUS_LOCK:
                job = JOB_STATUS[job_id]
                job["state"] = "processing"
                save_job(job)

            files = sorted(path for path in Path(folder).iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
            for pdf in files:
                with JOB_STATUS_LOCK:
                    job = JOB_STATUS[job_id]
                    file_index = len(job["files"])
                    job["files"].append({"file": pdf.name, "status": "processing"})
                    save_job(job)
                try:
                    metadata, duplicate = copy_folder_document(pdf)
                    with JOB_STATUS_LOCK:
                        entry = JOB_STATUS[job_id]["files"][file_index]
                        entry["document_id"] = metadata["document_id"]
                        entry["status"] = "duplicate" if duplicate else "queued"
                        save_job(JOB_STATUS[job_id])
                    if not duplicate:
                        enqueue_index(
                            IndexTask(metadata["document_id"], job_id, file_index)
                        )
                except Exception as exc:
                    logger.exception("Folder ingestion failed for %s", pdf)
                    with JOB_STATUS_LOCK:
                        entry = JOB_STATUS[job_id]["files"][file_index]
                        entry["status"] = "error"
                        entry["error"] = str(exc) or exc.__class__.__name__
                        save_job(JOB_STATUS[job_id])
            refresh_ingest_job(job_id)
        except Exception:
            logger.exception("Ingest worker failed")
            if item is not None:
                with JOB_STATUS_LOCK:
                    job = JOB_STATUS.get(item[0])
                    if job:
                        job["state"] = "error"
                        job["error"] = "ingest worker failed; see service logs"
                        save_job(job)
        finally:
            INGEST_QUEUE.task_done()


def recover_interrupted_work() -> None:
    for path in JOBS_DIR.glob("*.json"):
        try:
            job = read_json(path)
            if job.get("state") not in {"done", "partial", "error", "interrupted"}:
                job["state"] = "interrupted"
                job["error"] = "the service restarted; document indexing resumes separately"
                save_job(job)
            with JOB_STATUS_LOCK:
                JOB_STATUS[job["job_id"]] = job
        except Exception:
            logger.exception("Could not recover job %s", path)

    # Mark everything that needs (re)indexing as ``queued`` and let the worker
    # pull it. The pipeline rebuild for stale documents happens in the worker,
    # so a crash partway through recovery leaves consistent database state.
    requeued = 0
    for metadata in list_metadata():
        doc_id = metadata.get("document_id")
        if not doc_id:
            continue
        try:
            stale_pipeline = metadata.get("pipeline_version") != PIPELINE_VERSION
            needs_index = (
                stale_pipeline
                or metadata.get("indexing_status") in {"pending", "queued", "indexing"}
                or metadata.get("index_schema_version") != INDEX_SCHEMA_VERSION
                or metadata.get("embedding_model") != EMBEDDING_MODEL
            )
            if not needs_index:
                continue
            source_ready = (
                (DOCUMENTS_DIR / metadata["stored_filename"]).exists()
                if stale_pipeline
                else (CHUNKS_DIR / f"{doc_id}.json").exists()
            )
            if not source_ready:
                continue
            if metadata.get("indexing_status") != "queued":
                update_metadata(doc_id, indexing_status="queued", indexing_error=None)
            requeued += 1
        except Exception as exc:
            logger.exception("Could not queue %s for indexing after restart", doc_id)
            try:
                update_metadata(doc_id, indexing_status="error", indexing_error=str(exc))
            except Exception:
                logger.exception("Could not persist recovery error for %s", doc_id)

    if requeued:
        logger.info("Re-queued %d document(s) for indexing after restart", requeued)
    signal_index_worker()


def start_workers() -> None:
    global WORKER_THREADS, RERANK_EXECUTOR
    if any(thread.is_alive() for thread in WORKER_THREADS):
        return
    SHUTDOWN.clear()
    RERANK_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
        max_workers=RERANK_MAX_WORKERS,
        thread_name_prefix="reranker",
    )
    WORKER_THREADS = [
        threading.Thread(target=index_worker, daemon=True, name="index-worker"),
        threading.Thread(target=ingest_worker, daemon=True, name="ingest-worker"),
        threading.Thread(target=recover_interrupted_work, daemon=True, name="recovery-worker"),
    ]
    for thread in WORKER_THREADS:
        thread.start()


def stop_workers() -> None:
    global RERANK_EXECUTOR
    SHUTDOWN.set()
    signal_index_worker()
    try:
        INGEST_QUEUE.put_nowait(None)
    except queue.Full:
        logger.warning("Could not enqueue ingest worker shutdown signal")
    for thread in WORKER_THREADS:
        thread.join(timeout=5)
    if RERANK_EXECUTOR is not None:
        RERANK_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        RERANK_EXECUTOR = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_workers()
    try:
        yield
    finally:
        stop_workers()


def _request_authorized(scope) -> bool:
    if not APP_TOKEN or scope["method"] == "OPTIONS":
        return True
    if scope["path"] in _AUTH_EXEMPT_PATHS:
        return True
    scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(token, APP_TOKEN)


class RequestGateMiddleware:
    """Assign a request id (echoed as ``X-Request-ID``) and enforce ``APP_TOKEN``.

    Pure ASGI so the id context var reliably reaches the endpoint. Sits inside
    CORS, so preflight and CORS headers on a 401 are still handled.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        rid = uuid.uuid4().hex[:8]
        token = REQUEST_ID.set(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = rid
            await send(message)

        try:
            if not _request_authorized(scope):
                await JSONResponse(
                    {"detail": "missing or invalid API token"}, status_code=401
                )(scope, receive, send_with_id)
                return
            await self.app(scope, receive, send_with_id)
        finally:
            REQUEST_ID.reset(token)


def _sanitized_http_error(
    status: int, message: str, exc: BaseException, *, log_context: str = ""
) -> HTTPException:
    """Log the real exception; return a generic detail the client can quote."""
    rid = REQUEST_ID.get()
    logger.exception("%s%s [request %s]", message, f" ({log_context})" if log_context else "", rid)
    return HTTPException(status_code=status, detail=f"{message} (request {rid})")


app = FastAPI(
    title="AI Librarian",
    description="Local-first document ingestion and semantic search.",
    version="0.3.0",
    lifespan=lifespan,
)

# Added before CORS so CORS ends up the outermost middleware.
app.add_middleware(RequestGateMiddleware)

cors_value = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
cors_origins = [origin.strip() for origin in cors_value.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or [],
    allow_credentials="*" not in cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


def _rerank_passage(payload: dict, mode: Optional[str] = None) -> str:
    """The text the cross-encoder scores for one hit (see RERANK_PASSAGE)."""
    group = (payload.get("text") or "").strip()
    matched = (payload.get("retrieval_text") or "").strip()
    if (mode or RERANK_PASSAGE) == "matched" and 40 <= len(matched) < len(group):
        return matched
    return group or matched or (payload.get("embedding_text") or "")


async def run_reranker(
    query: str, hits: List[dict], passage_mode: Optional[str] = None
) -> List[dict]:
    if not hits or RERANK_EXECUTOR is None:
        return hits
    passages = [_rerank_passage(hit.get("payload") or {}, passage_mode) for hit in hits]
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(RERANK_EXECUTOR, rerank, query, passages)
    try:
        scores = await asyncio.wait_for(future, timeout=RERANK_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Reranking timed out after %.1f seconds", RERANK_TIMEOUT)
        return hits
    except Exception:
        logger.exception("Reranking failed; using vector order")
        return hits
    for hit, score in zip(hits, scores):
        hit["rerank_score"] = score
    return sorted(hits, key=lambda hit: hit.get("rerank_score", float("-inf")), reverse=True)


def coalesce_group_hits(hits: List[dict]) -> List[dict]:
    """Keep the strongest child hit while returning each parent group once."""
    grouped = {}
    order = []
    for hit in hits:
        payload = hit.get("payload") or {}
        key = (
            payload.get("document_id"),
            payload.get("group_id") or payload.get("chunk_id") or hit.get("id"),
        )
        if key not in grouped:
            grouped[key] = hit
            order.append(key)
            continue
        hit_score = hit.get("score")
        grouped_score = grouped[key].get("score")
        if (hit_score if hit_score is not None else float("-inf")) > (
            grouped_score if grouped_score is not None else float("-inf")
        ):
            grouped[key] = hit
    return [grouped[key] for key in order]


_WORD_RE = re.compile(r"[a-z0-9]+")


def _word_set(text: str) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


# Conversational scaffolding that carries no topic signal but drags the
# cross-encoder's score down (it is a short-query passage ranker). Stripped from
# the string handed to retrieval only — the LLM still gets the real question.
_QUERY_LEAD_RE = re.compile(
    r"^(?:"
    r"please|kindly|hey|"
    r"can you|could you|would you|will you|"
    r"i(?:'d| would) like to know|i want to know|i'm curious|help me|"
    r"tell me|show me|find|give me (?:a|an)?\s*(?:summary|overview|rundown)?(?:\s+of)?|"
    r"according to (?:my|the) (?:books?|library|notes?|collection),?|"
    r"across (?:my|the) (?:books?|library|notes?|collection),?|"
    r"in (?:my|the) (?:books?|library|notes?|collection),?|"
    r"from (?:my|the) (?:books?|library|notes?|collection),?|"
    r"what do (?:my|the) (?:books?|library|notes?)\s+(?:say|tell me)(?:\s+about)?|"
    r"what(?:'s| is| are| was| were)|"
    r"how (?:is|are|was|were|does|do|did|has|have|can|could)|"
    r"why (?:is|are|does|do|did)|"
    r"when (?:is|are|did|does)|where (?:is|are)|who (?:is|are|was|were)|"
    r"summar(?:ise|ize)(?:\s+(?:what(?:'s| is)|the))?|"
    r"(?:currently\s+)?known|about|"
    r"compare (?:and contrast\s+)?(?:how\s+)?|contrast\s+|"
    r"explain|describe|discuss|outline|list|overview of|"
    r"do (?:my|the) (?:books?|library)\s+(?:say|mention|cover|discuss|address)|"
    r"how|why|when|where|it|this|that"
    r")\b[\s,:-]*",
    re.IGNORECASE,
)
_QUERY_TRAIL_RE = re.compile(
    r"[\s,]*(?:"
    r"(?:is|are|was|were)?\s*(?:it|this|that|they|there)?\s*"
    r"(?:described|discussed|covered|treated|explained|mentioned|presented|"
    r"handled|addressed|portrayed|characteri[sz]ed|defined)|"
    r"in (?:my|the) (?:books?|library|notes?|collection)|"
    r"across (?:my|the) (?:books?|library|notes?)|"
    r"according to (?:my|the) (?:books?|library)"
    r")?[\s?]*$",
    re.IGNORECASE,
)


_QUERY_GENERIC_WORDS = {
    "it", "this", "that", "they", "them", "these", "those", "one", "work",
    "why", "how", "what", "who", "when", "where", "thing", "things", "about",
    "known", "described", "the", "a", "an",
}


def clean_query(text: str) -> str:
    """Strip question framing so the reranker scores on topic, not phrasing.

    Falls back to the original when stripping would gut it — a bare "why?", or a
    query that was almost entirely scaffolding.
    """
    original = (text or "").strip()
    cleaned = original
    for _ in range(5):  # peel nested lead-ins: "tell me how is X ..."
        stepped = _QUERY_LEAD_RE.sub("", cleaned, count=1).strip()
        if stepped == cleaned:
            break
        cleaned = stepped
    cleaned = _QUERY_TRAIL_RE.sub("", cleaned).strip(" ?,.-:")
    cleaned = re.sub(r"\s+", " ", cleaned)
    words = re.findall(r"[a-zA-Z]+", cleaned)
    if len(cleaned) < 3 or not words or all(w.lower() in _QUERY_GENERIC_WORDS for w in words):
        return original
    return cleaned


def diversify_hits(
    hits: List[dict], top_k: int, max_per_doc: int = 0, dedup_jaccard: float = 1.0
) -> List[dict]:
    """Trim a ranked list to `top_k`, favouring breadth.

    Walks best-first, skipping a hit once its document already has `max_per_doc`
    picks, or when its wording overlaps an already-kept hit by more than
    `dedup_jaccard`. Falls back to the plain top-k if the constraints would
    leave fewer than `top_k` and there are demoted hits to backfill with.
    """
    if not hits:
        return hits
    per_doc: dict = {}
    kept: List[dict] = []
    kept_words: List[set] = []
    demoted: List[dict] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        doc = payload.get("document_id")
        if max_per_doc and per_doc.get(doc, 0) >= max_per_doc:
            demoted.append(hit)
            continue
        if 0 < dedup_jaccard < 1:
            words = _word_set(payload.get("text") or payload.get("retrieval_text") or "")
            if words and any(
                len(words & seen) / len(words | seen) > dedup_jaccard for seen in kept_words
            ):
                demoted.append(hit)
                continue
            kept_words.append(words)
        per_doc[doc] = per_doc.get(doc, 0) + 1
        kept.append(hit)
        if len(kept) >= top_k:
            return kept
    return (kept + demoted)[:top_k]


async def retrieve(
    query: str,
    top_k: int,
    document_id: Optional[str],
    filename: Optional[str],
    rerank_enabled: bool,
    rerank_k: int,
    min_score: Optional[float] = None,
    fusion: Optional[str] = None,
    dense_weight: Optional[float] = None,
    rerank_passage: Optional[str] = None,
    max_per_doc: int = 0,
    dedup_jaccard: float = 1.0,
) -> tuple[List[dict], int, int, int]:
    query_vector = await asyncio.to_thread(lambda: embed_texts([query], kind="query")[0])
    filters = {
        key: value
        for key, value in {"document_id": document_id, "filename": filename}.items()
        if value
    }
    rerank_pool = max(rerank_k, RERANK_CANDIDATES) if rerank_enabled else 0
    desired_groups = max(top_k, rerank_pool) if rerank_enabled else top_k
    # A parent group can have several independently searchable child blocks.
    # Fetch extra candidates so those siblings do not crowd out other groups.
    candidate_count = min(400, max(desired_groups * 8, 40))
    hits = await asyncio.to_thread(
        search_vectors,
        query_vector,
        candidate_count,
        filters or None,
        query,
        fusion=fusion,
        dense_weight=dense_weight,
    )
    hits = coalesce_group_hits(hits)
    dropped = 0
    if rerank_enabled:
        hits = await run_reranker(query, hits[:rerank_pool], rerank_passage)
        # Apply the cutoff only when reranking actually produced scores; on a
        # timeout or failure run_reranker returns hits unscored in vector order,
        # and gating on a missing score would wrongly drop everything.
        scored = any(hit.get("rerank_score") is not None for hit in hits)
        if min_score is not None and scored:
            kept = [
                hit for hit in hits
                if hit.get("rerank_score") is not None and hit["rerank_score"] >= min_score
            ]
            dropped = len(hits) - len(kept)
            hits = kept
    relevant_count = len(hits)
    if max_per_doc or (0 < dedup_jaccard < 1):
        hits = diversify_hits(hits, top_k, max_per_doc, dedup_jaccard)
    return hits[:top_k], candidate_count, dropped, relevant_count


_TERMINAL_PUNCTUATION = tuple(".!?\"')”’»…")


def format_hits(hits: List[dict], max_text_chars: int = 20_000) -> List[dict]:
    formatted = []
    for hit in hits:
        payload = hit.get("payload") or {}
        text = (payload.get("text") or "").strip()
        truncated = len(text) > max_text_chars
        if truncated:
            text = text[: max_text_chars - 1].rstrip() + "…"
        elif text and not text.endswith(_TERMINAL_PUNCTUATION):
            # The passage ends mid-sentence because the paragraph runs into the
            # next group; signal that rather than implying it stops here.
            text = f"{text} …"

        # The sub-passage that actually won retrieval, for the UI to highlight.
        # Empty when the whole group matched (nothing more specific to mark).
        matched = (payload.get("retrieval_text") or "").strip()
        group_text = (payload.get("text") or "").strip()
        if not matched or matched == group_text or len(matched) >= len(group_text) * 0.9:
            matched = ""

        formatted.append(
            {
                "chunk_id": payload.get("chunk_id"),
                "group_id": payload.get("group_id"),
                "document_id": payload.get("document_id"),
                "document": payload.get("filename") or payload.get("document_id"),
                "page": payload.get("page"),
                "page_end": payload.get("page_end") or payload.get("page"),
                "block_types": payload.get("block_types") or [],
                "protected_type": payload.get("protected_type"),
                "score": hit.get("score"),
                "rerank_score": hit.get("rerank_score"),
                "lead_in": (payload.get("lead_in") or "").strip(),
                "matched": matched,
                "text": text,
            }
        )
    return formatted


@app.get("/config")
async def config():
    return {
        "ingest_root": str(INGEST_ROOT),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "embedding_model": EMBEDDING_MODEL,
        "reranker_model": RERANK_MODEL,
        "rerank_passage": RERANK_PASSAGE,
        "rerank_lowconf_score": RERANK_LOWCONF_SCORE,
        "generation": await generation.backend_info(),
        "pipeline_version": PIPELINE_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "fusion": FUSION_METHOD,
        "chunking": {
            "target_tokens": CHUNK_TARGET_TOKENS,
            "soft_max_tokens": CHUNK_SOFT_MAX_TOKENS,
            "hard_max_tokens": CHUNK_HARD_MAX_TOKENS,
            "overlap_tokens": CHUNK_OVERLAP_TOKENS,
        },
    }


@app.get("/documents")
async def get_documents():
    return {"documents": await asyncio.to_thread(list_metadata)}


@app.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")
    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="only PDF files are supported")

    doc_id = generate_id()
    stored_path = DOCUMENTS_DIR / f"{doc_id}.pdf"
    temporary_path = DOCUMENTS_DIR / f".{doc_id}.uploading"
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
                    )
                digest.update(chunk)
                destination.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="uploaded file is empty")
        temporary_path.replace(stored_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        stored_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    duplicate = await asyncio.to_thread(find_duplicate, digest.hexdigest())
    if duplicate:
        stored_path.unlink(missing_ok=True)
        return JSONResponse({**duplicate, "deduplicated": True}, status_code=200)

    try:
        metadata = await asyncio.to_thread(create_document, stored_path, Path(file.filename).name, digest.hexdigest())
    except ValueError as exc:
        # A deliberate, user-facing rejection from build_document_artifacts
        # (no extractable text, corrupt OCR layer, no searchable chunks).
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise _sanitized_http_error(
            422, "document ingestion failed", exc, log_context=file.filename
        ) from exc
    enqueue_index(IndexTask(doc_id))
    return JSONResponse(metadata, status_code=201)


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    return await asyncio.to_thread(read_metadata, doc_id)


@app.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str):
    validate_doc_id(doc_id)
    chunks_file = CHUNKS_DIR / f"{doc_id}.json"
    if not chunks_file.exists():
        raise HTTPException(status_code=404, detail="chunks not found")
    try:
        return await asyncio.to_thread(read_json, chunks_file)
    except Exception as exc:
        raise _sanitized_http_error(500, "failed to read chunks", exc, log_context=doc_id) from exc


def _stored_pdf_path(doc_id: str) -> Path:
    metadata = read_metadata(doc_id)
    stored = DOCUMENTS_DIR / metadata["stored_filename"]
    if not stored.exists():
        raise HTTPException(status_code=404, detail="the stored PDF is missing")
    return stored


def _highlight_quads(page, phrase: str):
    """Best-effort quads for the passage on a rendered page.

    The stored passage is repaired text and will not match the PDF's raw text
    layer verbatim, so fall back from the whole phrase to its longest sentence,
    then to a run of distinctive words.
    """
    phrase = " ".join(phrase.split())
    if not phrase:
        return []
    # Whole phrase first, then the longest runs of plain letters and spaces —
    # those survive text-layer damage (accents, ligatures, mis-decoded symbols)
    # far better than punctuation-bearing spans.
    candidates = [phrase]
    candidates += sorted(
        (run.strip() for run in re.split(r"[^A-Za-z ]+", phrase) if len(run.strip()) >= 12),
        key=len,
        reverse=True,
    )
    for candidate in candidates:
        if len(candidate) < 8:
            continue
        try:
            quads = page.search_for(candidate, quads=True)
        except Exception:
            quads = []
        if quads:
            return quads[:80]
    return []


@app.get("/documents/{doc_id}/file")
async def get_document_file(doc_id: str):
    stored = await asyncio.to_thread(_stored_pdf_path, doc_id)
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    return FileResponse(
        stored,
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=metadata.get("filename") or f"{doc_id}.pdf",
    )


@app.get("/documents/{doc_id}/page/{page}")
async def get_document_page(
    doc_id: str,
    page: int,
    highlight: str = Query(default="", max_length=2_000),
    zoom: float = Query(default=2.0, ge=1.0, le=4.0),
):
    stored = await asyncio.to_thread(_stored_pdf_path, doc_id)
    if page < 1:
        raise HTTPException(status_code=404, detail="page out of range")

    def render() -> bytes:
        with pymupdf.open(stored) as source:
            if page > source.page_count:
                raise HTTPException(status_code=404, detail="page out of range")
            single = pymupdf.open()
            try:
                single.insert_pdf(source, from_page=page - 1, to_page=page - 1)
                rendered = single[0]
                if highlight:
                    quads = _highlight_quads(rendered, highlight)
                    if quads:
                        annotation = rendered.add_highlight_annot(quads)
                        annotation.set_colors(stroke=(1.0, 0.86, 0.35))
                        annotation.update()
                return single[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
            finally:
                single.close()

    try:
        image = await asyncio.to_thread(render)
    except HTTPException:
        raise
    except Exception as exc:
        raise _sanitized_http_error(
            500, "could not render page", exc, log_context=f"page {page} of {doc_id}"
        ) from exc
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/documents/{doc_id}/retry")
async def retry_document(doc_id: str):
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    if metadata.get("indexing_status") in {"queued", "indexing"}:
        raise HTTPException(status_code=409, detail="document is already queued for indexing")
    if metadata.get("pipeline_version") != PIPELINE_VERSION:
        try:
            metadata = await asyncio.to_thread(rebuild_document_artifacts, metadata)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise _sanitized_http_error(
                422, "document rebuild failed", exc, log_context=doc_id
            ) from exc
    chunks_path = CHUNKS_DIR / f"{doc_id}.json"
    if not chunks_path.exists():
        raise HTTPException(status_code=409, detail="document chunks are missing; upload the document again")
    enqueue_index(IndexTask(doc_id))
    return await asyncio.to_thread(read_metadata, doc_id)


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    metadata = await asyncio.to_thread(read_metadata, doc_id)

    def delete_all() -> None:
        with get_document_lock(doc_id):
            delete_document_vectors(doc_id)
            for path in (
                DOCUMENTS_DIR / metadata["stored_filename"],
                EXTRACTED_DIR / f"{doc_id}.json",
                CHUNKS_DIR / f"{doc_id}.json",
            ):
                path.unlink(missing_ok=True)
            STORE.delete(doc_id)

    try:
        await asyncio.to_thread(delete_all)
    except Exception as exc:
        raise _sanitized_http_error(503, "could not delete document", exc, log_context=doc_id) from exc
    return JSONResponse(status_code=200, content={"deleted": doc_id})


@app.post("/search")
async def search(request: SearchRequest):
    started = time.monotonic()
    min_score = request.rerank_min_score if request.rerank_min_score is not None else RERANK_MIN_SCORE
    try:
        hits, candidate_count, rerank_dropped, _ = await retrieve(
            clean_query(request.query),
            request.top_k,
            request.document_id,
            request.filename,
            request.rerank,
            request.rerank_k,
            min_score,
            fusion=request.fusion,
            dense_weight=request.dense_weight,
            rerank_passage=request.rerank_passage,
        )
    except Exception as exc:
        raise _sanitized_http_error(503, "search failed", exc) from exc
    record_search(
        request,
        hits,
        candidate_count,
        (time.monotonic() - started) * 1000,
        rerank_min_score=min_score if request.rerank else None,
        rerank_dropped=rerank_dropped,
    )
    top_score = hits[0].get("rerank_score") if hits else None
    low_confidence = (
        request.rerank
        and RERANK_LOWCONF_SCORE is not None
        and top_score is not None
        and top_score < RERANK_LOWCONF_SCORE
    )
    return {
        "query": request.query,
        "results": format_hits(hits, request.max_text_chars),
        "low_confidence": low_confidence,
    }


@app.get("/search")
async def search_get(
    q: str = Query(..., min_length=1, max_length=1_000),
    top_k: int = Query(5, ge=1, le=50),
    document_id: Optional[str] = Query(default=None, pattern=r"^[a-f0-9]{12}$"),
    filename: Optional[str] = Query(default=None, max_length=255),
    max_text_chars: int = Query(20_000, ge=100, le=50_000),
    rerank_enabled: bool = Query(False, alias="rerank"),
    rerank_k: int = Query(20, ge=1, le=200),
    rerank_min_score: Optional[float] = Query(default=None, ge=-100, le=100),
    rerank_passage: Optional[str] = Query(default=None, pattern=r"^(group|matched)$"),
    fusion: Optional[str] = Query(default=None, pattern=r"^(rrf|dbsf|rsf)$"),
    dense_weight: Optional[float] = Query(default=None, ge=0.0, le=1.0),
):
    request = SearchRequest(
        query=q,
        top_k=top_k,
        document_id=document_id,
        filename=filename,
        max_text_chars=max_text_chars,
        rerank=rerank_enabled,
        rerank_k=rerank_k,
        rerank_min_score=rerank_min_score,
        rerank_passage=rerank_passage,
        fusion=fusion,
        dense_weight=dense_weight,
    )
    return await search(request)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


_ASK_NO_ANSWER = (
    "I couldn't find anything in your library about that. Try rephrasing, or add "
    "a document that covers the topic."
)


_CLOUD_PROVIDERS = ("anthropic", "openai", "google")
ASK_THOROUGH_MIN_SCORE = _parse_min_score(os.environ.get("ASK_THOROUGH_MIN_SCORE", "-5.0"))


def _sanitize_provider_keys(raw) -> dict:
    """Keep only well-formed {provider: key} entries; drop everything else."""
    if not isinstance(raw, dict):
        return {}
    return {
        provider: value.strip()
        for provider, value in raw.items()
        if provider in _CLOUD_PROVIDERS and isinstance(value, str) and 0 < len(value.strip()) <= 500
    }


async def _resolve_ask_model(requested: Optional[str], provider_keys: dict) -> Optional[str]:
    """The `provider:model` id to answer with: the request's choice if it is
    server-listed or the browser supplied that provider's key; else the server
    default (or None if nothing is available)."""
    if requested:
        known = {model["id"] for model in await generation.list_models()}
        if requested in known:
            return requested
        provider = requested.split(":", 1)[0]
        if provider in _CLOUD_PROVIDERS and provider_keys.get(provider):
            return requested
    return await generation.default_model()


@app.get("/ask/models")
async def ask_models():
    """Models the reader may pick in the Ask panel, and the current default."""
    return {"models": await generation.list_models(), "default": await generation.default_model()}


@app.post("/ask")
async def ask(request: AskRequest):
    """Retrieve passages for `question`, then stream a grounded, cited answer.

    Server-Sent Events: `{"type":"token","text":...}` chunks as the answer is
    generated, then one `{"type":"sources","results":[...],"low_confidence":b}`,
    or `{"type":"error","detail":...}` if generation fails after the stream
    opened. When retrieval finds nothing the answer is a fixed message and no LLM
    call is made. `request.model` ("provider:model") picks the model; an unknown
    or absent value falls back to the server default.
    """
    provider_keys = _sanitize_provider_keys(request.provider_keys)
    model = await _resolve_ask_model(request.model, provider_keys)
    if model is None:
        raise HTTPException(status_code=503, detail=generation.disabled_reason())

    thorough = request.mode == "thorough"
    if thorough:
        # A wider pool, grouped by document for the per-document map step, and a
        # looser gate — the map step is the real relevance filter.
        passages = generation.ASK_THOROUGH_PASSAGES
        max_per_doc = generation.ASK_THOROUGH_MAX_PER_DOC
        gate = ASK_THOROUGH_MIN_SCORE
    else:
        # How many passages this model sees and cites — more for big-context
        # cloud models, fewer for a local one. The request's top_k is a floor.
        passages = max(request.top_k, generation.context_passages_for(model))
        max_per_doc = ASK_MAX_PER_DOC
        gate = RERANK_MIN_SCORE

    started = time.monotonic()
    try:
        hits, candidate_count, rerank_dropped, relevant_count = await retrieve(
            clean_query(request.question),
            passages,
            request.document_id,
            request.filename,
            True,
            SearchRequest.model_fields["rerank_k"].default,
            gate,
            max_per_doc=max_per_doc,
            dedup_jaccard=ASK_DEDUP_JACCARD,
        )
    except Exception as exc:
        raise _sanitized_http_error(503, "retrieval failed", exc) from exc

    sources = format_hits(hits)
    top_score = hits[0].get("rerank_score") if hits else None
    low_confidence = (
        RERANK_LOWCONF_SCORE is not None
        and top_score is not None
        and top_score < RERANK_LOWCONF_SCORE
    )
    record_ask(
        request,
        hits,
        candidate_count,
        (time.monotonic() - started) * 1000,
        rerank_dropped,
        model=model,
    )

    history_turns = [turn.model_dump() for turn in request.history]

    async def event_stream():
        rid = REQUEST_ID.get()
        try:
            if not sources:
                yield _sse({"type": "token", "text": _ASK_NO_ANSWER})
                yield _sse(
                    {"type": "sources", "results": [], "low_confidence": False, "model": model}
                )
                return
            if thorough:
                grouped = collections.OrderedDict()
                for source in sources:
                    grouped.setdefault(source.get("document_id"), []).append(source)
                used = [
                    src
                    for group in list(grouped.values())[: generation.ASK_THOROUGH_MAX_DOCS]
                    for src in group
                ]
                async for kind, text in generation.generate_thorough(
                    model, request.question, used, history_turns, keys=provider_keys
                ):
                    yield _sse({"type": kind, "text": text})
            else:
                system, messages, used = generation.build_ask_prompt(
                    request.question, sources, history_turns, max_passages=passages
                )
                async for chunk in generation.generate_stream(
                    model, system, messages, keys=provider_keys
                ):
                    yield _sse({"type": "token", "text": chunk})
            yield _sse(
                {
                    "type": "sources",
                    "results": used,
                    "low_confidence": low_confidence,
                    "model": model,
                    "documents": len({s.get("document_id") for s in used}),
                    "relevant_count": relevant_count,
                }
            )
        except generation.GenerationError as exc:
            logger.warning("Ask generation failed [request %s]: %s", rid, exc)
            yield _sse({"type": "error", "detail": f"answer generation failed (request {rid})"})
        except Exception:
            logger.exception("Unexpected error while streaming an answer [request %s]", rid)
            yield _sse({"type": "error", "detail": f"answer generation failed (request {rid})"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


_CONV_ID = re.compile(r"^[a-f0-9]{12}$")


def _valid_conv_id(conv_id: str) -> str:
    if not _CONV_ID.fullmatch(conv_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv_id


@app.get("/conversations")
async def list_conversations(limit: int = Query(50, ge=1, le=200)):
    """Saved Ask conversations, newest first (summaries only)."""
    return {"conversations": await asyncio.to_thread(CONVERSATIONS.list, limit)}


@app.post("/conversations")
async def create_conversation(body: ConversationBody):
    try:
        conv = await asyncio.to_thread(
            CONVERSATIONS.create, body.messages, body.model, body.title
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(conv, status_code=201)


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = await asyncio.to_thread(CONVERSATIONS.get, _valid_conv_id(conv_id))
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@app.put("/conversations/{conv_id}")
async def update_conversation(conv_id: str, body: ConversationBody):
    try:
        conv = await asyncio.to_thread(
            CONVERSATIONS.replace, _valid_conv_id(conv_id), body.messages, body.model, body.title
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@app.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    if not await asyncio.to_thread(CONVERSATIONS.delete, _valid_conv_id(conv_id)):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"deleted": conv_id}


@app.get("/admin/search-log")
async def admin_search_log(limit: int = Query(50, ge=1, le=500)):
    """The most recent search-log entries, newest first."""
    path = LOGS_DIR / "search.jsonl"
    if not SEARCH_LOG_ENABLED or not path.exists():
        return {"enabled": SEARCH_LOG_ENABLED, "entries": []}

    def tail() -> List[dict]:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        entries.reverse()
        return entries

    return {"enabled": True, "entries": await asyncio.to_thread(tail)}


@app.get("/admin/reranker")
async def admin_reranker():
    info = model_info()
    pending = None
    if RERANK_EXECUTOR is not None:
        work_queue = getattr(RERANK_EXECUTOR, "_work_queue", None)
        pending = work_queue.qsize() if work_queue is not None else None
    return {"reranker": info, "executor": {"max_workers": RERANK_MAX_WORKERS, "pending_tasks": pending}}


@app.get("/admin/ingest-status/{job_id}")
async def ingest_status(job_id: str):
    with JOB_STATUS_LOCK:
        status = JOB_STATUS.get(job_id) or load_job(job_id)
        if status:
            JOB_STATUS[job_id] = status
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return status


@app.post("/admin/ingest-folder")
async def ingest_folder(request: IngestFolderRequest):
    folder = resolve_ingest_folder(request.folder)
    job_id = generate_id()
    now = utc_now()
    job = {
        "job_id": job_id,
        "state": "queued",
        "folder": str(folder.relative_to(INGEST_ROOT)) if folder != INGEST_ROOT else ".",
        "files": [],
        "created_at": now,
        "updated_at": now,
    }
    with JOB_STATUS_LOCK:
        JOB_STATUS[job_id] = job
        save_job(job)
    try:
        INGEST_QUEUE.put((job_id, str(folder)), timeout=2)
    except queue.Full as exc:
        with JOB_STATUS_LOCK:
            job["state"] = "error"
            job["error"] = "ingest queue is full; try again later"
            save_job(job)
        raise HTTPException(status_code=503, detail=job["error"]) from exc
    return JSONResponse({"job_id": job_id, "state": "queued"}, status_code=202)


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health")
@app.get("/health/ready")
async def health_ready():
    qdrant_ok = await asyncio.to_thread(vector_healthcheck)
    payload = {
        "status": "ready" if qdrant_ok else "degraded",
        "qdrant": qdrant_ok,
        "index_backlog": await asyncio.to_thread(STORE.count_indexing_backlog),
        "ingest_queue": INGEST_QUEUE.qsize(),
    }
    return JSONResponse(payload, status_code=200 if qdrant_ok else 503)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
