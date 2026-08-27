import fitz
from typing import List, Dict


def extract_pages(pdf_path: str) -> List[Dict]:
    """Extract text per page from a PDF using PyMuPDF.

    Returns a list of dicts: {"page": int, "text": str}
    """
    pages = []
    doc = fitz.open(str(pdf_path))
    for i, page in enumerate(doc):
        text = page.get_text()
        pages.append({"page": i + 1, "text": text})
    return pages
