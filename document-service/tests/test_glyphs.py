import numpy as np
import pymupdf
import pytest

import glyphs


def _render_symbol(symbol: str, size: int = 52) -> np.ndarray:
    font = pymupdf.Font(fontfile=str(glyphs.REFERENCE_FONT))
    document = pymupdf.open()
    page = document.new_page(width=84, height=84)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((12, 60), symbol, font=font, fontsize=size)
    writer.write_text(page)
    mask = glyphs._binarise(page.get_pixmap(colorspace=pymupdf.csGRAY))
    document.close()
    return mask


def test_only_subset_or_symbol_fonts_are_candidates():
    assert glyphs._is_recovery_candidate("ABCDEF+AdvOT410c0b94")
    assert glyphs._is_recovery_candidate("POYIRP+CMEX10")
    assert glyphs._is_recovery_candidate("MSBM10")
    assert not glyphs._is_recovery_candidate("Helvetica-Bold")
    assert not glyphs._is_recovery_candidate("Times-Roman")
    assert not glyphs._is_recovery_candidate("ArialMT")


def test_suspicious_matches_only_non_prose_characters():
    for good in "0 9 # $ ~ = / \\ | \x01 \x1a".split(" "):
        assert glyphs._SUSPICIOUS.fullmatch(good or "\x00")
    for prose in "a b z e m".split(" "):
        assert not glyphs._SUSPICIOUS.fullmatch(prose)


def test_reference_bitmaps_cover_the_core_symbols():
    references = glyphs._reference_bitmaps()
    assert len(references) > 80
    for symbol in "+−=×÷±∑∫∂∈λπ":
        assert symbol in references


@pytest.mark.parametrize("symbol", ["−", "+", "=", "×", "÷", "±", "∑", "∫", "∂", "∈", "λ", "π"])
def test_classifier_round_trips_known_symbols(symbol):
    assert glyphs._classify(_render_symbol(symbol)) == symbol


def test_classifier_rejects_noise_and_plain_letters():
    assert glyphs._classify(np.zeros((40, 40), dtype=bool)) is None
    letter = pymupdf.Font("helv")
    document = pymupdf.open()
    page = document.new_page(width=84, height=84)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((12, 60), "g", font=letter, fontsize=52)
    writer.write_text(page)
    mask = glyphs._binarise(page.get_pixmap(colorspace=pymupdf.csGRAY))
    document.close()
    assert glyphs._classify(mask) is None


def test_normalise_centres_and_scales_to_the_raster_box():
    mask = np.zeros((10, 4), dtype=bool)
    mask[2:8, 1:3] = True
    out = glyphs._normalise(mask)
    assert out.shape == (glyphs.RASTER, glyphs.RASTER)
    assert out.max() == 1.0
    ys, xs = np.where(out > 0.5)
    assert abs(ys.mean() - glyphs.RASTER / 2) < glyphs.RASTER / 4
    assert abs(xs.mean() - glyphs.RASTER / 2) < glyphs.RASTER / 4
