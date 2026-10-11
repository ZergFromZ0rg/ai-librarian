"""AI sort's decisions, with no model and no network: the evidence goes in, a plan
comes out. The point of the module is what it refuses to believe."""

import pytest

import ai_sort
from ai_sort import Answer, Evidence

CAMUS_PAGE = "A HAPPY DEATH\n\nALBERT CAMUS\n\nTranslated from the French by Richard Howard\n\nPublished by Knopf"
HAPPY = {"title": "A Happy Death", "author": "Albert Camus", "published_year": 1972, "score": 0.93, "id": "ol:1"}
OTHER = {"title": "A Happy Death: Study Guide", "author": "SparkNotes", "score": 0.6, "id": "ol:2"}


def book(**kw) -> Evidence:
    base = dict(filename="A Hapy Death - Camus.pdf", title="A Hapy Death", author="Camus", opening=CAMUS_PAGE, pages=56, kind="book")
    base.update(kw)
    return Evidence(**base)


def changes(plan):
    return {c.field: c for c in plan.changes}


# --- the model's reply, parsed defensively --------------------------------------


def test_a_reply_is_read_even_in_a_code_fence_and_bad_fields_become_no_answer():
    raw = ai_sort.parse_json('Sure!\n```json\n{"title": "A Happy Death", "same_work_as": 9, "confidence": "high", "kind": "novel", "is_fiction": "yes"}\n```')
    answer = ai_sort.parse_answer(raw, candidate_count=2)
    assert answer.title == "A Happy Death"
    assert answer.same_work_as is None  # there is no candidate 9
    assert answer.confidence == 0.0 and answer.kind is None and answer.is_fiction is None
    assert ai_sort.parse_answer({"same_work_as": 2, "confidence": 7, "is_fiction": True}, 2).confidence == 1.0
    assert ai_sort.parse_answer({"same_work_as": True}, 2).same_work_as is None  # a boolean is not a number


def test_a_reply_that_is_not_json_is_an_error_not_a_guess():
    with pytest.raises(ai_sort.AiError):
        ai_sort.parse_json("I think the author is Camus.")
    with pytest.raises(ai_sort.AiError):
        ai_sort.parse_json("[1, 2]")


# --- claims are checked against the evidence ----------------------------------------


def test_a_name_must_be_written_in_the_evidence():
    blob = ai_sort.evidence_blob(book())
    assert ai_sort.author_is_written_in("Albert Camus", blob)
    assert ai_sort.author_is_written_in("A. Camus", blob)  # initials are not required to match
    assert not ai_sort.author_is_written_in("Jean-Paul Sartre", blob)
    assert not ai_sort.author_is_written_in("", blob)
    assert ai_sort.quote_is_written_in("ALBERT CAMUS", blob)
    assert not ai_sort.quote_is_written_in("Albert Camus wrote this in 1936", blob)


# --- the author -------------------------------------------------------------------


def test_the_model_and_the_catalogue_agreeing_completes_the_surname_by_itself():
    ev = book(candidates=[HAPPY, OTHER])
    plan = ai_sort.plan_book(ev, Answer(author="Albert Camus", same_work_as=1, confidence=0.9, quote="ALBERT CAMUS"))
    author = changes(plan)["author"]
    assert (author.before, author.after, author.auto) == ("Camus", "Albert Camus", True)
    assert plan.match is HAPPY


def test_the_model_alone_is_trusted_only_when_it_quotes_the_page():
    ev = book()
    quoted = ai_sort.plan_book(ev, Answer(author="Albert Camus", confidence=0.9, quote="ALBERT CAMUS"))
    assert changes(quoted)["author"].auto is True
    unquoted = ai_sort.plan_book(ev, Answer(author="Albert Camus", confidence=0.9, quote=None))
    assert changes(unquoted)["author"].auto is False  # a suggestion for the reader


def test_an_invented_author_is_rejected_outright():
    ev = book()
    plan = ai_sort.plan_book(ev, Answer(author="Jean-Paul Sartre", confidence=0.99, quote="Jean-Paul Sartre"))
    assert "author" not in changes(plan)
    # ...and so is a different person who merely shares a first name with the page.
    assert "author" not in changes(ai_sort.plan_book(ev, Answer(author="Albert Einstein", confidence=0.9, quote="Albert")))


def test_a_full_name_is_never_replaced_even_when_the_model_disagrees():
    ev = book(author="Albert Camus")
    plan = ai_sort.plan_book(ev, Answer(author="Albert Camus", confidence=0.9, quote="ALBERT CAMUS"))
    assert "author" not in changes(plan)
    other = ai_sort.plan_book(book(author="Jim Manis"), Answer(author="Albert Camus", confidence=0.9, quote="ALBERT CAMUS"))
    assert "author" not in changes(other)


