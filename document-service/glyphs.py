"""Recover the real Unicode value of glyphs whose embedded font lies about them.

Older scholarly PDFs embed subsetted symbol fonts with no usable character map
and glyph names that describe the code position rather than the shape — the
glyph that draws a minus sign is named ``two`` and PyMuPDF decodes it as ``"2"``.
There is nothing in the file to correct from, so we correct from the *shape*:
render the glyph as it appears in the document, match it against reference
renderings of known mathematical symbols, and build a per-font
``{decoded_char: real_symbol}`` map that feeds the same repair pass as the
hand-written ``KNOWN_GLYPH_MAPS``.

Only glyphs whose decoded character is implausible for a symbol (a digit, a
stray punctuation mark, a control character) are considered, so ordinary body
text is never touched.
"""

import functools
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pymupdf

logger = logging.getLogger("ai_librarian.glyphs")

REFERENCE_FONT = Path(__file__).with_name("fonts") / "STIXTwoMath.otf"
RASTER = 40  # side of the normalised comparison bitmap

# Symbols worth recovering, each paired with the string to emit for it.
_SYMBOLS = (
    "+−±∓×÷·∗∘=≠≈≡≤≥≪≫∝∼≅"
    "()[]{}⟨⟩|‖⌊⌋⌈⌉"
    "√∛∑∏∫∮∂∇∆"
    "∞→←↔⇒⇐⇔↦↑↓"
    "∈∉∋⊂⊆⊄⊃⊇∪∩∅∀∃∄"
    "¬∧∨⊕⊗⊙⊥∠°′″†‡…⋯"
    "αβγδεζηθικλμνξοπρςστυφϕχψω"
    "ΓΔΘΛΞΠΣΦΨΩ"
)

# Characters a broken symbol font is likely to have decoded to. We refuse to
# "repair" a lowercase Latin letter, so prose can never be corrupted.
_SUSPICIOUS = re.compile(r"[0-9#$~=@_/\\<>^`|]|[\x00-\x1f\x7f]|[ðÞ¼½¾]")

# Only embedded subset fonts ("ABCDEF+Name") or fonts whose name marks them as
# symbol fonts are candidates. A standard family (Helvetica, Times, a Unicode
# text font) has a correct encoding by definition and must never be touched.
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
_SYMBOL_NAME = re.compile(
    r"Adv(?:OT|P\d|EucSymb|Els|MathPi)|Symbol|CM(?:SY|EX|MI|B)|MSA[MB]|MSBM"
    r"|MathematicalPi|MT?Extra|Fence|Marvosym|rsfs|wasy|stmary|bbold",
    re.IGNORECASE,
)


def _is_recovery_candidate(full_font_name: str) -> bool:
    return bool(_SUBSET_PREFIX.search(full_font_name) or _SYMBOL_NAME.search(full_font_name))

_ACCEPT_SCORE = 0.60
_ACCEPT_MARGIN = 0.06
_MIN_SAMPLES = 2
_PAGE_SAMPLE_CAP = 60  # scan at most this many pages to identify a font


def _winning_symbol(votes: Dict[str, int], decoded: str) -> Optional[str]:
    """The symbol a set of shape-match votes agrees on, or ``None``.

    Declines when nothing clears the minimum sample count, when the winner is
    just the character PyMuPDF already decoded, or when a runner-up symbol is
    within one vote — the last case catches glyphs drawn ambiguously and
    re-subsetted 3B2/Advent fonts where one code covers several symbols across
    the book, so no single mapping can be right.
    """
    if not votes:
        return None
    ranked = sorted(votes.values(), reverse=True)
    winner, count = max(votes.items(), key=lambda item: item[1])
    runner_up = ranked[1] if len(ranked) > 1 else 0
    if count < _MIN_SAMPLES or count - runner_up < _MIN_SAMPLES or winner == decoded:
        return None
    return winner


def _binarise(pixmap) -> np.ndarray:
    arr = np.frombuffer(pixmap.samples, dtype=np.uint8)
    arr = arr.reshape(pixmap.height, pixmap.width, pixmap.n)
    return arr[:, :, 0] < 128


