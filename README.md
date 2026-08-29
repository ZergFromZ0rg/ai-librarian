# AI Librarian

AI Librarian is a self-hosted, local-first knowledge library for PDF documents. It extracts layout-aware Markdown, parses typed document blocks, builds structure-aware token-budgeted passages, and combines semantic and exact-symbol retrieval in Qdrant before reranking passages with page-level source references.

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
- Combine dense semantic search with sparse lexical matching for exact terms, formulas, and symbols.

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

When upgrading from an older pipeline version, existing stored PDFs are automatically re-extracted and reindexed using the current structure-aware hybrid-search schema. The original PDFs remain unchanged. During this one-time migration, document badges move through `queued` and `indexing` again.

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

Then open `http://SERVER_LAN_IP:3100`. If port 3100 is already occupied, choose another unused `UI_PORT`; the container still listens internally on port 80.

Restrict ports 3100 and 8000 with the server firewall. This application has no built-in authentication and should only be exposed on a trusted LAN, through Tailscale, or behind an authenticated TLS reverse proxy.

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
| `RERANK_TIMEOUT` | `15` | Maximum reranker time in seconds |
| `CHUNK_TARGET_TOKENS` | `180` | Preferred token budget for a searchable passage |
| `CHUNK_SOFT_MAX_TOKENS` | `220` | Size allowed for an intact semantic group before child splitting |
| `CHUNK_HARD_MAX_TOKENS` | `240` | Maximum estimated tokens in an embedding child |
| `CHUNK_OVERLAP_TOKENS` | `32` | Context repeated between children of an oversized block |
| `CORS_ORIGINS` | local development origins | Allowed origins for direct API development |

If `MAX_UPLOAD_MB` is raised above 100, also update `client_max_body_size` in `ui/nginx.conf`.

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

## API

Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

Important endpoints:

- `POST /documents` — upload a PDF
- `GET /documents` — list documents and indexing states
- `GET /documents/{id}` — inspect one document's state
- `POST /documents/{id}/retry` — retry failed indexing
- `DELETE /documents/{id}` — delete stored files and vectors
- `POST /search` — semantic retrieval
- `POST /admin/ingest-folder` — import the mounted library folder
- `GET /admin/search-log` — recent queries with their returned pages and scores
- `GET /health/live` — process liveness
- `GET /health/ready` — Qdrant readiness, indexing backlog, and ingest queue depth

Every search appends one JSON line to `data/app/logs/search.jsonl` (rotated, stays on the server): the query, the candidate budget, latency, and each returned hit's page and dense/rerank scores. It is meant for tuning retrieval quality — inspect it with `GET /admin/search-log` or read the file directly. Set `SEARCH_LOG=off` in `.env` to disable.

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
- Image-only scanned PDFs require OCR before upload; OCR remains disabled to avoid silently corrupting mathematical notation.
- When a minority of pages carry a corrupt OCR text layer, those pages are skipped and the rest of the document is indexed; the document then shows an `extraction_notes` message saying how many pages were left out. A PDF whose text layer is *mostly* corrupt is still refused whole.
- Layout extraction can preserve mathematical symbols only when the PDF exposes usable text or glyph information. Equations stored solely as images require a dedicated math-aware OCR system.
- Token counts use a conservative local estimator so uploads do not have to load the embedding model. The lower 240-token hard budget leaves room for differences in the model's final WordPiece tokenization.
- The document service intentionally runs as one process. The indexing backlog is durable (the worker pulls `queued` documents straight from SQLite, so a restart or a full-library re-index never loses or fails work), but the folder-import queue is still in-process. Moving to Redis or another external worker system would be the next step for horizontal scaling.
- Search returns relevant source passages; it does not generate or summarize answers.
