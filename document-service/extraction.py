import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import pymupdf
import pymupdf4llm

from glyphs import build_glyph_repairs
from layout import recover_dropped_text

logger = logging.getLogger("ai_librarian.extraction")


# Some older scholarly PDFs embed this operator font without a ToUnicode map.
# PyMuPDF can still identify the font, which lets us recover the displayed
# operators instead of returning misleading digits.
KNOWN_GLYPH_MAPS = {
    "AdvOT410c0b94": {
        "1": "+",
        "2": "−",
        "3": "×",
        "4": "÷",
        "5": "=",
        "6": "±",
        "7": "∓",
        "#": "≤",
        "$": "≥",
        "~": "∝",
    },
    "AdvP4C4E74": {"ð": "(", "Þ": ")"},
    "AdvP4C4E51": {"=": "/"},
}

# Subsetted math fonts routinely ship the series-continuation dots with no
# ToUnicode entry, so PyMuPDF emits a run of U+FFFD where "⋯" belongs. Merge
# neighbouring replacement characters (optionally space-separated, never across
# a line break) into a single ellipsis so the passage stays readable and
# searchable instead of carrying "�" noise into the index.
REPLACEMENT_RUN_PATTERN = re.compile("�(?:[ \t]*�)+")
# A lone replacement character between two words or numbers is almost always a
# dash the font could not encode ("132�151", "Euler-Mayer (1751�1755)").
LONE_REPLACEMENT_DASH = re.compile(r"(?<=[\w)])[ \t]*�[ \t]*(?=[\w(])")
# A range of years whose dash was dropped entirely, leaving only a space —
# common for birth–death dates in a history text ("(1707 1783)").
YEAR_RANGE = re.compile(r"\((\d{3,4})\s+(\d{3,4})\)")
# Unmapped glyphs in symbol fonts (radical strokes, brace pieces) often decode
# to C0 control characters. They are always extraction noise, never content.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _collapse_replacement_runs(text: str) -> str:
    text = CONTROL_CHARACTERS.sub("", text)
    text = REPLACEMENT_RUN_PATTERN.sub("⋯", text)
    text = LONE_REPLACEMENT_DASH.sub("–", text)
    text = YEAR_RANGE.sub(r"(\1–\2)", text)
    return text


# pymupdf4llm emits <sup>/<sub> when the PDF marks a span as super/subscripted.
# The UI sanitises HTML away and the embedding normaliser would too, so those
# tags currently reach the reader as baseline digits ("z<sup>3</sup>" -> "z3").
# Rewrite them here: transliterate to real Unicode super/subscripts when every
# character has one, otherwise fall back to plain "x^(a+b)" / "H_(2)O" so the
# exponent is never silently flattened.
SUPERSCRIPT_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
    "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼",
    "(": "⁽", ")": "⁾", "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ",
    "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ",
    "m": "ᵐ", "n": "ⁿ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ",
    "u": "ᵘ", "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}
SUBSCRIPT_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
    "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "−": "₋", "=": "₌",
    "(": "₍", ")": "₎", "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ",
    "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}
