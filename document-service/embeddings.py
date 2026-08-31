import os
import threading
from typing import List, Optional

import numpy as np

_model = None
_model_name = None
_model_lock = threading.Lock()

# all-MiniLM-L6-v2 (2021) trails current small models on retrieval quality and
# only reads ~256 word-pieces of a chunk. e5-small-v2 is a same-size, same-speed
# 384-dimensional replacement with a 512-token window. It was trained with each
# input labelled by role, so a query and a stored passage must be prefixed
# differently or results degrade measurably.
DEFAULT_MODEL = os.environ.get("EMBEDDING_MODEL", "intfloat/e5-small-v2")
# Role prefixes the model expects. Set both to "" for an unprefixed model such as
# sentence-transformers/all-MiniLM-L6-v2 (also switch EMBEDDING_MODEL back).
QUERY_PREFIX = os.environ.get("EMBEDDING_QUERY_PREFIX", "query: ")
PASSAGE_PREFIX = os.environ.get("EMBEDDING_PASSAGE_PREFIX", "passage: ")
_PREFIX = {"query": QUERY_PREFIX, "passage": PASSAGE_PREFIX}
# Matryoshka models (nomic-embed, mxbai) stay accurate when truncated to a
# shorter prefix of their output, keeping storage flat. 0/unset uses the model's
# native width. Changing this needs a re-index and an INDEX_SCHEMA_VERSION bump.
TRUNCATE_DIMS = int(os.environ.get("EMBEDDING_DIMS", "0")) or None


def get_model(name: str = DEFAULT_MODEL):
    global _model, _model_name
    with _model_lock:
        if _model is None or _model_name != name:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(name)
            _model_name = name
    return _model


def embed_texts(
    texts: List[str], kind: str = "passage", model_name: Optional[str] = None
) -> List[List[float]]:
    """Embed `texts` as unit vectors.

    `kind` is "passage" for stored chunks or "query" for a search string; it
    selects the role prefix the model was trained to expect.
    """
    if not texts:
        return []
    prefix = _PREFIX.get(kind, "")
    model = get_model(model_name or DEFAULT_MODEL)
    vectors = model.encode(
        [prefix + text for text in texts],
        batch_size=int(os.environ.get("MODEL_BATCH_SIZE", "32")),
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    if TRUNCATE_DIMS and vectors.shape[1] > TRUNCATE_DIMS:
        vectors = vectors[:, :TRUNCATE_DIMS]
        vectors = vectors / np.clip(
            np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12, None
        )
    return [v.tolist() for v in vectors]
