import os
import time
import json
import hashlib
import shutil
import requests
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from pypdf import PdfReader

VAULT_DIR = Path("/ai-vault")
INBOX_DIR = VAULT_DIR / "inbox"
SORTED_FOLDERS = ["books", "papers", "notes", "conversations", "generated"]
WATCH_FOLDERS = SORTED_FOLDERS  # folders we index after sorting
MANIFEST_PATH = Path("/ai-vault/_stack/ingestion/manifest.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
EMBED_MODEL = "nomic-embed-text"
CLASSIFY_MODEL = "llama3.2:3b"
COLLECTION = "vault"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
POLL_INTERVAL = 30

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

client = QdrantClient(url=QDRANT_URL)


def ensure_collection():
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION not in collections:
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


def classify_file(filename, text_sample):
    prompt = f"""You are a file classifier for a personal knowledge vault.
Categories: books, papers, notes, conversations, generated

- books: full-length books, textbooks, long-form reference material
- papers: academic papers, research papers, technical reports
- notes: personal notes, class notes, quick writeups, todo lists
- conversations: chat logs, transcripts, exported conversations
- generated: AI-generated summaries, outputs, or synthetic content

Filename: {filename}
Content sample:
{text_sample[:800]}

Respond with ONLY one word: the category name. Nothing else."""

    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": CLASSIFY_MODEL, "prompt": prompt, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    answer = resp.json().get("response", "").strip().lower()

    for category in SORTED_FOLDERS:
        if category in answer:
            return category
    return "notes"  # safe fallback


def sort_inbox_files():
    if not INBOX_DIR.exists():
        return
    for path in list(INBOX_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        print(f"Sorting: {path.name}")
        try:
            text = extract_text(path)
            category = classify_file(path.name, text)
            dest_folder = VAULT_DIR / category
            dest_folder.mkdir(parents=True, exist_ok=True)
            dest_path = dest_folder / path.name

            # avoid overwriting existing files with same name
            counter = 1
            while dest_path.exists():
                dest_path = dest_folder / f"{path.stem}_{counter}{path.suffix}"
                counter += 1

            shutil.move(str(path), str(dest_path))
            print(f"  -> {category}/{dest_path.name}")
        except Exception as e:
            print(f"  Error sorting {path.name}: {e}")


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
                continue
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
            sort_inbox_files()
            scan_and_process()
        except Exception as e:
            print(f"Error during scan: {e}")
        time.sleep(POLL_INTERVAL)
