from extraction import (
    _collapse_replacement_runs,
    _page_is_boilerplate,
    _page_text_is_garbled,
    _rewrite_scripts,
    assess_text_layer,
    boilerplate_page_indices,
    garbled_page_indices,
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


def test_recovers_a_glyph_that_to_markdown_replaced_with_fffd():
    # PyMuPDF4LLM emits U+FFFD for a glyph it cannot decode; the raw layer still
    # carries the real (mis-encoded) character and its font, so a glyph map can
    # recover it once the diff is realigned across the replacement.
    layout = {
        "blocks": [
            {
                "type": 0,
                "lines": [
                    {
                        "spans": [
                            _span(
                                "x2 \x04 q",
                                "AmbigSym",
                                [(0, 4), (4, 8), (8, 10), (10, 16), (16, 18), (18, 22)],
                            )
                        ]
                    }
                ],
            }
        ]
    }

    out = repair_extracted_text("x2 � q", layout, {"AmbigSym": {"\x04": "≡"}})
    assert out == "x2 ≡ q"


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


def test_garbled_page_detection_is_not_fooled_by_a_roman_numeral_index():
    # A table of contents: every entry ends in a Roman-numeral page reference,
    # several of which contain "ii" / "iii". This must not read as OCR garbage.
    numerals = ["xii", "xiii", "xvii", "xviii", "viii", "iii"]
    toc = " ".join(
        f"Chapter {n} the subject of this particular section opens on page {numerals[n % len(numerals)]}"
        for n in range(30)
    )
    assert _page_text_is_garbled(toc) is False


def test_assess_text_layer_rejects_a_mostly_corrupt_document():
    pages = [{"text": _CLEAN} for _ in range(3)] + [{"text": _GARBLED} for _ in range(9)]
    reason = assess_text_layer(pages)
    assert reason and "corrupted" in reason


def test_assess_text_layer_tolerates_a_minority_of_corrupt_pages():
    # A quarter of the pages are garbage: below the catastrophic threshold, so
    # the document is not refused — the caller drops those pages instead.
    pages = [{"text": _CLEAN} for _ in range(9)] + [{"text": _GARBLED} for _ in range(3)]
    assert assess_text_layer(pages) is None


def test_garbled_page_indices_lists_only_the_corrupt_pages():
    pages = [
        {"text": _CLEAN},
        {"text": _GARBLED},
        {"text": "too little prose to judge"},
        {"text": _GARBLED},
    ]
    assert garbled_page_indices(pages) == [1, 3]


def test_assess_text_layer_accepts_a_clean_document_with_a_stray_bad_page():
    pages = [{"text": _CLEAN} for _ in range(20)] + [{"text": _GARBLED}]
    assert assess_text_layer(pages) is None
    assert garbled_page_indices(pages) == [20]


def test_assess_text_layer_abstains_without_enough_prose():
    assert assess_text_layer([{"text": "Table 1"}, {"text": "see figure 2"}]) is None


_TOC = "\n".join(
    ["# Contents"]
    + [
        f"{title} {'.' * 8} {page}"
        for title, page in [
            ("Introduction", 1), ("Wormholes as Gateways", 89), ("Exotic Matter", 95),
            ("Constructing Wormholes", 102), ("The Space Mirror", 107),
            ("Schwarzschild Surgery", 111), ("The World Turned Inside Out", 115),
            ("A Tangled Web", 118), ("The Black Hole Mining Expedition", 121),
            ("The Future Was Yesterday", 139),
        ]
    ]
)
_INDEX = "\n".join(
    [
        "Index",
        "Wormholes, 2-3, 92-96, 105-106, 109",
        "throat of, 93-94, 96-97, 105, 108",
        "stability, 106",
        "exotic matter, 97-109, 112, 199, 200",
        "white holes, 88, 91, 95-98, 101",
        "Einstein-Rosen bridges, 70, 83-84",
        "time travel, 153-158, 173, 187-199",
        "Thorne, Kip, 15, 104-105, 147, 211",
        "Sagan, Carl, 12, 15, 105",
    ]
)
_BIBLIOGRAPHY = "\n".join(
    ["Related Reading"]
    + [
        f"{author}. {title}. {journal} ({year}): {page}."
        for author, title, journal, year, page in [
            ("Visser, Matt", "Traversable Wormholes", "Nuclear Physics B328", 1989, 203),
            ("Morris, Michael S., and Kip S. Thorne", "Wormholes in Spacetime", "Am. J. Physics 56", 1988, 395),
            ("Hawking, Stephen", "Black Holes Are White Hot", "Nature", 1977, 30),
            ("Thorne, Kip S", "Black Holes and Time Warps", "Norton", 1994, 1),
            ("Novikov, Igor", "Black Holes and the Universe", "Cambridge", 1990, 1),
            ("Penrose, Roger", "Gravitational Collapse", "Rivista del Nuovo Cimento", 1969, 252),
            ("Wheeler, John A", "Geons and Quantum Foam", "Princeton", 1998, 1),
            ("Everett, Hugh", "Relative State Formulation", "Rev. Modern Physics", 1957, 454),
        ]
    ]
)
_PROSE_WITH_DATES = (
    "Euler was born in 1707 and died in 1783. Gauss lived from 1777 to 1855. "
    "The calculus priority dispute ran from about 1699 to 1712, poisoning relations "
    "between the Royal Society and the Continental mathematicians for a generation. "
    "Newton, born in 1642, developed his method of fluxions in 1666 but did not "
    "publish it until 1704. Leibniz, born in 1646, published his differential "
    "calculus in 1684 in the Acta Eruditorum. The real question was never who was "
    "first but whether Leibniz reached the ideas independently, which he did."
)


def test_boilerplate_detection_flags_front_and_back_matter():
    assert _page_is_boilerplate(_TOC) is True
    assert _page_is_boilerplate(_INDEX) is True
    assert _page_is_boilerplate(_BIBLIOGRAPHY) is True


def test_boilerplate_detection_leaves_prose_alone():
    assert _page_is_boilerplate(_CLEAN) is False
    # A history passage thick with years and dates must not read as an index.
    assert _page_is_boilerplate(_PROSE_WITH_DATES) is False
    # Too little text to judge.
    assert _page_is_boilerplate("Contents\nChapter 1 .... 5") is False


def test_boilerplate_page_indices_lists_only_the_boilerplate_pages():
    pages = [
        {"text": _TOC},
        {"text": _CLEAN},
        {"text": _PROSE_WITH_DATES},
        {"text": _INDEX},
    ]
    assert boilerplate_page_indices(pages) == [0, 3]


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
