"""Unit coverage for generation.py helpers (no network, no models)."""

import asyncio

import pytest

import generation


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "")
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GENERATION_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key in ("ANTHROPIC_MODELS", "OPENAI_MODELS", "GOOGLE_MODELS"):
        monkeypatch.delenv(key, raising=False)
    generation._ollama_cache = (0.0, None)


def run(coro):
    return asyncio.run(coro)


def test_delta_from_sse_line_reads_openai_chunks():
    assert generation._delta_from_sse_line('data: {"choices":[{"delta":{"content":"Hi"}}]}') == "Hi"
    assert generation._delta_from_sse_line("data: [DONE]") is None
    assert generation._delta_from_sse_line("data: not json") is None
    assert generation._delta_from_sse_line(": keep-alive") is None


def test_delta_from_ollama_line_reads_native_chunks():
    assert generation._delta_from_ollama_line('{"message":{"content":"Hi"},"done":false}') == "Hi"
    assert generation._delta_from_ollama_line('{"done":true,"done_reason":"stop"}') is None
    assert generation._delta_from_ollama_line("") is None
    assert generation._delta_from_ollama_line("not json") is None


def test_context_passages_scales_with_provider():
    assert generation.context_passages_for("ollama:llama3.2:3b") == generation.ASK_CONTEXT_PASSAGES
    assert generation.context_passages_for("anthropic:claude-opus-5") == generation.ASK_CONTEXT_PASSAGES_CLOUD
    assert generation.context_passages_for("openai:gpt-5.1") == generation.ASK_CONTEXT_PASSAGES_CLOUD


def test_system_prompt_flags_that_sources_are_a_sample():
    assert "not the whole library" in generation._SYSTEM_PROMPT


def test_parse_ollama_tags():
    payload = {"models": [{"name": "llama3.2:latest"}, {"model": "qwen2.5:7b"}, {}]}
    assert generation._parse_ollama_tags(payload) == [
        {"id": "ollama:llama3.2:latest", "label": "llama3.2:latest", "provider": "ollama"},
        {"id": "ollama:qwen2.5:7b", "label": "qwen2.5:7b", "provider": "ollama"},
    ]


def test_split_model_id():
    assert generation._split_model_id("ollama:llama3.2:latest") == ("ollama", "llama3.2:latest")
    assert generation._split_model_id("anthropic:claude-opus-5") == ("anthropic", "claude-opus-5")
    with pytest.raises(generation.GenerationError):
        generation._split_model_id("no-colon")


def test_list_models_merges_ollama_and_keyed_cloud_providers(monkeypatch):
    async def fake_ollama():
        return [{"id": "ollama:local", "label": "local", "provider": "ollama"}]

    monkeypatch.setattr(generation, "_fetch_ollama_models", fake_ollama)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("OPENAI_MODELS", "gpt-x,gpt-y")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    # google has no key -> excluded

    models = run(generation.list_models())
    ids = [m["id"] for m in models]
    assert ids[0] == "ollama:local"  # ollama first
    assert "anthropic:claude-opus-5" in ids
    assert "openai:gpt-x" in ids and "openai:gpt-y" in ids
    assert not any(m["provider"] == "google" for m in models)


def test_default_model_prefers_env_then_first_listed(monkeypatch):
    async def fake_ollama():
        return [{"id": "ollama:a", "label": "a", "provider": "ollama"}]

    monkeypatch.setattr(generation, "_fetch_ollama_models", fake_ollama)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    assert run(generation.default_model()) == "ollama:a"  # first listed

    monkeypatch.setenv("GENERATION_MODEL", "anthropic:claude-sonnet-5")
    assert run(generation.default_model()) == "anthropic:claude-sonnet-5"

    monkeypatch.setenv("GENERATION_MODEL", "openai:not-available")
    assert run(generation.default_model()) == "ollama:a"  # env default not listed -> first


def test_enabled_and_backend_info_when_nothing_is_configured():
    assert run(generation.enabled()) is False
    info = run(generation.backend_info())
    assert info == {"enabled": False, "default_model": None, "providers": []}


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


def test_build_ask_prompt_respects_max_passages():
    sources = [{"document": f"{i}.pdf", "page": i, "text": f"passage {i}"} for i in range(6)]
    _system, _messages, used = generation.build_ask_prompt("q", sources, max_passages=2)
    assert len(used) == 2
