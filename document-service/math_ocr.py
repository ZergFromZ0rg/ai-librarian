"""Optional fallback: read math from a page image when the PDF has no text
layer for it. Off unless ``MATH_OCR`` is set. Same goal as the glyph-shape
recovery in ``glyphs.py`` — recover math the standard tools miss — for the
harder case where there is nothing to extract at all.

The model (Nougat by default) is slow on CPU and can hallucinate or fall into
repetition loops, so its output is only *appended* to a page (never replaces the
text layer) and is discarded when it looks degenerate.
"""

import logging
import os
import re
import threading
from typing import Optional

logger = logging.getLogger("ai_librarian.math_ocr")

ENABLED = os.environ.get("MATH_OCR", "").strip().lower() in {"1", "true", "yes", "on"}
MODEL_NAME = os.environ.get("MATH_OCR_MODEL", "facebook/nougat-small")
RENDER_DPI = int(os.environ.get("MATH_OCR_DPI", "160"))
MAX_NEW_TOKENS = int(os.environ.get("MATH_OCR_MAX_TOKENS", "896"))

_model = None
_processor = None
_lock = threading.Lock()
_unavailable = False


def _load():
    global _model, _processor, _unavailable
    if _model is not None or _unavailable:
        return _model
    with _lock:
        if _model is None and not _unavailable:
            try:
                import torch
                from transformers import AutoModelForVision2Seq, AutoProcessor

                _processor = AutoProcessor.from_pretrained(MODEL_NAME)
                _model = AutoModelForVision2Seq.from_pretrained(MODEL_NAME)
                _model.eval()
                torch.set_grad_enabled(False)
                logger.info("math OCR model loaded: %s", MODEL_NAME)
            except Exception:
                _unavailable = True
                logger.warning(
                    "math OCR model %s could not be loaded; pages with image-only "
                    "math will stay absent from the index.", MODEL_NAME, exc_info=True
                )
    return _model


# A chunk of >=8 chars repeated 4+ times back to back — the classic Nougat loop.
_REPEATED_RUN = re.compile(r"(.{8,}?)\1{3,}", re.DOTALL)


def _is_degenerate(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 4:
        return True
    if _REPEATED_RUN.search(stripped):
        return True
    if len(stripped) > 40 and len(set(stripped)) < 8:
        return True
    return False


def transcribe_page(source_page) -> Optional[str]:
    """LaTeX/Markdown transcription of a rendered page, or None when OCR is off,
    the model is unavailable, generation fails, or the output fails the checks."""
    if not ENABLED or _load() is None:
        return None
    try:
        import numpy as np

        pixmap = source_page.get_pixmap(dpi=RENDER_DPI)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )[:, :, :3]
        inputs = _processor(images=image, return_tensors="pt")
        output = _model.generate(
            inputs["pixel_values"],
            max_new_tokens=MAX_NEW_TOKENS,
            no_repeat_ngram_size=3,
        )
        text = _processor.batch_decode(output, skip_special_tokens=True)[0]
        post_process = getattr(_processor, "post_process_generation", None)
        if callable(post_process):
            text = post_process(text, fix_markdown=False)
        text = text.strip()
    except Exception:
        logger.exception("math OCR failed for a page")
        return None
    if _is_degenerate(text):
        logger.warning("math OCR output looked degenerate; discarding it for this page")
        return None
    return text
