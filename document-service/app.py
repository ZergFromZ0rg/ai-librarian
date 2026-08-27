import os
import uuid
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from extraction import extract_pages
from chunking import chunk_document
from embeddings import embed_texts
from vector_store import upsert_chunks
from reranker import rerank, model_info
import threading
import queue
import concurrent.futures

# background queue for async embedding/upsert jobs
JOB_QUEUE = queue.Queue()
INGEST_QUEUE = queue.Queue()

# job status store for ingestion jobs
JOB_STATUS = {}
JOB_STATUS_LOCK = threading.Lock()

# reranker executor and settings
RERANK_MAX_WORKERS = int(os.environ.get("RERANK_MAX_WORKERS", "2"))
RERANK_TIMEOUT = int(os.environ.get("RERANK_TIMEOUT", "5"))
RERANK_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=RERANK_MAX_WORKERS)


def worker():
    while True:
        job = JOB_QUEUE.get()
        if job is None:
            break
        doc_id, chunks_path = job
        try:
            # load chunks (may not have embeddings yet)
            chunks = json.loads(Path(chunks_path).read_text())
            # embed in batches
            texts = [c["text"] for c in chunks]
            vectors = embed_texts(texts)
            for c, v in zip(chunks, vectors):
                c["embedding"] = v
            # write back chunks with embeddings
            Path(chunks_path).write_text(json.dumps(chunks, ensure_ascii=False, indent=2))
            # upsert to qdrant
            upsert_chunks(chunks)
        except Exception:
            pass
        finally:
            JOB_QUEUE.task_done()


# start background worker thread
_worker_thread = threading.Thread(target=worker, daemon=True)
_worker_thread.start()


def ingest_worker():
    while True:
        job = INGEST_QUEUE.get()
        if job is None:
            break
        job_id, folder, full = job
        with JOB_STATUS_LOCK:
            JOB_STATUS[job_id]["state"] = "running"
        p = Path(folder)
        files = sorted(p.glob("*.pdf")) if p.exists() else []
        for pdf in files:
            entry = {"file": pdf.name, "status": "pending"}
            with JOB_STATUS_LOCK:
                JOB_STATUS[job_id]["files"].append(entry)
            try:
                with JOB_STATUS_LOCK:
                    entry["status"] = "processing"
                doc_id = generate_id()
                stored_name = f"{doc_id}{pdf.suffix}"
                stored_path = DOCUMENTS_DIR / stored_name
                # copy file into documents dir
                with open(pdf, "rb") as src, open(stored_path, "wb") as dst:
                    dst.write(src.read())

                pages = extract_pages(stored_path)
                raw_chunks = chunk_document(pages, max_size=800, overlap=100)

                chunks_out = []
                for idx, (page_no, text_chunk) in enumerate(raw_chunks):
                    chunk_obj = {
                        "chunk_id": uuid.uuid4().hex[:12],
                        "document_id": doc_id,
                        "filename": pdf.name,
                        "page": page_no,
                        "chunk_index": idx,
                        "text": text_chunk,
                    }
                    chunks_out.append(chunk_obj)

                chunks_path = CHUNKS_DIR / f"{doc_id}.json"
                chunks_path.write_text(json.dumps(chunks_out, ensure_ascii=False, indent=2))

                metadata = {
                    "document_id": doc_id,
                    "filename": pdf.name,
                    "file_type": "pdf",
                    "uploaded_at": datetime.utcnow().isoformat() + "Z",
                    "title": title_from_filename(pdf.name),
                    "stored_filename": stored_name,
                    "pages": len(pages),
                    "chunks": len(chunks_out),
                }
                metadata_path = METADATA_DIR / f"{doc_id}.json"
                metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))

                # enqueue embedding/upsert job
                JOB_QUEUE.put((doc_id, str(chunks_path)))

                with JOB_STATUS_LOCK:
                    entry["status"] = "done"
                    entry["document_id"] = doc_id
            except Exception as e:
                with JOB_STATUS_LOCK:
                    entry["status"] = "error"
                    entry["error"] = str(e)

        with JOB_STATUS_LOCK:
            JOB_STATUS[job_id]["state"] = "done"
        INGEST_QUEUE.task_done()
    _cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
    if _cors_origins:
    _allow_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
else:
    _allow_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DOCUMENTS_DIR = DATA_DIR / "documents"
METADATA_DIR = DATA_DIR / "metadata"
EXTRACTED_DIR = DATA_DIR / "extracted"
CHUNKS_DIR = DATA_DIR / "chunks"

