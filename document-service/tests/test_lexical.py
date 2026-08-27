from lexical import lexical_terms, sparse_vector


def test_lexical_terms_preserve_formula_symbols():
    terms = dict(lexical_terms("For ∂f/∂x ≥ 0, x² is valid."))

    assert terms["symbol:∂"] == 2.0
    assert terms["symbol:≥"] == 2.0
    assert "term:x2" in terms
    assert "formula:∂f/∂x" in terms


def test_sparse_vector_is_stable_and_normalized():
    first = sparse_vector("x + y = z")
    second = sparse_vector("x + y = z")

    assert first == second
    assert first[0] == sorted(first[0])
    assert abs(sum(value * value for value in first[1]) - 1.0) < 1e-9
