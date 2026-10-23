import asyncio
import collections
import concurrent.futures
import contextvars
import hashlib
import io
import json
import logging
import logging.handlers
import multiprocessing
import os
import queue
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path
from typing import List, Optional, Sequence

import pymupdf
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.background import BackgroundTask
from starlette.datastructures import Headers, MutableHeaders

import ai_sort
import annotations as reader_annotations
import catalogue
import doc_kind
import generation
import intake
import portability
import shelves
from chunking import (
    build_semantic_groups,
    collapse_blank_lines,
    load_tokenizer,
    normalize_for_embedding,
    parse_typed_blocks,
)
from conversations import ConversationStore
from database import MetadataStore
from doc_types import LABELS as DOC_TYPE_LABELS, TYPES as DOC_TYPES, classify_document
from embeddings import (
    DEFAULT_MODEL as EMBEDDING_MODEL,
    embed_texts,
    unload_if_idle as unload_embedder_if_idle,
)
from extraction import (
    assess_scanned,
    assess_text_layer,
    boilerplate_page_indices,
    describe_skipped_pages,
    garbled_page_indices,
)
from ocr import estimate_seconds as estimate_ocr_seconds
from parsers import (
    OCR_APPROVAL_MARKER,
    SUPPORTED_EXTENSIONS,
    document_author,
    epub_title,
    extract_source_pages,
    file_type_for_path,
    is_supported_path,
    location_label,
    media_type_for,
)
from reranker import (
    DEFAULT_MODEL as RERANK_MODEL,
    model_info,
    rerank,
    unload_if_idle as unload_reranker_if_idle,
)
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
# Cached first-page covers for the UI's shelves (see /documents/{id}/thumbnail).
# Pure derived data: safe to delete, rebuilt on the next request.
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
# Uploads waiting for the reader to check them (see "Intake" below). Not part of
# the library: nothing here is indexed, searched, shelved or exported until saved.
INTAKE_DIR = DATA_DIR / "intake"
LOGS_DIR = DATA_DIR / "logs"
INGEST_ROOT = Path(os.environ.get("INGEST_ROOT", str(DATA_DIR / "inbox"))).resolve()
# The mount's path on the HOST, for display only -- never used for filesystem
# access (the container can only ever read INGEST_ROOT). Lets the UI accept a
# pasted absolute host path by recognizing this prefix. Trailing slashes
# stripped so prefix-matching a child path works either way.
LIBRARY_HOST_PATH = os.environ.get("LIBRARY_HOST_PATH", "").strip().rstrip("/")

SEARCH_LOG_ENABLED = os.environ.get("SEARCH_LOG", "on").strip().lower() not in {
    "off", "0", "false", "no", ""
}
SEARCH_LOG_MAX_BYTES = int(os.environ.get("SEARCH_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
SEARCH_LOG_BACKUPS = int(os.environ.get("SEARCH_LOG_BACKUPS", "3"))

# When set, every endpoint except the health checks and the static UI (below)
# requires ``Authorization: Bearer <APP_TOKEN>``. Empty (the default) leaves
# the API open, which is the intended posture for a loopback / SSH-tunnel
# deployment.
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
_AUTH_EXEMPT_PATHS = {"/health", "/health/live", "/health/ready", "/", "/index.html"}
# The built UI's own static assets (see the StaticFiles mount at the bottom of
# this file) -- these were always served unauthenticated, straight off nginx's
# filesystem, in the old two-container layout, so this preserves that: the
# token still gates every real API call the page goes on to make, just not
# loading the page itself.
_AUTH_EXEMPT_PREFIXES = ("/assets/",)
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))
INGEST_QUEUE_SIZE = int(os.environ.get("INGEST_QUEUE_SIZE", "10"))
# The index worker pulls ``queued`` documents from the database rather than
# draining an in-memory backlog. This is only a fallback re-check interval in
# case a wake-up signal is ever missed.
INDEX_POLL_SECONDS = float(os.environ.get("INDEX_POLL_SECONDS", "30"))
# How often to rescan the mounted library for PDFs that aren't indexed yet, so
# dropping files into it is enough on its own -- no manual Attach/Import click
# required. Runs once immediately at startup too, so a library populated before
# the container's first start is indexed without any UI interaction. 0 disables
# the scan entirely (Browse/Attach import remain manual-only).
# A directory walk is cheap in isolation but unnecessarily noisy on a sleeping
# NAS or external drive every minute.  The UI has a one-click scan for the
# moments when it matters; passive discovery is deliberately relaxed.
AUTO_INGEST_INTERVAL_SECONDS = float(os.environ.get("AUTO_INGEST_INTERVAL_SECONDS", "900"))
OCR_MODE = os.environ.get("OCR_MODE", "auto").strip().lower()
if OCR_MODE not in {"auto", "off"}:
    logger.warning("Ignoring unsupported OCR_MODE=%r; using auto", OCR_MODE)
    OCR_MODE = "auto"
OCR_LANGUAGES = os.environ.get("OCR_LANGUAGES", "eng").strip() or "eng"
# "auto" = Tesseract plus the equation-aware Nougat model (best page wins),
# "math" = Nougat only, "tesseract" = fast text-only OCR.
OCR_ENGINE = os.environ.get("OCR_ENGINE", "auto").strip().lower()
if OCR_ENGINE not in {"auto", "math", "tesseract"}:
    logger.warning("Ignoring unsupported OCR_ENGINE=%r; using auto", OCR_ENGINE)
    OCR_ENGINE = "auto"
# A scan needing OCR on more pages than this is not processed until the reader
# approves it (equation OCR can take tens of seconds a page on CPU). 0 = never ask.
OCR_APPROVAL_PAGES = int(os.environ.get("OCR_APPROVAL_PAGES", "20"))
RERANK_MAX_WORKERS = int(os.environ.get("RERANK_MAX_WORKERS", "2"))
# PDF extraction's per-page ML layout pass dominates indexing time (profiled at
# ~96% of it) and pymupdf4llm serializes every call to it within a process via
# its own internal lock -- so only separate OS processes actually run it
# concurrently, threads would just queue up behind that lock. Each worker
# loads its own copy of the layout model, so keep this modest.
INDEX_EXTRACTION_WORKERS = int(os.environ.get("INDEX_EXTRACTION_WORKERS", "2"))
# Background footprint. After this many idle seconds the embedding and
# reranker models are unloaded and the extraction worker processes exit,
# giving their memory back; the next search or upload loads them again (a
# few seconds' delay). 0 keeps everything resident.
MODEL_IDLE_UNLOAD_SECONDS = int(os.environ.get("MODEL_IDLE_UNLOAD_SECONDS", "600"))
# Niceness of extraction worker processes, so a big import yields the CPU to
# whatever else the machine is doing. 0 leaves them at normal priority.
EXTRACTION_NICE = int(os.environ.get("EXTRACTION_NICE", "10"))
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
PIPELINE_VERSION = 14  # Word tables in reading order, whole; table passages carry their heading and caption

