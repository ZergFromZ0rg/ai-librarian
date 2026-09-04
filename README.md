# AI Librarian

AI Librarian is a self-hosted, local-first knowledge library for PDF documents. It extracts layout-aware Markdown, parses typed document blocks, builds structure-aware token-budgeted passages, and combines semantic and BM25 keyword retrieval in Qdrant before reranking passages with page-level source references.

No hosted AI API is required for extraction or search: once the container images and search models are downloaded, document processing and retrieval stay on your server. The optional [Ask mode](#ask-mode) adds LLM-written answers over the retrieved passages — from a local [Ollama](https://ollama.com) model (fully offline) or, if you add a key, a cloud API (Claude, OpenAI, Gemini).

## What it does

- Upload one or more PDFs from the browser.
- Point at a folder of PDFs already on disk (any nesting, any size) and have it auto-indexed in place — no copying, no manual step. Type or paste the exact folder path into the website itself (Browse → **Library path**) to pick it, even when the underlying mount is broader.
- Track each document through `queued`, `indexing`, `indexed`, or `error` states.
- Retry failed indexing jobs and recover queued work after a restart.
- Avoid duplicate documents using a SHA-256 content hash.
- Search concepts, passages, names, and ideas semantically across the full library.
- Optionally ask questions in natural language and get an answer written from the retrieved passages, with `[n]` citations back to the source pages ([Ask mode](#ask-mode)).
- Preserve headings, lists, tables, whitespace, Unicode symbols, and mathematical notation when the PDF text layer contains them.
- Keep equations and tables attached to their introductions, captions, and explanations, including continuations across page boundaries.
- Index smaller child blocks for precise matching while returning their complete parent passage for context.
- Combine dense semantic search with BM25 keyword matching (rare words weighted up, long passages down), keeping math symbols and formula fragments as first-class searchable units.

## Architecture

```text
Browser
  │
  ▼
Nginx UI ── /api ──► FastAPI document service
                         ├── PyMuPDF4LLM layout extraction
                         ├── Typed paragraph/heading/equation/table/caption blocks
                         ├── 180-token target, protected groups, 32-token overlap
                         ├── Sentence Transformer embeddings/reranking
                         ├── Qdrant dense + sparse hybrid retrieval
                         └── Ask mode (optional): grounded answer generation
                             via a local Ollama model or a cloud API
```

Qdrant is private to the Compose network. Only the UI and API are published, and both bind to `127.0.0.1` by default.

## Server quick start

Prerequisites:

- Docker Engine with the current Docker Compose plugin
- At least 8 GB RAM; 16 GB or more is more comfortable
- Roughly 10 GB free disk space for images, models, and initial data

Clone the repository and prepare the configuration:

```bash
cp .env.example .env
mkdir -p data/app data/qdrant data/models library
```

Then edit `.env` and set `LIBRARY_PATH` — this only decides which host path the container is *allowed* to see; it's fine to point it at your real PDF folder directly, or somewhere broader (your whole home directory, an external drive) if you'd rather pick the exact folder afterward from the website. Either way, don't copy or move files into `./library` first — point at where they already are.

```bash
docker compose up -d --build
```

If `LIBRARY_PATH` already points at your PDFs, they're indexed automatically within a few seconds — no upload, no manual click. If you mounted something broader, open the Library panel's **Browse** tab and type or paste your real collection's path into the **Library path** box (or navigate to it and click **Set as library folder**) — from then on that's what gets scanned (`AUTO_INGEST_INTERVAL_SECONDS`, default every 60s) and where Browse opens by default, with no further `.env` editing or restart.

The first indexing request takes longer because the document service downloads its embedding model. The reranker is downloaded on the first reranked search. Model files are persisted under `data/models/`.

| Role | Model | Download |
| --- | --- | --- |
| embeddings | `BAAI/bge-base-en-v1.5` | ~440 MB |
| reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | ~90 MB |

The embedding model is a 768-dimensional, 512-token retrieval model. It wants an instruction prefix on the query only — the service prepends `Represent this sentence for searching relevant passages: ` to search strings and stores passages raw (both configurable). Override `EMBEDDING_MODEL` for a different model; for e5-\* set the prefixes to `query: ` / `passage: `, for an unprefixed one such as `sentence-transformers/all-MiniLM-L6-v2` set both empty. A model change re-embeds the whole library on the next start; if the new model's vector width differs from the indexed one, also set `ALLOW_INDEX_RESET=1` for that one start.

To fetch both up front instead of on the first request:

```bash
docker compose run --rm document-service python warm_models.py
```

### Offline / air-gapped

Build with the models baked into the image, then set `OFFLINE=1` so the huggingface libraries never try to reach the network:

```bash
PREBAKE_MODELS=1 docker compose build document-service
echo "OFFLINE=1" >> .env
docker compose up -d
```

`PREBAKE_MODELS=1` adds roughly 550 MB to the image (the embedding + reranker models); on first start the entrypoint copies the baked models into an empty `data/models/`. Alternatively, run `warm_models.py` on a networked machine and copy `data/models/` to the air-gapped host.

[Ask mode](#ask-mode) stays air-gap friendly when its models come from a local Ollama (`OLLAMA_URL`); the cloud API providers obviously need network egress and are not for air-gapped hosts.

When upgrading from an older pipeline version, or after changing `EMBEDDING_MODEL`, existing stored PDFs are automatically reindexed on first start — re-extracted when the pipeline changed, otherwise just re-embedded. The original PDFs remain unchanged. During this one-time migration, document badges move through `queued` and `indexing` again.

Follow startup progress:

```bash
docker compose ps
docker compose logs -f document-service
```

Open `http://127.0.0.1:3100` and upload a PDF. Wait for its badge to change to `indexed` before searching.

## Connecting to a remote server

The safest simple option is to keep the default loopback binding and open an SSH tunnel from your workstation:

```bash
ssh -L 3100:127.0.0.1:3100 -L 8000:127.0.0.1:8000 your-user@your-server
```

Then visit `http://127.0.0.1:3100` locally.

To expose the app directly on a trusted LAN, set this in `.env`:

```dotenv
BIND_ADDRESS=0.0.0.0
UI_PORT=3100
```

Then open `http://SERVER_LAN_IP:3100`. If port 3100 is already occupied, choose another unused `UI_PORT`; the container still listens internally on port 8080.

Restrict ports 3100 and 8000 with the server firewall.

### API token

Set `APP_TOKEN` in `.env` to require `Authorization: Bearer <APP_TOKEN>` on every document-service endpoint except the health checks:

```dotenv
APP_TOKEN=$(openssl rand -hex 24)
```

The bundled UI keeps working — nginx forwards the token to the API. This protects **direct** API access (port 8000); a request that reaches the UI's nginx (port 3100) is still proxied through. Exposing the UI to an untrusted network therefore still needs Tailscale or an authenticated TLS reverse proxy in front. Unhandled errors now return a generic message plus an `X-Request-ID` that matches the full exception in the service logs.

## Browsing and importing from a server folder

There are two layers, and only the first one ever needs `.env` or a restart:

1. **The mount** (`LIBRARY_PATH` in `.env`) — an absolute host path bind-mounted read-only at `/library`. This is a Docker-level fact: the container can only ever see host paths it was handed at start, so *some* path has to be declared here once. It's fine — encouraged, even — to make this broad (your whole home directory, an external drive's mount point) rather than guessing the exact folder up front.
2. **The library folder** — which subfolder of that mount is actually "the library": what the background scan walks and where the Browse tab opens by default. This is set **entirely from the website**, no `.env` editing or restart involved: at the top of **Browse**, type or paste the folder's path into the **Library path** box and hit **Set** — paste the exact absolute path as your file manager shows it (the box's placeholder shows the expected form) or a path relative to the mount, either works. Prefer clicking instead of typing? Navigate there in Browse and click **Set as library folder** on it — same result. **Reset to whole mount** (next to the box) undoes it. Whichever way this is set, PDFs are always **referenced in place** — nothing is ever copied, moved, or renamed on disk.

**This is automatic.** A background scan (`AUTO_INGEST_INTERVAL_SECONDS`, default every 60s) walks the designated library folder recursively and references any PDF it hasn't seen before — once immediately at startup (so a library already in place before the first `docker compose up` needs no UI interaction at all), then again on every interval (so a file dropped in later is picked up on its own). Set `AUTO_INGEST_INTERVAL_SECONDS=0` to disable this and rely only on manual import.

The Library panel has two tabs:

- **Browse** walks the mount like Finder or Explorer — sub-folders (with a PDF count) and PDF files, with a breadcrumb to navigate back up, even past the designated library folder if `LIBRARY_PATH` was mounted broad. **Attach main library folder** at the top imports everything under the currently designated folder in one click — useful to skip ahead of the next scheduled scan. Per-folder, **Set as library folder** narrows the designation to right there (and imports it immediately); **Import all** imports a folder without changing the designation; a single file gets its own **Import**. Removing a document from **Indexed** un-indexes it but leaves the original file untouched.
- **Indexed** is the list of documents currently in the index, with their status, page/chunk counts, retry, and remove.

The small **or upload a PDF file** link (in the Browse tab) still copies a file onto the server, into `data/app/documents/` — use it for the odd PDF that isn't under the mount at all.

Absolute paths and paths outside `/library` are rejected. The folder structure you already have is preserved as-is — nested sub-folders are walked recursively, but the app does not reorganize or rename anything on disk.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface used by published UI/API ports |
| `UI_PORT` | `3100` | Browser UI port |
| `API_PORT` | `8000` | Direct API and interactive docs port |
| `LIBRARY_PATH` | `./library` | Host folder mounted read-only at `/library`; can be your exact PDF folder or something broader — see [Browsing and importing](#browsing-and-importing-from-a-server-folder) |
| `AUTO_INGEST_INTERVAL_SECONDS` | `60` | How often to rescan `LIBRARY_PATH` for new PDFs to auto-import; `0` disables the scan (manual import only) |
| `MAX_UPLOAD_MB` | `100` | Per-file backend upload limit |
| `EMBEDDING_BATCH_SIZE` | `32` | Chunks embedded in each indexing batch |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | Sentence-embedding model; a change re-embeds the library on next start (add `ALLOW_INDEX_RESET=1` once if the vector width changes) |
| `EMBEDDING_QUERY_PREFIX` | `Represent this sentence for searching relevant passages: ` | Instruction prepended to search strings before embedding |
| `EMBEDDING_PASSAGE_PREFIX` | *(empty)* | Prefix prepended to stored passages before embedding |
| `EMBEDDING_DIMS` | `0` | Matryoshka models only: keep the first N dimensions (0 = native width) |
| `LEXICAL_K1` / `LEXICAL_B` | `1.5` / `0.75` | BM25 term-saturation and length-penalty parameters for the keyword half |
| `LEXICAL_AVGDL` | `180` | BM25 reference passage length (weighted terms); a re-index picks up a change |
| `FUSION_METHOD` | `rrf` | How the semantic and lexical lists merge: `rrf` (rank only), `dbsf` (score, Qdrant-normalised), `rsf` (min-max + weight). Query-time only |
| `FUSION_DENSE_WEIGHT` | `0.5` | `rsf` only: 0..1 weight on the semantic list (lexical gets the rest) |
| `RERANK_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder that re-sorts the shortlist. Tiny and fast (~1 s for 60 candidates on CPU). `BAAI/bge-reranker-base` is stronger on scholarly prose but ~15x slower and saturates its score on this library — with a strong dense model the extra cost didn't pay off |
| `RERANK_PASSAGE` | `group` | Text the reranker scores: `group` (the whole passage) or `matched` (just the winning retrieval unit). `group` gives the reranker context to separate near-duplicates; `matched` saturates a 0..1 model on short fragments. A search request may override it |
| `RERANK_TIMEOUT` | `15` | Maximum reranker time in seconds; on a timeout the fused order is returned unranked |
| `RERANK_CANDIDATES` | `60` | Fused candidates the cross-encoder scores per query; cheap at 60 with MiniLM, the main latency knob with a heavier model |
| `RERANK_MIN_SCORE` | `-2.0` | Hard floor on the reranker score; hits below it are dropped so an unanswerable query returns empty. `-2.0` suits the default MiniLM on this library (off-topic probes score below −3.5, real answers above −2); `BAAI/bge-reranker-base` can't be gated at all. Re-fit with `eval/harness.py calibrate` after changing `RERANK_MODEL`; `off` disables |
| `RERANK_LOWCONF_SCORE` | `0.0` | Soft version: when the top reranked hit scores below this, the response is flagged `low_confidence` and the UI shows a "nothing clearly matched" banner without hiding results. `0.0` suits MiniLM logits; raise (~0.3) for a 0..1 model; `off` disables |
| `CHUNK_TARGET_TOKENS` | `180` | Preferred token budget for a searchable passage |
| `CHUNK_SOFT_MAX_TOKENS` | `220` | Size allowed for an intact semantic group before child splitting |
| `CHUNK_HARD_MAX_TOKENS` | `240` | Maximum estimated tokens in an embedding child |
| `CHUNK_OVERLAP_TOKENS` | `32` | Context repeated between children of an oversized block |
| `CORS_ORIGINS` | local development origins | Allowed origins for direct API development |

If `MAX_UPLOAD_MB` is raised above 100, also update `client_max_body_size` in `ui/nginx.conf.template`.

## Ask mode

Ask mode turns the retrieved passages into a written answer with `[n]` citations back to the
source pages. Retrieval is unchanged — the same hybrid search and reranker feed the model —
so the answer is only as good as what search finds. Each `[n]` in the answer is clickable
and jumps to the passage card it cites; every card has a "view source" link to the original
page. The reader picks the model from a dropdown in the Ask tab; the choice is remembered
in the browser. Conversations are **saved on the server** — the **Chat** picker in the Ask
tab lists them, and re-opening one restores the full thread with its sources.

Ask mode appears **when at least one model is available**. Models come from two places:

| Source | Setup | Notes |
| --- | --- | --- |
| **Local — [Ollama](https://ollama.com)** on the Docker host | `OLLAMA_URL` (default `http://host.docker.internal:11434`); models discovered from `/api/tags` | free, offline; CPU generation takes tens of seconds |
| **Cloud API** — Claude / OpenAI / Gemini | set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | fast and strong; needs network egress, bills per query |

Each provider is hidden until it is configured. `GET /ask/models` returns the current list.

### Ollama on the host

Ollama runs on the server itself, not in Compose. Two requirements for the container to
reach it:

1. **Ollama must listen on all interfaces.** Its default `127.0.0.1` bind is unreachable
   from Docker. Set `OLLAMA_HOST=0.0.0.0:11434` (in Ollama's systemd override:
   `sudo systemctl edit ollama`, add `Environment=OLLAMA_HOST=0.0.0.0:11434`, then
   `sudo systemctl restart ollama`).
2. The `document-service` container maps `host.docker.internal` to the host gateway
   (already in `docker-compose.yml`; built in on Docker Desktop).

Check it from inside the container:

```bash
docker compose exec document-service python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').read()[:120])"
```

Pull models with `ollama pull llama3.2` on the host; they show up in the picker within a
few seconds. Set `OLLAMA_URL=` empty to disable Ollama discovery. Ask mode talks to Ollama
over its native `/api/chat` and raises the context window to `OLLAMA_NUM_CTX` (default 8192)
per request — otherwise a local model's ~2–4K default would silently truncate a
multi-passage prompt.

### Cloud APIs

Two ways to add Claude / OpenAI / Gemini:

- **In the browser** — the Ask tab's **API keys** button opens a panel; paste a key and
  that provider's models appear in the picker immediately. The key is held in that
  browser's `localStorage` and sent with each question — it is **never written to the
  server**. Re-enter it per browser/device. Because the key rides in the request body,
  only do this over localhost/LAN or behind `APP_TOKEN` + a TLS proxy.
- **On the server** — set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` in
  `.env`; those models are then available to everyone with no per-browser step.

Every provider's models are always listed in the picker; the ones without a key are greyed
out and labelled "needs API key" until you add one. The picker offers a small default model
set per provider — override with `ANTHROPIC_MODELS` / `OPENAI_MODELS` / `GOOGLE_MODELS`
(comma-separated, server-side). Claude uses the `anthropic` SDK (bundled, imported only when
picked); OpenAI and Gemini use their OpenAI-compatible REST endpoints over `httpx`, no SDK.
`GENERATION_MODEL` optionally sets the default model (`provider:model`, e.g.
`ollama:llama3.2:latest`). A key shorter than 30 characters is treated as unset, so the
`.env.example` placeholder never counts as configured.

### Coverage across many documents

Ask retrieval favours breadth so a large library doesn't answer from ten near-identical
paragraphs of one chapter:

- `ASK_CONTEXT_PASSAGES` / `ASK_CONTEXT_PASSAGES_CLOUD` — passages shown and cited, per
  answer (default 10 for a local model, 30 for a big-context cloud model).
- `ASK_MAX_PER_DOC` (default 3) — cap on how many of those may come from one document.
- `ASK_DEDUP_JACCARD` (default 0.6) — drop a passage whose wording overlaps an
  already-picked one by more than this.
- `RERANK_CANDIDATES` (default 100) — how deep the reranker looks; raise it as the library
  grows past a few hundred documents.
- Conversational framing ("across my library, how is X described?") is stripped to its
  topic before retrieval — the cross-encoder is a short-query ranker and the framing
  tanks its scores. The model still gets your exact question.
- `ASK_THOROUGH_MIN_SCORE` (default −5.0) — Thorough mode uses a much looser reranker gate
  than Quick mode's `RERANK_MIN_SCORE`, since its per-document map step is the real filter.

Each answer's source header shows the spread — "*N passages · M documents · K relevant
matches*" — so you can see when coverage is thin.

**Thorough mode** (the toggle in the Ask tab) is for library-wide questions — "what have I
read about X", "compare how these books treat Y". It retrieves a much wider pool
(`ASK_THOROUGH_PASSAGES`, default 40), groups it by document, has a cheap model pull the
relevant evidence from each document **in parallel**, then the model you picked synthesises
the answer from those notes with citations. It is slower (a handful of extra calls) and
shines with a capable synthesis model. The map passes use the cheapest available model — a
local one, else a small cloud model, else your selected model — overridable with
`ASK_THOROUGH_MAP_MODEL`.

### Other knobs

`GENERATION_MAX_TOKENS` (answer length ceiling), `GENERATION_TEMPERATURE`,
`GENERATION_TIMEOUT`, `ASK_MAX_CONTEXT_CHARS` (a character budget over the passages,
default 10000). `GET /config` reports whether Ask mode is enabled and which providers are
configured; the UI shows the **Ask** tab only when at least one model is available.

## Storage and backups

Persistent state lives under:

```text
data/
  app/       original PDFs, typed extracted blocks, semantic groups, folder-ingest
             job state, and library.db (SQLite document-metadata index)
  qdrant/    vector database storage
  models/    embedding and reranker cache
library/     optional read-only PDF import source
```

On first start after upgrading, any existing `data/app/metadata/*.json` records are
imported into `data/app/library.db` automatically. The JSON files are left in place
and can be deleted once the import is confirmed.

Stop the stack before taking a filesystem-level backup:

```bash
docker compose stop
tar -czf ai-librarian-backup.tgz data
docker compose start
```

## Operations

```bash
# Show service state
docker compose ps

# Follow application logs (timestamped; set LOG_LEVEL=DEBUG in .env for more)
docker compose logs -f document-service ui qdrant

# Restart services without deleting data
docker compose restart

# Stop services without deleting data
docker compose down
```

Do not use `docker compose down --volumes` or delete `data/` unless you intend to remove the library's persistent state.

Both application containers run as a non-root user. On Linux the document
service takes ownership of `data/app` and `data/models` on first start (uid
`10001`), so reading those paths from the host afterwards may need `sudo`; to
run the container as your own user instead, add `user: "$(id -u):$(id -g)"` to
the `document-service` service and make sure `data/` is owned by you.

### Backup and restore

Everything the library needs lives under `data/`:

| Path | Contents | Rebuildable? |
| --- | --- | --- |
| `data/app/library.db` | document metadata and indexing state | no |
| `data/app/documents/` | PDFs added via **Upload** (the only copy); files imported from `/library` stay on the source disk | no |
| `data/app/conversations.db` | saved Ask-mode conversations | no (but not essential) |
| `data/app/settings.json` | the designated library folder ([Browsing and importing](#browsing-and-importing-from-a-server-folder)) | no (but not essential — resets to the whole mount) |
| `data/app/extracted/`, `data/app/chunks/` | extracted text and passages | yes, from the PDFs |
| `data/app/jobs/`, `data/app/logs/` | folder-import state, search log | not needed |
| `data/qdrant/` | the vector index | yes, by re-indexing |
| `data/models/` | downloaded embedding and reranker models | yes, re-downloaded |

**Minimum backup** — stop the stack, then copy `data/app/library.db` and
`data/app/documents/`. On restore, put them back and `docker compose up -d`;
the service re-extracts and re-indexes every PDF on first start.

**Full backup** — also copy `data/qdrant/` to skip the re-index on restore.

**Recover from a corrupt vector index** — delete `data/qdrant/` and restart.
The service rebuilds every vector from the PDFs and `library.db`; documents
move back through `queued` and `indexing`.

## API

Interactive API documentation is available at `http://127.0.0.1:8000/docs` (also behind `APP_TOKEN` when set).

Important endpoints:

- `POST /documents` — upload a PDF
- `GET /documents` — list documents and indexing states
- `GET /documents/{id}` — inspect one document's state
- `POST /documents/{id}/retry` — retry failed indexing
- `DELETE /documents/{id}` — delete stored files and vectors
- `POST /search` — semantic retrieval (set `rerank: true` to reorder and relevance-gate; `rerank_min_score`, `fusion`, and `dense_weight` override the server defaults per request)
- `POST /ask` — retrieve, then stream a grounded answer as Server-Sent Events (`token` chunks, `progress` in thorough mode, then one `sources` event); body: `{"question", "history": [{"role", "content"}], "model": "provider:model", "mode": "quick"|"thorough"}`. Returns 503 when no model is available. See [Ask mode](#ask-mode)
- `GET /ask/models` — models the reader may pick, plus the current default
- `GET|POST /conversations`, `GET|PUT|DELETE /conversations/{id}` — saved Ask conversations (server-side; the UI's **Chat** picker)
- `GET /library/tree?path=` — one level of the mounted `/library` volume (sub-folders + PDF files, marked when indexed)
- `POST /library/import` — import one PDF (referenced in place) or every PDF under a folder (recursive); body `{"path"}`
- `GET /library/root` — the designated library folder (persisted; `""` = the whole mount) and whether it still resolves
- `POST /library/root` — narrow (or, with `path: ""`, reset) which folder under `/library` counts as the library; what auto-ingest scans and where Browse opens by default
- `POST /admin/ingest-folder` — recursively import a folder under `/library` (the older form of `POST /library/import` on a directory)
- `GET /admin/search-log` — recent queries with their returned pages and scores
- `GET /health/live` — process liveness
- `GET /health/ready` — Qdrant readiness, indexing backlog, and ingest queue depth

Every search appends one JSON line to `data/app/logs/search.jsonl` (rotated, stays on the server): the query, the candidate budget, latency, the reranker cutoff and how many hits it dropped, and each returned hit's page and dense/rerank scores. It is meant for tuning retrieval quality — inspect it with `GET /admin/search-log` or read the file directly. Set `SEARCH_LOG=off` in `.env` to disable.

## Development and tests

Backend:

```bash
cd document-service
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
```

The end-to-end test uses a generated PDF and deterministic in-memory embeddings. It covers upload, background indexing, Qdrant-compatible search, deduplication, deletion, failure persistence/retry, restart re-queueing, and folder containment without downloading AI models. `ruff check` lints for likely bugs and import hygiene (config in `pyproject.toml`); both run in CI.

Frontend:

```bash
cd ui
npm ci
npm test
npm run build
```

`npm test` runs the Vitest unit tests (the search-result match highlighter and the Ask-mode SSE stream parser).

## Current limitations

- PDF is the only accepted document format.
- The Browse tab sees only what is mounted at `/library` (`LIBRARY_PATH`). It is not a full filesystem browser — to reach another disk too, add it as a second bind mount. There is no in-browser PDF preview. New files are picked up automatically (`AUTO_INGEST_INTERVAL_SECONDS`), not instantly — up to one interval's delay, or use **Attach**/**Import** in Browse to skip the wait. The app never renames, moves, or otherwise reorganizes files on disk — sub-folder structure is only ever read, never written.
- The service does not run OCR. A PDF that is mostly page-images with no text layer — a scan, or image-only typesetting — is rejected on upload with a message to OCR it first; a few full-page figure plates in an otherwise text PDF are fine.
- When a minority of pages carry a corrupt OCR text layer, those pages are skipped and the rest of the document is indexed; the document then shows an `extraction_notes` message saying how many pages were left out. A PDF whose text layer is *mostly* corrupt is still refused whole.
- Front and back matter — tables of contents, back-of-book indexes, bibliographies — is detected by shape and dropped before indexing, so those keyword-dense pages don't outrank real passages. Detection is conservative and skips nothing when it would flag more than 40% of a document.
- Layout extraction can preserve mathematical symbols only when the PDF exposes usable text or glyph information. Equations stored solely as images are not recovered.
- Chunk sizes are measured with the embedding model's own tokenizer (loaded on its own, without the model). If that tokenizer cannot be loaded — no `transformers`, or offline before it is cached — the chunker falls back to a conservative regex estimate and logs a warning.
- [Ask mode](#ask-mode) answers are only as good as what retrieval surfaces and what the chosen model can do with it — a small local model will be weaker than a frontier cloud model. Answers are grounded in the retrieved passages and cite them, but always spot-check the cited pages.
- The document service intentionally runs as one process. The indexing backlog is durable (the worker pulls `queued` documents straight from SQLite, so a restart or a full-library re-index never loses or fails work), but the folder-import queue is still in-process. Moving to Redis or another external worker system would be the next step for horizontal scaling.

## Roadmap

- **Tier 3 — agentic retrieval.** Give the answering model a `search` tool and let it run
  its own follow-up queries when it notices a gap in what it was handed, instead of
  answering from a single retrieval pass. This is the biggest remaining lever on answer
  quality for hard, multi-part questions. Deferred pending whether [Thorough mode](#ask-mode)
  proves insufficient in practice — it is a real feature (tool loop, step limit, streamed
  intermediate searches), not a tweak.
- Answer-quality regression coverage beyond the seed set in
  [`eval/answers.jsonl`](document-service/eval/README.md#answer-quality-harness).
