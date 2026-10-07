"""Marks made in a PDF by any native tool: how accurately they are read back,
how changed files are noticed, and how the searchable digest is kept."""
import hashlib
import os
import time

import fitz
import pytest
from conftest import make_pdf, wait_for_status

import annotations as reader_annotations

LINES = [
    "Serendipity favours the prepared mind.",
    "A quixotic plan met an ephemeral hope and",
    "an obdurate refusal on the second line.",
    "Name Value Total",
    "Visit homepage today.",
]


def blank_page(document=None, lines=LINES, top=100, step=14):
    document = fitz.open() if document is None else document
    page = document.new_page()
    for number, text in enumerate(lines):
        page.insert_text((72, top + number * step), text, fontsize=12)
    return document, page


def saved(document, path):
    document.save(path)
    document.close()
    return path


def scan(path):
    return reader_annotations.scan_pdf_annotations(path)


def only(items, kind):
    return [item for item in items if item["kind"] == kind]


# ------------------------------------------------------------- the scanner
def test_clean_word():
    assert reader_annotations.clean_word("“Serendipity,”") == "Serendipity"
    assert reader_annotations.clean_word("don't.") == "don't"
    assert reader_annotations.clean_word("1984") is None
    assert reader_annotations.clean_word("—") is None
    assert reader_annotations.clean_word("x" * 80) is None


def test_highlight_quotes_exactly_the_marked_words_with_its_comment(tmp_path):
    document, page = blank_page()
    mark = page.add_highlight_annot(page.search_for("favours the prepared")[0])
    mark.set_info(content="Key claim", title="Boris")
    mark.update()
    (item,) = scan(saved(document, tmp_path / "a.pdf"))
    assert item["kind"] == "highlight" and item["page"] == 1
    assert item["text"] == "favours the prepared"
    assert item["comment"] == "Key claim" and item["author"] == "Boris"
    assert item["color"] == "#ffff00"


def test_a_highlight_across_lines_reads_in_order_and_ignores_the_lines_around_it(tmp_path):
    # Tight leading: each line's box nearly touches the next, so a sloppy
    # vertical test would drag in "Serendipity ..." above and "Name Value" below.
    document, page = blank_page(step=13)
    page.add_highlight_annot(page.search_for("quixotic plan met an ephemeral hope and")
                             + page.search_for("an obdurate refusal"))
    (item,) = scan(saved(document, tmp_path / "multi.pdf"))
    assert item["text"] == "quixotic plan met an ephemeral hope and an obdurate refusal"


def test_words_at_the_edges_count_only_when_mostly_covered(tmp_path):
    document, page = blank_page()
    word = page.search_for("favours")[0]
    following = page.search_for("the prepared")[0]
    # starts 55% of the way into "favours" and stops 15% into "mind."
    start = word.x0 + word.width * 0.45
    mind = page.search_for("mind.")[0]
    quad = fitz.Rect(start, word.y0, mind.x0 + mind.width * 0.15, word.y1)
    page.add_highlight_annot(quad)
    (item,) = scan(saved(document, tmp_path / "edge.pdf"))
    assert item["text"] == "favours the prepared"
    assert following.x0 > start


@pytest.mark.parametrize("adder,kind", [
    ("add_underline_annot", "underline"),
    ("add_squiggly_annot", "underline"),
    ("add_strikeout_annot", "strikeout"),
])
def test_underline_squiggly_and_strikeout_quote_their_words(tmp_path, adder, kind):
    document, page = blank_page()
    getattr(page, adder)(page.search_for("ephemeral")[0])
    (item,) = scan(saved(document, tmp_path / f"{adder}.pdf"))
    assert (item["kind"], item["text"]) == (kind, "ephemeral")


