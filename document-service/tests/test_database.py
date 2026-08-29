import json

import pytest

from database import MetadataStore


def make_record(document_id="abc123abc123", **overrides):
    record = {
        "document_id": document_id,
        "filename": "essay.pdf",
        "file_type": "pdf",
        "title": "Essay",
        "stored_filename": f"{document_id}.pdf",
        "content_sha256": "hash-" + document_id,
        "uploaded_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "pages": 3,
        "chunks": 5,
        "retrieval_units": 9,
        "indexing_status": "queued",
        "indexing_error": None,
        "embedding_model": "test-model",
        "vector_dim": 0,
        "pipeline_version": 5,
        "index_schema_version": 0,
    }
    record.update(overrides)
    return record


@pytest.fixture
def store(tmp_path):
    instance = MetadataStore(tmp_path / "library.db")
    yield instance
    instance.close()


def test_create_get_and_list_round_trip(store):
    created = store.create(make_record())
    assert created["document_id"] == "abc123abc123"
    assert created["indexed_at"] is None
    assert store.get("abc123abc123")["filename"] == "essay.pdf"

    store.create(make_record("def456def456", uploaded_at="2026-02-02T00:00:00Z"))
    listed = store.list_all()
    assert [row["document_id"] for row in listed] == ["def456def456", "abc123abc123"]


def test_find_by_hash_returns_matching_document(store):
    store.create(make_record())
    assert store.find_by_hash("hash-abc123abc123")["document_id"] == "abc123abc123"
    assert store.find_by_hash("missing") is None


def test_update_persists_changes_and_reports_missing(store):
    store.create(make_record())
    updated = store.update(
        "abc123abc123",
        {"indexing_status": "indexed", "vector_dim": 384, "updated_at": "later"},
    )
    assert updated["indexing_status"] == "indexed"
    assert updated["vector_dim"] == 384

    with pytest.raises(KeyError):
        store.update("nope", {"indexing_status": "indexed"})


def test_update_rejects_unknown_columns(store):
    store.create(make_record())
    with pytest.raises(ValueError):
        store.update("abc123abc123", {"totally_made_up": 1})


def test_delete_removes_the_record(store):
    store.create(make_record())
    store.delete("abc123abc123")
    assert store.get("abc123abc123") is None
    store.delete("abc123abc123")  # idempotent


def test_import_legacy_loads_json_once(tmp_path):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "abc123abc123.json").write_text(json.dumps(make_record()))
    (metadata_dir / "broken.json").write_text("{not json")

    store = MetadataStore(tmp_path / "library.db")
    try:
        assert store.import_legacy(metadata_dir) == 1
        assert store.get("abc123abc123")["title"] == "Essay"
        # Second call is a no-op because the table is already populated.
        (metadata_dir / "def456def456.json").write_text(json.dumps(make_record("def456def456")))
        assert store.import_legacy(metadata_dir) == 0
    finally:
        store.close()
