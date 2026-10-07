"""Reading the marks a reader made in a PDF with any native tool.

The app has no annotation editor of its own: a PDF opens in the browser's or
the operating system's viewer, where highlights, underlines, strike-outs,
typed text boxes, sticky notes and pen strokes are written into the file as
standard PDF annotations. This module reads them back so they can be searched
and cited, and so single underlined words can be collected as vocabulary.

Nothing here changes a PDF. Highlighted and underlined passages are recovered
from the page's own text layer by geometry, so the quoted text is what the
page actually says under the mark, not what an app guessed.
"""

import hashlib
import re
from collections import OrderedDict
from typing import Iterable, List, Optional, Sequence, Tuple

import pymupdf

# Marks that sit on text and quote it.
_TEXT_MARKUP = {
    pymupdf.PDF_ANNOT_HIGHLIGHT: "highlight",
    pymupdf.PDF_ANNOT_UNDERLINE: "underline",
    pymupdf.PDF_ANNOT_SQUIGGLY: "underline",
    pymupdf.PDF_ANNOT_STRIKE_OUT: "strikeout",
}
# Kinds that are never reader content: links, form fields, the pop-up window
# that belongs to a parent annotation.
_IGNORED = {pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_POPUP}

# A word counts as marked when this much of its box lies inside the mark.
_WORD_COVERAGE = 0.4
# A pen stroke at least this thick (points), or semi-transparent and fairly
# thick, is a highlighter, not a pen.
_HIGHLIGHTER_WIDTH = 8.0
_HIGHLIGHTER_SOFT_WIDTH = 4.0
# The tallest a hand-drawn line can be and still count as an underline.
_UNDERLINE_MAX_HEIGHT = 5.0
_MAX_PEN_POINTS = 4_000

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
def _coverage(word: Sequence[float], rect: "pymupdf.Rect") -> float:
    """The fraction of a word's box that lies inside ``rect``."""
    x0, y0, x1, y1 = word[:4]
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return 0.0
    width = min(x1, rect.x1) - max(x0, rect.x0)
    height = min(y1, rect.y1) - max(y0, rect.y0)
    if width <= 0 or height <= 0:
        return 0.0
    return (width * height) / area


def _words_in_rects(words: list, rects: Iterable["pymupdf.Rect"]) -> list:
    rects = list(rects)
    return [word for word in words if any(_coverage(word, rect) >= _WORD_COVERAGE for rect in rects)]


def _horizontal_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _words_under_rects(words: list, rects: Iterable["pymupdf.Rect"]) -> List[int]:
    """Indexes of the words an underline mark's quads sit on. A word counts when
    most of its width lies inside a quad and either the quad covers half the
    word's height (a full-line quad) or it is a thin strip hugging the baseline.
    A loose vertical test would also catch the lines above and below."""
    hit = []
    for index, word in enumerate(words):
        x0, y0, x1, y1 = word[:4]
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0:
            continue
        for rect in rects:
            if _horizontal_overlap(x0, x1, rect.x0, rect.x1) / width < 0.6:
                continue
            overlap = min(y1, rect.y1) - max(y0, rect.y0)
            full = overlap >= 0.5 * height
            strip = rect.height <= 0.4 * height and y1 - 0.5 * height <= rect.y0 <= y1 + 0.6 * height
            if full or strip:
                hit.append(index)
                break
    return hit


def _annotation_quads(annot: "pymupdf.Annot") -> List["pymupdf.Rect"]:
    vertices = annot.vertices or []
    if len(vertices) >= 4 and len(vertices) % 4 == 0 and not isinstance(vertices[0], list):
        return [pymupdf.Quad(vertices[i : i + 4]).rect for i in range(0, len(vertices), 4)]
    return [annot.rect]


def _strokes(annot: "pymupdf.Annot") -> List[List[Tuple[float, float]]]:
    """An Ink annotation's pen strokes as lists of (x, y) points."""
    strokes = []
    for stroke in annot.vertices or []:
        if isinstance(stroke, (list, tuple)) and stroke and isinstance(stroke[0], (list, tuple)):
            points = [(float(x), float(y)) for x, y in stroke]
            if len(points) > _MAX_PEN_POINTS:
                step = len(points) // _MAX_PEN_POINTS + 1
                points = points[::step] + [points[-1]]
            strokes.append(points)
    return strokes


