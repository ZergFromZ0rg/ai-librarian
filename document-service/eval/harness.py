#!/usr/bin/env python3
"""Retrieval evaluation harness for the document service.

Subcommands:

    python eval/harness.py review [-n 20]   # list recent real queries from the
                                            # search log with their current top
                                            # hits, as ready-to-edit judgment stubs
    python eval/harness.py capture "q" ...  # same stubs for queries given now
    python eval/harness.py score            # replay eval/queries.jsonl against
                                            # /search and report hit@k / recall@k
                                            # / MRR with 95% intervals (--fusion /
                                            # --dense-weight / --rerank-passage to
                                            # A/B the pipeline; --split test for the
                                            # held-out half; --save run.json)
    python eval/harness.py report run.json  # re-print a saved run's summary
    python eval/harness.py compare a.json b.json
                                            # paired diff of two saved runs: which
                                            # queries flipped, exact p-value
    python eval/harness.py calibrate        # fit the reranker's raw score to
                                            # P(relevant) on the judged set (only
                                            # useful if re-enabling RERANK_MIN_SCORE)

The eval set lives in ``eval/queries.jsonl`` -- one JSON object per line:

    {"id": "camus-absurd", "query": "what does Camus say about suicide",
     "relevant": [{"document": "myth-of-sisyphus.pdf", "page": 3,
                   "contains": "one truly serious philosophical problem"}]}
    {"id": "gate-gorilla", "query": "characteristics of a gorilla", "expect_empty": true}

Optional on any case: ``collection`` (a library's name) scopes the search to
that library, and any result from outside it is a SCOPE LEAK -- a hard failure.
A case with ``collection`` but neither ``relevant`` nor ``expect_empty`` is a
pure scope probe (give it ``"search": {"rerank_min_score": -100}`` so the
relevance gate cannot hide a leak by returning nothing). ``search`` passes
extra /search fields for one case. ``tags`` (a list of labels such as "excel" or "multilingual")
add a per-tag hit@k breakdown to the report.

``match`` ("any", the default, or "all") says how ``relevant`` is read. "any": one
acceptable passage in the top k is a hit. "all": every listed passage must be in
the top k -- for comparisons ("which is higher, A or B?") whose answer needs both.

Every case belongs to the ``dev`` or the ``test`` split, chosen by a hash of its
``id`` (a ``split`` field overrides). Tune on dev; quote the test number. A
test number you have looked at and then tuned against is a dev number.

``expect_empty`` cases are unanswerable queries. ``score`` reports whether the
RERANK_MIN_SCORE gate dropped them (informational -- the gate is a product
choice, not a release blocker); ``calibrate`` uses them to fit the floor.

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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

from evalstats import bootstrap_mean, mcnemar_exact, mean_ci, pct_ci, split_of, wilson

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
        # A reranked search on a CPU host (bge-base embed + bge-reranker over the
        # full candidate pool) can take 20 s+, so give it generous headroom.
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach {BASE_URL} ({exc.reason}). Is the service running? "
            f"Set AI_LIBRARIAN_URL to point elsewhere."
        ) from exc


def search(query: str, top_k: int, rerank: bool, collection_id: Optional[str] = None,
           overrides: Optional[dict] = None) -> list[dict]:
    # No rerank_k: let the server's RERANK_CANDIDATES decide the pool, so the
    # harness measures whatever a plain UI search does.
    payload = {"query": query, "top_k": top_k, "rerank": rerank, **SEARCH_OVERRIDES, **(overrides or {})}
    if collection_id:
        payload["collection_id"] = collection_id
    return _request("POST", "/search", payload)["results"]


class Scopes:
    """Resolves a case's ``collection`` name and knows which filenames belong
    to it, fetched once per run. Membership mirrors the server's rule: the
    recorded collection_id, or a source_path inside the collection's folder."""

    def __init__(self) -> None:
        self._collections: Optional[list[dict]] = None
        self._documents: Optional[list[dict]] = None

    def resolve(self, name: str) -> dict:
        if self._collections is None:
            self._collections = _request("GET", "/collections")["collections"]
        wanted = name.strip().lower()
        for collection in self._collections:
            if collection["name"].strip().lower() == wanted or collection["id"] == name:
                return collection
        known = ", ".join(repr(c["name"]) for c in self._collections) or "none"
        raise SystemExit(f"no library named {name!r} on this server (have: {known})")

    def members(self, collection: dict) -> set[str]:
        if self._documents is None:
            self._documents = _request("GET", "/documents")["documents"]
        return {doc["filename"].lower() for doc in self._documents
                if in_collection(doc, collection)}