def test_typed_text_and_sticky_notes_are_read(tmp_path):
    document, page = blank_page()
    box = page.add_freetext_annot(fitz.Rect(300, 300, 520, 340), "Check this against chapter 4", fontsize=11)
    box.set_info(content="Check this against chapter 4")
    box.update()
    note = page.add_text_annot((60, 60), "Come back to this")
    note.update()
    items = scan(saved(document, tmp_path / "typed.pdf"))
    assert [i["text"] for i in only(items, "text")] == ["Check this against chapter 4"]
    assert [i["comment"] for i in only(items, "note")] == ["Come back to this"]


def test_a_stamp_repeated_on_many_pages_is_not_a_margin_note(tmp_path):
    document = fitz.open()
    for number in range(4):
        _, page = blank_page(document, ["Chapter text for the stamp test."])
        stamp = page.add_freetext_annot(fitz.Rect(200, 700, 400, 720), "Download at SomeEbookSite.com", fontsize=10)
        stamp.set_info(content="Download at SomeEbookSite.com")
        stamp.update()
        if number < 2:  # a note typed on only two pages is the reader's own
            mine = page.add_freetext_annot(fitz.Rect(200, 600, 400, 620), "Check the source", fontsize=10)
            mine.set_info(content="Check the source")
            mine.update()
    page = document[0]
    page.add_highlight_annot(page.search_for("stamp test")[0])
    items = scan(saved(document, tmp_path / "stamp.pdf"))
    assert [i["text"] for i in only(items, "text")] == ["Check the source", "Check the source"]
    assert len(only(items, "highlight")) == 1


def test_a_text_box_over_a_highlight_is_not_quoted_as_book_text(tmp_path):
    document, page = blank_page()
    line = page.search_for("ephemeral hope and")[0]
    page.add_highlight_annot(line)
    box = page.add_freetext_annot(fitz.Rect(line.x0, line.y0 - 1, line.x1 + 80, line.y1 + 1), "NOTE TO SELF", fontsize=11)
    box.set_info(content="NOTE TO SELF")
    box.update()
    items = scan(saved(document, tmp_path / "overlap.pdf"))
    assert only(items, "highlight")[0]["text"] == "ephemeral hope and"


def test_a_highlighter_pen_reads_the_words_it_covers(tmp_path):
    document, page = blank_page()
    span = page.search_for("prepared mind.")[0]
    middle = (span.y0 + span.y1) / 2
    pen = page.add_ink_annot([[(span.x0, middle), (span.x1, middle)]])
    pen.set_border(width=12)
    pen.set_opacity(0.4)
    pen.set_colors(stroke=(1, 1, 0))
    pen.update()
    (item,) = scan(saved(document, tmp_path / "pen.pdf"))
    assert (item["kind"], item["text"]) == ("highlight", "prepared mind.")


def test_a_thin_pen_line_is_an_underline_and_a_scribble_is_a_drawing(tmp_path):
    document, page = blank_page()
    word = page.search_for("quixotic")[0]
    line = page.add_ink_annot([[(word.x0, word.y1 + 1), (word.x1, word.y1 + 1.5)]])
    line.set_border(width=1)
    line.update()
    far = fitz.Rect(300, 400, 400, 500)
    page.add_ink_annot([[(far.x0, far.y0), (far.x1, far.y1), (far.x0, far.y1)]]).update()
    items = scan(saved(document, tmp_path / "ink.pdf"))
    assert (only(items, "underline")[0]["text"]) == "quixotic"
    assert len(only(items, "drawing")) == 1 and only(items, "drawing")[0]["text"] == ""


def test_hidden_marks_links_and_form_fields_are_not_content(tmp_path):
    document, page = blank_page()
    hidden = page.add_highlight_annot(page.search_for("Serendipity")[0])
    hidden.set_flags(fitz.PDF_ANNOT_IS_HIDDEN)
    hidden.update()
    page.insert_link({"kind": fitz.LINK_URI, "from": page.search_for("homepage")[0], "uri": "https://example.com"})
    assert scan(saved(document, tmp_path / "hidden.pdf")) == []


