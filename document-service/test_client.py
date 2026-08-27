import sys
import requests
import json


def upload_file(url, filepath):
    with open(filepath, "rb") as f:
        files = {"file": (filepath, f, "application/pdf")}
        r = requests.post(f"{url}/documents", files=files)
    return r


def get_chunks(url, doc_id):
    r = requests.get(f"{url}/documents/{doc_id}/chunks")
    return r


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_client.py <path-to-pdf> [server_url]")
        return
    filepath = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"

    print(f"Uploading {filepath} to {url} ...")
    r = upload_file(url, filepath)
    print("Upload status:", r.status_code)
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text)
        return

    if r.status_code != 201:
        return

    doc_id = r.json().get("document_id")
    if not doc_id:
        print("No document_id in response")
        return

    print(f"Fetching chunks for {doc_id} ...")
    cr = get_chunks(url, doc_id)
    print("Chunks status:", cr.status_code)
    if cr.status_code == 200:
        chunks = cr.json()
        print(f"Chunk count: {len(chunks)}")
        if chunks:
            print("First chunk preview:")
            print(chunks[0]["text"][:500])
            emb = chunks[0].get("embedding")
            if emb:
                print(f"Embedding length: {len(emb)}")
    else:
        print(cr.text)


if __name__ == "__main__":
    main()