def _stroke_bbox(stroke: Sequence[Tuple[float, float]]) -> "pymupdf.Rect":
    xs, ys = [p[0] for p in stroke], [p[1] for p in stroke]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def _pen_width(annot: "pymupdf.Annot") -> float:
    border = annot.border or {}
    return float(border.get("width") or 1.0)


def _is_highlighter(annot: "pymupdf.Annot") -> bool:
    width, opacity = _pen_width(annot), annot.opacity
    soft = opacity is not None and 0 <= opacity < 0.95
    return width >= _HIGHLIGHTER_WIDTH or (soft and width >= _HIGHLIGHTER_SOFT_WIDTH)


def _words_under_stroke(words: list, stroke: Sequence[Tuple[float, float]], width: float) -> list:
    """Words a thick pen stroke passes over. The stroke is a swept band, so each
    word is sampled on a small grid and counts when enough of it is covered."""
    half = max(width, 1.0) / 2
    segments = [
        pymupdf.Rect(min(a[0], b[0]) - half, min(a[1], b[1]) - half, max(a[0], b[0]) + half, max(a[1], b[1]) + half)
        for a, b in zip(stroke, stroke[1:])
    ] or [pymupdf.Rect(stroke[0][0] - half, stroke[0][1] - half, stroke[0][0] + half, stroke[0][1] + half)]
    band = pymupdf.Rect(segments[0])
    for segment in segments[1:]:
        band |= segment
    chosen = []
    for word in words:
        if _coverage(word, band) == 0:
            continue
        x0, y0, x1, y1 = word[:4]
        inside = total = 0
        for i in range(6):
            for j in range(3):
                x = x0 + (x1 - x0) * (i + 0.5) / 6
                y = y0 + (y1 - y0) * (j + 0.5) / 3
                total += 1
                if any(s.x0 <= x <= s.x1 and s.y0 <= y <= s.y1 for s in segments):
                    inside += 1
        if inside / total >= 0.5:
            chosen.append(word)
    return chosen


def _words_on_line(words: list, rect: "pymupdf.Rect") -> list:
    """Words whose baseline a thin horizontal stroke runs along."""
    out = []
    for word in words:
        x0, y0, x1, y1 = word[:4]
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0:
            continue
        if _horizontal_overlap(x0, x1, rect.x0, rect.x1) / width < 0.6:
            continue
        if y1 - _LINE_ABOVE * height <= rect.y1 and rect.y0 <= y1 + _LINE_BELOW * height:
            out.append(word)
    return out


def _is_thin_horizontal(rect: "pymupdf.Rect") -> bool:
    return rect.height <= _UNDERLINE_MAX_HEIGHT and rect.width >= 3 and rect.width >= rect.height * 2


# ------------------------------------------------------------ reading marks
def _hex(color) -> Optional[str]:
    if not color or len(color) != 3:
        return None
    return "#" + "".join(f"{round(min(max(float(c), 0.0), 1.0) * 255):02x}" for c in color)


def _date(raw: Optional[str]) -> Optional[str]:
    match = re.match(r"D?:?(\d{4})(\d{2})(\d{2})", raw or "")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else None


def _quote(words: list) -> str:
    return " ".join(" ".join(word[4] for word in words).split())


def _snapshot(page: "pymupdf.Page") -> List[dict]:
    """Plain copies of a page's annotations, after which they are removed from
    the in-memory page. That leaves the page with only the book's own text and
    drawings, so a typed text box or a pen stroke's appearance can never be
    mistaken for book content. The file on disk is never touched."""
    snaps = []
    for annot in page.annots() or []:
        code = annot.type[0]
        if code in _IGNORED or annot.flags & pymupdf.PDF_ANNOT_IS_HIDDEN:
            continue
        colors = annot.colors or {}
        info = annot.info
        snap = {
            "code": code,
            "rect": pymupdf.Rect(annot.rect),
            "comment": (info.get("content") or "").strip(),
            "author": (info.get("title") or "").strip() or None,
            "modified": _date(info.get("modDate") or info.get("creationDate")),
            "color": _hex(colors.get("stroke") or colors.get("fill")),
            "xref": annot.xref,
            "reply_to": getattr(annot, "irt_xref", 0) or 0,
            "quads": _annotation_quads(annot) if code in _TEXT_MARKUP else None,
            "strokes": _strokes(annot) if code == pymupdf.PDF_ANNOT_INK else None,
            "width": _pen_width(annot) if code == pymupdf.PDF_ANNOT_INK else 0.0,
            "highlighter": _is_highlighter(annot) if code == pymupdf.PDF_ANNOT_INK else False,
            "typed": "",
        }
        if code == pymupdf.PDF_ANNOT_FREE_TEXT and not snap["comment"]:
            try:
                snap["typed"] = " ".join(annot.get_textpage().extractText().split())
            except Exception:
                pass
        snaps.append(snap)
    while page.first_annot:
        page.delete_annot(page.first_annot)
    return snaps


