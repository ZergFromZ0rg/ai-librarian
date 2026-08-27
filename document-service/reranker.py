from typing import List

_model = None
_model_name = None


def get_model(name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
    global _model
    global _model_name
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(name)
        _model_name = name
    return _model


def rerank(query: str, passages: List[str], model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> List[float]:
    """Return a list of scores (higher = more relevant) aligned with `passages`."""
    if not passages:
        return []
    model = get_model(model_name)
    pairs = [[query, p] for p in passages]
    scores = model.predict(pairs, show_progress_bar=False)
    return [float(s) for s in scores]


def model_info():
    """Return info about the reranker model: name and whether loaded."""
    return {"model_name": _model_name, "loaded": _model is not None}
