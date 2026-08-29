from layout import (
    _looks_like_math,
    _looks_like_prose,
    _rows_in_order,
    _signature,
    page_lines_in_reading_order,
    recover_dropped_text,
)


def line(text, x0, y0, x1=None, y1=None):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1 if x1 is not None else x0 + 50, "y1": y1 if y1 is not None else y0 + 10}


class FakePage:
    """Minimal stand-in exposing get_text('dict') the way PyMuPDF does."""

    def __init__(self, lines):
        self._lines = lines

    def get_text(self, kind, **_kwargs):
        assert kind == "dict"
        blocks = [
            {
                "type": 0,
                "lines": [
                    {"bbox": (ln["x0"], ln["y0"], ln["x1"], ln["y1"]), "spans": [{"text": ln["text"]}]}
                ],
            }
            for ln in self._lines
        ]
        return {"blocks": blocks}


def test_signature_ignores_markup_hyphenation_and_ligatures():
    assert _signature("the **de-** \nfined ﬁeld") == _signature("the defined field")
    assert _signature("z<sup>3</sup> / 3!") == "z3/3!"
    # superscript digits fold to plain digits (NFKC), real symbols are kept
    assert _signature("R<sup>d</sup>") == _signature("Rd")


def test_rows_in_order_stacks_labels_and_defers_equation_numbers():
    run = [
        line("maximize", 120, 118),
        line("φ⊤A φ,", 140, 116),
        line("subject to", 120, 138),
        line("φ⊤B φ = 1,", 140, 137),
        line("(14)", 300, 126),  # right-aligned, its own visual row
    ]
    ordered = [item["text"] for item in _rows_in_order(run)]
    assert ordered == ["maximize", "φ⊤A φ,", "subject to", "φ⊤B φ = 1,", "(14)"]


def test_math_and_prose_classifiers():
    assert _looks_like_math("L = φ⊤A φ − λ(φ⊤B φ − 1),")
    assert _looks_like_math("∂L/∂φ = 2Aφ − 2λBφ")
    assert not _looks_like_math("The Lagrangian for this problem is given below.")
    assert _looks_like_prose("As the Eq. (15) is a maximization problem, the eigenvectors are sorted by size.")
    assert not _looks_like_prose("x = y + z")


def test_two_column_reading_order_with_spanner():
    page = FakePage([
        line("Title Across The Whole Page", 40, 40, 560, 52),
        line("left column first line", 45, 70, 280, 82),
        line("right column first line", 320, 70, 560, 82),
        line("left column second line", 45, 92, 280, 104),
        line("right column second line", 320, 92, 560, 104),
        line("left column third line", 45, 114, 280, 126),
        line("right column third line", 320, 114, 560, 126),
    ])
    order = [ln["text"] for ln in page_lines_in_reading_order(page)]
    assert order == [
        "Title Across The Whole Page",
        "left column first line",
        "left column second line",
        "left column third line",
        "right column first line",
        "right column second line",
        "right column third line",
    ]


def test_recover_splices_dropped_equation_after_its_anchor():
    page = FakePage([
        line("The Lagrangian for Eq. (14) is:", 55, 100, 280, 112),
        line("L = φ⊤A φ − λ(φ⊤B φ − 1),", 90, 130, 250, 142),
        line("where λ is the Lagrange multiplier.", 55, 160, 280, 172),
    ])
    markdown = "The Lagrangian for Eq. (14) is: \n\n\n\nwhere λ is the Lagrange multiplier."
    recovered = recover_dropped_text(page, markdown)
    assert "$$ L = φ⊤A φ − λ(φ⊤B φ − 1), $$" in recovered
    assert recovered.index("Lagrangian for Eq") < recovered.index("$$ L =") < recovered.index("where λ")
    # nothing that was already present gets duplicated
    assert recovered.count("where λ is the Lagrange multiplier") == 1


def test_recover_skips_broken_font_equation_fragments():
    # "sin x = e^ix" mangled by a symbol font into a bare fragment: no relation
    # symbol survives, so it is noise, not a recoverable equation.
    page = FakePage([
        line("From the infinite series it was a short step to the identities", 55, 100, 280, 112),
        line("sin x 5 e", 120, 128, 175, 140),
        line("cos x 5 e", 120, 150, 175, 162),
        line("relationships known to Cotes and De Moivre became familiar tools.", 55, 178, 280, 190),
    ])
    markdown = (
        "From the infinite series it was a short step to the identities \n\n\n\n"
        "relationships known to Cotes and De Moivre became familiar tools."
    )
    assert recover_dropped_text(page, markdown) == markdown


def test_recover_keeps_an_equation_that_has_a_relation_symbol():
    page = FakePage([
        line("we obtain the result:", 55, 100, 200, 112),
        line("p(K < .5) = .649", 120, 128, 220, 140),
        line("which settles the question.", 55, 160, 280, 172),
    ])
    markdown = "we obtain the result: \n\n\n\nwhich settles the question."
    assert "p(K < .5) = .649" in recover_dropped_text(page, markdown)


def test_recover_skips_a_run_with_no_findable_anchor():
    page = FakePage([
        line("orphan fragment α = β", 90, 40, 250, 52),
        line("completely unrelated present text here", 55, 80, 280, 92),
    ])
    markdown = "completely unrelated present text here"
    assert recover_dropped_text(page, markdown) == markdown


def test_recover_is_a_no_op_when_nothing_was_dropped():
    page = FakePage([
        line("first sentence of the paragraph", 55, 40, 280, 52),
        line("second sentence of the paragraph", 55, 62, 280, 74),
    ])
    markdown = "first sentence of the paragraph second sentence of the paragraph"
    assert recover_dropped_text(page, markdown) == markdown
