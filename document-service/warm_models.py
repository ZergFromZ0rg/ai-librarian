"""Download the embedding, reranker and OCR models into the Hugging Face cache.

Run this to make the first search fast and deterministic instead of waiting on
downloads at request time:

    docker compose run --rm document-service python warm_models.py

It writes to ``HF_HOME`` (``/models/huggingface`` in the container, persisted as
``data/models/``). For a fully self-contained image, build with
``PREBAKE_MODELS=1`` instead — the Dockerfile runs this script at build time.

The equation-aware OCR model (~1 GB) is included unless ``OCR_ENGINE`` is
``tesseract`` or ``OCR_MODE`` is ``off``; without it cached, an offline
install still OCRs scans with Tesseract, just without equations.
"""

import os

from embeddings import DEFAULT_MODEL as EMBEDDING_MODEL, get_model as get_embedding_model
from reranker import DEFAULT_MODEL as RERANK_MODEL, get_model as get_rerank_model


def main() -> None:
    print(f"Fetching embedding model: {EMBEDDING_MODEL}", flush=True)
    get_embedding_model()
    print(f"Fetching reranker model:  {RERANK_MODEL}", flush=True)
    get_rerank_model()
    if os.environ.get("OCR_MODE", "auto") != "off" and os.environ.get("OCR_ENGINE", "auto") != "tesseract":
        from transformers import NougatProcessor, VisionEncoderDecoderModel

        from ocr import MATH_MODEL

        print(f"Fetching OCR model:       {MATH_MODEL}", flush=True)
        NougatProcessor.from_pretrained(MATH_MODEL)
        VisionEncoderDecoderModel.from_pretrained(MATH_MODEL)
    print(f"Cached under {os.environ.get('HF_HOME', '(default cache)')}", flush=True)


if __name__ == "__main__":
    main()
