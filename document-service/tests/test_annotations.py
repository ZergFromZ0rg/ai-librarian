"""Reader annotations: word boxes, underlined-word detection, the searchable
digest, and baking marks into a downloadable PDF."""
import fitz
import pytest
from conftest import wait_for_status

import annotations as reader_annotations


def make_marked_pdf(path):
    """A page with one real Underline annotation, one drawn underline, a
    two-word underline, a table-style rule, and an underlined hyperlink."""
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "Serendipity favours the prepared mind.", fontsize=12)
    page.insert_text((72, 130), "A quixotic plan met an ephemeral hope.", fontsize=12)
    page.insert_text((72, 160), "Both of these words are underlined together.", fontsize=12)
    page.insert_text((72, 190), "Name Value Total", fontsize=12)
    page.insert_text((72, 220), "Visit homepage today.", fontsize=12)
    page.insert_text((72, 300), "Second page. An obdurate refusal appeared.", fontsize=12)

    page.add_underline_annot(page.search_for("Serendipity")[0])
    # drawn line directly under "quixotic" (not a real annotation)
    box = page.search_for("quixotic")[0]
    page.draw_line((box.x0, box.y1 + 1.5), (box.x1, box.y1 + 1.5), width=0.8)
    # two words underlined by one annotation
    page.add_underline_annot(page.search_for("underlined together")[0])
    # a long table rule under a whole row of words
    row = page.search_for("Name Value Total")[0]
    page.draw_line((row.x0, row.y1 + 1.5), (row.x1, row.y1 + 1.5), width=0.8)
    # an underlined hyperlink
    link = page.search_for("homepage")[0]
    page.insert_link({"kind": fitz.LINK_URI, "from": link, "uri": "https://example.com"})
    page.draw_line((link.x0, link.y1 + 1.5), (link.x1, link.y1 + 1.5), width=0.8)

    page2 = document.new_page()
    page2.insert_text((72, 100), "An obdurate refusal appeared.", fontsize=12)
    page2.add_squiggly_annot(page2.search_for("obdurate")[0])
    document.save(path)
    document.close()


def test_clean_word():
    assert reader_annotations.clean_word("“Serendipity,”") == "Serendipity"
    assert reader_annotations.clean_word("don't.") == "don't"
    assert reader_annotations.clean_word("1984") is None
    assert reader_annotations.clean_word("—") is None
    assert reader_annotations.clean_word("x" * 80) is None


def test_finds_only_single_underlined_words(tmp_path):
    path = tmp_path / "marked.pdf"
    make_marked_pdf(path)
    found = {item["word"]: item for item in reader_annotations.find_underlined_words(path)}
    assert set(found) == {"Serendipity", "quixotic", "obdurate"}
    assert found["Serendipity"]["source"] == "pdf-underline"
    assert found["quixotic"]["source"] == "drawn-line"
    assert found["Serendipity"]["quote"] == "Serendipity favours the prepared mind."
    assert found["obdurate"]["page"] == 2


def test_reader_underlines_count_as_marks(tmp_path):
    path = tmp_path / "plain.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "A perspicacious reader marks words.", fontsize=12)
    box = page.search_for("perspicacious")[0]
    document.save(path)
    document.close()
    rect = [box.x0 / 595, box.y0 / 842, box.x1 / 595, box.y1 / 842]
    found = reader_annotations.find_underlined_words(path, {1: [[rect]]})
    assert [item["word"] for item in found] == ["perspicacious"]
    assert found[0]["source"] == "reader"


@pytest.fixture
def book(service):
    module, client, _ = service
    module.ANNOTATION_INDEX_DELAY = 0  # rebuild the digest synchronously
    path = module.DATA_DIR / "marked.pdf"
    make_marked_pdf(path)
    response = client.post("/documents", files={"file": ("marked.pdf", path.read_bytes(), "application/pdf")})
    assert response.status_code == 201
    doc = wait_for_status(client, response.json()["document_id"], "indexed")
    return module, client, doc["document_id"]


def test_page_words_are_normalised_boxes(book):
    _, client, doc_id = book
    data = client.get(f"/documents/{doc_id}/page/1/words").json()
    words = {w[4]: w[:4] for w in data["words"]}
    assert "Serendipity" in words
    x0, y0, x1, y1 = words["Serendipity"]
    assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    assert client.get(f"/documents/{doc_id}/page/99/words").status_code == 404


def test_annotation_lifecycle_and_digest(book):
    module, client, doc_id = book
    rect = [0.12, 0.1, 0.3, 0.12]
    created = client.post(
        f"/documents/{doc_id}/annotations",
        json={"page": 1, "kind": "highlight", "rects": [rect], "text": "prepared mind", "comment": "key claim"},
    )
    assert created.status_code == 201, created.text
    annotation = created.json()
    assert annotation["rects"] == [rect] and annotation["color"] == "#ffd84d"
    assert client.get(f"/documents/{doc_id}/annotations").json()["annotations"][0]["annotation_id"] == annotation["annotation_id"]

    # the digest is a generated note: searchable, but not listed as one of the reader's notes
    digest = module._digest_doc(doc_id)
    assert digest is not None and digest["title"].startswith("Annotations:")
    assert "> prepared mind" in module.pdf_path(digest).read_text() and "key claim" in module.pdf_path(digest).read_text()
    assert client.get("/notes").json()["notes"] == []
    assert client.get(f"/documents/{doc_id}").json()["note_count"] == 0

    wait_for_status(client, digest["document_id"], "indexed")  # a digest still indexing is refreshed once it finishes
    updated = client.put(f"/annotations/{annotation['annotation_id']}", json={"comment": "revised"})
    assert updated.json()["comment"] == "revised" and updated.json()["rects"] == [rect]
    assert "revised" in module.pdf_path(module._digest_doc(doc_id)).read_text()

    assert client.delete(f"/annotations/{annotation['annotation_id']}").status_code == 200
    assert module._digest_doc(doc_id) is None  # nothing searchable left
    assert client.delete(f"/annotations/{annotation['annotation_id']}").status_code == 404


