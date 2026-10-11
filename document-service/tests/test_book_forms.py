"""Books, papers and documents, and a book's form, through the API: what is
stored when a file is indexed, what the startup backfill re-reads, and how a
reader's correction or an edit to the book's details moves the form."""

from conftest import make_pdf, wait_for_status

FICTION = "She said that he was late and they had not asked her why it was so, but he would not say. " * 40
ESSAY = "The will is the thing in itself and representation is its appearance, which we must examine with care. " * 40


def _upload(client, name, text="x"):
    response = client.post("/documents", files={"file": (name, make_pdf(text), "application/pdf")})
    assert response.status_code == 201
    return wait_for_status(client, response.json()["document_id"], "indexed")


def _doc(client, doc_id):
    return client.get(f"/documents/{doc_id}").json()


def _as_pages(module, doc_id, text, count):
    """Pretend extraction found `count` pages of `text` (the test PDFs are tiny)."""
    path = module.EXTRACTED_DIR / f"{doc_id}.json"
    extracted = module.read_json(path)
    extracted["pages"] = [{"page": number + 1, "text": text} for number in range(8)]
    module.atomic_write_json(path, extracted)
    module.STORE.update(doc_id, {"pages": count})


def _reclassify(module, doc_id):
    module.STORE.update(doc_id, {"kind": None, "kind_rules": 1, "book_form": None})
    module.backfill_document_kinds()


def test_a_short_novel_is_a_book_and_a_short_story_and_lands_on_the_books_shelf(service):
    module, client, _indexed = service
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    assert _doc(client, doc["document_id"])["kind"] == "document"  # one tiny page: nothing says book
    _as_pages(module, doc["document_id"], FICTION, 56)
    _reclassify(module, doc["document_id"])

    book = _doc(client, doc["document_id"])
    assert (book["kind"], book["book_form"], book["kind_rules"]) == ("book", "short_story", module.doc_kind.RULES_VERSION)
    assert book["prose_score"] > 30
    assert book["shelf_suggested"].startswith("Books/") and book["shelf_suggested"].endswith("Camus")
    assert book["doc_type"] is None  # a book has no working-document type


def test_the_same_length_of_essay_is_a_document_unless_someone_is_credited(service):
    module, client, _indexed = service
    anonymous = _upload(client, "report-draft.pdf")
    credited = _upload(client, "The Rebel - Camus.pdf")
    for doc in (anonymous, credited):
        _as_pages(module, doc["document_id"], ESSAY, 112)
        _reclassify(module, doc["document_id"])
    assert _doc(client, anonymous["document_id"])["kind"] == "document"
    rebel = _doc(client, credited["document_id"])
    assert (rebel["kind"], rebel["book_form"]) == ("book", "nonfiction")


def test_a_novel_over_150_pages_and_an_anthology_by_several_authors(service):
    module, client, _indexed = service
    novel = _upload(client, "The Plague - Camus.pdf")
    anthology = _upload(client, "Witches Magical Worlds of Fantasy - Isaac Asimov.pdf")
    _as_pages(module, novel["document_id"], FICTION, 228)
    _as_pages(module, anthology["document_id"], FICTION, 372)
    module.STORE.update(anthology["document_id"], {"title": "Asimov's Magical Worlds of Fantasy"})
    for doc in (novel, anthology):
        _reclassify(module, doc["document_id"])
    assert _doc(client, novel["document_id"])["book_form"] == "novel"
    assert _doc(client, anthology["document_id"])["book_form"] == "collection"


def test_records_classified_under_current_rules_are_left_alone(service):
    module, client, _indexed = service
    doc = _upload(client, "Whatever - Someone.pdf")
    _as_pages(module, doc["document_id"], FICTION, 56)
    module.STORE.update(doc["document_id"], {"kind": "document", "kind_rules": module.doc_kind.RULES_VERSION})
    module.backfill_document_kinds()
    assert _doc(client, doc["document_id"])["kind"] == "document"  # not re-read, so not changed


def test_the_readers_choices_survive_a_reclassification(service):
    module, client, _indexed = service
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    doc_id = doc["document_id"]
    _as_pages(module, doc_id, FICTION, 56)
    client.patch(f"/documents/{doc_id}", json={"shelf": "Favourites", "form": "novel", "kind": "paper"})
    _reclassify(module, doc_id)
    after = _doc(client, doc_id)
    assert after["shelf"] == "Favourites"
    assert after["kind_override"] == "paper"
    assert after["kind"] == "book"  # the guess moved; the reader's choice stayed
    assert after["book_form"] is None  # the reader made it a paper, which has no form


def test_a_form_override_beats_the_guess_and_auto_hands_it_back(service):
    module, client, _indexed = service
    doc = _upload(client, "The Stranger - Camus.pdf")
    doc_id = doc["document_id"]
    _as_pages(module, doc_id, FICTION, 77)
    _reclassify(module, doc_id)
    assert _doc(client, doc_id)["book_form"] == "short_story"
    assert client.patch(f"/documents/{doc_id}", json={"form": "novel"}).json()["book_form_override"] == "novel"
    assert client.patch(f"/documents/{doc_id}", json={"form": "auto"}).json()["book_form_override"] is None
    assert client.patch(f"/documents/{doc_id}", json={"form": "sonnet"}).status_code == 422


def test_editing_the_page_count_or_genre_moves_the_form(service):
    module, client, _indexed = service
    doc = _upload(client, "The Stranger - Camus.pdf")
    doc_id = doc["document_id"]
    _as_pages(module, doc_id, FICTION, 77)
    _reclassify(module, doc_id)
    assert _doc(client, doc_id)["book_form"] == "short_story"
    # The printed edition is longer than the file: a novel by the 150-page rule.
    assert client.patch(f"/documents/{doc_id}", json={"page_count": 180}).json()["book_form"] == "novel"
    # A catalogue genre that says non-fiction settles it, whatever the text reads like.
    assert client.patch(f"/documents/{doc_id}", json={"genres": ["Nonfiction"]}).json()["book_form"] == "nonfiction"


def test_a_catalogued_book_with_nothing_to_judge_has_no_form(service):
    _module, client, _indexed = service
    created = client.post("/owned-books", json={"title": "A Book I Own", "author": "Someone"})
    assert created.status_code == 201
    doc_id = created.json()["book"]["book_id"]
    assert _doc(client, doc_id)["book_form"] is None  # no text, no genre: no guess
    patched = client.patch(f"/documents/{doc_id}", json={"genres": ["Fantasy"], "page_count": 90})
    assert patched.json()["book_form"] == "short_story"
