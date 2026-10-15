"""The synthetic benchmark's ground truth is only worth having if it is right.

These tests build the corpus, write it, read it back with the plain format
libraries, and check that every judged phrase is where the case says it is.
They also pin the properties that make the benchmark hard in the intended ways
(paraphrases really avoid the document's words, reverse questions really omit
the entity, absent entities really are absent) so a later edit to the
templates cannot quietly turn it into an easy one.
"""

import re
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL))

import evalstats  # noqa: E402
import harness  # noqa: E402
import synth_corpus as synth  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    corpus = synth.build_corpus()
    queries = synth.build_queries(corpus)
    answers = synth.build_answers(corpus)
    out = tmp_path_factory.mktemp("synth")
    synth.write_corpus(corpus, out)
    return corpus, queries, answers, out


def test_ground_truth_checks_out_against_the_rendered_files(built):
    corpus, queries, answers, out = built
    assert synth.verify(corpus, out, queries, answers) == []


def test_same_seed_same_benchmark_and_different_seed_different():
    first = synth.build_corpus(1)
    assert [f.sentence for f in first.facts] == [f.sentence for f in synth.build_corpus(1).facts]
    assert [f.sentence for f in first.facts] != [f.sentence for f in synth.build_corpus(2).facts]
    assert synth.build_queries(first, 1) == synth.build_queries(synth.build_corpus(1), 1)


def test_the_committed_sets_are_what_the_generator_writes():
    """Hand edits to the generated files would silently change what is measured."""
    corpus = synth.build_corpus()
    for name, expected in (("queries_synth.jsonl", synth.build_queries(corpus)),
                           ("answers_synth.jsonl", synth.build_answers(corpus))):
        path = EVAL / name
        if not path.exists():
            pytest.skip(f"{name} not generated yet")
        assert harness.load_cases(path) == expected, f"{name} is stale: rerun eval/synth_corpus.py"


def test_size_and_mix(built):
    _, queries, answers, _ = built
    judged = [q for q in queries if q.get("relevant")]
    assert len(queries) >= 300 and len(judged) >= 280
    assert len(answers) >= 100
    test_share = sum(1 for q in judged if evalstats.split_of(q["id"], q.get("split")) == "test") / len(judged)
    assert 0.2 < test_share < 0.4
    tags = {t for q in queries for t in q.get("tags", [])}
    assert {"direct", "para", "reverse", "comparison", "near-miss", "off-topic", "pdf", "table", "notes",
            "row", "shared-name"} <= tags
    assert len({q["id"] for q in queries}) == len(queries)
    assert len({q["query"] for q in queries}) == len(queries)


def test_every_format_the_service_ingests_is_covered(built):
    corpus, *_ = built
    assert {d.filename.rsplit(".", 1)[-1] for d in corpus.docs} == {"docx", "pdf", "pptx", "xlsx", "csv", "md", "txt"}


def _words(text: str) -> set:
    stop = {"the", "of", "at", "is", "was", "a", "an", "in", "to", "for", "by", "and", "or", "on", "its", "it",
            "what", "which", "who", "how", "does", "do", "did", "are", "has", "have", "with", "from", "that"}
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in stop}


def test_paraphrase_questions_share_fewer_words_with_the_document_than_direct_ones(built):
    corpus, queries, *_ = built
    facts = {f"{f.domain}-{f.attr}-{synth.slug(f.place)}": f for f in corpus.facts}
    overlap = {"direct": [], "para": []}
    for q in queries:
        kind = next((t for t in ("direct", "para") if t in q.get("tags", [])), None)
        if kind is None or "comparison" in q["tags"]:
            continue
        fact = facts[q["id"].rsplit("-", 1)[0]]
        own = _words(fact.entity)
        query_words, fact_words = _words(q["query"]) - own, _words(fact.sentence) - own
        overlap[kind].append(len(query_words & fact_words) / max(1, len(query_words)))
    assert len(overlap["direct"]) > 50 and len(overlap["para"]) > 50
    mean = {k: sum(v) / len(v) for k, v in overlap.items()}
    assert mean["para"] < mean["direct"] - 0.1, mean


def test_reverse_questions_give_the_value_and_not_the_entity(built):
    corpus, queries, *_ = built
    facts = {f"{f.domain}-{f.attr}-{synth.slug(f.place)}-reverse": f for f in corpus.facts}
    reverse = [q for q in queries if "reverse" in q.get("tags", [])]
    assert len(reverse) > 30
    for q in reverse:
        fact = facts[q["id"]]
        assert fact.value in q["query"]
        assert fact.place not in q["query"]


def test_absent_entities_appear_nowhere_in_the_corpus(built):
    corpus, queries, answers, out = built
    everything = " ".join(" ".join(synth.read_back(out / d.filename)) for d in corpus.docs).lower()
    assert len(corpus.absent_places) >= 30
    for place in corpus.absent_places:
        assert place.lower() not in everything
    near = [q for q in queries if "near-miss" in q.get("tags", [])]
    assert len(near) == 20 and all(q["expect_empty"] for q in near)
    assert any(p in near[0]["query"] for p in corpus.absent_places)


def test_the_value_each_answer_must_mention_is_in_the_fact_and_not_in_the_distractor(built):
    corpus, _, answers, _ = built
    by_id = {f"{f.domain}-{f.attr}-{synth.slug(f.place)}": f for f in corpus.facts}
    singles = [a for a in answers if a.get("key_points") and "comparison" not in a["tags"]]
    assert len(singles) >= 60
    guarded = 0
    for case in singles:
        fact = by_id[case["id"].rsplit("-", 1)[0]]
        assert all(re.search(p, fact.sentence) for p in case["must_mention"]), case["id"]
        for pattern in case.get("must_not_mention", []):
            guarded += 1
            assert not re.search(pattern, fact.sentence), case["id"]
    assert guarded > 40


def test_refusal_cases_cite_nothing_and_cover_three_kinds(built):
    _, _, answers, _ = built
    refusals = [a for a in answers if a.get("expect_refusal")]
    assert not any(a.get("must_cite") or a.get("must_mention") for a in refusals)
    assert {t for a in refusals for t in a["tags"]} >= {"absent-attribute", "near-miss", "off-topic"}


def test_number_patterns_accept_the_ways_a_model_writes_them():
    pattern = synth.answer_regex("4,310 pallets", "int")
    assert re.search(pattern, "about 4,310 pallets") and re.search(pattern, "4310 pallets")
    assert not re.search(pattern, "14,3100")
    pattern = synth.answer_regex("94.2 percent", "float")
    assert re.search(pattern, "94.2%") and not re.search(pattern, "194.2")
    assert re.search(synth.answer_regex("€48.60", "float"), "costs €48.60 per pallet")
    assert re.search(synth.answer_regex("Orlin Vasquez", "person"), "Managed by Mr. Vasquez")
