"""EPUB files can be imported, searched, and displayed as books."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from conftest import wait_for_status


def make_epub() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package version="2.0" '
            'xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>The Quiet Library</dc:title><dc:creator>Ada Reader</dc:creator>'
            '<dc:identifier id="bookid">test-epub</dc:identifier></metadata>'
            '<manifest><item id="chapter" href="chapter.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>',
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>'
            '<body><h1>Chapter One</h1><p>Freedom lives in the quiet library.</p></body></html>',
            compress_type=ZIP_DEFLATED,
        )
    return buffer.getvalue()


def test_epub_auto_import_indexes_and_renders(service, tmp_path):
    module, client, indexed = service
    root = tmp_path / "library" / "Books"
    root.mkdir(parents=True)
    source = root / "quiet-library.epub"
    source.write_bytes(make_epub())

    assert module._auto_ingest_scan() == 1
    docs = client.get("/documents").json()["documents"]
    assert len(docs) == 1
    doc_id = docs[0]["document_id"]
    metadata = wait_for_status(client, doc_id, "indexed")
    assert metadata["file_type"] == "epub"
    assert metadata["kind"] == "book"
    assert metadata["title"] == "The Quiet Library"
    assert metadata["author"] == "Ada Reader"
    assert metadata["pages"] >= 1
    assert any("quiet library" in chunk["payload"]["text"].lower() for chunk in indexed.values())
    assert client.get(f"/documents/{doc_id}/file").headers["content-type"].startswith("application/epub+zip")

    cover = client.get(f"/documents/{doc_id}/thumbnail")
    assert cover.status_code == 200 and cover.content.startswith(b"\xff\xd8")
    page = client.get(f"/documents/{doc_id}/page/1", params={"highlight": "Freedom"})
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
    assert client.get(f"/documents/{doc_id}/page/{metadata['pages'] + 1}").status_code == 404
