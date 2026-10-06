"""Reader annotations on PDF pages: word boxes, underlined-word detection, the
searchable digest, and baking marks into a downloadable copy.

Geometry is stored *normalised to the page* (0..1 of its width and height, in
the page as displayed, i.e. after rotation), so a mark survives any zoom and
the page image the reader sees is the only coordinate system the UI needs.
The original PDF is never modified: its content hash is the document's
identity, and a referenced library file is read-only.
"""

import re
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Sequence

import pymupdf

KINDS = ("highlight", "underline", "text", "ink")
KIND_LABELS = {"highlight": "Highlight", "underline": "Underline", "text": "Text", "ink": "Drawing"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_COLORS = {"highlight": "#ffd84d", "underline": "#e5484d", "text": "#1f2328", "ink": "#2f6fed"}

# How far a ruled line may sit below a word's baseline, and how much wider
# than the word it may be, to count as that word's underline rather than a
# table rule or a heading divider.
_LINE_BELOW = 0.55
_LINE_ABOVE = 0.15
_LINE_MAX_WIDTH_RATIO = 1.8
_MAX_RULE_THICKNESS = 2.0
_MAX_WORD_LENGTH = 40
_MAX_CANDIDATES = 3_000


def clean_word(raw: str) -> Optional[str]:
    """A word stripped of surrounding punctuation, or None if nothing wordlike
    remains (digits only, a lone symbol, an absurdly long token)."""
    word = re.sub(r"^[^\w]+|[^\w]+$", "", (raw or "").strip()).replace("’", "'")
    if not word or len(word) > _MAX_WORD_LENGTH or not re.search(r"[^\W\d_]", word):
        return None
    return word


def is_single_word(text: str) -> bool:
    return len((text or "").split()) == 1 and clean_word(text) is not None


# ----------------------------------------------------------------- geometry
def _display_rect(rect: "pymupdf.Rect", page: "pymupdf.Page") -> "pymupdf.Rect":
    return pymupdf.Rect(rect) * page.rotation_matrix if page.rotation else pymupdf.Rect(rect)


def _norm(rect: "pymupdf.Rect", width: float, height: float) -> List[float]:
    return [
        round(min(max(rect.x0 / width, 0.0), 1.0), 5),
        round(min(max(rect.y0 / height, 0.0), 1.0), 5),
        round(min(max(rect.x1 / width, 0.0), 1.0), 5),
        round(min(max(rect.y1 / height, 0.0), 1.0), 5),
    ]


def page_words(page: "pymupdf.Page") -> dict:
    """The page's words as normalised boxes, in reading (content) order."""
    width, height = page.rect.width, page.rect.height
    words = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        if not text.strip():
            continue
        box = _norm(_display_rect(pymupdf.Rect(x0, y0, x1, y1), page), width, height)
        words.append([*box, text])
    return {"width": width, "height": height, "words": words}


def _from_norm(rect: Sequence[float], page: "pymupdf.Page") -> "pymupdf.Rect":
    """A normalised display-space box as a rect in the page's own (unrotated) space."""
    width, height = page.rect.width, page.rect.height
    box = pymupdf.Rect(rect[0] * width, rect[1] * height, rect[2] * width, rect[3] * height)
    return box * page.derotation_matrix if page.rotation else box


# --------------------------------------------------- underlined-word finder
def _horizontal_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _words_under_rects(words: list, rects: Iterable["pymupdf.Rect"]) -> List[int]:
    """Indexes of the words an underline mark's quads sit on: a word counts when
    most of its width lies inside a quad and the quad reaches its height band
    (underline quads are sometimes only a thin strip under the text)."""
    hit = []
    for index, word in enumerate(words):
        x0, y0, x1, y1 = word[:4]
        width = x1 - x0
        if width <= 0:
            continue
        for rect in rects:
            shared = _horizontal_overlap(x0, x1, rect.x0, rect.x1)
            if shared / width >= 0.6 and rect.y0 <= y1 + 2 and rect.y1 >= y0 - 2:
                hit.append(index)
                break
    return hit


def _annotation_quads(annot: "pymupdf.Annot") -> List["pymupdf.Rect"]:
    vertices = annot.vertices or []
    if len(vertices) >= 4 and len(vertices) % 4 == 0:
        return [pymupdf.Quad(vertices[i : i + 4]).rect for i in range(0, len(vertices), 4)]
    return [annot.rect]


def _ruled_lines(page: "pymupdf.Page") -> List["pymupdf.Rect"]:
    """Thin horizontal strokes and slivers drawn on the page, which is how
    underlines made by a scanner app, a print-to-PDF, or a PDF editor's pen
    tool are stored when they are not real Underline annotations."""
    lines = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.height <= _MAX_RULE_THICKNESS and rect.width >= 3:
            lines.append(rect)
    return lines


def _word_for_rule(words: list, line: "pymupdf.Rect", links: List["pymupdf.Rect"]) -> Optional[int]:
    """The single word a ruled line underlines, or None when it underlines
    nothing, several words (a table rule, a divider), or a hyperlink."""
    under = []
    neighbours = 0
    for index, word in enumerate(words):
        x0, y0, x1, y1 = word[:4]
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0:
            continue
        shared = _horizontal_overlap(x0, x1, line.x0, line.x1)
        if not shared:
            continue
        on_band = y1 - _LINE_ABOVE * height <= line.y1 and line.y0 <= y1 + _LINE_BELOW * height
        if on_band and shared / width >= 0.7:
            under.append((index, width))
        elif shared / width > 0.25 and abs(y1 - line.y0) <= height:
            neighbours += 1
    if len(under) != 1 or neighbours:
        return None
    index, width = under[0]
    if line.width > width * _LINE_MAX_WIDTH_RATIO:
        return None
    word_rect = pymupdf.Rect(words[index][:4])
    if any(word_rect.intersects(link) for link in links):
        return None
    return index


def _sentence_around(words: list, index: int) -> str:
    """The sentence containing words[index], taken from the page's own text."""
    parts, start = [], 0
    for position, word in enumerate(words):
        if position == index:
            start = sum(len(part) + 1 for part in parts)
        parts.append(word[4])
    text = " ".join(parts)
    end = start + len(words[index][4])
    left = start
    while left > 0 and start - left < 220 and not (text[left - 1] in ".!?" and text[left : left + 1] == " "):
        left -= 1
    right = end
    while right < len(text) and right - end < 220 and text[right - 1] not in ".!?":
        right += 1
    return " ".join(text[left:right].split())


def find_underlined_words(
    path,
    reader_marks: Optional[Dict[int, List[List[List[float]]]]] = None,
) -> List[dict]:
    """Every single word that is underlined in the PDF at ``path``.

    Three sources, so it works however the underlining was made: real PDF
    Underline/Squiggly annotations (made in Preview, Acrobat, Apple Books
    exports...), thin ruled lines drawn under one word, and underlines made in
    this app's own reader (``reader_marks``: page -> a list of marks, each a
    list of normalised rects). Marks over several words are ignored: the
    notebook collects *words*, and a phrase is a highlight. Words with no text
    layer (a scan that has not been OCR'd) cannot be matched.

    Returns one entry per distinct word (case-insensitive), in book order.
    """
    found: "OrderedDict[str, dict]" = OrderedDict()
    reader_marks = reader_marks or {}

    def add(page_number: int, words: list, index: int, source: str) -> None:
        word = clean_word(words[index][4])
        if not word:
            return
        key = word.casefold()
        entry = found.get(key)
        if entry is None:
            if len(found) >= _MAX_CANDIDATES:
                return
            found[key] = {
                "word": word,
                "page": page_number,
                "pages": [page_number],
                "source": source,
                "quote": _sentence_around(words, index),
            }
        elif page_number not in entry["pages"]:
            entry["pages"].append(page_number)

    with pymupdf.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            marks = reader_marks.get(page_number, [])
            native = list(page.annots(types=[pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_SQUIGGLY]) or [])
            rules = _ruled_lines(page)
            if not (marks or native or rules):
                continue
            words = page.get_text("words")
            if not words:
                continue
            for annot in native:
                hit = _words_under_rects(words, _annotation_quads(annot))
                if len(hit) == 1:
                    add(page_number, words, hit[0], "pdf-underline")
            links = [pymupdf.Rect(link["from"]) for link in page.get_links() if link.get("from")]
            for line in rules:
                index = _word_for_rule(words, line, links)
                if index is not None:
                    add(page_number, words, index, "drawn-line")
            for mark in marks:
                hit = _words_under_rects(words, [_from_norm(rect, page) for rect in mark])
                if len(hit) == 1:
                    add(page_number, words, hit[0], "reader")
    return list(found.values())


# ----------------------------------------------------------- search digest
def digest_text(title: str, annotations: List[dict]) -> str:
    """One Markdown document of everything the reader marked in a book. It is
    indexed like a note, so a question or search can find a highlighted
    passage or a margin comment and cite the page it came from."""
    lines = [f"# Annotations: {title}", ""]
    by_page: "OrderedDict[int, List[dict]]" = OrderedDict()
    for item in sorted(annotations, key=lambda a: (a["page"], a.get("created_at") or "")):
        if (item.get("text") or "").strip() or (item.get("comment") or "").strip():
            by_page.setdefault(item["page"], []).append(item)
    for page, items in by_page.items():
        lines += [f"## Page {page}", ""]
        for item in items:
            text = " ".join((item.get("text") or "").split())
            comment = (item.get("comment") or "").strip()
            if item["kind"] == "text":
                lines.append(f"Margin note on page {page}: {text}")
            else:
                label = "Highlighted" if item["kind"] == "highlight" else "Underlined"
                lines.append(f"{label} on page {page}:" if text else f"{label} on page {page}.")
                if text:
                    lines.append(f"> {text}")
                if comment:
                    lines.append(f"Comment on page {page}: {comment}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------- baking
def _rgb(color: Optional[str], fallback: str) -> tuple:
    value = color if color and HEX_COLOR.match(color) else fallback
    return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))


