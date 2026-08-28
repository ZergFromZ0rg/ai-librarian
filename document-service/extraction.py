from difflib import SequenceMatcher
import re
import unicodedata
from typing import Dict, List, Tuple

import pymupdf
import pymupdf4llm


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

REVERSED_DIACRITICS = {
    "´": "\u0301",
    "`": "\u0300",
    "ˆ": "\u0302",
    "^": "\u0302",
    "¨": "\u0308",
    "˜": "\u0303",
    "~": "\u0303",
}


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


def repair_extracted_text(markdown: str, layout: Dict) -> str:
    """Repair font-encoding errors and missing visual word spaces."""
    visible, markdown_offsets = _visible_markdown(markdown)
    raw, raw_metadata = _raw_characters(layout)
    raw_to_visible = {}
    for match in SequenceMatcher(None, raw, visible, autojunk=False).get_matching_blocks():
        for delta in range(match.size):
            raw_to_visible[match.a + delta] = match.b + delta

    replacements = {}
    insertions = set()
    for raw_offset, metadata in enumerate(raw_metadata):
        if metadata is None or raw_offset not in raw_to_visible:
            continue
        visible_offset = raw_to_visible[raw_offset]
        markdown_offset = markdown_offsets[visible_offset]
        character = raw[raw_offset]

        for font_name, glyph_map in KNOWN_GLYPH_MAPS.items():
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
    return _repair_diacritics("".join(repaired))


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

        # Repair one page at a time so large books do not retain hundreds of
        # raw character-layout dictionaries in memory.
        for index, original_page in enumerate(extracted):
            page = fallbacks.get(index, original_page)
            metadata = page.get("metadata") or {}
            layout = document[index].get_text("rawdict", sort=True)
            pages.append(
                {
                    "page": int(metadata.get("page_number") or index + 1),
                    "text": repair_extracted_text(
                        (page.get("text") or "").strip(), layout
                    ),
                    "format": "markdown",
                }
            )
    return pages
