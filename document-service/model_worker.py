"""The process that owns the embedding and reranker models.

Started on demand by models.py (``python -m model_worker <idle-seconds>``),
it answers requests one at a time over its stdin/stdout and exits on its own
after ``idle-seconds`` without a request (0 = never). Exiting is the point:
PyTorch and the models can only give their memory back to the system by the
process ending, so an idle service keeps just the small web process alive.

Protocol: every message, both ways, is a 4-byte big-endian length followed by
a pickle. Requests are ``{"op": "embed" | "rerank" | "ping", ...}``; replies
are ``{"ok": True, "result": ...}`` or ``{"ok": False, "error": "..."}``. Only
the parent service ever talks to this process, over pipes it created.
"""

from __future__ import annotations

import os
import pickle
import select
import struct
import sys


def _read_exact(fd: int, size: int) -> bytes:
    chunks = []
    while size:
        chunk = os.read(fd, size)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _handle(request: dict):
    op = request.get("op")
    if op == "ping":
        return "pong"
    if op == "embed":
        from embeddings import embed_texts

        return embed_texts(request["texts"], request.get("kind", "passage"), request.get("model_name"))
    if op == "rerank":
        from reranker import rerank

        return rerank(request["query"], request["passages"], request.get("model_name"))
    raise ValueError(f"unknown op {op!r}")


def main(idle_seconds: float) -> None:
    requests_fd = sys.stdin.fileno()
    # Keep a private copy of stdout for replies, then point fd 1 at stderr so
    # anything a library prints lands in the log instead of in the protocol.
    replies = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    while True:
        if idle_seconds > 0:
            ready, _, _ = select.select([requests_fd], [], [], idle_seconds)
            if not ready:
                return  # idle: exit, and every byte of model memory goes with us
        try:
            (size,) = struct.unpack(">I", _read_exact(requests_fd, 4))
            request = pickle.loads(_read_exact(requests_fd, size))
        except EOFError:
            return  # the service closed the pipe (shutdown)
        try:
            reply = {"ok": True, "result": _handle(request)}
        except Exception as exc:  # reported to the caller, which raises it
            reply = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        data = pickle.dumps(reply, protocol=pickle.HIGHEST_PROTOCOL)
        replies.write(struct.pack(">I", len(data)) + data)
        replies.flush()


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 0.0)
