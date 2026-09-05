import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import AskPanel from "./AskPanel.jsx";
import LibraryBrowser from "./LibraryBrowser.jsx";
import ResultCard from "./ResultCard.jsx";
import Settings from "./Settings.jsx";

// `??`, not `||`: the production Docker build sets VITE_API_BASE="" on purpose
// (document-service serves the UI and the API from the same origin, so no
// prefix is needed there) -- an empty string must NOT fall back to "/api". The
// dev server (`npm run dev`) leaves VITE_API_BASE unset and relies on
// vite.config.js's own /api proxy to a standalone backend instead.
const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";
const TERMINAL_JOB_STATES = new Set(["done", "partial", "error", "interrupted"]);
const THEME_KEY = "ai-librarian.theme";
const SIDEBAR_KEY = "ai-librarian.sidebar-collapsed";
const ACTIVE_CHAT_KEY = "ai-librarian.ask.active";
const KEYS_KEY = "ai-librarian.ask.keys";
const OLLAMA_KEY = "ai-librarian.ask.ollama-models";

function currentTheme() {
  const stored = document.documentElement.dataset.theme;
  return stored === "light" || stored === "dark" ? stored : null;
}

function systemPrefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

function loadStored(key, fallback, parse = false) {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw != null) return parse ? JSON.parse(raw) : raw;
  } catch (_error) {
    // corrupt or unavailable storage
  }
  return fallback;
}