for directory in (
    DOCUMENTS_DIR, METADATA_DIR, EXTRACTED_DIR, CHUNKS_DIR, JOBS_DIR, LOGS_DIR, INGEST_ROOT, INTAKE_DIR
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
                "collection_id": request.collection_id,
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
    agentic_searches: Optional[List[str]] = None,
) -> None:
    """Append one ask-log line (same JSONL file as search). Never raises."""
    if not SEARCH_LOG_ENABLED:
        return
    try:
        entry = {
            "ts": utc_now(),
            "mode": "ask",
            "ask_mode": request.mode,
            "agentic_searches": agentic_searches or None,
            "query": request.question,
            "model": model,
            "history_turns": len(request.history),
            "filters": {
                key: value
                for key, value in {
                    "document_id": request.document_id,
                    "filename": request.filename,
                    "collection_id": request.collection_id,
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


def record_ask_outcome(
    request: "AskRequest",
    answer: str,
    sources: List[dict],
    latency_ms: float,
    model: Optional[str],
    low_confidence: bool,
    error: Optional[str] = None,
) -> None:
    """Persist answer-shape telemetry without storing answer content."""
    if not SEARCH_LOG_ENABLED:
        return
    try:
        cited = {
            int(number)
            for number in re.findall(r"\[(\d+)\]", answer or "")
            if 1 <= int(number) <= len(sources)
        }
        entry = {
            "ts": utc_now(),
            "mode": "ask_outcome",
            "ask_mode": request.mode,
            "query": request.question,
            "model": model,
            "answer_chars": len(answer or ""),
            "citation_count": len(cited),
            "source_count": len(sources),
            "cited_source_fraction": round(len(cited) / len(sources), 3) if sources else 0.0,
            "low_confidence": low_confidence,
            "generation_latency_ms": round(latency_ms, 1),
            "outcome": "error" if error else "ok",
            "error": error,
        }
        search_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        logger.exception("Could not write ask-outcome telemetry")


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
    # Restrict to one library (a named collection); "legacy-root" is the
    # implicit single library of an installation without collections.
    collection_id: Optional[str] = Field(default=None, pattern=r"^(?:[a-f0-9]{12}|legacy-root|notes)$")
    # Narrow to one virtual shelf and its sub-shelves ("Books/Philosophy").
    shelf: Optional[str] = Field(default=None, max_length=500)
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
    # Cap how many of the returned passages may come from any one document, so
    # a single long book can't fill every slot. 0 = no cap.
    max_per_doc: int = Field(default=0, ge=0, le=50)


def _normalized_isbn(value: Optional[str], length: int) -> Optional[str]:
    if value is None:
        return None
    normalized = re.sub(r"[-\s]", "", value).upper()
    if not normalized:
        return None
    if length == 10:
        if not re.fullmatch(r"\d{9}[\dX]", normalized):
            raise ValueError("ISBN-10 must contain 10 digits (the last may be X)")
        digits = [int(char) for char in normalized[:9]] + [10 if normalized[-1] == "X" else int(normalized[-1])]
        valid = sum((10 - index) * digit for index, digit in enumerate(digits)) % 11 == 0
    else:
        if not re.fullmatch(r"\d{13}", normalized):
            raise ValueError("ISBN-13 must contain 13 digits")
        valid = sum((1 if index % 2 == 0 else 3) * int(char) for index, char in enumerate(normalized)) % 10 == 0
    if not valid:
        raise ValueError(f"ISBN-{length} checksum is invalid")
    return normalized


class DocumentPatch(BaseModel):
    # Half-star steps, 0.5 to 5, like Letterboxd. Whole numbers stay integers.
    rating: Optional[float] = Field(default=None, ge=0.5, le=5, strict=True)
    review: Optional[str] = Field(default=None, max_length=20_000)
    liked: Optional[bool] = None
    review_spoiler: Optional[bool] = None
    reread: Optional[bool] = None
    title: Optional[str] = Field(default=None, max_length=300)
    author: Optional[str] = Field(default=None, max_length=240)
    subject: Optional[str] = Field(default=None, max_length=240)
    subtitle: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=20_000)
    publisher: Optional[str] = Field(default=None, max_length=300)
    published_year: Optional[int] = Field(default=None, ge=1000, le=2100, strict=True)
    page_count: Optional[int] = Field(default=None, ge=1, le=1_000_000, strict=True)
    language: Optional[str] = Field(default=None, max_length=50, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    isbn_10: Optional[str] = Field(default=None, max_length=24)
    isbn_13: Optional[str] = Field(default=None, max_length=24)
    genres: Optional[List[str]] = Field(default=None, max_length=20)
    acquisition_source: Optional[str] = Field(
        default=None,
        pattern=r"^(?:book_store|kindle|audiobook|borrowed|second_hand|gifted|library|other)$",
    )
    reading_status: Optional[str] = Field(default=None, pattern=r"^(?:to_read|reading|read|abandoned)$")
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    current_page: Optional[int] = Field(default=None, ge=1, le=1_000_000, strict=True)
    reading_progress: Optional[int] = Field(default=None, ge=0, le=100, strict=True)
    # Only the fields sent are changed. `owned` may be null to clear it;
    # `kind` "auto" drops the reader's override and goes back to the guess.
    owned: Optional[bool] = None
    read: Optional[bool] = None
    kind: Optional[str] = Field(default=None, pattern=r"^(book|paper|document|auto)$")
    # A book's form; "auto" drops the reader's choice.
    form: Optional[str] = Field(default=None, pattern=r"^(auto|novel|novella|short_story|collection|nonfiction)$")
    # A working document's type; "auto" drops the reader's choice.
    doc_type: Optional[str] = Field(default=None, pattern=r"^(auto|paper|report|manual|slides|data|writing|legal|other)$")
    # "Books/Philosophy/Albert Camus"; null or "" goes back to the suggestion.
    shelf: Optional[str] = Field(default=None, max_length=500)

    @field_validator("rating")
    @classmethod
    def validate_half_star(cls, value):
        if value is None:
            return None
        if value * 2 != int(value * 2):
            raise ValueError("rating must be a whole or half star")
        return int(value) if value == int(value) else value

    @field_validator("isbn_10", mode="before")
    @classmethod
    def validate_isbn_10(cls, value):
        return _normalized_isbn(value, 10)

    @field_validator("isbn_13", mode="before")
    @classmethod
    def validate_isbn_13(cls, value):
        return _normalized_isbn(value, 13)

    @field_validator("language", "acquisition_source", mode="before")
    @classmethod
    def empty_string_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("genres")
    @classmethod
    def validate_genres(cls, values):
        if values is None:
            return None
        cleaned = []
        for value in values:
            genre = value.strip()
            if not genre:
                continue
            if len(genre) > 80:
                raise ValueError("genres must be 80 characters or fewer")
            if genre.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(genre)
        return cleaned

    @field_validator("started_at", "finished_at")
    @classmethod
    def validate_calendar_date(cls, value):
        if value in (None, ""):
            return None
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("date must use YYYY-MM-DD") from exc
        return value


class OwnedBookBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: Optional[str] = Field(default=None, max_length=240)
    notes: Optional[str] = Field(default=None, max_length=5_000)
    pdf_less: bool = True


class OwnedBookPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    author: Optional[str] = Field(default=None, max_length=240)
    notes: Optional[str] = Field(default=None, max_length=5_000)
    pdf_less: Optional[bool] = None


class NoteBody(BaseModel):
    source_document_id: Optional[str] = None
    source_page: Optional[int] = Field(default=None, ge=1)
    source_quote: Optional[str] = Field(default=None, max_length=20_000)
    text: str = Field(min_length=1, max_length=20_000)


class VocabularyBody(BaseModel):
    word: str = Field(min_length=1, max_length=120)
    definition: str = Field(default="", max_length=4_000)
    part_of_speech: Optional[str] = Field(default=None, max_length=80)
    example: Optional[str] = Field(default=None, max_length=2_000)
    document_id: Optional[str] = None
    source_page: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    source_quote: Optional[str] = Field(default=None, max_length=4_000)


class HarvestWord(BaseModel):
    word: str = Field(min_length=1, max_length=120)
    page: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    quote: Optional[str] = Field(default=None, max_length=4_000)
    definition: Optional[str] = Field(default=None, max_length=4_000)


class HarvestBody(BaseModel):
    document_id: str
    words: List[HarvestWord] = Field(min_length=1, max_length=1_000)


class ShelfAccept(BaseModel):
    # Omitted: accept every pending suggestion.
    document_ids: Optional[List[str]] = Field(default=None, max_length=10_000)


class ShelfMove(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=500)


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
    # Restrict to one library (a named collection); "legacy-root" is the
    # implicit single library of an installation without collections.
    collection_id: Optional[str] = Field(default=None, pattern=r"^(?:[a-f0-9]{12}|legacy-root|notes)$")
    # Narrow to one virtual shelf and its sub-shelves ("Books/Philosophy").
    shelf: Optional[str] = Field(default=None, max_length=500)
    top_k: int = Field(default=10, ge=1, le=20)
    # "provider:model" from GET /ask/models; unknown or absent -> the server default.
    model: Optional[str] = Field(default=None, max_length=120)
    # "quick" = one grounded pass; "thorough" = map-reduce; "agentic" = a
    # bounded planner/search loop followed by one grounded pass.
    mode: str = Field(default="quick", pattern=r"^(quick|thorough|agentic)$")
    # Overrides the relevance gate (reranker score floor) for this question;
    # absent -> RERANK_MIN_SCORE (quick) or ASK_THOROUGH_MIN_SCORE (thorough).
    min_score: Optional[float] = Field(default=None, ge=-100, le=100)
    # Per-request cloud API keys the browser holds (it never persists them
    # server-side). {"openai"|"google": "<key>"}. Never logged.
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
# Fine-grained, ephemeral progress for the live indexing activity view. The
# durable source of truth remains ``documents.indexing_status``; these details
# simply describe which worker stage an active document is in and how far its
# chunk embedding pass has advanced.
INDEX_ACTIVITY = {}
INDEX_ACTIVITY_LOCK = threading.Lock()
# document_id -> (ingest_job_id, ingest_file_index) for documents that came in
# through a folder-ingest job, so the worker can report progress back to it.
INGEST_LINKS = {}
INGEST_LINKS_LOCK = threading.Lock()
# One ingest-job record is created per folder attach/import; without a cap a
# container that runs for months without a restart would keep every one of
# them in memory and on disk forever.
MAX_RETAINED_JOBS = 200
JOB_STATUS = {}
JOB_STATUS_LOCK = threading.RLock()
DOCUMENT_LOCKS = {}
DOCUMENT_LOCKS_LOCK = threading.Lock()
WORKER_THREADS = []
RERANK_EXECUTOR = None
EXTRACTION_EXECUTOR = None


def set_index_activity(document_id: str, stage: str, **details) -> None:
    now = utc_now()
    with INDEX_ACTIVITY_LOCK:
        current = INDEX_ACTIVITY.get(document_id, {})
        started_at = current.get("started_at")
        if stage != "queued" and not started_at:
            started_at = now
        INDEX_ACTIVITY[document_id] = {
            **current,
            "stage": stage,
            "updated_at": now,
            **({"started_at": started_at} if started_at else {}),
            **details,
        }


def clear_index_activity(document_id: str) -> None:
    with INDEX_ACTIVITY_LOCK:
        INDEX_ACTIVITY.pop(document_id, None)


def _lower_worker_priority() -> None:
    if EXTRACTION_NICE > 0:
        try:
            os.nice(EXTRACTION_NICE)
        except OSError:
            pass


def _new_extraction_executor() -> concurrent.futures.ProcessPoolExecutor:
    # "fork" (Linux/Docker's default anyway, made explicit here): the worker
    # only ever runs build_document_artifacts, a pure function of its plain
    # arguments that touches none of this process's shared locks or open
    # connections (STORE, Qdrant, the embedding model) -- exactly the case
    # fork-in-a-threaded-process is safe for, since the child never needs
    # any lock it might have inherited mid-acquisition.
    #
    # Load the chunker's tokenizer here, before forking, so every child
    # inherits it ready-made. Loading it inside a child both costs each worker
    # its own copy and, on macOS, crashes it: the transformers import chain
    # (sklearn -> pandas -> pyarrow) runs libarrow's curl initializer, and the
    # Hub download does a proxy lookup; both call CoreFoundation, which aborts
    # in a child forked from a multi-threaded process.
    load_tokenizer()
    return concurrent.futures.ProcessPoolExecutor(
        max_workers=INDEX_EXTRACTION_WORKERS,
        initializer=_lower_worker_priority,
        mp_context=multiprocessing.get_context("fork"),
    )


def signal_index_worker() -> None:
    try:
        INDEX_SIGNAL.put_nowait(True)
    except queue.Full:
        pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_id() -> str:
    return uuid.uuid4().hex[:12]


def title_from_filename(filename: str, author: Optional[str] = None) -> str:
    return shelves.title_from_filename(filename, author)


def _legacy_title_from_filename(filename: str) -> str:
    """What records made before the file-name parser got as their title, so the
    backfill can tell an untouched guess from a title someone chose."""
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip().title()


def atomic_write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# Small persisted app settings -- currently just which folder under INGEST_ROOT
# (the /library mount) counts as "the" library. Lets a broadly-mounted host
# path (a whole home directory, an external drive) be narrowed to the actual
# collection entirely from the website, with no docker-compose/.env edit or
# restart: click a folder in Browse, "Set as library folder", done.
SETTINGS_PATH = DATA_DIR / "settings.json"
SETTINGS_LOCK = threading.RLock()
_SETTINGS_CACHE: Optional[dict] = None
COLLECTION_ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")


def _load_settings() -> dict:
    global _SETTINGS_CACHE
    with SETTINGS_LOCK:
        if _SETTINGS_CACHE is None:
            try:
                _SETTINGS_CACHE = read_json(SETTINGS_PATH)
            except (OSError, json.JSONDecodeError):
                _SETTINGS_CACHE = {}
        return dict(_SETTINGS_CACHE)


def _save_settings(changes: dict) -> dict:
    global _SETTINGS_CACHE
    with SETTINGS_LOCK:
        current = _load_settings()
        current.update(changes)
        atomic_write_json(SETTINGS_PATH, current)
        _SETTINGS_CACHE = current
        return dict(current)


def library_root_path() -> Path:
    """The effective library root: the persisted ``library_root`` setting if
    it still resolves to a real folder under INGEST_ROOT, else INGEST_ROOT
    itself (today's behaviour, and the fallback if the chosen folder was
    since renamed or removed)."""
    rel = (_load_settings().get("library_root") or "").strip()
    if not rel:
        return INGEST_ROOT
    try:
        resolved = (INGEST_ROOT / rel).resolve(strict=True)
    except OSError:
        return INGEST_ROOT
    if resolved != INGEST_ROOT and INGEST_ROOT not in resolved.parents:
        return INGEST_ROOT
    if not resolved.is_dir():
        return INGEST_ROOT
    return resolved


def configured_collections() -> List[dict]:
    """Return the saved named folders, with a read-only legacy fallback.

    A pre-collections installation keeps its existing single library root and
    auto-ingest behaviour.  New installations can instead choose any number
    of clearly named, independently pausable collections.
    """
    stored = _load_settings().get("collections")
    valid = []
    if isinstance(stored, list):
        for item in stored:
            if not isinstance(item, dict):
                continue
            collection_id = str(item.get("id") or "")
            path = str(item.get("path") or "").strip().strip("/")
            name = str(item.get("name") or "").strip()
            if not COLLECTION_ID_PATTERN.fullmatch(collection_id) or not name:
                continue
            try:
                folder = resolve_ingest_folder(path)
            except HTTPException:
                continue
            valid.append(
                {
                    "id": collection_id,
                    "name": name[:80],
                    "path": "" if folder == INGEST_ROOT else str(folder.relative_to(INGEST_ROOT)),
                    "auto_scan": bool(item.get("auto_scan", True)),
                }
            )
    if valid:
        return sorted(valid, key=lambda item: (item["name"].lower(), item["id"]))
    legacy = library_root_path()
    return [
        {
            "id": "legacy-root",
            "name": "Library",
            "path": "" if legacy == INGEST_ROOT else str(legacy.relative_to(INGEST_ROOT)),
            "auto_scan": True,
            "legacy": True,
        }
    ]


def collection_for_source_path(source_path: str, collections: Optional[List[dict]] = None) -> Optional[str]:
    """The tightest collection containing a source path, if any."""
    candidates = []
    for collection in collections or configured_collections():
        root = collection.get("path") or ""
        if not root or source_path == root or source_path.startswith(root + "/"):
            candidates.append(collection)
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item.get("path") or ""))["id"]


def _belongs_to_collection(doc: dict, collection: dict) -> bool:
    """Membership is the recorded ``collection_id`` or, for files imported
    before the collection existed, a ``source_path`` inside its folder."""
    if doc.get("collection_id") == collection["id"]:
        return True
    root = collection.get("path") or ""
    source = doc.get("source_path") or ""
    return bool(source) and (not root or source == root or source.startswith(root + "/"))


# Notes jotted in the app form a built-in library of their own. It has no
# folder, so it is never scanned, and membership is by collection_id only.
NOTES_COLLECTION_ID = "notes"


def collection_document_ids(collection_id: Optional[str]) -> Optional[List[str]]:
    """Every document belonging to one library, for scoping search and Ask.

    ``None`` means "no restriction". Membership is the recorded
    ``collection_id`` or, for files imported before the collection existed,
    a ``source_path`` inside the collection's folder.
    """
    if not collection_id:
        return None
    if collection_id == NOTES_COLLECTION_ID:
        return [doc["document_id"] for doc in list_metadata() if doc.get("collection_id") == NOTES_COLLECTION_ID]
    collection = next(
        (item for item in configured_collections() if item["id"] == collection_id), None
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="library not found")
    root = collection.get("path") or ""
    if collection.get("legacy") and not root:
        return None  # the implicit library is the whole mount: no filter
    return [doc["document_id"] for doc in list_metadata() if _belongs_to_collection(doc, collection)]


def scope_document_ids(collection_id: Optional[str], shelf: Optional[str] = None) -> Optional[List[str]]:
    """The documents a search may return: one library's, narrowed to one shelf
    (and its sub-shelves) when given. None means no restriction."""
    if not shelf:
        return collection_document_ids(collection_id)

    collection = None
    if collection_id and collection_id != NOTES_COLLECTION_ID:
        collection = next(
            (item for item in configured_collections() if item["id"] == collection_id), None
        )
        if collection is None:
            raise HTTPException(status_code=404, detail="library not found")
    try:
        wanted = shelves.normalize_shelf(shelf)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Read the metadata table once and apply both predicates in memory. This is
    # especially useful for Ask's multi-pass modes, where the same scope is
    # reused for several retrievals.
    documents = list_metadata()
    scoped = []
    for doc in documents:
        if collection_id == NOTES_COLLECTION_ID:
            in_collection = doc.get("collection_id") == NOTES_COLLECTION_ID
        elif collection is None or (collection.get("legacy") and not collection.get("path")):
            in_collection = True
        else:
            in_collection = _belongs_to_collection(doc, collection)
        if in_collection and shelves.on_shelf(doc, wanted):
            scoped.append(doc["document_id"])
    return scoped


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


_TERMINAL_JOB_STATES = {"done", "partial", "error", "interrupted"}


def _prune_old_jobs() -> None:
    """Drop the oldest finished job records once there are more than
    MAX_RETAINED_JOBS, from both JOB_STATUS and JOBS_DIR."""
    with JOB_STATUS_LOCK:
        finished = sorted(
            (job for job in JOB_STATUS.values() if job.get("state") in _TERMINAL_JOB_STATES),
            key=lambda job: job.get("updated_at", ""),
        )
        excess = len(finished) - MAX_RETAINED_JOBS
        if excess <= 0:
            return
        for job in finished[:excess]:
            JOB_STATUS.pop(job["job_id"], None)
            try:
                job_path(job["job_id"]).unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove old job file for %s", job["job_id"])


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
    set_index_activity(task.document_id, "queued", progress=0)
    update_metadata(task.document_id, indexing_status="queued", indexing_error=None)
    signal_index_worker()


def _fail_indexing(doc_id: str, message: str) -> None:
    logger.error("Indexing failed for document %s: %s", doc_id, message)
    try:
        delete_document_vectors(doc_id)
    except Exception:
        logger.exception("Could not clean partial vectors for %s", doc_id)
    try:
        update_metadata(doc_id, indexing_status="error", indexing_error=message)
    except Exception:
        logger.exception("Could not persist indexing error for %s", doc_id)
    update_ingest_entry(doc_id, "error", message)
    clear_index_activity(doc_id)


def _describe_duration(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    return f"about {round(minutes / 60)} hours"


def _request_ocr_approval(doc_id: str, page_count: int) -> None:
    """Park a large scan until the reader approves OCR for it."""
    message = (
        f"This is a scanned document: {page_count} pages need OCR, which will take "
        f"{_describe_duration(estimate_ocr_seconds(page_count, OCR_ENGINE))}. "
        "Approve OCR to index it."
    )
    # Recorded before the status flips, so no reader ever sees the error
    # without the page count its Approve button needs.
    try:
        STORE.update(doc_id, {"ocr_pages": page_count})
    except KeyError:
        return
    _fail_indexing(doc_id, message)


def awaiting_ocr_approval(metadata: dict) -> bool:
    """True for a scan parked by _request_ocr_approval that the reader has not
    yet approved. Its page count is already known, so nothing is gained by
    extracting it again before they say yes."""
    return bool(
        metadata.get("indexing_status") == "error"
        and metadata.get("ocr_pages")
        and not metadata.get("ocr_approved")
    )


def _genres_of(doc: dict) -> List[str]:
    try:
        genres = json.loads(doc.get("genres_json") or "[]")
    except ValueError:
        return []
    return [str(genre) for genre in genres] if isinstance(genres, list) else []


def _json_of(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _kind_fields(doc_id: str, metadata: dict) -> dict:
    """Book / paper / document and the signals behind it (see doc_kind.py), from
    the text extraction already wrote. Best effort: {} when it can not be read."""
    try:
        extracted = read_json(EXTRACTED_DIR / f"{doc_id}.json")
        texts = [page.get("text") or "" for page in extracted.get("pages") or []]
        author = metadata.get("author") or shelves.author_from_text(texts[:3]) or ""
        authors = max(
            shelves.author_count(document_author(pdf_path(metadata))),
            shelves.credit_count(metadata.get("filename") or ""),
        )
        verdict = doc_kind.classify(
            texts,
            metadata.get("pages") or len(texts),
            file_type=metadata.get("file_type"),
            author=author,
            authors=authors,
            title=f"{metadata.get('title') or ''} {metadata.get('subtitle') or ''}",
            subject=metadata.get("subject"),
            genres=_genres_of(metadata),
            ai=_json_of(metadata.get("ai_json")),
        )
        stats = {"words": verdict.words, "past": verdict.past, "english": verdict.english, "weak": verdict.weak}
        return {
            "kind": verdict.kind,
            "kind_rules": doc_kind.RULES_VERSION,
            "prose_score": verdict.prose_score,
            "author_count": verdict.authors,
            "text_stats": json.dumps(stats),
        }
    except Exception:
        logger.warning("Could not detect the kind of document %s", doc_id, exc_info=True)
        return {}


def _form_fields(doc: dict) -> dict:
    """A book's form -- novel, novella, short story, collection or non-fiction
    -- from what is already known about it; nothing for anything that is not a
    book. `doc` is the record with any pending changes merged in."""
    if (doc.get("kind_override") or doc.get("kind")) != "book":
        return {"book_form": None}
    title = f"{doc.get('title') or ''} {doc.get('subtitle') or ''}"
    stats = _json_of(doc.get("text_stats"))
    fiction = doc_kind.is_fiction(
        doc.get("prose_score"), title, doc.get("subject"), _genres_of(doc), stats.get("past"), _json_of(doc.get("ai_json")).get("fiction")
    )
    pages = doc_kind.length_pages(doc.get("page_count"), stats.get("words") or 0, doc.get("pages"))
    return {"book_form": doc_kind.book_form(pages, fiction, doc.get("author_count") or 0, title, doc.get("author") or "")}


def backfill_document_kinds() -> None:
    """Classify documents indexed before kind detection existed, and re-read
    those classified under older rules (doc_kind.RULES_VERSION). Runs once per
    start, in the background. The reader's own choices -- kind, shelf, edited
    details -- are never touched."""
    for doc in list_metadata():
        if SHUTDOWN.is_set():
            return
        if doc.get("indexing_status") != "indexed" or (doc.get("kind") and (doc.get("kind_rules") or 0) >= doc_kind.RULES_VERSION):
            continue
        fields = _kind_fields(doc["document_id"], doc)
        if not fields:
            continue
        try:
            merged = {**doc, **fields}
            kind = merged.get("kind_override") or merged["kind"]
            fields.update(_shelf_fields(merged, kind))
            fields.update(_form_fields({**merged, **fields}))
            STORE.update(doc["document_id"], fields)
        except KeyError:
            pass


_SUBJECT_VECTORS: dict = {}
_SUBJECT_LOCK = threading.Lock()
_WORDISH = re.compile(r"[^\W\d_]{3,}")


def _subject_vectors() -> dict:
    """Each shelf subject's description, embedded once per process."""
    with _SUBJECT_LOCK:
        if not _SUBJECT_VECTORS:
            labels = list(shelves.SUBJECTS)
            vectors = embed_texts([shelves.SUBJECTS[label] for label in labels], kind="query")
            _SUBJECT_VECTORS.update(zip(labels, vectors))
        return dict(_SUBJECT_VECTORS)


def _document_vector(doc_id: str) -> Optional[List[float]]:
    """A document's gist for subject matching: the mean embedding of its most
    word-rich early passages (a spreadsheet's opening sheet may be all figures)."""
    chunks = read_json(CHUNKS_DIR / f"{doc_id}.json")
    texts = [chunk.get("embedding_text") or chunk.get("text") or "" for chunk in chunks[:40]]
    texts = [text for text in sorted(texts, key=lambda t: -len(_WORDISH.findall(t)))[:8] if text.strip()]
    return shelves.mean_vector(embed_texts(texts)) if texts else None


def _first_page_texts(doc_id: str, count: int = 3) -> List[str]:
    try:
        pages = read_json(EXTRACTED_DIR / f"{doc_id}.json").get("pages") or []
    except Exception:
        return []
    return [page.get("text") or "" for page in pages[:count]]


def _document_type(metadata: dict, kind: Optional[str]) -> Optional[str]:
    """Paper / report / manual / ... for a working document (anything that is
    not a book or a note): the reader's choice, else a guess from its text."""
    if kind in {"book", "note"}:
        return None
    if metadata.get("doc_type_override"):
        return metadata["doc_type_override"]
    try:
        return classify_document(
            metadata.get("file_type"), _first_page_texts(metadata["document_id"]),
            metadata.get("pages") or 0, kind,
        )
    except Exception:
        logger.warning("Could not classify document %s", metadata.get("document_id"), exc_info=True)
        return None


def _shelf_suggestion(kind: Optional[str], doc_type: Optional[str], subject: Optional[str], author: Optional[str]) -> str:
    if doc_type:
        return shelves.suggest_document_shelf(doc_type, subject)
    return shelves.suggest_shelf(kind, subject, author)


def _known_authors() -> List[str]:
    """Every author recorded in the library, so a lone surname from a file name
    ("Camus") can be completed the way the library already spells it."""
    return [doc["author"] for doc in list_metadata() if doc.get("author")]


def _shelf_fields(metadata: dict, kind: Optional[str]) -> dict:
    """A document's author, subject, type and suggested shelf (see shelves.py),
    for ``kind`` (the reader's override, else the detected kind). Never the
    reader's own ``shelf``. The title too, when it is still a guess from the
    file name that the author just learned makes tidier ("The Hobbit Tolkien").
    Best effort: {} when anything fails."""
    doc_id = metadata["document_id"]
    try:
        author = subject = None
        if kind != "note":
            author = shelves.pick_author(
                document_author(pdf_path(metadata)),
                metadata.get("filename") or "",
                lambda: _first_page_texts(doc_id, 6),
                _known_authors(),
            )
            subject = shelves.classify_subject(_document_vector(doc_id), _subject_vectors())
        if metadata.get("metadata_edited"):
            author, subject = metadata.get("author"), metadata.get("subject")
        doc_type = _document_type(metadata, kind)
        fields = {"author": author, "subject": subject, "shelf_suggested": _shelf_suggestion(kind, doc_type, subject, author)}
        fields["doc_type"] = doc_type  # None clears a stale type once a document turns out to be a book
        filename = metadata.get("filename") or ""
        if author and filename and not metadata.get("metadata_edited") and not metadata.get("metadata_source"):
            untouched_guess = {title_from_filename(filename), _legacy_title_from_filename(filename)}
            tidier = title_from_filename(filename, author)
            if metadata.get("title") in untouched_guess and tidier != metadata.get("title"):
                fields["title"] = tidier
        return fields
    except Exception:
        logger.warning("Could not suggest a shelf for document %s", doc_id, exc_info=True)
        return {}


def suggest_document_shelf(doc_id: str) -> Optional[dict]:
    """Recompute and store a document's suggested shelf."""
    metadata = read_metadata(doc_id)
    fields = _shelf_fields(metadata, metadata.get("kind_override") or metadata.get("kind"))
    return STORE.update(doc_id, fields) if fields else None


def _filename_detail_changes(doc: dict, known_authors: Sequence[str], spellings: Optional[dict] = None) -> dict:
    """The author and tidier title the file name, the file's own properties and
    the library give a record. Only guesses are touched: a record the reader
    edited, a catalogue match, or a title that is not the old file-name guess is
    left exactly as it is.

    An author already stored is replaced only when the file name credits
    someone with a different surname (the stored one was a translator, an
    editor or an institution), or to make it the library's one spelling of
    that person ("E.M. Cioran" -> "E. M. Cioran"); a lone surname is completed
    from the library or the title page."""
    filename = doc.get("filename") or ""
    if (
        not filename
        or doc.get("record_type") == "standalone"
        or doc.get("collection_id") == NOTES_COLLECTION_ID
        or doc.get("metadata_edited")
        or doc.get("metadata_source")
    ):
        return {}
    spellings = spellings or {}
    changes: dict = {}
    author = doc.get("author")
    pages_text = None

    def opening_pages() -> List[str]:
        nonlocal pages_text
        if pages_text is None:
            pages_text = _first_page_texts(doc["document_id"], 6) if doc.get("indexing_status") == "indexed" else []
        return pages_text

    if not author:
        try:
            embedded = document_author(pdf_path(doc))
        except Exception:
            embedded = None
        # The title page of an indexed book counts too; before indexing there is none.
        author = shelves.pick_author(embedded, filename, opening_pages, known_authors)
    elif shelves.author_from_filename(filename):
        author = shelves.pick_author(author, filename, opening_pages, known_authors) or author
    elif " " not in author.strip():
        author = shelves.complete_surname(author, opening_pages(), doc.get("title") or "") or author
    if author:
        author = spellings.get(shelves.normalize_author(author) or author) or shelves.normalize_author(author) or author
    if author and author != doc.get("author"):
        changes["author"] = author
    if doc.get("title") == _legacy_title_from_filename(filename):
        title = title_from_filename(filename, author)
        if title and title != doc["title"]:
            changes["title"] = title
    kind = doc.get("kind_override") or doc.get("kind")
    if "author" in changes and doc.get("shelf_suggested") and kind != "note":
        doc_type = None if kind == "book" else doc.get("doc_type")
        changes["shelf_suggested"] = _shelf_suggestion(kind, doc_type, doc.get("subject"), author)
    return changes


def backfill_filename_details() -> None:
    documents = list_metadata()
    known = [shelves.normalize_author(doc["author"]) or doc["author"] for doc in documents if doc.get("author")]
    spellings = shelves.canonical_authors(known)
    for doc in documents:
        if SHUTDOWN.is_set():
            return
        try:
            changes = _filename_detail_changes(doc, known, spellings)
            if changes:
                STORE.update(doc["document_id"], changes)
                if changes.get("author"):
                    known.append(changes["author"])
        except KeyError:
            pass  # deleted while the backfill ran
        except Exception:
            logger.warning("Could not read details from the file name of %s", doc.get("document_id"), exc_info=True)


def backfill_document_details() -> None:
    """Once per start, in the background: read the author and title out of file
    names, classify documents indexed before kind detection, then suggest
    shelves for those indexed before shelves."""
    backfill_filename_details()
    backfill_document_kinds()
    for doc in list_metadata():
        if SHUTDOWN.is_set():
            return
        if doc.get("indexing_status") == "indexed" and not doc.get("shelf_suggested"):
            suggest_document_shelf(doc["document_id"])
        elif doc.get("indexing_status") == "indexed" and not doc.get("doc_type") and _is_work_document(doc):
            doc_type = _document_type(doc, doc.get("kind_override") or doc.get("kind"))
            if doc_type:
                try:
                    # Only the type: a stored suggestion or the reader's shelf stays put.
                    STORE.update(doc["document_id"], {"doc_type": doc_type})
                except KeyError:
                    pass


def _embed_and_finish(doc_id: str) -> None:
    """Embed and upsert a document's already-extracted chunks, then mark it
    indexed. Runs under the document's lock: the embedding model, Qdrant
    client, and metadata store are single shared instances in this process
    (unlike extraction, which runs in its own worker process per document)."""
    with get_document_lock(doc_id):
        try:
            update_ingest_entry(doc_id, "indexing")
            chunks = read_json(CHUNKS_DIR / f"{doc_id}.json")
            total_chunks = len(chunks)
            set_index_activity(
                doc_id,
                "embedding",
                progress=45,
                completed_units=0,
                total_units=total_chunks,
            )
            delete_document_vectors(doc_id)
            vector_dim = 0
            for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
                batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
                vectors = embed_texts([chunk.get("embedding_text") or chunk["text"] for chunk in batch])
                indexed_batch = [{**chunk, "embedding": vector} for chunk, vector in zip(batch, vectors)]
                if indexed_batch:
                    vector_dim = len(indexed_batch[0]["embedding"])
                    upsert_chunks(indexed_batch)
                completed = min(start + len(batch), total_chunks)
                set_index_activity(
                    doc_id,
                    "embedding",
                    progress=45 + round((completed / max(1, total_chunks)) * 45),
                    completed_units=completed,
                    total_units=total_chunks,
                )
            set_index_activity(doc_id, "finalizing", progress=95)
            metadata = read_metadata(doc_id)
            kind_fields = _kind_fields(doc_id, metadata)
            kind = kind_fields.get("kind")
            shelf_fields = _shelf_fields(metadata, metadata.get("kind_override") or kind)
            form_fields = _form_fields({**metadata, **kind_fields, **shelf_fields})
            # One update: a document is never seen as indexed without its shelf.
            update_metadata(
                doc_id,
                indexing_status="indexed",
                indexing_error=None,
                indexed_at=utc_now(),
                embedding_model=EMBEDDING_MODEL,
                vector_dim=vector_dim,
                index_schema_version=INDEX_SCHEMA_VERSION,
                **kind_fields,
                **shelf_fields,
                **form_fields,
            )
            update_ingest_entry(doc_id, "indexed")
            clear_index_activity(doc_id)
            _maybe_auto_match(doc_id)
            try:
                _refresh_pdf_marks(read_metadata(doc_id))
            except Exception:
                logger.exception("could not read the marks in %s", doc_id)
        except Exception as exc:
            _fail_indexing(doc_id, str(exc) or exc.__class__.__name__)


def index_worker() -> None:
    """Drain every ``queued`` document whenever woken.

    Extraction -- a per-page ML layout pass, by far the most expensive step
    (profiled at ~96% of indexing time) -- is farmed out to
    EXTRACTION_EXECUTOR (a process pool) up to INDEX_EXTRACTION_WORKERS at a
    time, so documents queued together (a folder import, a bulk reindex, or
    several uploads landing close together) actually extract concurrently
    instead of one at a time. Embedding and upserting -- cheap by
    comparison, and dependent on this process's single shared embedding
    model and Qdrant client -- stay sequential here, run as each extraction
    finishes.

    Pulling work from the database (rather than a bounded in-memory queue) means
    a restart that re-queues the entire library cannot overflow anything or
    mark documents as failed for lack of space.
    """
    global EXTRACTION_EXECUTOR
    pending: dict = {}
    last_extraction = time.monotonic()
    while not SHUTDOWN.is_set():
        try:
            INDEX_SIGNAL.get(timeout=0.5 if pending else INDEX_POLL_SECONDS)
        except queue.Empty:
            pass

        while not SHUTDOWN.is_set() and len(pending) < INDEX_EXTRACTION_WORKERS:
            try:
                claimed = STORE.claim_for_indexing(utc_now())
            except Exception:
                logger.exception("Could not claim the next document for indexing")
                break
            if claimed is None:
                break
            doc_id = claimed["document_id"]
            if claimed.get("pipeline_version") != PIPELINE_VERSION:
                try:
                    stored_path = pdf_path(claimed)
                    if not stored_path.exists():
                        raise FileNotFoundError(
                            "the source PDF is missing (moved, or its disk is unmounted)"
                        )
                except Exception as exc:
                    _fail_indexing(doc_id, str(exc) or exc.__class__.__name__)
                    continue
                last_extraction = time.monotonic()
                set_index_activity(doc_id, "extracting", progress=None)
                if EXTRACTION_EXECUTOR is None:  # shut down while idle
                    EXTRACTION_EXECUTOR = _new_extraction_executor()
                try:
                    future = EXTRACTION_EXECUTOR.submit(
                        build_document_artifacts,
                        stored_path,
                        claimed["filename"],
                        doc_id,
                        bool(claimed.get("ocr_approved")),
                    )
                except concurrent.futures.BrokenExecutor:
                    # A worker process for some earlier document crashed (segfault/OOM
                    # on malformed PDF input) and left the pool unusable -- every
                    # further submit() would raise the same way forever. Replace it
                    # and retry this claim once against the fresh pool rather than
                    # letting the exception escape and kill this whole worker thread.
                    logger.exception(
                        "Extraction process pool was broken; restarting it before retrying %s",
                        doc_id,
                    )
                    EXTRACTION_EXECUTOR = _new_extraction_executor()
                    try:
                        future = EXTRACTION_EXECUTOR.submit(
                            build_document_artifacts,
                            stored_path,
                            claimed["filename"],
                            doc_id,
                            bool(claimed.get("ocr_approved")),
                        )
                    except concurrent.futures.BrokenExecutor as exc:
                        _fail_indexing(doc_id, str(exc) or exc.__class__.__name__)
                        continue
                pending[future] = doc_id
            else:
                _embed_and_finish(doc_id)

        if not pending:
            if (
                EXTRACTION_EXECUTOR is not None
                and MODEL_IDLE_UNLOAD_SECONDS > 0
                and time.monotonic() - last_extraction > MODEL_IDLE_UNLOAD_SECONDS
            ):
                # Nothing to extract for a while: let the worker processes
                # (each holding a layout model) exit. Recreated on demand.
                EXTRACTION_EXECUTOR.shutdown(wait=False, cancel_futures=True)
                EXTRACTION_EXECUTOR = None
                logger.info("Extraction workers idle; shut them down to free memory")
            continue
        last_extraction = time.monotonic()
        done, _ = concurrent.futures.wait(
            pending.keys(), timeout=1, return_when=concurrent.futures.FIRST_COMPLETED
        )
        for future in done:
            doc_id = pending.pop(future)
            try:
                page_count, chunks, group_count, extraction_notes = future.result()
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                if message.startswith(OCR_APPROVAL_MARKER):
                    _request_ocr_approval(doc_id, int(message[len(OCR_APPROVAL_MARKER):]))
                else:
                    _fail_indexing(doc_id, message)
                continue
            try:
                update_metadata(
                    doc_id,
                    pages=page_count,
                    chunks=group_count,
                    retrieval_units=len(chunks),
                    pipeline_version=PIPELINE_VERSION,
                    index_schema_version=0,
                    indexing_error=None,
                    extraction_notes=extraction_notes,
                )
                set_index_activity(
                    doc_id,
                    "embedding",
                    progress=45,
                    completed_units=0,
                    total_units=len(chunks),
                )
            except Exception as exc:
                _fail_indexing(doc_id, str(exc) or exc.__class__.__name__)
                continue
            _embed_and_finish(doc_id)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pdf_path(metadata: dict) -> Path:
    """Where this document's source actually lives.

    Browser folder imports reference the original file in place under
    ``INGEST_ROOT``; uploads are copied into ``DOCUMENTS_DIR``.
    """
    source = (metadata.get("source_path") or "").strip()
    if source:
        return INGEST_ROOT / source
    return DOCUMENTS_DIR / metadata["stored_filename"]


def build_document_artifacts(
    stored_path: Path, filename: str, doc_id: str, ocr_approved: bool = False
) -> tuple[int, List[dict], int, Optional[str]]:
    file_type = file_type_for_path(stored_path)
    pages, used_ocr = extract_source_pages(
        stored_path,
        ocr_mode=OCR_MODE,
        ocr_languages=OCR_LANGUAGES,
        ocr_engine=OCR_ENGINE,
        ocr_approved=ocr_approved,
        ocr_approval_pages=OCR_APPROVAL_PAGES,
    )
    if not any(page.get("text", "").strip() for page in pages):
        raise ValueError("the document contains no extractable text")
    # PDF-specific quality checks do not make sense for a spreadsheet sheet or
    # a slide deck.  OCR runs first, so a scan that was successfully read does
    # not get rejected as an image-only PDF afterwards.
    if file_type == "pdf":
        fatal_reason = assess_scanned(pages) or assess_text_layer(pages)
        if fatal_reason:
            raise ValueError(fatal_reason)

    total_pages = len(pages)
    # Likewise PDF-only: Office and text sources carry their text exactly as
    # written, so an OCR-mush verdict there is a false positive (a base64
    # blob, a table of codes) and would drop real content -- for a short file,
    # its only pseudo-page and so the whole document.
    garbled = set(garbled_page_indices(pages)) if file_type == "pdf" else set()
    extraction_notes = None
    if used_ocr:
        read_by = collections.Counter(page["ocr"] for page in pages if page.get("ocr"))
        extraction_notes = (
            f"{sum(read_by.values())} scanned page(s) were read with OCR"
            + (f", {read_by['nougat']} with equation recognition" if read_by.get("nougat") else "")
            + "."
        )
    if garbled:
        # A minority of the pages are OCR garbage. Drop just those and index
        # the rest rather than refusing the whole document. Page numbers must
        # be read off before filtering — `garbled` indexes the original list.
        skipped_page_numbers = sorted(pages[index]["page"] for index in garbled)
        pages = [page for index, page in enumerate(pages) if index not in garbled]
        skipped_note = describe_skipped_pages(skipped_page_numbers, total_pages)
        extraction_notes = " ".join(part for part in (extraction_notes, skipped_note) if part)
        logger.info("Skipped corrupt page(s) %s of %s", skipped_page_numbers, filename)

    # Contents / index / bibliography pages: keyword-dense, no retrievable prose.
    if file_type == "pdf":
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
        raise ValueError("the document produced no searchable text chunks")

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


def create_document(
    stored_path: Path,
    filename: str,
    content_sha256: str,
    *,
    doc_id: Optional[str] = None,
    source_path: Optional[str] = None,
    collection_id: Optional[str] = None,
) -> dict:
    """Record one document and queue it for extraction + indexing in the
    background. ``source_path`` (relative to ``INGEST_ROOT``) means the PDF
    is referenced in place, not owned by us.

    Extraction used to happen right here, synchronously, before this ever
    returned -- which meant an upload (or a folder import) never overlapped
    with any other document's extraction, the single most expensive step in
    the pipeline (a per-page ML layout pass). Recording it immediately with
    a sentinel ``pipeline_version`` and letting the background worker pool
    extract it lets documents queued together actually run concurrently,
    regardless of how they arrived. A PDF that turns out to have no
    extractable text, say, now fails later as this document's
    ``indexing_status`` rather than as an immediate error response here.
    """
    doc_id = doc_id or generate_id()
    now = utc_now()
    # Everything the file name and the file's own properties say, right away:
    # waiting for extraction and embedding would leave a queued book "Unknown
    # author" for as long as the queue is long.
    author = shelves.pick_author(document_author(stored_path), filename, known_authors=_known_authors())
    metadata = {
        "document_id": doc_id,
        "record_type": "source",
        "filename": filename,
        "file_type": file_type_for_path(filename) or "document",
        "title": (epub_title(stored_path) if file_type_for_path(filename) == "epub" else None)
        or title_from_filename(filename, author),
        "author": author,
        "stored_filename": stored_path.name,
        "content_sha256": content_sha256,
        "source_path": source_path,
        "collection_id": collection_id,
        "uploaded_at": now,
        "updated_at": now,
        "pages": 0,
        "chunks": 0,
        "retrieval_units": 0,
        "extraction_notes": None,
        # "pending" until enqueue_index flips it to "queued": the index
        # worker only claims "queued" rows, so a folder-ingest job always
        # has its progress link registered before the worker can pick it up.
        "indexing_status": "pending",
        "indexing_error": None,
        "embedding_model": EMBEDDING_MODEL,
        "vector_dim": 0,
        # Never a real version (those start at 1): guarantees the worker
        # takes its "needs extraction" path regardless of today's
        # PIPELINE_VERSION, since this document has no chunks yet at all.
        "pipeline_version": -1,
        "index_schema_version": 0,
        "reading_status": "to_read",
    }
    try:
        return STORE.create(metadata)
    except Exception:
        if source_path is None:  # only ever delete a PDF we own
            stored_path.unlink(missing_ok=True)
        STORE.delete(doc_id)
        raise


# Guards the duplicate-check-then-create sequence in import_library_file so
# two imports of the same file racing each other (a manual click landing at
# the same moment as the auto-ingest scan, say) can't both pass the "not a
# duplicate yet" check and create two document rows for one PDF.
_IMPORT_LOCK = threading.Lock()


def import_library_file(rel_path: str, collection_id: Optional[str] = None) -> tuple[dict, bool]:
    """Reference a supported file sitting under INGEST_ROOT (no copy). Returns
    ``(metadata, deduplicated)``."""
    source = resolve_library_path(rel_path)
    if not source.is_file() or not is_supported_path(source):
        raise HTTPException(status_code=400, detail="unsupported file type inside the library")
    content_sha256 = hash_file(source)  # outside the lock: hashing is the slow part
    with _IMPORT_LOCK:
        duplicate = find_duplicate(content_sha256)
        if duplicate:
            return duplicate, True
        rel = str(source.relative_to(INGEST_ROOT))
        return create_document(
            source,
            source.name,
            content_sha256,
            source_path=rel,
            collection_id=collection_id or collection_for_source_path(rel),
        ), False


def resolve_library_path(requested: Optional[str]) -> Path:
    """Resolve a path relative to INGEST_ROOT, refusing anything that escapes it.
    The path must exist (file or directory). ``requested`` can also be the
    mount's absolute HOST path (LIBRARY_HOST_PATH, e.g. pasted straight out of
    a file manager) -- that prefix is swapped for INGEST_ROOT first."""
    text = (requested or "").strip()
    if LIBRARY_HOST_PATH.startswith("/") and text.startswith("/") and (
        text == LIBRARY_HOST_PATH or text.startswith(LIBRARY_HOST_PATH + "/")
    ):
        text = text[len(LIBRARY_HOST_PATH):].lstrip("/")
    candidate = INGEST_ROOT if not text else Path(text)
    if not candidate.is_absolute():
        candidate = INGEST_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="path does not exist") from exc
    if resolved != INGEST_ROOT and INGEST_ROOT not in resolved.parents:
        raise HTTPException(status_code=403, detail="path must be inside the library root")
    return resolved


def resolve_ingest_folder(requested: Optional[str]) -> Path:
    resolved = resolve_library_path(requested)
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="ingest path is not a folder")
    return resolved


def _library_pdfs(folder: Path) -> List[Path]:
    """Every supported file under ``folder``, recursively, skipping dot-files and
    anything inside a dot-directory. Sorted for stable job ordering."""
    out = []
    for path in folder.rglob("*"):
        rel_parts = path.relative_to(folder).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.is_file() and is_supported_path(path):
            out.append(path)
    return sorted(out)


def ingest_worker() -> None:
    while True:
        item = INGEST_QUEUE.get()
        try:
            if item is None:
                return
            job_id, folder, collection_id = item
            with JOB_STATUS_LOCK:
                job = JOB_STATUS[job_id]
                job["state"] = "processing"
                save_job(job)

            files = _library_pdfs(Path(folder))
            with JOB_STATUS_LOCK:
                job = JOB_STATUS[job_id]
                job["total_files"] = len(files)
                save_job(job)
            for pdf in files:
                rel = str(pdf.relative_to(INGEST_ROOT))
                with JOB_STATUS_LOCK:
                    job = JOB_STATUS[job_id]
                    file_index = len(job["files"])
                    job["files"].append({"file": rel, "status": "processing"})
                    save_job(job)
                try:
                    metadata, duplicate = import_library_file(rel, collection_id)
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


def _auto_ingest_scan() -> int:
    """Reference-import new files from each active named collection.

    Cheap on a re-run: only files whose relative path isn't already a
    ``source_path`` in the metadata store are hashed at all, so a large,
    mostly-unchanged library costs one directory walk plus one DB read per
    scan, not a re-hash of everything in it.
    """
    known = {doc["source_path"] for doc in list_metadata() if doc.get("source_path")}
    imported = 0
    seen_paths = set()
    for collection in configured_collections():
        if not collection.get("auto_scan"):
            continue
        try:
            folder = resolve_ingest_folder(collection.get("path") or "")
        except HTTPException:
            continue
        for source in _library_pdfs(folder):
            rel = str(source.relative_to(INGEST_ROOT))
            if rel in known or rel in seen_paths:
                continue
            seen_paths.add(rel)
            try:
                metadata, duplicate = import_library_file(rel, collection["id"])
            except Exception:
                logger.exception("Automatic library scan could not import %s", rel)
                continue
            if not duplicate:
                enqueue_index(IndexTask(metadata["document_id"]))
                imported += 1
    if imported:
        logger.info("Automatic library scan imported %d new document(s)", imported)
    return imported


def auto_ingest_worker() -> None:
    """Rescans the mounted library on AUTO_INGEST_INTERVAL_SECONDS, starting
    with an immediate scan so a library populated before the container's
    first start doesn't need a manual Attach/Import click. Only started when
    the interval is positive; see start_workers()."""
    while not SHUTDOWN.is_set():
        try:
            _auto_ingest_scan()
            _refresh_all_pdf_marks()
        except Exception:
            logger.exception("Automatic library scan failed")
        if SHUTDOWN.wait(AUTO_INGEST_INTERVAL_SECONDS):
            return


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
    _prune_old_jobs()

    # Mark everything that needs (re)indexing as ``queued`` and let the worker
    # pull it. The pipeline rebuild for stale documents happens in the worker,
    # so a crash partway through recovery leaves consistent database state.
    requeued = 0
    parked = 0
    for metadata in list_metadata():
        doc_id = metadata.get("document_id")
        # Catalogue-only books have no source to index; without this their
        # empty stored path resolves to the documents directory, which exists.
        if not doc_id or metadata.get("record_type") == "standalone":
            continue
        # A scan waiting for the reader's OCR go-ahead never reached a current
        # pipeline_version, so it always looks stale. Re-queuing it would
        # extract the whole PDF again (minutes of CPU for a long book) only to
        # park it again at the same gate; approve-ocr and retry re-queue it
        # on demand.
        if awaiting_ocr_approval(metadata):
            parked += 1
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
                pdf_path(metadata).exists()
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
    if parked:
        logger.info("Left %d scanned document(s) waiting for OCR approval", parked)
    signal_index_worker()


def idle_reaper() -> None:
    """Unload the embedding and reranker models once nothing has used them
    for MODEL_IDLE_UNLOAD_SECONDS (see its definition)."""
    while not SHUTDOWN.wait(60):
        for name, unload in (("embedding", unload_embedder_if_idle), ("reranker", unload_reranker_if_idle)):
            try:
                if unload(MODEL_IDLE_UNLOAD_SECONDS):
                    logger.info("Unloaded the idle %s model to free memory", name)
            except Exception:
                logger.warning("Could not unload the idle %s model", name, exc_info=True)


def start_workers() -> None:
    global WORKER_THREADS, RERANK_EXECUTOR, EXTRACTION_EXECUTOR
    if any(thread.is_alive() for thread in WORKER_THREADS):
        return
    SHUTDOWN.clear()
    RERANK_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
        max_workers=RERANK_MAX_WORKERS,
        thread_name_prefix="reranker",
    )
    # "fork" (Linux/Docker's default anyway, made explicit here): the worker
    # only ever runs build_document_artifacts, a pure function of its plain
    # arguments that touches none of this process's shared locks or open
    # connections (STORE, Qdrant, the embedding model) -- exactly the case
    # fork-in-a-threaded-process is safe for, since the child never needs
    # any lock it might have inherited mid-acquisition.
    #
    # Created on first use (see index_worker), not here: a service that
    # starts with nothing to index never spawns the worker processes. The
    # small tokenizer they inherit is still loaded now, so the first upload
    # does not pay for it.
    load_tokenizer()
    EXTRACTION_EXECUTOR = None
    WORKER_THREADS = [
        threading.Thread(target=index_worker, daemon=True, name="index-worker"),
        threading.Thread(target=ingest_worker, daemon=True, name="ingest-worker"),
        threading.Thread(target=recover_interrupted_work, daemon=True, name="recovery-worker"),
        threading.Thread(target=backfill_document_details, daemon=True, name="detail-backfill"),
    ]
    if MODEL_IDLE_UNLOAD_SECONDS > 0:
        WORKER_THREADS.append(threading.Thread(target=idle_reaper, daemon=True, name="idle-reaper"))
    if AUTO_INGEST_INTERVAL_SECONDS > 0:
        WORKER_THREADS.append(
            threading.Thread(target=auto_ingest_worker, daemon=True, name="auto-ingest-worker")
        )
    for thread in WORKER_THREADS:
        thread.start()


