"""The "Check with AI" button on one book: the AI sort's engine, run on a single file,
that only proposes. Nothing changes until the reader applies it, and applying can be undone."""

import json

import pytest

from test_ai_sort_job import HAPPY, TITLE_PAGE, _confirming, _doc, _upload, _wire


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