def in_collection(doc: dict, collection: dict) -> bool:
    if doc.get("collection_id") == collection["id"]:
        return True
    if collection.get("builtin"):  # Notes: no folder, so membership is by id alone
        return False
    root = (collection.get("path") or "").strip("/")
    source = doc.get("source_path") or ""
    return bool(source) and (not root or source == root or source.startswith(root + "/"))


def scope_leaks(results: list[dict], members: set[str]) -> list[str]:
    """Filenames of results that fall outside the searched library."""
    return sorted({str(r.get("document") or "") for r in results
                   if str(r.get("document") or "").lower() not in members})


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

    # Whitespace is collapsed on both sides: a phrase that a PDF line wrap split
    # across two lines is still the same phrase.
    needle = " ".join(str(judged.get("contains", "")).lower().split())
    if needle:
        haystack = " ".join(
            " ".join(str(result.get(field) or "").split())
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


def evaluate(cases: list[dict], rerank: bool) -> list[dict]:
    """Replay every case against /search and return one plain record per case.

    No metrics are computed here: records are what ``--save`` writes and what
    ``compare`` reads back, so a run can be re-summarised or diffed later
    without touching the server again.
    """
    top_k = CUTOFFS[0]
    records: list[dict] = []
    scopes = Scopes()

    for case in cases:
        case_id = case.get("id") or case.get("query", "?")
        record: dict = {
            "id": case_id,
            "split": split_of(case_id, case.get("split")),
            "tags": list(case.get("tags") or []),
            "kind": "skipped",
            "note": "",
        }
        records.append(record)
        collection = scopes.resolve(case["collection"]) if case.get("collection") else None
        results = search(case["query"], top_k, rerank, collection["id"] if collection else None,
                         case.get("search"))
        scope_note = ""
        if collection is not None:
            leaked = scope_leaks(results, scopes.members(collection))
            record["scoped"] = True
            if leaked:
                record["scope_leak"] = leaked
                scope_note = f"  SCOPE LEAK outside {collection['name']!r}: {', '.join(leaked)}"
            elif not case.get("relevant") and not case.get("expect_empty"):
                record.update(kind="scope", note=f"scope OK -- {len(results)} results, all in {collection['name']!r}")
                continue
        if scope_note and not case.get("relevant") and not case.get("expect_empty"):
            record.update(kind="scope", note=scope_note.strip())
            continue

        if case.get("expect_empty"):
            # Unanswerable queries. With the RERANK_MIN_SCORE gate on they should
            # come back empty; report whichever way it went (informational -- the
            # gate is a soft product choice, not a release blocker).
            top = max((r.get("rerank_score") or 0.0 for r in results), default=0.0)
            record.update(kind="gate", leaked=len(results), top_score=top)
            if results:
                record["note"] = f"unanswerable -- LEAKED {len(results)} (top {top:.2f})" + scope_note
            else:
                record["note"] = "unanswerable -- gated out" + scope_note
            continue

        judged = case.get("relevant") or []
        if not judged:
            record["note"] = "skipped (no `relevant` and not `expect_empty`)"
            continue

        ranks = [first_hit_rank(entry, results) for entry in judged]
        require_all = str(case.get("match", "any")).lower() == "all"
        record.update(kind="judged", ranks=ranks, require_all=require_all, best=effective_rank(ranks, require_all))
        found_5 = sum(1 for r in ranks if r is not None and r <= 5)
        mark = "hit" if (record["best"] is not None and record["best"] <= 5) else "MISS"
        detail = ""
        if len(judged) > 1:
            detail = f"  {'all-of ' if require_all else ''}recall@5 {found_5}/{len(judged)}"
        record["note"] = f"{mark}@5  first@{record['best'] or '-'}{detail}{scope_note}"
    return records


def effective_rank(ranks: list[Optional[int]], require_all: bool = False) -> Optional[int]:
    """The rank a case is scored at. Normally the first acceptable passage
    ("any of these is an answer"). With ``match: all`` -- a comparison that
    needs both sources -- it is the rank of the *last* required passage, and a
    single missing one means the case is a miss at every cutoff."""
    found = [r for r in ranks if r is not None]
    if not found:
        return None
    if require_all:
        return max(found) if len(found) == len(ranks) else None
    return min(found)


def summarise(records: list[dict]) -> dict:
    """Pure metrics over ``evaluate`` records (the part tests exercise)."""
    # "All-of" cases (comparisons that need two sources) are scored apart: the
    # best possible rank for k required passages is k, so folding them into
    # hit@1 and MRR would drag both down for a reason that is not retrieval.
    every = [r for r in records if r["kind"] == "judged"]
    judged = [r for r in every if not r.get("require_all")]
    all_of = [r for r in every if r.get("require_all")]
    gates = [r for r in records if r["kind"] == "gate"]
    scoped = [r for r in records if r.get("scoped")]

    def block(rows: list[dict]) -> dict:
        n = len(rows)
        out: dict = {"n": n, "hits": {}, "recall": {}, "rr": []}
        for k in CUTOFFS:
            out["hits"][k] = sum(1 for r in rows if r["best"] is not None and r["best"] <= k)
            out["recall"][k] = (sum(
                sum(1 for x in r["ranks"] if x is not None and x <= k) / len(r["ranks"]) for r in rows
            ) / n) if n else 0.0
        out["rr"] = [(1.0 / r["best"]) if r["best"] else 0.0 for r in rows]
        return out

    summary = {
        "overall": block(judged),
        "all_of": block(all_of),
        "by_split": {s: block([r for r in judged if r["split"] == s]) for s in ("dev", "test")},
        "by_tag": {},
        "gate": {"total": len(gates), "gated": sum(1 for g in gates if not g["leaked"]),
                 "leaked": sum(1 for g in gates if g["leaked"])},
        "scope": {"checked": len(scoped), "leaked": sum(1 for r in scoped if r.get("scope_leak"))},
    }
    tags = sorted({t for r in judged for t in r["tags"]})
    for tag in tags:
        summary["by_tag"][tag] = block([r for r in judged if tag in r["tags"]])
    gate_tags = sorted({t for g in gates for t in g["tags"]})
    summary["gate_by_tag"] = {
        tag: {"total": sum(1 for g in gates if tag in g["tags"]),
              "gated": sum(1 for g in gates if tag in g["tags"] and not g["leaked"])}
        for tag in gate_tags
    }
    return summary


def exit_code(records: list[dict]) -> int:
    """Non-zero when not a single judged query found an answer, or when a
    library-scoped search returned anything from outside that library (a
    correctness bug, unlike a soft ranking miss or a gate leak)."""
    judged = [r for r in records if r["kind"] == "judged"]
    nothing_found = bool(judged) and all(r["best"] is None or r["best"] > CUTOFFS[0] for r in judged)
    return 1 if nothing_found or any(r.get("scope_leak") for r in records) else 0


def _row(label: str, b: dict, width: int) -> str:
    n = b["n"]
    return (f"    {label.ljust(width)}  n={n:<4} hit@1 {pct_ci(b['hits'][1], n):<18} "
            f"hit@5 {pct_ci(b['hits'][5], n):<18} MRR {mean_ci(b['rr'])}")


def print_report(records: list[dict], header: str = "") -> None:
    width = max((len(r["id"]) for r in records), default=0)
    for record in records:
        print(f"  {record['id'].ljust(width)}   {record['note']}")

    summary = summarise(records)
    overall = summary["overall"]
    print("\n" + "-" * (width + 40))
    if header:
        print(header)
    if SEARCH_OVERRIDES:
        print("  overrides: " + "  ".join(f"{k}={v}" for k, v in SEARCH_OVERRIDES.items()))
    n = overall["n"]
    if n:
        print(f"  {n} judged queries   (95% intervals; the line below the table is for all-of cases)"
              if summary["all_of"]["n"] else f"  {n} judged queries   (95% intervals)")
        print(f"    {'':8}{'rate':>6}   interval")
        for k in sorted(CUTOFFS):
            low, high = wilson(overall["hits"][k], n)
            print(f"    hit@{k:<4}{overall['hits'][k] / n:>6.2f}   {low:.2f}-{high:.2f}")
        mean, low, high = bootstrap_mean(overall["rr"])
        print(f"    MRR     {mean:>6.2f}   {low:.2f}-{high:.2f}")
        print("    recall:  " + "   ".join(f"@{k} {overall['recall'][k]:.2f}" for k in sorted(CUTOFFS)))
        splits = summary["by_split"]
        if splits["dev"]["n"] and splits["test"]["n"]:
            print("  by split  (tune on dev; quote test):")
            for name in ("dev", "test"):
                print(_row(name, splits[name], 4))
    all_of = summary["all_of"]
    if all_of["n"]:
        print(f"  all-of queries (every source must rank): n={all_of['n']}   "
              + "   ".join(f"top {k}: {pct_ci(all_of['hits'][k], all_of['n'])}" for k in (5, 10)))
    if summary["by_tag"]:
        print("  by tag:")
        tag_width = max(len(t) for t in summary["by_tag"])
        for tag in sorted(summary["by_tag"]):
            print(_row(tag, summary["by_tag"][tag], tag_width))
    scope = summary["scope"]
    if scope["checked"]:
        print(f"  library scoping:  {scope['checked'] - scope['leaked']}/{scope['checked']} scoped queries "
              "stayed inside their library" + (f", {scope['leaked']} LEAKED" if scope["leaked"] else ""))
    gate = summary["gate"]
    if gate["total"]:
        print(f"  unanswerable probes:  {pct_ci(gate['gated'], gate['total'])} gated out"
              + (f", {gate['leaked']} LEAKED" if gate["leaked"] else ""))
        for tag, g in sorted(summary["gate_by_tag"].items()):
            print(f"    {tag}: {g['gated']}/{g['total']} gated out")
    if not n and not gate["total"]:
        print("  nothing to score yet -- add cases to eval/queries.jsonl")


def select_cases(cases: list[dict], split: str = "all", tag: Optional[str] = None) -> list[dict]:
    """Filter by held-out split and/or tag before any query is sent."""
    chosen = []
    for case in cases:
        case_id = case.get("id") or case.get("query", "?")
        if split != "all" and split_of(case_id, case.get("split")) != split:
            continue
        if tag and tag not in (case.get("tags") or []):
            continue
        chosen.append(case)
    return chosen


def _server_config() -> dict:
    """The server settings that decide what a run measures (best effort)."""
    try:
        config = _request("GET", "/config")
    except SystemExit:
        return {}
    keep = ("embedding_model", "reranker_model", "rerank_passage", "rerank_min_score", "fusion_method",
            "pipeline_version", "index_schema_version")
    return {key: config[key] for key in keep if key in config}


def score(cases: list[dict], rerank: bool, save: Optional[Path] = None, source: str = "") -> int:
    records = evaluate(cases, rerank)
    print_report(records)
    if save is not None:
        payload = {
            "meta": {"url": BASE_URL, "queries": source, "rerank": rerank, "overrides": dict(SEARCH_OVERRIDES),
                     "server": _server_config(), "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
            "records": records,
        }
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  saved run -> {save}")
    return exit_code(records)


def report(path: Path) -> int:
    """Re-print the summary of a run saved with ``score --save`` (no server needed)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    header = f"  run saved {meta.get('saved_at', '?')} from {meta.get('queries', '?')}  {meta.get('server') or ''}"
    print_report(data["records"], header)
    return exit_code(data["records"])


def compare(path_a: Path, path_b: Path, metric_cutoffs: tuple[int, ...] = (1, 5)) -> int:
    """Paired comparison of two saved runs over the cases both contain.

    "B scored 0.03 higher" is meaningless without the question "on how many
    queries, and could it be luck?". This lines the runs up query by query,
    counts the ones that flipped each way, and gives an exact McNemar p-value
    plus a bootstrap interval on the mean reciprocal-rank change.
    """
    runs = []
    for path in (path_a, path_b):
        data = json.loads(path.read_text(encoding="utf-8"))
        # All-of (comparison) cases can never hit@1, so they are left out of
        # the rate comparison and shown on their own line below.
        runs.append({r["id"]: r for r in data["records"] if r["kind"] == "judged"})
        meta = data.get("meta", {})
        print(f"  {path.name}: {meta.get('overrides') or 'no overrides'}  {meta.get('server') or ''}")
    a, b = runs
    shared_all = sorted(set(a) & set(b))
    shared = [c for c in shared_all if not a[c].get("require_all") and not b[c].get("require_all")]
    all_of = [c for c in shared_all if c not in set(shared)]
    if not shared:
        print("  the two runs share no judged queries")
        return 1
    skipped = len(set(a) ^ set(b))
    print(f"\n  {len(shared)} queries in both runs" + (f" ({skipped} only in one, ignored)" if skipped else ""))
    if not shared:
        print("  no any-of queries to compare")
        return 1

    for k in metric_cutoffs:
        def hit(run: dict, case_id: str, k: int = k) -> bool:
            best = run[case_id]["best"]
            return best is not None and best <= k
        only_a = [c for c in shared if hit(a, c) and not hit(b, c)]
        only_b = [c for c in shared if hit(b, c) and not hit(a, c)]
        rate_a = sum(hit(a, c) for c in shared) / len(shared)
        rate_b = sum(hit(b, c) for c in shared) / len(shared)
        p = mcnemar_exact(len(only_a), len(only_b))
        verdict = "no clear difference" if p >= 0.05 else ("B better" if len(only_b) > len(only_a) else "A better")
        print(f"  hit@{k}:  A {rate_a:.2f}   B {rate_b:.2f}   only A right {len(only_a)}, only B right "
              f"{len(only_b)}   p={p:.3f}  -> {verdict}")
        for label, ids in (("A only", only_a), ("B only", only_b)):
            if ids:
                shown = ", ".join(ids[:12]) + (" ..." if len(ids) > 12 else "")
                print(f"      {label}: {shown}")

    deltas = [((1.0 / b[c]["best"]) if b[c]["best"] else 0.0) - ((1.0 / a[c]["best"]) if a[c]["best"] else 0.0)
              for c in shared]
    mean, low, high = bootstrap_mean(deltas)
    clear = "" if low <= 0 <= high else "  (interval excludes 0)"
    print(f"  MRR change B-A:  {mean:+.3f}   95% interval {low:+.3f} to {high:+.3f}{clear}")
    if all_of:
        def got(run: dict) -> int:
            return sum(1 for c in all_of if run[c]["best"] is not None and run[c]["best"] <= 5)
        print(f"  all-of queries ({len(all_of)}): every source in the top 5   A {got(a)}   B {got(b)}")
    return 0


def _isotonic(pairs: list[tuple[float, int]]) -> list[tuple[float, float]]:
    """Pool-adjacent-violators fit: returns (min_score, P(relevant)) per block,
    a monotone non-decreasing step function over score."""
    blocks: list[list[float]] = []  # [label_sum, count, min_score]
    for score, label in sorted(pairs):
        blocks.append([float(label), 1.0, score])
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            merged = blocks.pop()
            blocks[-1][0] += merged[0]
            blocks[-1][1] += merged[1]
    return [(score, total / count) for total, count, score in blocks]


def _prob_at(curve: list[tuple[float, float]], score: float) -> float:
    prob = curve[0][1] if curve else 0.0
    for block_score, block_prob in curve:
        if score >= block_score:
            prob = block_prob
        else:
            break
    return prob


def calibrate(cases: list[dict], _rerank: bool) -> int:
    """Fit the reranker's raw score to P(relevant) using the judged set, and
    recommend a RERANK_MIN_SCORE cutoff."""
    saved = dict(SEARCH_OVERRIDES)
    SEARCH_OVERRIDES["rerank_min_score"] = -100  # open the gate: see every score
    labelled: list[tuple[float, bool, bool]] = []  # (score, is_relevant, is_gate_case)
    try:
        for case in cases:
            gate = bool(case.get("expect_empty"))
            judged = case.get("relevant") or []
            if not gate and not judged:
                continue
            for result in search(case["query"], CUTOFFS[0], True):
                score = result.get("rerank_score")
                if score is None:
                    continue
                relevant = (not gate) and any(result_matches(j, result) for j in judged)
                labelled.append((float(score), relevant, gate))
    finally:
        SEARCH_OVERRIDES.clear()
        SEARCH_OVERRIDES.update(saved)

    positives = [s for s, rel, _ in labelled if rel]
    negatives = [s for s, rel, _ in labelled if not rel]
    gate_scores = [s for s, _, g in labelled if g]
    if not positives or not negatives:
        print("  need both matched and unmatched reranked passages -- run `score` first")
        return 1

    curve = _isotonic([(s, 1 if rel else 0) for s, rel, _ in labelled])

    print(f"  {len(positives)} relevant / {len(negatives)} not "
          f"({len(gate_scores)} from expected-empty queries)\n")
    print("  raw score -> P(relevant), isotonic fit:")
    lo, hi = min(s for s, *_ in labelled), max(s for s, *_ in labelled)
    for i in range(9):
        s = lo + (hi - lo) * i / 8
        print(f"    {s:+7.2f}  ->  {_prob_at(curve, s):.2f}")

    print("\n  cutoff        kept    of which     recall of   gate")
    print("  (min_score)   hits    off-topic    relevant    leaks")
    # ms-marco-MiniLM scores are logits spanning ~10+ points; bge-reranker-base
    # scores are a 0..1 probability. Step finely enough to resolve the floor in
    # either range without printing hundreds of rows.
    step = 0.05 if (hi - lo) <= 3 else 0.5
    candidates = sorted({round(s / step) * step for s, *_ in labelled})
    floor = None
    balanced = None
    balanced_j = -1.0
    for cutoff in candidates:
        kept_pos = sum(1 for s in positives if s >= cutoff)
        kept_neg = sum(1 for s in negatives if s >= cutoff)
        leaks = sum(1 for s in gate_scores if s >= cutoff)
        recall = kept_pos / len(positives)
        noise = kept_neg / (kept_pos + kept_neg) if (kept_pos + kept_neg) else 0.0
        flag = ""
        if leaks == 0:
            if floor is None:
                floor = cutoff
                flag = "  <- floor (no gate leaks)"
            if recall - noise > balanced_j:
                balanced_j, balanced = recall - noise, cutoff
        print(f"  {cutoff:+7.2f}      {kept_pos + kept_neg:4d}    {noise:6.0%}       "
              f"{recall:6.0%}      {leaks:3d}{flag}")

    if floor is None:
        print("\n  every cutoff leaks an expected-empty query -- the reranker cannot "
              "tell them from real answers. A stronger RERANK_MODEL is the fix.")
        return 0
    def keep(cutoff: float) -> float:
        return sum(1 for s in positives if s >= cutoff) / len(positives)

    print(f"\n  RERANK_MIN_SCORE floor    {floor:+.2f}  "
          f"(P(rel) ~ {_prob_at(curve, floor):.2f}, keeps {keep(floor):.0%} of known answers)")
    if balanced is not None and balanced != floor:
        print(f"  best noise/recall trade  {balanced:+.2f}  "
              f"(P(rel) ~ {_prob_at(curve, balanced):.2f}, keeps {keep(balanced):.0%})")
    return 0


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
    p_score.add_argument("--rerank-passage", choices=["group", "matched"],
                         help="override what text the reranker scores")
    p_score.add_argument("--rerank-min-score", type=float,
                         help="override the server's RERANK_MIN_SCORE gate for this run")
    p_score.add_argument("--split", choices=["all", "dev", "test"], default="all",
                         help="score only the dev or the held-out test cases (default all)")
    p_score.add_argument("--tag", help="score only cases carrying this tag")
    p_score.add_argument("--save", type=Path, metavar="FILE",
                         help="write the per-query results as JSON, for `compare` later")

    p_report = sub.add_parser("report", help="re-print the summary of a saved run")
    p_report.add_argument("run", type=Path)

    p_compare = sub.add_parser("compare", help="paired comparison of two runs saved with `score --save`")
    p_compare.add_argument("run_a", type=Path)
    p_compare.add_argument("run_b", type=Path)

    p_cal = sub.add_parser("calibrate", help="fit reranker score -> P(relevant), recommend a cutoff")
    p_cal.add_argument("--queries", type=Path, default=QUERIES_PATH)
    p_cal.add_argument("--rerank-passage", choices=["group", "matched"],
                       help="override what text the reranker scores")

    p_review = sub.add_parser("review", help="recent logged queries -> judgment stubs")
    p_review.add_argument("-n", "--limit", type=int, default=20)
    p_review.add_argument("--no-rerank", action="store_true")

    p_capture = sub.add_parser("capture", help="run queries now -> judgment stubs (any server)")
    p_capture.add_argument("query", nargs="+", help="one or more query strings")
    p_capture.add_argument("--no-rerank", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    rerank = not getattr(args, "no_rerank", False)
    BASE_URL = args.url.rstrip("/")
    if getattr(args, "fusion", None):
        SEARCH_OVERRIDES["fusion"] = args.fusion
    if getattr(args, "dense_weight", None) is not None:
        SEARCH_OVERRIDES["dense_weight"] = args.dense_weight
    if getattr(args, "rerank_passage", None):
        SEARCH_OVERRIDES["rerank_passage"] = args.rerank_passage
    if getattr(args, "rerank_min_score", None) is not None:
        SEARCH_OVERRIDES["rerank_min_score"] = args.rerank_min_score

    if args.command == "score":
        cases = select_cases(load_cases(args.queries), args.split, args.tag)
        if not cases:
            raise SystemExit("no cases match --split/--tag")
        return score(cases, rerank, args.save, args.queries.name)
    if args.command == "compare":
        return compare(args.run_a, args.run_b)
    if args.command == "report":
        return report(args.run)
    if args.command == "calibrate":
        return calibrate(load_cases(args.queries), rerank)
    if args.command == "review":
        return review(args.limit, rerank)
    if args.command == "capture":
        return capture(args.query, rerank)
    return 2


if __name__ == "__main__":
    sys.exit(main())
