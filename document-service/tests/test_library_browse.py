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


def test_auto_ingest_scan_picks_up_files_dropped_into_the_library(service, tmp_path):
    """Simulates a library populated before the app's first start, or a PDF
    dropped in while it's running: module._auto_ingest_scan() (what the
    background timer calls) finds and references it with no UI interaction."""
    module, client, _indexed = service
    root = _library(tmp_path)
    (root / "Essays").mkdir()
    (root / "Essays" / "sisyphus.pdf").write_bytes(make_pdf("One must imagine Sisyphus happy."))

    imported = module._auto_ingest_scan()
    assert imported == 1

    docs = client.get("/documents").json()["documents"]
    assert len(docs) == 1
    assert docs[0]["source_path"] == "Essays/sisyphus.pdf"
    wait_for_status(client, docs[0]["document_id"], "indexed")
    assert not any(module.DOCUMENTS_DIR.glob("*.pdf"))

    # A second scan with nothing new is a no-op: it doesn't re-import, and it
    # doesn't re-hash the file it already knows about (the whole point of
    # tracking already-known source_paths).
    hashed = []
    original_hash_file = module.hash_file
    module.hash_file = lambda path: hashed.append(path) or original_hash_file(path)
    try:
        assert module._auto_ingest_scan() == 0
    finally:
        module.hash_file = original_hash_file
    assert hashed == []
    assert len(client.get("/documents").json()["documents"]) == 1


def test_auto_ingest_scan_still_dedupes_by_content(service, tmp_path):
    module, client, _indexed = service
    root = _library(tmp_path)
    pdf = make_pdf("absurd freedom, scanned twice")
    (root / "original.pdf").write_bytes(pdf)
    (root / "duplicate.pdf").write_bytes(pdf)

    imported = module._auto_ingest_scan()
    assert imported == 1
    assert len(client.get("/documents").json()["documents"]) == 1


def test_library_root_can_be_narrowed_from_a_broad_mount(service, tmp_path):
    """The mount can stay broad (a whole home dir, say) while the actual
    collection is picked from the website: /library/root narrows what
    auto-ingest scans and what Browse opens to by default, with no
    docker-compose/.env edit."""
    module, client, _indexed = service
    root = _library(tmp_path)
    (root / "Unrelated").mkdir()
    (root / "Unrelated" / "invoice.pdf").write_bytes(make_pdf("not part of the library"))
    (root / "Books").mkdir()
    (root / "Books" / "sisyphus.pdf").write_bytes(make_pdf("One must imagine Sisyphus happy."))

    assert client.get("/library/root").json() == {"path": "", "valid": True}

    resp = client.post("/library/root", json={"path": "Books"})
    assert resp.status_code == 200 and resp.json() == {"path": "Books"}
    assert client.get("/library/root").json() == {"path": "Books", "valid": True}

    # Auto-ingest now only sees what's under the narrowed root.
    imported = module._auto_ingest_scan()
    assert imported == 1
    docs = client.get("/documents").json()["documents"]
    assert [doc["source_path"] for doc in docs] == ["Books/sisyphus.pdf"]

    # Reset back to the whole mount.
    assert client.post("/library/root", json={"path": ""}).json() == {"path": ""}
    assert client.get("/library/root").json()["path"] == ""


def test_library_root_falls_back_when_the_chosen_folder_disappears(service, tmp_path):
    module, client, _indexed = service
    root = _library(tmp_path)
    (root / "Temp").mkdir()
    client.post("/library/root", json={"path": "Temp"})

    (root / "Temp").rmdir()
    result = client.get("/library/root").json()
    assert result == {"path": "", "valid": False}
    assert module.library_root_path() == module.INGEST_ROOT


def test_library_root_rejects_paths_outside_the_mount(service, tmp_path):
    _module, client, _indexed = service
    _library(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert client.post("/library/root", json={"path": str(outside)}).status_code == 403
    assert client.post("/library/root", json={"path": "missing"}).status_code == 404


def test_auto_ingest_thread_only_starts_when_interval_is_positive(service):
    module, _client, _indexed = service
    assert not any(t.name == "auto-ingest-worker" for t in module.WORKER_THREADS)  # conftest sets 0

    module.stop_workers()
    try:
        module.AUTO_INGEST_INTERVAL_SECONDS = 0.05
        module.start_workers()
        assert any(t.name == "auto-ingest-worker" for t in module.WORKER_THREADS)
    finally:
        module.stop_workers()
        module.AUTO_INGEST_INTERVAL_SECONDS = 0
        module.start_workers()


def test_tree_rejects_escaping_paths(service, tmp_path):
    _module, client, _indexed = service
    _library(tmp_path)
    assert client.get("/library/tree", params={"path": "../.."}).status_code == 403
    assert client.get("/library/tree", params={"path": "nope/missing"}).status_code == 404
    assert client.post("/library/import", json={"path": "../secrets.pdf"}).status_code in (403, 404)
