from qdrant_client import QdrantClient

import vector_store


def test_vector_upsert_filter_search_and_delete(monkeypatch):
    monkeypatch.setattr(vector_store, "client", QdrantClient(":memory:"))
    monkeypatch.setattr(vector_store, "COLLECTION", "test-library")
    assert vector_store.search_vectors([1.0, 0.0, 0.0]) == []
    chunks = [
        {
            "chunk_id": "chunk-one",
            "document_id": "aaaaaaaaaaaa",
            "filename": "one.pdf",
            "page": 1,
            "chunk_index": 0,
            "text": "absurd freedom",
            "embedding": [1.0, 0.0, 0.0],
        },
        {
            "chunk_id": "chunk-two",
            "document_id": "bbbbbbbbbbbb",
            "filename": "two.pdf",
            "page": 2,
            "chunk_index": 0,
            "text": "another topic",
            "embedding": [0.0, 1.0, 0.0],
        },
    ]

    vector_store.upsert_chunks(chunks)

    hits = vector_store.search_vectors([1.0, 0.0, 0.0], top_k=2)
    assert hits[0]["payload"]["chunk_id"] == "chunk-one"
    filtered = vector_store.search_vectors(
        [1.0, 0.0, 0.0],
        top_k=2,
        filters={"document_id": "bbbbbbbbbbbb"},
    )
    assert [hit["payload"]["chunk_id"] for hit in filtered] == ["chunk-two"]

    vector_store.delete_document("aaaaaaaaaaaa")

    remaining = vector_store.search_vectors([1.0, 0.0, 0.0], top_k=2)
    assert [hit["payload"]["chunk_id"] for hit in remaining] == ["chunk-two"]
    assert vector_store.healthcheck() is True
