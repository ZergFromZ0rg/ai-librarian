"""OCR for PDF pages that have no usable text layer (scans).

Two engines, combined per page:

* **Tesseract** — fast (about a second a page) and dependable on ordinary
  printed text, but it reads equations as symbol soup.
* **Nougat** (``facebook/nougat-small``) — a vision transformer trained on
  scientific papers that transcribes a whole page to Markdown with the
  equations as LaTeX. Much slower (seconds to tens of seconds a page, far
  more on CPU), and it occasionally skips or loops on unusual layouts.

With ``engine="auto"`` both run: Nougat's transcription is kept unless it
recovered clearly fewer words than Tesseract (a sign it skipped content), in
which case Tesseract's is used for that page.

Nougat runs in a **fresh interpreter** (``python -m ocr``), never in the
caller. The caller is a forked extraction worker of a process that has already
used PyTorch for embeddings, and a forked PyTorch/OpenMP runtime can deadlock;
a separate process also means an out-of-memory in the model cannot take the
extraction pool down with it.

Nothing here raises for an engine problem. A missing binary, a failed model
download or a crash is logged and the page falls back to the other engine, or
is left as it was.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("ai_librarian.ocr")

MATH_MODEL = os.environ.get("OCR_MATH_MODEL", "facebook/nougat-small")
# Rendering resolution. Tesseract wants ~200-300 DPI; Nougat resizes to its own
# input size, so the same render serves both.
RENDER_DPI = 200
# Nougat generation budget per page. A dense page is ~1,000-2,500 tokens;
# hitting the cap almost always means the model is looping.
MATH_MAX_TOKENS = int(os.environ.get("OCR_MATH_MAX_TOKENS", "3000"))
# Keep Nougat's page unless it found fewer than this share of Tesseract's words.
MIN_WORD_RATIO = 0.6
# Wall-clock ceiling for the Nougat subprocess: model load plus per page.
_LOAD_TIMEOUT = 600
_PAGE_TIMEOUT = 600

_WORD = re.compile(r"[^\W\d_]{2,}")
_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")


def _render(document, page_number: int):
    import pymupdf
    from PIL import Image

    page = document[page_number - 1]
    scale = RENDER_DPI / 72
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


def word_count(text: str) -> int:
    """Real words, ignoring LaTeX command names like \\frac or \\alpha."""
    return len(_WORD.findall(_LATEX_COMMAND.sub(" ", text or "")))


# ---------------------------------------------------------------- tesseract
def tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _tesseract_pages(pdf_path, page_numbers: List[int], languages: str) -> Dict[int, str]:
    if not tesseract_available():
        logger.warning("Tesseract is unavailable; skipping text OCR")
        return {}
    import pymupdf
    import pytesseract

    results: Dict[int, str] = {}
    with pymupdf.open(str(pdf_path)) as document:
        for number in page_numbers:
            try:
                with _render(document, number) as image:
                    results[number] = pytesseract.image_to_string(
                        image, lang=languages, config="--psm 3"
                    ).strip()
            except Exception as exc:
                logger.warning("Tesseract failed on page %d of %s: %s", number, pdf_path, exc)
    return results


# ------------------------------------------------------------------ nougat
def math_engine_available() -> bool:
    """Whether Nougat can run here at all (torch + transformers importable).

    Checked without importing either, which would cost seconds and memory in
    the caller for nothing.
    """
    import importlib.util

    return all(importlib.util.find_spec(name) for name in ("torch", "transformers"))


def _nougat_pages(pdf_path, page_numbers: List[int]) -> Dict[int, str]:
    """Run Nougat on the pages in a separate interpreter; {} on any failure."""
    if not page_numbers or not math_engine_available():
        return {}
    command = [
        sys.executable,
        "-m",
        "ocr",
        str(pdf_path),
        ",".join(str(number) for number in page_numbers),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=_LOAD_TIMEOUT + _PAGE_TIMEOUT * len(page_numbers),
        )
    except subprocess.TimeoutExpired:
        logger.warning("Equation OCR timed out on %s; using text OCR only", pdf_path)
        return {}
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-3:]
        logger.warning("Equation OCR failed on %s: %s", pdf_path, " | ".join(tail))
        return {}
    try:
        raw = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        logger.warning("Equation OCR returned unreadable output for %s", pdf_path)
        return {}
    return {int(number): text for number, text in raw.items() if text}


def _nougat_main(pdf_path: str, page_numbers: List[int]) -> Dict[int, str]:
    """The subprocess side: load the model once and transcribe each page."""
    import pymupdf
    import torch
    from transformers import NougatProcessor, VisionEncoderDecoderModel

    processor = NougatProcessor.from_pretrained(MATH_MODEL)
    model = VisionEncoderDecoderModel.from_pretrained(MATH_MODEL)
    if torch.cuda.is_available():
        device = "cuda"
    elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    model.to(device).eval()

    results: Dict[int, str] = {}
    with pymupdf.open(pdf_path) as document:
        for number in page_numbers:
            try:
                with _render(document, number) as image:
                    pixel_values = processor(image, return_tensors="pt").pixel_values.to(device)
                with torch.no_grad():
                    output = model.generate(
                        pixel_values,
                        min_length=1,
                        max_new_tokens=MATH_MAX_TOKENS,
                        bad_words_ids=[[processor.tokenizer.unk_token_id]],
                    )
                text = processor.batch_decode(output, skip_special_tokens=True)[0]
                results[number] = clean_nougat_markdown(text)
            except Exception as exc:  # one bad page must not lose the rest
                print(f"page {number}: {exc}", file=sys.stderr, flush=True)
    return results


def _tabular_to_markdown(match: "re.Match") -> str:
    """Nougat writes tables as LaTeX tabular; turn simple ones into Markdown."""
    body = re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\})", "", match.group(1))
    rows = []
    for row in re.split(r"\\\\", body):
        cells = [re.sub(r"\\multicolumn\{\d+\}\{[^}]*\}\{([^}]*)\}", r"\1", cell).strip() for cell in row.split("&")]
        if any(cells):
            rows.append([cell.replace("|", "\\|") for cell in cells])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n\n" + "\n".join(lines) + "\n\n"


def _drop_repetition(text: str) -> str:
    """Cut the degenerate loops Nougat sometimes falls into.

    A line repeated three or more times in a row is kept once and everything
    after the loop is discarded — output past a loop is not trustworthy.
    """
    lines = text.split("\n")
    kept: List[str] = []
    run = 0
    for line in lines:
        if kept and line.strip() and line.strip() == kept[-1].strip() and len(line.strip()) > 3:
            run += 1
            if run >= 2:
                break
            continue
        run = 0
        kept.append(line)
    text = "\n".join(kept)
    # Within one line: the same 20+ character chunk repeated 4+ times.
    return re.sub(r"(.{20,}?)\1{3,}.*", r"\1", text, flags=re.DOTALL)


def clean_nougat_markdown(text: str) -> str:
    """Normalise Nougat's output to the Markdown the chunker understands."""
    text = _drop_repetition(text or "")
    text = re.sub(r"\\begin\{table\}(?:\[[^\]]*\])?|\\end\{table\}", "", text)
    text = re.sub(r"\\begin\{tabular\}\{[^}]*\}(.*?)\\end\{tabular\}", _tabular_to_markdown, text, flags=re.DOTALL)
    # "[MISSING_PAGE_...]" / "[MISSING_PAGE_EMPTY:n]" markers mean "could not read".
    text = re.sub(r"\[MISSING_PAGE_[^\]]*\]", "", text)
    # Display math on its own lines so the chunker keeps each equation whole.
    text = re.sub(r"\s*\\\[(.*?)\\\]\s*", lambda m: f"\n\n\\[{m.group(1).strip()}\\]\n\n", text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -------------------------------------------------------------- the entry
def ocr_pages(
    pdf_path,
    page_numbers: List[int],
    *,
    languages: str = "eng",
    engine: str = "auto",
) -> Dict[int, Dict[str, str]]:
    """OCR the given 1-based pages. Returns ``{page: {"text", "engine"}}``.

    Pages no engine could read are simply absent from the result.
    """
    if not page_numbers:
        return {}
    text_pages: Dict[int, str] = {}
    if engine in {"auto", "tesseract"}:
        text_pages = _tesseract_pages(pdf_path, page_numbers, languages)
    math_pages: Dict[int, str] = {}
    if engine in {"auto", "math"}:
        math_pages = _nougat_pages(pdf_path, page_numbers)
        if engine == "math" and not math_pages:
            # Asked for equations only, but the model isn't usable: still read the words.
            text_pages = _tesseract_pages(pdf_path, page_numbers, languages)

    results: Dict[int, Dict[str, str]] = {}
    for number in page_numbers:
        math_text = math_pages.get(number, "")
        plain_text = text_pages.get(number, "")
        if math_text and word_count(math_text) >= MIN_WORD_RATIO * word_count(plain_text):
            results[number] = {"text": math_text, "engine": "nougat"}
        elif plain_text:
            results[number] = {"text": plain_text, "engine": "tesseract"}
    return results


def estimate_seconds(page_count: int, engine: str = "auto") -> int:
    """Rough CPU cost, for the approval prompt. Nougat dominates."""
    per_page = 45 if engine in {"auto", "math"} and math_engine_available() else 2
    return page_count * per_page


if __name__ == "__main__":
    # python -m ocr <pdf> <page,page,...>  ->  one JSON line {page: markdown}
    pdf, pages = sys.argv[1], [int(value) for value in sys.argv[2].split(",") if value]
    print(json.dumps(_nougat_main(pdf, pages), ensure_ascii=False), flush=True)
