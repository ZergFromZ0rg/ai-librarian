"""Pure-logic checks for eval/ask_harness.py (the answer-quality harness).

The harness itself talks to a live service and is run by hand; these cover the
scoring functions so a regression in the checks is caught by CI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import ask_harness as ah  # noqa: E402


def test_parse_sse_and_ask_shape():
    body = (
        'data: {"type": "token", "text": "The absurd "}\n\n'
        'data: {"type": "token", "text": "is a confrontation [1]."}\n\n'
        'data: {"type": "sources", "results": [{"document": "x.pdf", "page": 3}], '
        '"documents": 1, "relevant_count": 4, "model": "ollama:qwen2.5:7b"}\n\n'
    )
    events = ah.parse_sse(body)
    assert [e["type"] for e in events] == ["token", "token", "sources"]


def test_cited_numbers():
    assert ah.cited_numbers("grounded [1] and also [2][3], plus [12]") == {1, 2, 3, 12}
    assert ah.cited_numbers("not a cite [12x] here") == set()
    assert ah.cited_numbers("no citations here") == set()


def test_looks_like_refusal():
    assert ah.looks_like_refusal("I couldn't find anything in your library about that.")
    assert ah.looks_like_refusal("The sources do not contain an answer to this.")
    assert not ah.looks_like_refusal("Camus argues the absurd is a confrontation [1].")


def test_source_matches_by_document_and_page_span():
    src = {"document": "A History of Mathematics.pdf", "page": 120, "page_end": 122}
    assert ah.source_matches({"document": "a history of mathematics.pdf", "page": 121}, src)
    assert not ah.source_matches({"document": "other.pdf", "page": 121}, src)
    assert not ah.source_matches({"document": "A History of Mathematics.pdf", "page": 200}, src)


def _result(answer, sources, error=None):
    return {
        "answer": answer,
        "sources": sources,
        "documents": len({s.get("document") for s in sources}),
        "relevant_count": len(sources),
        "model": "test",
        "error": error,
    }


def test_check_passes_a_good_grounded_answer():
    case = {
        "question": "What is a meme?",
        "must_mention": ["imitation"],
        "must_cite": [{"document": "meme.pdf", "page": 5}],
    }
    result = _result(
        "A meme is culture copied by imitation [1][2].",
        [{"document": "meme.pdf", "page": 5}, {"document": "meme.pdf", "page": 9}],
    )
    m = ah.check(case, result)
    assert m["hard_failures"] == []
    assert m["must_cite_retrieved"] and m["must_cite_cited"]
    assert m["cited_fraction"] == 1.0


def test_check_flags_missing_citation_and_mention_and_out_of_range():
    case = {"question": "q", "must_mention": ["Cantor"]}
    result = _result("Some ungrounded claim with a bogus marker [7].",
                     [{"document": "x.pdf", "page": 1}])
    fails = ah.check(case, result)["hard_failures"]
    assert any("must_mention" in f for f in fails)
    assert any("out of range" in f for f in fails)


def test_check_refusal_expectations():
    ok = ah.check({"question": "q", "expect_refusal": True},
                  _result("I couldn't find anything in your library about that.", []))
    assert ok["hard_failures"] == []

    bad = ah.check({"question": "q", "expect_refusal": True},
                   _result("Actually the answer is 42 [1].", [{"document": "x.pdf", "page": 1}]))
    assert any("expected a refusal" in f for f in bad["hard_failures"])

    leaked = ah.check({"question": "q"},  # substantive expected
                      _result("The sources do not contain this.", [{"document": "x.pdf", "page": 1}]))
    assert any("unexpected refusal" in f for f in leaked["hard_failures"])


def test_check_must_cite_is_any_of():
    case = {"question": "q", "must_cite": [
        {"document": "a.pdf", "page": 5}, {"document": "b.pdf", "page": 9}]}
    # only the second acceptable passage was retrieved -> still OK
    result = _result("Answer [1].", [{"document": "b.pdf", "page": 9}])
    m = ah.check(case, result)
    assert m["hard_failures"] == []
    assert m["must_cite_retrieved"] and m["must_cite_cited"]