PDF_NAME_PREFIX = "ailib:"


def _add_mark(page: "pymupdf.Page", item: dict) -> None:
    kind = item["kind"]
    color = _rgb(item.get("color"), DEFAULT_COLORS.get(kind, "#000000"))
    comment = (item.get("comment") or "").strip()
    if kind in ("highlight", "underline"):
        quads = [_from_norm(rect, page).quad for rect in item.get("rects", [])]
        if not quads:
            return
        annot = page.add_highlight_annot(quads) if kind == "highlight" else page.add_underline_annot(quads)
        annot.set_colors(stroke=color)
    elif kind == "text":
        rects = item.get("rects") or []
        if not rects or not (item.get("text") or "").strip():
            return
        size = max(6.0, float(item.get("size") or 0.02) * page.rect.width)
        annot = page.add_freetext_annot(
            _from_norm(rects[0], page), item["text"], fontsize=size, text_color=color, fill_color=None
        )
    elif kind == "ink":
        matrix = page.derotation_matrix if page.rotation else pymupdf.Identity
        strokes = [
            [tuple(pymupdf.Point(x * page.rect.width, y * page.rect.height) * matrix) for x, y in stroke]
            for stroke in item.get("paths", [])
            if len(stroke) > 1
        ]
        if not strokes:
            return
        annot = page.add_ink_annot(strokes)
        annot.set_colors(stroke=color)
        annot.set_border(width=max(0.5, float(item.get("width") or 0.004) * page.rect.width))
    else:
        return
    if comment:
        annot.set_info(content=comment)
    annot.set_name(f"{PDF_NAME_PREFIX}{item['annotation_id']}")
    annot.update()


def bake(path, annotations: List[dict]) -> bytes:
    """The PDF at ``path`` with the reader's marks written in as real PDF
    annotations (Preview, Acrobat and Apple Books show them). Marks made by the
    reader on an earlier pass are replaced, so edits and deletions carry over;
    annotations made in other apps are left alone."""
    with pymupdf.open(path) as document:
        for page in document:
            for annot in list(page.annots() or []):
                if (annot.info.get("name") or "").startswith(PDF_NAME_PREFIX):
                    page.delete_annot(annot)
        for item in annotations:
            if 1 <= item["page"] <= document.page_count:
                _add_mark(document[item["page"] - 1], item)
        return document.tobytes(garbage=3, deflate=True)
