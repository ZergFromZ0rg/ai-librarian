import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from chunking import chunk_document
from embeddings import DEFAULT_MODEL as EMBEDDING_MODEL
from embeddings import embed_texts
from extraction import extract_pages
from reranker import model_info, rerank
from vector_store import (
    delete_document as delete_document_vectors,
    healthcheck as vector_healthcheck,
    search_vectors,
    upsert_chunks,
)

logger = logging.getLogger("ai_librarian")

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
DOCUMENTS_DIR = DATA_DIR / "documents"
METADATA_DIR = DATA_DIR / "metadata"
EXTRACTED_DIR = DATA_DIR / "extracted"
CHUNKS_DIR = DATA_DIR / "chunks"
JOBS_DIR = DATA_DIR / "jobs"
INGEST_ROOT = Path(os.environ.get("INGEST_ROOT", str(DATA_DIR / "inbox"))).resolve()

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))
INDEX_QUEUE_SIZE = int(os.environ.get("INDEX_QUEUE_SIZE", "100"))
INGEST_QUEUE_SIZE = int(os.environ.get("INGEST_QUEUE_SIZE", "10"))
RERANK_MAX_WORKERS = int(os.environ.get("RERANK_MAX_WORKERS", "2"))
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "10"))
DOC_ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")

for directory in (DOCUMENTS_DIR, METADATA_DIR, EXTRACTED_DIR, CHUNKS_DIR, JOBS_DIR, INGEST_ROOT):
    directory.mkdir(parents=True, exist_ok=True)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=50)
    document_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{12}$")
    filename: Optional[str] = Field(default=None, max_length=255)
    rerank: bool = False
    rerank_k: int = Field(default=20, ge=1, le=50)
    max_text_chars: int = Field(default=1_500, ge=100, le=10_000)


class IngestFolderRequest(BaseModel):
    folder: Optional[str] = Field(default=None, max_length=1_024)


@dataclass(frozen=True)
class IndexTask:
    document_id: str
    chunks_path: str
    ingest_job_id: Optional[str] = None
    ingest_file_index: Optional[int] = None


INDEX_QUEUE = queue.Queue(maxsize=INDEX_QUEUE_SIZE)
INGEST_QUEUE = queue.Queue(maxsize=INGEST_QUEUE_SIZE)
JOB_STATUS = {}
JOB_STATUS_LOCK = threading.RLock()
FILE_LOCK = threading.RLock()
DOCUMENT_LOCKS = {}
DOCUMENT_LOCKS_LOCK = threading.Lock()
WORKER_THREADS = []
RERANK_EXECUTOR = None


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


def metadata_path(doc_id: str) -> Path:
    return METADATA_DIR / f"{doc_id}.json"


def read_metadata(doc_id: str) -> dict:
    validate_doc_id(doc_id)
    path = metadata_path(doc_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="document not found")
    with FILE_LOCK:
        return read_json(path)


def update_metadata(doc_id: str, **changes) -> dict:
    path = metadata_path(doc_id)
    with FILE_LOCK:
        if not path.exists():
            raise FileNotFoundError(f"metadata for {doc_id} does not exist")
        metadata = read_json(path)
        metadata.update(changes)
        metadata["updated_at"] = utc_now()
        atomic_write_json(path, metadata)
        return metadata


def list_metadata() -> List[dict]:
    documents = []
    with FILE_LOCK:
        for path in METADATA_DIR.glob("*.json"):
            try:
                documents.append(read_json(path))
            except (OSError, json.JSONDecodeError):
                logger.exception("Could not read metadata file %s", path)
    return sorted(documents, key=lambda item: item.get("uploaded_at", ""), reverse=True)


def find_duplicate(content_sha256: str) -> Optional[dict]:
    for metadata in list_metadata():
        if metadata.get("content_sha256") == content_sha256:
            return metadata
    return None


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


def update_ingest_entry(task: IndexTask, status: str, error: Optional[str] = None) -> None:
    if task.ingest_job_id is None or task.ingest_file_index is None:
        return
    with JOB_STATUS_LOCK:
        job = JOB_STATUS.get(task.ingest_job_id)
        if not job or task.ingest_file_index >= len(job["files"]):
            return
        entry = job["files"][task.ingest_file_index]
        entry["status"] = status
        if error:
            entry["error"] = error
        else:
            entry.pop("error", None)
        save_job(job)
    refresh_ingest_job(task.ingest_job_id)


def enqueue_index(task: IndexTask, update_status: bool = True) -> None:
    if update_status:
        update_metadata(task.document_id, indexing_status="queued", indexing_error=None)
    try:
        INDEX_QUEUE.put(task, timeout=2)
    except queue.Full as exc:
        message = "indexing queue is full; retry this document later"
        update_metadata(task.document_id, indexing_status="error", indexing_error=message)
        update_ingest_entry(task, "error", message)
        raise RuntimeError(message) from exc


