"""The service's handle on the embedding and reranker models.

The models run in a separate process (model_worker.py), started the first time
one is needed and exiting on its own after MODEL_IDLE_UNLOAD_SECONDS without
use. This web process never imports PyTorch, so an idle service holds only a
couple of hundred MB; unloading models inside a long-lived process cannot do
that, because PyTorch's own footprint stays until the process ends.

``embed_texts`` and ``rerank`` keep the signatures of the in-process functions
they replace, so callers (and tests that monkeypatch them) are unchanged.
Calls are serialised: one worker, one request at a time. If the worker has
exited (idle) or died, the next call starts a fresh one and retries once.
"""

from __future__ import annotations

import logging
import os
import pickle
import select
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from reranker import DEFAULT_MODEL as RERANK_MODEL

logger = logging.getLogger("ai_librarian")

IDLE_SECONDS = int(os.environ.get("MODEL_IDLE_UNLOAD_SECONDS", "600"))
# How long one call may take before the worker is presumed hung and killed.
# Covers a cold start (importing PyTorch, loading both models) on a slow CPU.
CALL_TIMEOUT = float(os.environ.get("MODEL_CALL_TIMEOUT", "600"))
_HERE = Path(__file__).resolve().parent


class ModelProcessError(RuntimeError):
    """The model worker could not answer (it failed to start, died, or hung)."""


class _Worker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self.started_at: Optional[float] = None

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self) -> subprocess.Popen:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_HERE), env.get("PYTHONPATH")]))
        # fork+exec of a fresh interpreter (subprocess never forks without
        # exec), so the child inherits none of this multi-threaded process's
        # state -- the macOS fork hazard does not apply.
        proc = subprocess.Popen(
            [sys.executable, "-m", "model_worker", str(IDLE_SECONDS)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=str(_HERE),
            env=env,
        )
        self.started_at = time.monotonic()
        logger.info("Started the model worker (pid %s)", proc.pid)
        return proc

    def _read_exact(self, fd: int, size: int, deadline: float) -> bytes:
        chunks = []
        while size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise TimeoutError(f"no answer from the model worker within {CALL_TIMEOUT:.0f}s")
            chunk = os.read(fd, size)
            if not chunk:
                raise EOFError
            chunks.append(chunk)
            size -= len(chunk)
        return b"".join(chunks)

    def _roundtrip(self, proc: subprocess.Popen, data: bytes) -> dict:
        proc.stdin.write(struct.pack(">I", len(data)) + data)
        proc.stdin.flush()
        deadline = time.monotonic() + CALL_TIMEOUT
        fd = proc.stdout.fileno()
        (size,) = struct.unpack(">I", self._read_exact(fd, 4, deadline))
        return pickle.loads(self._read_exact(fd, size, deadline))

    def _discard(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    def call(self, request: dict):
        data = pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)
        with self._lock:
            for attempt in (1, 2):
                if not self.alive():
                    self._proc = self._start()
                try:
                    reply = self._roundtrip(self._proc, data)
                    break
                except (BrokenPipeError, EOFError, OSError) as exc:
                    # Typically the worker had just exited for being idle.
                    self._discard()
                    if attempt == 2:
                        raise ModelProcessError(f"the model worker stopped unexpectedly ({exc})") from exc
                except TimeoutError as exc:
                    self._discard()
                    raise ModelProcessError(str(exc)) from exc
        if not reply.get("ok"):
            raise ModelProcessError(reply.get("error") or "the model worker failed")
        return reply["result"]

    def shutdown(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()  # the worker exits on EOF
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


_worker = _Worker()


def embed_texts(texts: List[str], kind: str = "passage", model_name: Optional[str] = None) -> List[List[float]]:
    """Unit vectors for ``texts`` (see embeddings.embed_texts), computed in the model worker."""
    if not texts:
        return []
    return _worker.call({"op": "embed", "texts": list(texts), "kind": kind, "model_name": model_name})


def rerank(query: str, passages: List[str], model_name: Optional[str] = None) -> List[float]:
    """Cross-encoder scores aligned with ``passages`` (see reranker.rerank)."""
    if not passages:
        return []
    return _worker.call({"op": "rerank", "query": query, "passages": list(passages), "model_name": model_name})


def model_info() -> dict:
    """The reranker's name and whether the model worker is running right now."""
    return {"model_name": RERANK_MODEL, "loaded": _worker.alive(), "idle_exit_seconds": IDLE_SECONDS}


def shutdown() -> None:
    _worker.shutdown()
