# AI Librarian

AI Librarian is a self-hosted, local-first knowledge library for PDF documents. It extracts layout-aware Markdown, parses typed document blocks, builds structure-aware token-budgeted passages, and combines semantic and BM25 keyword retrieval in Qdrant before reranking passages with page-level source references.

No hosted AI API or generative model is required. After the container images and search models have been downloaded, document processing and search stay on your server.

## What it does

- Upload one or more PDFs from the browser.
- Import PDFs from a read-only server folder.
- Track each document through `queued`, `indexing`, `indexed`, or `error` states.
- Retry failed indexing jobs and recover queued work after a restart.
- Avoid duplicate documents using a SHA-256 content hash.
- Search concepts, passages, names, and ideas semantically across the full library.
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
                         └── Qdrant dense + sparse hybrid retrieval
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

```bash
docker compose up -d --build
```

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

## Adding documents from a server folder

The host `library/` directory is mounted read-only at `/library`. Place PDFs anywhere under it, then enter `.` or a relative subfolder such as `books/philosophy` in the UI's folder-import field.

Absolute paths and paths outside `/library` are rejected. Folder imports scan PDFs directly inside the selected folder; they do not recurse into subfolders.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface used by published UI/API ports |
| `UI_PORT` | `3100` | Browser UI port |
| `API_PORT` | `8000` | Direct API and interactive docs port |
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
| `RERANK_MIN_SCORE` | `off` | Optional hard floor on the reranker score; hits below it are dropped so an unanswerable query can return empty. Off by default — neither reranker separates unanswerable queries from real ones cleanly enough. Set a float only where `eval/harness.py calibrate` finds a clean cutoff |
| `RERANK_LOWCONF_SCORE` | `0.0` | Soft version: when the top reranked hit scores below this, the response is flagged `low_confidence` and the UI shows a "nothing clearly matched" banner without hiding results. `0.0` suits MiniLM logits; raise (~0.3) for a 0..1 model; `off` disables |
| `CHUNK_TARGET_TOKENS` | `180` | Preferred token budget for a searchable passage |
| `CHUNK_SOFT_MAX_TOKENS` | `220` | Size allowed for an intact semantic group before child splitting |
| `CHUNK_HARD_MAX_TOKENS` | `240` | Maximum estimated tokens in an embedding child |
| `CHUNK_OVERLAP_TOKENS` | `32` | Context repeated between children of an oversized block |
| `CORS_ORIGINS` | local development origins | Allowed origins for direct API development |

If `MAX_UPLOAD_MB` is raised above 100, also update `client_max_body_size` in `ui/nginx.conf.template`.

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
| `data/app/documents/` | the uploaded PDFs (the only copy) | no |
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
- `POST /admin/ingest-folder` — import the mounted library folder
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

`npm test` runs the Vitest unit tests (currently the search-result match highlighter).

## Current limitations

- PDF is the only accepted document format.
- The service does not run OCR. A PDF that is mostly page-images with no text layer — a scan, or image-only typesetting — is rejected on upload with a message to OCR it first; a few full-page figure plates in an otherwise text PDF are fine.
- When a minority of pages carry a corrupt OCR text layer, those pages are skipped and the rest of the document is indexed; the document then shows an `extraction_notes` message saying how many pages were left out. A PDF whose text layer is *mostly* corrupt is still refused whole.
- Front and back matter — tables of contents, back-of-book indexes, bibliographies — is detected by shape and dropped before indexing, so those keyword-dense pages don't outrank real passages. Detection is conservative and skips nothing when it would flag more than 40% of a document.
- Layout extraction can preserve mathematical symbols only when the PDF exposes usable text or glyph information. Equations stored solely as images are not recovered.
- Chunk sizes are measured with the embedding model's own tokenizer (loaded on its own, without the model). If that tokenizer cannot be loaded — no `transformers`, or offline before it is cached — the chunker falls back to a conservative regex estimate and logs a warning.
- The document service intentionally runs as one process. The indexing backlog is durable (the worker pulls `queued` documents straight from SQLite, so a restart or a full-library re-index never loses or fails work), but the folder-import queue is still in-process. Moving to Redis or another external worker system would be the next step for horizontal scaling.
- Search returns relevant source passages; it does not generate or summarize answers.