def stop_workers() -> None:
    global RERANK_EXECUTOR, EXTRACTION_EXECUTOR
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
    if EXTRACTION_EXECUTOR is not None:
        EXTRACTION_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        EXTRACTION_EXECUTOR = None


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
    path = scope["path"]
    if path in _AUTH_EXEMPT_PATHS or path.startswith(_AUTH_EXEMPT_PREFIXES):
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
    document_ids: Optional[List[str]] = None,
) -> tuple[List[dict], int, int, int]:
    """``document_ids`` (a library's documents) narrows the search to those;
    an empty list means the library has nothing indexed, so nothing matches."""
    if document_ids is not None:
        if document_id:
            document_ids = [document_id] if document_id in document_ids else []
        if not document_ids:
            return [], 0, 0, 0
    query_vector = await asyncio.to_thread(lambda: embed_texts([query], kind="query")[0])
    filters = {
        key: value
        for key, value in {
            "document_id": document_ids if document_ids is not None else document_id,
            "filename": filename,
        }.items()
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


def _agentic_hit_key(hit: dict) -> tuple:
    payload = hit.get("payload") or {}
    return (
        payload.get("document_id"),
        payload.get("group_id") or payload.get("chunk_id"),
        payload.get("page"),
        payload.get("text") or payload.get("retrieval_text"),
    )


def merge_agentic_hits(existing: List[dict], additions: List[dict], limit: int) -> List[dict]:
    """Merge follow-up results while keeping the strongest score per passage."""
    merged = {_agentic_hit_key(hit): hit for hit in existing}
    for hit in additions:
        key = _agentic_hit_key(hit)
        previous = merged.get(key)
        hit_score = hit.get("rerank_score")
        previous_score = previous.get("rerank_score") if previous else None
        if previous is None or (
            hit_score is not None
            and (previous_score is None or hit_score > previous_score)
        ):
            merged[key] = hit
    return sorted(
        merged.values(),
        key=lambda hit: (
            hit.get("rerank_score") is not None,
            hit.get("rerank_score") if hit.get("rerank_score") is not None else hit.get("score", 0),
        ),
        reverse=True,
    )[:limit]


def _agentic_required_query(question: str, sources: List[dict]) -> Optional[str]:
    """Return a deterministic precision query for an explicit book-size ask."""
    lowered = (question or "").casefold()
    asks_book_size = "book" in lowered and any(
        term in lowered for term in ("page", "how large", "size", "how many")
    )
    if not asks_book_size:
        return None
    number_words = (
        "zero|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|"
        "forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million"
    )
    for source in sources:
        text = (source.get("text") or "").casefold()
        if (
            "book" in text
            and "page" in text
            and (re.search(r"\b\d{2,}\b", text) or re.search(rf"\b(?:{number_words})\b", text))
        ):
            return None
    return "how many pages does each book have"


_TERMINAL_PUNCTUATION = tuple(".!?\"')”’»…")


def _runs_on(text: str, filename: str) -> bool:
    """Whether a passage visibly stops mid-sentence.

    Only prose can: a slide, a sheet, a table row, a heading or an equation
    legitimately ends without punctuation, and an ellipsis there would
    suggest text is missing.
    """
    if not text or text.endswith(_TERMINAL_PUNCTUATION):
        return False
    if (file_type_for_path(filename) or "pdf") not in {"pdf", "epub", "word", "text", "markdown"}:
        return False
    last_line = text.rstrip().rsplit("\n", 1)[-1].strip()
    return not (
        last_line.startswith(("#", "|", "$$", "\\]", "- ", "* "))
        or last_line.endswith(("$$", "\\]", "|"))
        or re.match(r"^\d+[.)]\s", last_line)
    )


def format_hits(hits: List[dict], max_text_chars: int = 20_000) -> List[dict]:
    formatted = []
    for hit in hits:
        payload = hit.get("payload") or {}
        # Tidied at display time as well as at ingest, so passages indexed
        # before the ingest-side cleanup never render with blank gaps.
        group_text = collapse_blank_lines(payload.get("text") or "")
        text = group_text
        truncated = len(text) > max_text_chars
        if truncated:
            text = text[: max_text_chars - 1].rstrip() + "…"
        elif _runs_on(text, payload.get("filename") or ""):
            # The passage ends mid-sentence because the paragraph runs into the
            # next group; signal that rather than implying it stops here.
            text = f"{text} …"

        # The sub-passage that actually won retrieval, for the UI to highlight.
        # Empty when the whole group matched (nothing more specific to mark).
        matched = collapse_blank_lines(payload.get("retrieval_text") or "")
        if not matched or matched == group_text or len(matched) >= len(group_text) * 0.9:
            matched = ""

        filename = payload.get("filename") or ""
        current = STORE.get(payload.get("document_id") or "") or {}
        file_type = file_type_for_path(filename) or "pdf"
        page = payload.get("page")
        page_end = payload.get("page_end") or page
        formatted.append(
            {
                "chunk_id": payload.get("chunk_id"),
                "group_id": payload.get("group_id"),
                "document_id": payload.get("document_id"),
                "document": filename or payload.get("document_id"),
                "title": current.get("title"),
                "is_note": current.get("collection_id") == NOTES_COLLECTION_ID,
                "file_type": file_type,
                "page": page,
                "page_end": page_end,
                "location": location_label(file_type, page, page_end),
                "block_types": payload.get("block_types") or [],
                "protected_type": payload.get("protected_type"),
                "score": hit.get("score"),
                "rerank_score": hit.get("rerank_score"),
                "lead_in": collapse_blank_lines(payload.get("lead_in") or ""),
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
        # Defaults the UI's relevance-threshold controls start from.
        "rerank_min_score": RERANK_MIN_SCORE,
        "ask_thorough_min_score": ASK_THOROUGH_MIN_SCORE,
        "ask_agentic_max_steps": ASK_AGENTIC_MAX_STEPS,
        "generation": await generation.backend_info(),
        "pipeline_version": PIPELINE_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "supported_file_types": sorted(SUPPORTED_EXTENSIONS),
        "passive_scan_seconds": AUTO_INGEST_INTERVAL_SECONDS,
        "ocr": {
            "mode": OCR_MODE,
            "languages": OCR_LANGUAGES,
            "engine": OCR_ENGINE,
            "approval_pages": OCR_APPROVAL_PAGES,
        },
        "fusion": FUSION_METHOD,
        "chunking": {
            "target_tokens": CHUNK_TARGET_TOKENS,
            "soft_max_tokens": CHUNK_SOFT_MAX_TOKENS,
            "hard_max_tokens": CHUNK_HARD_MAX_TOKENS,
            "overlap_tokens": CHUNK_OVERLAP_TOKENS,
        },
    }


def _with_note_counts(documents: List[dict]) -> List[dict]:
    """Each book's `note_count`: how many of the reader's notes are linked to it
    (so the library can mark and filter books that have notes)."""
    counts = collections.Counter(
        doc["source_document_id"]
        for doc in documents
        if doc.get("collection_id") == NOTES_COLLECTION_ID and doc.get("source_document_id") and not _is_digest(doc)
    )
    return [
        doc if doc.get("collection_id") == NOTES_COLLECTION_ID else {**doc, "note_count": counts.get(doc["document_id"], 0)}
        for doc in documents
    ]


@app.get("/documents")
async def get_documents():
    return {"documents": await asyncio.to_thread(lambda: _with_note_counts(list_metadata()))}


def _index_activity_snapshot() -> dict:
    """Return durable queue history enriched with live worker-stage details."""
    documents = [
        document
        for document in list_metadata()
        if document.get("record_type") != "standalone"
    ]
    queued = sorted(
        (document for document in documents if document.get("indexing_status") == "queued"),
        key=lambda document: document.get("updated_at") or document.get("uploaded_at") or "",
    )
    queue_positions = {
        document["document_id"]: position for position, document in enumerate(queued, start=1)
    }
    with INDEX_ACTIVITY_LOCK:
        runtime = {document_id: dict(details) for document_id, details in INDEX_ACTIVITY.items()}

    items = []
    for document in documents:
        document_id = document["document_id"]
        status = document.get("indexing_status") or "queued"
        live = runtime.get(document_id, {})
        awaiting_ocr = awaiting_ocr_approval(document)
        if status == "indexed":
            stage, progress = "complete", 100
        elif awaiting_ocr:
            stage, progress = "awaiting_ocr", None
        elif status == "error":
            stage, progress = "failed", None
        elif status == "queued":
            stage, progress = live.get("stage", "queued"), live.get("progress", 0)
        elif status == "indexing":
            stage, progress = live.get("stage", "extracting"), live.get("progress")
        else:
            stage, progress = status, None

        try:
            size_bytes = pdf_path(document).stat().st_size
        except (OSError, ValueError):
            size_bytes = None

        items.append(
            {
                "document_id": document_id,
                "title": document.get("title"),
                "author": document.get("author"),
                "filename": document.get("filename"),
                "file_type": document.get("file_type"),
                "source_path": document.get("source_path"),
                "collection_id": document.get("collection_id"),
                "indexing_status": status,
                "indexing_error": document.get("indexing_error"),
                "stage": stage,
                "progress": progress,
                "completed_units": live.get("completed_units"),
                "total_units": live.get("total_units"),
                "queue_position": queue_positions.get(document_id),
                "size_bytes": size_bytes,
                "uploaded_at": document.get("uploaded_at"),
                "started_at": live.get("started_at"),
                "updated_at": live.get("updated_at") or document.get("updated_at"),
                "indexed_at": document.get("indexed_at"),
                "pages": document.get("pages"),
                "chunks": document.get("chunks"),
                "retrieval_units": document.get("retrieval_units"),
                "ocr_pages": document.get("ocr_pages"),
                "ocr_approved": bool(document.get("ocr_approved")),
            }
        )

    rank = {"indexing": 0, "queued": 1, "error": 2, "indexed": 3}
    items.sort(
        key=lambda item: (
            rank.get(item["indexing_status"], 4),
            item.get("queue_position") or 0,
            item.get("indexed_at") or item.get("updated_at") or "",
        ),
        reverse=False,
    )
    counts = {
        status: sum(item["indexing_status"] == status for item in items)
        for status in ("indexing", "queued", "indexed", "error")
    }
    active_items = [
        item for item in items if item["indexing_status"] in {"indexing", "queued"}
    ]
    return {
        "items": items,
        "summary": {
            **counts,
            "active": counts["indexing"] + counts["queued"],
            "active_bytes": sum(item.get("size_bytes") or 0 for item in active_items),
            "total": len(items),
        },
        "updated_at": utc_now(),
    }


@app.get("/index-activity")
async def get_index_activity():
    return await asyncio.to_thread(_index_activity_snapshot)


def _catalogue_book_response(document: dict) -> dict:
    """Document-shaped response plus the old keys for Release 1 clients."""
    return {
        **document,
        "book_id": document["document_id"],
        "notes": document.get("review"),
        "pdf_less": True,
        "match": None,
        "match_alert": False,
    }


def _standalone_books() -> list[dict]:
    return [
        _catalogue_book_response(document)
        for document in list_metadata()
        if document.get("record_type") == "standalone"
    ]


@app.get("/owned-books")
async def get_owned_books():
    return {"books": await asyncio.to_thread(_standalone_books)}


def _new_standalone_record(document_id: str, title: str, author: Optional[str]) -> dict:
    """A fileless catalogue book that the indexing workers never claim."""
    now = utc_now()
    return {
        "document_id": document_id,
        "record_type": "standalone",
        "filename": title,
        "file_type": "book",
        "title": title,
        "stored_filename": "",
        "content_sha256": f"standalone:{document_id}",
        "uploaded_at": now,
        "updated_at": now,
        "pages": 0,
        "chunks": 0,
        "retrieval_units": 0,
        "indexing_status": "catalogued",
        "vector_dim": 0,
        "pipeline_version": 0,
        "index_schema_version": 0,
        "kind": "book",
        "kind_override": "book",
        "owned": 1,
        "author": author,
        "reading_status": "to_read",
        "shelf_suggested": shelves.suggest_shelf("book", None, author),
    }


@app.post("/owned-books")
async def create_owned_book(body: OwnedBookBody):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title cannot be blank")
    document_id = secrets.token_hex(6)
    author = body.author.strip() if body.author else None
    record = _new_standalone_record(document_id, title, author)
    record["review"] = body.notes.strip() if body.notes else None
    created = await asyncio.to_thread(STORE.create, record)
    return JSONResponse({"book": _catalogue_book_response(created)}, status_code=201)


@app.patch("/owned-books/{book_id}")
async def patch_owned_book(book_id: str, body: OwnedBookPatch):
    if not re.fullmatch(r"[a-f0-9]{12}", book_id):
        raise HTTPException(status_code=400, detail="invalid owned-book id")
    document = await asyncio.to_thread(read_metadata, book_id)
    if document.get("record_type") != "standalone":
        raise HTTPException(status_code=404, detail="catalogue-only book not found")
    supplied = body.model_fields_set
    changes = {}
    if "title" in supplied:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="title cannot be blank")
        changes.update({"title": title, "filename": title})
    if "author" in supplied:
        changes["author"] = (body.author or "").strip() or None
    if "notes" in supplied:
        changes["review"] = (body.notes or "").strip() or None
    changes["updated_at"] = utc_now()
    try:
        updated = await asyncio.to_thread(STORE.update, book_id, changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="owned book not found") from exc
    return {"book": _catalogue_book_response(updated)}


@app.delete("/owned-books/{book_id}")
async def delete_owned_book(book_id: str):
    if not re.fullmatch(r"[a-f0-9]{12}", book_id):
        raise HTTPException(status_code=400, detail="invalid owned-book id")
    document = await asyncio.to_thread(read_metadata, book_id)
    if document.get("record_type") != "standalone":
        raise HTTPException(status_code=404, detail="catalogue-only book not found")
    await asyncio.to_thread((THUMBNAILS_DIR / f"{book_id}-custom.jpg").unlink, missing_ok=True)
    await asyncio.to_thread(STORE.delete, book_id)
    return {"ok": True}


@app.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="supported files are PDF, EPUB, Word, Excel, PowerPoint, text, Markdown, and CSV",
        )

    doc_id = generate_id()
    stored_path = DOCUMENTS_DIR / f"{doc_id}{suffix}"
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
        metadata = await asyncio.to_thread(
            create_document, stored_path, Path(file.filename).name, digest.hexdigest(), doc_id=doc_id
        )
    except Exception as exc:
        # Extraction itself now happens in the background (see
        # create_document) -- a rejection like "no extractable text" surfaces
        # later as this document's indexing_status, not here. Only a genuine
        # bookkeeping failure (the metadata insert itself) reaches this.
        raise _sanitized_http_error(
            422, "document ingestion failed", exc, log_context=file.filename
        ) from exc
    enqueue_index(IndexTask(doc_id))
    return JSONResponse(metadata, status_code=201)


