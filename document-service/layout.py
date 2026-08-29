"""Recover text that ``pymupdf4llm.to_markdown`` drops.

``to_markdown`` gives good prose, heading and table structure, but on pages with
centred display equations it frequently emits nothing where the equation was —
the equation text is entangled with the vector strokes that draw fraction bars
and radicals, and the layout pass clips both out together. The equation text is
still available from the raw text layer.

This module reads the page in proper reading order (handling one- and two-column
layouts and full-width spanning headers), finds the lines ``to_markdown`` left
out, and splices them back beside the line they followed so the equations reach
the index.
"""

import re
import unicodedata
from typing import Dict, List

# Characters that mark a line as "mathematics" rather than prose. Used both to
# decide whether a recovered run should be fenced as an equation and to bias the
# dropped-line detection towards symbol-heavy content.
EQUATION_NUMBER = re.compile(r"^\(?\d{1,3}[a-z]?\)?[.,]?$")

MATH_CHARACTERS = re.compile(
    r"[=<>≤≥≠≈≡∑∏∫∮√∞±∓×÷·−∂∇⊗⊕⊙⊤⊥∥∠⌊⌋⌈⌉⟨⟩⇒⇐⇔⟹→←↦∈∉⊂⊆⊃⊇∪∩∀∃∄∅"
    r"λμνξπρστφχψωΓ∆ΘΛΞΠΣΦΨΩαβγδεζηθικ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ₀₁₂₃₄₅₆₇₈₉]"
)
_SIGNATURE_STRIP = re.compile(
    "<[^>]+>|[*_`#>~\\s­‐‑‒–—\\-�\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+"
)
RELATION = re.compile("[=≈≅≃≠≤≥∝≡]|⟹|⇒|→|↦")


def _signature(text: str) -> str:
    """Collapse a string to a comparable core.

    Removes markup, whitespace, every kind of hyphen/dash, and undecodable
    glyphs, then applies NFKC so ligatures ("ﬁ") compare equal to their parts.
    This lets a de-hyphenated raw line match its wrapped Markdown counterpart.
    """
    return _SIGNATURE_STRIP.sub("", unicodedata.normalize("NFKC", text)).casefold()


def _span_text(span: Dict) -> str:
    if "text" in span:
        return span["text"]
    return "".join(character.get("c", "") for character in span.get("chars", []))


