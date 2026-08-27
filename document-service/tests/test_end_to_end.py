import importlib
import math
import sys
import time

import fitz
import pytest
from fastapi.testclient import TestClient


def make_pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def wait_for_status(client: TestClient, doc_id: str, expected: str, timeout: float = 3) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/documents/{doc_id}")
        assert response.status_code == 200
        metadata = response.json()
        if metadata["indexing_status"] == expected:
            return metadata
        time.sleep(0.02)
    pytest.fail(f"document {doc_id} did not reach {expected}")


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INGEST_ROOT", str(tmp_path / "library"))
    monkeypatch.setenv("RERANK_TIMEOUT", "1")

    service_path = str(__file__).rsplit("/tests/", 1)[0]
    sys.path.insert(0, service_path)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")

    indexed = {}

    def fake_embed(texts):
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
                    "document_id": chunk["document_id"],
                    "filename": chunk["filename"],
                    "page": chunk["page"],
                    "chunk_index": chunk["chunk_index"],
                    "text": chunk["text"],
                },
            }

    def fake_delete(document_id):
        for chunk_id in [
            key for key, value in indexed.items() if value["payload"]["document_id"] == document_id
        ]:
            del indexed[chunk_id]

    def fake_search(vector, top_k=5, filters=None):
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

    sys.path.remove(service_path)


def test_upload_index_search_deduplicate_and_delete(service):
    _module, client, indexed = service
    pdf = make_pdf("Camus writes about absurd life and the possibility of freedom.")

    upload = client.post("/documents", files={"file": ("essay.pdf", pdf, "application/pdf")})
    assert upload.status_code == 201
    doc_id = upload.json()["document_id"]

    metadata = wait_for_status(client, doc_id, "indexed")
    assert metadata["filename"] == "essay.pdf"
    assert metadata["vector_dim"] == 3
    assert indexed

    search = client.post("/search", json={"query": "absurd freedom", "rerank": False})
    assert search.status_code == 200
    assert search.json()["results"][0]["document"] == "essay.pdf"
    assert search.json()["results"][0]["chunk_id"]

    duplicate = client.post("/documents", files={"file": ("copy.pdf", pdf, "application/pdf")})
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True
    assert duplicate.json()["document_id"] == doc_id

    deleted = client.delete(f"/documents/{doc_id}")
    assert deleted.status_code == 200
    assert client.get(f"/documents/{doc_id}").status_code == 404
    assert not indexed


def test_indexing_error_is_persisted_and_retryable(service, monkeypatch):
    module, client, _indexed = service
    original_embed = module.embed_texts
    monkeypatch.setattr(module, "embed_texts", lambda _texts: (_ for _ in ()).throw(RuntimeError("model unavailable")))

    upload = client.post(
        "/documents",
        files={"file": ("failure.pdf", make_pdf("Text that should fail indexing."), "application/pdf")},
    )
    assert upload.status_code == 201
    doc_id = upload.json()["document_id"]
    metadata = wait_for_status(client, doc_id, "error")
    assert "model unavailable" in metadata["indexing_error"]

    monkeypatch.setattr(module, "embed_texts", original_embed)
    retry = client.post(f"/documents/{doc_id}/retry")
    assert retry.status_code == 200
    wait_for_status(client, doc_id, "indexed")


def test_folder_ingestion_cannot_escape_configured_root(service, tmp_path):
    module, client, _indexed = service
    outside = tmp_path / "outside"
    outside.mkdir()

    response = client.post("/admin/ingest-folder", json={"folder": str(outside)})

    assert response.status_code == 403
    assert module.INGEST_ROOT not in outside.parents
