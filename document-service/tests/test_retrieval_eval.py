"""Pure-logic checks for eval/harness.py (the retrieval harness).

The harness talks to a live service and is run by hand; these cover matching,
library scoping and the report's exit code with /search stubbed out.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

import evalstats  # noqa: E402
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
    notes = {"id": "notes", "name": "Notes", "path": None, "builtin": True}
    assert harness.in_collection({"collection_id": "notes"}, notes)
    assert not harness.in_collection({"source_path": "documents/a.docx"}, notes)
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
    excel = next(line for line in out.splitlines() if line.strip().startswith("excel"))
    word = next(line for line in out.splitlines() if line.strip().startswith("word"))
    assert "n=1" in excel and "hit@1 0.00" in excel
    assert "n=2" in word and "hit@1 0.50" in word
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


def test_matching_ignores_line_wraps_in_the_returned_text():
    judged = {"document": "a.pdf", "contains": "4,310 pallets"}
    assert harness.result_matches(judged, {"document": "a.pdf", "text": "handles 4,310\n  pallets per week"})


def test_wilson_interval_is_honest_about_small_sets():
    low, high = evalstats.wilson(39, 39)
    assert 0.90 < low < 0.92 and high == 1.0       # 39/39 still only supports "about 91% or better"
    low, high = evalstats.wilson(300, 300)
    assert low > 0.98
    assert evalstats.wilson(0, 0) == (0.0, 1.0)
    low, high = evalstats.wilson(32, 39)            # an 82% hit@1 on the old book set
    assert 0.67 < low < 0.70 and 0.90 < high < 0.93


def test_split_is_stable_roughly_thirty_percent_and_overridable():
    ids = [f"case-{i}" for i in range(2000)]
    first = [evalstats.split_of(i) for i in ids]
    assert first == [evalstats.split_of(i) for i in ids]
    share = first.count("test") / len(first)
    assert 0.26 < share < 0.34
    assert evalstats.split_of("anything", "test") == "test"
    assert evalstats.split_of("anything", "dev") == "dev"
    assert evalstats.split_of("x", "bogus") in {"dev", "test"}


def test_mcnemar_and_bootstrap():
    assert evalstats.mcnemar_exact(0, 0) == 1.0
    assert evalstats.mcnemar_exact(5, 5) == 1.0
    assert evalstats.mcnemar_exact(0, 10) < 0.01       # ten flips, all one way: not luck
    assert evalstats.mcnemar_exact(2, 4) > 0.05        # six flips, mixed: could be luck
    mean, low, high = evalstats.bootstrap_mean([1, 0, 1, 1, 0, 1, 1, 1])
    assert low <= mean <= high
    assert evalstats.bootstrap_mean([1, 0, 1, 1, 0, 1, 1, 1]) == (mean, low, high)   # seeded
    assert evalstats.bootstrap_mean([]) == (0.0, 0.0, 0.0)


def test_match_all_needs_every_passage_in_the_cutoff():
    assert harness.effective_rank([2, 5]) == 2                       # any: the first acceptable one
    assert harness.effective_rank([2, 5], require_all=True) == 5     # all: the last required one
    assert harness.effective_rank([2, None], require_all=True) is None
    assert harness.effective_rank([None, None]) is None


def _fake_search(monkeypatch, results):
    monkeypatch.setattr(harness, "search", lambda query, top_k, rerank, collection_id=None, overrides=None: results[query])


def test_comparison_case_scores_as_a_miss_when_one_source_is_missing(monkeypatch):
    cases = [{"id": "cmp", "query": "q", "match": "all",
              "relevant": [{"document": "a.docx"}, {"document": "b.docx"}]}]
    _fake_search(monkeypatch, {"q": [{"document": "a.docx", "page": 1}, {"document": "c.docx", "page": 1}]})
    records = harness.evaluate(cases, rerank=True)
    assert records[0]["best"] is None and records[0]["ranks"] == [1, None]
    summary = harness.summarise(records)
    assert summary["all_of"]["n"] == 1 and summary["all_of"]["hits"][10] == 0
    assert summary["overall"]["n"] == 0        # kept out of hit@1 / MRR


def test_all_of_cases_do_not_drag_down_hit_at_one(monkeypatch):
    cases = [{"id": "cmp", "query": "q", "match": "all",
              "relevant": [{"document": "a.docx"}, {"document": "b.docx"}]},
             {"id": "one", "query": "r", "relevant": [{"document": "a.docx"}]}]
    _fake_search(monkeypatch, {"q": [{"document": "a.docx", "page": 1}, {"document": "b.docx", "page": 1}],
                               "r": [{"document": "a.docx", "page": 1}]})
    summary = harness.summarise(harness.evaluate(cases, rerank=True))
    assert summary["overall"]["hits"][1] == 1 and summary["overall"]["n"] == 1
    assert summary["all_of"]["hits"][5] == 1 and summary["all_of"]["hits"][1] == 0


def test_summary_splits_dev_from_test_and_counts_gate_leaks(monkeypatch):
    test_id = next(f"t{i}" for i in range(200) if evalstats.split_of(f"t{i}") == "test")
    dev_id = next(f"d{i}" for i in range(200) if evalstats.split_of(f"d{i}") == "dev")
    cases = [
        {"id": dev_id, "query": "q1", "relevant": [{"document": "a.docx"}], "tags": ["x"]},
        {"id": test_id, "query": "q2", "relevant": [{"document": "a.docx"}], "tags": ["x"]},
        {"id": "g1", "query": "q3", "expect_empty": True, "tags": ["near-miss"]},
        {"id": "g2", "query": "q4", "expect_empty": True, "tags": ["near-miss"]},
    ]
    _fake_search(monkeypatch, {"q1": [{"document": "a.docx", "page": 1}], "q2": [{"document": "b.docx", "page": 1}],
                               "q3": [], "q4": [{"document": "a.docx", "page": 1, "rerank_score": -1.5}]})
    summary = harness.summarise(harness.evaluate(cases, rerank=True))
    assert summary["by_split"]["dev"]["hits"][1] == 1 and summary["by_split"]["dev"]["n"] == 1
    assert summary["by_split"]["test"]["hits"][1] == 0 and summary["by_split"]["test"]["n"] == 1
    assert summary["overall"]["n"] == 2 and summary["by_tag"]["x"]["n"] == 2
    assert summary["gate"] == {"total": 2, "gated": 1, "leaked": 1}
    assert summary["gate_by_tag"]["near-miss"] == {"total": 2, "gated": 1}


def test_select_cases_filters_by_split_and_tag():
    cases = [{"id": f"c{i}", "query": "q", "tags": ["a"] if i % 2 else ["b"]} for i in range(60)]
    dev = harness.select_cases(cases, "dev")
    test = harness.select_cases(cases, "test")
    assert len(dev) + len(test) == 60 and test and dev
    assert all(evalstats.split_of(c["id"]) == "test" for c in test)
    assert all("a" in c["tags"] for c in harness.select_cases(cases, "all", "a"))


def _run_file(tmp_path, name, ranks):
    records = [{"id": f"c{i}", "kind": "judged", "best": rank, "ranks": [rank], "tags": [], "split": "dev",
                "note": ""} for i, rank in enumerate(ranks)]
    path = tmp_path / name
    path.write_text(json.dumps({"meta": {"overrides": {}}, "records": records}))
    return path


def test_compare_reports_flips_and_significance(tmp_path, capsys):
    # B fixes eleven queries A missed and breaks none: that cannot be luck.
    a = _run_file(tmp_path, "a.json", [None] * 11 + [1] * 9)
    b = _run_file(tmp_path, "b.json", [1] * 11 + [1] * 9)
    assert harness.compare(a, b) == 0
    out = capsys.readouterr().out
    assert "20 queries in both runs" in out
    assert "only A right 0, only B right 11" in out and "B better" in out
    assert "interval excludes 0" in out
    # Identical runs: nothing to report.
    harness.compare(a, a)
    assert "no clear difference" in capsys.readouterr().out


def test_score_saves_a_run_that_compare_can_read(monkeypatch, tmp_path, capsys):
    _fake_search(monkeypatch, {"q": [{"document": "a.docx", "page": 1}]})
    monkeypatch.setattr(harness, "_server_config", lambda: {"embedding_model": "m"})
    cases = [{"id": "one", "query": "q", "relevant": [{"document": "a.docx"}]}]
    out = tmp_path / "runs" / "r.json"
    assert harness.score(cases, True, out, "q.jsonl") == 0
    saved = json.loads(out.read_text())
    assert saved["meta"]["server"] == {"embedding_model": "m"} and saved["meta"]["queries"] == "q.jsonl"
    assert saved["records"][0]["best"] == 1
    capsys.readouterr()
    assert harness.compare(out, out) == 0
