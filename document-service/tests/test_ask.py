"""Ask mode: retrieval -> streamed, cited answer over Server-Sent Events."""

import json

from conftest import make_pdf, wait_for_status


def parse_sse(body: str) -> list[dict]:
    """The JSON payload of every ``data:`` frame in an SSE response body."""
    events = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


FAKE_MODEL = "ollama:test-model"


def enable_fake_model(monkeypatch, module, model_id=FAKE_MODEL):
    async def fake_list_models():
        provider = model_id.split(":", 1)[0]
        return [{"id": model_id, "label": model_id, "provider": provider}]

    monkeypatch.setattr(module.generation, "list_models", fake_list_models)


def make_fake_stream():
    """An async generator standing in for a real backend, recording its calls."""

    calls = []

    async def fake_stream(model, system, messages, keys=None):
        calls.append({"model": model, "system": system, "messages": messages, "keys": keys})
        for chunk in ("The absurd ", "is the confrontation ", "[1]"):
            yield chunk

    fake_stream.calls = calls
    return fake_stream


def index_essay(client, text="Camus writes that the absurd is a confrontation with freedom."):
    upload = client.post("/documents", files={"file": ("essay.pdf", make_pdf(text), "application/pdf")})
    assert upload.status_code == 201
    doc_id = upload.json()["document_id"]
    wait_for_status(client, doc_id, "indexed")
    return doc_id


