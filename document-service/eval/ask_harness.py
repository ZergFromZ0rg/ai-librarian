#!/usr/bin/env python3
"""Answer-quality evaluation harness for Ask mode.

The retrieval harness (``harness.py``) checks that ``/search`` surfaces the right
passages. This one checks the next stage: does ``/ask`` turn those passages into
a good answer -- grounded, cited, on-topic, and refusing when the library has
nothing.

Subcommands:

    python eval/ask_harness.py capture "q" ...   # run questions now, print the
                                                 # answer + sources + a stub for
                                                 # eval/answers.jsonl
    python eval/ask_harness.py review [-n 20]    # recent real Ask questions from
                                                 # the search log, as stubs
    python eval/ask_harness.py score             # replay eval/answers.jsonl
                                                 # against /ask and report

Deterministic checks always run (no LLM needed):

    citations present, every [n] in range, how much of the context was cited,
    `must_mention` / `must_not_mention` substrings, `must_cite` passages present
    and cited, and -- for `expect_refusal` cases -- that the answer declines.

Optional on any case: ``collection`` (a library's name) scopes the question to
that library -- a source from outside it is a hard failure -- and ``tags``
(labels such as "excel") add a per-tag pass count to the report.

Add ``--judge <provider:model>`` for an LLM judge that also scores faithfulness,
relevance, citation accuracy, and `key_points` coverage. The judge talks to
Ollama (``--judge-url http://host:11434``) or the Cloud provider API
(``--judge model cloud-...`` + ``REMOTE_API_KEY``).

The eval set lives in ``eval/answers.jsonl`` -- one JSON object per line:

    {"id": "meme-def", "question": "What is a meme?",
     "must_mention": ["imitation"],
     "key_points": ["information copied person to person", "spreads by imitation"],
     "must_cite": [{"document": "The Meme Machine ....pdf", "page": 23}]}
    {"id": "carburetor", "question": "how do I rebuild a carburetor",
     "expect_refusal": true}

Config via environment:
    AI_LIBRARIAN_URL   base URL of the API   (default http://127.0.0.1:8000)
    APP_TOKEN          bearer token, if the API requires one
    REMOTE_API_KEY  only for --judge model cloud-provider:...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_URL = "http://127.0.0.1:8000"
BASE_URL = os.environ.get("AI_LIBRARIAN_URL", DEFAULT_URL).rstrip("/")
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
ANSWERS_PATH = Path(__file__).with_name("answers.jsonl")

# Ask requests -- especially thorough mode on a CPU host -- run for minutes.
ASK_TIMEOUT = 600

_CITE_RE = re.compile(r"\[(\d+)\]")
# Phrasings the answer uses when the library has nothing (see app._ASK_NO_ANSWER
# and generation._REDUCE_SYSTEM / _SYSTEM_PROMPT). Matched case-insensitively.
_REFUSAL_MARKERS = (
    "couldn't find anything",
    "could not find anything",
    "don't contain",
    "do not contain",
    "does not contain",
    "doesn't contain",
    "not in your library",
    "nothing in your library",
    "no information",
    "isn't covered",
    "is not covered",
    "not covered in",
    "the sources do not",
    "the notes do not",
)


def _request(method: str, path: str, body: Optional[dict] = None, timeout: int = 120):
    req = urllib.request.Request(f"{BASE_URL}{path}", method=method)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if APP_TOKEN:
        req.add_header("Authorization", f"Bearer {APP_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} -> {exc.code}: {exc.read().decode(errors='replace')}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach {BASE_URL} ({exc.reason}). Is the service running? "
            f"Set AI_LIBRARIAN_URL to point elsewhere."
        ) from exc


def parse_sse(body: str) -> list[dict]:
    """The JSON payload of every ``data:`` frame in an SSE response body."""
    events = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                pass
    return events


def ask(question: str, mode: str = "quick", model: Optional[str] = None,
        provider_keys: Optional[dict] = None, collection_id: Optional[str] = None) -> dict:
    """POST /ask, consume the stream, return {answer, sources, documents,
    relevant_count, model, error}."""
    payload: dict = {"question": question, "mode": mode}
    if collection_id:
        payload["collection_id"] = collection_id
    if model:
        payload["model"] = model
    if provider_keys:
        payload["provider_keys"] = provider_keys
    events = parse_sse(_request("POST", "/ask", payload, timeout=ASK_TIMEOUT))
    answer = "".join(e.get("text", "") for e in events if e.get("type") == "token")
    src = next((e for e in events if e.get("type") == "sources"), {})
    err = next((e.get("detail") for e in events if e.get("type") == "error"), None)
    return {
        "answer": answer.strip(),
        "sources": src.get("results") or [],
        "documents": src.get("documents"),
        "relevant_count": src.get("relevant_count"),
        "model": src.get("model"),
        "error": err,
    }


def cited_numbers(answer: str) -> set[int]:
    return {int(n) for n in _CITE_RE.findall(answer)}


def looks_like_refusal(answer: str) -> bool:
    low = answer.lower()
    return any(marker in low for marker in _REFUSAL_MARKERS)


def source_matches(want: dict, source: dict) -> bool:
    doc = str(want.get("document", "")).strip().lower()
    if doc and doc != str(source.get("document") or "").strip().lower():
        return False
    page = want.get("page")
    if page is not None:
        low = source.get("page") or 0
        high = source.get("page_end") or low
        if not (low <= page <= high):
            return False
    return True


def resolve_collection(name: str) -> dict:
    """A library by name (or id), plus the lower-cased filenames it holds --
    membership mirrors the server: recorded collection_id, or a source_path
    inside the library's folder."""
    collections = json.loads(_request("GET", "/collections"))["collections"]
    wanted = name.strip().lower()
    collection = next((c for c in collections
                       if c["name"].strip().lower() == wanted or c["id"] == name), None)
    if collection is None:
        known = ", ".join(repr(c["name"]) for c in collections) or "none"
        raise SystemExit(f"no library named {name!r} on this server (have: {known})")
    root = (collection.get("path") or "").strip("/")
    members = set()
    for doc in json.loads(_request("GET", "/documents"))["documents"]:
        source = doc.get("source_path") or ""
        if doc.get("collection_id") == collection["id"] or (
            not collection.get("builtin")  # Notes: no folder, membership by id alone
            and source and (not root or source == root or source.startswith(root + "/"))
        ):
            members.add(doc["filename"].lower())
    return {**collection, "members": members}


