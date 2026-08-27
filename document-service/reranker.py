import os
import threading
from typing import List, Optional

_model = None
_model_name = None
_model_lock = threading.Lock()
DEFAULT_MODEL = os.environ.get("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")


def get_model(name: str = DEFAULT_MODEL):
    global _model
    global _model_name
    with _model_lock:
        if _model is None or _model_name != name:
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(name)
            _model_name = name
    return _model


def rerank(query: str, passages: List[str], model_name: Optional[str] = None) -> List[float]:
    """Return a list of scores (higher = more relevant) aligned with `passages`."""
    if not passages:
        return []
    model = get_model(model_name or DEFAULT_MODEL)
    pairs = [[query, p] for p in passages]
    scores = model.predict(pairs, show_progress_bar=False)
    return [float(s) for s in scores]


def model_info():
    """Return info about the reranker model: name and whether loaded."""
    return {"model_name": _model_name, "loaded": _model is not None}