function saveStored(key, value) {
  try {
    window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch (_error) {
    // best effort
  }
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

// One row in the Indexed tab. Its indexing error (a real failure) or
// extraction notes (an FYI, e.g. pages skipped for a corrupt text layer)
// stay collapsed behind a toggle instead of always taking up space, since
// most documents have neither and the ones that do are the exception.
function DocumentCard({ document, selected, onToggleSelect, onRetry, onRemove, reindexing }) {
  const [notesOpen, setNotesOpen] = useState(false);
  const hasError = Boolean(document.indexing_error);
  const hasNotes = Boolean(document.extraction_notes);

  return (
    <article className={`document-card${selected ? " selected" : ""}`}>
      <div className="document-card-head">
        <input type="checkbox" checked={selected} onChange={onToggleSelect} aria-label={`Select ${document.filename}`} />
        <div className="document-name" title={document.filename}>{document.filename}</div>
      </div>
      <div className="document-meta">
        <span className={`badge ${document.indexing_status}`}>{document.indexing_status}</span>
        <span>{document.pages} pages</span>
        <span>{document.chunks} chunks</span>
      </div>
      {document.source_path && (
        <div className="document-meta"><span title={document.source_path}>↪ {document.source_path}</span></div>
      )}
      {(hasError || hasNotes) && (
        <button
          type="button"
          className={`document-notice-toggle${hasError ? " error" : ""}`}
          onClick={() => setNotesOpen((open) => !open)}
          aria-expanded={notesOpen}
        >
          {hasError ? "Error found" : "Notes"} {notesOpen ? "▴" : "▾"}
        </button>
      )}
      {notesOpen && hasError && <div className="status-message error">{document.indexing_error}</div>}
      {notesOpen && hasNotes && <div className="status-message">{document.extraction_notes}</div>}
      <div className="document-actions">
        {document.indexing_status === "error" && (
          <button className="secondary" type="button" disabled={reindexing} onClick={onRetry}>Retry</button>
        )}
        <button className="danger" type="button" onClick={onRemove}>Remove</button>
      </div>
    </article>
  );
}

// The sidebar's "Chats" tab: a full conversation list (switch/create/delete),
// replacing what used to be a cramped <select> inside the Ask panel itself.
function ChatList({ chats, activeId, onSelect, onNew, onDelete }) {
  const [filter, setFilter] = useState("");
  const query = filter.trim().toLowerCase();
  const visible = query ? chats.filter((chat) => (chat.title || "Untitled").toLowerCase().includes(query)) : chats;
  // No chat selected means exactly this: a new, not-yet-saved conversation
  // is the current one (that's what "+ New chat" sets activeId to). Show it
  // immediately rather than only once its first answer finishes and it gets
  // a real id — the row appearing on click is the point.
  const showDraft = activeId === null && !query;

  return (
    <div className="chat-list">
      <button type="button" className="primary chat-list-new" onClick={onNew}>
        + New chat
      </button>
      {chats.length > 0 && (
        <div className="chat-list-search">
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Search chats…"
            aria-label="Search chats"
          />
        </div>
      )}
      {chats.length === 0 && !showDraft && <p className="chat-list-empty">No conversations yet — ask a question to start one.</p>}
      {chats.length > 0 && visible.length === 0 && !showDraft && <p className="chat-list-empty">No chats match “{filter}”.</p>}
      {showDraft && (
        <div className="chat-list-item active">
          <span className="chat-list-title chat-list-draft">Untitled</span>
        </div>
      )}
      {visible.map((chat) => (
        <div className={`chat-list-item${chat.id === activeId ? " active" : ""}`} key={chat.id}>
          <button type="button" className="chat-list-title" onClick={() => onSelect(chat.id)}>
            {chat.title || "Untitled"}
          </button>
          <button
            type="button"
            className="chat-list-delete"
            aria-label="Delete conversation"
            title="Delete conversation"
            onClick={() => onDelete(chat.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [libraryTab, setLibraryTab] = useState("browse");
  const [sidebarSection, setSidebarSection] = useState("library");
  const [job, setJob] = useState(null);
  const [notice, setNotice] = useState("");
  const [noticeTone, setNoticeTone] = useState("neutral");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [lowConfidence, setLowConfidence] = useState(false);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState(() => new Set());
  const [reindexing, setReindexing] = useState(false);
  const [source, setSource] = useState(null);
  const [askEnabled, setAskEnabled] = useState(false);
  const [view, setView] = useState("search");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => loadStored(SIDEBAR_KEY, "") === "1");
  const [dragActive, setDragActive] = useState(false);
  const dragCounter = useRef(0);
  const [theme, setTheme] = useState(() => currentTheme() || (systemPrefersDark() ? "dark" : "light"));
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Library root (which folder auto-ingest scans) — set from Settings, but
  // also readable/settable from the Browse tree's "Set as library" shortcut.
  const [libraryRoot, setLibraryRoot] = useState(null); // null = not yet resolved
  const [hostPath, setHostPath] = useState("");
  const [settingRoot, setSettingRoot] = useState(false);
  const [rootNotice, setRootNotice] = useState("");
  const [rootError, setRootError] = useState("");

  // Cloud API keys — edited from Settings, consumed by AskPanel.
  const [apiKeys, setApiKeys] = useState(() => loadStored(KEYS_KEY, {}, true) || {});
  // Ollama models the reader has pulled on their own server but that this
  // app doesn't already know about — typed in from Settings, not fetched.
  const [ollamaModels, setOllamaModelsState] = useState(() => loadStored(OLLAMA_KEY, [], true) || []);
  const setOllamaModels = useCallback((models) => {
    setOllamaModelsState(models);
    saveStored(OLLAMA_KEY, models);
  }, []);

  // Chat history — the list lives in the sidebar; AskPanel just renders
  // whichever conversation `activeChatId` points at.
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatIdState] = useState(() => loadStored(ACTIVE_CHAT_KEY, "") || null);

  const api = useCallback(
    async (path, options = {}) => {
      const response = await fetch(`${API_BASE}${path}`, options);
      return parseResponse(response);
    },
    [],
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    saveStored(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    saveStored(SIDEBAR_KEY, sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  useEffect(() => {
    saveStored(KEYS_KEY, apiKeys);
  }, [apiKeys]);

  const setActiveChatId = useCallback((id) => {
    setActiveChatIdState(id);
    saveStored(ACTIVE_CHAT_KEY, id || "");
  }, []);

  const refreshChatList = useCallback(async () => {
    try {
      const data = await api("/conversations");
      setChats(data.conversations || []);
    } catch (_error) {
      // the list is a convenience; failing to load it is not fatal
    }
  }, [api]);

  useEffect(() => {
    refreshChatList();
  }, [refreshChatList]);

  function selectChat(id) {
    setActiveChatId(id);
    setView("ask");
  }

  function newChat() {
    setActiveChatId(null);
    setView("ask");
  }

  async function deleteChat(id) {
    if (!window.confirm("Delete this conversation?")) return;
    try {
      await api(`/conversations/${id}`, { method: "DELETE" });
      if (id === activeChatId) setActiveChatId(null);
      refreshChatList();
    } catch (error) {
      setNotice(error.message);
      setNoticeTone("error");
    }
  }

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

  const refreshDocuments = useCallback(async () => {
    try {
      const data = await api("/documents");
      setDocuments(data.documents || []);
      setNoticeTone("neutral");
    } catch (error) {
      setNotice(error.message);
      setNoticeTone("error");
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
    let cancelled = false;
    fetch(`${API_BASE}/library/root`)
      .then((response) => response.json())
      .then((data) => {
        if (cancelled) return;
        setLibraryRoot(data.path || "");
        setHostPath(data.host_path || "");
      })
      .catch(() => {
        if (!cancelled) setLibraryRoot("");
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
          setNoticeTone(next.state === "done" ? "success" : "error");
          refreshDocuments();
        }
      } catch (error) {
        setNotice(error.message);
        setNoticeTone("error");
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
        setNoticeTone("neutral");
        const form = new FormData();
        form.append("file", file, file.name);
        const result = await api("/documents", { method: "POST", body: form });
        completed += 1;
        setNotice(result.deduplicated ? `${file.name} was already in the library.` : `${file.name} is queued for indexing.`);
      }
      await refreshDocuments();
    } catch (error) {
      setNotice(error.message);
      setNoticeTone("error");
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
      setNoticeTone(pdfs.length === 0 ? "error" : "neutral");
    }
    if (pdfs.length) uploadFiles(pdfs);
  }

  // A convenience only: auto-ingest already scans `libraryRoot` on its own
  // (default every 60s), and setting a new root already triggers an import.
  // This just forces that scan to happen right now instead of waiting.
  async function rescanLibraryFolder() {
    setAttaching(true);
    setNotice("Rescanning the library folder…");
    setNoticeTone("neutral");
    try {
      const result = await api("/library/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: libraryRoot || "." }),
      });
      setJob(result);
      setNotice(`Rescan job ${result.job_id} is queued.`);
    } catch (error) {
      setNotice(error.message);
      setNoticeTone("error");
    } finally {
      setAttaching(false);
    }
  }

  // Shared by Settings (typing a path) and the Browse tree's "Set as
  // library" shortcut. Uses raw fetch (not the `api()` helper) so it can
  // give a specific, actionable message for the two failure modes that
  // actually happen here — outside the mount, or simply not found.
  async function setLibraryFolder(targetPath) {
    setSettingRoot(true);
    setRootNotice("");
    setRootError("");
    try {
      const response = await fetch(`${API_BASE}/library/root`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: targetPath }),
      });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 403) {
          throw new Error(
            `That path is outside what this server can see. It can only reach folders inside ${
              hostPath ? `${hostPath} (its LIBRARY_PATH mount)` : "its /library mount"
            } — widen that mount in .env and restart to reach elsewhere.`,
          );
        }
        if (response.status === 404) {
          throw new Error(
            `That path doesn't exist${hostPath ? ` under ${hostPath}` : ""}. Check the spelling, or that it's really inside what's mounted at /library.`,
          );
        }
        throw new Error(data.detail || `Request failed (${response.status})`);
      }
      setLibraryRoot(data.path || "");
      setRootNotice(data.path ? `Library folder set to “${data.path}”.` : "Library folder reset to the whole mount.");
      const importResult = await api("/library/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: data.path || "." }),
      });
      setJob(importResult);
      return { ok: true, path: data.path || "" };
    } catch (error) {
      setRootError(error.message);
      return { ok: false };
    } finally {
      setSettingRoot(false);
    }
  }

  // Handles both the single-document "Retry" button and the bulk
  // "Reindex selected/all" actions. `/retry` already rebuilds from the
  // source PDF whenever a document's pipeline_version is behind the
  // server's current one — exactly what picking up an extraction fix
  // needs — so this needed no new backend endpoint, just a way to call the
  // existing one for more than one document without one failure stopping
  // the rest of the batch.
  async function reindexDocuments(ids) {
    if (!ids.length || reindexing) return;
    setReindexing(true);
    let succeeded = 0;
    let failed = 0;
    for (const id of ids) {
      setNotice(`Reindexing… (${succeeded + failed + 1}/${ids.length})`);
      setNoticeTone("neutral");
      try {
        await api(`/documents/${id}/retry`, { method: "POST" });
        succeeded += 1;
      } catch (_error) {
        failed += 1;
      }
    }
    setNotice(
      failed
        ? `Reindexed ${succeeded} document${succeeded === 1 ? "" : "s"}; ${failed} failed.`
        : `Queued ${succeeded} document${succeeded === 1 ? "" : "s"} for reindexing.`,
    );
    setNoticeTone(failed ? "error" : "success");
    setSelectedDocs(new Set());
    setReindexing(false);
    refreshDocuments();
  }

  function toggleDocSelected(id) {
    setSelectedDocs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function removeDocument(document) {
    if (!window.confirm(`Remove “${document.filename}” from the library?`)) return;
    try {
      await api(`/documents/${document.document_id}`, { method: "DELETE" });
      setNotice(`${document.filename} was removed.`);
      setNoticeTone("neutral");
      refreshDocuments();
    } catch (error) {
      setNotice(error.message);
      setNoticeTone("error");
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
      setNoticeTone("neutral");
    } catch (error) {
      setResults([]);
      setLowConfidence(false);
      setSearched(true);
      setNotice(error.message);
      setNoticeTone("error");
    } finally {
      setSearching(false);
    }
  }

  const indexedCount = documents.filter((document) => document.indexing_status === "indexed").length;
  const healthReady = health?.status === "ready";
  const showSearchIntro = results.length === 0 && !searching && !searched;

  return (
    <div className="app-root">
      <aside className={`sidebar${sidebarCollapsed ? " collapsed" : ""}`}>
        <button
          type="button"
          className="sidebar-toggle"
          onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
          aria-label={sidebarCollapsed ? "Expand library panel" : "Collapse library panel"}
          title={sidebarCollapsed ? "Expand library panel" : "Collapse library panel"}
        >
          {sidebarCollapsed ? "»" : "«"}
        </button>

        <div className="sidebar-body">
          {askEnabled && (
            <div className="panel-tabs sidebar-section-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={sidebarSection === "library"}
                className={sidebarSection === "library" ? "active" : ""}
                onClick={() => setSidebarSection("library")}
              >
                Library
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={sidebarSection === "chats"}
                className={sidebarSection === "chats" ? "active" : ""}
                onClick={() => setSidebarSection("chats")}
              >
                Chats{chats.length ? ` (${chats.length})` : ""}
              </button>
            </div>
          )}

          {sidebarSection === "chats" && askEnabled ? (
            <ChatList
              chats={chats}
              activeId={activeChatId}
              onSelect={selectChat}
              onNew={newChat}
              onDelete={deleteChat}
            />
          ) : (
            <>
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

                  <button
                    type="button"
                    className="link rescan-link"
                    disabled={attaching}
                    onClick={rescanLibraryFolder}
                    title="Auto-ingest already scans the library folder on its own — this just forces it now instead of waiting."
                  >
                    {attaching ? "Rescanning…" : "↻ Rescan library folder now"}
                  </button>

                  <LibraryBrowser
                    apiBase={API_BASE}
                    onImported={refreshDocuments}
                    onJob={setJob}
                    libraryRoot={libraryRoot}
                    settingRoot={settingRoot}
                    onSetLibraryFolder={setLibraryFolder}
                  />
                </>
              ) : (
                <div className="document-list">
                  {documents.length === 0 && <p className="muted">Nothing indexed yet. Import a PDF from the Browse tab.</p>}
                  {documents.length > 0 && (
                    <div className="document-list-actions">
                      <label className="document-select-all">
                        <input
                          type="checkbox"
                          ref={(el) => {
                            if (el) el.indeterminate = selectedDocs.size > 0 && selectedDocs.size < documents.length;
                          }}
                          checked={documents.length > 0 && selectedDocs.size === documents.length}
                          onChange={(event) =>
                            setSelectedDocs(event.target.checked ? new Set(documents.map((d) => d.document_id)) : new Set())
                          }
                        />
                        <span>{selectedDocs.size ? `${selectedDocs.size} selected` : "Select all"}</span>
                      </label>
                      <button
                        type="button"
                        className="link"
                        disabled={reindexing || selectedDocs.size === 0}
                        onClick={() => reindexDocuments([...selectedDocs])}
                      >
                        Reindex selected
                      </button>
                      <button
                        type="button"
                        className="link"
                        disabled={reindexing}
                        onClick={() => reindexDocuments(documents.map((d) => d.document_id))}
                        title="Re-extracts and re-chunks every document from its source PDF — worth doing after an extraction-quality fix."
                      >
                        Reindex all
                      </button>
                    </div>
                  )}
                  {documents.map((document) => (
                    <DocumentCard
                      key={document.document_id}
                      document={document}
                      selected={selectedDocs.has(document.document_id)}
                      onToggleSelect={() => toggleDocSelected(document.document_id)}
                      onRetry={() => reindexDocuments([document.document_id])}
                      onRemove={() => removeDocument(document)}
                      reindexing={reindexing}
                    />
                  ))}
                </div>
              )}

              {notice && <div className={`status-message ${noticeTone !== "neutral" ? noticeTone : ""}`}>{notice}</div>}

              {job?.files?.length > 0 && !TERMINAL_JOB_STATES.has(job.state) && (() => {
                const done = job.files.filter((file) => ["indexed", "duplicate"].includes(file.status)).length;
                const percent = Math.round((done / job.files.length) * 100);
                return (
                  <div className="status-message job-progress">
                    <div className="job-progress-label">
                      <span>{done}/{job.files.length} files ready</span>
                      <span>{percent}%</span>
                    </div>
                    <div className="job-progress-bar">
                      <div className="job-progress-fill" style={{ width: `${percent}%` }} />
                    </div>
                  </div>
                );
              })()}
            </>
          )}
        </div>
      </aside>

      <div className="main-area">
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
            <div className="settings-anchor">
              <button
                type="button"
                className="settings-trigger"
                onClick={() => setSettingsOpen((open) => !open)}
                aria-label="Settings"
                aria-expanded={settingsOpen}
                title="Settings"
              >
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7zm8.94-3.5c0-.61-.06-1.2-.16-1.78l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.6 7.6 0 0 0-1.54-.89l-.36-2.54a.5.5 0 0 0-.5-.43h-3.84a.5.5 0 0 0-.5.43l-.36 2.54c-.55.23-1.07.53-1.54.89l-2.39-.96a.5.5 0 0 0-.6.22L2.57 7.99a.5.5 0 0 0 .12.64l2.03 1.58c-.1.58-.16 1.17-.16 1.78s.06 1.2.16 1.78l-2.03 1.6a.5.5 0 0 0-.12.64l1.92 3.32c.13.22.39.31.6.22l2.39-.97c.47.37.99.67 1.54.9l.36 2.54c.05.24.26.43.5.43h3.84c.24 0 .45-.19.5-.43l.36-2.54c.55-.23 1.07-.53 1.54-.9l2.39.97c.22.09.48 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.6c.1-.58.16-1.17.16-1.78z"
                  />
                </svg>
              </button>
              {settingsOpen && (
                <Settings
                  theme={theme}
                  onThemeChange={setTheme}
                  apiKeys={apiKeys}
                  setApiKeys={setApiKeys}
                  ollamaModels={ollamaModels}
                  setOllamaModels={setOllamaModels}
                  libraryRoot={libraryRoot}
                  hostPath={hostPath}
                  settingRoot={settingRoot}
                  rootNotice={rootNotice}
                  rootError={rootError}
                  onSetLibraryFolder={setLibraryFolder}
                  documentCount={documents.length}
                  reindexing={reindexing}
                  onReindexAll={() => reindexDocuments(documents.map((d) => d.document_id))}
                  onClose={() => setSettingsOpen(false)}
                />
              )}
            </div>
          </div>
        </header>

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
            <AskPanel
              apiBase={API_BASE}
              onViewSource={setSource}
              indexedCount={indexedCount}
              activeId={activeChatId}
              onActiveIdChange={setActiveChatId}
              onConversationsChanged={refreshChatList}
              apiKeys={apiKeys}
              ollamaModels={ollamaModels}
            />
          ) : (
            <>
              {showSearchIntro && (
                <div className="chat-header">
                  <h2>Search your library</h2>
                  <p>Semantic search finds and reranks the most relevant source passages.</p>
                </div>
              )}
              <div className="messages" aria-live="polite">
                {showSearchIntro && (
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
    </div>
  );
}
