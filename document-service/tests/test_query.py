"""clean_query strips conversational framing before retrieval."""

import app


def c(text):
    return app.clean_query(text)


def test_strips_library_framing_and_question_words():
    assert c("Across my library, how is infinity or the infinite described?") == "infinity or the infinite"
    assert c("What do my books say about the meaning of life?") == "the meaning of life"
    assert c("Summarize what's known about Hawking radiation") == "Hawking radiation"
    assert c("Tell me how black holes form") == "black holes form"
    assert c("According to my books, who was Cantor?") == "Cantor"


def test_leaves_already_terse_queries_alone():
    for q in ("Hawking radiation", "infinity and the infinite", "Cantor's diagonal argument",
              "non-euclidean geometry", "black hole evaporation"):
        assert c(q) == q


def test_guardrail_keeps_the_original_when_stripping_would_gut_it():
    assert c("why?") == "why?"
    assert c("What is it?") == "What is it?"
    assert c("How does it work?") == "How does it work?"


def test_never_returns_empty():
    assert c("") == ""
    assert c("   ") == ""
    assert c("Please explain.").strip()