def page_lines_in_reading_order(page) -> List[Dict]:
    """Return every text line as ``{text, x0, y0, x1, y1}`` ordered for reading.

    Two-column bodies are emitted left column then right column within each
    horizontal band; a line that spans the centre (a title or a full-width
    section heading) flushes the current band and starts a new one.
    """
    raw = page.get_text("dict")
    lines: List[Dict] = []
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(_span_text(span) for span in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    if not lines:
        return []

    page_left = min(line["x0"] for line in lines)
    page_right = max(line["x1"] for line in lines)
    span = page_right - page_left
    if span <= 0:
        return sorted(lines, key=lambda line: (round(line["y0"], 1), line["x0"]))
    midline = page_left + span / 2
    margin = span * 0.04

    def spans_centre(line: Dict) -> bool:
        return line["x0"] < midline - margin and line["x1"] > midline + margin

    body = [line for line in lines if not spans_centre(line)]
    left = [line for line in body if (line["x0"] + line["x1"]) / 2 < midline]
    right = [line for line in body if (line["x0"] + line["x1"]) / 2 >= midline]
    if len(left) < 3 or len(right) < 3:
        return sorted(lines, key=lambda line: (round(line["y0"], 1), line["x0"]))

    ordered: List[Dict] = []
    band_left: List[Dict] = []
    band_right: List[Dict] = []

    def flush() -> None:
        ordered.extend(sorted(band_left, key=lambda line: line["y0"]))
        ordered.extend(sorted(band_right, key=lambda line: line["y0"]))
        band_left.clear()
        band_right.clear()

    for line in sorted(lines, key=lambda line: line["y0"]):
        if spans_centre(line):
            flush()
            ordered.append(line)
        elif (line["x0"] + line["x1"]) / 2 < midline:
            band_left.append(line)
        else:
            band_right.append(line)
    flush()
    return ordered


def _rows_in_order(run: List[Dict]) -> List[Dict]:
    if not run:
        return run
    heights = sorted(max(1.0, line["y1"] - line["y0"]) for line in run)
    tolerance = heights[len(heights) // 2] * 0.6
    rows: List[List[Dict]] = []
    for line in sorted(run, key=lambda item: item["y0"]):
        if rows and line["y0"] - rows[-1][0]["y0"] <= tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    ordered: List[Dict] = []
    trailing_numbers: List[Dict] = []
    for row in rows:
        numbers = [item for item in row if EQUATION_NUMBER.match(item["text"].strip())]
        rest = [item for item in row if item not in numbers]
        if not rest:
            # A right-aligned "(14)" vertically centred on a two-line equation
            # lands in its own row; keep it out of the reading flow.
            trailing_numbers.extend(numbers)
            continue
        ordered.extend(sorted(rest, key=lambda item: item["x0"]))
        ordered.extend(sorted(numbers, key=lambda item: item["x0"]))
    return ordered + trailing_numbers


def _math_score(text: str) -> float:
    letters = sum(character.isalpha() and character.isascii() for character in text)
    symbols = len(MATH_CHARACTERS.findall(text)) + text.count("/") + text.count("^") + text.count("=")
    return symbols / max(1, letters + symbols)


def _looks_like_math(text: str) -> bool:
    symbols = len(MATH_CHARACTERS.findall(text)) + text.count("/") + text.count("^") + text.count("=")
    return symbols >= 1 and _math_score(text) >= 0.12


def _looks_like_prose(text: str) -> bool:
    """A long, symbol-sparse line is almost certainly a normalisation mismatch
    (hyphenation, ligature, OCR wobble), not something ``to_markdown`` dropped."""
    words = re.findall(r"[A-Za-z]{2,}", text)
    return len(text) > 45 and len(words) >= 7 and _math_score(text) < 0.06


def recover_dropped_text(page, markdown: str) -> str:
    """Splice lines missing from ``markdown`` back in, next to their anchor line."""
    lines = page_lines_in_reading_order(page)
    if not lines:
        return markdown

    markdown_signature = _signature(markdown)

    def is_present(line: Dict) -> bool:
        signature = _signature(line["text"])
        if len(signature) < 3 or signature in markdown_signature:
            return True
        # Treat a prose line we can't match as "present": re-inserting it would
        # duplicate a paragraph over a trivial hyphen/ligature difference.
        return _looks_like_prose(line["text"])

    present = [is_present(line) for line in lines]

    total_recovered = 0
    budget = max(200, len(_signature("".join(line["text"] for line in lines))) // 3)
    additions: List[tuple] = []  # (anchor_text, block_text)

    index = 0
    while index < len(lines):
        if present[index]:
            index += 1
            continue
        start = index
        while index < len(lines) and not present[index]:
            index += 1
        # A dropped run is virtually always one displayed block. Cluster its
        # lines into visual rows (labels like "maximize" sit slightly off the
        # baseline of the formula beside them), order rows top-to-bottom and
        # each row left-to-right, and push a trailing "(14)" equation number to
        # the end so "maximize / objective / subject to / (n)" reads correctly.
        run_lines = _rows_in_order(lines[start:index])
        joined = " ".join(line["text"].strip() for line in run_lines if line["text"].strip())
        joined = re.sub(r"\s{2,}", " ", joined).strip()
        signature = _signature(joined)
        # Skip suspiciously large gaps: those signal a whole misaligned column,
        # not a dropped equation, and re-inserting them would duplicate text.
        if len(signature) < 3 or len(signature) > 600:
            continue
        # A bare page number or lone equation label is noise, not content.
        if re.fullmatch(r"[\d\s.,()–—-]+", joined):
            continue
        # A run that is several very short fragments is a stacked display
        # (nested fractions, a system of equations) shredded by the PDF's vector
        # rules. We cannot reassemble it faithfully, and the pieces out of order
        # are worse than the gap, so leave it out.
        contentful = [line for line in run_lines if len(line["text"].strip()) > 1]
        if len(contentful) >= 3:
            lengths = sorted(len(line["text"].strip()) for line in contentful)
            if lengths[len(lengths) // 2] < 14:
                continue
        # Recover only what reads as a real equation: it carries a relation
        # symbol (=, ≤, ∝, ⟹ …) or is a substantial expression. A short
        # fragment with no relation is usually a stacked display whose operators
        # were lost to a broken symbol font ("sin x 5 e" for "sin x = eⁱˣ") —
        # re-inserting it just adds noise.
        if not RELATION.search(joined) and len(signature) < 25:
            continue
        if signature in markdown_signature or total_recovered + len(signature) > budget:
            continue
        anchor = None
        for position in range(start - 1, -1, -1):
            if present[position] and len(_signature(lines[position]["text"])) >= 10:
                anchor = lines[position]["text"]
                break
        # Fence as an equation only when the whole run reads as mathematics.
        # A prose line inside the run means the grouping is loose, so keep it as
        # plain text — still indexed, just not misrepresented as a formula.
        prose_contaminated = any(_looks_like_prose(line["text"]) for line in run_lines)
        if _looks_like_math(joined) and not prose_contaminated:
            block = f"$$ {joined} $$"
        else:
            block = joined
        additions.append((anchor, block))
        total_recovered += len(signature)

    result = markdown
    for anchor, block in additions:
        cut = _anchor_end(result, anchor) if anchor else -1
        # An out-of-context dump at the page top is worse than the gap: only
        # splice a recovered block when we can place it after a known line.
        if cut >= 0:
            result = f"{result[:cut]}\n\n{block}\n{result[cut:]}"
    return result


def _anchor_end(markdown: str, anchor: str) -> int:
    """Index just past ``anchor`` in ``markdown``, tolerant of line wrapping and
    markup. Tries the raw tail first, then a signature match that ignores
    markup, whitespace and hyphenation."""
    tail = anchor.strip()[-40:]
    position = markdown.find(tail)
    if position >= 0:
        return position + len(tail)

    target = _signature(anchor)[-30:]
    if len(target) < 12:
        return -1
    signature_chars = []
    source_index = []
    in_tag = False
    for index, character in enumerate(markdown):
        if character == "<":
            in_tag = True
            continue
        if in_tag:
            in_tag = character != ">"
            continue
        for piece in _SIGNATURE_STRIP.sub("", unicodedata.normalize("NFKC", character)).casefold():
            signature_chars.append(piece)
            source_index.append(index)
    found = "".join(signature_chars).find(target)
    if found < 0:
        return -1
    return source_index[found + len(target) - 1] + 1
