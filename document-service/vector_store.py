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
    Modifier,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from lexical import bm25_document_vector, bm25_query_vector

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "vault")
DENSE_VECTOR = "semantic"
SPARSE_VECTOR = "lexical"
INDEX_SCHEMA_VERSION = 6
ALLOW_INDEX_RESET = os.environ.get("ALLOW_INDEX_RESET", "").strip().lower() in {
    "1", "true", "yes", "on"
}

# How the dense (semantic) and sparse (lexical) result lists are merged:
#   rrf  - reciprocal rank fusion: merge by rank position, ignore scores. Robust.
#   dbsf - distribution-based score fusion: Qdrant normalises each score list by
#          its mean/stdev, then sums. Uses score magnitude.
#   rsf  - relative score fusion (client-side): min-max each list to [0, 1] and
#          take FUSION_DENSE_WEIGHT * dense + (1 - weight) * sparse.
# A request may override both per search; this is the default.
FUSION_METHOD = os.environ.get("FUSION_METHOD", "rrf").strip().lower()
FUSION_DENSE_WEIGHT = float(os.environ.get("FUSION_DENSE_WEIGHT", "0.5"))
_NATIVE_FUSION = {"rrf": Fusion.RRF, "dbsf": Fusion.DBSF}

logger = logging.getLogger("ai_librarian.vector_store")

client = QdrantClient(url=QDRANT_URL, timeout=10, check_compatibility=False)


def _create_collection(vector_size: int) -> None:
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={DENSE_VECTOR: VectorParams(size=vector_size, distance=Distance.COSINE)},
        # IDF: Qdrant tracks each term's document frequency and applies the
        # rarity weight at query time, so BM25 stays correct as the library grows.
        sparse_vectors_config={SPARSE_VECTOR: SparseVectorParams(modifier=Modifier.IDF)},
    )


def _point_count() -> int:
    try:
        return client.count(COLLECTION, exact=False).count
    except Exception:
        return -1


def ensure_collection(vector_size: int):
    collections = [collection.name for collection in client.get_collections().collections]
    if COLLECTION not in collections:
        _create_collection(vector_size)
        return

    info = client.get_collection(COLLECTION)
    vectors = info.config.params.vectors
    sparse_vectors = info.config.params.sparse_vectors or {}
    dense = vectors.get(DENSE_VECTOR) if isinstance(vectors, dict) else None
    sparse = sparse_vectors.get(SPARSE_VECTOR)
    sparse_ok = sparse is not None and getattr(sparse, "modifier", None) == Modifier.IDF
    structural_ok = dense is not None and sparse_ok
    dimension_ok = dense is not None and dense.size == vector_size
    if structural_ok and dimension_ok:
        return

    if structural_ok and not dimension_ok and not ALLOW_INDEX_RESET:
        # The dense vector is the right shape of collection but the wrong size:
        # the embedding model changed. That is almost always an accident, and
        # discarding the index means re-embedding the whole library, so refuse.
        raise RuntimeError(
            f"Qdrant collection {COLLECTION!r} was built for {dense.size}-dimensional "
            f"vectors but the embedding model now produces {vector_size}. Refusing to "
            f"discard ~{_point_count()} indexed points. Set ALLOW_INDEX_RESET=1 to drop "
            f"and rebuild the collection, or point QDRANT_COLLECTION at a new name."
        )

    # A structural mismatch (legacy dense-only collection, missing sparse index)
    # or an operator-authorised dimension change: recreate.
    logger.warning(
        "Recreating Qdrant collection %s (held ~%d points) for index schema %s",
        COLLECTION,
        _point_count(),
        INDEX_SCHEMA_VERSION,
    )
    client.delete_collection(COLLECTION)
    _create_collection(vector_size)


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
        sparse_indices, sparse_values = bm25_document_vector(search_text)
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


def _min_max(points):
    """Map point id -> (point, score rescaled to [0, 1] across this one list).

    When every score is equal (including a single hit) they are all equally the
    best that list has to offer, so they normalise to 1.0 rather than 0.0.
    """
    if not points:
        return {}
    scores = [p.score for p in points]
    low, span = min(scores), max(scores) - min(scores)
    if span <= 0:
        return {p.id: (p, 1.0) for p in points}
    return {p.id: (p, (p.score - low) / span) for p in points}


def _relative_score_fusion(dense_points, sparse_points, dense_weight: float):
    dense = _min_max(dense_points)
    sparse = _min_max(sparse_points)
    fused = []
    for pid in set(dense) | set(sparse):
        point = (dense.get(pid) or sparse.get(pid))[0]
        score = dense_weight * (dense[pid][1] if pid in dense else 0.0) + (
            1.0 - dense_weight
        ) * (sparse[pid][1] if pid in sparse else 0.0)
        fused.append((score, point))
    fused.sort(key=lambda item: item[0], reverse=True)
    return [{"id": p.id, "score": score, "payload": p.payload} for score, p in fused]


def search_vectors(
    vector: List[float],
    top_k: int = 5,
    filters: Optional[Dict] = None,
    query_text: Optional[str] = None,
    fusion: Optional[str] = None,
    dense_weight: Optional[float] = None,
):
    """Return fused semantic and exact-symbol matches.

    `filters` can be a dict like {"document_id": "<id>", "filename": "name.pdf"}.
    `fusion` / `dense_weight` override FUSION_METHOD / FUSION_DENSE_WEIGHT.
    """
    if not client.collection_exists(COLLECTION):
        return []

    method = (fusion or FUSION_METHOD).strip().lower()
    weight = FUSION_DENSE_WEIGHT if dense_weight is None else dense_weight

    query_filter = None
    if filters:
        must = []
        for k, v in filters.items():
            if v is None:
                continue
            must.append(FieldCondition(key=k, match=MatchValue(value=v)))
        if must:
            query_filter = Filter(must=must)

    sparse_indices, sparse_values = bm25_query_vector(query_text or "")
    if sparse_indices:
        prefetch_limit = max(20, top_k * 4)
        sparse_query = SparseVector(indices=sparse_indices, values=sparse_values)
        if method == "rsf":
            common = dict(query_filter=query_filter, limit=prefetch_limit, with_payload=True)
            dense_points = client.query_points(
                COLLECTION, query=vector, using=DENSE_VECTOR, **common
            ).points
            sparse_points = client.query_points(
                COLLECTION, query=sparse_query, using=SPARSE_VECTOR, **common
            ).points
            return _relative_score_fusion(dense_points, sparse_points, weight)[:top_k]
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
                    query=sparse_query,
                    using=SPARSE_VECTOR,
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
            ],
            query=FusionQuery(fusion=_NATIVE_FUSION.get(method, Fusion.RRF)),
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
    return [{"id": r.id, "score": r.score, "payload": r.payload} for r in results]


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
