import hashlib
import logging
import os
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    Fusion,
    FusionQuery,
    MatchValue,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from lexical import sparse_vector

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "vault")
DENSE_VECTOR = "semantic"
SPARSE_VECTOR = "lexical"
INDEX_SCHEMA_VERSION = 4

logger = logging.getLogger("ai_librarian.vector_store")

client = QdrantClient(url=QDRANT_URL, timeout=10, check_compatibility=False)


def ensure_collection(vector_size: int):
    collections = [collection.name for collection in client.get_collections().collections]
    if COLLECTION not in collections:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={DENSE_VECTOR: VectorParams(size=vector_size, distance=Distance.COSINE)},
            sparse_vectors_config={SPARSE_VECTOR: SparseVectorParams()},
        )
        return

    info = client.get_collection(COLLECTION)
    vectors = info.config.params.vectors
    sparse_vectors = info.config.params.sparse_vectors or {}
    compatible = (
        isinstance(vectors, dict)
        and DENSE_VECTOR in vectors
        and vectors[DENSE_VECTOR].size == vector_size
        and SPARSE_VECTOR in sparse_vectors
    )
    if not compatible:
        logger.warning(
            "Recreating derived Qdrant collection %s for hybrid index schema %s",
            COLLECTION,
            INDEX_SCHEMA_VERSION,
        )
        client.delete_collection(COLLECTION)
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={DENSE_VECTOR: VectorParams(size=vector_size, distance=Distance.COSINE)},
            sparse_vectors_config={SPARSE_VECTOR: SparseVectorParams()},
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
        search_text = c.get("embedding_text") or c.get("text", "")
        sparse_indices, sparse_values = sparse_vector(search_text)
        points.append(
            PointStruct(
                id=point_id,
                vector={
                    DENSE_VECTOR: c["embedding"],
                    SPARSE_VECTOR: SparseVector(indices=sparse_indices, values=sparse_values),
                },
                payload={
                    "chunk_id": c.get("chunk_id"),
                    "group_id": c.get("group_id"),
                    "text": c.get("text", ""),
                    "lead_in": c.get("lead_in", ""),
                    "retrieval_text": c.get("retrieval_text", search_text),
                    "embedding_text": search_text,
                    "document_id": c.get("document_id"),
                    "filename": c.get("filename"),
                    "page": c.get("page"),
                    "page_end": c.get("page_end", c.get("page")),
                    "chunk_index": c.get("chunk_index"),
                    "group_index": c.get("group_index"),
                    "retrieval_index": c.get("retrieval_index"),
                    "retrieval_kind": c.get("retrieval_kind"),
                    "block_types": c.get("block_types", []),
                    "protected_type": c.get("protected_type"),
                },
            )
        )
        if len(points) >= batch_size:
            client.upsert(collection_name=COLLECTION, points=points, wait=True)
            points = []

    if points:
        client.upsert(collection_name=COLLECTION, points=points, wait=True)


def search_vectors(
    vector: List[float],
    top_k: int = 5,
    filters: Optional[Dict] = None,
    query_text: Optional[str] = None,
):
    """Return fused semantic and exact-symbol matches.

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

    sparse_indices, sparse_values = sparse_vector(query_text or "")
    if sparse_indices:
        prefetch_limit = max(20, top_k * 4)
        results = client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                Prefetch(
                    query=vector,
                    using=DENSE_VECTOR,
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
                Prefetch(
                    query=SparseVector(indices=sparse_indices, values=sparse_values),
                    using=SPARSE_VECTOR,
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
            query_filter=query_filter,
        ).points
    else:
        results = client.query_points(
            collection_name=COLLECTION,
            query=vector,
            using=DENSE_VECTOR,
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
