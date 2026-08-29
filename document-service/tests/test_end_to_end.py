import importlib
import json
import logging
import math
import sys
import time

import fitz
import pytest
from fastapi.testclient import TestClient


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


_OCR_MUSH = "\n".join(
    [
        "Matheinatics as we kilow it has beeil created and used by huinan beiilgs "
        "niatheniaticians physicists computer scieiltists and ecoilomists al1 meinbers",
        "of the species Horno sapieils this inay be ail obvious fact but it has ail "
        "iinportant coilsequeilce for the study of the iiiiiid the coilceptual systein",
        "is systeinatic aild ilot arbitrary iii geileral and this pattern repeats "
        "throughout the volume across every chapter and every sectioil without relief",
    ]
)

_CLEAN_PROSE = "\n".join(
    [
        "The history of mathematics begins with counting and measurement in the "
        "ancient river valley civilizations of Egypt and Mesopotamia and beyond.",
        "Greek geometers later organized these scattered practical results into "
        "deductive systems built from explicit axioms, definitions, and theorems.",
        "That axiomatic tradition, revived and widely extended over many centuries, "
        "still shapes how working mathematicians present and justify new discoveries.",
    ]
)


def make_pdf_pages(texts) -> bytes:
    document = fitz.open()
    for text in texts:
        page = document.new_page()
        y = 72
        for line in text.split("\n"):
            page.insert_text((72, y), line)
            y += 14
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

    def fake_search(vector, top_k=5, filters=None, query_text=None):
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
    top = search.json()["results"][0]
    assert top["document"] == "essay.pdf"
    assert top["chunk_id"]
    # the display contract: every result carries run-in context and match fields
    assert "lead_in" in top and "matched" in top

    duplicate = client.post("/documents", files={"file": ("copy.pdf", pdf, "application/pdf")})
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True
    assert duplicate.json()["document_id"] == doc_id

    deleted = client.delete(f"/documents/{doc_id}")
    assert deleted.status_code == 200
    assert client.get(f"/documents/{doc_id}").status_code == 404
    assert not indexed


def test_search_is_logged_with_scored_hits(service):
    module, client, _indexed = service
    upload = client.post(
        "/documents",
        files={"file": ("essay.pdf", make_pdf("Camus writes about absurd life and freedom."), "application/pdf")},
    )
    doc_id = upload.json()["document_id"]
    wait_for_status(client, doc_id, "indexed")

    assert client.post("/search", json={"query": "absurd freedom", "rerank": False}).status_code == 200

    log_path = module.LOGS_DIR / "search.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["query"] == "absurd freedom"
    assert entry["rerank"] is False
    assert entry["candidate_count"] >= entry["result_count"] >= 1
    assert isinstance(entry["latency_ms"], (int, float))
    assert entry["hits"][0]["document_id"] == doc_id
    assert entry["hits"][0]["dense_score"] is not None

    recent = client.get("/admin/search-log").json()
    assert recent["enabled"] is True
    assert recent["entries"][0]["query"] == "absurd freedom"


def test_search_log_can_be_disabled(service, monkeypatch):
    module, client, _indexed = service
    monkeypatch.setattr(module, "SEARCH_LOG_ENABLED", False)
    upload = client.post(
        "/documents",
        files={"file": ("essay.pdf", make_pdf("Camus writes about absurd life and freedom."), "application/pdf")},
    )
    wait_for_status(client, upload.json()["document_id"], "indexed")
    client.post("/search", json={"query": "absurd freedom"})

    assert not (module.LOGS_DIR / "search.jsonl").exists()
    assert client.get("/admin/search-log").json() == {"enabled": False, "entries": []}


