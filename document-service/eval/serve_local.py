#!/usr/bin/env python3
"""Run the real document service on this machine -- no Docker, no Qdrant server.

    ../.venv/bin/python eval/serve_local.py ../library/synth --port 8765
    python eval/harness.py --url http://127.0.0.1:8765 score --queries eval/queries_synth.jsonl

The evals need the real embedding and reranking models behind a real index.
Normally that means the Docker stack; this script instead imports the service,
swaps its Qdrant client for an in-memory one (the same engine, run in-process),
points the library at the folder you give it, indexes everything in it, and
serves on localhost. Data lives in a temporary directory that is deleted on
exit, so a run never touches your real library.

Needs the service's Python dependencies and the models in the Hugging Face
cache (the first run downloads them unless OFFLINE=1). Run it with the
project's virtualenv. Stop it with Ctrl-C.

Anything after ``--env`` is set in the service's environment first, so a
pipeline setting can be A/B'd without editing .env:

    ... serve_local.py CORPUS --env RERANK_MIN_SCORE=-1.0 FUSION_METHOD=rsf
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as response:
        return json.loads(response.read())


def _announce_when_indexed(port: int, expected: int, timeout: float) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        time.sleep(3)
        try:
            docs = _get(port, "/documents")["documents"]
        except Exception:
            continue
        states: dict = {}
        for doc in docs:
            states[doc.get("indexing_status")] = states.get(doc.get("indexing_status"), 0) + 1
        summary = ", ".join(f"{n} {state}" for state, n in sorted(states.items(), key=lambda x: str(x[0])))
        if summary != last:
            print(f"  indexing: {len(docs)}/{expected} documents seen -- {summary}", flush=True)
            last = summary
        finished = states.get("indexed", 0) + states.get("error", 0) + states.get("failed", 0)
        if len(docs) >= expected and finished >= len(docs):
            print(f"READY http://127.0.0.1:{port}  ({states.get('indexed', 0)} indexed)", flush=True)
            return
    print("TIMEOUT waiting for indexing", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("corpus", type=Path, help="folder of documents to index")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, help="keep service data here (default: a temp dir, deleted on exit)")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for indexing")
    parser.add_argument("--env", nargs="*", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)

    corpus = args.corpus.resolve()
    if not corpus.is_dir():
        raise SystemExit(f"{corpus} is not a folder")
    expected = sum(1 for p in corpus.rglob("*") if p.is_file() and not p.name.startswith("."))
    scratch = None
    data_dir = args.data_dir
    if data_dir is None:
        scratch = tempfile.mkdtemp(prefix="ai-librarian-eval-")
        data_dir = Path(scratch)

    os.environ.update({
        "DATA_DIR": str(data_dir), "INGEST_ROOT": str(corpus),
        # An immediate scan at start-up imports the corpus; the long interval
        # means no second scan runs mid-eval.
        "AUTO_INGEST_INTERVAL_SECONDS": "3600",
        "OLLAMA_URL": os.environ.get("OLLAMA_URL", ""),
        "SEARCH_LOG": "off",
    })
    os.environ.setdefault("OFFLINE", "1")
    for pair in args.env:
        key, _, value = pair.partition("=")
        os.environ[key] = value

    import uvicorn
    from qdrant_client import QdrantClient

    sys.path.insert(0, str(SERVICE_ROOT))
    import vector_store

    # The swap has to happen before `app` is imported: its search and indexing
    # helpers look the client up on the module at call time.
    vector_store.client = QdrantClient(":memory:")
    import app as service

    print(f"serving {expected} files from {corpus} on http://127.0.0.1:{args.port}", flush=True)
    threading.Thread(target=_announce_when_indexed, args=(args.port, expected, args.timeout), daemon=True).start()
    try:
        uvicorn.run(service.app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
