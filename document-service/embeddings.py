import os
import threading
from typing import List, Optional

_model = None
_model_name = None
_model_lock = threading.Lock()
DEFAULT_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


def get_model(name: str = DEFAULT_MODEL):
    global _model, _model_name
    with _model_lock:
        if _model is None or _model_name != name:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(name)
            _model_name = name
    return _model


def embed_texts(texts: List[str], model_name: Optional[str] = None) -> List[List[float]]:
    if not texts:
        return []
    model = get_model(model_name or DEFAULT_MODEL)
    vectors = model.encode(
        texts,
        batch_size=int(os.environ.get("MODEL_BATCH_SIZE", "32")),
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return [v.tolist() for v in vectors]
