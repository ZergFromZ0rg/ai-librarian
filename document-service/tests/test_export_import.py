"""Export then import preserves what the reader created, and bad archives are refused."""
import hashlib
import io
import json
import zipfile

import pytest
from conftest import make_pdf, wait_for_status
from PIL import Image

import portability


def cover_png():
    out = io.BytesIO()
    Image.new("RGB", (60, 90), "#aa3322").save(out, format="PNG")
    return out.getvalue()


def upload(client, text="The absurd and freedom."):
    response = client.post("/documents", files={"file": ("original.pdf", make_pdf(text), "application/pdf")})
    return wait_for_status(client, response.json()["document_id"], "indexed")


def seed(client):
    """A source book with a review, a catalogue book with a cover, a note and a word."""
    source = upload(client)["document_id"]
    assert client.patch(f"/documents/{source}", json={
        "title": "My Camus", "author": "Albert Camus", "rating": 4.5, "review": "Stayed with me.",
        "liked": True, "review_spoiler": True, "reread": True, "reading_status": "read", "finished_at": "2026-03-04", "genres": ["Philosophy"],
    }).status_code == 200
    book = client.post("/owned-books", json={"title": "Paper Book", "author": "Someone"}).json()["book"]["document_id"]
    client.patch(f"/documents/{book}", json={"rating": 3, "review": "Fine.", "page_count": 210, "isbn_13": "9780679720201"})
    assert client.put(f"/documents/{book}/cover", files={"file": ("c.png", cover_png(), "image/png")}).status_code == 200
    note = client.post("/notes", json={"text": "## Thought\nAbsurd indeed.", "source_document_id": source,
                                      "source_page": 1, "source_quote": "The absurd and freedom."}).json()["document_id"]
    wait_for_status(client, note, "indexed")
    word = client.post("/vocabulary", json={"word": "Sisyphean", "definition": "Endless and futile.", "document_id": source}).json()
    return source, book, note, word


def export_bytes(client):
    response = client.get("/export")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    return response.content


def import_archive(client, data, **params):
    return client.post("/import", params=params, files={"file": ("export.zip", data, "application/zip")})


def wipe_reader_data(module, client, source, book, note, word):
    client.delete(f"/documents/{book}")
    client.delete(f"/documents/{note}")
    client.delete(f"/vocabulary/{word['word_id']}")
    module.STORE.update(source, {"rating": None, "review": None, "reading_status": None, "finished_at": None,
                                 "read_at": None, "genres_json": None, "title": "original", "author": None,
                                 "liked": None, "review_spoiler": None, "reread": None,
                                 "metadata_edited": 0})


def test_archive_is_human_readable_and_excludes_regenerable_data(service):
    _, client, _ = service
    source, book, note, word = seed(client)
    with zipfile.ZipFile(io.BytesIO(export_bytes(client))) as archive:
        names = set(archive.namelist())
        assert {"manifest.json", "README.txt", "books.json", "notes.json", "vocabulary.json",
                f"notes/{note}.md", f"covers/{book}.jpg"} <= names
        assert not any(name.endswith((".pdf", ".db")) for name in names)
        assert "Absurd indeed." in archive.read(f"notes/{note}.md").decode()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["counts"] == {"books": 2, "notes": 1, "vocabulary": 1, "covers": 1, "annotated_copies": 0}
        assert manifest["includes_source_files"] is False
        books = {item["document_id"]: item for item in json.loads(archive.read("books.json"))}
        assert books[source]["review"] == "Stayed with me." and books[source]["genres"] == ["Philosophy"]
        assert "chunks" not in books[source] and "indexing_status" not in books[source]


