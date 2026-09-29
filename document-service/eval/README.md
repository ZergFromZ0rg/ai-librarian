# Evaluation harnesses

Two hand-curated eval sets, each with a stdlib-only runner. Neither is a `pytest`
test — they need the real models and a real indexed library, so run them by hand
against a live service.

- **`harness.py` + `queries.jsonl`** — *retrieval*: replays queries against
  `/search`, reports hit@k / recall@k / MRR / gate accuracy. Catches regressions
  from chunking, embedding, or reranker changes. (This document.)
- **`ask_harness.py` + `answers.jsonl`** — *answer quality*: replays questions
  against `/ask`, checks the generated answers are grounded, cited, on-topic, and
  refuse when the library has nothing. See [Answer-quality harness](#answer-quality-harness)
  at the bottom.

---

# Retrieval evaluation harness

A small, hand-curated set of queries with relevance judgments, plus a runner that
replays them against `/search` and reports **hit rate@k**, **recall@k**, **MRR**,
and **relevance-gate accuracy**. Use it to catch retrieval regressions when the
chunking, embedding, or reranker changes.

This is **not** a unit test — it needs the real models and a real indexed
library, so it is not run by `pytest` / CI. Run it by hand against a live
service.

Standard-library only — run it with any `python3`, no venv, from any machine
that can reach the server.

## The eval set: `queries.jsonl`

One JSON object per line. Two kinds of case:

```jsonl
{"id": "camus-absurd", "query": "what does Camus say about suicide", "relevant": [{"document": "myth-of-sisyphus.pdf", "page": 3, "contains": "one truly serious philosophical problem"}]}
{"id": "history-conics", "query": "who first studied conic sections", "relevant": [{"document": "merzbach-history.pdf", "page": 119, "contains": "Menaechmus"}, {"document": "boyer-history.pdf", "page": 157, "contains": "Apollonius"}]}
{"id": "gate-gorilla", "query": "characteristics of a gorilla", "expect_empty": true}
```

| field | meaning |
|---|---|
| `id` | stable handle, shown in the report |
| `query` | the search string, exactly as a user would type it |
| `relevant` | one or more passages, *any* of which is an acceptable answer. Each: `document` (filename), `page` (PDF position, 1-based — same number the UI shows), optional `contains` (a distinctive phrase the passage must include) |
| `expect_empty` | `true` for a query nothing in the library should answer — asserts the rerank gate returns zero results |
| `collection` | optional library name: the search is scoped to that library, and any result from outside it is a **SCOPE LEAK** (hard failure). With neither `relevant` nor `expect_empty` the case is a pure scope probe |
| `search` | optional extra `/search` fields for this case only, e.g. `{"rerank_min_score": -100}` so a scope probe returns results instead of being gated to nothing |
| `tags` | optional labels (`"excel"`, `"multilingual"`, …); the report adds hit@1 / hit@5 / MRR per tag |

A `relevant` entry is counted as retrieved when the result's filename matches,
`page` falls inside the result's `[page, page_end]` span, and `contains` (if set)
appears in the returned text. **Judgments are keyed by filename + page** because
`chunk_id` / `group_id` are random and regenerated on every re-index; filename and
page survive.

List every passage that would genuinely answer the query. The two metrics read
the list differently:

- **hit@k** — did *at least one* listed passage land in the top k? This is the
  headline: it tracks whether a searcher got a good answer. Adding more
  acceptable passages can only help it.
- **recall@k** — of all the listed passages, what fraction were surfaced? A
  completeness measure. Only meaningful when you intend the list to be
  exhaustive; with "any of these is fine" judgments it will read low and that's
  expected.

`#`-prefixed and blank lines are ignored.

## Building the set

Both of these print each query's current top hits plus a ready-made JSONL stub.
Paste the stub into `queries.jsonl`, delete the hits that aren't actually good
answers, and tighten each `contains` to one distinctive phrase. If nothing in
the library should answer the query, replace `relevant` with
`"expect_empty": true`.

**`capture`** — run one or more queries right now (works against any build):

```bash
python eval/harness.py --url http://192.168.0.122:3100/api capture \
  "who first proved there are infinitely many primes" \
  "what is the library of babel"
```

**`review`** — pull the last N *real* queries out of the search log
(`data/app/logs/search.jsonl`). Needs a build with `/admin/search-log`
(search logging shipped after pipeline_version 5):

```bash
python eval/harness.py --url http://192.168.0.122:3100/api review -n 20
```

## Scoring

```bash
cd document-service
python eval/harness.py --url http://192.168.0.122:3100/api score
```

```
  camus-absurd      hit@5   first@1
  history-conics    hit@5   first@2   recall@5 1/2
  gate-gorilla      gate PASS

  ----------------------------------------
  2 judged queries
    hit rate:  hit@1 0.50   hit@3 1.00   hit@5 1.00   hit@10 1.00
    recall:    recall@1 0.25   recall@3 0.75   recall@5 0.75   recall@10 0.75
    MRR:       0.75
  relevance gate:  1/1 expected-empty queries returned nothing
```

Per-row: `hit@5` / `MISS@5` is whether any acceptable passage made the top 5;
`first@N` is the rank of the first one; `recall@5 x/y` is shown only when a query
lists more than one acceptable passage.

Exits non-zero if an `expect_empty` query leaks results, or if not one judged
query found an answer in the top 10 — so it can gate a release. `--no-rerank`
scores raw vector order for comparison.

### Comparing dense/sparse fusion

`--fusion {rrf,dbsf,rsf}` and `--dense-weight 0..1` are passed through on every
search, so a fusion method can be A/B'd against the eval set without touching the
server:

```bash
python eval/harness.py --url … score                          # server default
python eval/harness.py --url … score --fusion rsf --dense-weight 0.7
```

## Calibrating the relevance gate

```bash
python eval/harness.py --url … calibrate
```

Runs every judged query with the gate wide open, labels each returned passage
relevant / not (matched a judgment vs. not; everything from an `expect_empty`
query counts as not), fits the reranker's raw score to **P(relevant)** with
isotonic regression, and sweeps candidate `RERANK_MIN_SCORE` cutoffs — showing
for each what fraction of known answers it keeps, how much off-topic noise it
lets through, and how many `expect_empty` queries it leaks. It recommends the
lowest cutoff with no leaks. Re-run it after changing `RERANK_MODEL` or
`RERANK_PASSAGE`.

## Pointing it at the service

The harness talks to the **API**. Two ways to reach it:

- **Through the UI proxy** (works from anywhere on the LAN, and injects
  `APP_TOKEN` for you): `--url http://<host>:3100/api`
  e.g. `python eval/harness.py --url http://192.168.0.122:3100/api review`
- **Directly on :8000** — only if you run the harness on the server itself
  (`http://127.0.0.1:8000`) or set `BIND_ADDRESS=0.0.0.0` in `.env`.

`--url` goes before the subcommand. Or set `AI_LIBRARIAN_URL` in the
environment. `APP_TOKEN` is only needed when going direct to :8000 on a
token-protected API.

The script is standard-library only — run it with any `python3`, no venv needed.

## The Office corpus suites

`queries.jsonl` and `answers.jsonl` are judged against the 6-book home library.
`queries_office.jsonl` and `answers_office.jsonl` are judged against a generated,
reproducible corpus of 24 Word / Excel / PowerPoint files, so format handling,
library scoping and multilingual text can be checked on any machine:

```bash
cd document-service
python eval/office_corpus.py ../library/documents     # needs the service's venv
curl -X POST http://127.0.0.1:8010/collections -H 'Content-Type: application/json' \
  -d '{"name": "Documents", "path": "documents"}'     # or Library -> Collections in the UI
python eval/harness.py --url http://127.0.0.1:8010 score --queries eval/queries_office.jsonl
python eval/ask_harness.py --url http://127.0.0.1:8010 --model ollama:qwen3:4b \
  score --answers eval/answers_office.jsonl
```

The corpus exercises Word tables and bullet lists, multi-sheet workbooks
(formulas, dates, a header-only sheet), slide text boxes and speaker notes,
French / German / Japanese text, and a Word file ending in a 6,000-character
base64 blob. Cases tagged `known-weak` are expected to miss: an English
question about French or Japanese text (see
[Multilingual libraries](#multilingual-libraries)).

### Multilingual libraries

Same-language search works in any language. An English question about French
or Japanese text does not. Dense retrieval still finds the passage, but the
English-only reranker (`ms-marco-MiniLM-L-6-v2`) scores it around -8 to -11,
far below `RERANK_MIN_SCORE`. Swapping only the embedding model changes
nothing. Measured offline on the Office corpus (dense top 30, then rerank):

| setup | hit@1 | cross-lingual answers | top unanswerable score | rerank |
|---|---|---|---|---|
| bge-base-en + ms-marco (default) | 0.97 | gated out (-10.8, -8.3) | -8.0 | 2.5 s/query |
| multilingual-e5-base + ms-marco | 0.97 | gated out (same) | -8.7 | 6.4 s/query |
| multilingual-e5-base + `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | 0.94 | found (+9.2, +2.5) | **-1.9** | 12.7 s/query |

A library that needs cross-language search can set
`RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` in `.env`. Expect:
- reranking about 5x slower;
- a gate that must be re-fitted with `harness.py calibrate`, since the -2.0
  default lets an unanswerable query through;
- run the book suite first, because its effect on English retrieval is not
  yet measured.

Ask needs a model that emits `[n]` citations; `qwen2.5:1.5b` answers correctly
but cites nothing, so every substantive case fails the citation check with it.

---

# Answer-quality harness

`ask_harness.py` replays `answers.jsonl` against `/ask`, consumes the SSE stream,
and checks the **generated answer** — the stage after retrieval. It catches
regressions in the prompts, the map-reduce flow, query cleaning, and the model
wiring.

## The eval set: `answers.jsonl`

One JSON object per line. `#`-prefixed and blank lines are ignored.

```jsonl
{"id": "meme-def", "question": "What is a meme?", "must_mention": ["imitation"],
 "key_points": ["copied person to person", "spreads by imitation"],
 "must_cite": [{"document": "The Meme Machine ....pdf", "page": 23}]}
{"id": "carburetor", "question": "how do I rebuild a carburetor", "expect_refusal": true}
```

| field | meaning |
|---|---|
| `id`, `question` | required |
| `mode` | `quick` (default), `thorough`, or `agentic` (bounded follow-up retrieval) |
| `model` | per-case model override (`provider:model`) |
| `must_mention` | regexes (case-insensitive) the answer **must** contain — hard check |
| `must_not_mention` | regexes the answer must **not** contain — hard check |
| `key_points` | facts a good answer covers; scored by `--judge` only |
| `must_cite` | list of acceptable passages (`document` + `page`); **≥1 must be retrieved** (hard), and it's a soft signal whether one was actually cited `[n]` |
| `expect_refusal` | the library can't answer — assert the answer declines and cites nothing |
| `collection` | optional library name: the question is scoped to it, and a source from outside it is a hard failure |
| `tags` | optional labels; the report adds a pass count per tag |

## Deterministic checks (always run, no LLM)

- citations present (non-refusal answers), every `[n]` within the source count
- `must_mention` / `must_not_mention`
- `must_cite` retrieved / cited
- refusal detected for `expect_refusal`; **not** detected otherwise
- `cited_fraction` — how much of the supplied context the answer actually cited

`score` exits non-zero if any case has a hard failure, so it can gate a release.

**LLM answers vary run to run.** A single hard failure is a signal to look, not
proof of a regression — re-run, or use `--repeat N` (each case runs N times and
fails only if it fails a majority; the pass rate is printed). A local model on
CPU is slow, so `--repeat 3` on the full set is a coffee break.

## Optional LLM judge

`--judge <provider:model>` adds faithfulness / relevance / citation-accuracy
(1–5) and `key_points` coverage. Two backends:

```bash
# local Ollama judge
python eval/ask_harness.py --url http://192.168.0.122:3100/api \
  --judge ollama:qwen2.5:7b --judge-url http://192.168.0.122:11434 score

# Cloud provider judge (needs REMOTE_API_KEY)
REMOTE_API_KEY=sk-ant-... python eval/ask_harness.py --url … \
  --judge cloud-provider:cloud-sonnet-5 score
```

A missing key/URL disables the judge for the run (deterministic checks still run).

## Building the set

```bash
python eval/ask_harness.py --url http://192.168.0.122:3100/api capture \
  "What is a wormhole?" "Who discovered non-Euclidean geometry?"
python eval/ask_harness.py --url http://192.168.0.122:3100/api review -n 20   # from the search log
```

Both print the answer, its sources, and a ready-to-edit stub. Paste a line into
`answers.jsonl`, fill `must_mention` / `key_points` / `must_cite`, or swap in
`{"expect_refusal": true}`.

## Running

```bash
cd document-service
python eval/ask_harness.py --url http://192.168.0.122:3100/api score
python eval/ask_harness.py --url … --model ollama:qwen2.5:7b score   # pin a model
python eval/ask_harness.py --url … capture "…" --mode thorough       # try thorough
python eval/ask_harness.py --url … capture "…" --mode agentic        # try bounded follow-ups
```

`--model` / `--provider-key PROVIDER=KEY` before the subcommand override the model
and pass a browser-style cloud key with every `/ask`.
