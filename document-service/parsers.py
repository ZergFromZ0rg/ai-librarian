"""Text extraction for every source type the library accepts.

The PDF pipeline remains layout-aware in :mod:`extraction`.  Office files do
not have a portable, trustworthy page model, so this module produces a small
set of Markdown "pages" (a Word section, spreadsheet sheet, or slide).  The
normal chunker can then treat every format consistently.
"""

from __future__ import annotations

import csv
import datetime
import re
from pathlib import Path
from typing import Dict, List, Tuple

import pymupdf

from extraction import extract_pages
from ocr import ocr_pages

# Raised (as a plain ValueError, so it survives the extraction process pool)
# when a scan needs more OCR than may run without the reader's go-ahead. The
# page count follows the marker.
OCR_APPROVAL_MARKER = "ocr-approval-required:"

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".epub", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"})

FILE_TYPES = {
    ".pdf": "pdf",
    ".epub": "epub",
    ".docx": "word",
    ".xlsx": "excel",
    ".pptx": "powerpoint",
    ".txt": "text",
    ".md": "markdown",
    ".csv": "csv",
}

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "epub": "application/epub+zip",
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


# What a "page" is for each source type, as shown to readers and the LLM.
_LOCATION_UNITS = {
    "pdf": ("p.", "pp."),
    "powerpoint": ("slide", "slides"),
    "excel": ("sheet", "sheets"),
    "word": ("section", "sections"),
    "text": ("section", "sections"),
    "markdown": ("section", "sections"),
    "csv": ("table", "table"),
}


def location_label(file_type: str | None, page, page_end=None) -> str:
    """ "p. 12", "pp. 12–13", "slide 4", "sheet 2"… for a passage's position."""
    if page is None:
        return "location unknown"
    if file_type == "csv":
        return "table"
    single, plural = _LOCATION_UNITS.get(file_type or "pdf", _LOCATION_UNITS["pdf"])
    if page_end and page_end != page:
        return f"{plural} {page}–{page_end}"
    return f"{single} {page}"


def _clean(value) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _cell_text(value) -> str:
    """A spreadsheet cell as text; a date with no time of day drops the
    meaningless ``00:00:00`` openpyxl gives every date cell."""
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time(0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="minutes")
    return _clean(value)


def _records_markdown(rows: List[List[str]]) -> str:
    """Tabular data (a sheet, a CSV) as one ``Header: value; ...`` record per
    row, each its own paragraph.

    Rendered as a Markdown grid, a row's meaning ("Owner: Chidi") only exists
    by lining it up with a header many lines away, and the cross-encoder
    reranker cannot do that: a single matching row buried in a sheet-sized
    ``| a | b |`` table scored below the relevance gate where the same rows as
    labelled records scored well above it. Records also let the chunker pack
    a few rows per passage instead of treating the sheet as one block.
    """
    rows = [row for row in rows if any(cell for cell in row)]
    title = []
    # A lone leading cell ("Q3 expenses") is a title, not a one-column header.
    while len(rows) > 1 and sum(1 for cell in rows[0] if cell) < 2 <= max(
        sum(1 for cell in row if cell) for row in rows[1:]
    ):
        title.append(next(cell for cell in rows.pop(0) if cell))
    if not rows:
        return "\n\n".join(title)
    header = [cell or f"Column {number}" for number, cell in enumerate(rows[0], start=1)]
    records = []
    for row in rows[1:]:
        pairs = [
            f"{header[i] if i < len(header) else f'Column {i + 1}'}: {cell}"
            for i, cell in enumerate(row)
            if cell
        ]
        records.append("; ".join(pairs))
    if not records:  # a header-only sheet still says what it is for
        records.append("; ".join(cell for cell in rows[0] if cell))
    return "\n\n".join([*title, *records])


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


def _extract_epub(path: Path) -> List[Dict]:
    """Read the same fixed-layout pages that the cover and reader render.

    EPUB normally reflows to the reader's viewport. PyMuPDF uses a stable
    default layout when opening the file, so page numbers in search results
    refer to the same pages served by the page-image endpoint.
    """
    pages = []
    with pymupdf.open(str(path)) as book:
        if book.needs_pass:
            raise ValueError("password-protected EPUBs are not supported")
        for index in range(book.page_count):
            paragraphs = [
                " ".join(block[4].split())
                for block in book[index].get_text("blocks", sort=True)
                if block[6] == 0 and block[4].strip()
            ]
            pages.append({
                "page": index + 1,
                "text": "\n\n".join(paragraphs),
                "format": "text",
                "needs_ocr": False,
            })
    return pages


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
                values = [_cell_text(value) for value in row]
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
            text = f"## {sheet.title}\n\n{_records_markdown(rows)}"
            pages.append({"page": index, "text": text, "format": "markdown", "needs_ocr": False})
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
        # The slide's own title is its heading; the location label already
        # says "slide N", so "# Slide N" would only repeat it.
        title_shape = slide.shapes.title
        title = _clean(title_shape.text) if title_shape is not None and title_shape.has_text_frame else ""
        lines = [f"# {title}" if title else f"# Slide {index}"]
        for shape in slide.shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if getattr(shape, "has_text_frame", False):
                text = _clean(shape.text)
                if text:
                    lines.append(text)
            elif getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [_clean(cell.text).replace("|", "\\|") for cell in row.cells]
                    if any(cells):
                        lines.append("| " + " | ".join(cells) + " |")
        # Presenters often put the actual explanation in the notes, not on the slide.
        notes = _clean(slide.notes_slide.notes_text_frame.text) if slide.has_notes_slide else ""
        if notes:
            prefix = "" if notes.lower().startswith(("speaker notes", "notes:")) else "Speaker notes: "
            lines.append(prefix + notes)
        if len(lines) > 1:
            pages.append({"page": index, "text": "\n\n".join(lines), "format": "markdown", "needs_ocr": False})
    return pages


