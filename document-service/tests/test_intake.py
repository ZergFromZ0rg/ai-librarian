"""Uploads are staged and classified first; only a reader's save adds them to the library."""

import time
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

import intake
from conftest import make_pdf, wait_for_status

COPYRIGHT_PAGE = (
    "Published by Alfred A. Knopf, Inc.\n"
    "Copyright 1942 by Gallimard\n"
    "All rights reserved\n"
    "ISBN 978-0-306-40615-7"
)


def make_epub(title="The Quiet Library", author="Ada Reader", extra="") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator><dc:publisher>Quiet Press</dc:publisher>"
            "<dc:language>en-GB</dc:language><dc:date>2011-05-01</dc:date>"
            '<dc:identifier id="bookid">urn:isbn:9780306406157</dc:identifier>'
            f"{extra}</metadata>"
            '<manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>',
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>'
            "<body><h1>Chapter One</h1><p>Freedom lives in the quiet library.</p></body></html>",
            compress_type=ZIP_DEFLATED,
        )
    return buffer.getvalue()


def _stage(client, name, content, mime="application/pdf"):
    return client.post("/intake", files={"file": (name, content, mime)})


def _library(client):
    return client.get("/documents").json()["documents"]


# ------------------------------------------------------------------ helpers --
def test_the_content_decides_the_format_not_the_extension(tmp_path):
    pdf = tmp_path / "x.docx"
    pdf.write_bytes(make_pdf("Hello"))
    assert intake.sniff_format(pdf) == "pdf"
    file_type, note = intake.resolve_format("word", pdf)
    assert file_type == "pdf" and "content is PDF" in note

    epub = tmp_path / "y.pdf"
    epub.write_bytes(make_epub())
    assert intake.sniff_format(epub) == "epub"

    plain = tmp_path / "z.txt"
    plain.write_text("just words")
    assert intake.sniff_format(plain) is None
    assert intake.resolve_format("text", plain) == ("text", None)


def test_a_broken_package_or_binary_text_is_flagged_not_trusted(tmp_path):
    broken = tmp_path / "b.docx"
    broken.write_bytes(b"not a zip at all")
    file_type, note = intake.resolve_format("word", broken)
    assert file_type == "word" and "may fail to open" in note
    binary = tmp_path / "b.txt"
    binary.write_bytes(b"ab\x00cd" * 10)
    assert "binary" in intake.resolve_format("text", binary)[1]


