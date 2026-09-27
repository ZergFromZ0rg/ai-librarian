"""OCR engine combination and Nougat output cleanup (no real engines run)."""

import ocr


def test_nougat_page_wins_unless_it_lost_words(monkeypatch):
    monkeypatch.setattr(
        ocr,
        "_tesseract_pages",
        lambda *_a: {1: "Let x equal two thirds of y here", 2: "A long page of ordinary prose with many words in it"},
    )
    monkeypatch.setattr(
        ocr,
        "_nougat_pages",
        lambda *_a: {1: "Let \\(x=\\frac{2}{3}y\\) equal two thirds of y here", 2: "A long"},
    )
    result = ocr.ocr_pages("doc.pdf", [1, 2, 3])
    assert result[1]["engine"] == "nougat"  # same words, plus real math
    assert result[2]["engine"] == "tesseract"  # nougat skipped most of the page
    assert 3 not in result  # nothing could read it


def test_math_only_engine_falls_back_to_text_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "_nougat_pages", lambda *_a: {})
    monkeypatch.setattr(ocr, "_tesseract_pages", lambda *_a: {1: "plain words"})
    assert ocr.ocr_pages("doc.pdf", [1], engine="math") == {1: {"text": "plain words", "engine": "tesseract"}}


def test_word_count_ignores_latex_commands():
    assert ocr.word_count("\\frac{a}{b} \\alpha is small") == 2


def test_clean_nougat_markdown():
    raw = (
        "Intro text \\[a^2+b^2=c^2\\] after.\n\n"
        "\\begin{table}\n\\begin{tabular}{c c}\n\\hline Name & Value \\\\ \\hline x & 1 \\\\ \n\\end{tabular}\n\\end{table}\n\n"
        "[MISSING_PAGE_EMPTY:3]\n"
        "loop line again\nloop line again\nloop line again\nloop line again\ntrailing garbage"
    )
    cleaned = ocr.clean_nougat_markdown(raw)
    assert "\n\n\\[a^2+b^2=c^2\\]\n\n" in cleaned
    assert "| Name | Value |" in cleaned and "| x | 1 |" in cleaned
    assert "MISSING_PAGE" not in cleaned and "\\begin{table}" not in cleaned
    assert cleaned.count("loop line again") == 1 and "trailing garbage" not in cleaned
    assert "\n\n\n" not in cleaned


def test_nougat_subprocess_failure_is_contained(monkeypatch):
    import subprocess

    monkeypatch.setattr(ocr, "math_engine_available", lambda: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="CUDA out of memory"),
    )
    assert ocr._nougat_pages("doc.pdf", [1]) == {}
