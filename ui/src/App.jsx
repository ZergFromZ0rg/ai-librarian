import React, { useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function App() {
  const [folder, setFolder] = useState("/app/test");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [ingestJob, setIngestJob] = useState(null);
  const [ingestFiles, setIngestFiles] = useState([]);
  const [uploadList, setUploadList] = useState([]);

  async function handleIngest() {
    setStatus("Queueing ingestion job...");
    setResults([]);
    try {
      const res = await fetch(`${API_BASE}/admin/ingest-folder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder, full_pipeline: true }),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus(`Error: ${data.detail || JSON.stringify(data)}`);
        return;
      }
      const jobId = data.job_id;
      setIngestJob(jobId);
      setStatus(`Job queued: ${jobId}`);
      // poll status
      const poll = setInterval(async () => {
        const sr = await fetch(`${API_BASE}/admin/ingest-status/${jobId}`);
        const sd = await sr.json();
        if (!sr.ok) {
          setStatus(`Status error: ${sd.detail || JSON.stringify(sd)}`);
          clearInterval(poll);
          return;
        }
        setIngestFiles(sd.files || []);
        setStatus(`Job ${jobId} state: ${sd.state}`);
        if (sd.state === "done" || sd.state === "error") {
          clearInterval(poll);
          setIngestJob(null);
        }
      }, 2000);
    } catch (e) {
      setStatus("Ingest failed: " + e.message);
    }
  }

  async function handleFileUpload(files) {
    const list = Array.from(files).map((f) => ({ name: f.name, status: "queued" }));
    setUploadList(list);
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      try {
        setUploadList((s) => s.map((it, idx) => (idx === i ? { ...it, status: "uploading" } : it)));
        const form = new FormData();
        form.append("file", f, f.name);
        const r = await fetch(`${API_BASE}/documents`, { method: "POST", body: form });
        const d = await r.json();
        if (!r.ok) {
          setUploadList((s) => s.map((it, idx) => (idx === i ? { ...it, status: "error", error: d.detail || JSON.stringify(d) } : it)));
        } else {
          setUploadList((s) => s.map((it, idx) => (idx === i ? { ...it, status: "done", document_id: d.document_id } : it)));
        }
      } catch (e) {
        setUploadList((s) => s.map((it, idx) => (idx === i ? { ...it, status: "error", error: e.message } : it)));
      }
    }
  }

  async function handleSearch(e) {
    e.preventDefault();
    setStatus("Searching...");
    setResults([]);
    try {
      const res = await fetch(`${API_BASE}/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, top_k: 10, rerank: true, rerank_k: 5 }),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus(`Error: ${data.detail || JSON.stringify(data)}`);
        return;
      }
      setResults(data.results || []);
      setStatus(`Found ${data.results.length} results`);
    } catch (e) {
      setStatus("Search failed: " + e.message);
    }
  }

  return (
    <div style={{ padding: 24, fontFamily: "Arial, sans-serif" }}>
      <h2>AI Librarian — Test UI</h2>

      <div style={{ marginBottom: 16 }}>
        <label>Library folder (server-side): </label>
        <input style={{ width: 400 }} value={folder} onChange={(e) => setFolder(e.target.value)} />
        <button style={{ marginLeft: 8 }} onClick={handleIngest}>
          Ingest (async)
        </button>
        <div style={{ marginTop: 8 }}>
          <label>Upload PDFs from browser: </label>
          <input type="file" multiple accept="application/pdf" onChange={(e) => handleFileUpload(e.target.files)} />
          <div>
            {uploadList.map((u, i) => (
              <div key={i} style={{ fontSize: 12 }}>
                {u.name}: {u.status} {u.document_id ? `(${u.document_id})` : ''} {u.error ? ` - ${u.error}` : ''}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <form onSubmit={handleSearch}>
          <input style={{ width: 400 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ask a question (e.g. what is absurdism)" />
          <button style={{ marginLeft: 8 }} type="submit">Search</button>
        </form>
          <div style={{ marginTop: 8, color: "#666" }}>{status}</div>
          {ingestFiles.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <strong>Ingest progress:</strong>
              <ul>
                {ingestFiles.map((f, i) => (
                  <li key={i}>{f.file}: {f.status} {f.document_id ? ` - ${f.document_id}` : ''} {f.error ? ` - ${f.error}` : ''}</li>
                ))}
              </ul>
            </div>
          )}
      </div>

      <div style={{ maxHeight: 400, overflow: "auto", border: "1px solid #ddd", padding: 12 }}>
        {results.map((r, i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: "#888" }}>{r.document} — page {r.page} — score {r.score?.toFixed(3)}</div>
            <div style={{ whiteSpace: "pre-wrap" }}>{r.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
