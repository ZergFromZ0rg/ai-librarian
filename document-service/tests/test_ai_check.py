"""The "Check with AI" button: the AI sort's engine run on one book, or on an upload waiting
for review. It only proposes. Nothing changes until the reader applies it, and a change applied
to a library book can be undone. A fake local model and a fake catalogue; the real engine,
endpoints and database."""

import json

import pytest

from conftest import make_pdf, wait_for_status

HAPPY = {
    "id": "ol:/works/OL1W", "provider": "openlibrary", "title": "A Happy Death", "author": "Albert Camus", "published_year": 1972,
    "publisher": "Knopf", "language": "en", "genres": ["Fiction"], "description": "A young man plans a perfect crime.", "score": 0.93,
}
# Indexing already completes a surname printed in full on the title page, so the pages the
# check works on name the author only by surname.
TITLE_PAGE = "A HAPPY DEATH\nCAMUS\nTranslated by Richard Howard"


def _upload(client, name, text=TITLE_PAGE):
    response = client.post("/documents", files={"file": (name, make_pdf(text), "application/pdf")})
    assert response.status_code == 201
    return wait_for_status(client, response.json()["document_id"], "indexed")


def _doc(client, doc_id):
    return client.get(f"/documents/{doc_id}").json()


def _wire(module, monkeypatch, *, models=("fake:1b",), candidates=(HAPPY,), reply=None):
    """A fake Ollama and a fake catalogue. `reply(prompt)` returns the model's JSON."""
    calls = {"model": [], "search": []}
    monkeypatch.setattr(module.ai_sort, "list_local_models", lambda timeout=3.0: list(models))

    def ask(model, user, **_kw):
        calls["model"].append(user)
        return reply(user) if reply else {}

    def search(query, limit=8):
        calls["search"].append(query)
        return [dict(item) for item in candidates]

    monkeypatch.setattr(module.ai_sort, "ask_local_model", ask)
    monkeypatch.setattr(module.catalogue, "search", search)
    return calls


def _confirming(_prompt):
    return {"title": "A Happy Death", "author": None, "same_work_as": 1, "is_fiction": True, "kind": "book",
            "confidence": 0.92, "quote": None}


def _check(client, doc_id, **body):
    return client.post(f"/documents/{doc_id}/ai-check", json={"online": True, **body})


def _fields(result):
    return {item["field"]: item for item in result["suggestions"]}


def _happy_death(service, monkeypatch, **wire):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming, **wire)
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    assert (doc["title"], doc["author"]) == ("A Hapy Death", "Camus")
    return module, client, doc


