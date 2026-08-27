import hashlib
import os
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "vault")

client = QdrantClient(url=QDRANT_URL, timeout=10, check_compatibility=False)


def ensure_collection(vector_size: int):
    collections = [collection.name for collection in client.get_collections().collections]
    if COLLECTION not in collections:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        return
    info = client.get_collection(COLLECTION)
    configured_size = info.config.params.vectors.size
    if configured_size != vector_size:
        raise ValueError(
            f"Qdrant collection '{COLLECTION}' expects {configured_size}-dimension vectors, "
            f"but the embedding model returned {vector_size}; use a new collection or restore the original model"
        )


def chunk_id_to_int(cid: str) -> int:
    return int(hashlib.sha256(cid.encode()).hexdigest()[:16], 16) % (2 ** 63)


def upsert_chunks(chunks: List[dict], batch_size: int = 64):
    if not chunks:
        return
    vector_size = len(chunks[0].get("embedding", []))
    if vector_size == 0:
        raise ValueError("Chunks do not contain embeddings")
    ensure_collection(vector_size)

    points = []
    for c in chunks:
        point_id = chunk_id_to_int(c["chunk_id"])
        points.append(
            PointStruct(
                id=point_id,
                vector=c["embedding"],
                payload={
                    "chunk_id": c.get("chunk_id"),
                    "text": c.get("text", ""),
                    "document_id": c.get("document_id"),
                    "filename": c.get("filename"),
                    "page": c.get("page"),
                    "chunk_index": c.get("chunk_index"),
                },
            )
        )
        if len(points) >= batch_size:
            client.upsert(collection_name=COLLECTION, points=points, wait=True)
            points = []

    if points:
        client.upsert(collection_name=COLLECTION, points=points, wait=True)


def search_vectors(vector: List[float], top_k: int = 5, filters: Optional[Dict] = None):
    """Return top_k nearest points with payload and score.

    `filters` can be a dict like {"document_id": "<id>", "filename": "name.pdf"}
    """
    if not client.collection_exists(COLLECTION):
        return []

    query_filter = None
    if filters:
        must = []
        for k, v in filters.items():
            if v is None:
                continue
            must.append(FieldCondition(key=k, match=MatchValue(value=v)))
        if must:
            query_filter = Filter(must=must)

    results = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    ).points
    out = []
    for r in results:
        out.append({"id": r.id, "score": r.score, "payload": r.payload})
    return out


def delete_document(document_id: str) -> None:
    if not client.collection_exists(COLLECTION):
        return
    client.delete(
        collection_name=COLLECTION,
        points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))])
        ),
        wait=True,
    )


def healthcheck() -> bool:
    try:
        client.get_collections()
        return True
    except Exception:
        return False