def _extract_csv(path: Path) -> List[Dict]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
        rows = list(csv.reader(source))
    if not rows:
        return []
    cleaned = [[_clean(value) for value in row] for row in rows[:50_001]]
    text = _records_markdown([row for row in cleaned if any(row)])
    if len(rows) > 50_001:
        text += "\n\n[CSV truncated after 50,000 rows]"
    return [{"page": 1, "text": text, "format": "markdown", "needs_ocr": False}] if text else []


def _tidy_tesseract(text: str) -> str:
    """Re-join words Tesseract leaves hyphenated across line ends."""
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


def _apply_ocr(source: Path, pages: List[Dict], *, languages: str, engine: str, approved: bool, approval_pages: int) -> Tuple[List[Dict], bool]:
    targets = [page["page"] for page in pages if page.get("needs_ocr")]
    if not targets:
        return pages, False
    if approval_pages and len(targets) > approval_pages and not approved:
        raise ValueError(f"{OCR_APPROVAL_MARKER}{len(targets)}")
    results = ocr_pages(source, targets, languages=languages, engine=engine)
    updated = []
    for page in pages:
        result = results.get(page["page"])
        if not result:
            updated.append(page)
            continue
        from_math = result["engine"] == "nougat"
        updated.append(
            {
                **page,
                "text": result["text"] if from_math else _tidy_tesseract(result["text"]),
                "format": "markdown" if from_math else "text",
                "needs_ocr": False,
                "ocr": result["engine"],
            }
        )
    return updated, bool(results)


def extract_source_pages(
    path: str | Path,
    *,
    ocr_mode: str = "auto",
    ocr_languages: str = "eng",
    ocr_engine: str = "auto",
    ocr_approved: bool = False,
    ocr_approval_pages: int = 0,
) -> Tuple[List[Dict], bool]:
    """Return normalised page dictionaries and whether OCR supplied any text.

    Scanned PDF pages are OCR'd (``ocr_engine``: "auto" = Tesseract plus the
    equation-aware model, "math" or "tesseract"). When more than
    ``ocr_approval_pages`` pages need it and ``ocr_approved`` is false, this
    raises ``ValueError(OCR_APPROVAL_MARKER + count)`` instead of starting a
    potentially hours-long job unasked. 0 disables the approval gate.
    """
    source = Path(path)
    kind = file_type_for_path(source)
    if kind is None:
        raise ValueError(f"unsupported document type: {source.suffix or 'unknown'}")
    if kind == "pdf":
        pages = extract_pages(str(source))
        if ocr_mode == "off":
            return pages, False
        return _apply_ocr(
            source,
            pages,
            languages=ocr_languages,
            engine=ocr_engine,
            approved=ocr_approved,
            approval_pages=ocr_approval_pages,
        )
    if kind == "epub":
        return _extract_epub(source), False
    if kind == "word":
        return _extract_word(source), False
    if kind == "excel":
        return _extract_excel(source), False
    if kind == "powerpoint":
        return _extract_powerpoint(source), False
    if kind == "csv":
        return _extract_csv(source), False
    return _pages_from_text(source.read_text(encoding="utf-8", errors="replace")), False


def document_author(path: str | Path) -> str | None:
    """The author recorded in a file's own properties (PDF info, Office core
    properties), unvalidated -- ``shelves.normalize_author`` filters junk such
    as the authoring tool's name. None when absent or unreadable."""
    path = Path(path)
    kind = file_type_for_path(path)
    try:
        if kind in {"pdf", "epub"}:
            import fitz

            with fitz.open(str(path)) as document:
                return (document.metadata or {}).get("author") or None
        if kind == "word":
            from docx import Document

            return Document(str(path)).core_properties.author or None
        if kind == "powerpoint":
            from pptx import Presentation

            return Presentation(str(path)).core_properties.author or None
        if kind == "excel":
            from openpyxl import load_workbook

            workbook = load_workbook(filename=str(path), read_only=True)
            try:
                return workbook.properties.creator or None
            finally:
                workbook.close()
    except Exception:
        return None
    return None


def epub_title(path: str | Path) -> str | None:
    """The book title in an EPUB's package metadata, when present."""
    try:
        with pymupdf.open(str(path)) as book:
            return ((book.metadata or {}).get("title") or "").strip() or None
    except Exception:
        return None