def test_a_check_proposes_every_change_and_writes_none_of_them(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    response = _check(client, doc["document_id"])
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    found = _fields(result)
    assert found["author"]["after"] == "Albert Camus" and found["title"]["after"] == "A Happy Death"
    assert "publisher" in found["details"]["after"] and found["details"]["source"] == "Open Library"
    assert result["applied"] == [] and result["model"] == "fake:1b" and "undo" not in json.dumps(result)

    after = _doc(client, doc["document_id"])
    for key in ("title", "author", "publisher", "published_year", "metadata_edited", "metadata_source", "shelf_suggested"):
        assert after[key] == doc[key], key  # nothing was written
    assert json.loads(after["ai_json"])["checked_at"]  # but the book is marked as checked

    again = client.get(f"/documents/{doc['document_id']}/ai-check").json()
    assert again["reason"] is None and _fields(again["result"]).keys() == found.keys()


def test_applying_the_proposals_changes_the_book_and_can_be_undone(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    original = _doc(client, doc["document_id"])
    result = _check(client, doc["document_id"]).json()["result"]
    ids = [item["id"] for item in result["suggestions"]]

    applied = client.post(f"/documents/{doc['document_id']}/ai-check/apply", json={"ids": ids})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["applied"] == len(ids) and body["failed"] == []
    assert (body["document"]["title"], body["document"]["author"]) == ("A Happy Death", "Albert Camus")
    assert body["document"]["publisher"] == "Knopf" and body["document"]["published_year"] == 1972
    assert body["result"]["suggestions"] == [] and len(body["result"]["applied"]) == len(ids)
    assert "undo" not in json.dumps(body["result"])

    undone = client.post(f"/documents/{doc['document_id']}/ai-check/undo")
    assert undone.status_code == 200 and undone.json()["restored"] == len(ids) and undone.json()["skipped"] == []
    restored = _doc(client, doc["document_id"])
    for key in ("title", "author", "publisher", "published_year", "genres_json", "metadata_source", "metadata_edited", "shelf_suggested", "book_form_override"):
        assert restored[key] == original[key], key
    assert all(item["undone"] for item in undone.json()["result"]["applied"])


def test_a_single_proposal_can_be_applied_and_another_dismissed(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    found = _fields(_check(client, doc["document_id"]).json()["result"])
    one = client.post(f"/documents/{doc['document_id']}/ai-check/apply", json={"ids": [found["author"]["id"]]}).json()
    assert one["document"]["author"] == "Albert Camus" and one["document"]["title"] == "A Hapy Death"
    gone = client.post(f"/documents/{doc['document_id']}/ai-check/dismiss", json={"ids": [found["title"]["id"]]}).json()
    assert gone["dismissed"] == 1 and "title" not in _fields(gone["result"])
    assert "details" in _fields(gone["result"])  # what was neither applied nor dismissed is still waiting


def test_a_book_the_reader_edited_is_still_checked_but_never_overwritten(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    client.patch(f"/documents/{doc['document_id']}", json={"author": "Camus", "title": "My own title"})
    result = _check(client, doc["document_id"]).json()["result"]
    found = _fields(result)
    assert found["author"]["after"] == "Albert Camus"  # offered ...
    after = _doc(client, doc["document_id"])
    assert (after["author"], after["title"]) == ("Camus", "My own title")  # ... not written
    assert "title" not in found  # the reader's own title is not second-guessed


def test_a_book_that_cannot_be_checked_says_why(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    doc_id = doc["document_id"]

    client.patch(f"/documents/{doc_id}", json={"kind": "paper"})
    refused = _check(client, doc_id)
    assert refused.status_code == 422 and "paper or document" in refused.json()["detail"]
    assert "paper or document" in client.get(f"/documents/{doc_id}/ai-check").json()["reason"]
    client.patch(f"/documents/{doc_id}", json={"kind": "book"})

    module.STORE.update(doc_id, {"indexing_status": "queued"})
    waiting = _check(client, doc_id)
    assert waiting.status_code == 422 and "not been read yet" in waiting.json()["detail"]

    owned = client.post("/owned-books", json={"title": "No file", "author": "Nobody"}).json()["book"]
    no_file = _check(client, owned["document_id"])
    assert no_file.status_code == 422 and "no file" in no_file.json()["detail"]


def test_with_no_model_and_no_catalogue_there_is_nothing_to_ask(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch, models=())
    refused = _check(client, doc["document_id"], online=False)
    assert refused.status_code == 422 and "nothing to ask" in refused.json()["detail"]


def test_a_model_that_does_not_answer_is_reported_not_trusted(service, monkeypatch):
    module, client, _ = service

    def down(_prompt):
        raise module.ai_sort.AiError("fake:1b: timed out")

    _wire(module, monkeypatch, candidates=(), reply=down)
    doc = _upload(client, "A Hapy Death.pdf", "A HAPPY DEATH\nALBERT CAMUS")
    result = _check(client, doc["document_id"], online=False).json()["result"]
    assert result["suggestions"] == [] and result["stats"]["model_failures"] == 1
    assert any("did not answer" in note for note in result["notes"])


def test_a_fiction_verdict_is_offered_as_the_books_form_not_stored_behind_its_back(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, candidates=(), reply=lambda _p: {
        "title": None, "author": None, "same_work_as": None, "is_fiction": True, "kind": "book", "confidence": 0.9, "quote": None,
    })
    doc = _upload(client, "Pedro Paramo - Rulfo.pdf", "\n".join(["El hombre dijo que ella estaba cansada."] * 30))
    doc_id = doc["document_id"]
    module.STORE.update(doc_id, {"kind": "book", "pages": 99, "prose_score": None, "text_stats": '{"words": 36000, "english": false}'})
    module.STORE.update(doc_id, module._form_fields(module.read_metadata(doc_id)))
    assert _doc(client, doc_id)["book_form"] is None

    found = _fields(_check(client, doc_id, online=False).json()["result"])
    assert found["form"]["after"] == "novella" and found["form"]["patch"] == {"form": "novella"}
    after = _doc(client, doc_id)
    assert after["book_form"] is None and after["book_form_override"] is None
    assert "fiction" not in json.loads(after["ai_json"])  # the verdict itself was not kept

    applied = client.post(f"/documents/{doc_id}/ai-check/apply", json={"ids": [found["form"]["id"]]}).json()
    assert applied["document"]["book_form_override"] == "novella"
    client.post(f"/documents/{doc_id}/ai-check/undo")
    assert _doc(client, doc_id)["book_form_override"] is None


def test_a_second_check_keeps_the_first_ones_changes_undoable(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    doc_id = doc["document_id"]
    first = _fields(_check(client, doc_id).json()["result"])
    client.post(f"/documents/{doc_id}/ai-check/apply", json={"ids": [first["author"]["id"]]})
    second = _check(client, doc_id).json()["result"]
    assert len(second["applied"]) == 1 and "author" not in _fields(second)  # already applied, so not proposed again
    undone = client.post(f"/documents/{doc_id}/ai-check/undo").json()
    assert undone["restored"] == 1 and undone["document"]["author"] == "Camus"


def test_a_book_never_checked_has_no_result_and_nothing_to_apply(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    assert client.get(f"/documents/{doc['document_id']}/ai-check").json() == {"reason": None, "result": None}
    for path, body in (("apply", {"ids": ["x"]}), ("dismiss", {"ids": ["x"]}), ("undo", None)):
        assert client.post(f"/documents/{doc['document_id']}/ai-check/{path}", json=body).status_code == 404
    assert client.get("/documents/abcdef012345/ai-check").status_code == 404


def test_deleting_a_book_removes_its_kept_result(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch)
    _check(client, doc["document_id"])
    kept = module._ai_check_path(doc["document_id"])
    assert kept.exists()
    assert client.delete(f"/documents/{doc['document_id']}").status_code == 200
    assert not kept.exists()


def test_the_models_endpoint_lists_the_local_models_and_whether_the_catalogue_is_allowed(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, models=("qwen2.5:7b", "llama3.2:3b"))
    info = client.get("/ai-check/models").json()
    assert info["models"] == ["qwen2.5:7b", "llama3.2:3b"] and info["default"] == "qwen2.5:7b" and info["online_allowed"] is True
    assert client.get("/library/ai-sort").status_code in {404, 405}  # the library-wide sort is gone


def test_without_a_local_model_the_catalogue_alone_still_checks(service, monkeypatch):
    module, client, doc = _happy_death(service, monkeypatch, models=())
    result = _check(client, doc["document_id"]).json()["result"]
    assert result["model"] is None and result["stats"]["model_calls"] == 0
    assert _fields(result)["details"]["source"] == "Open Library"  # a strict catalogue match is enough for details


def test_an_author_the_model_names_takes_the_spelling_the_library_already_uses(service, monkeypatch):
    module, client, _ = service
    quote = "JOHN RONALD REUEL TOLKIEN"
    _wire(module, monkeypatch, candidates=(), reply=lambda _p: {"title": None, "author": "John Ronald Reuel Tolkien", "same_work_as": None,
                                                                "is_fiction": None, "kind": None, "confidence": 0.9, "quote": quote})
    known = _upload(client, "The Hobbit - J. R. R. Tolkien.pdf", "THE HOBBIT\nJ. R. R. TOLKIEN")
    assert known["author"] == "J. R. R. Tolkien"
    other = _upload(client, "Tom Bombadil.pdf", f"THE ADVENTURES OF TOM BOMBADIL\n{quote}")
    found = _fields(_check(client, other["document_id"], online=False).json()["result"])
    assert found["author"]["after"] == "J. R. R. Tolkien"  # one spelling per person


# ------------------------------------------------- an upload waiting for review --
def _stage(client, name="A Hapy Death - Camus.pdf", text=TITLE_PAGE):
    response = client.post("/intake", files={"file": (name, make_pdf(text), "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()


def _form(item, **changes):
    """The review form as the screen would send it: the proposal, with edits."""
    p = item["proposal"]
    form = {key: p[key] for key in ("title", "subtitle", "author", "subject", "description", "publisher", "language", "isbn_10",
                                    "isbn_13", "published_year", "page_count", "genres", "type")}
    form["type"] = "book"  # a one-page sample is proposed as a document; the reader has set it to Book
    form.update(changes)
    return form


def _staged_check(client, item, **changes):
    return client.post(f"/intake/{item['id']}/ai-check", json={"online": True, **_form(item, **changes)})


def test_an_upload_waiting_for_review_can_be_checked_and_nothing_is_recorded(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming)
    item = _stage(client)
    assert (item["proposal"]["title"], item["proposal"]["author"]) == ("A Hapy Death", "Camus")

    response = _staged_check(client, item)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    found = _fields(result)
    assert found["author"]["after"] == "Albert Camus" and found["title"]["after"] == "A Happy Death"
    assert found["details"]["patch"]["publisher"] == "Knopf" and found["details"]["patch"]["published_year"] == 1972
    assert result["applied"] == [] and "undo" not in json.dumps(result)

    assert module.list_metadata() == []  # no library record exists to write to
    assert not module.AI_CHECK_DIR.exists() or not list(module.AI_CHECK_DIR.iterdir())  # and no kept result
    assert client.get(f"/intake/{item['id']}").json()["proposal"] == item["proposal"]  # the staged proposal is untouched


def test_what_the_reader_already_filled_in_is_not_proposed_again(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming)
    item = _stage(client)
    found = _fields(_staged_check(client, item, publisher="My Press", published_year=1999, language="fr", genres=["Essays"]).json()["result"])
    patch = found["details"]["patch"]
    for filled in ("publisher", "published_year", "language", "genres"):
        assert filled not in patch  # the form already has them
    assert "description" in patch  # what was empty is still offered


def test_a_title_the_reader_changed_on_the_form_is_not_second_guessed(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming)
    item = _stage(client)
    found = _fields(_staged_check(client, item, title="My own title").json()["result"])
    assert "title" not in found and found["author"]["after"] == "Albert Camus"


def test_an_upload_that_cannot_be_checked_says_why(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming)
    item = _stage(client)
    refused = _staged_check(client, item, type="manual")
    assert refused.status_code == 422 and "for books" in refused.json()["detail"]

    note = client.post("/intake", files={"file": ("notes.md", b"# Notes\n\nSome words here.\n" * 5, "text/markdown")}).json()
    refused = _staged_check(client, note)
    assert refused.status_code == 422 and "PDF and EPUB" in refused.json()["detail"]

    broken = client.post("/intake", files={"file": ("Broken.pdf", b"%PDF-1.4 not really", "application/pdf")}).json()
    refused = _staged_check(client, broken)
    assert refused.status_code == 422 and "could not be read" in refused.json()["detail"]

    monkeypatch.setattr(module.ai_sort, "list_local_models", lambda timeout=3.0: [])
    nothing = client.post(f"/intake/{item['id']}/ai-check", json={"online": False, **_form(item)})
    assert nothing.status_code == 422 and "nothing to ask" in nothing.json()["detail"]
    assert client.post("/intake/abcdef012345/ai-check", json={"online": True, "title": "x"}).status_code == 404
