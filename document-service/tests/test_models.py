"""The model worker process (models.py + model_worker.py).

Uses the worker's "ping" op, which loads no model, so these run without
PyTorch or any downloads.
"""

import time

import pytest

import models


@pytest.fixture
def worker(monkeypatch):
    fresh = models._Worker()
    monkeypatch.setattr(models, "_worker", fresh)
    yield fresh
    fresh.shutdown()


def test_the_worker_starts_on_first_use_and_answers(worker):
    assert not worker.alive()
    assert worker.call({"op": "ping"}) == "pong"
    assert worker.alive()
    assert models.model_info()["loaded"] is True


def test_empty_requests_never_start_a_worker(worker):
    assert models.embed_texts([]) == []
    assert models.rerank("q", []) == []
    assert not worker.alive()


def test_an_idle_worker_exits_and_the_next_call_starts_another(worker, monkeypatch):
    monkeypatch.setattr(models, "IDLE_SECONDS", 1)
    assert worker.call({"op": "ping"}) == "pong"
    first = worker._proc
    deadline = time.monotonic() + 10
    while first.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert first.poll() is not None  # exited by itself: all its memory is released
    assert worker.call({"op": "ping"}) == "pong"
    assert worker._proc is not first


def test_errors_are_raised_and_a_killed_worker_is_replaced(worker):
    worker.call({"op": "ping"})
    with pytest.raises(models.ModelProcessError, match="unknown op"):
        worker.call({"op": "nonsense"})
    assert worker.alive()  # a failed request does not take the worker down
    worker._proc.kill()
    worker._proc.wait()
    assert worker.call({"op": "ping"}) == "pong"