def test_replies_are_folded_into_the_mark_they_answer(tmp_path):
    document, page = blank_page()
    parent = page.add_highlight_annot(page.search_for("prepared mind.")[0])
    parent.set_info(content="Is this true?")
    parent.update()
    reply = page.add_text_annot((60, 80), "Yes, see chapter 2")
    if not hasattr(reply, "set_irt_xref"):
        pytest.skip("this PyMuPDF cannot write reply threads")
    reply.set_irt_xref(parent.xref)
    reply.update()
    (item,) = scan(saved(document, tmp_path / "reply.pdf"))
    assert item["comment"] == "Is this true?\nReply: Yes, see chapter 2"


def test_rotated_pages_and_second_pages_read_correctly(tmp_path):
    document, page = blank_page()
    page.add_highlight_annot(page.search_for("prepared mind.")[0])
    page.set_rotation(90)
    _, second = blank_page(document, ["Page two has an obdurate refusal."])
    second.add_underline_annot(second.search_for("obdurate")[0])
    items = scan(saved(document, tmp_path / "rot.pdf"))
    assert [(i["page"], i["kind"], i["text"]) for i in items] == [
        (1, "highlight", "prepared mind."), (2, "underline", "obdurate"),
    ]


def test_marks_on_a_page_with_no_text_layer_are_kept_without_text(tmp_path):
    document = fitz.open()
    page = document.new_page()
    mark = page.add_highlight_annot(fitz.Rect(100, 100, 300, 120))
    mark.set_info(content="scanned page, but my comment survives")
    mark.update()
    (item,) = scan(saved(document, tmp_path / "scan.pdf"))
    assert item["text"] == "" and item["comment"].startswith("scanned page")


def test_unreadable_and_encrypted_files_give_no_marks_instead_of_errors(tmp_path):
    garbage = tmp_path / "garbage.pdf"
    garbage.write_bytes(b"not a pdf at all")
    assert scan(garbage) == [] and scan(tmp_path / "missing.pdf") == []
    document, page = blank_page()
    page.add_highlight_annot(page.search_for("Serendipity")[0])
    locked = tmp_path / "locked.pdf"
    document.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    assert scan(locked) == [] and reader_annotations.text_signature(locked) is None


def test_the_text_fingerprint_ignores_marks_but_notices_real_edits(tmp_path):
    document, page = blank_page()
    plain = saved(document, tmp_path / "plain.pdf")
    before = reader_annotations.text_signature(plain)
    document = fitz.open(plain)
    page = document[0]
    page.add_highlight_annot(page.search_for("favours")[0])
    box = page.add_freetext_annot(fitz.Rect(300, 300, 520, 340), "typed words", fontsize=11)
    box.set_info(content="typed words")
    box.update()
    marked = tmp_path / "marked.pdf"
    document.save(marked)
    assert reader_annotations.text_signature(marked) == before
    _, edited = blank_page(fitz.open(), LINES + ["A brand new sentence."])
    other = tmp_path / "edited.pdf"
    edited.parent.save(other)
    assert reader_annotations.text_signature(other) != before


def test_digest_lists_every_searchable_mark_by_page():
    items = [
        {"page": 2, "kind": "highlight", "text": "prepared mind", "comment": "key claim"},
        {"page": 1, "kind": "text", "text": "check chapter 4", "comment": ""},
        {"page": 1, "kind": "note", "text": "", "comment": "come back"},
        {"page": 1, "kind": "strikeout", "text": "lazy", "comment": ""},
        {"page": 1, "kind": "drawing", "text": "", "comment": ""},
    ]
    text = reader_annotations.digest_text("My Book", items)
    assert text.startswith("# Annotations: My Book")
    assert text.index("## Page 2") > text.index("## Page 1") or text.index("## Page 1") > text.index("## Page 2")
    for expected in ("> prepared mind", "Comment on page 2: key claim", "Margin note on page 1: check chapter 4",
                     "Note on page 1: come back", "Struck out on page 1:", "> lazy"):
        assert expected in text
    assert "Drawing" not in text