def test_round_trip_restores_everything_into_a_library_that_lost_it(service):
    module, client, _ = service
    source, book, note, word = seed(client)
    data = export_bytes(client)
    wipe_reader_data(module, client, source, book, note, word)

    preview = import_archive(client, data).json()  # dry run is the default
    assert preview["dry_run"] is True
    assert preview["books"]["created"] == 1 and preview["books"]["updated"] == 1
    assert preview["notes"]["created"] == 1 and preview["vocabulary"]["created"] == 1
    assert client.get(f"/documents/{book}").status_code == 404  # nothing changed yet
    assert client.get("/notes").json()["notes"] == []

    report = import_archive(client, data, dry_run="false").json()
    assert report["books"]["created"] == 1 and report["books"]["updated"] == 1 and report["problems"] == []

    restored_source = client.get(f"/documents/{source}").json()
    assert (restored_source["title"], restored_source["author"], restored_source["rating"]) == ("My Camus", "Albert Camus", 4.5)
    assert (restored_source["liked"], restored_source["review_spoiler"], restored_source["reread"]) == (1, 1, 1)
    assert restored_source["review"] == "Stayed with me." and restored_source["reading_status"] == "read"
    assert restored_source["finished_at"] == "2026-03-04" and restored_source["read_at"] == "2026-03-04T00:00:00Z"
    assert restored_source["genres_json"] == '["Philosophy"]'

    restored_book = client.get(f"/documents/{book}").json()
    assert restored_book["record_type"] == "standalone" and restored_book["title"] == "Paper Book"
    assert (restored_book["rating"], restored_book["page_count"], restored_book["isbn_13"]) == (3, 210, "9780679720201")
    assert client.get(f"/documents/{book}/thumbnail").headers["content-type"] == "image/jpeg"

    notes = client.get("/notes", params={"source_document_id": source}).json()["notes"]
    assert len(notes) == 1 and notes[0]["text"] == "## Thought\nAbsurd indeed."
    assert notes[0]["source_page"] == 1 and notes[0]["source_quote"] == "The absurd and freedom."
    wait_for_status(client, notes[0]["document_id"], "indexed")
    words = client.get("/vocabulary", params={"document_id": source}).json()["words"]
    assert [item["word"] for item in words] == ["Sisyphean"]

    # Importing the same archive again changes nothing.
    again = import_archive(client, data, dry_run="false").json()
    assert again["books"]["created"] == 0 and again["notes"]["created"] == 0 and again["vocabulary"]["created"] == 0
    assert len(client.get("/notes").json()["notes"]) == 1


def test_existing_values_are_kept_unless_replace_is_chosen(service):
    module, client, _ = service
    source, book, note, word = seed(client)
    data = export_bytes(client)
    client.patch(f"/documents/{source}", json={"rating": 2, "review": "Changed my mind."})

    kept = import_archive(client, data, dry_run="false").json()
    assert kept["books"]["kept_existing_values"] >= 2
    assert client.get(f"/documents/{source}").json()["rating"] == 2

    replaced = import_archive(client, data, dry_run="false", conflict="replace").json()
    assert replaced["books"]["updated"] >= 1
    after = client.get(f"/documents/{source}").json()
    assert after["rating"] == 4.5 and after["review"] == "Stayed with me."


def test_books_whose_file_is_missing_are_reported_not_invented(service):
    module, client, _ = service
    source, book, note, word = seed(client)
    data = export_bytes(client)
    client.delete(f"/documents/{source}")
    report = import_archive(client, data, dry_run="false").json()
    assert report["books"]["skipped"] == 1
    assert any("scan your library" in message for message in report["problems"])
    # The note's book is gone too, so it is reported rather than orphaned.
    assert report["notes"] == {"created": 0, "unchanged": 0, "skipped": 1}
    assert client.get(f"/documents/{source}").status_code == 404