def test_a_missing_author_is_found_on_the_title_page():
    ev = book(author=None, filename="A Hapy Death.pdf")
    plan = ai_sort.plan_book(ev, Answer(author="Albert Camus", confidence=0.8, quote="ALBERT CAMUS"))
    author = changes(plan)["author"]
    assert (author.before, author.after, author.auto) == (None, "Albert Camus", True)


def test_a_catalogue_author_with_a_different_surname_does_not_fill_a_surname():
    wrong = dict(HAPPY, author="Somebody Else")
    plan = ai_sort.plan_book(book(candidates=[wrong]), Answer(same_work_as=1, confidence=0.9))
    assert "author" not in changes(plan)


# --- the catalogue match -----------------------------------------------------------


def test_without_a_model_only_a_strict_match_is_confirmed():
    ev = book(candidates=[HAPPY, OTHER])
    assert ai_sort.strict_match(ev) is HAPPY
    plan = ai_sort.plan_book(ev, None)
    assert plan.match is HAPPY
    assert changes(plan)["author"].auto is True and "rules" in changes(plan)["author"].source

    # A near-tie between different books is not convincing.
    tie = book(candidates=[dict(HAPPY, score=0.9), dict(OTHER, score=0.88, title="A Happy Death Revisited")])
    assert ai_sort.strict_match(tie) is None
    # Nor is a match whose author contradicts what we know.
    assert ai_sort.strict_match(book(candidates=[dict(HAPPY, author="Somebody Else")])) is None
    # Nor one whose title is a different book.
    assert ai_sort.strict_match(book(candidates=[dict(HAPPY, title="The Plague")])) is None


def test_the_model_can_reject_every_match():
    plan = ai_sort.plan_book(book(candidates=[HAPPY]), Answer(same_work_as=None, confidence=0.9))
    assert plan.match is None
    assert any("no catalogue match" in note for note in plan.notes)


def test_a_match_with_a_different_title_is_not_the_same_work_unless_the_model_is_very_sure():
    unrelated = dict(HAPPY, title="Gardening for Beginners", author="Albert Camus")
    plan = ai_sort.plan_book(book(candidates=[unrelated]), Answer(same_work_as=1, confidence=0.6))
    assert plan.match is None
    assert ai_sort.plan_book(book(candidates=[unrelated]), Answer(same_work_as=1, confidence=0.9)).match is unrelated


# --- the title ----------------------------------------------------------------------


def test_a_typo_in_a_guessed_title_is_fixed_from_the_confirmed_match():
    plan = ai_sort.plan_book(book(candidates=[HAPPY]), Answer(same_work_as=1, confidence=0.9))
    title = changes(plan)["title"]
    assert (title.before, title.after, title.auto) == ("A Hapy Death", "A Happy Death", True)


def test_a_title_the_reader_chose_is_never_touched():
    ev = book(title="My copy of A Hapy Death", title_untouched=False, candidates=[HAPPY])
    assert "title" not in changes(ai_sort.plan_book(ev, Answer(same_work_as=1, confidence=0.9, title="A Happy Death")))


def test_a_model_may_fix_a_title_but_not_replace_it_and_only_as_a_suggestion():
    ev = book(opening="A HAPPY DEATH\nALBERT CAMUS")
    fixed = changes(ai_sort.plan_book(ev, Answer(title="A Happy Death", confidence=0.9)))["title"]
    assert fixed.auto is False  # one source
    # A different title is not a fix.
    assert "title" not in changes(ai_sort.plan_book(ev, Answer(title="The Plague", confidence=0.9)))
    # A title that is nowhere in the evidence is not offered, however close it is.
    assert "title" not in changes(ai_sort.plan_book(book(opening=""), Answer(title="A Happy Deaths", confidence=0.9)))


# --- verdicts the text could not give -----------------------------------------------


def test_a_fiction_verdict_is_kept_only_when_the_text_was_in_doubt_and_the_model_was_sure():
    doubtful = book(fiction_in_doubt=True)
    plan = ai_sort.plan_book(doubtful, Answer(is_fiction=True, confidence=0.9))
    assert plan.ai == {"fiction": True} and changes(plan)["fiction"].auto is True
    assert ai_sort.plan_book(doubtful, Answer(is_fiction=True, confidence=0.4)).ai == {}  # unsure
    assert ai_sort.plan_book(book(fiction_in_doubt=False, form="nonfiction"), Answer(is_fiction=True, confidence=0.99)).ai == {}  # the text was clear
    assert ai_sort.plan_book(book(fiction_in_doubt=False, form=None), Answer(is_fiction=False, confidence=0.9)).ai == {"fiction": False}  # unknown form