def test_stored_pdf_and_rendered_pages_are_served(service):
    _module, client, _indexed = service
    upload = client.post(
        "/documents",
        files={"file": ("source.pdf", make_pdf("Camus writes about the absurd and revolt.", pages=3), "application/pdf")},
    )
    doc_id = upload.json()["document_id"]

    pdf = client.get(f"/documents/{doc_id}/file")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"

    page = client.get(f"/documents/{doc_id}/page/1", params={"highlight": "Camus writes about the absurd"})
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert page.content[:8] == b"\x89PNG\r\n\x1a\n"

    assert client.get(f"/documents/{doc_id}/page/99").status_code == 404
    assert client.get(f"/documents/{doc_id}/page/0").status_code == 404
    assert client.get("/documents/ffffffffffff/file").status_code == 404


def test_pdf_with_a_corrupt_ocr_text_layer_is_rejected(service):
    _module, client, _indexed = service
    response = client.post(
        "/documents",
        files={"file": ("scan.pdf", make_pdf(_OCR_MUSH, pages=8), "application/pdf")},
    )
    assert response.status_code == 422
    assert "corrupted" in response.json()["detail"]


def test_document_with_a_few_corrupt_pages_indexes_the_rest_with_a_note(service):
    _module, client, _indexed = service
    pages = [_CLEAN_PROSE] * 10 + [_OCR_MUSH] * 2
    upload = client.post(
        "/documents",
        files={"file": ("mixed.pdf", make_pdf_pages(pages), "application/pdf")},
    )
    assert upload.status_code == 201
    doc_id = upload.json()["document_id"]

    metadata = wait_for_status(client, doc_id, "indexed")
    assert metadata["pages"] == 12  # the true page count, not the kept count
    assert "2 of 12 pages were skipped" in (metadata["extraction_notes"] or "")


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


def test_folder_ingestion_indexes_each_file_and_reports_progress(service, tmp_path):
    module, client, _indexed = service
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    for name in ("one.pdf", "two.pdf"):
        (library / name).write_bytes(make_pdf(f"Absurd freedom in {name}."))

    response = client.post("/admin/ingest-folder", json={"folder": "."})
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.time() + 5
    job = None
    while time.time() < deadline:
        job = client.get(f"/admin/ingest-status/{job_id}").json()
        if job["state"] in {"done", "partial", "error"}:
            break
        time.sleep(0.05)

    assert job["state"] == "done"
    assert len(job["files"]) == 2
    assert {entry["status"] for entry in job["files"]} == {"indexed"}


