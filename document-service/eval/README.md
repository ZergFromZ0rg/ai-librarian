# Retrieval evaluation harness

A small, hand-curated set of queries with relevance judgments, plus a runner that
replays them against `/search` and reports **recall@k**, **MRR**, and
**relevance-gate accuracy**. Use it to catch retrieval regressions when the
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
| `relevant` | passages that *should* come back. Each: `document` (filename), `page` (PDF position, 1-based — same number the UI shows), optional `contains` (a distinctive phrase the passage must include) |
| `expect_empty` | `true` for a query nothing in the library should answer — asserts the rerank gate returns zero results |

A `relevant` entry is counted as retrieved when the result's filename matches,
`page` falls inside the result's `[page, page_end]` span, and `contains` (if set)
appears in the returned text. **Judgments are keyed by filename + page** because
`chunk_id` / `group_id` are random and regenerated on every re-index; filename and
page survive.

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
  camus-absurd      recall@5 1/1  first@1
  history-conics    recall@5 1/2  first@2
  gate-gorilla      gate PASS

  ----------------------------------------
  2 judged queries:  recall@1 0.50   recall@3 0.75   recall@5 0.75   recall@10 0.75   MRR 0.75
  relevance gate:  1/1 expected-empty queries returned nothing
```

Exits non-zero if an `expect_empty` query leaks results, or if a judged query
finds nothing relevant at all — so it can gate a release. `--no-rerank` scores
raw vector order for comparison.

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
