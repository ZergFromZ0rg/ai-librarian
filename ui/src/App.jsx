import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import AskPanel from "./AskPanel.jsx";
import LibraryBrowser from "./LibraryBrowser.jsx";
import ResultCard from "./ResultCard.jsx";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";
const TERMINAL_JOB_STATES = new Set(["done", "partial", "error", "interrupted"]);
const THEME_KEY = "ai-librarian.theme";
const SIDEBAR_KEY = "ai-librarian.sidebar-collapsed";

// Reads whatever the inline head script already applied to <html>, so the
// toggle's first render matches the page instead of flashing to the default.
function currentTheme() {
  const stored = document.documentElement.dataset.theme;
  return stored === "light" || stored === "dark" ? stored : null;
}

function systemPrefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

// A two-state toggle (not a tri-state incl. "system"): once someone picks a
// theme explicitly we remember that choice, but the very first press just
// flips away from whatever the OS preference currently resolves to.
function ThemeToggle() {
  const [theme, setTheme] = useState(() => currentTheme() || (systemPrefersDark() ? "dark" : "light"));

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch (_error) {
      // best effort
    }
  }, [theme]);

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

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

  // Rendered through a portal straight onto <body>: any ancestor with its own
  // filter/transform/backdrop-filter turns `position: fixed` into "fixed to
  // that ancestor" instead of the viewport, which would silently
  // shrink/misposition this overlay if it were ever nested inside such a
  // panel. A portal sidesteps that regardless of what the panels do.
  return createPortal(
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
    </div>,
    document.body,
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(SIDEBAR_KEY) === "1";
    } catch (_error) {
      return false;
    }
  });
  const [dragActive, setDragActive] = useState(false);
  const dragCounter = useRef(0);

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed ? "1" : "0");
    } catch (_error) {
      // best effort
    }
  }, [sidebarCollapsed]);

  // The browser's default reaction to a dropped file is to navigate to it —
  // block that everywhere so a drop that misses the dropzone doesn't blow
  // away the app instead of just being ignored.
  useEffect(() => {
    const preventDefault = (event) => event.preventDefault();
    window.addEventListener("dragover", preventDefault);
    window.addEventListener("drop", preventDefault);
    return () => {
      window.removeEventListener("dragover", preventDefault);
      window.removeEventListener("drop", preventDefault);
    };
  }, []);

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

  // Counted rather than toggled on enter/leave: dragging over a child
  // element fires leave-then-enter on the parent, which would otherwise flip
  // drag-active off and back on and make the dropzone flicker.
  function handleDragEnter(event) {
    event.preventDefault();
    dragCounter.current += 1;
    setDragActive(true);
  }

  function handleDragOver(event) {
    event.preventDefault();
  }

  function handleDragLeave(event) {
    event.preventDefault();
    dragCounter.current = Math.max(0, dragCounter.current - 1);
    if (dragCounter.current === 0) setDragActive(false);
  }

  function handleDrop(event) {
    event.preventDefault();
    dragCounter.current = 0;
    setDragActive(false);
    if (uploading) return;
    // Unlike the file picker's `accept`, a drop isn't filtered by the
    // browser — anything from the desktop can land here.
    const dropped = Array.from(event.dataTransfer.files || []);
    const pdfs = dropped.filter((file) => file.type === "application/pdf" || /\.pdf$/i.test(file.name));
    if (pdfs.length < dropped.length) {
      setNotice(dropped.length === 1 ? "Only PDF files can be added." : `Skipped ${dropped.length - pdfs.length} non-PDF file(s).`);
      setNoticeError(pdfs.length === 0);
    }
    if (pdfs.length) uploadFiles(pdfs);
  }

  async function attachLibraryFolder() {
    setAttaching(true);
    setNotice("Queueing the library folder for import…");
    setNoticeError(false);
    try {
      // Target whatever folder is currently designated as the library
      // (narrowed from the /library mount via Browse's "Set as library
      // folder", or the whole mount if it was never narrowed).
      const root = await api("/library/root");
      const result = await api("/library/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: root.path || "." }),
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
        <div className="topbar-status">
          <div className="health" title={`Qdrant: ${health?.qdrant ? "ready" : "offline"}`}>
            <span className={`health-dot ${healthReady ? "ready" : ""}`} />
            {healthReady ? "Library ready" : health?.status || "Connecting"}
          </div>
          <ThemeToggle />
        </div>
      </header>

      <div className={`layout${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
        <section className={`panel library-panel${sidebarCollapsed ? " collapsed" : ""}`}>
          <button
            type="button"
            className="sidebar-toggle"
            onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
            aria-label={sidebarCollapsed ? "Expand library panel" : "Collapse library panel"}
            title={sidebarCollapsed ? "Expand library panel" : "Collapse library panel"}
          >
            {sidebarCollapsed ? "»" : "«"}
          </button>

          <div className="library-panel-body">
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
                </div>

                <label
                  className={`dropzone${dragActive ? " drag-active" : ""}${uploading ? " busy" : ""}`}
                  onDragEnter={handleDragEnter}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                >
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
                  <span className="dropzone-icon" aria-hidden="true">⇪</span>
                  <span>
                    {uploading ? "Uploading…" : dragActive ? "Drop to upload" : (
                      <>Drop PDFs here, or <strong>browse</strong></>
                    )}
                  </span>
                </label>

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
          </div>
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
