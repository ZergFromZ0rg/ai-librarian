"""The AI sort through the API: a fake local model and a fake catalogue, the real
job, the real database. What matters is what it changes, what it refuses to, and
that everything it did can be taken back."""

import time

from conftest import make_pdf, wait_for_status

HAPPY = {
    "id": "ol:/works/OL1W", "provider": "openlibrary", "title": "A Happy Death", "author": "Albert Camus", "published_year": 1972,
    "publisher": "Knopf", "language": "en", "genres": ["Fiction"], "description": "A young man plans a perfect crime.", "score": 0.93,
}
# Indexing already completes a surname printed in full on the title page, so the
# pages the sort has to work on name the author only by surname -- or, for the
# suggestion tests, in full but with no author in the file name.
TITLE_PAGE = "A HAPPY DEATH\nCAMUS\nTranslated by Richard Howard"
FULL_TITLE_PAGE = "A HAPPY DEATH\nALBERT CAMUS\nTranslated by Richard Howard"


def _upload(client, name, text=TITLE_PAGE):
    response = client.post("/documents", files={"file": (name, make_pdf(text), "application/pdf")})
    assert response.status_code == 201
    return wait_for_status(client, response.json()["document_id"], "indexed")


def _doc(client, doc_id):
    return client.get(f"/documents/{doc_id}").json()


def _sort(client, **body):
    body = {"online": True, "include_checked": False, **body}
    started = client.post("/library/ai-sort", json=body)
    assert started.status_code == 202, started.text
    deadline = time.time() + 30
    while time.time() < deadline:
        report = client.get("/library/ai-sort").json()
        if report["state"] != "running":
            return report
        time.sleep(0.05)
    raise AssertionError("the sort did not finish")


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


def test_the_models_endpoint_lists_the_local_models_and_whether_the_catalogue_is_allowed(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, models=("qwen2.5:7b", "llama3.2:3b"))
    info = client.get("/library/ai-sort/models").json()
    assert info["models"] == ["qwen2.5:7b", "llama3.2:3b"] and info["default"] == "qwen2.5:7b" and info["online_allowed"] is True


def test_a_confirmed_match_fixes_the_title_the_author_and_fills_in_the_details(service, monkeypatch):
    module, client, _ = service
    calls = _wire(module, monkeypatch, reply=_confirming)
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    assert (doc["title"], doc["author"]) == ("A Hapy Death", "Camus")

    report = _sort(client)
    assert report["state"] == "done" and report["model"] == "fake:1b"
    after = _doc(client, doc["document_id"])
    assert (after["title"], after["author"]) == ("A Happy Death", "Albert Camus")
    assert after["publisher"] == "Knopf" and after["published_year"] == 1972 and "Fiction" in (after["genres_json"] or "")
    assert after["metadata_source"] == "Open Library" and after["metadata_edited"] == 1
    entry = next(item for item in report["applied"] if item["document_id"] == doc["document_id"])
    fields = {change["field"] for change in entry["changes"]}
    assert {"title", "author", "details"} <= fields and "undo" not in entry  # the undo data stays on the server
    assert len(calls["model"]) == 1 and "CAMUS" in calls["model"][0] and "1. A Happy Death" in calls["model"][0]
    assert report["stats"]["model_calls"] == 1 and report["stats"]["lookups"] == 1


def test_everything_the_sort_did_can_be_undone(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming)
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    original = _doc(client, doc["document_id"])
    _sort(client)
    assert _doc(client, doc["document_id"])["title"] == "A Happy Death"

    undone = client.post("/library/ai-sort/undo").json()
    assert undone["restored"] >= 1 and undone["skipped"] == []
    restored = _doc(client, doc["document_id"])
    for key in ("title", "author", "publisher", "published_year", "genres_json", "metadata_source", "metadata_edited", "shelf_suggested", "book_form"):
        assert restored[key] == original[key], key
    assert client.get("/library/ai-sort").json()["state"] == "undone"