# ------------------------------------------------------------------ intake --
# An upload does not go straight into the library. It is staged, read, and
# classified (format, type, shelf, title, author, year ...); the reader checks
# the result on a review screen and only then saves it, which creates the real
# record and queues indexing. Staged files live in INTAKE_DIR as <id>.<ext> beside
# <id>.json (the proposal); nothing else in the app ever lists them.

INTAKE_MAX_PAGES = 2500  # pages read for the first look; the indexer reads them all later
INTAKE_ORPHAN_SECONDS = 3600  # a staged file with no proposal beside it is leftover after this long
_INTAKE_ID = re.compile(r"^[a-f0-9]{12}$")


def sweep_intake() -> int:
    """Remove what an interrupted upload left in INTAKE_DIR: partial downloads, and
    staged files whose proposal was never written. A waiting upload (a file with its
    proposal) is never touched; the reader decides what happens to those."""
    removed = 0
    now = time.time()
    for path in INTAKE_DIR.iterdir():
        try:
            if path.name.startswith(".") and path.name.endswith(".uploading"):
                stale = now - path.stat().st_mtime > INTAKE_ORPHAN_SECONDS
            elif path.suffix != ".json" and not (INTAKE_DIR / f"{path.stem}.json").exists():
                stale = now - path.stat().st_mtime > INTAKE_ORPHAN_SECONDS
            else:
                stale = False
            if stale:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


sweep_intake()


def _intake_item_path(item_id: str) -> Path:
    if not _INTAKE_ID.fullmatch(item_id):
        raise HTTPException(status_code=404, detail="upload not found")
    return INTAKE_DIR / f"{item_id}.json"


def _intake_read(item_id: str) -> dict:
    path = _intake_item_path(item_id)
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="upload not found") from exc


def _intake_items() -> List[dict]:
    items = []
    for path in sorted(INTAKE_DIR.glob("*.json")):
        try:
            item = read_json(path)
        except (OSError, ValueError):
            continue
        if item.get("id") and (INTAKE_DIR / item.get("stored_filename", "")).exists():
            items.append(item)
    return sorted(items, key=lambda item: item.get("created_at") or "")


def _intake_discard_files(item: dict) -> None:
    (INTAKE_DIR / item["stored_filename"]).unlink(missing_ok=True)
    (INTAKE_DIR / f"{item['id']}.json").unlink(missing_ok=True)


def _light_pages(path: Path, file_type: str) -> tuple[List[str], int]:
    """The text of each page, read quickly for a first look: no layout model and no
    OCR (the indexer does the careful reading once the reader saves). Also the
    file's real page count, which can exceed the pages read."""
    if file_type == "pdf":
        with pymupdf.open(str(path)) as document:
            if document.needs_pass:
                raise ValueError("password-protected PDFs are not supported")
            total = document.page_count
            return [document[index].get_text("text") for index in range(min(total, INTAKE_MAX_PAGES))], total
    pages, _ = extract_source_pages(path, ocr_mode="off")
    texts = [page.get("text") or "" for page in pages]
    return texts[:INTAKE_MAX_PAGES], len(pages)


def _intake_subject(texts: List[str]) -> Optional[str]:
    """The shelf subject (Philosophy, Physics ...) the opening text is closest to."""
    passages = []
    for text in texts[:60]:
        for paragraph in re.split(r"\n{2,}", text):
            if len(_WORDISH.findall(paragraph)) >= 25:
                passages.append(paragraph.strip()[:1200])
    passages = sorted(passages, key=lambda item: -len(_WORDISH.findall(item)))[:8]
    if not passages:
        return None
    return shelves.classify_subject(shelves.mean_vector(embed_texts(passages)), _subject_vectors())


def _author_source(author: Optional[str], filename: str, embedded: Optional[str], texts: List[str]) -> Optional[str]:
    """Where an author came from, for the reader to judge it by."""
    if not author:
        return None
    surname = author.split()[-1].casefold()
    named = shelves.author_from_filename(filename)
    if named and named.split()[-1].casefold() == surname:
        return "file name"
    metadata = shelves.normalize_author(embedded)
    if metadata and metadata.split()[-1].casefold() == surname:
        return "file properties"
    if shelves.author_from_text(texts[:6]):
        return "title page"
    return "guessed"


def _analyze_intake(path: Path, filename: str, file_type: str) -> dict:
    """Everything the rules can say about a staged file: {"proposal", "sources",
    "notes", "pages"}. Raises when the file cannot be read at all."""
    notes: List[str] = []
    texts, total = _light_pages(path, file_type)
    readable = [text for text in texts if len(text.strip()) >= 25]
    if file_type == "pdf" and total and len(readable) < max(1, total // 5):
        notes.append("This looks like a scan with little or no text layer. After you save it, its text is read with OCR; a large scan waits for your approval first.")
    elif not readable:
        notes.append("No readable text was found in this file.")
    if total > len(texts):
        notes.append(f"Only the first {len(texts):,} of {total:,} pages were read for this first look.")

    embedded = intake.embedded_details(path, file_type)
    author = shelves.pick_author(embedded.get("author"), filename, lambda: texts[:6], _known_authors())
    stem = Path(filename).stem
    if file_type == "epub" and embedded.get("title"):
        title, title_source = embedded["title"], "file properties"
    elif not intake.junk_stem(stem):
        title, title_source = title_from_filename(filename, author), "file name"
    elif embedded.get("title"):
        title, title_source = embedded["title"], "file properties"
    else:
        title, title_source = title_from_filename(filename, author) or stem, "file name"

    genres = embedded.get("genres") or []
    authors = max(shelves.author_count(embedded.get("author")), shelves.credit_count(filename))
    verdict = doc_kind.classify(
        texts, total, file_type=file_type, author=author or "", authors=authors, title=title, subject=None, genres=genres,
    )
    try:
        subject = _intake_subject(texts) if readable else None
    except Exception:
        logger.warning("Could not match %s to a subject", filename, exc_info=True)
        subject = None
    pseudo = {
        "kind": verdict.kind, "title": title, "subtitle": None, "subject": subject, "genres_json": json.dumps(genres),
        "prose_score": verdict.prose_score, "text_stats": json.dumps({"words": verdict.words, "past": verdict.past}),
        "page_count": None, "pages": total, "author_count": verdict.authors, "author": author,
    }
    form = _form_fields(pseudo).get("book_form")
    doc_type = None if verdict.kind == "book" else classify_document(file_type, texts[:3], total, verdict.kind)
    type_value = intake.type_of(verdict.kind, form, doc_type)

    details: dict = {}
    sources: dict = {"title": title_source, "type": "the text of the file" if readable else "the file format"}
    if author:
        sources["author"] = _author_source(author, filename, embedded.get("author"), texts)
    if subject:
        sources["subject"] = "the opening text"
    for key in ("publisher", "published_year", "language", "isbn_10", "isbn_13", "description"):
        if embedded.get(key):
            details[key] = embedded[key]
            sources[key] = "file properties"
    if verdict.kind in {"book", "paper"}:
        for key, value in intake.front_matter_details(texts).items():
            if key not in details:
                details[key] = value
                sources[key] = "copyright page"
    if "language" not in details and verdict.english:
        details["language"], sources["language"] = "en", "the text of the file"
    proposal = {
        "title": title, "subtitle": "", "author": author or "", "subject": subject or "", "genres": genres,
        "description": details.get("description", ""), "publisher": details.get("publisher", ""),
        "published_year": details.get("published_year"), "language": details.get("language", ""),
        "isbn_10": details.get("isbn_10", ""), "isbn_13": details.get("isbn_13", ""), "page_count": None,
        "type": type_value, "shelf": _shelf_suggestion(verdict.kind, doc_type, subject, author),
    }
    return {"proposal": proposal, "sources": sources, "notes": notes, "pages": total}


def _stage_upload(item_id: str, staged: Path, filename: str, file_type: str, sha256: str, size: int, format_note: Optional[str]) -> dict:
    """Read a staged file and keep what was found beside it (blocking)."""
    item = {
        "id": item_id, "filename": filename, "stored_filename": staged.name, "format": file_type,
        "format_label": intake.FORMAT_LABELS.get(file_type, file_type), "format_note": format_note,
        "size": size, "sha256": sha256, "created_at": utc_now(), "state": "ready", "error": None,
        "has_cover": file_type in {"pdf", "epub"}, "pages": 0, "notes": [], "sources": {}, "proposal": None,
    }
    try:
        item.update(_analyze_intake(staged, filename, file_type))
    except Exception as exc:
        logger.warning("Could not read the upload %s", filename, exc_info=True)
        author = shelves.pick_author(None, filename, known_authors=_known_authors())
        item.update(
            state="unreadable", error=str(exc)[:300] or exc.__class__.__name__,
            proposal={
                "title": title_from_filename(filename, author), "subtitle": "", "author": author or "", "subject": "", "genres": [],
                "description": "", "publisher": "", "published_year": None, "language": "", "isbn_10": "", "isbn_13": "",
                "page_count": None, "type": "book" if file_type in {"pdf", "epub"} else "other",
                "shelf": _shelf_suggestion("book" if file_type in {"pdf", "epub"} else "document", None, None, author),
            },
            sources={"title": "file name"},
            notes=["This file could not be read, so these details come from its name alone. It will probably fail to index; you can still save it or discard it."],
        )
    atomic_write_json(INTAKE_DIR / f"{item_id}.json", item)
    return item


@app.post("/intake")
async def intake_upload(file: UploadFile = File(...)):
    """Stage one uploaded file: store it, work out what it is, and return the
    proposal for the reader to check. Nothing joins the library yet."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")
    filename = Path(file.filename).name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="supported files are PDF, EPUB, Word, Excel, PowerPoint, text, Markdown, and CSV")

    item_id = generate_id()
    temporary = INTAKE_DIR / f".{item_id}.uploading"
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit")
                digest.update(chunk)
                destination.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="uploaded file is empty")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    sha256 = digest.hexdigest()
    duplicate = await asyncio.to_thread(find_duplicate, sha256)
    if duplicate:
        temporary.unlink(missing_ok=True)
        return JSONResponse({"duplicate": {"document_id": duplicate["document_id"], "title": duplicate.get("title") or duplicate.get("filename")}}, status_code=200)
    waiting = next((item for item in await asyncio.to_thread(_intake_items) if item.get("sha256") == sha256), None)
    if waiting:
        temporary.unlink(missing_ok=True)
        return JSONResponse({"duplicate": {"intake_id": waiting["id"], "title": waiting["proposal"]["title"]}}, status_code=200)

    # The content decides how the file is read, whatever its extension says.
    named_type = file_type_for_path(filename) or "text"
    file_type, format_note = await asyncio.to_thread(intake.resolve_format, named_type, temporary)
    if file_type != named_type:
        filename = Path(filename).with_suffix(intake.SUFFIXES[file_type]).name
    staged = INTAKE_DIR / f"{item_id}{intake.SUFFIXES.get(file_type, suffix)}"
    temporary.replace(staged)
    try:
        item = await asyncio.to_thread(_stage_upload, item_id, staged, filename, file_type, sha256, total, format_note)
    except Exception as exc:
        staged.unlink(missing_ok=True)
        raise _sanitized_http_error(422, "could not read the upload", exc, log_context=filename) from exc
    return JSONResponse(item, status_code=201)


@app.get("/intake/options")
async def intake_options():
    """The choices the review screen offers: the types, and the shelves in use."""

    def build() -> dict:
        shelf_paths = set(shelves.TOP_LEVEL.values()) | set(shelves.DOCUMENT_TOP_LEVEL.values())
        for doc in list_metadata():
            shelf = shelves.effective_shelf(doc)
            if shelf and doc.get("collection_id") != NOTES_COLLECTION_ID:
                parts = shelf.split(shelves.SEPARATOR)
                shelf_paths.update(shelves.SEPARATOR.join(parts[: index + 1]) for index in range(len(parts)))
        shelf_paths.discard("Notes")
        return {"types": intake.type_options(), "shelves": sorted(shelf_paths, key=str.casefold)}

    return await asyncio.to_thread(build)


@app.get("/intake")
async def intake_list():
    return {"items": await asyncio.to_thread(_intake_items)}


@app.get("/intake/{item_id}")
async def intake_get(item_id: str):
    return await asyncio.to_thread(_intake_read, item_id)


@app.get("/intake/{item_id}/cover")
async def intake_cover(item_id: str, w: int = Query(default=320, ge=80, le=640)):
    """The first page of a staged PDF or EPUB, for the review screen."""
    item = await asyncio.to_thread(_intake_read, item_id)
    staged = INTAKE_DIR / item["stored_filename"]
    if item["format"] not in {"pdf", "epub"} or not staged.exists():
        raise HTTPException(status_code=404, detail="no cover for this file")

    def render() -> bytes:
        with pymupdf.open(staged) as source:
            if source.page_count < 1:
                raise HTTPException(status_code=404, detail="document has no pages")
            page = source[0]
            scale = w / max(page.rect.width, 1)
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("jpeg", jpg_quality=82)

    try:
        image = await asyncio.to_thread(render)
    except HTTPException:
        raise
    except Exception as exc:
        raise _sanitized_http_error(500, "could not render the cover", exc, log_context=item_id) from exc
    return Response(content=image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


class IntakeSave(DocumentPatch):
    """The reviewed details of a staged upload: a patch, plus the chosen type."""

    title: str = Field(min_length=1, max_length=300)
    type: str = Field(pattern=r"^(?:novel|novella|short_story|collection|nonfiction|book|paper|report|manual|slides|data|writing|legal|other)$")


@app.post("/intake/{item_id}/save", status_code=201)
async def intake_save(item_id: str, body: IntakeSave):
    """Add a reviewed upload to the library and queue it for indexing. What the
    reader confirmed counts as theirs: later rules do not overwrite it."""
    item = await asyncio.to_thread(_intake_read, item_id)
    staged = INTAKE_DIR / item["stored_filename"]
    if not staged.exists():
        raise HTTPException(status_code=410, detail="the uploaded file is gone; upload it again")
    if await asyncio.to_thread(find_duplicate, item["sha256"]):
        raise HTTPException(status_code=409, detail="this file is already in the library")
    fields = body.model_dump(exclude={"type"}, exclude_unset=True)
    fields.update(intake.type_patch(body.type))
    patch = DocumentPatch(**fields)

    doc_id = generate_id()
    destination = DOCUMENTS_DIR / f"{doc_id}{staged.suffix}"
    # Copy, then remove the staged file only once the record exists, so a failure
    # anywhere leaves the upload waiting for review rather than lost.
    await asyncio.to_thread(shutil.copy2, staged, destination)
    try:
        await asyncio.to_thread(create_document, destination, item["filename"], item["sha256"], doc_id=doc_id)
        await patch_document(doc_id, patch)
    except Exception:
        STORE.delete(doc_id)
        destination.unlink(missing_ok=True)
        raise
    enqueue_index(IndexTask(doc_id))
    await asyncio.to_thread(_intake_discard_files, item)
    return await asyncio.to_thread(read_metadata, doc_id)


@app.delete("/intake/{item_id}")
async def intake_discard(item_id: str):
    item = await asyncio.to_thread(_intake_read, item_id)
    await asyncio.to_thread(_intake_discard_files, item)
    return {"ok": True}



@app.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    def read() -> dict:
        document = read_metadata(doc_id)
        if document.get("collection_id") == NOTES_COLLECTION_ID:
            return document
        linked = sum(
            1 for doc in list_metadata()
            if doc.get("collection_id") == NOTES_COLLECTION_ID and doc.get("source_document_id") == doc_id
            and not _is_digest(doc)
        )
        return {**document, "note_count": linked}

    return await asyncio.to_thread(read)


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


@app.get("/documents/{doc_id}/extracted-page/{page}")
async def get_extracted_page(doc_id: str, page: int):
    """Text preview for Office and text documents (and an accessible PDF fallback)."""
    validate_doc_id(doc_id)
    if page < 1:
        raise HTTPException(status_code=404, detail="page out of range")
    extracted_file = EXTRACTED_DIR / f"{doc_id}.json"
    if not extracted_file.exists():
        raise HTTPException(status_code=404, detail="extracted text not found")

    def read_page() -> dict:
        extracted = read_json(extracted_file)
        for item in extracted.get("pages") or []:
            if int(item.get("page") or 0) == page:
                return {
                    "page": page,
                    "text": collapse_blank_lines(item.get("text") or ""),
                    "format": item.get("format") or "markdown",
                }
        raise HTTPException(status_code=404, detail="page out of range")

    return await asyncio.to_thread(read_page)


def _stored_file_path(doc_id: str) -> Path:
    metadata = read_metadata(doc_id)
    if metadata.get("record_type") == "standalone":
        raise HTTPException(status_code=404, detail="this book has no source file")
    stored = pdf_path(metadata)
    if not stored.exists():
        raise HTTPException(status_code=404, detail="the source document is missing")
    return stored


def _stored_paged_path(doc_id: str) -> Path:
    metadata = read_metadata(doc_id)
    if metadata.get("file_type") not in {"pdf", "epub"}:
        raise HTTPException(status_code=400, detail="page rendering is only available for PDF and EPUB files")
    return _stored_file_path(doc_id)


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
    stored = await asyncio.to_thread(_stored_file_path, doc_id)
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    if metadata.get("file_type") == "pdf":
        stored = await asyncio.to_thread(effective_pdf_path, metadata)  # the annotated copy, if any
    return FileResponse(
        stored,
        media_type=media_type_for(metadata.get("file_type") or ""),
        content_disposition_type="inline",
        filename=metadata.get("filename") or stored.name,
    )


def _thumbnail_width(requested: int) -> int:
    # Snap to a few sizes so the on-disk cache can't be filled with one file
    # per arbitrary width.
    return min((160, 320, 480), key=lambda size: abs(size - requested))


def _save_custom_cover(doc_id: str, data: bytes) -> None:
    """Validate, normalise and store a reader-chosen cover (blocking)."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 20_000_000:
                raise ValueError("unsupported or oversized image")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((600, 900))
            THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
            target = THUMBNAILS_DIR / f"{doc_id}-custom.jpg"
            temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
            image.save(temporary, format="JPEG", quality=85)
            os.replace(temporary, target)
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422, detail="choose a JPEG, PNG or WebP image under 20 megapixels") from exc


@app.put("/documents/{doc_id}/cover")
async def upload_document_cover(doc_id: str, file: UploadFile = File(...)):
    await asyncio.to_thread(read_metadata, doc_id)
    data = await file.read(8 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="cover must be smaller than 8 MB")
    await asyncio.to_thread(_save_custom_cover, doc_id, data)
    return {"saved": True}


@app.delete("/documents/{doc_id}/cover")
async def reset_document_cover(doc_id: str):
    await asyncio.to_thread(read_metadata, doc_id)
    await asyncio.to_thread((THUMBNAILS_DIR / f"{doc_id}-custom.jpg").unlink, missing_ok=True)
    return {"reset": True}


