"""Server-side Ask-mode conversation persistence."""


def _msgs(*pairs):
    return [{"role": r, "content": c} for r, c in pairs]


def test_create_list_get_update_delete(service):
    _module, client, _indexed = service

    created = client.post("/conversations", json={
        "messages": _msgs(("user", "What is a meme?"), ("assistant", "A unit of culture [1].")),
        "model": "ollama:qwen2.5:7b",
    })
    assert created.status_code == 201
    conv = created.json()
    cid = conv["id"]
    assert conv["title"] == "What is a meme?"  # derived from the first user turn
    assert conv["model"] == "ollama:qwen2.5:7b"
    assert len(conv["messages"]) == 2

    listing = client.get("/conversations").json()["conversations"]
    assert listing[0]["id"] == cid
    assert listing[0]["message_count"] == 2
    assert "messages" not in listing[0]  # summaries only

    full = client.get(f"/conversations/{cid}").json()
    assert full["messages"][0]["content"] == "What is a meme?"

    updated = client.put(f"/conversations/{cid}", json={
        "messages": _msgs(
            ("user", "What is a meme?"), ("assistant", "A unit of culture [1]."),
            ("user", "Who coined it?"), ("assistant", "Richard Dawkins [2]."),
        ),
    })
    assert updated.status_code == 200
    assert len(updated.json()["messages"]) == 4
    assert updated.json()["updated_at"] >= full["updated_at"]

    assert client.delete(f"/conversations/{cid}").json() == {"deleted": cid}
    assert client.get(f"/conversations/{cid}").status_code == 404
    assert client.get("/conversations").json()["conversations"] == []


def test_missing_and_malformed_ids_are_404(service):
    _module, client, _indexed = service
    assert client.get("/conversations/abcdef012345").status_code == 404
    assert client.put("/conversations/abcdef012345", json={"messages": []}).status_code == 404
    assert client.delete("/conversations/abcdef012345").status_code == 404
    assert client.get("/conversations/NOT-HEX").status_code == 404


def test_oversized_conversation_is_rejected(service):
    _module, client, _indexed = service
    huge = _msgs(("user", "x" * (6 * 1024 * 1024)))
    assert client.post("/conversations", json={"messages": huge}).status_code == 422


def test_messages_over_the_cap_are_trimmed(service):
    _module, client, _indexed = service
    many = _msgs(*[("user", f"q{i}") for i in range(250)])
    conv = client.post("/conversations", json={"messages": many}).json()
    assert len(conv["messages"]) == 200  # MAX_MESSAGES, oldest dropped
    assert conv["messages"][-1]["content"] == "q249"
