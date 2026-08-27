import re
from typing import List, Tuple


def split_into_paragraphs(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def normalize_for_embedding(markdown: str) -> str:
    """Remove presentation-only Markdown while preserving words and symbols."""
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "").replace("~~", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def trailing_overlap(text: str, max_chars: int) -> str:
    """Return a bounded overlap beginning at a readable boundary."""
    if max_chars <= 0 or not text:
        return ""
    window = text[-max_chars:]
    minimum_tail = min(30, max_chars // 3)
    sentence_boundary = re.search(r"(?<=[.!?])\s+|\n+", window)
    if sentence_boundary and len(window) - sentence_boundary.end() >= minimum_tail:
        return window[sentence_boundary.end() :].lstrip()
    word_boundary = re.search(r"\s+", window)
    if word_boundary:
        return window[word_boundary.end() :].lstrip()
    return window


def split_into_sentences(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def split_oversized_text(text: str, max_size: int) -> List[str]:
    """Split text into bounded pieces, preferring sentence and word boundaries."""
    if len(text) <= max_size:
        return [text]

    pieces = []
    current = ""
    sentences = split_into_sentences(text)
    if len(sentences) == 1:
        sentences = text.split()

    for unit in sentences:
        separator = " " if current else ""
        candidate = f"{current}{separator}{unit}"
        if len(candidate) <= max_size:
            current = candidate
            continue
        if current:
            pieces.append(current)
            current = ""
        while len(unit) > max_size:
            pieces.append(unit[:max_size])
            unit = unit[max_size:]
        current = unit
    if current:
        pieces.append(current)
    return pieces


def make_units_from_pages(pages: List[dict], max_size: int = 800) -> List[Tuple[int, str]]:
    units = []
    for page in pages:
        page_no = page.get("page")
        for paragraph in split_into_paragraphs(page.get("text", "")):
            for piece in split_oversized_text(paragraph, max_size):
                units.append((page_no, piece))
    return units


def build_chunks(
    units: List[Tuple[int, str]],
    max_size: int = 800,
    overlap: int = 100,
) -> List[Tuple[int, str]]:
    if max_size < 1:
        raise ValueError("max_size must be positive")
    if overlap < 0 or overlap >= max_size:
        raise ValueError("overlap must be between zero and max_size - 1")

    chunks = []
    current: List[Tuple[int, str]] = []
    current_length = 0

    for page_no, unit in units:
        if current and page_no != current[-1][0]:
            chunks.append((current[0][0], "\n\n".join(text for _, text in current)))
            current = []
            current_length = 0

        separator_length = 2 if current else 0
        if current and current_length + separator_length + len(unit) > max_size:
            chunk_text = "\n\n".join(text for _, text in current)
            chunks.append((current[0][0], chunk_text))

            available_overlap = max(0, max_size - len(unit) - 2)
            overlap_length = min(overlap, available_overlap)
            overlap_text = trailing_overlap(chunk_text, overlap_length)
            current = [(current[-1][0], overlap_text)] if overlap_text else []
            current_length = len(overlap_text)

        if current:
            current_length += 2
        current.append((page_no, unit))
        current_length += len(unit)

    if current:
        chunks.append((current[0][0], "\n\n".join(text for _, text in current)))

    return chunks


def chunk_document(
    pages: List[dict],
    max_size: int = 800,
    overlap: int = 100,
) -> List[Tuple[int, str]]:
    if max_size < 1:
        raise ValueError("max_size must be positive")
    if overlap < 0 or overlap >= max_size:
        raise ValueError("overlap must be between zero and max_size - 1")
    units = make_units_from_pages(pages, max_size=max_size)
    return build_chunks(units, max_size=max_size, overlap=overlap)
