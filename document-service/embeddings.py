from typing import List

_model = None


def get_model(name: str = "all-MiniLM-L6-v2"):
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(name)
    return _model


def embed_texts(texts: List[str], model_name: str = "all-MiniLM-L6-v2") -> List[List[float]]:
    model = get_model(model_name)
    vectors = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    # convert numpy arrays to python lists
    return [v.tolist() for v in vectors]