SCRIPT_TAG_PATTERN = re.compile(r"<(sup|sub)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_INNER_TAG_PATTERN = re.compile(r"<[^>]+>")
_EMPHASIS_PATTERN = re.compile(r"\*\*|__|[*_`]")


def _rewrite_scripts(markdown: str) -> str:
    def replace(match: "re.Match[str]") -> str:
        kind = match.group(1).lower()
        content = _EMPHASIS_PATTERN.sub("", _INNER_TAG_PATTERN.sub("", match.group(2))).strip()
        if not content:
            return ""
        table = SUPERSCRIPT_MAP if kind == "sup" else SUBSCRIPT_MAP
        marker = "^" if kind == "sup" else "_"
        if all(character in table for character in content):
            return "".join(table[character] for character in content)
        if len(content) == 1 or (content.startswith("(") and content.endswith(")")):
            return f"{marker}{content}"
        return f"{marker}({content})"

    return SCRIPT_TAG_PATTERN.sub(replace, markdown)

REVERSED_DIACRITICS = {
    "´": "\u0301",
    "`": "\u0300",
    "ˆ": "\u0302",
    "^": "\u0302",
    "¨": "\u0308",
    "˜": "\u0303",
    "~": "\u0303",
}


_CEDILLA_AFTER_LETTER = re.compile("([cCsStTgGkKlLnNrR])[¸̧ˋ]")
# An acute that landed on a following consonant it can never sit on, one letter
# past the vowel it belongs to ("Acadeḿie" -> "Académie"). Restricted
# to m/l/r, where an acute is never legitimate, so Polish ć/ń etc. are
# left alone. Matched in NFD form: the acute is combining U+0301.
_ACUTE_ON_CONSONANT = re.compile("([aeiouAEIOU])([mlrMLR])́")


def _visible_markdown(text: str) -> Tuple[str, List[int]]:
    """Return rendered text plus a map back to Markdown character offsets."""
    visible = []
    offsets = []
    in_tag = False
    for offset, character in enumerate(text):
        if character == "<":
            in_tag = True
        if not in_tag:
            visible.append(character)
            offsets.append(offset)
        if in_tag and character == ">":
            in_tag = False
    return "".join(visible), offsets


def _raw_characters(layout: Dict) -> Tuple[str, List[Dict | None]]:
    """Flatten positioned PDF characters while retaining font and gap data."""
    text = []
    metadata: List[Dict | None] = []
    for block in layout.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            previous = None
            for span in line.get("spans", []):
                for character in span.get("chars", []):
                    bbox = character.get("bbox") or (0, 0, 0, 0)
                    text.append(character.get("c", ""))
                    metadata.append(
                        {
                            "font": span.get("font", ""),
                            "size": float(span.get("size") or 0),
                            "bbox": bbox,
                            "gap": None if previous is None else float(bbox[0] - previous[2]),
                        }
                    )
                    previous = bbox
            text.append("\n")
            metadata.append(None)
    return "".join(text), metadata


def _repair_diacritics(text: str) -> str:
    pattern = "[" + re.escape("".join(REVERSED_DIACRITICS)) + "]"

    def replace(match: re.Match) -> str:
        accent, letter = match.groups()
        return unicodedata.normalize("NFC", letter + REVERSED_DIACRITICS[accent])

    text = re.sub(f"({pattern})([A-Za-z])", replace, text)

    text = _CEDILLA_AFTER_LETTER.sub(
        lambda m: unicodedata.normalize("NFC", m.group(1) + "\u0327"), text
    )

    decomposed = unicodedata.normalize("NFD", text)
    shifted = _ACUTE_ON_CONSONANT.sub("\\1\u0301\\2", decomposed)
    if shifted != decomposed:
        text = unicodedata.normalize("NFC", shifted)

    # A few title lines put an acute accent at the end of the next word even
    # though it visually belongs on the preceding final "e" (for example,
    # "Rene Descartes´"). Limit this correction to Markdown headings.
    def repair_heading(match: re.Match) -> str:
        prefix, first_word, second_word = match.groups()
        first_word = unicodedata.normalize("NFC", first_word[:-1] + "e\u0301")
        return f"{prefix}{first_word} {second_word}"

    return re.sub(
        r"(?m)^(#{1,6}\s+)([A-Za-z]*e)\s+([A-Za-z]+)´\s*$",
        repair_heading,
        text,
    )


def repair_extracted_text(markdown: str, layout: Dict, glyph_maps: Dict | None = None) -> str:
    """Repair font-encoding errors and missing visual word spaces.

    ``glyph_maps`` supplies extra ``{font_name: {wrong_char: symbol}}`` entries
    recovered by shape from this document (see ``glyphs.build_glyph_repairs``);
    the hand-written ``KNOWN_GLYPH_MAPS`` take precedence over them.
    """
    combined_maps = {**(glyph_maps or {}), **KNOWN_GLYPH_MAPS}
    visible, markdown_offsets = _visible_markdown(markdown)
    raw, raw_metadata = _raw_characters(layout)
    matcher = SequenceMatcher(None, raw, visible, autojunk=False)
    raw_to_visible = {}
    for match in matcher.get_matching_blocks():
        for delta in range(match.size):
            raw_to_visible[match.a + delta] = match.b + delta
    # ``to_markdown`` emits U+FFFD for a glyph it cannot decode, which the diff
    # above cannot line up with the original glyph character. Align an
    # equal-length replaced region that is all replacement characters back onto
    # the raw glyphs, so a glyph map can still recover them.
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if (
            tag == "replace"
            and i2 - i1 == j2 - j1
            and all(visible[j] == "�" for j in range(j1, j2))
        ):
            for delta in range(i2 - i1):
                raw_to_visible.setdefault(i1 + delta, j1 + delta)

    replacements = {}
    insertions = set()
    for raw_offset, metadata in enumerate(raw_metadata):
        if metadata is None or raw_offset not in raw_to_visible:
            continue
        visible_offset = raw_to_visible[raw_offset]
        markdown_offset = markdown_offsets[visible_offset]
        character = raw[raw_offset]

        for font_name, glyph_map in combined_maps.items():
            if font_name in metadata["font"] and character in glyph_map:
                replacements[markdown_offset] = glyph_map[character]
                break

        if raw_offset == 0 or not character.isalpha() or not raw[raw_offset - 1].isalpha():
            continue
        previous_visible = raw_to_visible.get(raw_offset - 1)
        if previous_visible is None or previous_visible + 1 != visible_offset:
            continue
        threshold = max(0.8, metadata["size"] * 0.12)
        if (
            metadata["size"] >= 9.5
            and metadata["gap"] is not None
            and metadata["gap"] > threshold
        ):
            insertions.add(markdown_offset)

    repaired = []
    for offset, character in enumerate(markdown):
        if offset in insertions:
            repaired.append(" ")
        repaired.append(replacements.get(offset, character))
    return _rewrite_scripts(_repair_diacritics(_collapse_replacement_runs("".join(repaired))))


_WORD_TOKEN = re.compile(r"[A-Za-z]{2,}")
_HAS_VOWEL = re.compile(r"[aeiouy]")
_TRIPLED_LETTER = re.compile(r"(.)\1\1")
# Roman numerals legitimately carry "ii"/"iii" runs ("xiii", "xviii"); a table
# of contents or an index is dense with them and must not read as OCR garbage.
_ROMAN_NUMERAL = re.compile(r"^m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def _page_text_is_garbled(text: str) -> Optional[bool]:
    """Whether a page's words carry the fingerprints of a bad OCR text layer:
    m/n/u misread as "ii", vowels dropped, letters tripled. ``None`` when the
    page has too little prose to judge."""
    tokens = [token.lower() for token in _WORD_TOKEN.findall(text)]
    if len(tokens) < 40:
        return None
    count = len(tokens)
    # Roman numerals count as prose for the denominator but are exempt from the
    # fingerprints they would otherwise trip ("xiii" -> "ii" run and a tripled
    # letter), so a table of contents or index does not read as OCR garbage.
    prose = [token for token in tokens if not _ROMAN_NUMERAL.match(token)]
    ii_runs = sum("ii" in token for token in prose)
    vowelless = sum(len(token) >= 4 and not _HAS_VOWEL.search(token) for token in prose)
    tripled = sum(bool(_TRIPLED_LETTER.search(token)) for token in prose)
    return (
        ii_runs / count > 0.012
        or vowelless / count > 0.03
        or tripled / count > 0.015
    )


# A document with at least this fraction of judged pages tripping the
# OCR-mangling fingerprints is treated as a failed scan and refused whole.
# Below it, the garbled pages are dropped and the remainder is indexed.
CATASTROPHIC_GARBLED_FRACTION = 0.35
_MIN_JUDGED_PAGES = 5


def garbled_page_indices(pages: List[Dict]) -> List[int]:
    """0-based indices of pages whose text layer reads as OCR garbage.

    Only pages with enough prose to judge and a positive verdict are returned;
    equation-dense or sparse pages (verdict ``None``) are never listed.
    """
    return [
        index
        for index, page in enumerate(pages)
        if _page_text_is_garbled(page.get("text", "")) is True
    ]


def assess_text_layer(pages: List[Dict]) -> Optional[str]:
    """Return a rejection reason only when the text layer is *mostly* OCR garbage.

    Some PDFs are scans carrying a baked-in OCR layer. When most judged pages
    are corrupt the whole file is refused (re-running OCR is out of scope, see
    ``extract_pages``). When only a minority are corrupt the caller drops those
    pages and indexes the rest — see ``garbled_page_indices``.
    """
    verdicts = [
        verdict
        for page in pages
        if (verdict := _page_text_is_garbled(page.get("text", ""))) is not None
    ]
    if len(verdicts) < _MIN_JUDGED_PAGES:
        return None
    garbled_fraction = sum(verdicts) / len(verdicts)
    if garbled_fraction >= CATASTROPHIC_GARBLED_FRACTION:
        return (
            "this PDF's text layer appears to be corrupted "
            f"({garbled_fraction:.0%} of pages are unreadable) — it is most likely a "
            "scan with a poor OCR layer; re-OCR the file and upload it again"
        )
    return None


def describe_skipped_pages(count: int, total: int) -> str:
    """The note stored on a document that indexed with some pages dropped."""
    return (
        f"{count} of {total} pages were skipped because their text layer is "
        "corrupted (most likely a poor OCR scan of those pages); the rest of "
        "the document was indexed normally."
    )


# --- Front/back-matter pages --------------------------------------------------
# A table of contents, a back-of-book index, and a bibliography are all dense
# with on-topic keywords but carry no prose worth retrieving. Left in the index
# they outrank real explanations for broad queries ("what is a wormhole" pulling
# up the contents page). Detect them by shape and drop them before chunking.

_BOILERPLATE_HEADING = re.compile(
    r"^\s{0,3}#{0,4}\s*\**\s*(?:"
    r"(?:table\s+of\s+)?contents"
    r"|(?:subject\s+|name\s+|author\s+)?index"
    r"|(?:select(?:ed)?\s+)?(?:bibliography|references|works\s+cited"
    r"|related\s+reading|further\s+reading|literature\s+cited)"
    r"|list\s+of\s+(?:figures|tables|illustrations|plates|maps)"
    r")\b",
    re.IGNORECASE | re.MULTILINE,
)
# Three-plus (optionally spaced) dots running into a page number: "Gateways .... 89".
_DOT_LEADER = re.compile(r"(?:\.\s?){3,}\s*\d")
_BARE_NUMBER = re.compile(r"\d{1,4}[.,;:)]?")
_PAGE_RANGE = re.compile(r"\b\d{1,4}\s*[–—-]\s*\d{1,4}\b")
# "term, 12, 45" — a comma directly followed by a page number. Indexes are full
# of these even when the page's line breaks were flattened into one long run.
_COMMA_NUMBER = re.compile(r",\s*\d{1,4}")
# A line ending in a page reference: some text, then a number (optionally a range).
_TRAILING_PAGE_REF = re.compile(r"\D\s\d{1,4}(?:[–—-]\d{1,4})?\s*$")
# An index line: a term, then a comma-separated list of page numbers/ranges.
_INDEX_ENTRY = re.compile(
    r"[A-Za-z].*?,\s*\d{1,4}(?:[–—-]\d{1,4})?"
    r"(?:\s*,\s*\d{1,4}(?:[–—-]\d{1,4})?)*\.?\s*$"
)
_CITATION_YEAR = re.compile(r"\((?:1[6-9]\d\d|20\d\d)[a-z]?\)")
_MIN_BOILERPLATE_LINES = 8


def _page_is_boilerplate(text: str) -> bool:
    tokens = text.split()
    if len(tokens) < 30:
        return False

    numeric = sum(1 for token in tokens if _BARE_NUMBER.fullmatch(token))
    page_ranges = len(_PAGE_RANGE.findall(text))
    comma_numbers = len(_COMMA_NUMBER.findall(text))
    # Tokens that are a bare page number or part of a page range: contents and
    # index pages run 30%+ page references, prose almost never breaks 15%.
    numberish_ratio = (numeric + 2 * page_ranges) / len(tokens)
    dot_leaders = len(_DOT_LEADER.findall(text))
    citations = len(_CITATION_YEAR.findall(text))
    has_heading = bool(_BOILERPLATE_HEADING.search(text))

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= _MIN_BOILERPLATE_LINES:
        trailing = sum(bool(_TRAILING_PAGE_REF.search(line)) for line in lines) / len(lines)
        index_entry = sum(bool(_INDEX_ENTRY.match(line)) for line in lines) / len(lines)
    else:
        trailing = index_entry = 0.0

    # Any one of these shapes is decisive on its own.
    if numberish_ratio > 0.30 and (numeric + page_ranges) >= 12:
        return True
    if dot_leaders >= 3:
        return True
    if comma_numbers >= 10 and page_ranges >= 4:
        return True
    if trailing > 0.55 or index_entry > 0.45:
        return True
    if citations >= 8 and citations / max(len(lines), 1) > 0.4:
        return True
    # A front/back-matter heading plus a milder-but-present list shape.
    if has_heading and (
        numberish_ratio > 0.15
        or dot_leaders >= 1
        or page_ranges >= 4
        or comma_numbers >= 5
        or trailing > 0.3
        or index_entry > 0.25
        or citations >= 3
    ):
        return True
    return False


def boilerplate_page_indices(pages: List[Dict]) -> List[int]:
    """0-based indices of front/back-matter pages (contents, index, bibliography).

    These pages are keyword-dense but hold no retrievable prose; indexing them
    lets them outrank real answers for broad queries.
    """
    return [
        index
        for index, page in enumerate(pages)
        if _page_is_boilerplate(page.get("text", ""))
    ]


def extract_pages(pdf_path: str) -> List[Dict]:
    """Extract layout-aware Markdown per page from a text-based PDF.

    OCR is intentionally disabled: scanned pages remain explicit ingestion errors
    instead of silently producing low-quality mathematical or symbolic text.
    """
    pages = []
    with pymupdf.open(str(pdf_path)) as document:
        if document.needs_pass:
            raise ValueError("password-protected PDFs are not supported")
        extracted = pymupdf4llm.to_markdown(
            document,
            page_chunks=True,
            use_ocr=False,
            show_progress=False,
            header=False,
            footer=False,
        )

        # A layout classifier can occasionally mistake all content on a sparse
        # page for a header/footer. Preserve the page rather than returning it
        # empty, while still removing normal running headers from book pages.
        empty_pages = [
            index for index, page in enumerate(extracted) if not (page.get("text") or "").strip()
        ]
        fallbacks = {}
        for index in empty_pages:
            fallback = pymupdf4llm.to_markdown(
                document,
                pages=[index],
                page_chunks=True,
                use_ocr=False,
                show_progress=False,
                header=True,
                footer=True,
            )
            if fallback:
                fallbacks[index] = fallback[0]

        # Identify the document's mis-encoded symbol-font glyphs once, by shape.
        try:
            glyph_maps = build_glyph_repairs(document)
        except Exception:
            logger.exception("Glyph shape recovery failed; continuing without it")
            glyph_maps = {}

        # Repair one page at a time so large books do not retain hundreds of
        # raw character-layout dictionaries in memory.
        for index, original_page in enumerate(extracted):
            page = fallbacks.get(index, original_page)
            metadata = page.get("metadata") or {}
            source_page = document[index]
            markdown = recover_dropped_text(
                source_page, (page.get("text") or "").strip()
            )
            layout = source_page.get_text("rawdict", sort=True)
            pages.append(
                {
                    "page": int(metadata.get("page_number") or index + 1),
                    "text": repair_extracted_text(markdown, layout, glyph_maps),
                    "format": "markdown",
                }
            )
    return pages
