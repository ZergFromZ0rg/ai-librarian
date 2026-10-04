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


def test_richer_metadata_and_reading_lifecycle(service):
    _, client, _ = service
    doc = upload(client)
    path = f"/documents/{doc['document_id']}"
    metadata = {
        "subtitle": "A reader's edition",
        "description": "A concise catalogue description.",
        "publisher": "Example Press",
        "published_year": 2024,
        "page_count": 312,
        "language": "EN",
        "isbn_10": "0-306-40615-2",
        "isbn_13": "978-0-306-40615-7",
        "genres": ["Philosophy", "Essays", "philosophy"],
        "acquisition_source": "second_hand",
        "reading_status": "reading",
        "started_at": "2026-09-01",
    }
    response = client.patch(path, json=metadata)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["isbn_10"] == "0306406152"
    assert saved["isbn_13"] == "9780306406157"
    assert saved["language"] == "en"
    assert saved["genres_json"] == '["Philosophy", "Essays"]'
    assert saved["reading_status"] == "reading"
    assert saved["started_at"] == "2026-09-01"
    assert saved["finished_at"] is None and saved["read_at"] is None

    finished = client.patch(
        path,
        json={"reading_status": "read", "finished_at": "2026-10-02"},
    )
    assert finished.status_code == 200
    assert finished.json()["finished_at"] == "2026-10-02"
    assert finished.json()["read_at"].startswith("2026-10-02")

    rereading = client.patch(path, json={"reading_status": "reading"}).json()
    assert rereading["finished_at"] is None and rereading["read_at"] is None
    assert rereading["started_at"] == "2026-09-01"
    cleared = client.patch(path, json={"language": "", "acquisition_source": ""})
    assert cleared.status_code == 200
    assert cleared.json()["language"] is None
    assert cleared.json()["acquisition_source"] is None


def test_richer_metadata_validation(service):
    _, client, _ = service
    doc = upload(client)
    path = f"/documents/{doc['document_id']}"
    invalid = [
        {"isbn_10": "0306406153"},
        {"isbn_13": "9780306406158"},
        {"published_year": "2024"},
        {"page_count": 0},
        {"language": "not a language"},
        {"acquisition_source": "somewhere"},
        {"reading_status": "paused"},
        {"started_at": "2026-02-30"},
    ]
    for payload in invalid:
        assert client.patch(path, json=payload).status_code == 422, payload
    assert client.patch(
        path,
        json={
            "reading_status": "read",
            "started_at": "2026-10-02",
            "finished_at": "2026-09-01",
        },
    ).status_code == 422


def test_book_without_a_file_uses_the_main_catalogue(service):
    module, client, _ = service
    created = client.post(
        "/owned-books",
        json={
            "title": "The Dispossessed",
            "author": "Ursula K. Le Guin",
            "notes": "Start with the paperback edition.",
            "pdf_less": True,
        },
    )
    assert created.status_code == 201
    book = created.json()["book"]
    book_id = book["document_id"]
    assert book["book_id"] == book_id
    assert book["record_type"] == "standalone"
    assert book["indexing_status"] == "catalogued"
    assert book["owned"] == 1
    assert book["review"] == "Start with the paperback edition."

    documents = client.get("/documents").json()["documents"]
    assert next(item for item in documents if item["document_id"] == book_id)["title"] == "The Dispossessed"
    assert client.get(f"/documents/{book_id}/file").status_code == 404
    assert client.post(f"/documents/{book_id}/retry").status_code == 409

    updated = client.patch(
        f"/documents/{book_id}",
        json={"rating": 5, "reading_status": "reading", "genres": ["Science Fiction"]},
    )
    assert updated.status_code == 200
    assert updated.json()["rating"] == 5
    assert updated.json()["reading_status"] == "reading"

    note = client.post(
        "/notes",
        json={"text": "Compare its two social systems.", "source_document_id": book_id},
    )
    assert note.status_code == 201
    linked = client.get("/notes", params={"source_document_id": book_id}).json()["notes"]
    assert linked[0]["text"] == "Compare its two social systems."

    assert client.delete(f"/documents/{book_id}").status_code == 200
    assert client.get(f"/documents/{book_id}").status_code == 404
    reopened = MetadataStore(module.STORE._path)
    try:
        assert reopened.get(book_id) is None
    finally:
        reopened.close()
