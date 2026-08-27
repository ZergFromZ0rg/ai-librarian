import fitz
from typing import List, Dict


def extract_pages(pdf_path: str) -> List[Dict]:
    """Extract text per page from a PDF using PyMuPDF.

    Returns a list of dicts: {"page": int, "text": str}
    """
    pages = []
    with fitz.open(str(pdf_path)) as document:
        if document.needs_pass:
            raise ValueError("password-protected PDFs are not supported")
        for i, page in enumerate(document):
            text = page.get_text("text", sort=True)
            pages.append({"page": i + 1, "text": text})
    return pages
