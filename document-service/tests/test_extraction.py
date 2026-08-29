from extraction import (
    _collapse_replacement_runs,
    _page_text_is_garbled,
    _rewrite_scripts,
    assess_text_layer,
    repair_extracted_text,
)

_GARBLED = (
    "Matheinatics as we kilow it has beeil created and used by huinan beiilgs "
    "niatheniaticians physicists computer scieiltists and ecoilomists al1 meinbers "
    "of the species Horno sapieils this inay be ail obvious fact but it has ail "
    "iinportant coilsequeilce for the study of the iiiiiid the coilceptual systein "
    "is systeinatic aild ilot arbitrary iii geileral"
)
_CLEAN = (
    "Mathematics as we know it has been created and used by human beings "
    "mathematicians physicists computer scientists and economists all members "
    "of the species Homo sapiens this may be an obvious fact but it has an "
    "important consequence for the study of the mind the conceptual system "
    "is systematic and not arbitrary in general and worth restating here"
)


def _span(text, font, positions, size=10):
    return {
        "font": font,
        "size": size,
        "chars": [
            {"c": character, "bbox": (x0, 0, x1, 10)}
            for character, (x0, x1) in zip(text, positions)
        ],
    }


def test_repairs_known_math_font_without_changing_regular_digits():
    layout = {
        "blocks": [
            {
                "type": 0,
                "lines": [
                    {
                        "spans": [
                            _span("v ", "Body", [(0, 4), (4, 6)]),
                            _span("1", "AdvOT410c0b94", [(6, 10)]),
                            _span(" f ", "Body", [(10, 12), (12, 16), (16, 18)]),
                            _span("5", "AdvOT410c0b94", [(18, 22)]),
                            _span(" e 12", "Body", [(22, 24), (24, 28), (28, 30), (30, 34), (34, 38)]),
                        ]
                    }
                ],
            }
        ]
    }

    assert repair_extracted_text("v 1 f 5 e 12", layout) == "v + f = e 12"


def test_collapses_runs_of_undecodable_glyphs_into_an_ellipsis():
    # Three unmapped glyphs where the series-continuation dots belong.
    assert (
        repair_extracted_text("z 2 z5 / 5! 2 ��� for z", {"blocks": []})
        == "z 2 z5 / 5! 2 ⋯ for z"
    )
    # A run collapses to an ellipsis, even when space-separated, but never
    # across a line break.
    assert _collapse_replacement_runs("a � � b") == "a ⋯ b"
    assert _collapse_replacement_runs("first line �\n� second line").startswith("first line �")
    # A lone mark between words or numbers is a dash the font could not encode.
    assert _collapse_replacement_runs("pages 132�151") == "pages 132–151"
    assert _collapse_replacement_runs("the Euler�Mayer letters") == "the Euler–Mayer letters"


def test_rewrites_superscript_and_subscript_tags():
    # every character has a Unicode form -> transliterate inline
    assert _rewrite_scripts("z<sup>3</sup> and e<sup>n+1</sup>") == "z³ and eⁿ⁺¹"
    assert _rewrite_scripts("H<sub>2</sub>O, x<sub>0</sub>") == "H₂O, x₀"
    # mixed / unsupported content -> readable plain-text fallback, never dropped
    assert _rewrite_scripts("x<sup>(a+bq)</sup>") == "x^(a+bq)"
    assert _rewrite_scripts("A<sub>Q</sub>") == "A_Q"
    # emphasis and stray tags inside the script run are stripped first
    assert _rewrite_scripts("y<sup>**2**</sup>") == "y²"
    assert _rewrite_scripts("n<sup></sup>") == "n"
    # runs through the full repair pipeline
    assert repair_extracted_text("z<sup>3</sup> / 3!", {"blocks": []}) == "z³ / 3!"


def test_repairs_dropped_dashes_in_ranges_and_names():
    assert repair_extracted_text(
        "Gazette, 8 (1915), 132�151, and 9 (1916), 303�305", {"blocks": []}
    ) == "Gazette, 8 (1915), 132–151, and 9 (1916), 303–305"
    assert repair_extracted_text(
        "Leonhard Euler (1707 1783) was Swiss", {"blocks": []}
    ) == "Leonhard Euler (1707–1783) was Swiss"


def test_repairs_misplaced_cedilla_and_shifted_acute():
    assert repair_extracted_text("the academician Franc¸ois Arago", {"blocks": []}) == (
        "the academician François Arago"
    )
    assert repair_extracted_text("the Parisian Acadeḿie des Sciences", {"blocks": []}) == (
        "the Parisian Académie des Sciences"
    )
    # Polish is left alone: acute stays on ć even though a vowel precedes it.
    assert "być" in repair_extracted_text("the word być here", {"blocks": []})


def test_garbled_page_detection_separates_ocr_mush_from_clean_prose():
    assert _page_text_is_garbled(_GARBLED) is True
    assert _page_text_is_garbled(_CLEAN) is False
    assert _page_text_is_garbled("only a handful of words here") is None


def test_assess_text_layer_rejects_a_mostly_corrupt_document():
    pages = [{"text": _CLEAN} for _ in range(9)] + [{"text": _GARBLED} for _ in range(3)]
    reason = assess_text_layer(pages)
    assert reason and "corrupted" in reason


def test_assess_text_layer_accepts_a_clean_document_with_a_stray_bad_page():
    pages = [{"text": _CLEAN} for _ in range(20)] + [{"text": _GARBLED}]
    assert assess_text_layer(pages) is None


def test_assess_text_layer_abstains_without_enough_prose():
    assert assess_text_layer([{"text": "Table 1"}, {"text": "see figure 2"}]) is None


def test_repairs_visual_word_spacing_and_reversed_accents():
    layout = {
        "blocks": [
            {
                "type": 0,
                "lines": [
                    {
                        "spans": [
                            _span(
                                "Thiscurve in g´eom´etrie",
                                "Body",
                                [
                                    (0, 4), (4, 8), (8, 12), (12, 16),
                                    (18, 22), (22, 26), (26, 30), (30, 34), (34, 38),
                                    (38, 40), (40, 44),
                                    (44, 48), (48, 52), (52, 56), (56, 60),
                                    (60, 64), (64, 68), (68, 72), (72, 76),
                                    (76, 80), (80, 84), (84, 88), (88, 92),
                                    (92, 96), (96, 100),
                                ],
                                size=10,
                            )
                        ]
                    }
                ],
            }
        ]
    }

    assert repair_extracted_text("Thiscurve in g´eom´etrie", layout) == "This curve in géométrie"


def test_repairs_misplaced_heading_accent_without_splitting_letterspaced_header():
    layout = {
        "blocks": [
            {
                "type": 0,
                "lines": [
                    {
                        "spans": [
                            _span(
                                "Rene Descartes´",
                                "RunningHeader",
                                [(i * 5, i * 5 + 3) for i in range(15)],
                                size=8.5,
                            )
                        ]
                    }
                ],
            }
        ]
    }

    assert repair_extracted_text("# Rene Descartes´", layout) == "# René Descartes"