# ------------------------------------------------- the underlined-word finder
def make_marked_pdf(path):
    """A real Underline annotation, a drawn underline, a two-word underline, a
    table-style rule, an underlined hyperlink, and a thin pen line."""
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "Serendipity favours the prepared mind.", fontsize=12)
    page.insert_text((72, 130), "A quixotic plan met an ephemeral hope.", fontsize=12)
    page.insert_text((72, 160), "Both of these words are underlined together.", fontsize=12)
    page.insert_text((72, 190), "Name Value Total", fontsize=12)
    page.insert_text((72, 220), "Visit homepage today.", fontsize=12)
    page.insert_text((72, 250), "A perspicacious reader draws lines.", fontsize=12)

    page.add_underline_annot(page.search_for("Serendipity")[0])
    box = page.search_for("quixotic")[0]
    page.draw_line((box.x0, box.y1 + 1.5), (box.x1, box.y1 + 1.5), width=0.8)
    page.add_underline_annot(page.search_for("underlined together")[0])
    row = page.search_for("Name Value Total")[0]
    page.draw_line((row.x0, row.y1 + 1.5), (row.x1, row.y1 + 1.5), width=0.8)
    link = page.search_for("homepage")[0]
    page.insert_link({"kind": fitz.LINK_URI, "from": link, "uri": "https://example.com"})
    page.draw_line((link.x0, link.y1 + 1.5), (link.x1, link.y1 + 1.5), width=0.8)
    pen = page.search_for("perspicacious")[0]
    ink = page.add_ink_annot([[(pen.x0, pen.y1 + 1), (pen.x1, pen.y1 + 1.4)]])
    ink.set_border(width=1)
    ink.update()

    page2 = document.new_page()
    page2.insert_text((72, 100), "An obdurate refusal appeared.", fontsize=12)
    page2.add_squiggly_annot(page2.search_for("obdurate")[0])
    document.save(path)
    document.close()


def test_finds_only_single_underlined_words_however_they_were_made(tmp_path):
    path = tmp_path / "marked.pdf"
    make_marked_pdf(path)
    found = {item["word"]: item for item in reader_annotations.find_underlined_words(path)}
    assert set(found) == {"Serendipity", "quixotic", "obdurate", "perspicacious"}
    assert found["Serendipity"]["source"] == "pdf-underline"
    assert found["quixotic"]["source"] == "drawn-line"
    assert found["perspicacious"]["source"] == "pen-line"
    assert found["Serendipity"]["quote"] == "Serendipity favours the prepared mind."
    assert found["obdurate"]["page"] == 2


def test_an_underline_does_not_spill_onto_neighbouring_lines(tmp_path):
    document, page = blank_page(step=12)  # lines nearly touching
    page.add_underline_annot(page.search_for("ephemeral")[0])
    path = saved(document, tmp_path / "tight.pdf")
    assert [w["word"] for w in reader_annotations.find_underlined_words(path)] == ["ephemeral"]


# ------------------------------------------------------------- the service
def marked_pdf_bytes(tmp_path, name="marked.pdf"):
    path = tmp_path / name
    make_marked_pdf(path)
    return path


@pytest.fixture
def book(service, tmp_path):
    module, client, _ = service
    path = marked_pdf_bytes(tmp_path)
    response = client.post("/documents", files={"file": ("marked.pdf", path.read_bytes(), "application/pdf")})
    assert response.status_code == 201
    doc = wait_for_status(client, response.json()["document_id"], "indexed")
    settle(module, client, doc["document_id"])
    return module, client, doc["document_id"]