for d in (DOCUMENTS_DIR, METADATA_DIR, EXTRACTED_DIR):
    d.mkdir(parents=True, exist_ok=True)
CHUNKS_DIR.mkdir(parents=True, exist_ok=True)


def generate_id():
    return uuid.uuid4().hex[:12]


def title_from_filename(filename: str) -> str:
    name = Path(filename).stem
    return name.replace("_", " ").replace("-", " ").strip().title()


@app.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="missing file or filename")

    filename = file.filename
    ext = Path(filename).suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="only PDF supported in v1")

    doc_id = generate_id()
    stored_name = f"{doc_id}{ext}"
    stored_path = DOCUMENTS_DIR / stored_name
    content = await file.read()
    stored_path.write_bytes(content)

    try:
        pages = extract_pages(stored_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"extraction failed: {e}")

    extracted_path = EXTRACTED_DIR / f"{doc_id}.json"
    extracted_path.write_text(json.dumps(pages, ensure_ascii=False, indent=2))

    raw_chunks = chunk_document(pages, max_size=800, overlap=100)

    chunks_out = []
    for idx, (page_no, text_chunk) in enumerate(raw_chunks):
        chunk_obj = {
            "chunk_id": uuid.uuid4().hex[:12],
            "document_id": doc_id,
            "page": page_no,
            "chunk_index": idx,
            "text": text_chunk,
        }
        chunks_out.append(chunk_obj)

    chunks_path = CHUNKS_DIR / f"{doc_id}.json"
    chunks_path.write_text(json.dumps(chunks_out, ensure_ascii=False, indent=2))
    try:
        JOB_QUEUE.put((doc_id, str(chunks_path)))
    except Exception:
        pass

    vector_dim = 0
    chunk_ids_preview = [c["chunk_id"] for c in chunks_out[:3]]

    metadata = {
        "document_id": doc_id,
        "filename": filename,
        "file_type": "pdf",
        "uploaded_at": datetime.utcnow().isoformat() + "Z",
        "title": title_from_filename(filename),
        "stored_filename": stored_name,
        "pages": len(pages),
        "chunks": len(chunks_out),
        "embedding_model": "all-MiniLM-L6-v2",
        "vector_dim": vector_dim,
        "chunk_ids_preview": chunk_ids_preview,
    }

    metadata_path = METADATA_DIR / f"{doc_id}.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))

    return JSONResponse(metadata, status_code=201)


@app.get("/documents/{doc_id}/chunks")
def get_chunks(doc_id: str):
    chunks_file = CHUNKS_DIR / f"{doc_id}.json"
    if not chunks_file.exists():
        raise HTTPException(status_code=404, detail="chunks not found")
    try:
        data = json.loads(chunks_file.read_text())
        return JSONResponse(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to read chunks: {e}")


@app.post("/search")
async def search(body: dict):
    if not body or "query" not in body:
        raise HTTPException(status_code=400, detail="missing query")
    query = body["query"]
    top_k = int(body.get("top_k", 5))

    doc_filter = {}
    if body.get("document_id"):
        doc_filter["document_id"] = body.get("document_id")
    if body.get("filename"):
        doc_filter["filename"] = body.get("filename")

    rerank_flag = bool(body.get("rerank", False))
    rerank_k = int(body.get("rerank_k", min(20, top_k)))

    try:
        q_vec = await asyncio.to_thread(lambda: embed_texts([query])[0])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"embedding failed: {e}")

    try:
        from vector_store import search_vectors

        hits = search_vectors(q_vec, top_k=top_k, filters=doc_filter or None)

        if rerank_flag and hits:
            passages = [h.get("payload", {}).get("text", "") for h in hits]
            try:
                future = RERANK_EXECUTOR.submit(rerank, query, passages)
                scores = future.result(timeout=RERANK_TIMEOUT)
                for h, s in zip(hits, scores):
                    h["rerank_score"] = s
                hits = sorted(hits, key=lambda x: x.get("rerank_score", 0), reverse=True)[:rerank_k]
            except concurrent.futures.TimeoutError:
                pass
            except Exception:
                pass

        formatted = []
        for h in hits:
            payload = h.get("payload") or {}
            formatted.append(
                {
                    "document": payload.get("filename") or payload.get("document_id"),
                    "page": payload.get("page"),
                    "score": h.get("score"),
                    "rerank_score": h.get("rerank_score"),
                    "text": payload.get("text"),
                }
            )
        return JSONResponse({"query": query, "results": formatted})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"search failed: {e}")


