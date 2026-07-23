import os
import time
import json
import hashlib
import requests
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from pypdf import PdfReader

VAULT_DIR = Path("/ai-vault")
WATCH_FOLDERS = ["books", "papers", "notes", "conversations", "generated", "inbox"]
MANIFEST_PATH = Path("/ai-vault/_stack/ingestion/manifest.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
EMBED_MODEL = "nomic-embed-text"
COLLECTION = "vault"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
POLL_INTERVAL = 30  # seconds

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

client = QdrantClient(url=QDRANT_URL)


def ensure_collection():
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION not in collections:
        # nomic-embed-text produces 768-dim vectors
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )
        print(f"Created Qdrant collection '{COLLECTION}'")


def load_manifest():
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def extract_text(path):
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return path.read_text(errors="ignore")
    elif ext == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return ""


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return [c.strip() for c in chunks if c.strip()]


def embed(text):
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def process_file(path, manifest):
    rel_path = str(path.relative_to(VAULT_DIR))
    print(f"Processing: {rel_path}")
    text = extract_text(path)
    if not text.strip():
        print(f"  No text extracted, skipping.")
        return

    chunks = chunk_text(text)
    points = []
    for i, chunk in enumerate(chunks):
        vector = embed(chunk)
        point_id = int(hashlib.sha256(f"{rel_path}-{i}".encode()).hexdigest()[:16], 16) % (2**63)
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "text": chunk,
                    "source_path": rel_path,
                    "filename": path.name,
                    "chunk_index": i,
                },
            )
        )

    if points:
        client.upsert(collection_name=COLLECTION, points=points)
        print(f"  Indexed {len(points)} chunks.")


def scan_and_process():
    manifest = load_manifest()
    changed = False

    for folder in WATCH_FOLDERS:
        folder_path = VAULT_DIR / folder
        if not folder_path.exists():
            continue
        for path in folder_path.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            rel = str(path.relative_to(VAULT_DIR))
            current_hash = file_hash(path)
            if manifest.get(rel) == current_hash:
                continue  # already processed, unchanged
            process_file(path, manifest)
            manifest[rel] = current_hash
            changed = True

    if changed:
        save_manifest(manifest)


if __name__ == "__main__":
    print("Ingestion service starting...")
    ensure_collection()
    while True:
        try:
            scan_and_process()
        except Exception as e:
            print(f"Error during scan: {e}")
        time.sleep(POLL_INTERVAL)
