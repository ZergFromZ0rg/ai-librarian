"""Document types: file type first, then paper shape, then plain keywords."""
import pytest

from doc_types import TYPES, classify_document


@pytest.mark.parametrize("file_type,expected", [("powerpoint", "slides"), ("excel", "data"), ("csv", "data"),
                                                ("markdown", "writing"), ("text", "writing")])
def test_file_type_decides_before_any_text(file_type, expected):
    assert classify_document(file_type, ["Executive summary and recommendations and findings"], 3) == expected


def test_a_paper_stays_a_paper_whatever_it_mentions():
    assert classify_document("pdf", ["Agreement contract hereby"], 12, kind="paper") == "paper"


def test_keywords_in_the_opening_pages_pick_the_type():
    legal = ["INVOICE\nBill to: Acme\nAmount due: $40\nTerms and conditions apply."]
    manual = ["Installation guide\nTroubleshooting and configuration\nRevision history"]
    report = ["Annual report\nExecutive summary\nKey takeaways and recommendations"]
    assert classify_document("pdf", legal, 2) == "legal"
    assert classify_document("pdf", manual, 30) == "manual"
    assert classify_document("pdf", report, 25) == "report"


def test_weak_or_missing_evidence_is_other_and_never_raises():
    assert classify_document("pdf", ["Notes from a meeting about lunch."], 1) == "other"
    assert classify_document("pdf", [], 0) == "other"
    assert classify_document(None, [None, ""], 5) == "other"
    assert classify_document("pdf", ["agreement"], 4) == "other"  # one weak hit is not enough


def test_a_long_document_mentioning_a_contract_is_not_legal():
    assert classify_document("pdf", ["agreement contract hereby governing law"], 200) == "other"
    assert set(TYPES) >= {"paper", "report", "manual", "slides", "data", "writing", "legal", "other"}