def test_annotation_validation(book):
    _, client, doc_id = book
    url = f"/documents/{doc_id}/annotations"
    assert client.post(url, json={"page": 1, "kind": "highlight", "rects": []}).status_code == 422
    assert client.post(url, json={"page": 1, "kind": "highlight", "rects": [[0.5, 0.5, 0.2, 0.6]]}).status_code == 422
    assert client.post(url, json={"page": 1, "kind": "text", "rects": [[0.1, 0.1, 0.3, 0.2]]}).status_code == 422
    assert client.post(url, json={"page": 1, "kind": "ink", "paths": [[[0.1, 0.1]]]}).status_code == 422
    assert client.post(url, json={"page": 1, "kind": "highlight", "rects": [[0.1, 0.1, 0.3, 0.2]], "color": "red"}).status_code == 422
    assert client.post(url, json={"page": 9, "kind": "highlight", "rects": [[0.1, 0.1, 0.3, 0.2]]}).status_code == 422


def test_annotated_pdf_contains_real_annotations(book, tmp_path):
    _, client, doc_id = book
    url = f"/documents/{doc_id}/annotations"
    box = [0.1, 0.1, 0.4, 0.13]
    client.post(url, json={"page": 1, "kind": "highlight", "rects": [box], "text": "x"})
    client.post(url, json={"page": 1, "kind": "text", "rects": [[0.5, 0.5, 0.9, 0.56]], "text": "margin words", "size": 0.02})
    client.post(url, json={"page": 1, "kind": "ink", "paths": [[[0.2, 0.7], [0.4, 0.75], [0.6, 0.7]]], "width": 0.003})
    response = client.get(f"/documents/{doc_id}/annotated.pdf")
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    out = tmp_path / "out.pdf"
    out.write_bytes(response.content)
    with fitz.open(out) as document:
        kinds = {a.type[1] for a in document[0].annots()}
    assert {"Highlight", "FreeText", "Ink"} <= kinds


def test_scan_and_harvest_vocabulary(book):
    _, client, doc_id = book
    scan = client.post(f"/documents/{doc_id}/underlined-words")
    assert scan.status_code == 200, scan.text
    words = {item["word"]: item for item in scan.json()["words"]}
    assert set(words) == {"Serendipity", "quixotic", "obdurate"}
    assert not any(item["saved"] for item in words.values())

    chosen = [{"word": w["word"], "page": w["page"], "quote": w["quote"]} for w in words.values()]
    harvested = client.post("/vocabulary/harvest", json={"document_id": doc_id, "words": chosen})
    assert harvested.status_code == 201 and harvested.json() == {"created": 3, "skipped": 0}
    again = client.post("/vocabulary/harvest", json={"document_id": doc_id, "words": chosen})
    assert again.json() == {"created": 0, "skipped": 3}
    assert all(item["saved"] for item in client.post(f"/documents/{doc_id}/underlined-words").json()["words"])

    saved = client.get("/vocabulary", params={"document_id": doc_id}).json()["words"]
    entry = next(item for item in saved if item["word"] == "quixotic")
    assert entry["definition"] == "" and entry["definition_source"] == "underline" and entry["source_page"] == 1
    # the definition can be filled in later without any other field
    edited = client.put(f"/vocabulary/{entry['word_id']}", json={"word": "quixotic", "definition": "Exceedingly idealistic.", "document_id": doc_id, "source_page": 1})
    assert edited.status_code == 200 and edited.json()["definition"] == "Exceedingly idealistic."
    assert edited.json()["definition_source"] == "manual"
    # a brand-new manual word still needs its definition
    assert client.post("/vocabulary", json={"word": "foo"}).status_code == 422


def test_deleting_a_book_removes_its_marks_and_digest(book):
    module, client, doc_id = book
    client.post(f"/documents/{doc_id}/annotations", json={"page": 1, "kind": "highlight", "rects": [[0.1, 0.1, 0.4, 0.13]], "text": "gone soon"})
    digest = module._digest_doc(doc_id)
    assert digest
    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert module.STORE.list_annotations(doc_id) == []
    assert module.STORE.get(digest["document_id"]) is None


def test_marks_are_written_into_the_stored_pdf(book):
    module, client, doc_id = book
    stored = module.pdf_path(module.read_metadata(doc_id))
    original = stored.read_bytes()
    made = client.post(f"/documents/{doc_id}/annotations", json={"page": 1, "kind": "highlight", "rects": [[0.1, 0.1, 0.4, 0.13]], "text": "x", "comment": "why"}).json()
    client.post(f"/documents/{doc_id}/annotations", json={"page": 1, "kind": "text", "rects": [[0.5, 0.5, 0.9, 0.56]], "text": "typed"})
    with fitz.open(stored) as document:
        names = {a.info["name"] for a in document[0].annots()}
        assert f"ailib:{made['annotation_id']}" in names
        assert any(a.info["content"] == "why" for a in document[0].annots())
    assert (module.DATA_DIR / "originals" / f"{doc_id}.pdf").read_bytes() == original  # first-time backup
    assert module.read_metadata(doc_id)["content_sha256"] != __import__("hashlib").sha256(original).hexdigest()

    client.delete(f"/annotations/{made['annotation_id']}")
    with fitz.open(stored) as document:
        assert f"ailib:{made['annotation_id']}" not in {a.info["name"] for a in document[0].annots()}
