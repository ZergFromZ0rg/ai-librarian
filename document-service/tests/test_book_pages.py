"""Book details survive indexing, and notes retain their original evidence."""
import io

from conftest import make_pdf, wait_for_status
from PIL import Image

from database import MetadataStore


def upload(client):
    response = client.post("/documents", files={"file": ("original.pdf", make_pdf("The absurd and freedom."), "application/pdf")})
    assert response.status_code == 201
    return wait_for_status(client, response.json()["document_id"], "indexed")


def test_metadata_corrections_survive_shelf_refresh_and_restart(service):
    module, client, _ = service
    doc = upload(client)
    doc_id = doc["document_id"]
    changes = {"title": " My book ", "author": "Reader's correction", "subject": "Philosophy"}
    response = client.patch(f"/documents/{doc_id}", json=changes)
    assert response.status_code == 200
    assert response.json()["title"] == "My book"
    assert response.json()["filename"] == "original.pdf"
    module.suggest_document_shelf(doc_id)
    reopened = MetadataStore(module.STORE._path)
    try:
        saved = reopened.get(doc_id)
        assert saved["author"] == "Reader's correction"
        assert saved["subject"] == "Philosophy"
        assert saved["title"] == "My book"
    finally:
        reopened.close()
    assert client.patch(f"/documents/{doc_id}", json={"title": "  "}).status_code == 422


def test_linked_notes_index_quote_preserve_source_and_filter(service):
    module, client, indexed = service
    doc = upload(client)
    doc_id = doc["document_id"]
    source = {"source_document_id": doc_id, "source_page": 1, "source_quote": "The absurd and freedom."}
    response = client.post("/notes", json={"text": "## My interpretation\nA useful idea.", **source})
    assert response.status_code == 201
    note_id = response.json()["document_id"]
    wait_for_status(client, note_id, "indexed")
    assert any("The absurd and freedom" in item["payload"]["text"] for item in indexed.values() if item["payload"]["document_id"] == note_id)
    notes = client.get("/notes", params={"source_document_id": doc_id}).json()["notes"]
    assert len(notes) == 1 and notes[0]["source_quote"] == source["source_quote"]
    assert notes[0]["text"] == "## My interpretation\nA useful idea."
    assert client.get("/notes", params={"source_document_id": "other"}).json()["notes"] == []
    updated = client.put(f"/notes/{note_id}", json={"text": "Revised thought"})
    assert updated.status_code == 200
    assert updated.json()["source_quote"] == source["source_quote"]
    assert updated.json()["source_page"] == 1
    wait_for_status(client, note_id, "indexed")
    # Removing a source must not discard the reader's work or its quotation.
    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert client.get("/notes").json()["notes"][0]["source_quote"] == source["source_quote"]


def test_invalid_note_sources_are_rejected(service):
    _, client, _ = service
    doc = upload(client)
    assert client.post("/notes", json={"text": "x", "source_page": 1}).status_code == 422
    assert client.post("/notes", json={"text": "x", "source_document_id": doc["document_id"], "source_page": 20}).status_code == 422
    assert client.post("/notes", json={"text": "x", "source_document_id": "a" * 32}).status_code == 404


def test_cover_upload_reset_and_original_file_preserved(service):
    module, client, _ = service
    doc = upload(client)
    doc_id = doc["document_id"]
    original = module.pdf_path(doc).read_bytes()
    image = io.BytesIO()
    Image.new("RGB", (1200, 1800), "red").save(image, "PNG")
    assert client.put(f"/documents/{doc_id}/cover", files={"file": ("cover.png", image.getvalue(), "image/png")}).status_code == 200
    cover = client.get(f"/documents/{doc_id}/thumbnail")
    assert cover.status_code == 200
    with Image.open(io.BytesIO(cover.content)) as saved:
        assert saved.size == (600, 900)
        assert saved.format == "JPEG"
    assert module.pdf_path(doc).read_bytes() == original
    assert client.put(f"/documents/{doc_id}/cover", files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 422
    assert client.get(f"/documents/{doc_id}/thumbnail").content == cover.content
    assert client.delete(f"/documents/{doc_id}/cover").status_code == 200
    assert client.get(f"/documents/{doc_id}/thumbnail").content != cover.content


def test_rating_review_and_ownership_persist_and_can_be_cleared(service):
    module, client, _ = service
    doc = upload(client)
    path = f"/documents/{doc['document_id']}"
    response = client.patch(path, json={"rating": 5, "review": "  A favourite.\nWorth returning to.  ", "owned": True})
    assert response.status_code == 200
    assert response.json()["rating"] == 5
    assert response.json()["review"] == "A favourite.\nWorth returning to."
    assert response.json()["owned"] == 1
    reopened = MetadataStore(module.STORE._path)
    try:
        assert reopened.get(doc["document_id"])["rating"] == 5
        assert reopened.get(doc["document_id"])["review"] == "A favourite.\nWorth returning to."
    finally:
        reopened.close()
    # Metadata corrections and shelf refresh cannot erase a reader's review.
    client.patch(path, json={"title": "Corrected title"})
    module.suggest_document_shelf(doc["document_id"])
    assert client.get(path).json()["rating"] == 5
    assert client.get(path).json()["file_type"] == "pdf"
    for invalid in (0, 6, 2.5, True, "5"):
        assert client.patch(path, json={"rating": invalid}).status_code == 422
    assert client.patch(path, json={"review": "x" * 20001}).status_code == 422
    response = client.patch(path, json={"rating": None, "review": "", "owned": False})
    assert response.json()["rating"] is None
    assert response.json()["review"] is None
    assert response.json()["owned"] == 0
