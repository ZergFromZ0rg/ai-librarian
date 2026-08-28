from extraction import repair_extracted_text


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
