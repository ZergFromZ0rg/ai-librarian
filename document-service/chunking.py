import re
from typing import List, Tuple


def split_into_paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    return parts


def split_into_sentences(text: str) -> List[str]:
    sentence_end = re.compile(r'(?<=[.!?])\s+')
    parts = [s.strip() for s in sentence_end.split(text) if s.strip()]
    return parts


def make_units_from_pages(pages_list: List[dict]) -> List[Tuple[int, str]]:
    units = []  # list of (page, text_unit)
    for p in pages_list:
        page_no = p.get("page")
        text = p.get("text", "")
        paragraphs = split_into_paragraphs(text)
        for para in paragraphs:
            if len(para) > 1200:
                for sent in split_into_sentences(para):
                    units.append((page_no, sent))
            else:
                units.append((page_no, para))
    return units


def build_chunks(units: List[Tuple[int, str]], max_size: int = 800, overlap: int = 100) -> List[Tuple[int, str]]:
    chunks = []
    current = []
    current_len = 0
    for page_no, unit in units:
        unit_len = len(unit)
        if current_len + unit_len <= max_size or not current:
            current.append((page_no, unit))
            current_len += unit_len + 1
        else:
            chunk_text = "\n\n".join(u for _, u in current)
            chunks.append((current[0][0], chunk_text))
            overlap_text = chunk_text[-overlap:] if overlap > 0 else ""
            current = [(current[-1][0], overlap_text), (page_no, unit)] if overlap_text else [(page_no, unit)]
            current_len = sum(len(u) for _, u in current) + len(current) - 1

    if current:
        chunk_text = "\n\n".join(u for _, u in current)
        chunks.append((current[0][0], chunk_text))

    return chunks


def chunk_document(pages: List[dict], max_size: int = 800, overlap: int = 100) -> List[Tuple[int, str]]:
    units = make_units_from_pages(pages)
    return build_chunks(units, max_size=max_size, overlap=overlap)
