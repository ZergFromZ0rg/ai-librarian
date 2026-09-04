"""Browsing the mounted library and importing files in place."""

from conftest import make_pdf, wait_for_status


def _library(tmp_path):
    root = tmp_path / "library"
    root.mkdir(exist_ok=True)
    return root


def test_tree_lists_folders_first_then_pdfs(service, tmp_path):
    _module, client, _indexed = service
    root = _library(tmp_path)
    (root / "Physics").mkdir()
    (root / "Physics" / "feynman.pdf").write_bytes(make_pdf("Feynman on the absurd."))
    (root / "notes.pdf").write_bytes(make_pdf("Some notes about freedom."))
    (root / "readme.txt").write_text("ignored")
    (root / ".hidden").mkdir()

    tree = client.get("/library/tree").json()
    assert tree["path"] == "" and tree["parent"] is None
    names = [(e["name"], e["type"]) for e in tree["entries"]]
    assert names == [("Physics", "dir"), ("notes.pdf", "file")]  # dir first, .hidden + .txt skipped
    assert tree["entries"][0]["pdf_count"] == 1
    assert tree["entries"][1]["indexed"] is False

    sub = client.get("/library/tree", params={"path": "Physics"}).json()
    assert sub["path"] == "Physics" and sub["parent"] == ""
    assert [e["name"] for e in sub["entries"]] == ["feynman.pdf"]


def test_import_one_file_references_it_in_place(service, tmp_path):
    module, client, _indexed = service
    root = _library(tmp_path)
    (root / "Camus").mkdir()
    src = root / "Camus" / "sisyphus.pdf"
    src.write_bytes(make_pdf("One must imagine Sisyphus happy. absurd freedom."))

    resp = client.post("/library/import", json={"path": "Camus/sisyphus.pdf"})
    assert resp.status_code == 201
    doc_id = resp.json()["document_id"]
    assert resp.json()["source_path"] == "Camus/sisyphus.pdf"
    wait_for_status(client, doc_id, "indexed")

    # No copy was made under data/app/documents/
    assert not any(module.DOCUMENTS_DIR.glob("*.pdf"))
    assert src.exists()

    # The tree now shows it as indexed.
    sub = client.get("/library/tree", params={"path": "Camus"}).json()
    assert sub["entries"][0]["indexed"] is True
    assert sub["entries"][0]["document_id"] == doc_id

    # It serves like any other document.
    assert client.get(f"/documents/{doc_id}/file").status_code == 200

    # Removing it un-indexes but leaves the original on disk.
    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert src.exists()
    assert client.get(f"/documents/{doc_id}").status_code == 404


def test_import_a_folder_recurses(service, tmp_path):
    _module, client, _indexed = service
    root = _library(tmp_path)
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "top.pdf").write_bytes(make_pdf("absurd freedom one"))
    (root / "a" / "b" / "deep.pdf").write_bytes(make_pdf("absurd freedom two"))

    resp = client.post("/library/import", json={"path": "a"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    import time

    deadline = time.time() + 5
    job = None
    while time.time() < deadline:
        job = client.get(f"/admin/ingest-status/{job_id}").json()
        if job["state"] in {"done", "partial", "error"}:
            break
        time.sleep(0.05)
    assert job["state"] == "done"
    assert {entry["file"] for entry in job["files"]} == {"a/top.pdf", "a/b/deep.pdf"}


def test_import_dot_attaches_the_whole_library_root(service, tmp_path):
    """The UI's "Attach main library folder" button posts path="." — this
    must resolve to INGEST_ROOT itself and pick up nested PDFs too."""
    _module, client, _indexed = service
    root = _library(tmp_path)
    (root / "top.pdf").write_bytes(make_pdf("absurd freedom at the root"))
    (root / "Nested").mkdir()
    (root / "Nested" / "deep.pdf").write_bytes(make_pdf("absurd freedom nested"))

    resp = client.post("/library/import", json={"path": "."})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    import time

    deadline = time.time() + 5
    job = None
    while time.time() < deadline:
        job = client.get(f"/admin/ingest-status/{job_id}").json()
        if job["state"] in {"done", "partial", "error"}:
            break
        time.sleep(0.05)
    assert job["state"] == "done"
    assert {entry["file"] for entry in job["files"]} == {"top.pdf", "Nested/deep.pdf"}


def test_import_deduplicates_by_content(service, tmp_path):
    _module, client, _indexed = service
    root = _library(tmp_path)
    pdf = make_pdf("absurd freedom identical bytes")
    (root / "one.pdf").write_bytes(pdf)
    (root / "copy.pdf").write_bytes(pdf)

    first = client.post("/library/import", json={"path": "one.pdf"})
    assert first.status_code == 201
    second = client.post("/library/import", json={"path": "copy.pdf"})
    assert second.status_code == 200 and second.json()["deduplicated"] is True
    assert second.json()["document_id"] == first.json()["document_id"]


def test_tree_rejects_escaping_paths(service, tmp_path):
    _module, client, _indexed = service
    _library(tmp_path)
    assert client.get("/library/tree", params={"path": "../.."}).status_code == 403
    assert client.get("/library/tree", params={"path": "nope/missing"}).status_code == 404
    assert client.post("/library/import", json={"path": "../secrets.pdf"}).status_code in (403, 404)
