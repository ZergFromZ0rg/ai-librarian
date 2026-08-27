import os
import hashlib
from typing import List, Optional, Dict
from qdrant_client.models import Filter, FieldCondition, MatchValue
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "vault")

client = QdrantClient(url=QDRANT_URL)


def ensure_collection(vector_size: int):
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION not in collections:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
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
                    "text": c.get("text", ""),
                    "document_id": c.get("document_id"),
                    "filename": c.get("filename"),
                    "page": c.get("page"),
                    "chunk_index": c.get("chunk_index"),
                },
            )
        )
        if len(points) >= batch_size:
            client.upsert(collection_name=COLLECTION, points=points)
            points = []

    if points:
        client.upsert(collection_name=COLLECTION, points=points)


def search_vectors(vector: List[float], top_k: int = 5, filters: Optional[Dict] = None):
    """Return top_k nearest points with payload and score.

    `filters` can be a dict like {"document_id": "<id>", "filename": "name.pdf"}
    """
    query_filter = None
    if filters:
        must = []
        for k, v in filters.items():
            if v is None:
                continue
            must.append(FieldCondition(key=k, match=MatchValue(value=v)))
        if must:
            query_filter = Filter(must=must)

    results = client.search(
        collection_name=COLLECTION, query_vector=vector, limit=top_k, with_payload=True, query_filter=query_filter
    )
    out = []
    for r in results:
        out.append({"id": r.id, "score": r.score, "payload": r.payload})
    return out