@app.get("/documents/{doc_id}/thumbnail")
async def get_document_thumbnail(doc_id: str, w: int = Query(default=320, ge=80, le=640)):
    """The document's first page as a small JPEG, cached on disk.

    The UI shows these as covers on its shelves, often dozens at once, so they
    are rendered once at a fixed set of widths and then served from the cache
    until the source file changes.
    """
    await asyncio.to_thread(read_metadata, doc_id)
    custom = THUMBNAILS_DIR / f"{doc_id}-custom.jpg"
    if custom.exists():
        return FileResponse(custom, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})
    stored = await asyncio.to_thread(_stored_paged_path, doc_id)
    width = _thumbnail_width(w)
    cached = THUMBNAILS_DIR / f"{doc_id}-{width}.jpg"

    def render() -> bytes:
        try:
            if cached.stat().st_mtime >= stored.stat().st_mtime:
                return cached.read_bytes()
        except OSError:
            pass
        with pymupdf.open(stored) as source:
            if source.page_count < 1:
                raise HTTPException(status_code=404, detail="document has no pages")
            page = source[0]
            scale = width / max(page.rect.width, 1)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            data = pixmap.tobytes("jpeg", jpg_quality=82)
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, cached)
        return data

    try:
        image = await asyncio.to_thread(render)
    except HTTPException:
        raise
    except Exception as exc:
        raise _sanitized_http_error(
            500, "could not render thumbnail", exc, log_context=f"thumbnail of {doc_id}"
        ) from exc
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/documents/{doc_id}/page/{page}")
async def get_document_page(
    doc_id: str,
    page: int,
    highlight: str = Query(default="", max_length=2_000),
    zoom: float = Query(default=2.0, ge=1.0, le=4.0),
):
    stored = await asyncio.to_thread(_stored_paged_path, doc_id)
    if page < 1:
        raise HTTPException(status_code=404, detail="page out of range")

    def render() -> bytes:
        with pymupdf.open(stored) as source:
            if page > source.page_count:
                raise HTTPException(status_code=404, detail="page out of range")
            if stored.suffix.lower() == ".epub":
                # EPUB pages are reflowed by PyMuPDF and cannot be inserted
                # into a temporary PDF for annotation. Render the page itself.
                return source[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
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
    """Queue a document for another indexing attempt. Extraction (if its
    pipeline is behind PIPELINE_VERSION) and embedding both now happen in
    the background worker -- via the process pool for extraction -- rather
    than blocking this request, so retrying several documents at once (a
    bulk reindex) actually overlaps instead of running one at a time."""
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    if metadata.get("record_type") == "standalone":
        raise HTTPException(status_code=409, detail="catalogue-only books have no source to index")
    if metadata.get("indexing_status") in {"queued", "indexing"}:
        raise HTTPException(status_code=409, detail="document is already queued for indexing")
    stale_pipeline = metadata.get("pipeline_version") != PIPELINE_VERSION
    if stale_pipeline:
        if not await asyncio.to_thread(lambda: pdf_path(metadata).exists()):
            raise HTTPException(
                status_code=404, detail="the source PDF is missing (moved, or its disk is unmounted)"
            )
    elif not (CHUNKS_DIR / f"{doc_id}.json").exists():
        raise HTTPException(status_code=409, detail="document chunks are missing; upload the document again")
    enqueue_index(IndexTask(doc_id))
    return await asyncio.to_thread(read_metadata, doc_id)


@app.post("/documents/{doc_id}/approve-ocr")
async def approve_ocr(doc_id: str):
    """The reader's go-ahead for a large scan's OCR: record it and re-queue."""
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    if metadata.get("indexing_status") in {"queued", "indexing"}:
        raise HTTPException(status_code=409, detail="document is already queued for indexing")
    if not awaiting_ocr_approval(metadata):
        raise HTTPException(status_code=409, detail="this document is not waiting for OCR approval")
    if not await asyncio.to_thread(lambda: pdf_path(metadata).exists()):
        raise HTTPException(status_code=404, detail="the source document is missing")
    await asyncio.to_thread(
        STORE.update, doc_id, {"ocr_approved": 1, "pipeline_version": 0}
    )
    enqueue_index(IndexTask(doc_id))
    return await asyncio.to_thread(read_metadata, doc_id)


@app.patch("/documents/{doc_id}")
async def patch_document(doc_id: str, patch: DocumentPatch):
    """Update the reader's catalogue metadata and personal marks."""
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    changes = {}
    sent = patch.model_fields_set
    if "rating" in sent:
        changes["rating"] = patch.rating
    if "review" in sent:
        changes["review"] = (patch.review or "").strip() or None
    for flag in ("liked", "review_spoiler", "reread"):
        if flag in sent:
            changes[flag] = None if getattr(patch, flag) is None else int(getattr(patch, flag))
    text_fields = (
        "title",
        "author",
        "subject",
        "subtitle",
        "description",
        "publisher",
        "language",
        "isbn_10",
        "isbn_13",
        "acquisition_source",
    )
    for field in text_fields:
        if field in sent:
            value = (getattr(patch, field) or "").strip()
            if field == "title" and not value:
                raise HTTPException(status_code=422, detail="title cannot be empty")
            if field == "language":
                value = value.lower()
            changes[field] = value or None
            changes["metadata_edited"] = 1
    for field in ("published_year", "page_count"):
        if field in sent:
            changes[field] = getattr(patch, field)
            changes["metadata_edited"] = 1
    if "genres" in sent:
        changes["genres_json"] = json.dumps(patch.genres or [], ensure_ascii=False) if patch.genres else None
        changes["metadata_edited"] = 1
    if "owned" in sent:
        changes["owned"] = None if patch.owned is None else int(patch.owned)
    today = datetime.now(timezone.utc).date().isoformat()
    if "read" in sent and patch.read is not None and "reading_status" not in sent:
        # Backward compatibility for the quick Read toggle used outside the
        # book editor. The richer control writes `reading_status` directly.
        changes["reading_status"] = "read" if patch.read else "to_read"
    if "reading_status" in sent:
        changes["reading_status"] = patch.reading_status
    for field in ("started_at", "finished_at"):
        if field in sent:
            changes[field] = getattr(patch, field)

    if {"current_page", "reading_progress"} & sent:
        total_pages = metadata.get("pages") or metadata.get("page_count") or 0
        if "current_page" in sent:
            if patch.current_page is not None and total_pages and patch.current_page > total_pages:
                raise HTTPException(status_code=422, detail="current page is beyond the end of this book")
            changes["current_page"] = patch.current_page
        if "reading_progress" in sent:
            changes["reading_progress"] = patch.reading_progress or 0
        elif patch.current_page is not None and total_pages:
            changes["reading_progress"] = min(100, round(patch.current_page / total_pages * 100))
        changes["last_read_at"] = utc_now()
        effective_progress = changes.get("reading_progress", metadata.get("reading_progress") or 0)
        if "reading_status" not in sent and effective_progress > 0 and (metadata.get("reading_status") or "to_read") == "to_read":
            changes["reading_status"] = "reading"

    if "reading_status" in changes:
        status = changes["reading_status"]
        if status is None:
            changes.update({"read_at": None, "started_at": None, "finished_at": None})
        elif status == "to_read":
            changes.update({"read_at": None, "started_at": None, "finished_at": None})
        elif status == "reading":
            changes["read_at"] = None
            changes["finished_at"] = None
            if "started_at" not in sent and not metadata.get("started_at"):
                changes["started_at"] = today
        elif status == "read":
            finished = changes.get("finished_at", metadata.get("finished_at")) or today
            changes["finished_at"] = finished
            changes["read_at"] = f"{finished}T00:00:00Z"
        elif status == "abandoned":
            changes["read_at"] = None
            changes["finished_at"] = None

    effective_started = changes.get("started_at", metadata.get("started_at"))
    effective_finished = changes.get("finished_at", metadata.get("finished_at"))
    if effective_started and effective_finished and effective_finished < effective_started:
        raise HTTPException(status_code=422, detail="finished date cannot be before started date")
    if changes.get("reading_status", metadata.get("reading_status")) == "read" and "finished_at" in sent:
        finished = changes.get("finished_at") or today
        changes["finished_at"] = finished
        changes["read_at"] = f"{finished}T00:00:00Z"
    if "kind" in sent and patch.kind is not None:
        changes["kind_override"] = None if patch.kind == "auto" else patch.kind
    if "form" in sent and patch.form is not None:
        changes["book_form_override"] = None if patch.form == "auto" else patch.form
    if "doc_type" in sent and patch.doc_type is not None:
        changes["doc_type_override"] = None if patch.doc_type == "auto" else patch.doc_type
    if "shelf" in sent:
        try:
            changes["shelf"] = shelves.normalize_shelf(patch.shelf)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if {"kind", "doc_type", "author", "subject"} & sent:
        # The top-level shelf follows the kind and type (Books, Papers, Reports,
        # ...); author and subject were already worked out, so nothing is
        # re-embedded.
        merged = {**metadata, **changes}
        kind = merged.get("kind_override") or merged.get("kind")
        if kind in {"book", "note"}:
            doc_type = None
        elif merged.get("doc_type_override"):
            doc_type = merged["doc_type_override"]
        elif {"kind", "doc_type"} & sent or not merged.get("doc_type"):
            doc_type = _document_type(merged, kind)
        else:
            doc_type = merged["doc_type"]
        if doc_type:
            changes["doc_type"] = doc_type
        changes["shelf_suggested"] = _shelf_suggestion(kind, doc_type, merged.get("subject"), merged.get("author"))
    if changes.keys() & {"kind_override", "page_count", "genres_json", "subject", "title", "subtitle", "author"}:
        # The form follows the kind, length, genre, subject and title.
        changes.update(_form_fields({**metadata, **changes}))
    if not changes:
        return await asyncio.to_thread(read_metadata, doc_id)
    # Not update_metadata(): a reader's mark isn't a pipeline change, so it
    # shouldn't bump updated_at (which orders the indexing queue).
    return await asyncio.to_thread(STORE.update, doc_id, changes)


class ClassifyBody(BaseModel):
    # Omitted: every working document. `apply` false only proposes.
    document_ids: Optional[List[str]] = Field(default=None, max_length=10_000)
    apply: bool = False
    # Documents the reader already shelved are left alone unless this is set.
    include_filed: bool = False


def _is_work_document(doc: dict) -> bool:
    return (
        doc.get("record_type") != "standalone"
        and doc.get("collection_id") != NOTES_COLLECTION_ID
        and (doc.get("kind_override") or doc.get("kind") or "document") not in {"book", "note"}
    )


@app.post("/documents/classify")
async def classify_documents(body: ClassifyBody):
    """Work out what each working document is and where it belongs.

    Nothing on disk moves: "moving" sets the document's virtual shelf. With
    ``apply`` false this only proposes; with it true the proposals for
    documents without a shelf of their own are stored."""

    def run() -> dict:
        wanted = set(body.document_ids) if body.document_ids is not None else None
        proposals, waiting = [], 0
        for doc in list_metadata():
            if not _is_work_document(doc) or (wanted is not None and doc["document_id"] not in wanted):
                continue
            if doc.get("indexing_status") != "indexed":
                waiting += 1
                continue
            kind = doc.get("kind_override") or doc.get("kind")
            doc_type = _document_type(doc, kind) or "other"
            proposed = _shelf_suggestion(kind, doc_type, doc.get("subject"), doc.get("author"))
            filed = bool(doc.get("shelf"))
            proposals.append({
                "document_id": doc["document_id"],
                "title": doc.get("title") or doc.get("filename"),
                "file_type": doc.get("file_type"),
                "doc_type": doc_type,
                "type_label": DOC_TYPE_LABELS[doc_type],
                "current_shelf": shelves.effective_shelf(doc),
                "proposed_shelf": proposed,
                "filed": filed,
                "changes": (doc.get("doc_type") != doc_type) or (not filed and doc.get("shelf_suggested") != proposed)
                or (filed and body.include_filed and doc.get("shelf") != proposed),
            })
        applied = 0
        if body.apply:
            for item in proposals:
                if item["filed"] and not body.include_filed:
                    continue
                updates = {"doc_type": item["doc_type"], "shelf": item["proposed_shelf"], "shelf_suggested": item["proposed_shelf"]}
                try:
                    STORE.update(item["document_id"], updates)
                except KeyError:
                    continue
                applied += 1
        return {"proposals": proposals, "applied": applied, "waiting": waiting, "types": list(DOC_TYPES)}

    return await asyncio.to_thread(run)


def _note_title(text: str) -> str:
    first = next((line.strip(" #*-\t") for line in text.splitlines() if line.strip(" #*-\t")), "Note")
    return first if len(first) <= 80 else first[:79].rstrip() + "…"


def _note_index_text(text: str, quote: Optional[str]) -> str:
    return text + ("\n\n<!-- source-passage -->\n> " + quote.replace("\n", "\n> ") if quote else "") + "\n"


def _note_response(doc: dict) -> dict:
    try:
        text = pdf_path(doc).read_text(encoding="utf-8")
    except OSError:
        text = ""
    return {
        "document_id": doc["document_id"],
        "title": doc.get("title"),
        "text": doc.get("note_text") if doc.get("note_text") is not None else text,
        "source_document_id": doc.get("source_document_id"),
        "source_page": doc.get("source_page"),
        "source_quote": doc.get("source_quote"),
        "created_at": doc.get("uploaded_at"),
        "updated_at": doc.get("updated_at"),
        "indexing_status": doc.get("indexing_status"),
    }


def _create_note_record(
    text: str,
    source_document_id: Optional[str],
    source_page: Optional[int],
    source_quote: Optional[str],
    created_at: Optional[str] = None,
) -> dict:
    """Store a note as an indexed Markdown document (blocking)."""
    doc_id = generate_id()
    stored = DOCUMENTS_DIR / f"{doc_id}.md"
    stored.write_text(_note_index_text(text, source_quote), encoding="utf-8")
    stamp = utc_now()[:16].replace(":", "")
    create_document(
        stored,
        f"note-{stamp}.md",
        hashlib.sha256(stored.read_bytes()).hexdigest(),
        doc_id=doc_id,
        collection_id=NOTES_COLLECTION_ID,
    )
    changes = {"title": _note_title(text), "kind_override": "note",
               "source_document_id": source_document_id,
               "source_page": source_page, "source_quote": source_quote, "note_text": text}
    if created_at:
        changes["uploaded_at"] = created_at
    STORE.update(doc_id, changes)
    enqueue_index(IndexTask(doc_id))
    return _note_response(read_metadata(doc_id))


@app.post("/notes", status_code=201)
async def create_note(body: NoteBody):
    """Jot a note. It is saved as a Markdown file in the app's own data (never
    in the read-only library), indexed like any document, and searchable on
    its own through the built-in "Notes" library."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="a note needs some text")

    if body.source_document_id:
        source = await asyncio.to_thread(read_metadata, body.source_document_id)
        if source.get("collection_id") == NOTES_COLLECTION_ID:
            raise HTTPException(status_code=422, detail="link notes to a source document, not another note")
        if body.source_page and body.source_page > (source.get("pages") or 0):
            raise HTTPException(status_code=422, detail="source page out of range")
    elif body.source_page or body.source_quote:
        raise HTTPException(status_code=422, detail="a source document is required")

    return await asyncio.to_thread(
        _create_note_record, text, body.source_document_id, body.source_page, body.source_quote
    )


@app.get("/notes")
async def list_notes(source_document_id: Optional[str] = None):
    """Every note, newest first, with its text."""

    def notes() -> dict:
        docs = [doc for doc in list_metadata() if doc.get("collection_id") == NOTES_COLLECTION_ID and not _is_digest(doc)]
        if source_document_id:
            docs = [doc for doc in docs if doc.get("source_document_id") == source_document_id]
        docs.sort(key=lambda doc: doc.get("uploaded_at") or "", reverse=True)
        return {"notes": [_note_response(doc) for doc in docs]}

    return await asyncio.to_thread(notes)


@app.put("/notes/{doc_id}")
async def update_note(doc_id: str, body: NoteBody):
    """Replace a note's text and re-index it. Delete with DELETE /documents/{id}."""
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    if metadata.get("collection_id") != NOTES_COLLECTION_ID:
        raise HTTPException(status_code=404, detail="note not found")
    if metadata.get("indexing_status") == "indexing":
        # The running extraction would finish with the old text and mark the
        # note indexed, silently dropping this edit from the index.
        raise HTTPException(status_code=409, detail="this note is being indexed; try again in a moment")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="a note needs some text")

    def update() -> dict:
        stored = pdf_path(metadata)
        stored.write_text(_note_index_text(text, metadata.get("source_quote")), encoding="utf-8")
        STORE.update(
            doc_id,
            {
                "title": _note_title(text),
                "note_text": text,
                "content_sha256": hashlib.sha256(stored.read_bytes()).hexdigest(),
                "pipeline_version": -1,  # re-extract from the new text
            },
        )
        enqueue_index(IndexTask(doc_id))
        return _note_response(read_metadata(doc_id))

    return await asyncio.to_thread(update)


def _vocabulary_record(body: VocabularyBody, *, blank_definition_ok: bool = False) -> dict:
    word = " ".join(body.word.split())
    definition = body.definition.strip()
    if not word or not (definition or blank_definition_ok):
        raise HTTPException(status_code=422, detail="a word and definition are required")
    if body.document_id:
        source = read_metadata(body.document_id)
        if source.get("collection_id") == NOTES_COLLECTION_ID:
            raise HTTPException(status_code=422, detail="link vocabulary to a source document, not a note")
        total_pages = source.get("pages") or source.get("page_count") or 0
        if body.source_page and total_pages and body.source_page > total_pages:
            raise HTTPException(status_code=422, detail="source page out of range")
    elif body.source_page or body.source_quote:
        raise HTTPException(status_code=422, detail="a source document is required")
    return {
        "word": word,
        "normalized_word": word.casefold(),
        "definition": definition,
        "part_of_speech": (body.part_of_speech or "").strip() or None,
        "example": (body.example or "").strip() or None,
        "document_id": body.document_id,
        "source_page": body.source_page,
        "source_quote": (body.source_quote or "").strip() or None,
        "definition_source": "manual",
    }


@app.get("/vocabulary")
async def list_vocabulary(document_id: Optional[str] = None, q: Optional[str] = Query(default=None, max_length=120)):
    def listed() -> dict:
        words = STORE.list_vocabulary(document_id)
        needle = (q or "").strip().casefold()
        if needle:
            words = [item for item in words if needle in item["normalized_word"] or needle in item["definition"].casefold()]
        return {"words": words}
    return await asyncio.to_thread(listed)


@app.post("/vocabulary", status_code=201)
async def create_vocabulary(body: VocabularyBody):
    record = await asyncio.to_thread(_vocabulary_record, body)
    now = utc_now()
    return await asyncio.to_thread(
        STORE.create_vocabulary,
        {"word_id": generate_id(), **record, "created_at": now, "updated_at": now},
    )


@app.put("/vocabulary/{word_id}")
async def update_vocabulary(word_id: str, body: VocabularyBody):
    # A word collected from an underline starts without a definition; saving
    # an edit to it (adding the definition later) must not demand one.
    changes = await asyncio.to_thread(partial(_vocabulary_record, body, blank_definition_ok=True))
    changes["definition_source"] = "manual"
    changes["updated_at"] = utc_now()
    try:
        return await asyncio.to_thread(STORE.update_vocabulary, word_id, changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="vocabulary entry not found") from exc


@app.delete("/vocabulary/{word_id}")
async def delete_vocabulary(word_id: str):
    try:
        await asyncio.to_thread(STORE.delete_vocabulary, word_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="vocabulary entry not found") from exc
    return {"ok": True}


# ---------------------------------------------------------- annotations --
# The app has no annotation editor. A PDF opens in the reader's own viewer (the
# browser's or the OS's), and whatever they mark there is read back out of the
# file: highlights, underlines, strike-outs, typed text, notes and pen strokes.
# The originals are never modified. A copy saved from a viewer is kept beside
# the original as an "annotated copy", and that copy is what opens and is read.

ANNOTATION_DIGEST_KIND = "annotations"
ANNOTATED_DIR = DATA_DIR / "annotated"


def _is_digest(doc: dict) -> bool:
    """The per-book Markdown digest of a reader's marks. It lives in the Notes
    library so search finds it, but it is generated, not a note the reader wrote."""
    return doc.get("collection_id") == NOTES_COLLECTION_ID and doc.get("kind_override") == ANNOTATION_DIGEST_KIND


def _digest_doc(book_id: str) -> Optional[dict]:
    return next(
        (doc for doc in list_metadata() if _is_digest(doc) and doc.get("source_document_id") == book_id), None
    )


def annotated_copy_path(doc_id: str) -> Path:
    return ANNOTATED_DIR / f"{doc_id}.pdf"


def effective_pdf_path(metadata: dict) -> Path:
    """The file to open and to read marks from: the annotated copy when there is
    one that is not older than the original, otherwise the original itself."""
    original = pdf_path(metadata)
    copy = annotated_copy_path(metadata["document_id"])
    try:
        if copy.exists() and (not original.exists() or copy.stat().st_mtime_ns >= original.stat().st_mtime_ns):
            return copy
    except OSError:
        pass
    return original


def _apply_digest(book_id: str, items: List[dict], attempt: int = 0) -> None:
    """Make the book's searchable digest say what ``items`` say, creating,
    updating or removing the Markdown document as needed."""
    try:
        book = read_metadata(book_id)
        digest = _digest_doc(book_id)
        searchable = [a for a in items if (a.get("text") or "").strip() or (a.get("comment") or "").strip()]
        if not searchable:
            if digest:
                _delete_document_blocking(digest)
            return
        title = book.get("title") or book.get("filename") or "Untitled"
        text = reader_annotations.digest_text(title, items)
        if digest is None:
            doc_id = generate_id()
            stored = DOCUMENTS_DIR / f"{doc_id}.md"
            stored.write_text(text, encoding="utf-8")
            create_document(
                stored, f"annotations-{doc_id[:8]}.md", hashlib.sha256(stored.read_bytes()).hexdigest(),
                doc_id=doc_id, collection_id=NOTES_COLLECTION_ID,
            )
            STORE.update(doc_id, {"title": f"Annotations: {title}", "kind_override": ANNOTATION_DIGEST_KIND,
                                  "source_document_id": book_id})
            enqueue_index(IndexTask(doc_id))
            return
        if digest.get("indexing_status") == "indexing" and attempt < 24:
            # The running extraction would finish on the old text and mark it
            # indexed; try again once it is done.
            timer = threading.Timer(5.0, _apply_digest, args=(book_id, items, attempt + 1))
            timer.daemon = True
            timer.start()
            return
        stored = pdf_path(digest)
        if stored.exists() and stored.read_text(encoding="utf-8") == text:
            return
        stored.write_text(text, encoding="utf-8")
        STORE.update(digest["document_id"], {
            "title": f"Annotations: {title}",
            "content_sha256": hashlib.sha256(stored.read_bytes()).hexdigest(),
            "pipeline_version": -1,
        })
        enqueue_index(IndexTask(digest["document_id"]))
    except Exception:
        logger.exception("could not refresh the annotation digest for %s", book_id)


def _file_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _refresh_pdf_marks(book: dict, *, force: bool = False) -> Optional[List[dict]]:
    """Re-read a PDF's marks and bring the searchable digest up to date.

    Cheap when nothing changed: a PDF is only opened when the size or modified
    time of the original, or of its annotated copy, differs from the last scan.
    When the original itself changed, a fingerprint of its text (which ignores
    every annotation) says whether the book's content changed or the reader only
    added marks. Only a real content change re-indexes the book. Returns the
    marks found, or None when there was nothing to do.
    """
    book_id = book["document_id"]
    if book.get("file_type") != "pdf" or book.get("record_type") == "standalone" \
            or book.get("collection_id") == NOTES_COLLECTION_ID:
        return None
    original = pdf_path(book)
    original_sig = _file_signature(original)
    if not original_sig:
        return None
    signature = f"{original_sig}|{_file_signature(annotated_copy_path(book_id))}"
    if not force and book.get("file_sig") == signature:
        return None
    previous_original = (book.get("file_sig") or "").split("|")[0]
    changes = {"file_sig": signature}
    reindex = False
    if previous_original != original_sig or not book.get("text_sig"):
        text_sig = reader_annotations.text_signature(original)
        if text_sig:
            changed_content = bool(book.get("text_sig")) and book["text_sig"] != text_sig
            if changed_content or (previous_original and previous_original != original_sig):
                changes["content_sha256"] = hash_file(original)
            if changed_content:
                reindex = True
                changes["pipeline_version"] = -1
            changes["text_sig"] = text_sig
    STORE.update(book_id, changes)
    if reindex:
        logger.info("%s changed on disk; re-indexing it", book.get("filename") or book_id)
        enqueue_index(IndexTask(book_id))
    items = reader_annotations.scan_pdf_annotations(effective_pdf_path({**book, **changes}))
    _apply_digest(book_id, items)
    return items


def _refresh_all_pdf_marks() -> int:
    """Look at every indexed PDF for new or changed marks. Run by the
    background scan; a library of unchanged files costs one stat per book."""
    refreshed = 0
    for doc in list_metadata():
        if doc.get("file_type") != "pdf" or doc.get("indexing_status") != "indexed":
            continue
        try:
            if _refresh_pdf_marks(doc) is not None:
                refreshed += 1
        except Exception:
            logger.exception("could not read the marks in %s", doc.get("filename") or doc.get("document_id"))
    if refreshed:
        logger.info("Read the marks in %d PDF(s)", refreshed)
    return refreshed


def _annotatable_pdf(doc_id: str) -> dict:
    metadata = read_metadata(doc_id)
    if metadata.get("file_type") != "pdf" or metadata.get("collection_id") == NOTES_COLLECTION_ID:
        raise HTTPException(status_code=400, detail="marks are only read from PDF books")
    return metadata


def _marks_response(metadata: dict, items: List[dict]) -> dict:
    using_copy = effective_pdf_path(metadata) == annotated_copy_path(metadata["document_id"])
    return {"annotations": items, "source": "annotated-copy" if using_copy else "original"}