def test_ask_streams_answer_then_sources(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module)
    monkeypatch.setattr(module, "rerank", lambda query, passages: [1.0 for _ in passages])
    fake_stream = make_fake_stream()
    monkeypatch.setattr(module.generation, "generate_stream", fake_stream)

    response = client.post("/ask", json={"question": "What is the absurd?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "The absurd is the confrontation [1]"

    sources = [e for e in events if e["type"] == "sources"]
    assert len(sources) == 1
    assert sources[0]["results"][0]["document"] == "essay.pdf"
    assert sources[0]["model"] == FAKE_MODEL
    assert "low_confidence" in sources[0]
    assert fake_stream.calls[0]["model"] == FAKE_MODEL


def test_ask_uses_the_requested_model_and_falls_back_when_unknown(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module, "anthropic:claude-opus-5")
    monkeypatch.setattr(module, "rerank", lambda query, passages: [1.0 for _ in passages])
    fake_stream = make_fake_stream()
    monkeypatch.setattr(module.generation, "generate_stream", fake_stream)

    # A known model is honoured.
    client.post("/ask", json={"question": "q", "model": "anthropic:claude-opus-5"})
    assert fake_stream.calls[-1]["model"] == "anthropic:claude-opus-5"

    # An unknown model falls back to the server default.
    client.post("/ask", json={"question": "q", "model": "openai:gpt-does-not-exist"})
    assert fake_stream.calls[-1]["model"] == "anthropic:claude-opus-5"


def test_ask_accepts_a_browser_supplied_api_key_for_an_unlisted_model(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    # Only a local model is server-listed; the browser brings its own Claude key.
    enable_fake_model(monkeypatch, module, "ollama:local")
    monkeypatch.setattr(module, "rerank", lambda query, passages: [1.0 for _ in passages])
    fake_stream = make_fake_stream()
    monkeypatch.setattr(module.generation, "generate_stream", fake_stream)

    response = client.post(
        "/ask",
        json={
            "question": "the absurd",
            "model": "anthropic:claude-sonnet-5",
            "provider_keys": {"anthropic": "sk-ant-xyz", "bogus": "x", "openai": ""},
        },
    )
    assert response.status_code == 200
    call = fake_stream.calls[-1]
    assert call["model"] == "anthropic:claude-sonnet-5"
    assert call["keys"] == {"anthropic": "sk-ant-xyz"}  # sanitised: bogus + empty dropped


def test_sanitize_provider_keys(service):
    module, _client, _indexed = service
    assert module._sanitize_provider_keys({"anthropic": " sk-1 ", "openai": "", "x": "y"}) == {
        "anthropic": "sk-1"
    }
    assert module._sanitize_provider_keys("nope") == {}
    assert module._sanitize_provider_keys({"google": 123}) == {}


def test_ask_thorough_uses_a_looser_gate(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module)
    # Score every passage in the band that a -2.0 gate rejects but -5.0 keeps.
    monkeypatch.setattr(module, "rerank", lambda query, passages: [-3.5 for _ in passages])
    monkeypatch.setattr(module.generation, "generate_stream", make_fake_stream())

    async def fake_thorough(model, question, sources, history=None, keys=None):
        yield ("token", f"answer over {len(sources)} passages")

    monkeypatch.setattr(module.generation, "generate_thorough", fake_thorough)

    quick = parse_sse(client.post("/ask", json={"question": "the absurd", "mode": "quick"}).text)
    assert [e for e in quick if e["type"] == "sources"][0]["results"] == []  # gated out at -2.0

    thorough = parse_sse(
        client.post("/ask", json={"question": "the absurd", "mode": "thorough"}).text
    )
    assert [e for e in thorough if e["type"] == "sources"][0]["results"]  # survives at -5.0


def test_ask_no_hits_skips_generation(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module)
    monkeypatch.setattr(module, "rerank", lambda query, passages: [-9.0 for _ in passages])
    fake_stream = make_fake_stream()
    monkeypatch.setattr(module.generation, "generate_stream", fake_stream)

    response = client.post("/ask", json={"question": "How do I rebuild a carburetor?"})
    assert response.status_code == 200
    events = parse_sse(response.text)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "couldn't find anything" in tokens
    assert [e for e in events if e["type"] == "sources"][0]["results"] == []
    assert fake_stream.calls == []


def test_ask_passes_conversation_history_through(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module)
    monkeypatch.setattr(module, "rerank", lambda query, passages: [1.0 for _ in passages])
    fake_stream = make_fake_stream()
    monkeypatch.setattr(module.generation, "generate_stream", fake_stream)

    history = [
        {"role": "user", "content": "Who wrote about the absurd?"},
        {"role": "assistant", "content": "Albert Camus."},
    ]
    response = client.post("/ask", json={"question": "Say more.", "history": history})
    assert response.status_code == 200

    sent = fake_stream.calls[0]["messages"]
    assert sent[:2] == history
    assert sent[-1]["role"] == "user" and "Say more." in sent[-1]["content"]
    assert "Sources:" in sent[-1]["content"]


def test_ask_returns_503_when_no_models_are_available(service, monkeypatch):
    module, client, _indexed = service

    async def no_models():
        return []

    monkeypatch.setattr(module.generation, "list_models", no_models)
    response = client.post("/ask", json={"question": "anything"})
    assert response.status_code == 503
    assert "no models" in response.json()["detail"].lower()


def test_ask_emits_error_event_when_generation_fails(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module)
    monkeypatch.setattr(module, "rerank", lambda query, passages: [1.0 for _ in passages])

    async def boom(model, system, messages, keys=None):
        raise module.generation.GenerationError("upstream 500")
        yield  # pragma: no cover - marks this an async generator

    monkeypatch.setattr(module.generation, "generate_stream", boom)
    response = client.post("/ask", json={"question": "What is the absurd?"})
    assert response.status_code == 200
    errors = [e for e in parse_sse(response.text) if e["type"] == "error"]
    assert len(errors) == 1
    assert "generation failed" in errors[0]["detail"]


def test_ask_models_endpoint_lists_models_and_default(service, monkeypatch):
    module, client, _indexed = service
    enable_fake_model(monkeypatch, module)
    body = client.get("/ask/models").json()
    assert body["models"][0]["id"] == FAKE_MODEL
    assert body["default"] == FAKE_MODEL


def _hit(doc, text, score):
    return {"score": score, "payload": {"document_id": doc, "text": text}}


def test_diversify_caps_passages_per_document(service):
    module, _client, _indexed = service
    hits = [_hit("a", f"alpha passage number {i}", 9 - i) for i in range(5)] + [
        _hit("b", "beta content here", 3),
        _hit("c", "gamma content here", 2),
    ]
    out = module.diversify_hits(hits, top_k=4, max_per_doc=2, dedup_jaccard=1.0)
    docs = [h["payload"]["document_id"] for h in out]
    assert len(out) == 4
    assert docs.count("a") == 2  # one document can't dominate
    assert set(docs) == {"a", "b", "c"}


def test_diversify_drops_near_duplicate_passages(service):
    module, _client, _indexed = service
    dup = "the library of babel contains every possible book of four hundred ten pages"
    hits = [
        _hit("a", dup, 9),
        _hit("b", dup + " indeed truly", 8),
        _hit("c", "wormholes fold spacetime for interstellar travel", 7),
    ]
    out = module.diversify_hits(hits, top_k=2, max_per_doc=0, dedup_jaccard=0.6)
    texts = {h["payload"]["text"] for h in out}
    assert dup in texts
    assert "wormholes fold spacetime for interstellar travel" in texts  # not the dup of 'a'


def test_ask_sources_event_reports_document_spread(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module)
    monkeypatch.setattr(module, "rerank", lambda query, passages: [1.0 for _ in passages])
    monkeypatch.setattr(module.generation, "generate_stream", make_fake_stream())

    events = parse_sse(client.post("/ask", json={"question": "the absurd"}).text)
    src = [e for e in events if e["type"] == "sources"][0]
    assert src["documents"] >= 1
    assert src["relevant_count"] >= len(src["results"])


def test_ask_thorough_mode_streams_progress_then_synthesis(service, monkeypatch):
    module, client, _indexed = service
    index_essay(client)
    enable_fake_model(monkeypatch, module)
    monkeypatch.setattr(module, "rerank", lambda query, passages: [1.0 for _ in passages])

    async def fake_thorough(model, question, sources, history=None, keys=None):
        yield ("progress", "Reading 1 documents…")
        yield ("token", "Across the sources, ")
        yield ("token", "the absurd is a confrontation [1].")

    monkeypatch.setattr(module.generation, "generate_thorough", fake_thorough)

    events = parse_sse(
        client.post("/ask", json={"question": "the absurd", "mode": "thorough"}).text
    )
    assert any(e["type"] == "progress" for e in events)
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert answer == "Across the sources, the absurd is a confrontation [1]."
    assert [e for e in events if e["type"] == "sources"][0]["results"]


def test_config_reports_generation_backend(service):
    _module, client, _indexed = service
    config = client.get("/config").json()
    assert "generation" in config
    assert set(config["generation"]) == {"enabled", "default_model", "providers"}
    assert config["generation"]["enabled"] is False  # hermetic: no models