def test_an_epub_package_states_its_details(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(make_epub(extra="<dc:subject>Libraries</dc:subject><dc:subject>Quiet</dc:subject>"))
    details = intake.embedded_details(path, "epub")
    assert details["title"] == "The Quiet Library" and details["author"] == "Ada Reader"
    assert details["publisher"] == "Quiet Press" and details["language"] == "en-gb" and details["published_year"] == 2011
    assert details["isbn_13"] == "9780306406157" and details["genres"] == ["Libraries", "Quiet"]


def test_a_placeholder_title_or_file_name_says_nothing():
    assert intake.plausible_title("Microsoft Word - report.docx") is None
    assert intake.plausible_title("untitled") is None
    assert intake.plausible_title("A Happy Death") == "A Happy Death"
    assert intake.junk_stem("scan0012") and intake.junk_stem("3f2a9c1be45d77a0aa11")
    assert not intake.junk_stem("A Happy Death - Camus")


def test_the_copyright_page_gives_isbn_year_and_publisher():
    found = intake.front_matter_details([COPYRIGHT_PAGE])
    assert found == {"isbn_13": "9780306406157", "published_year": 1942, "publisher": "Alfred A. Knopf"}
    assert intake.valid_isbn("0-306-40615-2") == "0306406152" and intake.valid_isbn("978-0-306-40615-8") is None


def test_the_latest_year_on_the_page_is_the_editions_and_a_bad_isbn_is_ignored():
    pages = ["Copyright © 1972, 1989 by Someone\nTranslation copyright 2004\nISBN 978-0-306-40615-8\nPublished by the author"]
    assert intake.front_matter_details(pages) == {"published_year": 2004}  # no ISBN: its checksum fails; no publisher: "the author"


def test_the_year_is_this_editions_not_a_reprinted_story_or_an_answer_key():
    anthology = "Copyright © 1983 by Isaac Asimov\n\"Spell\" copyright 1942 by Street & Smith\nrenewed 1970"
    assert intake.front_matter_details([anthology])["published_year"] == 1983
    textbook_answers = "(c) 1617.56 ⋯88.33°\n**7. (a)** 10 ⋯36.87°"
    assert "published_year" not in intake.front_matter_details([textbook_answers])
    assert intake.front_matter_details(["(c) 2019 Some Author"])["published_year"] == 2019


def test_a_publishers_street_address_is_not_part_of_its_name():
    page = "Published by Oxford University Press 198 Madison Avenue, New York, New York 10016"
    assert intake.front_matter_details([page])["publisher"] == "Oxford University Press"


def test_every_type_maps_to_a_kind_form_and_document_type():
    assert intake.type_patch("short_story") == {"kind": "book", "form": "short_story", "doc_type": "auto"}
    assert intake.type_patch("book") == {"kind": "book", "form": "auto", "doc_type": "auto"}
    assert intake.type_patch("paper") == {"kind": "paper", "form": "auto", "doc_type": "paper"}
    assert intake.type_patch("manual") == {"kind": "document", "form": "auto", "doc_type": "manual"}
    with pytest.raises(ValueError):
        intake.type_patch("poem")
    assert intake.type_of("book", "novella", None) == "novella" and intake.type_of("book", None, None) == "book"
    assert intake.type_of("document", None, "legal") == "legal" and intake.type_of("paper", None, "paper") == "paper"
    assert {option["value"] for option in intake.type_options()} >= {"novel", "paper", "other"}
    shelf_of = {option["value"]: option["shelf"] for option in intake.type_options()}
    assert shelf_of["novel"] == "Books" and shelf_of["paper"] == "Papers" and shelf_of["manual"] == "Manuals" and shelf_of["legal"] == "Legal & Finance"


# ----------------------------------------------------------------- the flow --
def test_an_upload_is_read_and_proposed_but_is_not_in_the_library(service):
    module, client, _ = service
    response = _stage(client, "A Hapy Death - Albert Camus.pdf", make_pdf("A HAPPY DEATH\nALBERT CAMUS\n" + COPYRIGHT_PAGE))
    assert response.status_code == 201
    item = response.json()
    assert item["format"] == "pdf" and item["format_label"] == "PDF" and item["state"] == "ready" and item["has_cover"]
    proposal = item["proposal"]
    assert (proposal["title"], proposal["author"]) == ("A Hapy Death", "Albert Camus")
    assert proposal["publisher"] == "Alfred A. Knopf" and proposal["published_year"] == 1942 and proposal["isbn_13"] == "9780306406157"
    assert proposal["type"] in {"book", "novel", "novella", "short_story", "collection", "nonfiction"}
    assert proposal["shelf"].startswith("Books")
    assert item["sources"]["title"] == "file name" and item["sources"]["author"] == "file name"
    assert item["sources"]["publisher"] == "copyright page"

    assert _library(client) == []  # nothing joined the library
    assert [entry["id"] for entry in client.get("/intake").json()["items"]] == [item["id"]]
    assert client.get(f"/intake/{item['id']}").json()["proposal"] == proposal
    cover = client.get(f"/intake/{item['id']}/cover")
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/jpeg"


def test_a_staged_upload_is_never_indexed_or_shelved(service):
    module, client, _ = service
    _stage(client, "Waiting.pdf", make_pdf("WAITING\n" + COPYRIGHT_PAGE))
    time.sleep(0.3)  # long enough for an index worker to claim it, were it queued
    assert module.list_metadata() == []  # no record at all: nothing to index, shelve, search or export
    assert _library(client) == [] and client.get("/intake/options").json()["shelves"] == sorted(
        set(module.shelves.TOP_LEVEL.values()) - {"Notes"} | set(module.shelves.DOCUMENT_TOP_LEVEL.values()), key=str.casefold
    )  # its suggested shelf did not appear among the shelves in use
    assert not list(module.DOCUMENTS_DIR.iterdir())  # and the file was never moved into the library's storage


def test_the_review_screen_gets_the_types_and_the_shelves_in_use(service):
    module, client, _ = service
    options = client.get("/intake/options").json()
    assert {"novel", "short_story", "paper", "manual", "other"} <= {item["value"] for item in options["types"]}
    assert "Books" in options["shelves"] and "Papers" in options["shelves"]


def test_saving_adds_the_reviewed_book_and_queues_indexing(service):
    module, client, _ = service
    staged = _stage(client, "scan0001.pdf", make_pdf("A HAPPY DEATH\nALBERT CAMUS\n" + COPYRIGHT_PAGE, pages=3)).json()
    review = {
        "title": "A Happy Death", "subtitle": "", "author": "Albert Camus", "subject": "Philosophy",
        "publisher": "Knopf", "published_year": 1972, "language": "en", "isbn_13": "9780306406157",
        "type": "novella", "shelf": "Books/Fiction/Albert Camus", "genres": ["Fiction"],
    }
    saved = client.post(f"/intake/{staged['id']}/save", json=review)
    assert saved.status_code == 201, saved.text
    doc = saved.json()
    assert (doc["title"], doc["author"], doc["publisher"], doc["published_year"]) == ("A Happy Death", "Albert Camus", "Knopf", 1972)
    assert doc["kind_override"] == "book" and doc["book_form_override"] == "novella"
    assert doc["shelf"] == "Books/Fiction/Albert Camus" and doc["metadata_edited"] == 1

    assert client.get("/intake").json()["items"] == []  # no longer waiting
    done = wait_for_status(client, doc["document_id"], "indexed")
    # What the reader confirmed survives indexing.
    assert (done["title"], done["author"], done["shelf"], done["book_form_override"]) == ("A Happy Death", "Albert Camus", "Books/Fiction/Albert Camus", "novella")
    assert [item["document_id"] for item in _library(client)] == [doc["document_id"]]
    assert client.get(f"/documents/{doc['document_id']}/file").status_code == 200


def test_a_document_type_saves_as_a_document_not_a_book(service):
    module, client, _ = service
    staged = _stage(client, "Router manual.pdf", make_pdf("USER MANUAL\nInstallation guide\nTroubleshooting\nSpecification", pages=2)).json()
    saved = client.post(f"/intake/{staged['id']}/save", json={"title": "Router manual", "type": "manual", "shelf": "Manuals"})
    assert saved.status_code == 201
    doc = saved.json()
    assert doc["kind_override"] == "document" and doc["doc_type_override"] == "manual" and doc["shelf"] == "Manuals"


def test_a_save_that_fails_validation_leaves_the_upload_waiting(service):
    module, client, _ = service
    staged = _stage(client, "Camus.pdf", make_pdf("CAMUS")).json()
    assert client.post(f"/intake/{staged['id']}/save", json={"title": "", "type": "novel"}).status_code == 422
    assert client.post(f"/intake/{staged['id']}/save", json={"title": "X", "type": "novel", "isbn_13": "9780306406158"}).status_code == 422
    assert client.post(f"/intake/{staged['id']}/save", json={"title": "X", "type": "poem"}).status_code == 422
    assert [item["id"] for item in client.get("/intake").json()["items"]] == [staged["id"]]
    assert module.list_metadata() == []


def test_discarding_removes_the_staged_file(service):
    module, client, _ = service
    staged = _stage(client, "Gone.pdf", make_pdf("GONE")).json()
    files_before = list(module.INTAKE_DIR.iterdir())
    assert len(files_before) == 2  # the file and its proposal
    assert client.delete(f"/intake/{staged['id']}").json() == {"ok": True}
    assert list(module.INTAKE_DIR.iterdir()) == [] and client.get("/intake").json()["items"] == []
    assert client.get(f"/intake/{staged['id']}").status_code == 404


def test_the_same_file_is_not_staged_or_added_twice(service):
    module, client, _ = service
    content = make_pdf("TWICE\n" + COPYRIGHT_PAGE)
    first = _stage(client, "Twice.pdf", content).json()
    again = _stage(client, "Twice (copy).pdf", content)
    assert again.status_code == 200 and again.json()["duplicate"]["intake_id"] == first["id"]
    doc = client.post(f"/intake/{first['id']}/save", json={"title": "Twice", "type": "book"}).json()
    in_library = _stage(client, "Twice again.pdf", content)
    assert in_library.status_code == 200 and in_library.json()["duplicate"]["document_id"] == doc["document_id"]
    assert client.get("/intake").json()["items"] == []


def test_a_file_named_for_one_format_but_holding_another_is_read_as_what_it_is(service):
    module, client, _ = service
    item = _stage(client, "Camus - A Book.docx", make_pdf("A BOOK\nCAMUS"), "application/octet-stream").json()
    assert item["format"] == "pdf" and "content is PDF" in item["format_note"]
    assert item["filename"].endswith(".pdf")
    saved = client.post(f"/intake/{item['id']}/save", json={"title": "A Book", "type": "book"}).json()
    assert saved["file_type"] == "pdf" and saved["filename"] == item["filename"]


def test_an_epub_is_classified_as_a_book_from_its_own_package(service):
    module, client, _ = service
    item = _stage(client, "download.epub", make_epub(), "application/epub+zip").json()
    assert item["format"] == "epub" and item["has_cover"]
    proposal = item["proposal"]
    assert (proposal["title"], proposal["author"]) == ("The Quiet Library", "Ada Reader")
    assert proposal["publisher"] == "Quiet Press" and proposal["language"] == "en-gb" and proposal["published_year"] == 2011
    assert proposal["type"] in {"book", "novel", "novella", "short_story", "collection", "nonfiction"}
    assert item["sources"]["publisher"] == "file properties"


def test_office_and_text_files_are_read_too(service):
    module, client, _ = service
    notes = _stage(client, "Meeting notes.md", b"# Meeting\n\nWe agreed on the plan.\n" * 5, "text/markdown").json()
    assert notes["format"] == "markdown" and notes["proposal"]["type"] == "writing" and notes["proposal"]["shelf"].startswith("Writing")
    table = _stage(client, "Sales.csv", b"month,total\njan,10\nfeb,20\n", "text/csv").json()
    assert table["format"] == "csv" and table["proposal"]["type"] == "data"


def test_an_unreadable_file_is_still_offered_from_its_name(service):
    module, client, _ = service
    response = _stage(client, "Broken - Someone.pdf", b"%PDF-1.4 this is not really a pdf")
    assert response.status_code == 201
    item = response.json()
    assert item["state"] == "unreadable" and item["error"] and item["proposal"]["title"] == "Broken"
    assert any("could not be read" in note for note in item["notes"])


def test_a_scan_with_no_text_layer_is_called_out(service):
    module, client, _ = service
    item = _stage(client, "scanned.pdf", make_pdf("", pages=3)).json()
    assert any("scan" in note for note in item["notes"])


def test_unsupported_empty_and_oversized_uploads_are_refused(service, monkeypatch):
    module, client, _ = service
    assert _stage(client, "run.exe", b"MZ", "application/octet-stream").status_code == 400
    assert _stage(client, "empty.pdf", b"").status_code == 400
    monkeypatch.setattr(module, "MAX_UPLOAD_BYTES", 10)
    assert _stage(client, "big.pdf", make_pdf("TOO BIG")).status_code == 413
    assert list(module.INTAKE_DIR.iterdir()) == []  # a refused upload leaves nothing behind


def test_an_unknown_upload_id_is_not_found(service):
    module, client, _ = service
    for path in ("/intake/abcdef012345", "/intake/not-an-id", "/intake/abcdef012345/cover"):
        assert client.get(path).status_code == 404
    assert client.post("/intake/abcdef012345/save", json={"title": "X", "type": "novel"}).status_code == 404
    assert client.delete("/intake/abcdef012345").status_code == 404


def test_a_crashed_upload_leaves_nothing_behind_but_a_waiting_one_is_kept(service):
    import os

    module, client, _ = service
    waiting = _stage(client, "Waiting.pdf", make_pdf("WAITING")).json()
    old = time.time() - module.INTAKE_ORPHAN_SECONDS - 60
    partial = module.INTAKE_DIR / ".abcdef012345.uploading"
    orphan = module.INTAKE_DIR / "abcdef012345.pdf"
    fresh_orphan = module.INTAKE_DIR / "fedcba543210.pdf"
    for path in (partial, orphan, fresh_orphan):
        path.write_bytes(b"x")
    for path in (partial, orphan):
        os.utime(path, (old, old))
    assert module.sweep_intake() == 2
    assert not partial.exists() and not orphan.exists()
    assert fresh_orphan.exists()  # could be an upload still being read
    assert [item["id"] for item in client.get("/intake").json()["items"]] == [waiting["id"]]