def test_upload_rejects_non_pdf_and_empty_files(service):
    _module, client, _indexed = service
    non_pdf = client.post("/documents", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert non_pdf.status_code == 400
    empty = client.post("/documents", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert empty.status_code == 400


def test_upload_rejects_a_file_over_the_size_limit(service, monkeypatch):
    module, client, _indexed = service
    monkeypatch.setattr(module, "MAX_UPLOAD_BYTES", 16)
    response = client.post(
        "/documents",
        files={"file": ("big.pdf", make_pdf("This PDF is comfortably over sixteen bytes."), "application/pdf")},
    )
    assert response.status_code == 413


def test_retry_conflicts_while_indexing_is_already_underway(service):
    module, client, _indexed = service
    upload = client.post(
        "/documents",
        files={"file": ("ok.pdf", make_pdf("Absurd freedom essay."), "application/pdf")},
    )
    doc_id = upload.json()["document_id"]
    wait_for_status(client, doc_id, "indexed")

    module.update_metadata(doc_id, indexing_status="indexing")
    assert client.post(f"/documents/{doc_id}/retry").status_code == 409


def test_unknown_and_malformed_document_ids_return_404(service):
    _module, client, _indexed = service
    missing = "abcdef012345"
    assert client.get(f"/documents/{missing}").status_code == 404
    assert client.get(f"/documents/{missing}/chunks").status_code == 404
    assert client.post(f"/documents/{missing}/retry").status_code == 404
    assert client.delete(f"/documents/{missing}").status_code == 404
    assert client.get("/documents/NOTHEXVALUE1").status_code == 404


def test_search_rejects_invalid_input(service):
    _module, client, _indexed = service
    assert client.post("/search", json={"query": ""}).status_code == 422
    assert client.get("/search", params={"q": "camus", "document_id": "not-a-real-id"}).status_code == 422


def test_folder_ingestion_cannot_escape_configured_root(service, tmp_path):
    module, client, _indexed = service
    outside = tmp_path / "outside"
    outside.mkdir()

    response = client.post("/admin/ingest-folder", json={"folder": str(outside)})

    assert response.status_code == 403
    assert module.INGEST_ROOT not in outside.parents


def test_stored_pdf_can_be_rebuilt_for_new_pipeline_version(service):
    module, client, _indexed = service
    upload = client.post(
        "/documents",
        files={"file": ("migration.pdf", make_pdf("A structured passage for migration."), "application/pdf")},
    )
    assert upload.status_code == 201
    doc_id = upload.json()["document_id"]
    wait_for_status(client, doc_id, "indexed")
    stale = module.update_metadata(doc_id, pipeline_version=1, index_schema_version=1)

    rebuilt = module.rebuild_document_artifacts(stale)
    chunks = client.get(f"/documents/{doc_id}/chunks").json()
    extracted = module.read_json(module.EXTRACTED_DIR / f"{doc_id}.json")

    assert rebuilt["pipeline_version"] == module.PIPELINE_VERSION
    assert rebuilt["index_schema_version"] == 0
    assert extracted["format"] == "typed-markdown"
    assert extracted["blocks"][0]["type"] == "paragraph"
    assert chunks[0]["format"] == "markdown"
    assert chunks[0]["embedding_text"]
    assert chunks[0]["group_id"]


def test_service_logger_is_configured_and_emits_info(service):
    import io

    service_logger = logging.getLogger("ai_librarian")
    assert service_logger.handlers, "the ai_librarian logger has no handler"
    assert service_logger.level <= logging.INFO
    assert service_logger.propagate is False

    sink = io.StringIO()
    probe = logging.StreamHandler(sink)
    service_logger.addHandler(probe)
    try:
        # a child module logger must reach the handler at INFO
        logging.getLogger("ai_librarian.extraction").info("ops-breadcrumb-42")
    finally:
        service_logger.removeHandler(probe)
    assert "ops-breadcrumb-42" in sink.getvalue()


def test_restart_requeues_the_whole_library_without_failing_documents(service):
    module, client, _indexed = service
    doc_ids = []
    for number in range(8):
        upload = client.post(
            "/documents",
            files={"file": (f"book{number}.pdf", make_pdf(f"Absurd passage {number} about freedom."), "application/pdf")},
        )
        assert upload.status_code == 201
        doc_ids.append(upload.json()["document_id"])
    for doc_id in doc_ids:
        wait_for_status(client, doc_id, "indexed")

    # Simulate an index-schema bump followed by a restart: every document is
    # stale and must be re-embedded. The worker pulls them from the database, so
    # none may be marked "error" for lack of queue space.
    for doc_id in doc_ids:
        module.update_metadata(doc_id, index_schema_version=0, indexing_status="indexed")
    module.recover_interrupted_work()

    for doc_id in doc_ids:
        metadata = wait_for_status(client, doc_id, "indexed", timeout=5)
        assert metadata["indexing_error"] is None


def test_child_hits_are_coalesced_to_the_complete_parent_group(service):
    module, _client, _indexed = service
    hits = [
        {
            "id": "child-one",
            "score": 0.9,
            "payload": {
                "chunk_id": "child-one",
                "group_id": "parent",
                "document_id": "doc",
                "text": "Introduction\n\n$$x = 1$$\n\nExplanation",
                "embedding_text": "x = 1",
            },
        },
        {
            "id": "child-two",
            "score": 0.8,
            "payload": {
                "chunk_id": "child-two",
                "group_id": "parent",
                "document_id": "doc",
                "text": "Introduction\n\n$$x = 1$$\n\nExplanation",
                "embedding_text": "Explanation",
            },
        },
    ]

    grouped = module.coalesce_group_hits(hits)

    assert len(grouped) == 1
    assert grouped[0]["payload"]["text"].startswith("Introduction")