@app.get("/search")
async def search_get(q: str = Query(...), top_k: int = Query(5), document_id: Optional[str] = None, filename: Optional[str] = None, highlight: str = Query("true"), max_text_chars: int = Query(500), rerank: str = Query("false"), rerank_k: Optional[int] = None):
    if not q:
        raise HTTPException(status_code=400, detail="missing q parameter")
    try:
        q_vec = await asyncio.to_thread(lambda: embed_texts([q])[0])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"embedding failed: {e}")

    doc_filter = {}
    if document_id:
        doc_filter["document_id"] = document_id
    if filename:
        doc_filter["filename"] = filename

    highlight_flag = highlight.lower() in ("1", "true", "yes")
    try:
        max_text_chars = int(max_text_chars)
    except Exception:
        max_text_chars = 500

    rerank_flag = rerank.lower() in ("1", "true", "yes")
    if rerank_k is None:
        rerank_k = min(20, top_k)

    try:
        from vector_store import search_vectors

        hits = search_vectors(q_vec, top_k=top_k, filters=doc_filter or None)

        if rerank_flag and hits:
            passages = [h.get("payload", {}).get("text", "") for h in hits]
            try:
                future = RERANK_EXECUTOR.submit(rerank, q, passages)
                scores = future.result(timeout=RERANK_TIMEOUT)
                for h, s in zip(hits, scores):
                    h["rerank_score"] = s
                hits = sorted(hits, key=lambda x: x.get("rerank_score", 0), reverse=True)[:rerank_k]
            except concurrent.futures.TimeoutError:
                pass
            except Exception:
                pass

        formatted = []
        q_lower = q.lower()
        for h in hits:
            payload = h.get("payload") or {}
            text = payload.get("text") or ""
            snippet = text
            if highlight_flag and q_lower in text.lower():
                idx = text.lower().find(q_lower)
                start = max(0, idx - 100)
                end = min(len(text), idx + len(q) + 100)
                snippet = text[start:end]
                snippet = snippet.replace(text[idx : idx + len(q)], f"<em>{text[idx: idx+len(q)]}</em>")
            else:
                if len(snippet) > max_text_chars:
                    snippet = snippet[: max_text_chars - 3] + "..."

            formatted.append(
                {
                    "document": payload.get("filename") or payload.get("document_id"),
                    "page": payload.get("page"),
                    "score": h.get("score"),
                    "rerank_score": h.get("rerank_score"),
                    "text": snippet,
                }
            )
        return JSONResponse({"query": q, "results": formatted})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"search failed: {e}")


@app.get("/admin/reranker")
def admin_reranker():
    try:
        info = model_info()
    except Exception:
        info = {"loaded": False, "model_name": None}

    exec_info = {"max_workers": RERANK_MAX_WORKERS}
    try:
        qsize = getattr(RERANK_EXECUTOR, "_work_queue", None)
        exec_info["pending_tasks"] = qsize.qsize() if qsize is not None else None
    except Exception:
        exec_info["pending_tasks"] = None

    return JSONResponse({"reranker": info, "executor": exec_info})


@app.get("/health")
async def health():
    """Simple health endpoint. Checks Qdrant connectivity if possible."""
    status = {"status": "ok"}
    try:
        import vector_store

        try:
            # call a light qdrant RPC to assert connectivity
            vector_store.client.get_collections()
            status["qdrant"] = True
        except Exception:
            status["qdrant"] = False
    except Exception:
        status["qdrant"] = None

    return JSONResponse(status)


@app.get("/admin/ingest-status/{job_id}")
async def ingest_status(job_id: str):
    with JOB_STATUS_LOCK:
        status = JOB_STATUS.get(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(status)


@app.post("/admin/ingest-folder")
async def ingest_folder(payload: dict):
    folder = payload.get("folder")
    full = bool(payload.get("full_pipeline", True))
    if not folder:
        raise HTTPException(status_code=400, detail="missing folder")
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"folder not found: {folder}")

    job_id = uuid.uuid4().hex[:12]
    with JOB_STATUS_LOCK:
        JOB_STATUS[job_id] = {"state": "queued", "folder": folder, "files": []}
    INGEST_QUEUE.put((job_id, folder, full))
    return JSONResponse({"job_id": job_id})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), log_level="info")
