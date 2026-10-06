"""What kind of working document something is: a paper, report, manual, deck,
dataset, piece of writing, or legal/financial paper.

``doc_kind`` only separates books from papers from "everything else". The
Documents library needs a finer split to file things sensibly, so this module
reads the file type and the opening pages and picks one of ``TYPES``. It is a
guess from plain keywords (no model), the reader can override it, and it only
ever looks at text already extracted during indexing.
"""

import re
from typing import Iterable, Optional

TYPES = ("paper", "report", "manual", "slides", "data", "writing", "legal", "other")

LABELS = {
    "paper": "Paper",
    "report": "Report",
    "manual": "Manual & spec",
    "slides": "Slides",
    "data": "Data",
    "writing": "Writing",
    "legal": "Legal & finance",
    "other": "Other",
}

_SIGNALS = {
    "legal": re.compile(
        r"\b(?:agreement|contract|hereby|whereas|terms and conditions|terms of service|invoice|"
        r"receipt|purchase order|statement of account|tax return|insurance policy|"
        r"non-disclosure|indemnif\w+|governing law|bill to|amount due|total due)\b", re.I),
    "manual": re.compile(
        r"\b(?:user(?:'s)? guide|user manual|installation guide|owner'?s manual|datasheet|data sheet|"
        r"specification|reference manual|getting started|quick start|troubleshooting|"
        r"configuration|api reference|release notes|revision history|part number|warranty)\b", re.I),
    "report": re.compile(
        r"\b(?:executive summary|annual report|white ?paper|technical report|case study|"
        r"findings|recommendations|quarterly|market analysis|status report|project report|"
        r"key takeaways|methodology|survey results|prepared by|prepared for)\b", re.I),
}
_THRESHOLD = {"legal": 2, "manual": 2, "report": 2}


def classify_document(
    file_type: Optional[str],
    page_texts: Iterable[str],
    page_count: int = 0,
    kind: Optional[str] = None,
) -> str:
    """One of ``TYPES``. `kind` is the book/paper/document guess (or the
    reader's correction) from ``doc_kind``."""
    if file_type == "powerpoint":
        return "slides"
    if file_type in {"excel", "csv"}:
        return "data"
    if kind == "paper":
        return "paper"
    if file_type in {"markdown", "text"}:
        return "writing"

    opening = "\n".join(text or "" for text in list(page_texts)[:3])
    scores = {name: len(pattern.findall(opening)) for name, pattern in _SIGNALS.items()}
    # A form or invoice is short; a long document that merely mentions a
    # contract is not one, so legal signals count for less as length grows.
    if page_count > 40:
        scores["legal"] = max(0, scores["legal"] - 3)
    best = max(scores, key=lambda name: scores[name])
    if scores[best] >= _THRESHOLD[best]:
        return best
    return "other"