def check(case: dict, result: dict, members: Optional[set] = None) -> dict:
    """Deterministic checks. Returns {metrics..., hard_failures: [str]}.
    ``members`` -- the filenames of the case's library, when it is scoped."""
    answer = result["answer"]
    sources = result["sources"]
    n_sources = len(sources)
    cites = cited_numbers(answer)
    fails: list[str] = []

    if result["error"]:
        fails.append(f"stream error: {result['error']}")
    if not answer:
        fails.append("empty answer")

    refusal = looks_like_refusal(answer) or not sources
    if case.get("expect_refusal"):
        if not refusal:
            fails.append("expected a refusal, got a substantive answer")
        if cites:
            fails.append(f"refusal should cite nothing, cites {sorted(cites)}")
    else:
        if refusal:
            fails.append("unexpected refusal (library should be able to answer)")
        if not cites and answer:
            fails.append("answer has no [n] citations")

    out_of_range = sorted(n for n in cites if n < 1 or n > n_sources)
    if out_of_range:
        fails.append(f"citations out of range (have {n_sources} sources): {out_of_range}")

    for phrase in case.get("must_mention", []):
        if not re.search(phrase, answer, re.IGNORECASE):
            fails.append(f"must_mention not found: {phrase!r}")
    for phrase in case.get("must_not_mention", []):
        if re.search(phrase, answer, re.IGNORECASE):
            fails.append(f"must_not_mention present: {phrase!r}")

    # `must_cite` lists acceptable passages ("any of these"): at least one must be
    # retrieved (hard), and it is a soft signal whether one was actually cited.
    cited_ok = retrieved_ok = None
    must_cite = case.get("must_cite", [])
    if must_cite:
        matched = [i for i, s in enumerate(sources, 1)
                   if any(source_matches(w, s) for w in must_cite)]
        retrieved_ok = bool(matched)
        cited_ok = any(i in cites for i in matched)
        if not retrieved_ok:
            wanted = ", ".join(f"{w.get('document')} p.{w.get('page')}" for w in must_cite)
            fails.append(f"none of the must_cite passages were retrieved ({wanted})")

    if members is not None:
        outside = sorted({str(s.get("document") or "") for s in sources
                          if str(s.get("document") or "").lower() not in members})
        if outside:
            fails.append(f"sources outside the library: {', '.join(outside)}")

    in_range_cites = {n for n in cites if 1 <= n <= n_sources}
    return {
        "refusal": refusal,
        "citations": sorted(cites),
        "cited_fraction": (len(in_range_cites) / n_sources) if n_sources else 0.0,
        "sources": n_sources,
        "documents": result["documents"],
        "must_cite_retrieved": retrieved_ok,
        "must_cite_cited": cited_ok,
        "answer_chars": len(answer),
        "hard_failures": fails,
    }


