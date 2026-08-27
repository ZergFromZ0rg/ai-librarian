from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

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
            "text": "**Absurd freedom**\n\nThe derivative is ∂f/∂x.",
            "embedding_text": "Absurd freedom The derivative is ∂f/∂x.",
            "embedding": [1.0, 0.0, 0.0],
        },
        {
            "chunk_id": "chunk-two",
            "document_id": "bbbbbbbbbbbb",
            "filename": "two.pdf",
            "page": 2,
            "chunk_index": 0,
            "text": "another topic",
            "embedding_text": "another topic",
            "embedding": [0.0, 1.0, 0.0],
        },
    ]

    vector_store.upsert_chunks(chunks)

    hits = vector_store.search_vectors([1.0, 0.0, 0.0], top_k=2, query_text="∂f/∂x")
    assert hits[0]["payload"]["chunk_id"] == "chunk-one"
    assert hits[0]["payload"]["text"].startswith("**Absurd freedom**")
    filtered = vector_store.search_vectors(
        [1.0, 0.0, 0.0],
        top_k=2,
        filters={"document_id": "bbbbbbbbbbbb"},
        query_text="another topic",
    )
    assert [hit["payload"]["chunk_id"] for hit in filtered] == ["chunk-two"]

    vector_store.delete_document("aaaaaaaaaaaa")

    remaining = vector_store.search_vectors([1.0, 0.0, 0.0], top_k=2, query_text="another topic")
    assert [hit["payload"]["chunk_id"] for hit in remaining] == ["chunk-two"]
    assert vector_store.healthcheck() is True


def test_legacy_dense_collection_is_recreated_for_hybrid_schema(monkeypatch):
    local_client = QdrantClient(":memory:")
    monkeypatch.setattr(vector_store, "client", local_client)
    monkeypatch.setattr(vector_store, "COLLECTION", "legacy-library")
    local_client.create_collection(
        collection_name="legacy-library",
        vectors_config=VectorParams(size=3, distance=Distance.COSINE),
    )

    vector_store.ensure_collection(3)

    params = local_client.get_collection("legacy-library").config.params
    assert vector_store.DENSE_VECTOR in params.vectors
    assert vector_store.SPARSE_VECTOR in params.sparse_vectors
