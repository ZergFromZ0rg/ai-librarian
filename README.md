# AI Librarian

AI Librarian is a self-hosted, local-first knowledge library for PDF documents. It extracts and chunks text, creates local semantic embeddings, stores them in Qdrant, and reranks relevant passages with page-level source references.

No hosted AI API or generative model is required. After the container images and search models have been downloaded, document processing and search stay on your server.

## What it does

- Upload one or more PDFs from the browser.
- Import PDFs from a read-only server folder.
- Track each document through `queued`, `indexing`, `indexed`, or `error` states.
- Retry failed indexing jobs and recover queued work after a restart.
- Avoid duplicate documents using a SHA-256 content hash.
- Search concepts, passages, names, and ideas semantically across the full library.

## Architecture

```text
Browser
  │
  ▼
Nginx UI ── /api ──► FastAPI document service
                         ├── PDF extraction and chunking
                         ├── Sentence Transformer embeddings/reranking
                         └── Qdrant semantic retrieval
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
| `CORS_ORIGINS` | local development origins | Allowed origins for direct API development |

If `MAX_UPLOAD_MB` is raised above 100, also update `client_max_body_size` in `ui/nginx.conf`.

## Storage and backups

Persistent state lives under:

```text
data/
  app/       original PDFs, extracted text, chunks, metadata, and job state
  qdrant/    vector database storage
  models/    embedding and reranker cache
library/     optional read-only PDF import source
```

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

# Follow application logs
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
- `GET /health/live` — process liveness
- `GET /health/ready` — Qdrant readiness and worker queue status

## Development and tests

Backend:

```bash
cd document-service
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The end-to-end test uses a generated PDF and deterministic in-memory embeddings. It covers upload, background indexing, Qdrant-compatible search, deduplication, deletion, failure persistence/retry, and folder containment without downloading AI models.

Frontend:

```bash
cd ui
npm ci
npm run build
```

## Current limitations

- PDF is the only accepted document format.
- Image-only scanned PDFs require OCR before upload.
- The document service intentionally runs as one process because its bounded work queues are in-process. Moving queues to Redis or another durable worker system would be the next step for horizontal scaling.
- Search returns relevant source passages; it does not generate or summarize answers.
