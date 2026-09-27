"""Non-PDF source extraction and targeted OCR routing."""

import pytest
from conftest import make_pdf

import parsers


def test_supported_types_and_unknowns():
    assert parsers.file_type_for_path("Deck.PPTX") == "powerpoint"
    assert parsers.is_supported_path("notes.md")
    assert not parsers.is_supported_path("archive.zip")
    with pytest.raises(ValueError):
        parsers.extract_source_pages("archive.zip")


def test_plain_text_is_split_into_bounded_pseudo_pages(tmp_path):
    source = tmp_path / "long.txt"
    source.write_text("\n\n".join(f"Paragraph {i} " + "word " * 400 for i in range(20)))
    pages, used_ocr = parsers.extract_source_pages(source)
    assert not used_ocr
    assert len(pages) > 1
    assert [page["page"] for page in pages] == list(range(1, len(pages) + 1))
    assert all(len(page["text"]) <= 12_000 + 2_100 for page in pages)


def test_csv_rows_become_labelled_records(tmp_path):
    source = tmp_path / "table.csv"
    source.write_text("name,note\nCamus,absurd | free\nSartre\n")
    (page,), _ = parsers.extract_source_pages(source)
    assert page["text"].split("\n\n") == [
        "name: Camus; note: absurd | free",
        "name: Sartre",  # a ragged row just has fewer fields
    ]


def test_word_headings_paragraphs_and_tables(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("The Myth", level=1)
    document.add_paragraph("One must imagine Sisyphus happy.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Author", "Work"
    table.cell(1, 0).text, table.cell(1, 1).text = "Camus", "The Stranger"
    path = tmp_path / "essay.docx"
    document.save(path)

    (page,), _ = parsers.extract_source_pages(path)
    assert "# The Myth" in page["text"]
    assert "Sisyphus happy" in page["text"]
    assert "| Camus | The Stranger |" in page["text"]


def test_excel_sheet_per_page(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    workbook.active.title = "Reading"
    workbook.active.append(["Title", "Pages"])
    workbook.active.append(["The Plague", 308])
    workbook.create_sheet("Empty")
    path = tmp_path / "list.xlsx"
    workbook.save(path)

    pages, _ = parsers.extract_source_pages(path)
    assert len(pages) == 1  # the empty sheet contributes nothing
    assert pages[0]["text"] == "## Reading\n\nTitle: The Plague; Pages: 308"


def test_excel_rows_are_records_with_clean_dates_and_titles(tmp_path):
    import datetime

    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Log"
    sheet.append(["Q3 carrier log"])  # a title row above the header
    sheet.append(["Date", "Owner", None, "Amount"])
    sheet.append([datetime.datetime(2026, 2, 3), "Chidi", "late twice", 406.29])
    sheet.append([datetime.datetime(2026, 2, 4, 14, 30), None, "on time", None])
    workbook.create_sheet("Blank").append(["Header only"])
    path = tmp_path / "log.xlsx"
    workbook.save(path)

    log, blank = parsers.extract_source_pages(path)[0]
    assert log["text"].split("\n\n") == [
        "## Log",
        "Q3 carrier log",
        "Date: 2026-02-03; Owner: Chidi; Column 3: late twice; Amount: 406.29",
        "Date: 2026-02-04 14:30; Column 3: on time",
    ]
    assert blank["text"] == "## Blank\n\nHeader only"


def test_powerpoint_slide_per_page(tmp_path):
    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Absurdism"
    slide.placeholders[1].text = "Revolt, freedom, passion"
    path = tmp_path / "talk.pptx"
    presentation.save(path)

    (page,), _ = parsers.extract_source_pages(path)
    assert page["text"].startswith("# Absurdism")  # the slide title, once
    assert page["text"].count("Absurdism") == 1
    assert "Revolt, freedom, passion" in page["text"]


def test_powerpoint_speaker_notes_are_indexed(tmp_path):
    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Absurdism"
    slide.placeholders[1].text = "Revolt"
    slide.notes_slide.notes_text_frame.text = "Mention the rock and the hill here."
    bare = presentation.slides.add_slide(presentation.slide_layouts[5])  # title only
    bare.shapes.title.text = "Q&A"
    bare.notes_slide.notes_text_frame.text = "Leave ten minutes for questions."
    path = tmp_path / "talk.pptx"
    presentation.save(path)

    pages, _ = parsers.extract_source_pages(path)
    assert [page["page"] for page in pages] == [1, 2]
    assert pages[0]["text"].endswith("Speaker notes: Mention the rock and the hill here.")
    assert "Leave ten minutes for questions." in pages[1]["text"]  # notes alone keep a slide


def _mixed_pages():
    return [
        {"page": 1, "text": "born digital text", "format": "markdown", "needs_ocr": False},
        {"page": 2, "text": "", "format": "markdown", "needs_ocr": True},
        {"page": 3, "text": "", "format": "markdown", "needs_ocr": True},
    ]


def test_pdf_ocr_runs_only_for_pages_that_need_it(tmp_path, monkeypatch):
    path = tmp_path / "mixed.pdf"
    path.write_bytes(make_pdf("born digital text"))
    calls = []
    monkeypatch.setattr(parsers, "extract_pages", lambda _path: _mixed_pages())

    def fake_ocr(_path, targets, languages, engine):
        calls.append((targets, languages, engine))
        return {
            2: {"text": "\\[E = mc^2\\]", "engine": "nougat"},
            3: {"text": "hyphen-\nated words", "engine": "tesseract"},
        }

    monkeypatch.setattr(parsers, "ocr_pages", fake_ocr)
    pages, used = parsers.extract_source_pages(path, ocr_languages="eng+fra", ocr_engine="auto")
    assert used and calls == [([2, 3], "eng+fra", "auto")]
    assert pages[0]["text"] == "born digital text"
    assert pages[1]["format"] == "markdown" and pages[1]["ocr"] == "nougat"
    assert pages[2]["text"] == "hyphenated words" and pages[2]["format"] == "text"
    assert not any(page["needs_ocr"] for page in pages)

    calls.clear()
    _pages, used = parsers.extract_source_pages(path, ocr_mode="off")
    assert not used and calls == []


def test_large_scans_wait_for_approval(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.write_bytes(make_pdf("irrelevant"))
    monkeypatch.setattr(parsers, "extract_pages", lambda _path: _mixed_pages())
    monkeypatch.setattr(parsers, "ocr_pages", lambda *a, **k: {})

    with pytest.raises(ValueError) as refused:
        parsers.extract_source_pages(path, ocr_approval_pages=1)
    assert str(refused.value) == f"{parsers.OCR_APPROVAL_MARKER}2"
    # Approved, or within the limit, or with the gate off: it just runs.
    parsers.extract_source_pages(path, ocr_approval_pages=1, ocr_approved=True)
    parsers.extract_source_pages(path, ocr_approval_pages=2)
    parsers.extract_source_pages(path, ocr_approval_pages=0)


def test_location_labels_follow_the_file_type():
    assert parsers.location_label("pdf", 3) == "p. 3"
    assert parsers.location_label("pdf", 3, 4) == "pp. 3–4"
    assert parsers.location_label("powerpoint", 2) == "slide 2"
    assert parsers.location_label("excel", 1, 2) == "sheets 1–2"
    assert parsers.location_label("word", 5) == "section 5"
    assert parsers.location_label("csv", 1) == "table"
    assert parsers.location_label("pdf", None) == "location unknown"
