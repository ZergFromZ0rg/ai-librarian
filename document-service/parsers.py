"""Text extraction for every source type the library accepts.

The PDF pipeline remains layout-aware in :mod:`extraction`.  Office files do
not have a portable, trustworthy page model, so this module produces a small
set of Markdown "pages" (a Word section, spreadsheet sheet, or slide).  The
normal chunker can then treat every format consistently.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple

from extraction import extract_pages, ocr_pdf_pages

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"})

FILE_TYPES = {
    ".pdf": "pdf",
    ".docx": "word",
    ".xlsx": "excel",
    ".pptx": "powerpoint",
    ".txt": "text",
    ".md": "markdown",
    ".csv": "csv",
}

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "powerpoint": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text": "text/plain; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


def file_type_for_path(path: str | Path) -> str | None:
    return FILE_TYPES.get(Path(path).suffix.lower())


def is_supported_path(path: str | Path) -> bool:
    return file_type_for_path(path) is not None


def media_type_for(file_type: str) -> str:
    return MEDIA_TYPES.get(file_type, "application/octet-stream")


def _clean(value) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _pages_from_text(text: str, *, format_name: str = "markdown") -> List[Dict]:
    """Use bounded pseudo-pages so a huge plain-text file does not form one
    enormous protected block before semantic chunking has a chance to split it."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = []
    current = []
    current_size = 0
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and current_size + len(paragraph) > 12_000:
            blocks.append("\n\n".join(current))
            current, current_size = [], 0
        current.append(paragraph)
        current_size += len(paragraph) + 2
    if current:
        blocks.append("\n\n".join(current))
    return [
        {"page": index, "text": block, "format": format_name, "needs_ocr": False}
        for index, block in enumerate(blocks, start=1)
    ]


def _extract_word(path: Path) -> List[Dict]:
    try:
        from docx import Document
    except ImportError as exc:  # makes a bare local checkout still importable
        raise RuntimeError("Word support is unavailable; install python-docx") from exc

    document = Document(str(path))
    sections = []
    current = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        if style.startswith("heading"):
            if current:
                sections.append("\n\n".join(current))
                current = []
            level = next((char for char in style if char.isdigit()), "2")
            current.append(f"{'#' * min(int(level), 6)} {text}")
        else:
            current.append(text)
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [_clean(cell.text).replace("|", "\\|") for cell in row.cells]
            if any(cells):
                rows.append(cells)
        if rows:
            header = rows[0]
            current.append("| " + " | ".join(header) + " |")
            current.append("| " + " | ".join("---" for _ in header) + " |")
            current.extend("| " + " | ".join(row[: len(header)]) + " |" for row in rows[1:])
    if current:
        sections.append("\n\n".join(current))
    return _pages_from_text("\n\n".join(sections))


def _extract_excel(path: Path) -> List[Dict]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Excel support is unavailable; install openpyxl") from exc

    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    pages = []
    try:
        for index, sheet in enumerate(workbook.worksheets, start=1):
            rows = []
            used_cells = 0
            for row in sheet.iter_rows(values_only=True):
                values = [_clean(value).replace("|", "\\|") for value in row]
                while values and not values[-1]:
                    values.pop()
                if not values:
                    continue
                used_cells += len(values)
                if used_cells > 50_000:
                    rows.append(["[Sheet truncated after 50,000 populated cells]"])
                    break
                rows.append(values)
            if not rows:
                continue
            width = max(len(row) for row in rows)
            normalized = [row + [""] * (width - len(row)) for row in rows]
            header = normalized[0]
            text = [f"# Sheet: {sheet.title}", "", "| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
            text.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
            pages.append({"page": index, "text": "\n".join(text), "format": "markdown", "needs_ocr": False})
    finally:
        workbook.close()
    return pages


def _extract_powerpoint(path: Path) -> List[Dict]:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError("PowerPoint support is unavailable; install python-pptx") from exc

    presentation = Presentation(str(path))
    pages = []
    for index, slide in enumerate(presentation.slides, start=1):
        lines = [f"# Slide {index}"]
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = _clean(shape.text)
                if text:
                    lines.append(text)
            elif getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [_clean(cell.text).replace("|", "\\|") for cell in row.cells]
                    if any(cells):
                        lines.append("| " + " | ".join(cells) + " |")
        if len(lines) > 1:
            pages.append({"page": index, "text": "\n\n".join(lines), "format": "markdown", "needs_ocr": False})
    return pages


def _extract_csv(path: Path) -> List[Dict]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
        rows = list(csv.reader(source))
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = [_clean(value).replace("|", "\\|") for value in normalized[0]]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(_clean(value).replace("|", "\\|") for value in row) + " |" for row in normalized[1:50_001])
    if len(normalized) > 50_001:
        lines.append("\n[CSV truncated after 50,000 rows]")
    return [{"page": 1, "text": "\n".join(lines), "format": "markdown", "needs_ocr": False}]


def extract_source_pages(path: str | Path, *, ocr_mode: str = "auto", ocr_languages: str = "eng") -> Tuple[List[Dict], bool]:
    """Return normalised page dictionaries and whether OCR supplied any text."""
    source = Path(path)
    kind = file_type_for_path(source)
    if kind is None:
        raise ValueError(f"unsupported document type: {source.suffix or 'unknown'}")
    if kind == "pdf":
        pages = extract_pages(str(source))
        targets = [page["page"] for page in pages if page.get("needs_ocr")]
        if targets and ocr_mode != "off":
            pages = ocr_pdf_pages(source, pages, targets, languages=ocr_languages)
        return pages, any(page.get("ocr") for page in pages)
    if kind == "word":
        return _extract_word(source), False
    if kind == "excel":
        return _extract_excel(source), False
    if kind == "powerpoint":
        return _extract_powerpoint(source), False
    if kind == "csv":
        return _extract_csv(source), False
    return _pages_from_text(source.read_text(encoding="utf-8", errors="replace")), False
