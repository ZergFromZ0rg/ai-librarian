import importlib
import math
import sys
import time
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


def make_pdf(text: str, pages: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        y = 72
        for line in text.split("\n"):
            page.insert_text((72, y), line)
            y += 14
    content = document.tobytes()
    document.close()
    return content


def wait_for_status(client: TestClient, doc_id: str, expected: str, timeout: float = 10) -> dict:
    # 10s, not 3s: extraction now runs in a fresh worker process (the `service`
    # fixture reimports `app` per test, so its process pool is never warm),
    # and that process's first call has to cold-start the ML layout model
    # pymupdf4llm uses internally before it can process even a tiny test PDF.
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/documents/{doc_id}")
        assert response.status_code == 200
        metadata = response.json()
        if metadata["indexing_status"] == expected:
            return metadata
        time.sleep(0.02)
    pytest.fail(f"document {doc_id} did not reach {expected}")


def wait_for_pipeline_version(client: TestClient, doc_id: str, version: int, timeout: float = 10) -> dict:
    """Like wait_for_status, but for confirming a rebuild actually ran: a
    document already sitting at ``indexed`` before a retry would make
    wait_for_status(..., "indexed") return immediately without ever
    observing the queued -> indexing round-trip."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/documents/{doc_id}")
        assert response.status_code == 200
        metadata = response.json()
        if metadata.get("pipeline_version") == version and metadata["indexing_status"] == "indexed":
            return metadata
        time.sleep(0.02)
    pytest.fail(f"document {doc_id} did not reach pipeline_version {version}")


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A fresh ``app`` module wired to in-memory embeddings, search, and Qdrant.

    Yields ``(module, client, indexed)`` — the reimported module, a ``TestClient``
    over it, and the dict the fake upsert writes chunks into. No AI models are
    downloaded; tests that exercise reranking monkeypatch ``module.rerank``.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INGEST_ROOT", str(tmp_path / "library"))
    monkeypatch.setenv("RERANK_TIMEOUT", "1")
    # The background auto-ingest scan is exercised directly (module._auto_ingest_scan())
    # by tests that want it; disabled here so it can't race a test's own library
    # writes/imports on a live timer thread.
    monkeypatch.setenv("AUTO_INGEST_INTERVAL_SECONDS", "0")
    # Keep Ask mode hermetic: no Ollama probe, no cloud keys. Tests that exercise
    # generation monkeypatch generation.list_models / generate_stream directly.
    monkeypatch.setenv("OLLAMA_URL", "")
    for _key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GENERATION_MODEL"):
        monkeypatch.delenv(_key, raising=False)
    import generation

    generation._ollama_cache = (0.0, None)

    sys.modules.pop("app", None)
    module = importlib.import_module("app")

    indexed = {}

    def fake_embed(texts, kind="passage", model_name=None):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([
                float(lowered.count("absurd")),
                float(lowered.count("freedom")),
                1.0,
            ])
        return vectors

    def fake_upsert(chunks):
        for chunk in chunks:
            indexed[chunk["chunk_id"]] = {
                "vector": chunk["embedding"],
                "payload": {
                    "chunk_id": chunk["chunk_id"],
                    "group_id": chunk.get("group_id"),
                    "document_id": chunk["document_id"],
                    "filename": chunk["filename"],
                    "page": chunk["page"],
                    "page_end": chunk.get("page_end", chunk["page"]),
                    "chunk_index": chunk["chunk_index"],
                    "text": chunk["text"],
                    "retrieval_text": chunk.get("retrieval_text", chunk["text"]),
                    "embedding_text": chunk.get("embedding_text", chunk["text"]),
                    "block_types": chunk.get("block_types", []),
                    "protected_type": chunk.get("protected_type"),
                },
            }

    def fake_delete(document_id):
        for chunk_id in [
            key for key, value in indexed.items() if value["payload"]["document_id"] == document_id
        ]:
            del indexed[chunk_id]

    def fake_search(vector, top_k=5, filters=None, query_text=None, **_kwargs):
        hits = []
        vector_norm = math.sqrt(sum(value * value for value in vector)) or 1
        for chunk_id, item in indexed.items():
            if filters and any(item["payload"].get(key) != value for key, value in filters.items()):
                continue
            candidate = item["vector"]
            candidate_norm = math.sqrt(sum(value * value for value in candidate)) or 1
            score = sum(left * right for left, right in zip(vector, candidate)) / (vector_norm * candidate_norm)
            hits.append({"id": chunk_id, "score": score, "payload": item["payload"]})
        return sorted(hits, key=lambda hit: hit["score"], reverse=True)[:top_k]

    monkeypatch.setattr(module, "embed_texts", fake_embed)
    monkeypatch.setattr(module, "upsert_chunks", fake_upsert)
    monkeypatch.setattr(module, "delete_document_vectors", fake_delete)
    monkeypatch.setattr(module, "search_vectors", fake_search)
    monkeypatch.setattr(module, "vector_healthcheck", lambda: True)

    with TestClient(module.app) as client:
        yield module, client, indexed

    module.STORE.close()
    module.CONVERSATIONS.close()
