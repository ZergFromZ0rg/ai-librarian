import os
import threading
import time
from typing import List, Optional

_model = None
_model_name = None
_model_lock = threading.Lock()
_last_used = 0.0
DEFAULT_MODEL = os.environ.get("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")


def get_model(name: str = DEFAULT_MODEL):
    global _model, _model_name, _last_used
    with _model_lock:
        if _model is None or _model_name != name:
            from sentence_transformers import CrossEncoder

            _model = CrossEncoder(name)
            _model_name = name
        _last_used = time.monotonic()
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


def unload_if_idle(idle_seconds: float) -> bool:
    """Drop the model when it has not been used for ``idle_seconds``, giving
    its memory back; the next call loads it again. True if it was unloaded."""
    global _model, _model_name
    with _model_lock:
        if _model is None or time.monotonic() - _last_used < idle_seconds:
            return False
        _model = None
        _model_name = None
    _release_memory()
    return True


def _release_memory() -> None:
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass
