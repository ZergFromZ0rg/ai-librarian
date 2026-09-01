"""Download the embedding and reranker models into the Hugging Face cache.

Run this to make the first search fast and deterministic instead of waiting on
two downloads at request time:

    docker compose run --rm document-service python warm_models.py

It writes to ``HF_HOME`` (``/models/huggingface`` in the container, persisted as
``data/models/``). For a fully self-contained image, build with
``PREBAKE_MODELS=1`` instead — the Dockerfile runs this script at build time.
"""

import os

from embeddings import DEFAULT_MODEL as EMBEDDING_MODEL, get_model as get_embedding_model
from reranker import DEFAULT_MODEL as RERANK_MODEL, get_model as get_rerank_model


def main() -> None:
    print(f"Fetching embedding model: {EMBEDDING_MODEL}", flush=True)
    get_embedding_model()
    print(f"Fetching reranker model:  {RERANK_MODEL}", flush=True)
    get_rerank_model()

    import math_ocr

    if math_ocr.ENABLED:
        print(f"Fetching math-OCR model:  {math_ocr.MODEL_NAME}", flush=True)
        math_ocr._load()

    print(f"Cached under {os.environ.get('HF_HOME', '(default cache)')}", flush=True)


if __name__ == "__main__":
    main()