@app.get("/documents/{doc_id}/annotations")
async def list_annotations(doc_id: str):
    """The marks currently in this book's PDF, read from the file."""
    metadata = await asyncio.to_thread(_annotatable_pdf, doc_id)
    path = await asyncio.to_thread(effective_pdf_path, metadata)
    if not await asyncio.to_thread(path.exists):
        raise HTTPException(status_code=404, detail="the source PDF is missing")
    items = await asyncio.to_thread(reader_annotations.scan_pdf_annotations, path)
    return _marks_response(metadata, items)


@app.post("/documents/{doc_id}/annotations/rescan")
async def rescan_annotations(doc_id: str):
    """Read the PDF again now and refresh the searchable digest, instead of
    waiting for the background scan."""
    metadata = await asyncio.to_thread(_annotatable_pdf, doc_id)
    items = await asyncio.to_thread(_refresh_pdf_marks, metadata, force=True)
    if items is None:
        raise HTTPException(status_code=404, detail="the source PDF is missing")
    return _marks_response(metadata, items)


def _check_annotated_pdf(path: Path, metadata: dict) -> None:
    """Refuse a file that cannot be this book's annotated copy: not a readable
    PDF, password-protected, or with a different number of pages."""
    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise HTTPException(status_code=422, detail="this PDF is password-protected")
            expected = metadata.get("pages") or 0
            if expected and document.page_count != expected:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"this file has {document.page_count} pages but the book has {expected}. "
                        "If it is a different edition, add it to the library as a new book."
                    ),
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="this does not look like a readable PDF") from exc


@app.post("/documents/{doc_id}/annotated-copy")
async def upload_annotated_copy(doc_id: str, file: UploadFile = File(...)):
    """Attach a copy of this book's PDF that was marked up in a viewer and saved.
    It is kept beside the original (never replacing it), opens in its place, and
    its marks are read and indexed."""
    metadata = await asyncio.to_thread(_annotatable_pdf, doc_id)
    ANNOTATED_DIR.mkdir(parents=True, exist_ok=True)
    temporary = ANNOTATED_DIR / f".{doc_id}.{secrets.token_hex(4)}.uploading"
    total = 0
    try:
        with temporary.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
                    )
                destination.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="uploaded file is empty")

        await asyncio.to_thread(_check_annotated_pdf, temporary, metadata)
        os.replace(temporary, annotated_copy_path(doc_id))
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
    items = await asyncio.to_thread(_refresh_pdf_marks, metadata, force=True)
    return _marks_response(metadata, items or [])


@app.delete("/documents/{doc_id}/annotated-copy")
async def remove_annotated_copy(doc_id: str):
    """Go back to the original PDF."""
    metadata = await asyncio.to_thread(_annotatable_pdf, doc_id)
    await asyncio.to_thread(lambda: annotated_copy_path(doc_id).unlink(missing_ok=True))
    items = await asyncio.to_thread(_refresh_pdf_marks, metadata, force=True)
    return _marks_response(metadata, items or [])


@app.post("/documents/{doc_id}/underlined-words")
async def scan_underlined_words(doc_id: str):
    """Find the single words underlined in a book, for the vocabulary notebook
    to collect. Nothing is saved: the reader reviews the list first."""
    metadata = await asyncio.to_thread(_annotatable_pdf, doc_id)
    stored = await asyncio.to_thread(effective_pdf_path, metadata)
    if not await asyncio.to_thread(stored.exists):
        raise HTTPException(status_code=404, detail="the source PDF is missing")

    def scan() -> dict:
        found = reader_annotations.find_underlined_words(stored)
        saved = {entry["normalized_word"] for entry in STORE.list_vocabulary(doc_id)}
        return {
            "document_id": doc_id,
            "title": metadata.get("title") or metadata.get("filename"),
            "words": [{**item, "saved": item["word"].casefold() in saved} for item in found],
        }

    try:
        return await asyncio.to_thread(scan)
    except HTTPException:
        raise
    except Exception as exc:
        raise _sanitized_http_error(500, "could not scan this book", exc, log_context=doc_id) from exc


@app.post("/vocabulary/harvest", status_code=201)
async def harvest_vocabulary(body: HarvestBody):
    """Save words found by the underline scan, each linked to its page and the
    sentence it appeared in. Definitions can be added afterwards."""

    def harvest() -> dict:
        source = read_metadata(body.document_id)
        if source.get("collection_id") == NOTES_COLLECTION_ID:
            raise HTTPException(status_code=422, detail="link vocabulary to a source document, not a note")
        have = {entry["normalized_word"] for entry in STORE.list_vocabulary(body.document_id)}
        created = skipped = 0
        for item in body.words:
            word = " ".join(item.word.split())
            if not word or word.casefold() in have:
                skipped += 1
                continue
            now = utc_now()
            STORE.create_vocabulary({
                "word_id": generate_id(), "word": word, "normalized_word": word.casefold(),
                "definition": (item.definition or "").strip(), "part_of_speech": None, "example": None,
                "document_id": body.document_id, "source_page": item.page,
                "source_quote": (item.quote or "").strip() or None,
                "definition_source": "underline", "created_at": now, "updated_at": now,
            })
            have.add(word.casefold())
            created += 1
        return {"created": created, "skipped": skipped}

    return await asyncio.to_thread(harvest)


# ------------------------------------------------------------ catalogue --

# Open Library cover ids are numbers; Google Books covers are "g:<volume id>".
COVER_ID_PATTERN = r"^(?:\d{1,12}|g:[A-Za-z0-9_-]{6,20})$"

# What a catalogue match may fill in. Reviews, ratings, notes and reading
# state are the reader's own and can never be written from a provider.
CATALOGUE_FIELDS = frozenset({
    "title", "subtitle", "author", "publisher", "published_year", "page_count",
    "language", "genres", "isbn_10", "isbn_13", "subject", "description",
})


def _provider_label(provider_id: Optional[str]) -> str:
    return "Google Books" if (provider_id or "").startswith("gb:") else "Open Library"


def _catalogue_call(function, *args):
    try:
        return function(*args)
    except catalogue.CatalogueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/catalogue/search")
async def catalogue_search(q: str = Query(min_length=2, max_length=200), limit: int = Query(default=8, ge=1, le=15)):
    return {"results": await asyncio.to_thread(_catalogue_call, catalogue.search, q, limit)}


@app.get("/catalogue/isbn/{isbn}")
async def catalogue_isbn(isbn: str):
    digits = re.sub(r"[-\s]", "", isbn)
    try:
        normalized = _normalized_isbn(digits, 13 if len(digits) == 13 else 10)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if normalized is None:
        raise HTTPException(status_code=422, detail="enter an ISBN")
    return {"results": await asyncio.to_thread(_catalogue_call, catalogue.by_isbn, normalized)}


def _normalised_cover_jpeg(data: bytes) -> bytes:
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 20_000_000:
                raise ValueError("unsupported or oversized image")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((600, 900))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=502, detail="the catalogue sent an unusable cover") from exc


@app.get("/catalogue/cover")
async def catalogue_cover(id: str = Query(pattern=COVER_ID_PATTERN)):
    """A catalogue cover, fetched and re-encoded here so the browser never
    talks to a third-party image host."""
    def fetch() -> bytes:
        return _normalised_cover_jpeg(_catalogue_call(catalogue.fetch_cover, id))
    return Response(content=await asyncio.to_thread(fetch), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


# --- Matching books to the catalogue without typing --------------------------
# Opt-in: with "auto match" on, a newly indexed book's cleaned-up file name is
# sent to the catalogue and a confident match fills in only what is missing.
# Off by default because it sends file names to a third party unasked.

_AUTO_MATCH_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="auto-match")
_AUTO_MATCH_FIELDS = ("subtitle", "publisher", "published_year", "page_count", "language", "genres", "isbn_10", "isbn_13", "description")


def _auto_match_enabled() -> bool:
    return bool(_load_settings().get("catalogue_auto_match")) and not catalogue._offline()


def _catalogue_query_for(doc: dict) -> str:
    """What to look a book up by: the title and author the reader settled on,
    else its file name."""
    if doc.get("metadata_edited") and doc.get("title"):
        return " ".join(part for part in (doc.get("title"), doc.get("author")) if part)
    return Path(doc.get("filename") or doc.get("title") or "").stem


def _auto_match_eligible(doc: dict) -> bool:
    return bool(
        doc.get("record_type") != "standalone"
        and doc.get("collection_id") != NOTES_COLLECTION_ID
        and doc.get("file_type") in {"pdf", "epub"}
        and (doc.get("kind_override") or doc.get("kind")) == "book"
        and not doc.get("metadata_source")
        and not doc.get("isbn_13") and not doc.get("isbn_10")
        and doc["document_id"] not in (_load_settings().get("auto_match_tried") or [])
    )


def _auto_match_book(doc_id: str) -> bool:
    """Look one book up and apply a confident match. Never replaces anything
    the reader entered; returns whether details were applied."""
    try:
        doc = read_metadata(doc_id)
        if not _auto_match_eligible(doc):
            return False
        tried = (_load_settings().get("auto_match_tried") or [])[-4000:]
        _save_settings({"auto_match_tried": [*tried, doc_id]})
        match = catalogue.best_match(_catalogue_query_for(doc))
        if not match:
            return False
        fields = {key: match[key] for key in _AUTO_MATCH_FIELDS if match.get(key) not in (None, "", []) and not doc.get(
            "genres_json" if key == "genres" else key)}
        if not doc.get("metadata_edited"):
            # The title and author were only guessed from the file name.
            for key in ("title", "author"):
                if match.get(key):
                    fields[key] = match[key]
        if fields.get("title"):
            # Drop a subtitle or translator credit ("A Happy Death. Translated from the French by ...").
            fields["title"] = catalogue._main_title(fields["title"]).strip() or fields["title"]
        patch = DocumentPatch(**fields)
        if patch.model_fields_set:
            asyncio.run(patch_document(doc_id, patch))
        STORE.update(doc_id, {"metadata_source": _provider_label(match["id"]), "metadata_source_id": match["id"]})
        if match.get("cover_id") and not (THUMBNAILS_DIR / f"{doc_id}-custom.jpg").exists():
            try:
                _save_custom_cover(doc_id, _normalised_cover_jpeg(catalogue.fetch_cover(match["cover_id"])))
            except Exception:
                logger.info("auto-match found no usable cover for %s", doc_id)
        return True
    except Exception:
        logger.exception("auto-match failed for %s", doc_id)
        return False


def _maybe_auto_match(doc_id: str) -> None:
    if _auto_match_enabled():
        _AUTO_MATCH_POOL.submit(_auto_match_book, doc_id)


class CatalogueSettings(BaseModel):
    auto_match: bool


@app.get("/catalogue/settings")
async def get_catalogue_settings():
    return {"auto_match": bool(_load_settings().get("catalogue_auto_match")), "providers": catalogue._providers(),
            "google_key": bool(os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()), "offline": catalogue._offline()}


@app.put("/catalogue/settings")
async def put_catalogue_settings(body: CatalogueSettings):
    await asyncio.to_thread(_save_settings, {"catalogue_auto_match": body.auto_match})
    return await get_catalogue_settings()


@app.post("/catalogue/auto-match")
async def auto_match_library():
    """Queue every book the app has not matched yet. Runs in the background."""
    if catalogue._offline():
        raise HTTPException(status_code=409, detail="Catalogue lookup is disabled because OFFLINE mode is on.")
    docs = [doc for doc in await asyncio.to_thread(list_metadata) if doc.get("indexing_status") == "indexed" and _auto_match_eligible(doc)]
    for doc in docs:
        _AUTO_MATCH_POOL.submit(_auto_match_book, doc["document_id"])
    return {"queued": len(docs)}


@app.get("/documents/{doc_id}/catalogue-query")
async def catalogue_query_for_document(doc_id: str):
    """The cleaned-up search text for a book (no network): its title and author
    if the reader set them, else its file name without the download-site noise."""
    doc = await asyncio.to_thread(read_metadata, doc_id)
    return {"query": catalogue.clean_query(_catalogue_query_for(doc))}


class CatalogueApply(BaseModel):
    """The fields the reader ticked, and optionally the cover to keep."""
    fields: DocumentPatch = Field(default_factory=DocumentPatch)
    cover_id: Optional[str] = Field(default=None, pattern=COVER_ID_PATTERN)
    provider_id: Optional[str] = Field(default=None, max_length=64)


@app.post("/documents/{doc_id}/metadata-apply")
async def apply_catalogue_metadata(doc_id: str, body: CatalogueApply):
    """Apply only what the reader selected from a catalogue match."""
    await asyncio.to_thread(read_metadata, doc_id)
    extra = body.fields.model_fields_set - CATALOGUE_FIELDS
    if extra:
        raise HTTPException(status_code=422, detail=f"catalogue matches cannot change: {', '.join(sorted(extra))}")
    cover = None
    if body.cover_id:
        cover = await asyncio.to_thread(
            lambda: _normalised_cover_jpeg(_catalogue_call(catalogue.fetch_cover, body.cover_id))
        )
    updated = await patch_document(doc_id, body.fields) if body.fields.model_fields_set else await asyncio.to_thread(read_metadata, doc_id)
    if body.fields.model_fields_set:
        updated = await asyncio.to_thread(
            STORE.update, doc_id, {"metadata_source": _provider_label(body.provider_id), "metadata_source_id": body.provider_id}
        )
    if cover:
        await asyncio.to_thread(_save_custom_cover, doc_id, cover)
    return updated


# ------------------------------------------------------------ AI check --
# "Check with AI": the rules, the optional online catalogue and the reader's own
# Ollama look at ONE book (on its page) or one upload waiting for review, and say what
# they would change. See ai_sort.py for what it will and will not believe. It only
# proposes: applying is the reader's click, and a change applied to a library book is
# logged with its before and after values so it can be undone.

AI_SORT_SOURCE = "AI sort"
# Every field a check can change, so one snapshot can restore it.
_AI_SORT_TOUCHED = (
    "title", "subtitle", "author", "subject", "description", "publisher", "language", "isbn_10", "isbn_13",
    "published_year", "page_count", "genres_json", "metadata_edited", "metadata_source", "metadata_source_id",
    "shelf_suggested", "book_form", "kind_override", "doc_type", "ai_json", "book_form_override", "doc_type_override",
)
_AI_SORT_LOCK = threading.RLock()


def _ai_sort_blank() -> dict:
    return {
        "state": "idle", "run_id": None, "started_at": None, "finished_at": None, "options": {}, "model": None,
        "total": 0, "done": 0, "current": None, "applied": [], "suggestions": [], "skipped": {}, "unchanged": 0,
        "notes": [], "stats": {"model_calls": 0, "model_failures": 0, "lookups": 0, "lookup_failures": 0},
    }


def _ai_sort_skip_reason(doc: dict) -> Optional[str]:
    """Why this record cannot be checked, or None when it can."""
    if doc.get("record_type") == "standalone":
        return "has no file"
    if doc.get("collection_id") == NOTES_COLLECTION_ID or (doc.get("kind_override") or doc.get("kind")) == "note":
        return "a note"
    if doc.get("file_type") not in {"pdf", "epub"}:
        return "not a PDF or EPUB"
    kind = _library_kind(doc)
    # A weak verdict (only the page count decided it) is worth a second opinion, but not
    # a kind the reader chose themselves.
    weak = bool(_json_of(doc.get("text_stats")).get("weak")) and not doc.get("kind_override")
    if kind not in {"book", "pending"} and not weak:
        return "a paper or document"
    return None


def _ai_sort_texts(doc_id: str) -> List[str]:
    try:
        pages = read_json(EXTRACTED_DIR / f"{doc_id}.json").get("pages") or []
    except Exception:
        return []
    return [page.get("text") or "" for page in pages]


def _ai_sort_change(field: str, before, after, source: str, reason: str) -> dict:
    return {"field": field, "before": before, "after": after, "source": source, "reason": reason}


def _apply_sort(doc_id: str, *, quiet: Optional[dict] = None, patch: Optional[dict] = None, source: Optional[str] = None,
                source_id: Optional[str] = None, ai: Optional[dict] = None, cover: Optional[bytes] = None) -> dict:
    """Write one book's changes and return what is needed to undo them.

    `quiet` are fixes the rules are sure of, written as they are (like the
    startup backfill); `patch` goes through the same validation as the book
    editor, which also marks the details as settled; `ai` is a model verdict to
    remember; `cover` a catalogue cover for a book that has none."""
    doc = read_metadata(doc_id)
    before = {key: doc.get(key) for key in _AI_SORT_TOUCHED}
    if quiet:
        STORE.update(doc_id, quiet)
    if patch:
        asyncio.run(patch_document(doc_id, DocumentPatch(**patch)))
    extra: dict = {}
    if source:
        extra.update(metadata_source=source, metadata_source_id=source_id)
    if ai is not None:
        extra["ai_json"] = json.dumps({**_json_of(doc.get("ai_json")), **ai})
    if extra:
        STORE.update(doc_id, extra)
    after_doc = read_metadata(doc_id)
    kind = after_doc.get("kind_override") or after_doc.get("kind")
    settled = dict(_form_fields(after_doc))
    if {"author", "subject"} & {key for key in _AI_SORT_TOUCHED if after_doc.get(key) != before.get(key)}:
        doc_type = None if kind == "book" else after_doc.get("doc_type")
        settled["shelf_suggested"] = _shelf_suggestion(kind, doc_type, after_doc.get("subject"), after_doc.get("author"))
    STORE.update(doc_id, settled)
    cover_added = False
    if cover and not (THUMBNAILS_DIR / f"{doc_id}-custom.jpg").exists():
        try:
            _save_custom_cover(doc_id, cover)
            cover_added = True
        except Exception:
            logger.info("AI sort could not store a cover for %s", doc_id)
    final = read_metadata(doc_id)
    changed = [key for key in _AI_SORT_TOUCHED if final.get(key) != before.get(key)]
    return {
        "before": {key: before.get(key) for key in changed},
        "after": {key: final.get(key) for key in changed},
        "cover_added": cover_added,
    }


def _ai_sort_match_fields(doc: dict, match: dict) -> dict:
    """The details a confirmed catalogue match may fill in: only what is empty."""
    return {
        key: match[key]
        for key in _AUTO_MATCH_FIELDS
        if match.get(key) not in (None, "", []) and not doc.get("genres_json" if key == "genres" else key)
    }


def _ai_sort_spelling(ctx: dict, name: str, *, remember: bool) -> str:
    """The way the library already spells this person, else the name as given
    ("John Ronald Reuel Tolkien" when the shelf says "J. R. R. Tolkien"). A name
    that is applied is remembered, so the books after it spell it the same way."""
    name = shelves.normalize_author(name) or name
    chosen = shelves.spelling_in_use(name, ctx["known"]) or name
    if remember:
        ctx["known"].append(chosen)
    return chosen


def _ai_sort_gather(doc: dict, ctx: dict, report: dict, *, texts: Optional[List[str]] = None,
                    embedded: Optional[str] = None, quiet: Optional[dict] = None) -> dict:
    """What the rules, the optional online catalogue and the local model say about one
    book, without writing anything: {"quiet", "work", "plan", "checked", "title"}.
    A library book's text and file properties are read from the library; an upload that
    is not a record yet passes its own `texts`, `embedded` author and `quiet` ({})."""
    doc_id = doc["document_id"]
    if texts is None:
        texts = _ai_sort_texts(doc_id)
        try:
            embedded = document_author(pdf_path(doc))
        except Exception:
            embedded = None
    # 1. What the rules can settle on their own: spelling, a surname from the library or the title page.
    if quiet is None:
        quiet = {key: value for key, value in _filename_detail_changes(doc, ctx["known"], ctx["spellings"]).items()}
    work = {**doc, **quiet}
    author, title = work.get("author"), work.get("title") or ""
    stats = _json_of(doc.get("text_stats"))
    pages = doc_kind.length_pages(doc.get("page_count"), stats.get("words") or 0, doc.get("pages"))
    needs_author = not author or " " not in author.strip()
    in_doubt = doc_kind.fiction_in_doubt(doc.get("prose_score"), stats.get("past")) and (doc.get("kind_override") or doc.get("kind")) in {"book", None}
    form_unknown = (doc.get("kind_override") or doc.get("kind")) == "book" and not (doc.get("book_form_override") or doc.get("book_form"))
    weak = bool(stats.get("weak"))
    needs = needs_author or in_doubt or form_unknown or weak or ctx["online"]

    plan = ai_sort.Plan()
    checked = False
    if needs and (ctx["online"] or ctx["model"]):
        candidates: List[dict] = []
        if ctx["online"]:
            try:
                report["stats"]["lookups"] += 1
                found = catalogue.search(catalogue.clean_query(f"{title} {author or ''}"), 5)
                candidates = [dict(item) for item in found if item.get("score", 0) >= 0.5][:4]
                ctx["lookup_errors"] = 0
                checked = True
            except catalogue.CatalogueError as exc:
                report["stats"]["lookup_failures"] += 1
                ctx["lookup_errors"] += 1
                if ctx["lookup_errors"] >= 3:
                    ctx["online"] = False
                    report["notes"].append(f"The online catalogue stopped answering ({exc}); the rest was checked without it.")
        ev = ai_sort.Evidence(
            filename=doc.get("filename") or "", title=title, author=author, embedded_author=embedded,
            opening=ai_sort.clip_opening(texts), excerpt=ai_sort.clip_excerpt(texts), pages=pages,
            words=stats.get("words") or 0, kind=doc.get("kind_override") or doc.get("kind"), form=doc.get("book_form"),
            weak=weak, fiction_in_doubt=in_doubt,
            title_untouched=not doc.get("metadata_edited"), candidates=candidates,
        )
        answer = None
        if ctx["model"]:
            try:
                report["stats"]["model_calls"] += 1
                answer = ai_sort.parse_answer(ai_sort.ask_local_model(ctx["model"], ai_sort.build_prompt(ev)), len(candidates))
                ctx["model_errors"] = 0
                checked = True
            except ai_sort.AiError as exc:
                report["stats"]["model_failures"] += 1
                ctx["model_errors"] += 1
                logger.info("AI sort: %s", exc)
                if ctx["model_errors"] >= 3:
                    ctx["model"] = None
                    report["notes"].append(f"The local model stopped answering ({exc}); the rest was checked without it.")
        plan = ai_sort.plan_book(ev, answer, use_catalogue=bool(candidates))
        plan.notes = [f"{title}: {note}" for note in plan.notes]
    return {"quiet": quiet, "work": work, "plan": plan, "checked": checked, "title": title}


@app.get("/ai-check/models")
async def ai_check_models():
    """What a check can use: the reader's own Ollama models, and whether the online
    catalogue is allowed (OFFLINE mode forbids it)."""
    models = await asyncio.to_thread(ai_sort.list_local_models)
    return {
        "models": models, "default": ai_sort.pick_model(None, models), "ollama_configured": bool(ai_sort.ollama_url()),
        "online_allowed": not catalogue._offline(), "providers": catalogue._providers(),
    }


class AiSortIds(BaseModel):
    ids: List[str] = Field(max_length=500)


def _ai_sort_apply_suggestions(ids: List[str], report: dict, save) -> dict:
    """Apply the suggestions with these ids from one book's kept result, then `save` it."""
    done, failed = 0, []
    for suggestion in list(report["suggestions"]):
        if suggestion["id"] not in ids:
            continue
        try:
            undo = _apply_sort(suggestion["document_id"], patch=suggestion["patch"], source=AI_SORT_SOURCE, ai={"reviewed_at": utc_now()})
        except Exception as exc:
            failed.append(f"{suggestion['title']}: {getattr(exc, 'detail', exc)}")
            continue
        with _AI_SORT_LOCK:
            report["suggestions"].remove(suggestion)
            report["applied"].append({
                "id": uuid.uuid4().hex[:10], "document_id": suggestion["document_id"], "title": suggestion["title"], "via": "review",
                "changes": [{k: suggestion[k] for k in ("field", "before", "after", "source", "reason")}], "undo": undo,
            })
        done += 1
    save()
    return {"applied": done, "failed": failed}


