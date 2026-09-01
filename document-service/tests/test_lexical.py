import lexical
from lexical import bm25_document_vector, bm25_query_vector, lexical_terms


def test_lexical_terms_preserve_formula_symbols():
    terms = dict(lexical_terms("For ∂f/∂x ≥ 0, x² is valid."))

    assert terms["symbol:∂"] == 2.0
    assert terms["symbol:≥"] == 2.0
    assert "term:x2" in terms
    assert "formula:∂f/∂x" in terms


def test_bm25_vectors_are_stable_and_sorted():
    first = bm25_document_vector("x + y = z")
    assert first == bm25_document_vector("x + y = z")
    assert first[0] == sorted(first[0])
    assert bm25_query_vector("x + y = z")[0] == sorted(bm25_query_vector("x + y = z")[0])


def test_bm25_document_frequency_saturates():
    once = dict(zip(*bm25_document_vector("Galois")))
    many = dict(zip(*bm25_document_vector("Galois " * 20)))
    key = next(iter(once))
    # 20x the term frequency is worth well under 20x the score (BM25 saturation).
    assert many[key] > once[key]
    assert many[key] < once[key] * 4


def test_bm25_length_normalisation_penalises_long_passages(monkeypatch):
    monkeypatch.setattr(lexical, "BM25_AVGDL", 3.0)
    short = dict(zip(*bm25_document_vector("Galois theory solvability")))
    padded = dict(zip(*bm25_document_vector("Galois theory solvability " + "filler " * 50)))
    key = next(k for k in short if k in padded)
    assert padded[key] < short[key]


def test_bm25_query_vector_keeps_symbol_weighting():
    values = dict(zip(*bm25_query_vector("∂f/∂x")))
    # the bare symbol term carries its 2.0 weight, unlike a plain word
    assert max(values.values()) >= 2.0
