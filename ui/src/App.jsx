import React, { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { makeMatchHighlighter } from "./highlight.js";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";
const TERMINAL_JOB_STATES = new Set(["done", "partial", "error", "interrupted"]);

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : { detail: await response.text() };
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

// A lightbox over a server-rendered page image with the matched passage
// highlighted, plus a link out to the raw PDF at the same page.
function SourceViewer({ source, onClose }) {
  const { documentId, documentName, page: startPage, matched, snippet } = source;
  const [page, setPage] = useState(startPage);
  const [pageCount, setPageCount] = useState(null);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/documents/${documentId}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((meta) => {
        if (!cancelled && meta && meta.pages) setPageCount(meta.pages);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  const step = useCallback(
    (delta) =>
      setPage((current) => {
        const next = current + delta;
        if (next < 1) return current;
        if (pageCount && next > pageCount) return current;
        return next;
      }),
    [pageCount],
  );

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, step]);

  const highlight = (matched || snippet || "").slice(0, 600);
  const imageSrc = `${API_BASE}/documents/${documentId}/page/${page}?highlight=${encodeURIComponent(highlight)}`;

  useEffect(() => {
    setStatus("loading");
  }, [imageSrc]);

  return (
    <div className="source-overlay" onClick={onClose}>
      <div className="source-panel" onClick={(event) => event.stopPropagation()}>
        <div className="source-bar">
          <strong className="source-title" title={documentName}>
            {documentName}
          </strong>
          <div className="source-nav">
            <button type="button" onClick={() => step(-1)} disabled={page <= 1} aria-label="Previous page">
              ‹
            </button>
            <span>
              page {page}
              {pageCount ? ` / ${pageCount}` : ""}
            </span>
            <button
              type="button"
              onClick={() => step(1)}
              disabled={pageCount ? page >= pageCount : false}
              aria-label="Next page"
            >
              ›
            </button>
          </div>
          <a
            className="source-open"
            href={`${API_BASE}/documents/${documentId}/file#page=${page}`}
            target="_blank"
            rel="noreferrer"
          >
            Open PDF ↗
          </a>
          <button type="button" className="source-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="source-view">
          {status === "loading" && <div className="source-status">Rendering page…</div>}
          {status === "error" && <div className="source-status error">Could not render this page.</div>}
          <img
            key={imageSrc}
            src={imageSrc}
            alt={`${documentName}, page ${page}`}
            onLoad={() => setStatus("ready")}
            onError={() => setStatus("error")}
            style={status === "error" ? { display: "none" } : undefined}
          />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [folder, setFolder] = useState(".");
  const [job, setJob] = useState(null);
  const [notice, setNotice] = useState("");
  const [noticeError, setNoticeError] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [source, setSource] = useState(null);

  const api = useCallback(
    async (path, options = {}) => {
      const response = await fetch(`${API_BASE}${path}`, options);
      return parseResponse(response);
    },
    [],
  );

  const refreshDocuments = useCallback(async () => {
    try {
      const data = await api("/documents");
      setDocuments(data.documents || []);
      setNoticeError(false);
    } catch (error) {
      setNotice(error.message);
      setNoticeError(true);
    }
  }, [api]);

  const refreshHealth = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/health/ready`);
      const data = await response.json();
      setHealth(data);
    } catch (_error) {
      setHealth({ status: "offline", qdrant: false });
    }
  }, []);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  useEffect(() => {
    refreshDocuments();
  }, [refreshDocuments]);

  useEffect(() => {
    const hasPending = documents.some((document) => ["queued", "indexing"].includes(document.indexing_status));
    if (!hasPending) return undefined;
    const timer = window.setInterval(refreshDocuments, 2500);
    return () => window.clearInterval(timer);
  }, [documents, refreshDocuments]);

  useEffect(() => {
    if (!job || TERMINAL_JOB_STATES.has(job.state)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await api(`/admin/ingest-status/${job.job_id}`);
        setJob(next);
        if (TERMINAL_JOB_STATES.has(next.state)) {
          setNotice(`Folder ingestion finished with status: ${next.state}.`);
          setNoticeError(["partial", "error", "interrupted"].includes(next.state));
          refreshDocuments();
        }
      } catch (error) {
        setNotice(error.message);
        setNoticeError(true);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [api, job, refreshDocuments]);

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setUploading(true);
    let completed = 0;
    try {
      for (const file of files) {
        setNotice(`Uploading ${file.name}… (${completed + 1}/${files.length})`);
        setNoticeError(false);
        const form = new FormData();
        form.append("file", file, file.name);
        const result = await api("/documents", { method: "POST", body: form });
        completed += 1;
        setNotice(result.deduplicated ? `${file.name} was already in the library.` : `${file.name} is queued for indexing.`);
      }
      await refreshDocuments();
    } catch (error) {
      setNotice(error.message);
      setNoticeError(true);
    } finally {
      setUploading(false);
    }
  }

  async function ingestFolder() {
    setNotice("Queueing folder ingestion…");
    setNoticeError(false);
    try {
      const result = await api("/admin/ingest-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: folder.trim() || "." }),
      });
      setJob(result);
      setNotice(`Folder job ${result.job_id} is queued.`);
    } catch (error) {
      setNotice(error.message);
      setNoticeError(true);
    }
  }

  async function retryDocument(documentId) {
    try {
      await api(`/documents/${documentId}/retry`, { method: "POST" });
      setNotice("Document queued for another indexing attempt.");
      setNoticeError(false);
      refreshDocuments();
    } catch (error) {
      setNotice(error.message);
      setNoticeError(true);
    }
  }

  async function removeDocument(document) {
    if (!window.confirm(`Remove “${document.filename}” from the library?`)) return;
    try {
      await api(`/documents/${document.document_id}`, { method: "DELETE" });
      setNotice(`${document.filename} was removed.`);
      setNoticeError(false);
      refreshDocuments();
    } catch (error) {
      setNotice(error.message);
      setNoticeError(true);
    }
  }

  async function searchLibrary(event) {
    event.preventDefault();
    const cleanQuery = query.trim();
    if (!cleanQuery || searching) return;
    setSearching(true);
    try {
      const result = await api("/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: cleanQuery, top_k: 8, rerank: true, rerank_k: 20, max_text_chars: 20000 }),
      });
      setResults(result.results || []);
      setSearched(true);
      setNotice(`Found ${result.results?.length || 0} relevant passages.`);
      setNoticeError(false);
    } catch (error) {
      setResults([]);
      setSearched(true);
      setNotice(error.message);
      setNoticeError(true);
    } finally {
      setSearching(false);
    }
  }

  const indexedCount = documents.filter((document) => document.indexing_status === "indexed").length;
  const healthReady = health?.status === "ready";

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <h1>AI Librarian</h1>
          <p>Your private, searchable reading room.</p>
        </div>
        <div className="health" title={`Qdrant: ${health?.qdrant ? "ready" : "offline"}`}>
          <span className={`health-dot ${healthReady ? "ready" : ""}`} />
          {healthReady ? "Library ready" : health?.status || "Connecting"}
        </div>
      </header>

      <div className="layout">
        <section className="panel library-panel">
          <div className="panel-title">
            <h2>Library</h2>
            <span className="muted">{indexedCount} ready · {documents.length} total</span>
          </div>

          <label className="drop-zone">
            <input
              type="file"
              multiple
              accept="application/pdf,.pdf"
              disabled={uploading}
              onChange={(event) => {
                uploadFiles(event.target.files);
                event.target.value = "";
              }}
            />
            <strong>{uploading ? "Uploading…" : "Choose PDF files"}</strong>
            <span>Files are indexed locally on your server.</span>
          </label>

          <div className="field-row">
            <input
              aria-label="Folder inside the mounted library"
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
              placeholder="Folder inside /library"
            />
            <button className="secondary" type="button" onClick={ingestFolder}>Import</button>
          </div>

          {notice && <div className={`status-message ${noticeError ? "error" : ""}`}>{notice}</div>}

          {job?.files?.length > 0 && !TERMINAL_JOB_STATES.has(job.state) && (
            <div className="status-message">
              {job.files.filter((file) => ["indexed", "duplicate"].includes(file.status)).length}/{job.files.length} files ready
            </div>
          )}

          <div className="document-list">
            {documents.length === 0 && <p className="muted">No documents yet. Add a PDF to begin.</p>}
            {documents.map((document) => (
              <article className="document-card" key={document.document_id}>
                <div className="document-name" title={document.filename}>{document.filename}</div>
                <div className="document-meta">
                  <span className={`badge ${document.indexing_status}`}>{document.indexing_status}</span>
                  <span>{document.pages} pages</span>
                  <span>{document.chunks} chunks</span>
                </div>
                {document.indexing_error && <div className="status-message error">{document.indexing_error}</div>}
                {document.extraction_notes && <div className="status-message">{document.extraction_notes}</div>}
                <div className="document-actions">
                  {document.indexing_status === "error" && (
                    <button className="secondary" type="button" onClick={() => retryDocument(document.document_id)}>Retry</button>
                  )}
                  <button className="danger" type="button" onClick={() => removeDocument(document)}>Remove</button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="panel chat-panel">
          <div className="chat-header">
            <h2>Search your library</h2>
            <p>Semantic search finds and reranks the most relevant source passages.</p>
          </div>
          <div className="messages" aria-live="polite">
            {results.length === 0 && !searching && !searched && (
              <div className="empty-state">
                <div>
                  <strong>What are you looking for?</strong>
                  Add a document, wait for it to be indexed, then search across your collection.
                </div>
              </div>
            )}
            {results.length === 0 && !searching && searched && (
              <div className="empty-state">
                <div>
                  <strong>No relevant passages found.</strong>
                  Nothing in your library matched this closely enough. Try rephrasing, or add a
                  document that covers the topic.
                </div>
              </div>
            )}
            {results.map((result) => {
              const highlighter = makeMatchHighlighter(result.matched);
              return (
                <article className="search-result" key={`${result.document_id}-${result.chunk_id}`}>
                  <div className="search-result-meta">
                    <strong>{result.document}</strong>
                    <span>
                      {result.page_end && result.page_end !== result.page
                        ? `pages ${result.page}–${result.page_end}`
                        : `page ${result.page}`}
                    </span>
                    <span>score {(result.rerank_score ?? result.score)?.toFixed(3)}</span>
                    <button
                      type="button"
                      className="source-link"
                      onClick={() =>
                        setSource({
                          documentId: result.document_id,
                          documentName: result.document,
                          page: result.page,
                          matched: result.matched,
                          snippet: result.text,
                        })
                      }
                    >
                      view source ↗
                    </button>
                  </div>
                  {result.lead_in && (
                    <p className="search-result-leadin">…{result.lead_in}</p>
                  )}
                  <div className="search-result-text">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      rehypePlugins={highlighter ? [highlighter] : []}
                      skipHtml
                    >
                      {result.text}
                    </ReactMarkdown>
                  </div>
                </article>
              );
            })}
            {searching && <div className="message assistant">Finding the most relevant passages…</div>}
          </div>
          {source && <SourceViewer source={source} onClose={() => setSource(null)} />}
          <form className="question-box" onSubmit={searchLibrary}>
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form.requestSubmit();
                }
              }}
              placeholder="Search concepts, passages, names, or ideas…"
              aria-label="Search query"
            />
            <button className="primary" type="submit" disabled={!query.trim() || searching || indexedCount === 0}>
              {searching ? "Searching…" : "Search"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
