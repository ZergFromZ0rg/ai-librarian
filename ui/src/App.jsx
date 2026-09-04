import React, { useCallback, useEffect, useState } from "react";

import AskPanel from "./AskPanel.jsx";
import LibraryBrowser from "./LibraryBrowser.jsx";
import ResultCard from "./ResultCard.jsx";

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
  const [libraryTab, setLibraryTab] = useState("browse");
  const [job, setJob] = useState(null);
  const [notice, setNotice] = useState("");
  const [noticeError, setNoticeError] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [lowConfidence, setLowConfidence] = useState(false);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [source, setSource] = useState(null);
  const [askEnabled, setAskEnabled] = useState(false);
  const [view, setView] = useState("search");

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
    let cancelled = false;
    api("/config")
      .then((config) => {
        if (!cancelled) setAskEnabled(Boolean(config?.generation?.enabled));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [api]);

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

  async function attachLibraryFolder() {
    setAttaching(true);
    setNotice("Queueing the whole library folder for import…");
    setNoticeError(false);
    try {
      const result = await api("/library/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: "." }),
      });
      setJob(result);
      setNotice(`Import job ${result.job_id} is queued.`);
    } catch (error) {
      setNotice(error.message);
      setNoticeError(true);
    } finally {
      setAttaching(false);
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
        body: JSON.stringify({ query: cleanQuery, top_k: 10, rerank: true, rerank_k: 20, max_text_chars: 20000 }),
      });
      setResults(result.results || []);
      setLowConfidence(Boolean(result.low_confidence));
      setSearched(true);
      setNotice(`Found ${result.results?.length || 0} relevant passages.`);
      setNoticeError(false);
    } catch (error) {
      setResults([]);
      setLowConfidence(false);
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

          <div className="panel-tabs library-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={libraryTab === "browse"}
              className={libraryTab === "browse" ? "active" : ""}
              onClick={() => setLibraryTab("browse")}
            >
              Browse
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={libraryTab === "indexed"}
              className={libraryTab === "indexed" ? "active" : ""}
              onClick={() => setLibraryTab("indexed")}
            >
              Indexed{documents.length ? ` (${documents.length})` : ""}
            </button>
          </div>

          {libraryTab === "browse" ? (
            <>
              <div className="library-attach">
                <button
                  type="button"
                  className="primary"
                  disabled={attaching}
                  onClick={attachLibraryFolder}
                >
                  {attaching ? "Attaching…" : "Attach main library folder"}
                </button>
                <p className="muted">Recursively imports every PDF under the mounted library folder, in place — nothing is copied.</p>
                <label className="upload-link">
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
                  <span>{uploading ? "Uploading…" : "or upload a PDF file"}</span>
                </label>
              </div>

              <LibraryBrowser apiBase={API_BASE} onImported={refreshDocuments} onJob={setJob} />
            </>
          ) : (
            <div className="document-list">
              {documents.length === 0 && <p className="muted">Nothing indexed yet. Import a PDF from the Browse tab.</p>}
              {documents.map((document) => (
                <article className="document-card" key={document.document_id}>
                  <div className="document-name" title={document.filename}>{document.filename}</div>
                  <div className="document-meta">
                    <span className={`badge ${document.indexing_status}`}>{document.indexing_status}</span>
                    <span>{document.pages} pages</span>
                    <span>{document.chunks} chunks</span>
                  </div>
                  {document.source_path && (
                    <div className="document-meta"><span title={document.source_path}>↪ {document.source_path}</span></div>
                  )}
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
          )}

          {notice && <div className={`status-message ${noticeError ? "error" : ""}`}>{notice}</div>}

          {job?.files?.length > 0 && !TERMINAL_JOB_STATES.has(job.state) && (
            <div className="status-message">
              {job.files.filter((file) => ["indexed", "duplicate"].includes(file.status)).length}/{job.files.length} files ready
            </div>
          )}
        </section>

        <section className="panel chat-panel">
          {askEnabled && (
            <div className="panel-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={view === "search"}
                className={view === "search" ? "active" : ""}
                onClick={() => setView("search")}
              >
                Search
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={view === "ask"}
                className={view === "ask" ? "active" : ""}
                onClick={() => setView("ask")}
              >
                Ask
              </button>
            </div>
          )}

          {view === "ask" && askEnabled ? (
            <AskPanel apiBase={API_BASE} onViewSource={setSource} indexedCount={indexedCount} />
          ) : (
            <>
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
                {results.length > 0 && lowConfidence && (
                  <div className="empty-state low-confidence">
                    <div>
                      <strong>Low confidence.</strong>
                      Nothing in your library scored as a clear match for this query — the
                      passages below are the closest available, not necessarily an answer.
                    </div>
                  </div>
                )}
                {results.map((result) => (
                  <ResultCard
                    key={`${result.document_id}-${result.chunk_id}`}
                    result={result}
                    onViewSource={setSource}
                  />
                ))}
                {searching && <div className="message assistant">Finding the most relevant passages…</div>}
              </div>
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
            </>
          )}
          {source && <SourceViewer source={source} onClose={() => setSource(null)} />}
        </section>
      </div>
    </main>
  );
}
