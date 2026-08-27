import argparse
import time
import shutil
from pathlib import Path
import requests

def upload_file(url, path, wait=False, poll_interval=2, timeout=300):
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "application/pdf")}
        r = requests.post(f"{url}/documents", files=files, timeout=timeout)
    if r.status_code not in (200, 201):
        print(f"Failed to upload {path.name}: {r.status_code} {r.text}")
        return None
    data = r.json()
    doc_id = data.get("document_id")
    action = "Already present" if data.get("deduplicated") else "Uploaded"
    print(f"{action}: {path.name} -> document_id={doc_id}")

    if wait and doc_id:
        start = time.time()
        while time.time() - start < timeout:
            status_response = requests.get(f"{url}/documents/{doc_id}", timeout=10)
            if status_response.status_code == 200:
                status = status_response.json().get("indexing_status")
                if status == "indexed":
                    print(f"Index ready for {doc_id}")
                    return doc_id
                if status == "error":
                    print(f"Indexing failed for {doc_id}: {status_response.json().get('indexing_error')}")
                    return None
            time.sleep(poll_interval)
        print(f"Timed out waiting for indexing for {doc_id}")
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
                if doc_id:
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
    parser = argparse.ArgumentParser(description="Upload PDF files to AI Librarian")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--move", action="store_true", help="move watched files into an uploaded subfolder")
    args = parser.parse_args()
    if args.watch:
        watch_and_upload(args.folder, url=args.url, poll_interval=5, move_after_upload=args.move)
        return
    bulk_upload(args.folder, url=args.url, wait=args.wait)


if __name__ == "__main__":
    main()
