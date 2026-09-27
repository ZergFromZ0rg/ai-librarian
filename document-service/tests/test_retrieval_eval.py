"""Pure-logic checks for eval/harness.py (the retrieval harness).

The harness talks to a live service and is run by hand; these cover matching,
library scoping and the report's exit code with /search stubbed out.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import harness  # noqa: E402

DOCS = {
    "id": "c0ffee00c0ff",
    "name": "Documents",
    "path": "documents",
}


def test_result_matches_by_document_page_span_and_phrase():
    judged = {"document": "Deck.pptx", "page": 3, "contains": "Four to six hours"}
    assert harness.result_matches(judged, {"document": "deck.pptx", "page": 2, "page_end": 4,
                                           "text": "bulk fermentation takes four to six hours"})
    assert not harness.result_matches(judged, {"document": "deck.pptx", "page": 5, "text": "four to six hours"})
    assert not harness.result_matches(judged, {"document": "deck.pptx", "page": 3, "text": "eight hours"})
    # No page: any page of the document with the phrase counts.
    assert harness.result_matches({"document": "a.docx", "contains": "x"}, {"document": "a.docx", "page": 9, "text": "x"})


def test_collection_membership_uses_recorded_id_or_folder():
    assert harness.in_collection({"collection_id": DOCS["id"]}, DOCS)
    assert harness.in_collection({"source_path": "documents/a.docx", "collection_id": "legacy-root"}, DOCS)
    assert not harness.in_collection({"source_path": "documents-old/a.docx"}, DOCS)
    assert not harness.in_collection({"source_path": None}, DOCS)  # an upload
    assert harness.scope_leaks(
        [{"document": "A.docx"}, {"document": "newton.pdf"}, {"document": "newton.pdf"}], {"a.docx"}
    ) == ["newton.pdf"]


class _FakeScopes:
    def resolve(self, name):
        return DOCS

    def members(self, collection):
        return {"a.docx"}


def _score(monkeypatch, capsys, cases, results):
    monkeypatch.setattr(harness, "Scopes", _FakeScopes)
    calls = []

    def fake_search(query, top_k, rerank, collection_id=None, overrides=None):
        calls.append((query, collection_id, overrides))
        return results[query]

    monkeypatch.setattr(harness, "search", fake_search)
    code = harness.score(cases, rerank=True)
    return code, capsys.readouterr().out, calls


def test_score_reports_tags_and_passes_scope_and_overrides(monkeypatch, capsys):
    cases = [
        {"id": "hit", "query": "q1", "tags": ["word"], "collection": "Documents",
         "relevant": [{"document": "a.docx", "contains": "answer"}]},
        {"id": "miss", "query": "q2", "tags": ["word", "excel"],
         "relevant": [{"document": "b.xlsx"}]},
        {"id": "probe", "query": "q3", "collection": "Documents", "search": {"rerank_min_score": -100}},
    ]
    results = {"q1": [{"document": "a.docx", "page": 1, "text": "the answer"}],
               "q2": [{"document": "a.docx", "page": 1}],
               "q3": [{"document": "a.docx", "page": 1}]}
    code, out, calls = _score(monkeypatch, capsys, cases, results)
    assert code == 0
    assert calls[0][1] == DOCS["id"] and calls[1][1] is None
    assert calls[2][2] == {"rerank_min_score": -100}
    assert "excel  n=1   hit@1 0.00" in out
    assert "word   n=2   hit@1 0.50" in out
    assert "scope OK -- 1 results" in out
    assert "2/2 scoped queries stayed inside their library" in out


def test_a_scope_leak_fails_the_run(monkeypatch, capsys):
    cases = [
        {"id": "leaky", "query": "q1", "collection": "Documents",
         "relevant": [{"document": "a.docx"}]},
        {"id": "probe", "query": "q2", "collection": "Documents"},
    ]
    results = {"q1": [{"document": "a.docx", "page": 1}, {"document": "newton.pdf", "page": 1}],
               "q2": [{"document": "newton.pdf", "page": 1}]}
    code, out, _ = _score(monkeypatch, capsys, cases, results)
    assert code == 1
    assert "hit@5  first@1  SCOPE LEAK outside 'Documents': newton.pdf" in out
    assert "2 LEAKED" in out
