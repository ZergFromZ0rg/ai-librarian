"""Unit coverage for generation.py helpers (no network, no models)."""

import generation


def test_delta_from_sse_line_reads_openai_chunks():
    line = 'data: {"choices":[{"delta":{"content":"Hello"}}]}'
    assert generation._delta_from_sse_line(line) == "Hello"


def test_delta_from_sse_line_ignores_control_and_junk_lines():
    assert generation._delta_from_sse_line("") is None
    assert generation._delta_from_sse_line(": keep-alive") is None
    assert generation._delta_from_sse_line("data: [DONE]") is None
    assert generation._delta_from_sse_line("data: not json") is None
    assert generation._delta_from_sse_line('data: {"choices":[{"delta":{}}]}') is None


def test_build_ask_prompt_numbers_sources_and_appends_history():
    sources = [
        {"document": "Physics.pdf", "page": 12, "page_end": 12, "text": "Energy is conserved."},
        {"document": "Physics.pdf", "page": 40, "page_end": 41, "text": "Momentum too."},
    ]
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    system, messages, used = generation.build_ask_prompt("Why?", sources, history)

    assert "only the numbered sources" in system
    assert messages[:2] == history
    user_turn = messages[-1]["content"]
    assert "[1] Physics.pdf (p. 12)" in user_turn
    assert "[2] Physics.pdf (pp. 40-41)" in user_turn
    assert user_turn.rstrip().endswith("Question: Why?")
    assert used == sources


def test_build_ask_prompt_respects_the_character_budget(monkeypatch):
    monkeypatch.setattr(generation, "ASK_MAX_CONTEXT_CHARS", 20)
    sources = [
        {"document": "a.pdf", "page": 1, "text": "first passage well under budget"},
        {"document": "b.pdf", "page": 2, "text": "second passage that should be dropped"},
    ]
    _system, messages, used = generation.build_ask_prompt("q", sources)
    assert len(used) == 1
    assert "[2]" not in messages[-1]["content"]


def test_backend_info_shape():
    info = generation.backend_info()
    assert set(info) == {"backend", "model", "enabled"}
    assert info["backend"] in {"llamacpp", "anthropic", "off"}