def _item(page_number: int, kind: str, snap: dict, text: str = "", comment: str = "") -> dict:
    return {
        "page": page_number,
        "kind": kind,
        "text": text,
        "comment": comment,
        "color": snap["color"],
        "author": snap["author"],
        "modified": snap["modified"],
        "_xref": snap["xref"],
        "_reply_to": snap["reply_to"],
        "_y": snap["rect"].y0,
        "_x": snap["rect"].x0,
    }


def _scan_page(page: "pymupdf.Page", page_number: int) -> List[dict]:
    snaps = _snapshot(page)
    if not snaps:
        return []
    words = page.get_text("words")
    items: List[dict] = []
    for snap in snaps:
        code, comment = snap["code"], snap["comment"]
        if code in _TEXT_MARKUP:
            quoted = _quote(_words_in_rects(words, snap["quads"]))
            items.append(_item(page_number, _TEXT_MARKUP[code], snap, quoted, comment))
        elif code == pymupdf.PDF_ANNOT_FREE_TEXT:
            typed = comment or snap["typed"]
            if typed:
                items.append(_item(page_number, "text", snap, typed, ""))
        elif code == pymupdf.PDF_ANNOT_TEXT:
            if comment:
                items.append(_item(page_number, "note", snap, "", comment))
        elif code == pymupdf.PDF_ANNOT_INK:
            items.append(_scan_ink(page_number, snap, words, comment))
        elif comment:
            items.append(_item(page_number, "shape", snap, "", comment))
    return items


def _scan_ink(page_number: int, snap: dict, words: list, comment: str) -> dict:
    """A pen stroke is a highlight if it is a highlighter, an underline if it is
    a thin line along text, and otherwise a drawing (kept, but with no text)."""
    strokes = snap["strokes"] or []
    quoted: List[list] = []
    kind = "drawing"
    if strokes and snap["highlighter"]:
        for stroke in strokes:
            quoted.extend(_words_under_stroke(words, stroke, snap["width"]))
        if quoted:
            kind = "highlight"
    elif strokes:
        for stroke in strokes:
            rect = _stroke_bbox(stroke)
            if _is_thin_horizontal(rect):
                quoted.extend(_words_on_line(words, rect))
        if quoted:
            kind = "underline"
    seen, ordered = set(), []
    for word in quoted:
        key = tuple(round(v, 1) for v in word[:4])
        if key not in seen:
            seen.add(key)
            ordered.append(word)
    ordered.sort(key=lambda w: (w[5], w[6], w[7]))
    return _item(page_number, kind, snap, _quote(ordered), comment)


def scan_pdf_annotations(path) -> List[dict]:
    """Every mark a reader made in the PDF at ``path``, in book order.

    Each entry has ``page``, ``kind`` (highlight, underline, strikeout, text,
    note, drawing, shape), the ``text`` it covers or contains, the ``comment``
    attached to it, and its ``color``, ``author`` and ``modified`` date when the
    file records them. Replies to a mark are folded into its comment. An
    encrypted or unreadable file yields no marks rather than an error.
    """
    items: List[dict] = []
    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                return []
            for page_number, page in enumerate(document, start=1):
                items.extend(_scan_page(page, page_number))
    except Exception:
        return []
    by_xref = {(item["page"], item["_xref"]): item for item in items}
    kept = []
    for item in items:
        parent = by_xref.get((item["page"], item["_reply_to"])) if item["_reply_to"] else None
        reply_text = item["comment"] or item["text"]
        if parent is not None and parent is not item:
            if reply_text:
                parent["comment"] = f"{parent['comment']}\nReply: {reply_text}".strip()
            continue
        kept.append(item)
    kept.sort(key=lambda i: (i["page"], round(i["_y"]), i["_x"]))
    unique, seen = [], set()
    for item in kept:
        key = (item["page"], item["kind"], item["text"], item["comment"])
        if key in seen:
            continue
        seen.add(key)
        unique.append({k: v for k, v in item.items() if not k.startswith("_")})
    return unique


