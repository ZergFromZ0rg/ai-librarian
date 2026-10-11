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


def _as_pages(module, doc_id, text, pages):
    """Pretend extraction found a book of `pages` printed pages of `text`: as many
    pages of it as make that many words (the test PDFs are tiny), since a book's
    length is judged from its words."""
    count = max(8, round(pages * module.doc_kind.WORDS_PER_PAGE / 800))
    path = module.EXTRACTED_DIR / f"{doc_id}.json"
    extracted = module.read_json(path)
    extracted["pages"] = [{"page": number + 1, "text": text} for number in range(count)]
    module.atomic_write_json(path, extracted)
    module.STORE.update(doc_id, {"pages": pages})


def _reclassify(module, doc_id):
    module.STORE.update(doc_id, {"kind": None, "kind_rules": 1, "book_form": None})
    module.backfill_document_kinds()


def test_a_short_novel_is_a_book_and_a_novella_and_lands_on_the_books_shelf(service):
    module, client, _indexed = service
    doc = _upload(client, "A Hapy Death - Camus.pdf")
    assert _doc(client, doc["document_id"])["kind"] == "document"  # one tiny page: nothing says book
    _as_pages(module, doc["document_id"], FICTION, 56)
    _reclassify(module, doc["document_id"])

    book = _doc(client, doc["document_id"])
    assert (book["kind"], book["book_form"], book["kind_rules"]) == ("book", "novella", module.doc_kind.RULES_VERSION)
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
    assert _doc(client, doc_id)["book_form"] == "novella"
    assert client.patch(f"/documents/{doc_id}", json={"form": "novel"}).json()["book_form_override"] == "novel"
    assert client.patch(f"/documents/{doc_id}", json={"form": "auto"}).json()["book_form_override"] is None
    assert client.patch(f"/documents/{doc_id}", json={"form": "sonnet"}).status_code == 422


def test_editing_the_page_count_or_genre_moves_the_form(service):
    module, client, _indexed = service
    doc = _upload(client, "The Stranger - Camus.pdf")
    doc_id = doc["document_id"]
    _as_pages(module, doc_id, FICTION, 77)
    _reclassify(module, doc_id)
    assert _doc(client, doc_id)["book_form"] == "novella"
    # The printed edition is longer than the text suggests: a novel by the 150-page rule.
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
    assert patched.json()["book_form"] == "novella"


def test_a_short_story_is_under_fifty_pages_and_the_length_comes_from_the_words(service):
    module, client, _indexed = service
    story = _upload(client, "The Library - Someone Famous.pdf")
    _as_pages(module, story["document_id"], FICTION, 30)  # 30 printed pages of text, 30 pages in the file
    _reclassify(module, story["document_id"])
    assert _doc(client, story["document_id"])["book_form"] == "short_story"

    # The file has 300 pages (a scan, two to a sheet) but only 120 pages' worth of words.
    short = _upload(client, "The Stranger - Camus.pdf")
    _as_pages(module, short["document_id"], FICTION, 120)
    module.STORE.update(short["document_id"], {"pages": 300})
    _reclassify(module, short["document_id"])
    after = _doc(client, short["document_id"])
    assert after["book_form"] == "novella"
    assert 100_000 > __import__("json").loads(after["text_stats"])["words"] > 30_000


def test_a_text_that_reads_as_present_tense_is_not_fiction_even_with_many_pronouns(service):
    module, client, _indexed = service
    doc = _upload(client, "A Diary Of Aphorisms - Someone.pdf")
    _as_pages(module, doc["document_id"], "She says that he is late and they are not asking her why it is so, but he does not say. " * 40, 200)
    _reclassify(module, doc["document_id"])
    assert _doc(client, doc["document_id"])["book_form"] == "nonfiction"


def test_a_models_fiction_verdict_is_remembered_and_moves_the_form(service):
    module, client, _indexed = service
    doc = _upload(client, "Pedro Paramo - Rulfo.pdf")
    _as_pages(module, doc["document_id"], "El hombre dijo que ella estaba cansada y que los dos habian llegado tarde a la casa de su madre. " * 8, 99)
    _reclassify(module, doc["document_id"])
    assert _doc(client, doc["document_id"])["book_form"] is None  # not English: unknown, not "non-fiction"
    module.STORE.update(doc["document_id"], {"ai_json": '{"fiction": true, "kind": "book"}'})
    module.STORE.update(doc["document_id"], module._form_fields(module.read_metadata(doc["document_id"])))
    assert _doc(client, doc["document_id"])["book_form"] in {"short_story", "novella"}


def test_a_collections_badge_counts_the_books_the_library_lists_not_every_file(service, monkeypatch):
    module, client, _indexed = service
    book = _upload(client, "The Stranger - Camus.pdf")
    paper = _upload(client, "A Study.pdf")
    sheet = _upload(client, "budget.pdf")
    _as_pages(module, book["document_id"], FICTION, 77)
    _reclassify(module, book["document_id"])
    module.STORE.update(paper["document_id"], {"kind": "paper", "kind_rules": module.doc_kind.RULES_VERSION})
    module.STORE.update(sheet["document_id"], {"kind": "document", "kind_rules": module.doc_kind.RULES_VERSION})
    # Everything uploaded here sits in one collection, as a scanned folder's files would.
    monkeypatch.setattr(module, "configured_collections", lambda: [{"id": "all", "name": "All", "path": "", "auto_scan": True}])
    monkeypatch.setattr(module, "_belongs_to_collection", lambda doc, collection: True)
    counted = client.get("/collections").json()["collections"][0]
    assert (counted["document_count"], counted["book_count"]) == (3, 1)  # three files, one of them a book


def test_the_server_and_the_ui_agree_on_where_a_file_belongs(service):
    module, _client, _indexed = service
    cases = [
        ({"kind": "document", "kind_override": "book"}, "book"), ({"kind": "paper"}, "paper"), ({"kind_override": "note"}, "note"),
        ({"file_type": "epub"}, "book"), ({"record_type": "standalone"}, "book"), ({"file_type": "pdf"}, "pending"),
        ({"file_type": "excel"}, "document"), ({"file_type": "word"}, "document"),
    ]
    assert [module._library_kind(doc) for doc, _ in cases] == [expected for _, expected in cases]  # mirrors storage.test.js
