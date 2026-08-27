import sys
import time
import shutil
from pathlib import Path
import requests


def upload_file(url, path, wait=False, poll_interval=2, timeout=60):
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "application/pdf")}
        r = requests.post(f"{url}/documents", files=files)
    if r.status_code != 201:
        print(f"Failed to upload {path.name}: {r.status_code} {r.text}")
        return None
    data = r.json()
    doc_id = data.get("document_id")
    print(f"Uploaded {path.name} -> document_id={doc_id}")

    if wait and doc_id:
        start = time.time()
        while time.time() - start < timeout:
            cr = requests.get(f"{url}/documents/{doc_id}/chunks")
            if cr.status_code == 200:
                chunks = cr.json()
                # check if first chunk has embedding
                if chunks and isinstance(chunks, list) and chunks[0].get("embedding"):
                    print(f"Embeddings ready for {doc_id} (chunks={len(chunks)})")
                    return doc_id
            time.sleep(poll_interval)
        print(f"Timed out waiting for embeddings for {doc_id}")
    return doc_id


def bulk_upload(folder, url="http://127.0.0.1:8000", wait=False):
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        print("Folder not found:", folder)
        return
    pdfs = list(p.glob("*.pdf"))
    if not pdfs:
        print("No PDFs found in folder:", folder)
        return
    for pdf in pdfs:
        upload_file(url, pdf, wait=wait)


def watch_and_upload(folder, url="http://127.0.0.1:8000", poll_interval=5, move_after_upload=True, uploaded_dir_name="uploaded"):
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        print("Folder not found:", folder)
        return

    uploaded_dir = p / uploaded_dir_name
    uploaded_dir.mkdir(parents=True, exist_ok=True)

    seen = set()
    print(f"Watching {p} for new PDFs (poll every {poll_interval}s)")
    try:
        while True:
            for pdf in p.glob("*.pdf"):
                if pdf.name in seen:
                    continue
                # ignore files already in uploaded dir
                if pdf.parent == uploaded_dir:
                    continue
                print(f"Found new PDF: {pdf.name}")
                doc_id = upload_file(url, pdf, wait=False)
                seen.add(pdf.name)
                if move_after_upload and doc_id:
                    try:
                        dest = uploaded_dir / pdf.name
                        shutil.move(str(pdf), str(dest))
                        print(f"Moved {pdf.name} -> {dest}")
                    except Exception as e:
                        print(f"Failed to move {pdf.name}: {e}")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("Stopping watch.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python bulk_upload.py <pdf-folder> [server_url] [--wait] [--watch] [--move]")
        return
    folder = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "http://127.0.0.1:8000"
    wait = "--wait" in sys.argv
    watch = "--watch" in sys.argv
    move = "--move" in sys.argv
    if watch:
        watch_and_upload(folder, url=url, poll_interval=5, move_after_upload=move)
        return
    bulk_upload(folder, url=url, wait=wait)


if __name__ == "__main__":
    main()