def test_a_book_changed_since_the_sort_is_not_rolled_back_over_the_readers_edit(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming)
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    _sort(client)
    client.patch(f"/documents/{doc['document_id']}", json={"author": "A. Camus (my edit)"})
    undone = client.post("/library/ai-sort/undo").json()
    assert undone["restored"] == 0 and "changed since" in undone["skipped"][0]
    assert _doc(client, doc["document_id"])["author"] == "A. Camus (my edit)"


def test_a_model_that_invents_an_author_changes_nothing(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=lambda _p: {"title": "A Happy Death", "author": "Jean-Paul Sartre", "same_work_as": None,
                                                 "is_fiction": None, "kind": None, "confidence": 0.99, "quote": "Jean-Paul Sartre"})
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    report = _sort(client)
    after = _doc(client, doc["document_id"])
    assert after["author"] == "Camus"  # not Sartre, and not completed on one weak source either
    assert not any("Sartre" in str(item) for item in report["applied"] + report["suggestions"])


def test_the_reader_edits_are_never_touched(service, monkeypatch):
    module, client, _ = service
    calls = _wire(module, monkeypatch, reply=_confirming)
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    client.patch(f"/documents/{doc['document_id']}", json={"title": "My copy of A Hapy Death"})
    report = _sort(client)
    after = _doc(client, doc["document_id"])
    assert after["title"] == "My copy of A Hapy Death" and after["author"] == "Camus"
    assert report["skipped"].get("edited by you") == 1 and calls["model"] == []


def test_a_model_alone_only_suggests_and_the_reader_decides(service, monkeypatch):
    module, client, _ = service
    # No catalogue match; the model reads the title page but is not quoting it.
    _wire(module, monkeypatch, candidates=(), reply=lambda _p: {"title": "A Happy Death", "author": "Albert Camus", "same_work_as": None,
                                                                "is_fiction": None, "kind": None, "confidence": 0.9, "quote": None})
    doc = _upload(client, "A Hapy Death.pdf", FULL_TITLE_PAGE)
    report = _sort(client, online=False)
    after = _doc(client, doc["document_id"])
    assert (after["title"], after["author"]) == ("A Hapy Death", None)  # nothing applied by itself
    suggested = {item["field"]: item for item in report["suggestions"] if item["document_id"] == doc["document_id"]}
    assert suggested["author"]["after"] == "Albert Camus" and suggested["title"]["after"] == "A Happy Death"

    applied = client.post("/library/ai-sort/apply", json={"ids": [suggested["author"]["id"], suggested["title"]["id"]]}).json()
    assert applied["applied"] == 2 and applied["failed"] == []
    after = _doc(client, doc["document_id"])
    assert (after["title"], after["author"]) == ("A Happy Death", "Albert Camus")
    state = client.get("/library/ai-sort").json()
    assert state["suggestions"] == [] and {c["field"] for item in state["applied"] if item["via"] == "review" for c in item["changes"]} == {"author", "title"}
    client.post("/library/ai-sort/undo")
    assert _doc(client, doc["document_id"])["title"] == "A Hapy Death"


def test_a_suggestion_can_be_dismissed(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, candidates=(), reply=lambda _p: {"author": "Albert Camus", "confidence": 0.9, "quote": None, "title": None,
                                                                "same_work_as": None, "is_fiction": None, "kind": None})
    doc = _upload(client, "A Hapy Death.pdf", FULL_TITLE_PAGE)
    report = _sort(client, online=False)
    ids = [item["id"] for item in report["suggestions"] if item["document_id"] == doc["document_id"]]
    assert ids and client.post("/library/ai-sort/dismiss", json={"ids": ids}).json()["dismissed"] == len(ids)
    assert _doc(client, doc["document_id"])["author"] is None


def test_without_a_local_model_the_rules_and_a_strict_catalogue_match_still_work(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, models=())
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    report = _sort(client)
    assert report["model"] is None and any("No local Ollama model" in note for note in report["notes"])
    after = _doc(client, doc["document_id"])
    # The strict check confirms a lone match with a clear lead whose title is ours with a typo.
    assert (after["title"], after["author"]) == ("A Happy Death", "Albert Camus")
    entry = next(item for item in report["applied"] if item["document_id"] == doc["document_id"])
    assert any("rules" in change["source"] for change in entry["changes"] if change["field"] == "author")


