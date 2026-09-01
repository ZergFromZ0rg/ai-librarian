import math_ocr


def test_degenerate_output_is_rejected():
    assert math_ocr._is_degenerate("")
    assert math_ocr._is_degenerate("  \n ")
    assert math_ocr._is_degenerate("x = 1 " * 20)          # a repetition loop
    assert math_ocr._is_degenerate("aaaaaaaa" * 10)        # near-zero diversity
    assert not math_ocr._is_degenerate(
        r"The Cauchy--Schwarz inequality: $|\langle x, y\rangle| \le \|x\|\,\|y\|$."
    )


def test_transcribe_page_is_a_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr(math_ocr, "ENABLED", False)
    assert math_ocr.transcribe_page(object()) is None
