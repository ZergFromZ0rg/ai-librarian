import re
from typing import List, Tuple


def split_into_paragraphs(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


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
        separator_length = 2 if current else 0
        if current and current_length + separator_length + len(unit) > max_size:
            chunk_text = "\n\n".join(text for _, text in current)
            chunks.append((current[0][0], chunk_text))

            available_overlap = max(0, max_size - len(unit) - 2)
            overlap_length = min(overlap, available_overlap)
            overlap_text = chunk_text[-overlap_length:].lstrip() if overlap_length else ""
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