# --- optional LLM judge ----------------------------------------------------

_JUDGE_SYSTEM = (
    "You are grading one answer produced by a retrieval-augmented reading "
    "assistant. You are given the user's question, the numbered source passages "
    "the assistant was shown, and its answer. Judge ONLY against those sources.\n\n"
    "Return a single JSON object, no prose, with:\n"
    '  "faithfulness": 1-5  (5 = every claim is supported by the sources; '
    "1 = contains clear fabrication)\n"
    '  "relevance": 1-5     (5 = directly answers the question)\n'
    '  "citation_accuracy": 1-5  (5 = the [n] markers point to passages that '
    "support the adjacent claim; 3 = citations present but loose; 1 = "
    "missing or wrong)\n"
    '  "key_points_covered": a list of booleans, one per listed key point, in '
    "order (omit if no key points were given)\n"
    '  "notes": one short sentence\n'
)


def _judge_prompt(case: dict, result: dict) -> str:
    blocks = "\n\n".join(
        f"[{i}] {s.get('document')} p.{s.get('page')}\n{(s.get('text') or '').strip()}"
        for i, s in enumerate(result["sources"], 1)
    ) or "(no sources)"
    parts = [f"Question: {case['question']}", f"\nSources:\n{blocks}", f"\nAnswer:\n{result['answer']}"]
    if case.get("key_points"):
        pts = "\n".join(f"  {i}. {p}" for i, p in enumerate(case["key_points"], 1))
        parts.append(f"\nKey points to check for coverage:\n{pts}")
    return "\n".join(parts)