def _ai_sort_undo(report: dict, save) -> dict:
    """Take back the changes applied from one book's kept result, unless the reader has
    changed the book since."""
    restored, skipped = 0, []
    for item in reversed(list(report["applied"])):
        if item.get("undone"):
            continue
        undo = item.get("undo") or {}
        try:
            current = read_metadata(item["document_id"])
        except KeyError:
            skipped.append(f"{item['title']}: no longer in the library")
            continue
        after = undo.get("after") or {}
        # ai_json is bookkeeping (when a book was last checked), not something the reader
        # changed: a later check rewrites it, and that must not block taking an earlier change back.
        if any(current.get(key) != value for key, value in after.items() if key != "ai_json"):
            skipped.append(f"{item['title']}: changed since, so left as it is")
            continue
        before = dict(undo.get("before") or {})
        if current.get("ai_json") != after.get("ai_json"):
            before.pop("ai_json", None)  # keep the newer record of the last check
        if before:
            STORE.update(item["document_id"], before)
        if undo.get("cover_added"):
            (THUMBNAILS_DIR / f"{item['document_id']}-custom.jpg").unlink(missing_ok=True)
        with _AI_SORT_LOCK:
            item["undone"] = True
        restored += 1
    with _AI_SORT_LOCK:
        if not report["applied"] or all(item.get("undone") for item in report["applied"]):
            report["state"] = "undone" if report["state"] == "done" else report["state"]
    save()
    return {"restored": restored, "skipped": skipped}


# --------------------------------------------------- AI check of one book --
# The "Check with AI" button on a book's page. It is the AI sort's engine run on
# that one book, but it only PROPOSES: the rules, the optional online catalogue and
# the local model say what they would change, the reader sees each change with where
# it came from, and nothing is written until they apply it (and they can undo it).
# The result is kept per book, so it is still there when the page is opened again.

AI_CHECK_DIR = DATA_DIR / "ai_check"
_AI_CHECK_WHY = {
    "has no file": "This record has no file to read.",
    "a note": "Notes are not checked.",
    "not a PDF or EPUB": "Only PDF and EPUB files can be checked.",
    "a paper or document": "This is a paper or document; the check is for books.",
}


class AiCheckOptions(BaseModel):
    online: bool = False
    model: Optional[str] = Field(default=None, max_length=120)


def _ai_check_reason(doc: dict) -> Optional[str]:
    """Why this record cannot be checked, in words, or None when it can."""
    reason = _ai_sort_skip_reason(doc)
    if reason:
        return _AI_CHECK_WHY.get(reason, f"Cannot be checked: {reason}.")
    if doc.get("indexing_status") != "indexed":
        return "Its text has not been read yet. Try again once indexing has finished."
    return None


def _ai_check_path(doc_id: str) -> Path:
    return AI_CHECK_DIR / f"{validate_doc_id(doc_id)}.json"


def _ai_check_load(doc_id: str) -> Optional[dict]:
    try:
        return {**_ai_sort_blank(), **read_json(_ai_check_path(doc_id))}
    except (OSError, ValueError):
        return None


def _ai_check_saver(doc_id: str, report: dict):
    def save() -> None:
        with _AI_SORT_LOCK:
            AI_CHECK_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(_ai_check_path(doc_id), report)

    return save


def _ai_check_public(report: Optional[dict]) -> Optional[dict]:
    """The result as the page needs it; the undo data stays on the server."""
    if report is None:
        return None
    shown = json.loads(json.dumps(report))
    shown["applied"] = [{key: value for key, value in item.items() if key != "undo"} for item in shown["applied"]]
    return shown


def _ai_check_one(doc: dict, ctx: dict, report: dict, *, texts: Optional[List[str]] = None,
                  embedded: Optional[str] = None, quiet: Optional[dict] = None, remember: bool = True) -> None:
    """Gather what the rules, the catalogue and the model say about one book and record
    each change as a suggestion. The only thing written is that the book was checked
    (`remember`; an upload that is not a record yet has nothing to write it to)."""
    doc_id = doc["document_id"]
    found = _ai_sort_gather(doc, ctx, report, texts=texts, embedded=embedded, quiet=quiet)
    quiet, work, plan, checked, title = found["quiet"], found["work"], found["plan"], found["checked"], found["title"]
    wanted: dict = {}  # field -> suggestion, so a later source replaces an earlier one for the same field

    def propose(field: str, before, after, source: str, reason: str, patch: dict) -> None:
        wanted[field] = {
            "id": uuid.uuid4().hex[:10], "document_id": doc_id, "title": title,
            **_ai_sort_change(field, before, after, source, reason), "patch": patch,
        }

    for field in ("author", "title"):
        if field in quiet and quiet[field] != doc.get(field):
            propose(field, doc.get(field), quiet[field], "rules", "from the file name, the library or the title page", {field: quiet[field]})
    for change in plan.changes:
        if change.field in {"author", "title"}:
            after = _ai_sort_spelling(ctx, change.after, remember=False) if change.field == "author" else change.after
            if after != doc.get(change.field):
                propose(change.field, doc.get(change.field), after, change.source, change.reason, {change.field: after})
        elif change.field == "kind":
            propose("kind", change.before, change.after, change.source, change.reason, {"kind": change.after})
    if plan.ai.get("fiction") is not None:
        # The model's verdict on fiction is only a way to a book's form: propose that, not the verdict.
        current = doc.get("book_form_override") or doc.get("book_form")
        form = _form_fields({**work, "ai_json": json.dumps({**_json_of(doc.get("ai_json")), "fiction": plan.ai["fiction"]})}).get("book_form")
        if form and form != current:
            propose("form", current, form, "model", "the text alone could not tell", {"form": form})
    if plan.match:
        details = _ai_sort_match_fields(work, plan.match)
        if details:
            propose("details", None, ", ".join(sorted(details)), _provider_label(plan.match["id"]), "filled in from the catalogue match", details)
    report["suggestions"] = list(wanted.values())
    report["notes"].extend(plan.notes)
    if not wanted:
        report["unchanged"] += 1
    if checked and remember:
        STORE.update(doc_id, {"ai_json": json.dumps({**_json_of(doc.get("ai_json")), "checked_at": utc_now(), "model": ctx["model"]})})


def _ai_check_known_authors() -> tuple[List[str], dict]:
    known = [shelves.normalize_author(item["author"]) or item["author"] for item in list_metadata() if item.get("author")]
    return known, shelves.canonical_authors(known)


def _ai_check_report(model: Optional[str], online: bool, applied: Optional[list] = None) -> dict:
    return {
        **_ai_sort_blank(), "state": "done", "run_id": uuid.uuid4().hex[:10], "started_at": utc_now(), "model": model,
        "options": {"online": online, "model": model}, "total": 1, "done": 1, "applied": list(applied or []),
    }


def _ai_check_finish(report: dict) -> dict:
    """Say in words why a check came to nothing, and stamp it done."""
    if report["stats"]["model_failures"]:
        report["notes"].append("The local model did not answer; nothing could be confirmed by it. Try again, or pick another model.")
    if report["stats"]["lookup_failures"]:
        report["notes"].append("The online catalogue could not be reached, so no match was compared.")
    report["finished_at"] = utc_now()
    return report


def _run_ai_check(doc: dict, model: Optional[str], online: bool) -> dict:
    """Check one book (blocking: the model can take a minute) and keep the result."""
    doc_id = doc["document_id"]
    known, spellings = _ai_check_known_authors()
    ctx = {"online": online, "model": model, "known": known, "spellings": spellings, "model_errors": 0, "lookup_errors": 0}
    previous = _ai_check_load(doc_id)
    # Earlier applied changes stay undoable after a new check.
    report = _ai_check_report(model, online, previous["applied"] if previous else None)
    _ai_check_one(doc, ctx, report)
    _ai_check_finish(report)
    _ai_check_saver(doc_id, report)()
    return report


async def _ai_check_choice(body: AiCheckOptions) -> tuple[Optional[str], bool]:
    """The model to ask and whether to look online, or 422 when there is neither."""
    models = await asyncio.to_thread(ai_sort.list_local_models)
    model = ai_sort.pick_model(body.model, models)
    online = body.online and not catalogue._offline()
    if not model and not online:
        raise HTTPException(status_code=422, detail="No local model is available and the online catalogue is off, so there is nothing to ask.")
    return model, online


@app.get("/documents/{doc_id}/ai-check")
async def ai_check_status(doc_id: str):
    doc = await asyncio.to_thread(read_metadata, doc_id)
    return {
        "reason": await asyncio.to_thread(_ai_check_reason, doc),
        "result": _ai_check_public(await asyncio.to_thread(_ai_check_load, doc_id)),
    }


@app.post("/documents/{doc_id}/ai-check")
async def ai_check_run(doc_id: str, body: AiCheckOptions):
    doc = await asyncio.to_thread(read_metadata, doc_id)
    reason = await asyncio.to_thread(_ai_check_reason, doc)
    if reason:
        raise HTTPException(status_code=422, detail=reason)
    model, online = await _ai_check_choice(body)
    report = await asyncio.to_thread(_run_ai_check, doc, model, online)
    return {"reason": None, "result": _ai_check_public(report)}


def _ai_check_act(doc_id: str, action) -> dict:
    """Run apply, dismiss or undo on this book's kept result and return it with the book."""
    with _AI_SORT_LOCK:
        report = _ai_check_load(doc_id)
        if report is None:
            raise HTTPException(status_code=404, detail="this book has not been checked")
        outcome = action(report, _ai_check_saver(doc_id, report))
    return {**outcome, "result": _ai_check_public(report), "document": read_metadata(doc_id)}


@app.post("/documents/{doc_id}/ai-check/apply")
async def ai_check_apply(doc_id: str, body: AiSortIds):
    return await asyncio.to_thread(_ai_check_act, doc_id, lambda report, save: _ai_sort_apply_suggestions(body.ids, report, save))


@app.post("/documents/{doc_id}/ai-check/dismiss")
async def ai_check_dismiss(doc_id: str, body: AiSortIds):
    def dismiss(report: dict, save) -> dict:
        before = len(report["suggestions"])
        report["suggestions"] = [item for item in report["suggestions"] if item["id"] not in body.ids]
        save()
        return {"dismissed": before - len(report["suggestions"])}

    return await asyncio.to_thread(_ai_check_act, doc_id, dismiss)


@app.post("/documents/{doc_id}/ai-check/undo")
async def ai_check_undo(doc_id: str):
    return await asyncio.to_thread(_ai_check_act, doc_id, lambda report, save: _ai_sort_undo(report, save))


class IntakeCheck(AiCheckOptions):
    """A waiting upload's details as they stand in the review form, plus the check's options."""

    title: str = Field(default="", max_length=300)
    subtitle: str = Field(default="", max_length=300)
    author: str = Field(default="", max_length=240)
    subject: str = Field(default="", max_length=240)
    description: str = Field(default="", max_length=20_000)
    publisher: str = Field(default="", max_length=300)
    language: str = Field(default="", max_length=50)
    isbn_10: str = Field(default="", max_length=24)
    isbn_13: str = Field(default="", max_length=24)
    published_year: Optional[int] = Field(default=None, ge=1000, le=2100)
    page_count: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    genres: List[str] = Field(default_factory=list, max_length=20)
    type: str = Field(default="book", pattern=r"^(?:novel|novella|short_story|collection|nonfiction|book|paper|report|manual|slides|data|writing|legal|other)$")


def _intake_check_reason(item: dict, chosen_type: str) -> Optional[str]:
    if item.get("state") != "ready":
        return "This file could not be read, so there is nothing to check."
    if item.get("format") not in {"pdf", "epub"}:
        return "Only PDF and EPUB files can be checked."
    if chosen_type not in intake.BOOK_TYPES:
        return "The check is for books; this is set as a document."
    return None


def _run_intake_check(item: dict, body: IntakeCheck, model: Optional[str], online: bool) -> dict:
    """Check an upload that is not in the library yet (blocking). It works from what the
    review form shows now, so a value the reader already corrected is not second-guessed,
    and nothing is recorded: the reader applies the suggestions to the form."""
    staged = INTAKE_DIR / item["stored_filename"]
    texts, total = _light_pages(staged, item["format"])
    embedded = intake.embedded_details(staged, item["format"])
    proposal = item["proposal"]
    title = body.title.strip() or proposal["title"]
    author = body.author.strip() or None
    genres = [genre.strip() for genre in body.genres if genre.strip()]
    verdict = doc_kind.classify(
        texts, total, file_type=item["format"], author=author or "", subject=body.subject.strip() or None, genres=genres, title=title,
        authors=max(shelves.author_count(embedded.get("author")), shelves.credit_count(item["filename"])),
    )
    blank = lambda value: (value or "").strip() or None  # noqa: E731
    pseudo = {
        "document_id": item["id"], "filename": item["filename"], "title": title, "subtitle": blank(body.subtitle), "author": author,
        "subject": blank(body.subject), "description": blank(body.description), "publisher": blank(body.publisher),
        "language": blank(body.language), "isbn_10": blank(body.isbn_10), "isbn_13": blank(body.isbn_13),
        "published_year": body.published_year, "page_count": body.page_count, "genres_json": json.dumps(genres) if genres else None,
        "pages": total, "kind": "book", "prose_score": verdict.prose_score, "author_count": verdict.authors,
        "text_stats": json.dumps({"words": verdict.words, "past": verdict.past, "english": verdict.english, "weak": verdict.weak}),
        "book_form_override": body.type if body.type not in {"book"} else None,
        "metadata_edited": 1 if title != proposal["title"] else 0,  # a title the reader changed is theirs
    }
    known, spellings = _ai_check_known_authors()
    ctx = {"online": online, "model": model, "known": known, "spellings": spellings, "model_errors": 0, "lookup_errors": 0}
    report = _ai_check_report(model, online)
    _ai_check_one(pseudo, ctx, report, texts=texts, embedded=embedded.get("author"), quiet={}, remember=False)
    return _ai_check_finish(report)


@app.post("/intake/{item_id}/ai-check")
async def intake_ai_check(item_id: str, body: IntakeCheck):
    item = await asyncio.to_thread(_intake_read, item_id)
    reason = _intake_check_reason(item, body.type)
    if reason:
        raise HTTPException(status_code=422, detail=reason)
    model, online = await _ai_check_choice(body)
    report = await asyncio.to_thread(_run_intake_check, item, body, model, online)
    return {"reason": None, "result": _ai_check_public(report)}


# ------------------------------------------------------ export and import --

APP_VERSION = "0.3.0"
IMPORT_LIMIT_BYTES = portability.MAX_ARCHIVE_BYTES
# Fields restored onto a book that already exists. They are filled in when the
# local value is empty and replaced only when the reader asks for it.
IMPORT_BOOK_FIELDS = (
    "title", "subtitle", "author", "subject", "description", "publisher", "published_year",
    "page_count", "language", "isbn_10", "isbn_13", "acquisition_source", "reading_status",
    "started_at", "finished_at", "current_page", "reading_progress", "last_read_at",
    "rating", "review", "liked", "review_spoiler", "reread", "shelf", "owned", "kind_override", "book_form_override",
    "metadata_source", "metadata_source_id",
)
# For these the app guesses a value itself, so an untouched guess is not
# something the reader chose and may be overwritten by their saved correction.
IMPORT_GUESSED_FIELDS = frozenset({"title", "author", "subject"})


def _export_archive(destination: Path) -> dict:
    documents = list_metadata()
    books = [portability.book_record(doc) for doc in documents if doc.get("collection_id") != NOTES_COLLECTION_ID]
    notes = [
        portability.note_record(doc, _note_response(doc)["text"])
        for doc in documents
        if doc.get("collection_id") == NOTES_COLLECTION_ID and not _is_digest(doc)
    ]
    covers = [
        (doc["document_id"], THUMBNAILS_DIR / f"{doc['document_id']}-custom.jpg")
        for doc in documents
        if doc.get("collection_id") != NOTES_COLLECTION_ID and (THUMBNAILS_DIR / f"{doc['document_id']}-custom.jpg").exists()
    ]
    annotated = [
        (doc["document_id"], annotated_copy_path(doc["document_id"]))
        for doc in documents
        if doc.get("file_type") == "pdf" and doc.get("record_type") != "standalone"
        and doc.get("collection_id") != NOTES_COLLECTION_ID and annotated_copy_path(doc["document_id"]).exists()
    ]
    return portability.build_archive(
        destination, books=books, notes=notes, vocabulary=STORE.list_vocabulary(), covers=covers, annotated=annotated,
        app_version=APP_VERSION, created_at=utc_now(), timezone_name=time.tzname[0],
    )


@app.get("/export")
async def export_library():
    """Download everything the reader created as one ZIP archive."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=DATA_DIR, prefix="export-", suffix=".zip", delete=False)
    handle.close()
    path = Path(handle.name)
    try:
        await asyncio.to_thread(_export_archive, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    name = f"ai-librarian-export-{datetime.now(timezone.utc).date().isoformat()}.zip"
    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(path.unlink, missing_ok=True))


def _import_changes(record: dict, local: Optional[dict], replace: bool) -> tuple:
    """(changes to apply, number of fields kept because the reader already set them)."""
    changes: dict = {}
    kept = 0
    for field in IMPORT_BOOK_FIELDS:
        value = record.get(field)
        if value is None or value == "":
            continue
        current = local.get(field) if local else None
        unset = current in (None, "") or (field == "reading_status" and current == "to_read") \
            or (field in IMPORT_GUESSED_FIELDS and not local.get("metadata_edited"))
        if unset or replace:
            if current != value:
                changes[field] = value
        elif current != value:
            kept += 1
    genres = record.get("genres")
    if genres and isinstance(genres, list) and (replace or not (local or {}).get("genres_json")):
        encoded = json.dumps([str(item) for item in genres[:20]], ensure_ascii=False)
        if encoded != (local or {}).get("genres_json"):
            changes["genres_json"] = encoded
    if changes.keys() & {"title", "author", "subject", "subtitle", "description", "genres_json", "publisher"}:
        changes["metadata_edited"] = 1
    status = changes.get("reading_status", (local or {}).get("reading_status"))
    finished = changes.get("finished_at", (local or {}).get("finished_at"))
    if "reading_status" in changes or "finished_at" in changes:
        changes["read_at"] = f"{finished}T00:00:00Z" if status == "read" and finished else None
    return changes, kept


def _validated_book_record(record: dict) -> dict:
    """Run an imported book through the same validation as the editor."""
    allowed = {key: record[key] for key in (
        "title", "author", "subject", "subtitle", "description", "publisher", "published_year",
        "page_count", "language", "isbn_10", "isbn_13", "acquisition_source", "reading_status",
        "started_at", "finished_at", "current_page", "reading_progress", "rating", "review",
        "liked", "review_spoiler", "reread", "genres", "owned",
    ) if record.get(key) not in (None, "")}
    DocumentPatch(**allowed)  # raises ValidationError on a bad value
    for key in ("started_at", "finished_at"):
        if key in allowed:
            date.fromisoformat(str(allowed[key])[:10])
    return record


def _import_annotated_copy(archive, source_id, local, label, dry_run, replace, report, problem) -> None:
    """Re-attach an exported annotated PDF to its book. An existing copy is kept
    unless the reader chose to use the file's values. The file is checked the
    same way an uploaded one is, even on a dry run, so the preview is honest."""
    target = annotated_copy_path(local["document_id"])
    if target.exists() and not replace:
        report["annotated_copies"]["kept"] += 1
        return
    ANNOTATED_DIR.mkdir(parents=True, exist_ok=True)
    temporary = ANNOTATED_DIR / f".{local['document_id']}.{secrets.token_hex(4)}.importing"
    try:
        archive.extract_annotated_copy(source_id, temporary)
        try:
            _check_annotated_pdf(temporary, local)
        except HTTPException as exc:
            problem(f"{label}: its annotated copy was skipped, {exc.detail}")
            return
        report["annotated_copies"]["restored"] += 1
        if not dry_run:
            os.replace(temporary, target)
            try:
                _refresh_pdf_marks(read_metadata(local["document_id"]), force=True)
            except Exception:
                logger.exception("could not read the marks in the restored copy for %s", local["document_id"])
    finally:
        temporary.unlink(missing_ok=True)


def _import_archive(archive: "portability.Archive", dry_run: bool, replace: bool) -> dict:
    documents = list_metadata()
    by_id = {doc["document_id"]: doc for doc in documents}
    by_hash = {
        doc["content_sha256"]: doc for doc in documents
        if doc.get("record_type") != "standalone" and doc.get("collection_id") != NOTES_COLLECTION_ID
    }
    report = {
        "dry_run": dry_run,
        "books": {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "kept_existing_values": 0},
        "notes": {"created": 0, "unchanged": 0, "skipped": 0},
        "vocabulary": {"created": 0, "unchanged": 0},
        "covers": {"restored": 0},
        "annotated_copies": {"restored": 0, "kept": 0},
        "problems": [],
        "archive": {"created_at": archive.manifest.get("created_at"), "app_version": archive.manifest.get("app_version"),
                    "counts": archive.manifest.get("counts")},
    }

    def problem(message: str) -> None:
        if len(report["problems"]) < 50:
            report["problems"].append(message)

    id_map: dict = {}
    for record in archive.books():
        label = record.get("title") or record.get("filename") or record.get("document_id") or "a book"
        try:
            _validated_book_record(record)
        except (ValidationError, ValueError, TypeError) as exc:
            report["books"]["skipped"] += 1
            problem(f"{label}: skipped, a saved value is invalid ({str(exc).splitlines()[0][:100]})")
            continue
        source_id = record.get("document_id")
        local = None
        if record.get("record_type") == "standalone":
            candidate = by_id.get(source_id)
            local = candidate if candidate and candidate.get("record_type") == "standalone" else None
        else:
            local = by_hash.get(record.get("content_sha256"))
            if local is None:
                report["books"]["skipped"] += 1
                problem(f"{label}: skipped, its file is not in this library yet (scan your library, then import again)")
                continue
        changes, kept = _import_changes(record, local, replace)
        report["books"]["kept_existing_values"] += kept
        if local is None:
            report["books"]["created"] += 1
            title = (record.get("title") or record.get("filename") or "Untitled").strip()[:300]
            new_id = source_id if source_id and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id) and source_id not in by_id else secrets.token_hex(6)
            id_map[source_id] = new_id
            if not dry_run:
                created = _new_standalone_record(new_id, title, None)
                changes.pop("title", None)
                STORE.create(created)
                if changes:
                    STORE.update(new_id, changes)
                if changes.get("author") or changes.get("subject"):
                    STORE.update(new_id, {"shelf_suggested": shelves.suggest_shelf(
                        "book", changes.get("subject"), changes.get("author"))})
                by_id[new_id] = created
            target_id = new_id
        else:
            id_map[source_id] = local["document_id"]
            target_id = local["document_id"]
            if changes:
                report["books"]["updated"] += 1
                if not dry_run:
                    STORE.update(target_id, changes)
                    if {"author", "subject"} & changes.keys():
                        merged = {**local, **changes}
                        STORE.update(target_id, {"shelf_suggested": shelves.suggest_shelf(
                            merged.get("kind_override") or merged.get("kind"), merged.get("subject"), merged.get("author"))})
            else:
                report["books"]["unchanged"] += 1
        cover = archive.cover(source_id) if source_id else None
        if cover and (replace or not (THUMBNAILS_DIR / f"{target_id}-custom.jpg").exists()):
            try:
                _normalised_cover_jpeg(cover)
                report["covers"]["restored"] += 1
                if not dry_run:
                    _save_custom_cover(target_id, cover)
            except HTTPException:
                problem(f"{label}: its saved cover could not be read")
        if source_id and local is not None and local.get("file_type") == "pdf" and archive.has_annotated_copy(source_id):
            _import_annotated_copy(archive, source_id, local, label, dry_run, replace, report, problem)

    existing_notes = {
        (doc.get("source_document_id"), doc.get("note_text"))
        for doc in documents if doc.get("collection_id") == NOTES_COLLECTION_ID and not _is_digest(doc)
    }
    for note in archive.notes():
        text = (note.get("text") or "").strip() if isinstance(note.get("text"), str) else ""
        source = note.get("source_document_id")
        if not text or len(text) > 20_000:
            report["notes"]["skipped"] += 1
            problem("A note was skipped because its text was empty or too long")
            continue
        if source:
            source = id_map.get(source) or (source if source in by_id else None)
            if source is None:
                report["notes"]["skipped"] += 1
                problem(f"A note was skipped because its book ({note.get('source_document_id')}) is not in this library")
                continue
        page = note.get("source_page") if isinstance(note.get("source_page"), int) and note.get("source_page") >= 1 else None
        quote = note.get("source_quote") if isinstance(note.get("source_quote"), str) else None
        if (source, text) in existing_notes:
            report["notes"]["unchanged"] += 1
            continue
        existing_notes.add((source, text))
        report["notes"]["created"] += 1
        if not dry_run:
            _create_note_record(text, source, page if source else None, quote if source else None,
                                note.get("created_at") if isinstance(note.get("created_at"), str) else None)

    known_words = {
        (item["normalized_word"], item.get("document_id"), item["definition"]) for item in STORE.list_vocabulary()
    }
    for item in archive.vocabulary():
        word = " ".join(str(item.get("word") or "").split())[:120]
        definition = str(item.get("definition") or "").strip()[:4000]
        if not word:
            continue
        linked = item.get("document_id")
        linked = (id_map.get(linked) or (linked if linked in by_id else None)) if linked else None
        key = (word.casefold(), linked, definition)
        if key in known_words:
            report["vocabulary"]["unchanged"] += 1
            continue
        known_words.add(key)
        report["vocabulary"]["created"] += 1
        if not dry_run:
            now = utc_now()
            page = item.get("source_page") if isinstance(item.get("source_page"), int) and item.get("source_page") >= 1 else None
            STORE.create_vocabulary({
                "word_id": generate_id(), "word": word, "normalized_word": word.casefold(),
                "definition": definition,
                "part_of_speech": str(item.get("part_of_speech") or "")[:80] or None,
                "example": str(item.get("example") or "")[:2000] or None,
                "document_id": linked, "source_page": page if linked else None,
                "source_quote": (str(item.get("source_quote") or "")[:4000] or None) if linked else None,
                "definition_source": str(item.get("definition_source") or "manual")[:40],
                "created_at": str(item.get("created_at") or now), "updated_at": now,
            })
    return report


@app.post("/import")
async def import_library(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=True),
    conflict: str = Query(default="keep", pattern="^(keep|replace)$"),
):
    """Check (dry_run, the default) or apply an export archive.

    Books are matched by content hash, so run a library scan first. Existing
    values are kept unless conflict=replace; nothing is ever deleted."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=DATA_DIR, prefix="import-", suffix=".zip", delete=False)
    path = Path(handle.name)
    try:
        size = 0
        with handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > IMPORT_LIMIT_BYTES:
                    raise HTTPException(status_code=413, detail="this archive is too large to import")
                handle.write(chunk)
        await file.close()

        def run() -> dict:
            try:
                with portability.Archive(path) as archive:
                    return _import_archive(archive, dry_run, conflict == "replace")
            except portability.ArchiveError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        return await asyncio.to_thread(run)
    finally:
        path.unlink(missing_ok=True)