def index_worker() -> None:
    while True:
        task = INDEX_QUEUE.get()
        try:
            if task is None:
                return
            with get_document_lock(task.document_id):
                update_metadata(task.document_id, indexing_status="indexing", indexing_error=None)
                update_ingest_entry(task, "indexing")
                chunks = read_json(Path(task.chunks_path))
                delete_document_vectors(task.document_id)
                vector_dim = 0
                for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
                    batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
                    vectors = embed_texts([chunk["text"] for chunk in batch])
                    indexed_batch = [{**chunk, "embedding": vector} for chunk, vector in zip(batch, vectors)]
                    if indexed_batch:
                        vector_dim = len(indexed_batch[0]["embedding"])
                        upsert_chunks(indexed_batch)
                update_metadata(
                    task.document_id,
                    indexing_status="indexed",
                    indexing_error=None,
                    indexed_at=utc_now(),
                    embedding_model=EMBEDDING_MODEL,
                    vector_dim=vector_dim,
                )
                update_ingest_entry(task, "indexed")
        except Exception as exc:
            if task is not None:
                message = str(exc) or exc.__class__.__name__
                logger.exception("Indexing failed for document %s", task.document_id)
                try:
                    delete_document_vectors(task.document_id)
                except Exception:
                    logger.exception("Could not clean partial vectors for %s", task.document_id)
                try:
                    update_metadata(task.document_id, indexing_status="error", indexing_error=message)
                except Exception:
                    logger.exception("Could not persist indexing error for %s", task.document_id)
                update_ingest_entry(task, "error", message)
        finally:
            INDEX_QUEUE.task_done()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_document(stored_path: Path, filename: str, content_sha256: str) -> dict:
    doc_id = stored_path.stem
    try:
        pages = extract_pages(stored_path)
        if not any(page.get("text", "").strip() for page in pages):
            raise ValueError("the PDF contains no extractable text; scanned PDFs require OCR")
        raw_chunks = chunk_document(pages, max_size=800, overlap=100)
        chunks = [
            {
                "chunk_id": uuid.uuid4().hex[:12],
                "document_id": doc_id,
                "filename": filename,
                "page": page_no,
                "chunk_index": index,
                "text": text,
            }
            for index, (page_no, text) in enumerate(raw_chunks)
        ]
        if not chunks:
            raise ValueError("the PDF produced no searchable text chunks")

        atomic_write_json(EXTRACTED_DIR / f"{doc_id}.json", pages)
        chunks_path = CHUNKS_DIR / f"{doc_id}.json"
        atomic_write_json(chunks_path, chunks)
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
            "pages": len(pages),
            "chunks": len(chunks),
            "indexing_status": "queued",
            "indexing_error": None,
            "embedding_model": EMBEDDING_MODEL,
            "vector_dim": 0,
        }
        atomic_write_json(metadata_path(doc_id), metadata)
        return metadata
    except Exception:
        stored_path.unlink(missing_ok=True)
        (EXTRACTED_DIR / f"{doc_id}.json").unlink(missing_ok=True)
        (CHUNKS_DIR / f"{doc_id}.json").unlink(missing_ok=True)
        metadata_path(doc_id).unlink(missing_ok=True)
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
                            IndexTask(
                                metadata["document_id"],
                                str(CHUNKS_DIR / f"{metadata['document_id']}.json"),
                                job_id,
                                file_index,
                            ),
                            update_status=False,
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
            JOB_STATUS[job["job_id"]] = job
        except Exception:
            logger.exception("Could not recover job %s", path)

    for metadata in list_metadata():
        if metadata.get("indexing_status") in {"queued", "indexing"}:
            doc_id = metadata.get("document_id")
            chunks_path = CHUNKS_DIR / f"{doc_id}.json"
            if doc_id and chunks_path.exists():
                try:
                    enqueue_index(IndexTask(doc_id, str(chunks_path)), update_status=True)
                except Exception:
                    logger.exception("Could not recover indexing for %s", doc_id)


def start_workers() -> None:
    global WORKER_THREADS, RERANK_EXECUTOR
    if any(thread.is_alive() for thread in WORKER_THREADS):
        return
    RERANK_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
        max_workers=RERANK_MAX_WORKERS,
        thread_name_prefix="reranker",
    )
    WORKER_THREADS = [
        threading.Thread(target=index_worker, daemon=True, name="index-worker"),
        threading.Thread(target=ingest_worker, daemon=True, name="ingest-worker"),
    ]
    for thread in WORKER_THREADS:
        thread.start()
    recover_interrupted_work()