def _judge_via_ollama(url: str, model: str, system: str, prompt: str) -> str:
    body = json.dumps({
        "model": model, "stream": False,
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/api/chat", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=ASK_TIMEOUT) as response:
        return json.loads(response.read())["message"]["content"]


class JudgeUnavailable(RuntimeError):
    """The judge could not run (missing key / URL) -- score degrades to deterministic."""


def _judge_via_cloud-provider(model: str, system: str, prompt: str) -> str:
    key = os.environ.get("REMOTE_API_KEY", "").strip()
    if not key:
        raise JudgeUnavailable("cloud-provider judge needs REMOTE_API_KEY in the environment")
    body = json.dumps({
        "model": model, "max_tokens": 1024, "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request("https://api.cloud-provider.com/v1/messages", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", key)
    req.add_header("cloud-provider-version", "2023-06-01")
    with urllib.request.urlopen(req, timeout=ASK_TIMEOUT) as response:
        return "".join(b.get("text", "") for b in json.loads(response.read())["content"])


def judge(case: dict, result: dict, spec: str, judge_url: Optional[str]) -> dict:
    provider, _, model = spec.partition(":")
    if not model:  # bare model name -> cloud-provider
        provider, model = "cloud-provider", spec
    system, prompt = _JUDGE_SYSTEM, _judge_prompt(case, result)
    if provider == "ollama":
        if not judge_url:
            raise JudgeUnavailable("ollama judge needs --judge-url http://host:11434")
        raw = _judge_via_ollama(judge_url, model, system, prompt)
    elif provider == "cloud-provider":
        raw = _judge_via_cloud-provider(model, system, prompt)
    else:
        raise SystemExit(f"unknown judge provider {provider!r} (use ollama:... or cloud-provider:...)")
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {"error": f"judge returned no JSON: {raw[:200]}"}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {"error": f"judge JSON invalid ({exc}): {match.group(0)[:200]}"}


# --- subcommands ---------------------------------------------------------------

def load_cases(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"no eval set at {path}")
    cases = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            cases.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{n}: invalid JSON ({exc})") from exc
    return cases


def score(cases: list[dict], model: Optional[str], judge_spec: Optional[str],
          judge_url: Optional[str], provider_keys: Optional[dict], repeat: int = 1) -> int:
    rows: list[tuple[str, str]] = []
    hard_fail_cases = 0
    faith = rel = cite_acc = 0.0
    judged_n = 0
    kp_hits = kp_total = 0
    cited_frac_sum = 0.0
    substantive = 0
    tag_stats: dict[str, list[int]] = {}  # tag -> [passed, total]
    libraries: dict[str, dict] = {}

    width = max((len(c.get("id") or c.get("question", "?")) for c in cases), default=0)
    for case in cases:
        cid = case.get("id") or case.get("question", "?")
        library = None
        if case.get("collection"):
            name = case["collection"]
            library = libraries.get(name) or libraries.setdefault(name, resolve_collection(name))
        # LLM answers vary run to run; with --repeat, a case fails only when it
        # fails on more than half the attempts, and we report the pass rate.
        runs = []
        for _ in range(repeat):
            try:
                result = ask(case["question"], case.get("mode", "quick"),
                             case.get("model") or model, provider_keys,
                             library["id"] if library else None)
            except (TimeoutError, OSError) as exc:
                # One slow or dropped answer is that case's failure, not the run's.
                result = {"answer": "", "sources": [], "documents": None, "relevant_count": None,
                          "model": None, "error": f"no response ({exc.__class__.__name__}: {exc})"}
            runs.append((result, check(case, result, library["members"] if library else None)))
        passed = [(r, m) for r, m in runs if not m["hard_failures"]]
        result, m = (passed[0] if passed else runs[-1])
        case_failed = len(passed) * 2 <= repeat  # majority must pass
        for tag in case.get("tags") or []:
            stats = tag_stats.setdefault(tag, [0, 0])
            stats[0] += 0 if case_failed else 1
            stats[1] += 1

        note = []
        if repeat > 1:
            note.append(f"{len(passed)}/{repeat} ok")
        if case_failed:
            hard_fail_cases += 1
            worst = min((mm for _, mm in runs), key=lambda mm: -len(mm["hard_failures"]))
            note.append("FAIL: " + "; ".join(worst["hard_failures"]))
        elif case.get("expect_refusal"):
            note.append("refusal OK")
        else:
            substantive += 1
            cited_frac_sum += m["cited_fraction"]
            bits = [f"{m['sources']} src", f"{m['documents']} docs",
                    f"cited {m['cited_fraction']:.0%}"]
            if case.get("must_cite"):
                bits.append("must_cite " + ("cited" if m["must_cite_cited"]
                            else "retrieved" if m["must_cite_retrieved"] else "MISSING"))
            note.append(" / ".join(bits))

        if judge_spec and not case.get("expect_refusal") and not case_failed:
            try:
                j = judge(case, result, judge_spec, judge_url)
            except JudgeUnavailable as exc:
                print(f"  (judge disabled: {exc})")
                judge_spec = None
                j = {"error": str(exc)}
            if "error" in j:
                note.append(f"judge: {j['error']}")
            else:
                judged_n += 1
                faith += _num(j.get("faithfulness"))
                rel += _num(j.get("relevance"))
                cite_acc += _num(j.get("citation_accuracy"))
                covered = j.get("key_points_covered") or []
                kp_hits += sum(1 for c in covered if c)
                kp_total += len(case.get("key_points", []))
                kp = f" kp {sum(1 for c in covered if c)}/{len(covered)}" if covered else ""
                note.append(f"judge F{_num(j.get('faithfulness')):.0f} "
                            f"R{_num(j.get('relevance')):.0f} "
                            f"C{_num(j.get('citation_accuracy')):.0f}{kp}")
        rows.append((cid, "  ".join(note)))
        # Print as we go: a long run that dies late keeps what it already scored.
        print(f"  {cid.ljust(width)}   {rows[-1][1]}", flush=True)


    print("\n" + "-" * (width + 40))
    print(f"  {len(cases)} cases, {hard_fail_cases} with hard failures")
    if tag_stats:
        print("  by tag:  " + "   ".join(f"{tag} {ok}/{n}" for tag, (ok, n) in sorted(tag_stats.items())))
    if substantive:
        print(f"  mean context cited:  {cited_frac_sum / substantive:.0%}  "
              f"({substantive} substantive answers)")
    if judged_n:
        print(f"  judge ({judge_spec}):  faithfulness {faith / judged_n:.1f}  "
              f"relevance {rel / judged_n:.1f}  citation {cite_acc / judged_n:.1f}"
              + (f"  key-point coverage {kp_hits}/{kp_total}" if kp_total else ""))
    return 1 if hard_fail_cases else 0


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _stub(question: str, result: dict) -> None:
    print(f"## {question!r}")
    if result["error"]:
        print(f"#   ERROR: {result['error']}")
    print(f"#   model={result['model']}  {len(result['sources'])} sources  "
          f"{result['documents']} docs  cites={sorted(cited_numbers(result['answer']))}")
    for line in result["answer"].splitlines():
        print(f"#   {line}")
    for i, s in enumerate(result["sources"][:6], 1):
        print(f"#   [{i}] {s.get('document')} p.{s.get('page')}")
    stub = {
        "id": "-".join(question.lower().split()[:4]) or "q",
        "question": question,
        "must_mention": [],
        "key_points": [],
        "must_cite": [{"document": s.get("document"), "page": s.get("page")}
                      for s in result["sources"][:1]],
    }
    print(json.dumps(stub, ensure_ascii=False))
    print()


_STUB_HELP = (
    "# Paste a line into eval/answers.jsonl. Fill must_mention with distinctive\n"
    "# words the answer must contain, key_points with facts a good answer covers\n"
    "# (judged by --judge), must_cite with the passage(s) it should cite. For a\n"
    '# question the library cannot answer, use {"expect_refusal": true} instead.\n'
)


def capture(questions: list[str], mode: str, model: Optional[str],
            provider_keys: Optional[dict]) -> int:
    print(_STUB_HELP)
    for q in questions:
        _stub(q, ask(q, mode, model, provider_keys))
    return 0


def review(limit: int, mode: str, model: Optional[str], provider_keys: Optional[dict]) -> int:
    try:
        log = json.loads(_request("GET", f"/admin/search-log?limit={limit * 3}"))
    except SystemExit as exc:
        if "404" in str(exc):
            raise SystemExit("this build has no /admin/search-log -- use `capture` instead") from exc
        raise
    seen: set[str] = set()
    questions = []
    for entry in log.get("entries", []):
        if entry.get("mode") != "ask":
            continue
        q = entry.get("query", "")
        if q and q not in seen:
            seen.add(q)
            questions.append(q)
        if len(questions) >= limit:
            break
    if not questions:
        print("no Ask-mode questions in the search log yet")
        return 0
    print(f"# {len(questions)} recent Ask question(s) from the search log\n")
    print(_STUB_HELP)
    for q in questions:
        _stub(q, ask(q, mode, model, provider_keys))
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    global BASE_URL, ASK_TIMEOUT

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=BASE_URL,
                        help=f"API base URL, or the UI proxy http://<host>:3100/api (default {BASE_URL})")
    parser.add_argument("--model", help="override the Ask model for every case, e.g. ollama:qwen2.5:7b")
    parser.add_argument("--provider-key", action="append", default=[], metavar="PROVIDER=KEY",
                        help="cloud key sent with each /ask (repeatable), e.g. cloud-provider=sk-ant-...")
    parser.add_argument("--judge", metavar="PROVIDER:MODEL",
                        help="LLM judge: ollama:qwen2.5:7b (with --judge-url) or cloud-provider:cloud-sonnet-5")
    parser.add_argument("--judge-url", help="Ollama base URL for --judge ollama:..., e.g. http://192.168.0.122:11434")

    sub = parser.add_subparsers(dest="command", required=True)
    p_score = sub.add_parser("score", help="replay answers.jsonl and report")
    p_score.add_argument("--answers", type=Path, default=ANSWERS_PATH)
    p_score.add_argument("--timeout", type=int, default=ASK_TIMEOUT,
                         help=f"seconds to wait for one answer (default {ASK_TIMEOUT}); a thinking model "
                              "sends nothing while it reasons, so allow for that on slow hardware")
    p_score.add_argument("--repeat", type=int, default=1,
                         help="run each case N times; fail only if it fails a majority (default 1)")
    p_cap = sub.add_parser("capture", help="run questions now -> stubs")
    p_cap.add_argument("question", nargs="+")
    p_cap.add_argument("--mode", choices=["quick", "thorough", "agentic"], default="quick")
    p_rev = sub.add_parser("review", help="recent Ask questions from the log -> stubs")
    p_rev.add_argument("-n", "--limit", type=int, default=20)
    p_rev.add_argument("--mode", choices=["quick", "thorough", "agentic"], default="quick")

    args = parser.parse_args(list(argv) if argv is not None else None)
    BASE_URL = args.url.rstrip("/")
    keys = {}
    for pair in args.provider_key:
        prov, _, val = pair.partition("=")
        if val:
            keys[prov.strip()] = val.strip()
    keys = keys or None

    if args.command == "score":
        ASK_TIMEOUT = max(1, args.timeout)
        return score(load_cases(args.answers), args.model, args.judge, args.judge_url,
                     keys, max(1, args.repeat))
    if args.command == "capture":
        return capture(args.question, args.mode, args.model, keys)
    if args.command == "review":
        return review(args.limit, args.mode, args.model, keys)
    return 2


if __name__ == "__main__":
    sys.exit(main())
