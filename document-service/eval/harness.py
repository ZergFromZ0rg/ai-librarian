#!/usr/bin/env python3
"""Retrieval evaluation harness for the document service.

Two subcommands:

    python eval/harness.py review [-n 20]   # list recent real queries from the
                                            # search log with their current top
                                            # hits, as ready-to-edit judgment stubs
    python eval/harness.py score            # replay eval/queries.jsonl against
                                            # /search and report hit@k / recall@k
                                            # / MRR / relevance-gate accuracy
                                            # (--fusion / --dense-weight to A/B
                                            # the dense+sparse merge)

The eval set lives in ``eval/queries.jsonl`` -- one JSON object per line:

    {"id": "camus-absurd", "query": "what does Camus say about suicide",
     "relevant": [{"document": "myth-of-sisyphus.pdf", "page": 3,
                   "contains": "one truly serious philosophical problem"}]}
    {"id": "gate-gorilla", "query": "characteristics of a gorilla", "expect_empty": true}

A ``relevant`` entry counts as retrieved when the result's filename matches,
the judged ``page`` falls inside the result's [page, page_end] span, and (when
given) ``contains`` appears in the returned passage. Judgments are keyed by
filename + page on purpose: chunk_id and group_id are regenerated on every
re-index, but the filename and PDF-position page are stable.

Config via environment:
    AI_LIBRARIAN_URL   base URL of the API   (default http://127.0.0.1:8000)
    APP_TOKEN          bearer token, if the API requires one
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_URL = "http://127.0.0.1:8000"
# Reach the API directly on :8000, or through the UI's nginx proxy at
# http://<host>:3100/api (that path also injects APP_TOKEN for you).
BASE_URL = os.environ.get("AI_LIBRARIAN_URL", DEFAULT_URL).rstrip("/")
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
QUERIES_PATH = Path(__file__).with_name("queries.jsonl")

# Ranks we report recall at. Top of this list is how many hits we ask for.
CUTOFFS = [10, 5, 3, 1]

# Per-request overrides set from the CLI (score --fusion / --dense-weight), so a
# fusion method can be A/B'd against the eval set without redeploying the server.
SEARCH_OVERRIDES: dict = {}


def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if APP_TOKEN:
        req.add_header("Authorization", f"Bearer {APP_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach {BASE_URL} ({exc.reason}). Is the service running? "
            f"Set AI_LIBRARIAN_URL to point elsewhere."
        ) from exc


def search(query: str, top_k: int, rerank: bool) -> list[dict]:
    # No rerank_k: let the server's RERANK_CANDIDATES decide the pool, so the
    # harness measures whatever a plain UI search does.
    payload = {"query": query, "top_k": top_k, "rerank": rerank, **SEARCH_OVERRIDES}
    return _request("POST", "/search", payload)["results"]


def load_cases(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"no eval set at {path}")
    cases = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            cases.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_number}: invalid JSON ({exc})") from exc
    return cases


def result_matches(judged: dict, result: dict) -> bool:
    """True when `result` satisfies the `judged` relevance entry."""
    want_doc = str(judged.get("document", "")).strip().lower()
    got_doc = str(result.get("document") or "").strip().lower()
    if want_doc and want_doc != got_doc:
        return False

    page = judged.get("page")
    if page is not None:
        low = result.get("page") or 0
        high = result.get("page_end") or low
        if not (low <= page <= high):
            return False

    needle = str(judged.get("contains", "")).strip().lower()
    if needle:
        haystack = " ".join(
            str(result.get(field) or "")
            for field in ("text", "matched", "lead_in")
        ).lower()
        if needle not in haystack:
            return False
    return True


def first_hit_rank(judged: dict, results: list[dict]) -> Optional[int]:
    for rank, result in enumerate(results, 1):
        if result_matches(judged, result):
            return rank
    return None


def score(cases: list[dict], rerank: bool) -> int:
    top_k = CUTOFFS[0]
    # hit@k: did *any* acceptable passage land in the top k (the number that
    # tracks whether the user got a good answer).
    # recall@k: of all the acceptable passages, what fraction were surfaced.
    hit_totals = {k: 0 for k in CUTOFFS}
    recall_totals = {k: 0.0 for k in CUTOFFS}
    scored_cases = 0
    mrr_total = 0.0
    gate_pass = gate_total = 0
    rows: list[tuple[str, str]] = []

    for case in cases:
        case_id = case.get("id") or case.get("query", "?")
        results = search(case["query"], top_k, rerank)

        if case.get("expect_empty"):
            gate_total += 1
            ok = len(results) == 0
            gate_pass += ok
            rows.append((case_id, "gate PASS" if ok else f"gate FAIL ({len(results)} returned)"))
            continue

        judged = case.get("relevant") or []
        if not judged:
            rows.append((case_id, "skipped (no `relevant` and not `expect_empty`)"))
            continue

        ranks = [first_hit_rank(entry, results) for entry in judged]
        scored_cases += 1
        best = min((r for r in ranks if r is not None), default=None)
        mrr_total += (1.0 / best) if best else 0.0
        for k in CUTOFFS:
            if best is not None and best <= k:
                hit_totals[k] += 1
            found = sum(1 for r in ranks if r is not None and r <= k)
            recall_totals[k] += found / len(judged)
        found_5 = sum(1 for r in ranks if r is not None and r <= 5)
        mark = "hit" if (best is not None and best <= 5) else "MISS"
        detail = f"  recall@5 {found_5}/{len(judged)}" if len(judged) > 1 else ""
        rows.append((case_id, f"{mark}@5  first@{best or '-'}{detail}"))

    width = max((len(cid) for cid, _ in rows), default=0)
    for case_id, note in rows:
        print(f"  {case_id.ljust(width)}   {note}")

    print("\n" + "-" * (width + 40))
    if SEARCH_OVERRIDES:
        print("  overrides: " + "  ".join(f"{k}={v}" for k, v in SEARCH_OVERRIDES.items()))
    if scored_cases:
        hit = "   ".join(f"hit@{k} {hit_totals[k] / scored_cases:.2f}" for k in sorted(CUTOFFS))
        rec = "   ".join(f"recall@{k} {recall_totals[k] / scored_cases:.2f}" for k in sorted(CUTOFFS))
        print(f"  {scored_cases} judged queries")
        print(f"    hit rate:  {hit}")
        print(f"    recall:    {rec}")
        print(f"    MRR:       {mrr_total / scored_cases:.2f}")
    if gate_total:
        print(f"  relevance gate:  {gate_pass}/{gate_total} expected-empty queries returned nothing")
    if not scored_cases and not gate_total:
        print("  nothing to score yet -- add cases to eval/queries.jsonl")
        return 0

    # Non-zero exit when the gate leaks or not a single query found an answer,
    # so this can guard a release.
    failed = (gate_total and gate_pass < gate_total) or (
        scored_cases and hit_totals[CUTOFFS[0]] == 0
    )
    return 1 if failed else 0


def _snippet(result: dict, length: int) -> str:
    return " ".join((result.get("matched") or result.get("text") or "").split())[:length]


def print_stub(query: str, results: list[dict]) -> None:
    """Print a query's top hits plus a ready-to-edit judgment line."""
    print(f"## {query!r}  ->  {len(results)} results")
    for result in results:
        print(f"#   {result.get('document')}  p.{result.get('page')}   {_snippet(result, 110)!r}")
    stub = {
        "id": "-".join(query.lower().split()[:4]) or "query",
        "query": query,
        "relevant": [
            {"document": r.get("document"), "page": r.get("page"), "contains": _snippet(r, 60)}
            for r in results[:3]
        ],
    }
    print(json.dumps(stub, ensure_ascii=False))
    print()