def settle(module, client, doc_id):
    """Wait for the book's generated digest to exist and finish indexing. While
    it is still indexing, a change is deliberately deferred a few seconds so the
    running index cannot overwrite it; tests that edit marks start from rest."""
    deadline = time.time() + float(os.environ.get("TEST_INDEX_TIMEOUT", "10"))
    while time.time() < deadline:
        digest = module._digest_doc(doc_id)
        if digest:
            wait_for_status(client, digest["document_id"], "indexed")
            return
        time.sleep(0.05)
    pytest.fail("the marks digest was never created")


def digest_text(module, doc_id):
    digest = module._digest_doc(doc_id)
    return module.pdf_path(digest).read_text() if digest else None


def test_marks_in_a_new_pdf_are_read_when_it_finishes_indexing(book):
    module, client, doc_id = book
    listed = client.get(f"/documents/{doc_id}/annotations").json()
    assert listed["source"] == "original"
    assert {(m["page"], m["kind"], m["text"]) for m in listed["annotations"] if m["kind"] == "underline"} >= {
        (1, "underline", "Serendipity"), (2, "underline", "obdurate")}
    text = digest_text(module, doc_id)
    assert text and "Underlined on page 1:" in text and "> Serendipity" in text
    # the generated digest is searchable but is not one of the reader's own notes
    assert client.get("/notes").json()["notes"] == []
    assert client.get(f"/documents/{doc_id}").json()["note_count"] == 0


def test_a_pdf_edited_in_place_is_noticed_and_only_reindexed_when_its_text_changed(book):
    module, client, doc_id = book
    stored = module.pdf_path(module.read_metadata(doc_id))
    before = module.read_metadata(doc_id)
    assert before["file_sig"] and before["text_sig"]
    assert module._refresh_all_pdf_marks() == 0  # nothing changed: no work

    # 1. a native viewer saves the same book with one more highlight
    with fitz.open(stored) as document:
        page = document[0]
        mark = page.add_highlight_annot(page.search_for("prepared mind.")[0])
        mark.set_info(content="the thesis")
        mark.update()
        document.save(stored.with_suffix(".new"), garbage=3)
    stored.with_suffix(".new").replace(stored)
    assert module._refresh_all_pdf_marks() == 1
    settle(module, client, doc_id)
    after = module.read_metadata(doc_id)
    assert after["pipeline_version"] == before["pipeline_version"], "annotation-only edits must not re-index the book"
    assert after["content_sha256"] == hashlib.sha256(stored.read_bytes()).hexdigest() != before["content_sha256"]
    text = digest_text(module, doc_id)
    assert "> prepared mind." in text and "Comment on page 1: the thesis" in text

    # 2. the reader removes every mark: the digest goes away
    with fitz.open(stored) as document:
        for page in document:
            while page.first_annot:
                page.delete_annot(page.first_annot)
        document.save(stored.with_suffix(".new"), garbage=3)
    stored.with_suffix(".new").replace(stored)
    module._refresh_all_pdf_marks()
    assert module._digest_doc(doc_id) is None

    # 3. the book's own text changes: that does re-index
    with fitz.open(stored) as document:
        document[0].insert_text((72, 400), "A newly added paragraph of real content.", fontsize=12)
        document.save(stored.with_suffix(".new"), garbage=3)
    stored.with_suffix(".new").replace(stored)
    module._refresh_all_pdf_marks()
    wait_for_status(client, doc_id, "indexed")
    assert module.read_metadata(doc_id)["text_sig"] != after["text_sig"]