def test_a_kind_change_is_always_a_suggestion_and_only_where_length_alone_decided():
    weak = book(kind="document", weak=True)
    plan = ai_sort.plan_book(weak, Answer(kind="book", confidence=0.9))
    kind = changes(plan)["kind"]
    assert (kind.before, kind.after, kind.auto) == ("document", "book", False)
    assert "kind" not in changes(ai_sort.plan_book(book(kind="document", weak=False), Answer(kind="book", confidence=0.9)))
    assert "kind" not in changes(ai_sort.plan_book(weak, Answer(kind="book", confidence=0.5)))


# --- the prompt ---------------------------------------------------------------------


def test_the_prompt_numbers_the_candidates_and_carries_the_evidence():
    prompt = ai_sort.build_prompt(book(candidates=[HAPPY, OTHER], embedded_author="Albert Camus"))
    assert "1. A Happy Death - Albert Camus, 1972" in prompt and "2. A Happy Death: Study Guide" in prompt
    assert "File name: A Hapy Death - Camus.pdf" in prompt and "ALBERT CAMUS" in prompt
    assert "(No catalogue matches were looked up.)" in ai_sort.build_prompt(book())


def test_model_selection_prefers_the_asked_for_then_the_configured_one(monkeypatch):
    monkeypatch.setenv("GENERATION_MODEL", "ollama:qwen2.5:7b")
    assert ai_sort.pick_model("ollama:llama3.2:3b", ["qwen2.5:7b", "llama3.2:3b"]) == "llama3.2:3b"
    assert ai_sort.pick_model(None, ["llama3.2:3b", "qwen2.5:7b"]) == "qwen2.5:7b"
    assert ai_sort.pick_model("missing", ["llama3.2:3b"]) == "llama3.2:3b"
    assert ai_sort.pick_model(None, []) is None


# --- the translator is not the author (found by running the real model) ------------------

SCHOPENHAUER_PAGE = "THE ESSAYS OF ARTHUR SCHOPENHAUER\nSELECTED AND TRANSLATED BY T. BAILEY SAUNDERS, M.A.\nARTHUR SCHOPENHAUER"
ESSAYS = {"title": "Essays of Arthur Schopenhauer", "author": "T. Bailey Saunders, Arthur Schopenhauer", "score": 1.0, "id": "ol:3"}


def essays(**kw) -> Evidence:
    base = dict(filename="Essays of Arthur Schopenhauer.pdf", title="Essays of Arthur Schopenhauer", author=None,
                opening=SCHOPENHAUER_PAGE, pages=490, kind="book", candidates=[ESSAYS])
    base.update(kw)
    return Evidence(**base)


def test_a_translator_credited_before_the_name_is_not_an_author():
    assert ai_sort.contributor_only("T. Bailey Saunders", SCHOPENHAUER_PAGE) is True
    assert ai_sort.contributor_only("Arthur Schopenhauer", SCHOPENHAUER_PAGE) is False
    assert ai_sort.contributor_only("Richard Howard", "Translated from the French by Richard Howard") is True
    assert ai_sort.contributor_only("Jane Roe", "Edited by Jane Roe. " + "and so it begins " * 12 + "Jane Roe wrote the novel") is False  # named elsewhere
    assert ai_sort.contributor_only("Someone Else", SCHOPENHAUER_PAGE) is False  # not printed: not known to be a contributor
    assert ai_sort.contributor_only("Ann Smith", "Introduction by Ann Smith") is True


def test_a_catalogue_entry_listing_a_translator_first_gives_the_author_named_in_the_title():
    assert ai_sort.candidate_author(ESSAYS, essays()) == "Arthur Schopenhauer"
    assert ai_sort.candidate_author({"author": "Camus, Albert"}, essays()) == "Albert Camus"
    assert ai_sort.candidate_author({"author": "Albert Camus"}, essays()) == "Albert Camus"
    assert ai_sort.candidate_author({"author": "Camus"}, essays()) is None  # a surname is not a full name


def test_the_model_naming_the_translator_does_not_get_him_made_the_author():
    answer = Answer(author="T. Bailey Saunders", same_work_as=1, confidence=0.9, quote="TRANSLATED BY T. BAILEY SAUNDERS")
    plan = ai_sort.plan_book(essays(), answer)
    assert changes(plan)["author"].after == "Arthur Schopenhauer"  # from the entry: the surname in the title
    # With nothing but the model's answer, the translator is rejected outright.
    alone = ai_sort.plan_book(essays(candidates=[]), answer)
    assert "author" not in changes(alone)