def test_a_model_that_keeps_failing_is_dropped_for_the_rest_of_the_run(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch)

    def broken(model, user, **_kw):
        raise module.ai_sort.AiError("connection refused")

    monkeypatch.setattr(module.ai_sort, "ask_local_model", broken)
    for number in range(5):
        _upload(client, f"Book {number} - Writer.pdf", f"BOOK {number}\nWRITER")
    report = _sort(client, online=False)
    assert report["stats"]["model_failures"] == 3 and any("stopped answering" in note for note in report["notes"])
    assert report["state"] == "done" and report["done"] == report["total"]


def test_a_second_run_skips_what_was_already_checked_unless_asked(service, monkeypatch):
    module, client, _ = service
    calls = _wire(module, monkeypatch, reply=_confirming)
    _upload(client, "A Hapy Death - Camus.pdf")
    _sort(client)
    asked = len(calls["model"])
    second = _sort(client)
    assert second["total"] == 0 and second["skipped"].get("already checked") == 1 and len(calls["model"]) == asked
    third = _sort(client, include_checked=True)
    assert third["total"] == 1 and len(calls["model"]) == asked + 1


def test_only_one_sort_runs_at_a_time_and_it_can_be_cancelled(service, monkeypatch):
    module, client, _ = service
    import threading
    gate = threading.Event()
    _wire(module, monkeypatch, reply=lambda _p: (gate.wait(5) and None) or {})
    for number in range(3):
        _upload(client, f"Slow {number} - Writer.pdf", f"SLOW {number}\nWRITER")
    assert client.post("/library/ai-sort", json={"online": False}).status_code == 202
    assert client.post("/library/ai-sort", json={"online": False}).status_code == 409
    assert client.post("/library/ai-sort/undo").status_code == 409  # not while it is running
    client.post("/library/ai-sort/cancel")
    gate.set()
    deadline = time.time() + 20
    while time.time() < deadline and client.get("/library/ai-sort").json()["state"] == "running":
        time.sleep(0.05)
    assert client.get("/library/ai-sort").json()["state"] == "cancelled"


def test_the_report_survives_a_restart_and_a_cut_off_run_shows_as_interrupted(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, reply=_confirming)
    _upload(client, "A Hapy Death - Camus.pdf")
    _sort(client)
    saved = module.read_json(module.AI_SORT_FILE)
    assert saved["state"] == "done" and saved["applied"]
    # A run killed mid-way leaves "running" on disk; the next process reports it honestly.
    module.atomic_write_json(module.AI_SORT_FILE, {**saved, "state": "running"})
    module._AI_SORT_REPORT.clear()
    assert client.get("/library/ai-sort").json()["state"] == "interrupted"


def test_a_models_fiction_verdict_settles_a_book_the_text_could_not(service, monkeypatch):
    module, client, _ = service
    _wire(module, monkeypatch, candidates=(), reply=lambda _p: {"title": None, "author": None, "same_work_as": None, "is_fiction": True,
                                                                "kind": "book", "confidence": 0.9, "quote": None})
    doc = _upload(client, "Pedro Paramo - Rulfo.pdf", "\n".join(["El hombre dijo que ella estaba cansada."] * 30))
    module.STORE.update(doc["document_id"], {"kind": "book", "pages": 99, "prose_score": None, "text_stats": '{"words": 36000, "english": false}'})
    module.STORE.update(doc["document_id"], module._form_fields(module.read_metadata(doc["document_id"])))
    assert _doc(client, doc["document_id"])["book_form"] is None
    report = _sort(client, online=False)
    after = _doc(client, doc["document_id"])
    assert after["book_form"] == "novella"  # fiction, ~124 pages of words
    assert any(c["field"] == "fiction" for item in report["applied"] for c in item["changes"])
