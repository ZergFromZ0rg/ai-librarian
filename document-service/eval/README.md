# Evaluation harnesses

Hand-built eval sets, each with a stdlib-only runner. None is a `pytest` test --
they need the real models and a real indexed library, so run them by hand
against a live service. (`tests/test_retrieval_eval.py`, `test_ask_eval.py` and
`test_synth_corpus.py` do cover the harness *logic* and the generated ground
truth in CI.)

- **`harness.py`** -- *retrieval*: replays queries against `/search`, reports
  hit@k / recall@k / MRR / gate accuracy **with 95% intervals**, a dev/test
  split, and per-tag breakdowns. `compare` diffs two saved runs query by query.
- **`ask_harness.py`** -- *answer quality*: replays questions against `/ask`,
  checks the answers are grounded, cited, correct and refuse when they should,
  and says **why** each failing case failed. See [Answer-quality harness](#answer-quality-harness).

## Which set to trust

| Set | Cases | Ground truth | Use it for |
|---|---|---|---|
| `queries_synth.jsonl` / `answers_synth.jsonl` | 326 / 117 | **planted before any model saw it** | the number you quote; A/B tests |
| `queries_office.jsonl` / `answers_office.jsonl` | 41 / 19 | seeded generator, judged by hand | format handling, scoping, multilingual |
| `queries.jsonl` / `answers.jsonl` | 47 / 20 | pooled from the system's own results, then tuned against | regression canary on the real books |

The book set is useful but flatters the system: its judgments were made by
keeping the hits that looked right, and the relevance gate and reranker were
tuned on those same queries. With 39 positives one query is 2.6 points. Use it to
notice that something broke, not to claim a score.

The synthetic set exists to fix that. [Its documents](#the-synthetic-benchmark)
are generated from planted facts (a manager's name, a pallet count, a charter
year), and the questions are written from the facts, so the right answer is
known before any retrieval runs.

## Reading a number

Every rate is printed with a 95% interval, e.g. `hit@1 0.83 [0.77-0.88]`.
Read the range, not the point: a difference smaller than the interval is not a
finding. Two habits make the numbers honest:

1. **Tune on `dev`, quote `test`.** Each case is assigned to `dev` or `test` by a
   hash of its `id` (about 30% land in `test`; a `split` field overrides). It
   cannot be nudged by hand. Run `score --split dev` while changing things and
   `score --split test` once at the end. A test number you have looked at and
   then tuned against is a dev number.
2. **Compare runs, don't eyeball them.** `score --save a.json`, change one thing,
   `score --save b.json`, then `compare a.json b.json`. It lines the runs up
   query by query, lists what flipped each way, and gives an exact McNemar
   p-value. "0.03 better" on 40 queries is usually noise; `compare` says so.

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
| `tags` | optional labels (`"excel"`, `"multilingual"`, …); the report adds hit@1 / hit@5 / MRR, with intervals, per tag |
| `match` | `"any"` (default: one listed passage in the top k is a hit) or `"all"` (every listed passage must be in the top k -- for "which is higher, A or B?" questions). All-of cases are reported on their own line, because k required passages cannot rank better than k and would otherwise drag down hit@1 and MRR |
| `split` | optional `"dev"` / `"test"`; by default derived from a hash of `id` (see above) |

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
python eval/harness.py --url … score --queries eval/queries_synth.jsonl --split test --save runs/baseline.json
```

```
  depot-opened-tavgate-para   hit@5  first@1
  town-charter-iverwick-vs-eskholm   hit@5  first@2  all-of recall@5 2/2
  near-miss-depot-marmere     unanswerable -- LEAKED 10 (top 2.79)

  270 judged queries   (95% intervals; the line below the table is for all-of cases)
              rate   interval
    hit@1     0.83   0.78-0.87
    hit@5     0.94   0.91-0.96
    MRR       0.88   0.85-0.91
  by split  (tune on dev; quote test):
    dev   n=186  hit@1 0.83 [0.77-0.88]   hit@5 0.94 [0.90-0.97]
    test  n=84   hit@1 0.83 [0.74-0.90]   hit@5 0.94 [0.87-0.97]
  all-of queries (every source must rank): n=24   top 5: 1.00 [0.86-1.00]
  by tag:
    pdf    n=44   hit@1 0.68 [0.53-0.80]   hit@5 1.00 [0.92-1.00]   MRR 0.81 [0.72-0.89]
    table  n=10   hit@1 0.20 [0.06-0.51]   hit@5 0.50 [0.24-0.76]   MRR 0.33 [0.13-0.56]
  unanswerable probes:  0.41 [0.26-0.58] gated out, 19 LEAKED
```

Per-row: `hit@5` / `MISS@5` is whether any acceptable passage made the top 5;
`first@N` is the rank of the first one; `recall@5 x/y` is shown only when a query
lists more than one acceptable passage.

| flag | does |
|---|---|
| `--split dev\|test` | score only that half (default both) |
| `--tag NAME` | score only cases carrying a tag, e.g. `--tag table` while working on table extraction |
| `--save FILE` | write every query's outcome as JSON, for `report` and `compare` |
| `--no-rerank` | score raw vector order |

```bash
python eval/harness.py report runs/baseline.json          # re-print a saved run, no server needed
python eval/harness.py compare runs/baseline.json runs/rsf.json
```

```
  270 queries in both runs
  hit@1:  A 0.83   B 0.78   only A right 31, only B right 17   p=0.059  -> no clear difference
      A only: policy-approver-ormdale-direct, policy-notice-kesham-reverse, ...
      B only: depot-cost-pelley-reverse, depot-staff-galholm-para, ...
  hit@5:  A 0.94   B 0.95   only A right 7, only B right 10   p=0.629  -> no clear difference
  MRR change B-A:  -0.024   95% interval -0.056 to +0.008
  all-of queries (24): every source in the top 5   A 24   B 22
```

(That is the real output of reranking on vs off, run A = reranked, B = `--no-rerank`:
the reranker's gain at hit@1 looks like 5 points but 48 flipped queries split 31/17
is not yet distinguishable from luck at p=0.059. Without `compare` that would have
been reported as "the reranker adds 5 points".)

Exits non-zero if not one judged query found an answer in the top 10, or if a
library-scoped search returned anything from outside its library -- so it can
gate a release. Unanswerable-query leaks are reported, not failed: the gate is a
product choice.

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

## The synthetic benchmark

```bash
cd document-service
../.venv/bin/python eval/synth_corpus.py /tmp/synth          # 60 files + queries_synth.jsonl + answers_synth.jsonl
../.venv/bin/python eval/serve_local.py /tmp/synth --port 8765   # the real service, no Docker (second terminal)
python eval/harness.py --url http://127.0.0.1:8765 score --queries eval/queries_synth.jsonl --save runs/synth.json
```

`synth_corpus.py` writes sixty documents in all seven ingestible formats (docx,
pdf, pptx, xlsx, csv, md, txt) from 366 planted facts, then writes the questions
from the facts. Same seed, same text. After writing it reads every judged phrase
back out of the rendered files with plain python-docx / openpyxl / python-pptx /
PyMuPDF (not the service's parsers) and refuses to finish if one is missing, so a
broken generator fails there, not as a mysterious retrieval miss. The committed
`queries_synth.jsonl` / `answers_synth.jsonl` are checked against a fresh
generation in CI; regenerate them, never hand-edit.

What it is built to make hard:

| tag | what it tests |
|---|---|
| `direct` / `para` | a question reusing the document's words vs one that avoids them (a test asserts paraphrases really share fewer words) |
| `reverse` | the value is given and the entity is asked for: "which depot employs 212 people?" |
| siblings | fourteen depot reports share a template, so only the name separates "the Calanford depot" from thirteen near-copies |
| `shared-name` | a place can be a depot, a survey, an office handbook and a town chronicle at once; the question has to be read |
| `pdf` | the judged page number must also match |
| `table` / `notes` / `row` | the fact is in a Word table, a slide's speaker notes, or a spreadsheet row, not in prose |
| `comparison` | "which is higher, A or B?" needs both sources (`match: "all"`) |
| `near-miss` | a real attribute of an entity that does not exist. The reranker cannot tell, so most leak: this is a measurement, not a failure |
| `off-topic` | nothing in the library is related; the gate should return nothing |

Answer cases add two refusal kinds the retrieval set cannot: `absent-attribute`
(a real depot, a fact nobody wrote down -- the model must say so, not invent
one) and `must_not_mention` set to a *neighbour's* number, which catches
entity confusion that `must_mention` alone would pass.

`serve_local.py` runs the real service in-process with an in-memory Qdrant and a
throw-away data directory, so none of this needs Docker or touches your library.
`--env KEY=VALUE ...` sets service settings first (`RERANK_MIN_SCORE=-1.0`,
`FUSION_METHOD=rsf`, `EMBEDDING_MODEL=...`), which is how you A/B a pipeline
setting: serve, `score --save`, restart with the other setting, `score --save`,
`compare`. The models must already be in the Hugging Face cache
(`OFFLINE=1` is the default here).

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

Add `--json-out path/to/snapshot.json` to `score` to save a compact regression
snapshot with per-case pass/fail, source count, citation coverage, and answer
length. Snapshots are intended for release comparisons; model-generated answers
remain intentionally out of the file.

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

### Reading the report

```
  117 cases, 21 with hard failures
  pass rate:  0.82 [0.74-0.88]   (95% interval)
  by split (tune on dev; quote test):  dev 0.83 [0.74-0.89] n=82   test 0.80 [0.64-0.90] n=35
  by tag:
    absent-attribute  12/16  0.75 [0.51-0.90]
    near-miss         10/10  1.00 [0.72-1.00]
  why cases failed:  wrong-answer 9   invented-answer 4   wrong-entity 3   retrieval-miss 2
```

The last line is the useful one: a failing case is labelled by cause, because
the causes have different fixes.

| why | meaning | look at |
|---|---|---|
| `retrieval-miss` | none of the `must_cite` passages were even retrieved | the retrieval harness first |
| `wrong-answer` | `must_mention` is missing from the answer | the prompt, or the model's reading |
| `wrong-entity` | the answer contains the value from a *sibling* document (`must_not_mention`) | context mixing, citation discipline |
| `invented-answer` | a refusal was expected and the model answered anyway | the "say so when the sources don't" prompt |
| `over-refusal` | the model refused something the library answers | threshold / prompt too cautious |
| `uncited`, `bad-citation` | no `[n]`, or `[n]` beyond the supplied sources | formatting |

`--split dev|test` and `--tag NAME` run a subset (an Ask run is slow: a 117-case
pass at ~70 s per case on `qwen3:4b` is over two hours, so iterate on one tag
and run the full set once). `answers_synth.jsonl` checks the planted value
exactly (`must_mention` is the number or surname, not a paraphrase), so its
pass rate needs no LLM judge.

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

# Local Ollama judge
python eval/ask_harness.py --url … --judge-url http://host:11434 \
  --judge ollama:qwen2.5:7b score
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