def text_signature(path) -> Optional[str]:
    """A fingerprint of a PDF's own text, ignoring every annotation, so an
    edit that only adds marks can be told apart from one that changes the book."""
    digest = hashlib.sha256()
    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                return None
            digest.update(str(document.page_count).encode())
            for page in document:
                while page.first_annot:
                    page.delete_annot(page.first_annot)  # in memory only; the file is never saved
                digest.update(page.get_text("text").encode("utf-8", "replace"))
    except Exception:
        return None
    return digest.hexdigest()


# --------------------------------------------------- underlined-word finder
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


def _ink_lines(snaps: list) -> List["pymupdf.Rect"]:
    """Thin horizontal pen strokes: underlines drawn with a viewer's pen."""
    lines = []
    for snap in snaps:
        if snap["code"] != pymupdf.PDF_ANNOT_INK or snap["highlighter"]:
            continue
        for stroke in snap["strokes"] or []:
            rect = _stroke_bbox(stroke)
            if _is_thin_horizontal(rect):
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


def find_underlined_words(path) -> List[dict]:
    """Every single word that is underlined in the PDF at ``path``.

    Three sources, so it works however the underlining was made: real PDF
    Underline/Squiggly annotations (Preview, Acrobat, a browser's viewer, Apple
    Books exports...), thin pen strokes drawn along a word, and thin ruled lines
    drawn into the page. Marks over several words are ignored: the notebook
    collects *words*, and a phrase is a highlight. Words with no text layer (a
    scan that has not been OCR'd) cannot be matched.

    Returns one entry per distinct word (case-insensitive), in book order.
    """
    found: "OrderedDict[str, dict]" = OrderedDict()

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
            snaps = _snapshot(page)
            native = [s for s in snaps if s["code"] in (pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_SQUIGGLY)]
            rules = _ruled_lines(page)
            pen = _ink_lines(snaps)
            if not (native or rules or pen):
                continue
            words = page.get_text("words")
            if not words:
                continue
            for snap in native:
                hit = _words_under_rects(words, snap["quads"])
                if len(hit) == 1:
                    add(page_number, words, hit[0], "pdf-underline")
            links = [pymupdf.Rect(link["from"]) for link in page.get_links() if link.get("from")]
            for line in rules:
                index = _word_for_rule(words, line, links)
                if index is not None:
                    add(page_number, words, index, "drawn-line")
            for line in pen:
                index = _word_for_rule(words, line, links)
                if index is not None:
                    add(page_number, words, index, "pen-line")
    return list(found.values())


# ----------------------------------------------------------- search digest
_LABELS = {"highlight": "Highlighted", "underline": "Underlined", "strikeout": "Struck out"}


def digest_text(title: str, annotations: List[dict]) -> str:
    """One Markdown document of everything the reader marked in a book. It is
    indexed like a note, so a question or search can find a highlighted
    passage or a margin comment and cite the page it came from."""
    lines = [f"# Annotations: {title}", ""]
    by_page: "OrderedDict[int, List[dict]]" = OrderedDict()
    for item in annotations:
        if (item.get("text") or "").strip() or (item.get("comment") or "").strip():
            by_page.setdefault(item["page"], []).append(item)
    for page, items in by_page.items():
        lines += [f"## Page {page}", ""]
        for item in items:
            text = " ".join((item.get("text") or "").split())
            comment = (item.get("comment") or "").strip()
            kind = item["kind"]
            if kind == "text":
                lines.append(f"Margin note on page {page}: {text}")
            elif kind == "note":
                lines.append(f"Note on page {page}: {comment}")
            elif kind in _LABELS:
                label = _LABELS[kind]
                lines.append(f"{label} on page {page}:" if text else f"{label} on page {page}.")
                if text:
                    lines.append(f"> {text}")
                if comment:
                    lines.append(f"Comment on page {page}: {comment}")
            else:
                lines.append(f"Comment on page {page}: {comment or text}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