def stop_workers() -> None:
    global RERANK_EXECUTOR
    for work_queue in (INDEX_QUEUE, INGEST_QUEUE):
        try:
            work_queue.put_nowait(None)
        except queue.Full:
            logger.warning("Could not enqueue worker shutdown signal")
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


app = FastAPI(
    title="AI Librarian",
    description="Local-first document ingestion and semantic search.",
    version="0.2.0",
    lifespan=lifespan,
)

cors_value = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
cors_origins = [origin.strip() for origin in cors_value.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or [],
    allow_credentials="*" not in cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


async def run_reranker(query: str, hits: List[dict]) -> List[dict]:
    if not hits or RERANK_EXECUTOR is None:
        return hits
    passages = [hit.get("payload", {}).get("text", "") for hit in hits]
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


async def retrieve(
    query: str,
    top_k: int,
    document_id: Optional[str],
    filename: Optional[str],
    rerank_enabled: bool,
    rerank_k: int,
) -> List[dict]:
    query_vector = await asyncio.to_thread(lambda: embed_texts([query])[0])
    filters = {key: value for key, value in {"document_id": document_id, "filename": filename}.items() if value}
    candidate_count = max(top_k, rerank_k) if rerank_enabled else top_k
    hits = await asyncio.to_thread(search_vectors, query_vector, candidate_count, filters or None)
    if rerank_enabled:
        hits = await run_reranker(query, hits)
    return hits[:top_k]


def format_hits(hits: List[dict], max_text_chars: int = 10_000) -> List[dict]:
    formatted = []
    for hit in hits:
        payload = hit.get("payload") or {}
        text = payload.get("text") or ""
        if len(text) > max_text_chars:
            text = text[: max_text_chars - 3] + "..."
        formatted.append(
            {
                "chunk_id": payload.get("chunk_id"),
                "document_id": payload.get("document_id"),
                "document": payload.get("filename") or payload.get("document_id"),
                "page": payload.get("page"),
                "score": hit.get("score"),
                "rerank_score": hit.get("rerank_score"),
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
    except Exception as exc:
        logger.exception("Document ingestion failed for %s", file.filename)
        raise HTTPException(status_code=422, detail=f"document ingestion failed: {exc}") from exc
    try:
        enqueue_index(IndexTask(doc_id, str(CHUNKS_DIR / f"{doc_id}.json")), update_status=False)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        raise HTTPException(status_code=500, detail=f"failed to read chunks: {exc}") from exc


@app.post("/documents/{doc_id}/retry")
async def retry_document(doc_id: str):
    metadata = await asyncio.to_thread(read_metadata, doc_id)
    if metadata.get("indexing_status") in {"queued", "indexing"}:
        raise HTTPException(status_code=409, detail="document is already queued for indexing")
    chunks_path = CHUNKS_DIR / f"{doc_id}.json"
    if not chunks_path.exists():
        raise HTTPException(status_code=409, detail="document chunks are missing; upload the document again")
    try:
        enqueue_index(IndexTask(doc_id, str(chunks_path)))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
                metadata_path(doc_id),
            ):
                path.unlink(missing_ok=True)

    try:
        await asyncio.to_thread(delete_all)
    except Exception as exc:
        logger.exception("Could not delete document %s", doc_id)
        raise HTTPException(status_code=503, detail=f"could not delete document: {exc}") from exc
    return JSONResponse(status_code=200, content={"deleted": doc_id})


@app.post("/search")
async def search(request: SearchRequest):
    try:
        hits = await retrieve(
            request.query,
            request.top_k,
            request.document_id,
            request.filename,
            request.rerank,
            request.rerank_k,
        )
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=503, detail=f"search failed: {exc}") from exc
    return {"query": request.query, "results": format_hits(hits, request.max_text_chars)}


@app.get("/search")
async def search_get(
    q: str = Query(..., min_length=1, max_length=1_000),
    top_k: int = Query(5, ge=1, le=50),
    document_id: Optional[str] = Query(default=None, pattern=r"^[a-f0-9]{12}$"),
    filename: Optional[str] = Query(default=None, max_length=255),
    max_text_chars: int = Query(1_500, ge=100, le=10_000),
    rerank_enabled: bool = Query(False, alias="rerank"),
    rerank_k: int = Query(20, ge=1, le=50),
):
    request = SearchRequest(
        query=q,
        top_k=top_k,
        document_id=document_id,
        filename=filename,
        max_text_chars=max_text_chars,
        rerank=rerank_enabled,
        rerank_k=rerank_k,
    )
    return await search(request)


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
        "index_queue": INDEX_QUEUE.qsize(),
        "ingest_queue": INGEST_QUEUE.qsize(),
    }
    return JSONResponse(payload, status_code=200 if qdrant_ok else 503)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