def build_zip(files, manifest_overrides=None, checksums=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        sums = {}
        for name, data in files.items():
            archive.writestr(name, data)
            sums[name] = hashlib.sha256(data).hexdigest()
        manifest = {"format": "ai-librarian-export", "schema_version": 1, "checksums": checksums or sums,
                    **(manifest_overrides or {})}
        archive.writestr("manifest.json", json.dumps(manifest))
    return out.getvalue()


def test_bad_archives_are_refused_and_leave_the_library_untouched(service):
    _, client, _ = service
    seed(client)
    before = client.get("/documents").json()["documents"]
    good = {"books.json": b"[]", "notes.json": b"[]", "vocabulary.json": b"[]"}

    cases = {
        "not a zip": b"hello",
        "no manifest": (lambda o: (zipfile.ZipFile(o, "w").writestr("books.json", "[]"), o.getvalue())[1])(io.BytesIO()),
        "path traversal": build_zip({**good, "../evil.json": b"{}"}),
        "unexpected file": build_zip({**good, "notes/../../x.md": b"x"}),
        "newer format": build_zip(good, {"schema_version": 99}),
        "checksum mismatch": build_zip(good, checksums={name: "0" * 64 for name in good}),
        "unlisted file": build_zip(good, checksums={"books.json": hashlib.sha256(b"[]").hexdigest()}),
        "bad json": build_zip({**good, "books.json": b"{not json"}),
    }
    for label, data in cases.items():
        response = import_archive(client, data, dry_run="false")
        assert response.status_code == 422, label
    assert client.get("/documents").json()["documents"] == before


def test_invalid_book_values_are_skipped_individually(service):
    _, client, _ = service
    bad = [{"document_id": "abc123abc123", "record_type": "standalone", "title": "Good", "rating": 4},
           {"document_id": "def456def456", "record_type": "standalone", "title": "Bad", "rating": 99}]
    data = build_zip({"books.json": json.dumps(bad).encode(), "notes.json": b"[]", "vocabulary.json": b"[]"})
    report = import_archive(client, data, dry_run="false").json()
    assert report["books"]["created"] == 1 and report["books"]["skipped"] == 1
    assert client.get("/documents/abc123abc123").json()["rating"] == 4
    assert client.get("/documents/def456def456").status_code == 404


def test_decompression_bomb_is_rejected(tmp_path):
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("books.json", b"[" + b" " * 20_000_000 + b"]")
        archive.writestr("manifest.json", json.dumps({"format": "ai-librarian-export", "schema_version": 1, "checksums": {}}))
    try:
        portability.Archive(path)
    except portability.ArchiveError as exc:
        assert "bomb" in str(exc)
    else:
        raise AssertionError("expected the archive to be refused")


# ------------------------------------------------- annotated PDF copies
def pdf_with_mark(pages=1, comment="my mark"):
    import fitz
    document = fitz.open()
    for number in range(pages):
        page = document.new_page()
        page.insert_text((72, 100), f"The absurd and freedom. Page {number + 1}.", fontsize=12)
    page = document[0]
    mark = page.add_highlight_annot(page.search_for("absurd and freedom")[0])
    mark.set_info(content=comment)
    mark.update()
    return document.tobytes()


def write_archive(path, **kwargs):
    base = dict(books=[], notes=[], vocabulary=[], covers=[], app_version="test", created_at="now", timezone_name="UTC")
    return portability.build_archive(path, **{**base, **kwargs})


def test_archive_carries_annotated_pdfs_byte_for_byte_with_checksums(tmp_path):
    pdf = tmp_path / "copy.pdf"
    pdf.write_bytes(pdf_with_mark())
    archive_path = tmp_path / "out.zip"
    manifest = write_archive(archive_path, annotated=[("abc123", pdf)])
    assert manifest["counts"]["annotated_copies"] == 1 and manifest["schema_version"] == 2
    assert manifest["checksums"]["annotated/abc123.pdf"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
    with portability.Archive(archive_path) as archive:
        assert archive.has_annotated_copy("abc123") and not archive.has_annotated_copy("nope")
        target = tmp_path / "restored.pdf"
        assert archive.extract_annotated_copy("abc123", target) is True
        assert target.read_bytes() == pdf.read_bytes()
        assert archive.extract_annotated_copy("nope", tmp_path / "x.pdf") is False


def test_a_damaged_annotated_pdf_is_caught_by_its_checksum(tmp_path):
    pdf = tmp_path / "copy.pdf"
    pdf.write_bytes(pdf_with_mark())
    good = tmp_path / "good.zip"
    write_archive(good, annotated=[("abc123", pdf)])
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "annotated/abc123.pdf":
                data = data[:-5] + b"XXXXX"
            target.writestr(info.filename, data)
    with pytest.raises(portability.ArchiveError, match="checksum"):
        portability.Archive(tampered)


def test_a_pdf_too_large_for_the_archive_is_skipped_on_export_and_refused_on_import(tmp_path, monkeypatch):
    pdf = tmp_path / "copy.pdf"
    pdf.write_bytes(pdf_with_mark())
    monkeypatch.setattr(portability, "MAX_PDF_ENTRY_BYTES", 100)
    skipped = tmp_path / "skipped.zip"
    manifest = write_archive(skipped, annotated=[("abc123", pdf)])
    assert manifest["counts"]["annotated_copies"] == 0 and manifest["skipped_annotated_copies"] == ["abc123"]
    monkeypatch.setattr(portability, "MAX_PDF_ENTRY_BYTES", 1024 * 1024 * 1024)
    big = tmp_path / "big.zip"
    write_archive(big, annotated=[("abc123", pdf)])
    monkeypatch.setattr(portability, "MAX_PDF_ENTRY_BYTES", 100)
    with pytest.raises(portability.ArchiveError, match="too large"):
        portability.Archive(big)


def test_pdf_entries_are_streamed_not_held_in_memory(tmp_path, monkeypatch):
    pdf = tmp_path / "copy.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * (3 * 1024 * 1024))
    archive_path = tmp_path / "out.zip"
    write_archive(archive_path, annotated=[("abc123", pdf)])
    reads = []
    original = portability.Archive._read
    monkeypatch.setattr(portability.Archive, "_read", lambda self, name: (reads.append(name), original(self, name))[1])
    with portability.Archive(archive_path):
        pass
    assert "annotated/abc123.pdf" not in reads


def annotate_stored_copy(module, client, doc_id, comment="my mark", pages=None):
    stored = module.pdf_path(module.read_metadata(doc_id))
    import fitz
    with fitz.open(stored) as document:
        page = document[0]
        mark = page.add_highlight_annot(page.search_for("absurd and freedom")[0])
        mark.set_info(content=comment)
        mark.update()
        data = document.tobytes()
    response = client.post(f"/documents/{doc_id}/annotated-copy", files={"file": ("a.pdf", data, "application/pdf")})
    assert response.status_code == 200, response.text
    return data


def test_exported_annotated_copies_are_restored_after_the_library_loses_them(service):
    module, client, _ = service
    source, book, note, word = seed(client)
    data = annotate_stored_copy(module, client, source)
    exported = export_bytes(client)
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        assert f"annotated/{source}.pdf" in archive.namelist()
        assert json.loads(archive.read("manifest.json"))["counts"]["annotated_copies"] == 1
        assert archive.read(f"annotated/{source}.pdf") == data

    module.annotated_copy_path(source).unlink()
    module.STORE.update(source, {"file_sig": None})
    assert client.get(f"/documents/{source}/annotations").json()["source"] == "original"

    preview = import_archive(client, exported).json()  # dry run is the default
    assert preview["dry_run"] is True and preview["annotated_copies"] == {"restored": 1, "kept": 0}
    assert not module.annotated_copy_path(source).exists(), "a dry run must not write anything"

    report = import_archive(client, exported, dry_run="false").json()
    assert report["annotated_copies"]["restored"] == 1 and report["problems"] == []
    assert module.annotated_copy_path(source).read_bytes() == data
    restored = client.get(f"/documents/{source}/annotations").json()
    assert restored["source"] == "annotated-copy"
    assert any(mark["comment"] == "my mark" for mark in restored["annotations"])


def test_an_existing_annotated_copy_is_kept_unless_the_file_is_preferred(service):
    module, client, _ = service
    source, *_ = seed(client)
    annotate_stored_copy(module, client, source, comment="from the backup")
    exported = export_bytes(client)
    newer = annotate_stored_copy(module, client, source, comment="made since")

    kept = import_archive(client, exported, dry_run="false").json()
    assert kept["annotated_copies"] == {"restored": 0, "kept": 1}
    assert module.annotated_copy_path(source).read_bytes() == newer

    replaced = import_archive(client, exported, dry_run="false", conflict="replace").json()
    assert replaced["annotated_copies"]["restored"] == 1
    assert any(m["comment"] == "from the backup" for m in client.get(f"/documents/{source}/annotations").json()["annotations"])


def test_an_annotated_copy_with_the_wrong_page_count_is_reported_not_attached(service, tmp_path):
    module, client, _ = service
    source, *_ = seed(client)
    book = module.read_metadata(source)
    wrong = tmp_path / "wrong.pdf"
    wrong.write_bytes(pdf_with_mark(pages=3))
    archive_path = tmp_path / "wrong.zip"
    record = portability.book_record(book)
    write_archive(archive_path, books=[record], annotated=[(source, wrong)])
    report = import_archive(client, archive_path.read_bytes(), dry_run="false").json()
    assert report["annotated_copies"] == {"restored": 0, "kept": 0}
    assert any("annotated copy was skipped" in message and "pages" in message for message in report["problems"])
    assert not module.annotated_copy_path(source).exists()