_STUB_HELP = (
    "# Paste a block into eval/queries.jsonl, drop the hits that aren't good\n"
    "# answers, tighten each `contains` to one distinctive phrase. Replace\n"
    '# `relevant` with {"expect_empty": true} for a query nothing should answer.\n'
)


def capture(queries: list[str], rerank: bool) -> int:
    print(_STUB_HELP)
    for query in queries:
        print_stub(query, search(query, CUTOFFS[0], rerank))
    return 0


def review(limit: int, rerank: bool) -> int:
    try:
        log = _request("GET", f"/admin/search-log?limit={limit}")
    except SystemExit as exc:
        if "404" in str(exc):
            raise SystemExit(
                "this service has no search log (older build). Update it, or use:\n"
                "    python eval/harness.py capture \"your query here\" \"another query\""
            ) from exc
        raise
    if not log.get("enabled"):
        raise SystemExit("search logging is disabled (SEARCH_LOG=off) -- nothing to review")

    seen: set[str] = set()
    queries = []
    for entry in log.get("entries", []):
        q = entry.get("query", "")
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    if not queries:
        print("no queries in the search log yet -- ask some questions first")
        return 0

    print(f"# {len(queries)} recent quer{'y' if len(queries) == 1 else 'ies'} from the search log\n")
    print(_STUB_HELP)
    for query in queries:
        print_stub(query, search(query, CUTOFFS[0], rerank))
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    global BASE_URL

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--url",
        default=BASE_URL,
        help=f"API base URL, or the UI proxy at http://<host>:3100/api (default {BASE_URL})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_score = sub.add_parser("score", help="replay queries.jsonl and report metrics")
    p_score.add_argument("--no-rerank", action="store_true", help="test raw vector order")
    p_score.add_argument("--queries", type=Path, default=QUERIES_PATH)
    p_score.add_argument("--fusion", choices=["rrf", "dbsf", "rsf"],
                         help="override the server's dense/sparse fusion method")
    p_score.add_argument("--dense-weight", type=float,
                         help="rsf only: 0..1 weight on the semantic list")

    p_review = sub.add_parser("review", help="recent logged queries -> judgment stubs")
    p_review.add_argument("-n", "--limit", type=int, default=20)
    p_review.add_argument("--no-rerank", action="store_true")

    p_capture = sub.add_parser("capture", help="run queries now -> judgment stubs (any server)")
    p_capture.add_argument("query", nargs="+", help="one or more query strings")
    p_capture.add_argument("--no-rerank", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    rerank = not args.no_rerank
    BASE_URL = args.url.rstrip("/")
    if getattr(args, "fusion", None):
        SEARCH_OVERRIDES["fusion"] = args.fusion
    if getattr(args, "dense_weight", None) is not None:
        SEARCH_OVERRIDES["dense_weight"] = args.dense_weight

    if args.command == "score":
        return score(load_cases(args.queries), rerank)
    if args.command == "review":
        return review(args.limit, rerank)
    if args.command == "capture":
        return capture(args.query, rerank)
    return 2


if __name__ == "__main__":
    sys.exit(main())
