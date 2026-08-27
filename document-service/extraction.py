from typing import Dict, List

import pymupdf
import pymupdf4llm


def extract_pages(pdf_path: str) -> List[Dict]:
    """Extract layout-aware Markdown per page from a text-based PDF.

    OCR is intentionally disabled: scanned pages remain explicit ingestion errors
    instead of silently producing low-quality mathematical or symbolic text.
    """
    with pymupdf.open(str(pdf_path)) as document:
        if document.needs_pass:
            raise ValueError("password-protected PDFs are not supported")
        extracted = pymupdf4llm.to_markdown(
            document,
            page_chunks=True,
            use_ocr=False,
            show_progress=False,
        )

    pages = []
    for index, page in enumerate(extracted):
        metadata = page.get("metadata") or {}
        pages.append(
            {
                "page": int(metadata.get("page_number") or index + 1),
                "text": (page.get("text") or "").strip(),
                "format": "markdown",
            }
        )
    return pages