def test_an_annotated_copy_replaces_the_file_that_opens_but_never_the_original(book, tmp_path):
    module, client, doc_id = book
    stored = module.pdf_path(module.read_metadata(doc_id))
    original = stored.read_bytes()
    with fitz.open(stored) as document:
        page = document[0]
        mark = page.add_highlight_annot(page.search_for("favours the prepared")[0])
        mark.set_info(content="from my viewer")
        mark.update()
        document.save(tmp_path / "from-viewer.pdf")
    copy = (tmp_path / "from-viewer.pdf").read_bytes()

    response = client.post(f"/documents/{doc_id}/annotated-copy", files={"file": ("x.pdf", copy, "application/pdf")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "annotated-copy"
    assert any(m["text"] == "favours the prepared" and m["comment"] == "from my viewer" for m in body["annotations"])
    assert client.get(f"/documents/{doc_id}/file").content == copy  # opens the copy
    assert stored.read_bytes() == original  # the original is untouched
    assert "from my viewer" in digest_text(module, doc_id)
    assert {w["word"] for w in client.post(f"/documents/{doc_id}/underlined-words").json()["words"]} >= {"Serendipity"}

    settle(module, client, doc_id)  # the digest has just been rewritten and is re-indexing
    back = client.delete(f"/documents/{doc_id}/annotated-copy").json()
    assert back["source"] == "original"
    assert client.get(f"/documents/{doc_id}/file").content == original
    assert "from my viewer" not in (digest_text(module, doc_id) or "")


def test_a_wrong_file_is_refused_as_an_annotated_copy(book):
    _, client, doc_id = book
    url = f"/documents/{doc_id}/annotated-copy"
    assert client.post(url, files={"file": ("x.pdf", b"", "application/pdf")}).status_code == 400
    assert client.post(url, files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 422
    other = client.post(url, files={"file": ("x.pdf", make_pdf("one page only"), "application/pdf")})
    assert other.status_code == 422 and "pages" in other.json()["detail"]
    assert client.get(f"/documents/{doc_id}/annotations").json()["source"] == "original"


def test_rescan_reads_the_file_now(book):
    module, client, doc_id = book
    response = client.post(f"/documents/{doc_id}/annotations/rescan")
    assert response.status_code == 200 and response.json()["annotations"]


def test_deleting_a_book_removes_its_copy_and_digest(book, tmp_path):
    module, client, doc_id = book
    stored = module.pdf_path(module.read_metadata(doc_id))
    assert client.post(f"/documents/{doc_id}/annotated-copy", files={"file": ("x.pdf", stored.read_bytes(), "application/pdf")}).status_code == 200
    digest = module._digest_doc(doc_id)
    assert digest and module.annotated_copy_path(doc_id).exists()
    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert not module.annotated_copy_path(doc_id).exists()
    assert module.STORE.get(digest["document_id"]) is None


def test_scan_and_harvest_vocabulary(book):
    _, client, doc_id = book
    scan_response = client.post(f"/documents/{doc_id}/underlined-words")
    assert scan_response.status_code == 200, scan_response.text
    words = {item["word"]: item for item in scan_response.json()["words"]}
    assert set(words) == {"Serendipity", "quixotic", "obdurate", "perspicacious"}
    assert not any(item["saved"] for item in words.values())
    chosen = [{"word": w["word"], "page": w["page"], "quote": w["quote"]} for w in words.values()]
    harvested = client.post("/vocabulary/harvest", json={"document_id": doc_id, "words": chosen})
    assert harvested.status_code == 201 and harvested.json() == {"created": 4, "skipped": 0}
    assert client.post("/vocabulary/harvest", json={"document_id": doc_id, "words": chosen}).json() == {"created": 0, "skipped": 4}
    assert all(item["saved"] for item in client.post(f"/documents/{doc_id}/underlined-words").json()["words"])
    saved_words = client.get("/vocabulary", params={"document_id": doc_id}).json()["words"]
    entry = next(item for item in saved_words if item["word"] == "quixotic")
    assert entry["definition"] == "" and entry["definition_source"] == "underline" and entry["source_page"] == 1
    edited = client.put(f"/vocabulary/{entry['word_id']}", json={"word": "quixotic", "definition": "Exceedingly idealistic.", "document_id": doc_id, "source_page": 1})
    assert edited.status_code == 200 and edited.json()["definition_source"] == "manual"
    assert client.post("/vocabulary", json={"word": "foo"}).status_code == 422
