"""Virtual shelves, jotted notes and idle unloading, through the API.

The service fixture's fake embedder scores "absurd" on its first axis, and the
Philosophy subject description mentions "the absurd" -- so a PDF about the
absurd really is classified as Philosophy here, end to end.
"""

import time

from conftest import make_pdf, wait_for_status


def _upload(client, name, text):
    response = client.post("/documents", files={"file": (name, make_pdf(text), "application/pdf")})
    assert response.status_code == 201
    return wait_for_status(client, response.json()["document_id"], "indexed")


def _doc(client, doc_id):
    return client.get(f"/documents/{doc_id}").json()


def test_indexed_document_is_suggested_a_shelf_the_reader_can_change(service):
    _module, client, _indexed = service
    doc = _upload(client, "sisyphus.pdf", "The absurd hero. The absurd man. One must imagine the absurd.")
    doc = _doc(client, doc["document_id"])
    assert doc["subject"] == "Philosophy"
    assert doc["shelf_suggested"] == "Documents/Philosophy"
    assert doc["shelf"] is None

    # A kind correction moves the suggestion up to the right top level.
    patched = client.patch(f"/documents/{doc['document_id']}", json={"kind": "book"}).json()
    assert patched["shelf_suggested"] == "Books/Philosophy"

    patched = client.patch(f"/documents/{doc['document_id']}", json={"shelf": " Books › Favourites / Camus "}).json()
    assert patched["shelf"] == "Books/Favourites/Camus"
    assert patched["shelf_suggested"] == "Books/Philosophy"  # kept, but the reader's choice wins

    assert client.patch(f"/documents/{doc['document_id']}", json={"shelf": None}).json()["shelf"] is None
    too_deep = client.patch(f"/documents/{doc['document_id']}", json={"shelf": "/".join("abcdefg")})
    assert too_deep.status_code == 422


def test_accepting_and_moving_shelves_and_searching_one(service):
    _module, client, _indexed = service
    first = _upload(client, "one.pdf", "The absurd, the absurd and freedom.")
    second = _upload(client, "two.pdf", "Absurd reasoning about the absurd.")
    other = _upload(client, "three.pdf", "Freedom and nothing else at all here.")

    client.patch(f"/documents/{other['document_id']}", json={"shelf": "Keep/Here"})
    accepted = client.post("/shelves/accept", json={"document_ids": [first["document_id"]]}).json()
    assert accepted == {"accepted": 1}
    assert _doc(client, first["document_id"])["shelf"] == "Documents/Philosophy"
    assert client.post("/shelves/accept", json={}).json()["accepted"] == 1  # the rest; `Keep` untouched
    assert _doc(client, other["document_id"])["shelf"] == "Keep/Here"

    moved = client.post("/shelves/move", json={"source": "Documents/Philosophy", "target": "Reading/Existentialism"})
    assert moved.json() == {"moved": 2, "shelf": "Reading/Existentialism"}
    assert _doc(client, second["document_id"])["shelf"] == "Reading/Existentialism"
    inside = client.post("/shelves/move", json={"source": "Reading", "target": "Reading/Deeper"})
    assert inside.status_code == 422

    def search(shelf):
        body = {"query": "absurd freedom", "top_k": 10, "shelf": shelf}
        return {hit["document_id"] for hit in client.post("/search", json=body).json()["results"]}

    assert search("Reading") == {first["document_id"], second["document_id"]}
    assert search("Keep") == {other["document_id"]}
    assert search("Nowhere") == set()


def test_notes_are_jotted_listed_scoped_edited_and_deleted(service):
    module, client, _indexed = service
    book = _upload(client, "book.pdf", "A book about freedom and the absurd.")
    created = client.post("/notes", json={"text": "Idea: the absurd hero keeps pushing\nmore thoughts later"})
    assert created.status_code == 201
    note = created.json()
    assert note["title"] == "Idea: the absurd hero keeps pushing"
    wait_for_status(client, note["document_id"], "indexed")

    listed = client.get("/notes").json()["notes"]
    assert [n["document_id"] for n in listed] == [note["document_id"]]
    assert listed[0]["text"].startswith("Idea: the absurd hero")
    assert _doc(client, note["document_id"])["shelf_suggested"] == "Notes"

    libraries = {c["id"]: c for c in client.get("/collections").json()["collections"]}
    assert libraries["notes"]["builtin"] and libraries["notes"]["document_count"] == 1
    assert "notes" not in {c["id"] for c in module.configured_collections()}  # never scanned

    def search(**scope):
        body = {"query": "absurd", "top_k": 10, **scope}
        return {hit["document_id"] for hit in client.post("/search", json=body).json()["results"]}

    assert search(collection_id="notes") == {note["document_id"]}
    assert book["document_id"] in search()

    edited = client.put(f"/notes/{note['document_id']}", json={"text": "Rewritten: freedom only"})
    assert edited.status_code == 200 and edited.json()["title"] == "Rewritten: freedom only"
    wait_for_status(client, note["document_id"], "indexed")
    assert client.get("/notes").json()["notes"][0]["text"].strip() == "Rewritten: freedom only"

    assert client.put(f"/notes/{book['document_id']}", json={"text": "x"}).status_code == 404
    assert client.post("/notes", json={"text": "   "}).status_code == 422
    assert client.delete(f"/documents/{note['document_id']}").status_code in (200, 204)
    assert client.get("/notes").json()["notes"] == []


def test_models_unload_after_idle_and_extraction_starts_lazily(service):
    module, client, _indexed = service
    import embeddings
    import reranker

    for model_module in (embeddings, reranker):
        model_module._model, model_module._model_name = object(), "fake"
        model_module._last_used = time.monotonic()
        assert not model_module.unload_if_idle(60)  # just used
        model_module._last_used = time.monotonic() - 120
        assert model_module.unload_if_idle(60)
        assert model_module._model is None and not model_module.unload_if_idle(60)

    # No extraction worker processes exist until there is something to extract.
    assert module.EXTRACTION_EXECUTOR is None
    _upload(client, "lazy.pdf", "Freedom.")
    assert module.EXTRACTION_EXECUTOR is not None
