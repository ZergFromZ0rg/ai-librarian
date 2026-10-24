from conftest import make_pdf, wait_for_status


def _book(client, title="Dune", author="Frank Herbert", **extra):
    response = client.post("/owned-books", json={"title": title, "author": author, **extra})
    assert response.status_code == 201
    return response.json()["book"]["document_id"]


def _attach(client, doc_id, content=None, name="dune.pdf"):
    content = content if content is not None else make_pdf("Dune\nFear is the mind-killer.", pages=2)
    return client.post(f"/documents/{doc_id}/file", files={"file": (name, content, "application/pdf")})


def test_a_catalogue_book_keeps_everything_when_it_gets_its_file(service):
    """Attaching must not make a second record or lose what the reader added."""
    _module, client, _indexed = service
    doc_id = _book(client, notes="Slow start, worth it.")
    assert client.patch(f"/documents/{doc_id}", json={"rating": 4.5, "reading_status": "reading", "liked": True}).status_code == 200
    before = len(client.get("/documents").json()["documents"])

    response = _attach(client, doc_id)
    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == doc_id
    assert body["record_type"] == "source"
    assert body["file_type"] == "pdf"
    assert body["filename"] == "dune.pdf"

    done = wait_for_status(client, doc_id, "indexed")
    assert (done["title"], done["author"]) == ("Dune", "Frank Herbert")
    assert done["review"] == "Slow start, worth it."
    assert done["rating"] == 4.5 and done["liked"] and done["reading_status"] == "reading"
    assert done["pages"] == 2 and done["chunks"] >= 1
    assert len(client.get("/documents").json()["documents"]) == before
    # The file can now be read, which a catalogue record could not.
    assert client.get(f"/documents/{doc_id}/file").status_code == 200


def test_a_book_that_already_has_a_file_cannot_be_given_another(service):
    _module, client, _indexed = service
    doc_id = _book(client)
    assert _attach(client, doc_id).status_code == 200
    wait_for_status(client, doc_id, "indexed")
    again = _attach(client, doc_id, make_pdf("something else entirely"))
    assert again.status_code == 409
    assert "already has a file" in again.json()["detail"]


def test_a_file_already_in_the_library_is_refused_and_not_kept(service):
    module, client, _indexed = service
    pdf = make_pdf("Same bytes in two places")
    first = client.post("/documents", files={"file": ("first.pdf", pdf, "application/pdf")}).json()
    wait_for_status(client, first["document_id"], "indexed")
    doc_id = _book(client, title="Another")

    response = _attach(client, doc_id, pdf, name="again.pdf")
    assert response.status_code == 409
    assert "already in your library" in response.json()["detail"]
    # Still a catalogue book, and the refused upload left nothing on disk.
    assert client.get(f"/documents/{doc_id}").json()["record_type"] == "standalone"
    assert [path.name for path in module.DOCUMENTS_DIR.iterdir()] == [f"{first['document_id']}.pdf"]


def test_attaching_checks_the_file_and_the_book(service):
    _module, client, _indexed = service
    doc_id = _book(client)
    assert _attach(client, doc_id, b"MZ not a book", name="setup.exe").status_code == 400
    assert _attach(client, doc_id, b"", name="empty.pdf").status_code == 400
    assert _attach(client, "0123456789ab").status_code == 404
    assert client.get(f"/documents/{doc_id}").json()["record_type"] == "standalone"