@app.post("/shelves/accept")
async def accept_shelves(request: ShelfAccept):
    """File documents on their suggested shelf (the listed ones, or every
    document still waiting). Documents the reader already placed are skipped."""

    def accept() -> dict:
        wanted = set(request.document_ids) if request.document_ids is not None else None
        accepted = 0
        for doc in list_metadata():
            if doc.get("shelf") or not doc.get("shelf_suggested"):
                continue
            if wanted is not None and doc["document_id"] not in wanted:
                continue
            STORE.update(doc["document_id"], {"shelf": doc["shelf_suggested"]})
            accepted += 1
        return {"accepted": accepted}

    return await asyncio.to_thread(accept)


@app.post("/shelves/move")
async def move_shelf(request: ShelfMove):
    """Rename or move a shelf; everything on it and on its sub-shelves comes
    along. Suggested placements become the reader's choice as they move."""
    try:
        source = shelves.normalize_shelf(request.source)
        target = shelves.normalize_shelf(request.target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not source or not target:
        raise HTTPException(status_code=422, detail="both shelves need a name")
    if target.startswith(source + shelves.SEPARATOR):
        raise HTTPException(status_code=422, detail="a shelf cannot move inside itself")

    def move() -> dict:
        moved = 0
        for doc in list_metadata():
            destination = shelves.moved_shelf(shelves.effective_shelf(doc) or "", source, target)
            if destination:
                try:
                    shelves.normalize_shelf(destination)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                STORE.update(doc["document_id"], {"shelf": destination})
                moved += 1
        return {"moved": moved, "shelf": target}

    return await asyncio.to_thread(move)


def _delete_document_blocking(metadata: dict) -> None:
    doc_id = metadata["document_id"]
    with get_document_lock(doc_id):
        delete_document_vectors(doc_id)
        artifacts = [EXTRACTED_DIR / f"{doc_id}.json", CHUNKS_DIR / f"{doc_id}.json"]
        artifacts.extend(THUMBNAILS_DIR.glob(f"{doc_id}-*.jpg"))
        artifacts.append(annotated_copy_path(doc_id))
        artifacts.append(_ai_check_path(doc_id))  # the kept result of a "Check with AI"
        # Only remove the PDF itself when we own it (an upload). A
        # referenced library file is left exactly where the reader put it.
        if metadata.get("record_type") != "standalone" and not (metadata.get("source_path") or "").strip():
            artifacts.append(DOCUMENTS_DIR / metadata["stored_filename"])
        for path in artifacts:
            path.unlink(missing_ok=True)
        STORE.delete(doc_id)
        clear_index_activity(doc_id)
    with DOCUMENT_LOCKS_LOCK:
        DOCUMENT_LOCKS.pop(doc_id, None)


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    metadata = await asyncio.to_thread(read_metadata, doc_id)

    def delete_all() -> None:
        digest = _digest_doc(doc_id)
        if digest:
            _delete_document_blocking(digest)
        _delete_document_blocking(metadata)

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
        scoped_document_ids = await asyncio.to_thread(
            scope_document_ids, request.collection_id, request.shelf
        )
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
            max_per_doc=request.max_per_doc,
            document_ids=scoped_document_ids,
        )
    except HTTPException:
        raise
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


_CLOUD_PROVIDERS = ("openai", "google")
ASK_THOROUGH_MIN_SCORE = _parse_min_score(os.environ.get("ASK_THOROUGH_MIN_SCORE", "-5.0"))
ASK_AGENTIC_MAX_STEPS = max(0, generation.ASK_AGENTIC_MAX_STEPS)


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
    agentic = request.mode == "agentic"
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
    if request.min_score is not None:
        gate = request.min_score

    started = time.monotonic()
    history_turns = [turn.model_dump() for turn in request.history]
    agentic_queries: list[str] = []
    try:
        # Resolve the scope once. Quick precision recovery and Agentic follow-up
        # searches reuse it, avoiding repeated SQLite scans of the library.
        scoped_document_ids = await asyncio.to_thread(
            scope_document_ids, request.collection_id, request.shelf
        )
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
            document_ids=scoped_document_ids,
        )
        if agentic and hits and ASK_AGENTIC_MAX_STEPS:
            merged_hits = list(hits)
            seen_queries = {clean_query(request.question).casefold()}
            required_query = _agentic_required_query(request.question, format_hits(merged_hits))
            for _ in range(ASK_AGENTIC_MAX_STEPS):
                if required_query:
                    followup = required_query
                    required_query = None
                else:
                    followup = await generation.decide_followup(
                        model,
                        request.question,
                        format_hits(merged_hits),
                        history_turns,
                        provider_keys,
                    )
                normalized = clean_query(followup or "").casefold()
                if not normalized or normalized in seen_queries:
                    break
                seen_queries.add(normalized)
                agentic_queries.append(followup)
                extra_hits, extra_candidates, extra_dropped, extra_relevant = await retrieve(
                    followup,
                    passages,
                    request.document_id,
                    request.filename,
                    True,
                    SearchRequest.model_fields["rerank_k"].default,
                    gate,
                    max_per_doc=max_per_doc,
                    dedup_jaccard=ASK_DEDUP_JACCARD,
                    document_ids=scoped_document_ids,
                )
                candidate_count += extra_candidates
                rerank_dropped += extra_dropped
                relevant_count += extra_relevant
                merged_hits = merge_agentic_hits(merged_hits, extra_hits, passages * 4)
                hits = diversify_hits(
                    merged_hits, passages, max_per_doc, ASK_DEDUP_JACCARD
                )
        elif request.mode == "quick" and hits:
            # Keep Quick mode inexpensive but recover an explicit page/count
            # fact when the first broad query lands on a neighbouring passage.
            precision_query = _agentic_required_query(request.question, format_hits(hits))
            if precision_query:
                extra_hits, extra_candidates, extra_dropped, extra_relevant = await retrieve(
                    precision_query,
                    passages,
                    request.document_id,
                    request.filename,
                    True,
                    SearchRequest.model_fields["rerank_k"].default,
                    gate,
                    max_per_doc=max_per_doc,
                    dedup_jaccard=ASK_DEDUP_JACCARD,
                    document_ids=scoped_document_ids,
                )
                candidate_count += extra_candidates
                rerank_dropped += extra_dropped
                relevant_count += extra_relevant
                hits = diversify_hits(
                    merge_agentic_hits(hits, extra_hits, passages * 4),
                    passages,
                    max_per_doc,
                    ASK_DEDUP_JACCARD,
                )
    except HTTPException:
        raise
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
        agentic_searches=agentic_queries,
    )

    async def event_stream():
        rid = REQUEST_ID.get()
        generation_started = time.monotonic()
        answer_parts: list[str] = []
        used: list[dict] = []
        try:
            if not sources:
                answer_parts.append(_ASK_NO_ANSWER)
                yield _sse({"type": "token", "text": _ASK_NO_ANSWER})
                record_ask_outcome(
                    request,
                    _ASK_NO_ANSWER,
                    used,
                    (time.monotonic() - generation_started) * 1000,
                    model,
                    False,
                )
                yield _sse(
                    {"type": "sources", "results": [], "low_confidence": False, "model": model}
                )
                return
            if agentic and agentic_queries:
                yield _sse({"type": "progress", "text": f"Follow-up searches: {len(agentic_queries)}…"})
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
                    if kind == "token":
                        answer_parts.append(text)
                    yield _sse({"type": kind, "text": text})
            elif agentic:
                _system, _messages, used = generation.build_ask_prompt(
                    request.question, sources, history_turns, max_passages=passages
                )
                async for kind, text in generation.generate_agentic(
                    model,
                    request.question,
                    used,
                    history_turns,
                    keys=provider_keys,
                    max_passages=passages,
                ):
                    if kind == "token":
                        answer_parts.append(text)
                    yield _sse({"type": kind, "text": text})
            else:
                system, messages, used = generation.build_ask_prompt(
                    request.question, sources, history_turns, max_passages=passages
                )
                async for chunk in generation.generate_stream(
                    model, system, messages, keys=provider_keys
                ):
                    answer_parts.append(chunk)
                    yield _sse({"type": "token", "text": chunk})
            record_ask_outcome(
                request,
                "".join(answer_parts),
                used,
                (time.monotonic() - generation_started) * 1000,
                model,
                low_confidence,
            )
            yield _sse(
                {
                    "type": "sources",
                    "results": used,
                    "low_confidence": low_confidence,
                    "model": model,
                    "documents": len({s.get("document_id") for s in used}),
                    "relevant_count": relevant_count,
                    "agentic_searches": agentic_queries,
                }
            )
        except generation.GenerationError as exc:
            logger.warning("Ask generation failed [request %s]: %s", rid, exc)
            record_ask_outcome(
                request,
                "".join(answer_parts),
                used,
                (time.monotonic() - generation_started) * 1000,
                model,
                low_confidence,
                error="generation_error",
            )
            yield _sse({"type": "error", "detail": f"answer generation failed (request {rid})"})
        except Exception:
            logger.exception("Unexpected error while streaming an answer [request %s]", rid)
            record_ask_outcome(
                request,
                "".join(answer_parts),
                used,
                (time.monotonic() - generation_started) * 1000,
                model,
                low_confidence,
                error="unexpected_error",
            )
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


@app.get("/admin/ask-metrics")
async def admin_ask_metrics(limit: int = Query(500, ge=1, le=5000)):
    """Aggregate answer-shape telemetry from the persistent search log."""
    path = LOGS_DIR / "search.jsonl"
    if not SEARCH_LOG_ENABLED or not path.exists():
        return {"enabled": SEARCH_LOG_ENABLED, "count": 0, "by_mode": {}}

    def summarize() -> dict:
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("mode") == "ask_outcome":
                entries.append(item)
        entries = entries[-limit:]
        by_mode: dict[str, dict] = {}
        for item in entries:
            mode = item.get("ask_mode") or "unknown"
            bucket = by_mode.setdefault(
                mode,
                {"count": 0, "errors": 0, "answer_chars": 0.0, "cited_source_fraction": 0.0},
            )
            bucket["count"] += 1
            bucket["errors"] += item.get("outcome") != "ok"
            bucket["answer_chars"] += item.get("answer_chars") or 0
            bucket["cited_source_fraction"] += item.get("cited_source_fraction") or 0.0
        for bucket in by_mode.values():
            count = bucket["count"] or 1
            bucket["average_answer_chars"] = round(bucket.pop("answer_chars") / count, 1)
            bucket["average_cited_source_fraction"] = round(
                bucket.pop("cited_source_fraction") / count, 3
            )
        return {"enabled": True, "count": len(entries), "by_mode": by_mode}

    return await asyncio.to_thread(summarize)


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


def _start_folder_ingest(folder: Path, collection_id: Optional[str] = None) -> dict:
    """Register and enqueue a recursive ingest job for `folder`."""
    job_id = generate_id()
    now = utc_now()
    job = {
        "job_id": job_id,
        "state": "queued",
        "folder": str(folder.relative_to(INGEST_ROOT)) if folder != INGEST_ROOT else ".",
        "collection_id": collection_id,
        # ``None`` means the worker is still walking the folder. Once it has
        # the stable file list this becomes the real denominator used by the
        # UI's in-place progress indicators.
        "total_files": None,
        "files": [],
        "created_at": now,
        "updated_at": now,
    }
    with JOB_STATUS_LOCK:
        JOB_STATUS[job_id] = job
        save_job(job)
    _prune_old_jobs()
    try:
        INGEST_QUEUE.put((job_id, str(folder), collection_id), timeout=2)
    except queue.Full as exc:
        with JOB_STATUS_LOCK:
            job["state"] = "error"
            job["error"] = "ingest queue is full; try again later"
            save_job(job)
        raise HTTPException(status_code=503, detail=job["error"]) from exc
    return job


@app.post("/admin/ingest-folder")
async def ingest_folder(request: IngestFolderRequest):
    folder = resolve_ingest_folder(request.folder)
    return JSONResponse({**_start_folder_ingest(folder), "state": "queued"}, status_code=202)


@app.get("/library/tree")
async def library_tree(path: str = Query("", max_length=1024)):
    """One level of the mounted library: sub-folders and supported documents
    (marked when already indexed). Everything is relative to
    INGEST_ROOT; `path=""` is the root."""

    def build() -> dict:
        folder = resolve_ingest_folder(path)
        by_source = {
            (doc.get("source_path") or ""): doc
            for doc in list_metadata()
            if doc.get("source_path")
        }
        dirs, files = [], []
        with os.scandir(folder) as scan:
            for entry in scan:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    try:
                        document_count = sum(
                            1
                            for child in os.scandir(entry.path)
                            if not child.name.startswith(".")
                            and child.is_file()
                            and is_supported_path(child.name)
                        )
                    except OSError:
                        document_count = None
                    # `pdf_count` remains for older browsers; it now means
                    # supported document count, not just literal PDFs.
                    dirs.append({"name": entry.name, "type": "dir", "pdf_count": document_count, "document_count": document_count})
                elif entry.is_file() and is_supported_path(entry.name):
                    rel = str(Path(entry.path).resolve().relative_to(INGEST_ROOT))
                    doc = by_source.get(rel)
                    files.append({
                        "name": entry.name,
                        "type": "file",
                        "size": entry.stat().st_size,
                        "file_type": file_type_for_path(entry.name),
                        "indexed": doc is not None,
                        "document_id": doc["document_id"] if doc else None,
                    })
        rel_here = "" if folder == INGEST_ROOT else str(folder.relative_to(INGEST_ROOT))
        parent = None if not rel_here else str(Path(rel_here).parent).replace(".", "")
        return {
            "path": rel_here,
            "parent": parent,
            "entries": sorted(dirs, key=lambda d: d["name"].lower())
            + sorted(files, key=lambda f: f["name"].lower()),
        }

    return await asyncio.to_thread(build)


class LibraryImportRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    collection_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{12}$")


@app.post("/library/import")
async def library_import(request: LibraryImportRequest):
    """Import one document (referenced in place) or every supported file under a folder."""
    target = resolve_library_path(request.path)
    collection_id = request.collection_id or collection_for_source_path(
        str(target.relative_to(INGEST_ROOT))
    )
    if target.is_dir():
        return JSONResponse(
            {**_start_folder_ingest(target, collection_id), "state": "queued"}, status_code=202
        )
    try:
        metadata, deduplicated = await asyncio.to_thread(
            import_library_file, str(target.relative_to(INGEST_ROOT)), collection_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Extraction now happens in the background (see create_document); a
        # rejection like "no extractable text" surfaces later as this
        # document's indexing_status, not here.
        raise _sanitized_http_error(422, "import failed", exc, log_context=request.path) from exc
    if deduplicated:
        return JSONResponse({**metadata, "deduplicated": True}, status_code=200)
    enqueue_index(IndexTask(metadata["document_id"]))
    return JSONResponse(metadata, status_code=201)


class CollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=1024)
    auto_scan: bool = True


class CollectionPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    auto_scan: Optional[bool] = None


def _library_kind(doc: dict) -> str:
    """Where a file belongs: "book", "paper", "document", "note", or "pending"
    for a PDF not classified yet. The same rule as libraryKind() in the UI's
    storage.js, so a collection's badge counts what the Library lists."""
    kind = doc.get("kind_override") or doc.get("kind")
    if kind:
        return kind
    file_type = str(doc.get("file_type") or "").lower()
    if doc.get("record_type") == "standalone" or file_type == "epub":
        return "book"
    return "pending" if file_type in ("pdf", "") else "document"


def _collection_response() -> List[dict]:
    all_documents = list_metadata()
    docs = [doc for doc in all_documents if doc.get("collection_id") != NOTES_COLLECTION_ID]
    notes = sum(1 for doc in all_documents if doc.get("collection_id") == NOTES_COLLECTION_ID)
    return [
        {
            **collection,
            "document_count": sum(_belongs_to_collection(doc, collection) for doc in docs),
            "book_count": sum(_belongs_to_collection(doc, collection) and _library_kind(doc) in {"book", "pending"} for doc in docs),
        }
        for collection in configured_collections()
    ] + [
        {
            "id": NOTES_COLLECTION_ID,
            "name": "Notes",
            "path": None,
            "auto_scan": False,
            "builtin": True,
            "document_count": notes,
            "book_count": notes,
        }
    ]


@app.get("/collections")
async def get_collections():
    return {"collections": await asyncio.to_thread(_collection_response)}


@app.post("/collections")
async def create_collection(request: CollectionRequest):
    """Save a named folder and immediately queue its current supported files.

    The scan stays lazy afterwards (and can be paused per collection), but a
    newly-added Books or Work folder should feel ready without waiting for the
    passive timer.
    """
    def create() -> dict:
        folder = resolve_ingest_folder(request.path)
        path = "" if folder == INGEST_ROOT else str(folder.relative_to(INGEST_ROOT))
        name = request.name.strip()
        current = _load_settings()
        items = list(current.get("collections") or [])
        if any(str(item.get("path") or "").strip("/") == path for item in items if isinstance(item, dict)):
            raise HTTPException(status_code=409, detail="that folder already has a collection")
        collection = {
            "id": generate_id(),
            "name": name,
            "path": path,
            "auto_scan": request.auto_scan,
        }
        _save_settings({"collections": [*items, collection]})
        job = _start_folder_ingest(folder, collection["id"])
        return {"collection": collection, "job": job}

    return JSONResponse(await asyncio.to_thread(create), status_code=201)


@app.patch("/collections/{collection_id}")
async def patch_collection(collection_id: str, request: CollectionPatch):
    if not COLLECTION_ID_PATTERN.fullmatch(collection_id):
        raise HTTPException(status_code=404, detail="collection not found")

    def patch() -> dict:
        current = _load_settings()
        items = list(current.get("collections") or [])
        for index, item in enumerate(items):
            if isinstance(item, dict) and item.get("id") == collection_id:
                next_item = dict(item)
                if request.name is not None:
                    next_item["name"] = request.name.strip()
                if request.auto_scan is not None:
                    next_item["auto_scan"] = request.auto_scan
                items[index] = next_item
                _save_settings({"collections": items})
                return next_item
        raise HTTPException(status_code=404, detail="collection not found")

    return await asyncio.to_thread(patch)


@app.delete("/collections/{collection_id}")
async def delete_collection(collection_id: str):
    if not COLLECTION_ID_PATTERN.fullmatch(collection_id):
        raise HTTPException(status_code=404, detail="collection not found")

    def delete() -> dict:
        current = _load_settings()
        items = list(current.get("collections") or [])
        kept = [item for item in items if not isinstance(item, dict) or item.get("id") != collection_id]
        if len(kept) == len(items):
            raise HTTPException(status_code=404, detail="collection not found")
        _save_settings({"collections": kept})
        return {"deleted": collection_id}

    return await asyncio.to_thread(delete)


class LibraryRootRequest(BaseModel):
    # Unlike LibraryImportRequest, "" is valid here -- it means "the whole
    # /library mount", used to reset a previously-narrowed root.
    path: str = Field("", max_length=1024)


@app.get("/library/root")
async def get_library_root():
    """The folder auto-ingest scans and Browse opens to by default: the
    persisted library_root setting, narrowed from the full /library mount by
    a "Set as library folder" click, or "" if never set."""

    def build() -> dict:
        requested = (_load_settings().get("library_root") or "").strip()
        effective = library_root_path()
        effective_rel = "" if effective == INGEST_ROOT else str(effective.relative_to(INGEST_ROOT))
        return {
            "path": effective_rel,
            "valid": effective_rel == requested,
            # For display, and so the UI's "Library path" box can accept a
            # pasted absolute host path (e.g. "/home/zerg/Books"): "" if the
            # server never declared one (LIBRARY_HOST_PATH unset).
            "host_path": LIBRARY_HOST_PATH,
        }

    return await asyncio.to_thread(build)


@app.post("/library/root")
async def set_library_root(request: LibraryRootRequest):
    """Narrow (or, with path="", reset) which folder under /library counts as
    the library -- what auto-ingest scans and where Browse opens by default.
    Does not itself import anything; the next auto-ingest scan (or a manual
    Attach/Import in Browse) picks up whatever is there."""

    def build() -> dict:
        folder = resolve_ingest_folder(request.path)
        rel = "" if folder == INGEST_ROOT else str(folder.relative_to(INGEST_ROOT))
        _save_settings({"library_root": rel})
        return {"path": rel}

    return await asyncio.to_thread(build)


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


# Serve the built UI (see the Dockerfile's ui-build stage, which copies
# ui/dist here) at "/", *after* every API route above -- Starlette matches
# routes in registration order, so a real request for e.g. /documents is
# handled by that route, and only paths nothing above claims fall through to
# static files. html=True serves index.html for "/". Missing in local dev
# (no image build has happened) -- guarded so `pytest`/`uvicorn app:app`
# without a built ui/dist still starts, just without a UI at "/".
class UiFiles(StaticFiles):
    """The built UI, with caching that survives an update.

    ``index.html`` names the build's scripts by a hash of their contents, and an
    update replaces them. If a browser kept the old ``index.html`` (with no
    ``Cache-Control`` it may reuse it for hours), the page asked for scripts the
    server no longer had and broke. So the page is revalidated on every visit
    (a cheap 304 when nothing changed), while the hashed files under ``/assets``,
    which never change under the same name, may be kept for good."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        first = os.path.relpath(full_path, self.directory).split(os.sep)[0]
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if first == "assets" else "no-cache"
        return response


_UI_DIST_DIR = Path(os.environ.get("UI_DIST_DIR", str(Path(__file__).resolve().parent / "static")))
if _UI_DIST_DIR.is_dir():
    app.mount("/", UiFiles(directory=_UI_DIST_DIR, html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