def _normalise(mask: np.ndarray) -> Optional[np.ndarray]:
    """Crop to the inked pixels and scale into a centred RASTER×RASTER box."""
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if mask.sum() < 4 or not rows.any() or not cols.any():
        return None
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    crop = mask[y0 : y1 + 1, x0 : x1 + 1].astype(np.float32)
    height, width = crop.shape
    scale = (RASTER - 4) / max(height, width)
    new_h = max(1, min(RASTER, round(height * scale)))
    new_w = max(1, min(RASTER, round(width * scale)))
    row_pick = np.minimum((np.arange(new_h) / scale).astype(int), height - 1)
    col_pick = np.minimum((np.arange(new_w) / scale).astype(int), width - 1)
    resized = crop[row_pick][:, col_pick]
    out = np.zeros((RASTER, RASTER), dtype=np.float32)
    top = (RASTER - new_h) // 2
    left = (RASTER - new_w) // 2
    out[top : top + new_h, left : left + new_w] = resized
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    hot_a = a > 0.5
    hot_b = b > 0.5
    union = np.logical_or(hot_a, hot_b).sum()
    if not union:
        return 0.0
    return float(np.logical_and(hot_a, hot_b).sum() / union)


@functools.lru_cache(maxsize=1)
def _reference_bitmaps() -> Dict[str, np.ndarray]:
    if not REFERENCE_FONT.exists():
        logger.warning("Reference math font missing at %s; glyph recovery disabled", REFERENCE_FONT)
        return {}
    try:
        font = pymupdf.Font(fontfile=str(REFERENCE_FONT))
    except Exception:
        logger.exception("Could not load reference math font")
        return {}
    references: Dict[str, np.ndarray] = {}
    for symbol in dict.fromkeys(_SYMBOLS):
        if not font.has_glyph(ord(symbol)):
            continue
        document = pymupdf.open()
        page = document.new_page(width=72, height=72)
        writer = pymupdf.TextWriter(page.rect)
        try:
            writer.append((9, 52), symbol, font=font, fontsize=48)
            writer.write_text(page)
            normalised = _normalise(_binarise(page.get_pixmap(colorspace=pymupdf.csGRAY)))
        except Exception:
            normalised = None
        finally:
            document.close()
        if normalised is not None:
            references[symbol] = normalised
    return references


def _classify(mask: np.ndarray) -> Optional[str]:
    normalised = _normalise(mask)
    if normalised is None:
        return None
    ranked = sorted(
        ((_iou(normalised, reference), symbol) for symbol, reference in _reference_bitmaps().items()),
        reverse=True,
    )
    if len(ranked) < 2:
        return None
    (best_score, best_symbol), (second_score, _) = ranked[0], ranked[1]
    if best_score >= _ACCEPT_SCORE and best_score - second_score >= _ACCEPT_MARGIN:
        return best_symbol
    return None


def _render_from_page(page, bbox) -> Optional[np.ndarray]:
    rect = pymupdf.Rect(bbox)
    if rect.is_empty or rect.width < 1 or rect.height < 1:
        return None
    try:
        pixmap = page.get_pixmap(
            clip=rect, matrix=pymupdf.Matrix(4, 4), colorspace=pymupdf.csGRAY
        )
    except Exception:
        return None
    return _binarise(pixmap)


def build_glyph_repairs(document) -> Dict[str, Dict[str, str]]:
    """Return ``{font_name: {decoded_char: real_symbol}}`` for this document."""
    if not _reference_bitmaps():
        return {}

    # Collect a few sample locations for each (font, decoded char) pair whose
    # decoded character looks wrong for a symbol.
    samples: Dict[tuple, List[tuple]] = {}
    page_count = document.page_count
    step = max(1, page_count // _PAGE_SAMPLE_CAP)
    for page_index in range(0, page_count, step):
        page = document[page_index]
        raw = page.get_text("rawdict")
        for block in raw.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    full_name = span.get("font") or ""
                    if not full_name or not _is_recovery_candidate(full_name):
                        continue
                    font_name = full_name.split("+")[-1]
                    for character in span.get("chars", []):
                        value = character.get("c") or ""
                        if not value or not _SUSPICIOUS.fullmatch(value):
                            continue
                        bucket = samples.setdefault((font_name, value), [])
                        if len(bucket) < 4:
                            bucket.append((page_index, character.get("bbox")))

    repairs: Dict[str, Dict[str, str]] = {}
    for (font_name, decoded), locations in samples.items():
        votes: Dict[str, int] = {}
        for page_index, bbox in locations:
            symbol = _classify_safe(document[page_index], bbox)
            if symbol:
                votes[symbol] = votes.get(symbol, 0) + 1
        winner = _winning_symbol(votes, decoded)
        if winner is not None:
            repairs.setdefault(font_name, {})[decoded] = winner

    if repairs:
        logger.info("Recovered glyph maps by shape: %s", repairs)
    return repairs


def _classify_safe(page, bbox) -> Optional[str]:
    mask = _render_from_page(page, bbox)
    if mask is None:
        return None
    try:
        return _classify(mask)
    except Exception:
        logger.exception("Glyph classification failed")
        return None
