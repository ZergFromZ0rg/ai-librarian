import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import AskThread, { AskRail } from "./AskThread.jsx";
import Console from "./Console.jsx";
import BookRow, { SHELF_SIZE } from "./BookRow.jsx";
import Notebook from "./Notebook.jsx";
import SearchResults from "./SearchResults.jsx";
import Settings from "./Settings.jsx";
import SourceViewer from "./SourceViewer.jsx";
import Stacks from "./Stacks.jsx";
import {
  clearInquiries,
  loadInquiries,
  loadRecents,
  loadStored,
  recordInquiry,
  recordOpen,
  saveStored,
  shelfItems,
} from "./storage.js";
import useAsk from "./useAsk.js";

// `??`, not `||`: the production Docker build sets VITE_API_BASE="" on purpose
// (document-service serves the UI and the API from the same origin, so no
// prefix is needed there) -- an empty string must NOT fall back to "/api". The
// dev server (`npm run dev`) leaves VITE_API_BASE unset and relies on
// vite.config.js's own /api proxy to a standalone backend instead.
const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";
const TERMINAL_JOB_STATES = new Set(["done", "partial", "error", "interrupted"]);
const THEME_KEY = "ai-librarian.theme";
const ACTIVE_CHAT_KEY = "ai-librarian.ask.active";
const KEYS_KEY = "ai-librarian.ask.keys";
const OLLAMA_KEY = "ai-librarian.ask.ollama-models";
const MODE_KEY = "ai-librarian.mode";
const SEARCH_PARAMS_KEY = "ai-librarian.search.params";
const ASK_PARAMS_KEY = "ai-librarian.ask.params";
const LEGACY_THOROUGH_KEY = "ai-librarian.ask.thorough";

// Server defaults until /config says otherwise (RERANK_MIN_SCORE and
// ASK_THOROUGH_MIN_SCORE).
const DEFAULT_FLOORS = { search: -2, thorough: -5 };

function currentTheme() {
  const stored = document.documentElement.dataset.theme;
  return stored === "light" || stored === "dark" ? stored : null;
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : { detail: await response.text() };
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

function loadSearchParams() {
  const stored = loadStored(SEARCH_PARAMS_KEY, null, true) || {};
  return {
    topK: Number.isInteger(stored.topK) ? stored.topK : 10,
    minScore: typeof stored.minScore === "number" ? stored.minScore : null, // null until /config resolves the default
    perDoc: [0, 1, 3].includes(stored.perDoc) ? stored.perDoc : 0,
  };
}

function loadAskParams() {
  const stored = loadStored(ASK_PARAMS_KEY, null, true) || {};
  const legacyThorough = loadStored(LEGACY_THOROUGH_KEY, "") === "1";
  return {
    mode: stored.mode === "thorough" || stored.mode === "quick" ? stored.mode : legacyThorough ? "thorough" : "quick",
    topK: Number.isInteger(stored.topK) ? stored.topK : 10,
    minScore: typeof stored.minScore === "number" ? stored.minScore : null, // null = server default for the depth
  };
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7zm8.94-3.5c0-.61-.06-1.2-.16-1.78l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.6 7.6 0 0 0-1.54-.89l-.36-2.54a.5.5 0 0 0-.5-.43h-3.84a.5.5 0 0 0-.5.43l-.36 2.54c-.55.23-1.07.53-1.54.89l-2.39-.96a.5.5 0 0 0-.6.22L2.57 7.99a.5.5 0 0 0 .12.64l2.03 1.58c-.1.58-.16 1.17-.16 1.78s.06 1.2.16 1.78l-2.03 1.6a.5.5 0 0 0-.12.64l1.92 3.32c.13.22.39.31.6.22l2.39-.97c.47.37.99.67 1.54.9l.36 2.54c.05.24.26.43.5.43h3.84c.24 0 .45-.19.5-.43l.36-2.54c.55-.23 1.07-.53 1.54-.9l2.39.97c.22.09.48 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.6c.1-.58.16-1.17.16-1.78z"
      />
    </svg>
  );
}

function LibraryIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 4h4v16H4zM10 4h4v16h-4zM16.5 4.5l3.8 1-3.9 14.6-3.8-1" />
    </svg>
  );
}

function NotebookIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
      <rect x="5" y="3" width="14" height="18" rx="1.5" />
      <path d="M9 3v18M12 8h4M12 12h4" />
    </svg>
  );
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [askEnabled, setAskEnabled] = useState(false);
  const [floors, setFloors] = useState(DEFAULT_FLOORS);
  const [theme, setTheme] = useState(() => currentTheme() || "light");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [notebookOpen, setNotebookOpen] = useState(false);

  // Collection maintenance: uploads, rescans, folder-import jobs, reindexing.
  const [job, setJob] = useState(null);
  const [notice, setNotice] = useState({ text: "", tone: "neutral" });
  const [uploading, setUploading] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const dragCounter = useRef(0);

  // Library root (which folder auto-ingest scans) — set from Settings, but
  // also from the Shelves' "Set as library" shortcut.
  const [libraryRoot, setLibraryRoot] = useState(null); // null = not yet resolved
  const [hostPath, setHostPath] = useState("");
  const [settingRoot, setSettingRoot] = useState(false);
  const [rootNotice, setRootNotice] = useState("");
  const [rootError, setRootError] = useState("");

  // Cloud API keys — edited from Settings, consumed by Ask.
  const [apiKeys, setApiKeys] = useState(() => loadStored(KEYS_KEY, {}, true) || {});
  // Ollama models the reader has pulled on their own server but that this
  // app doesn't already know about — typed in from Settings, not fetched.
  const [ollamaModels, setOllamaModelsState] = useState(() => loadStored(OLLAMA_KEY, [], true) || []);
  const setOllamaModels = useCallback((models) => {
    setOllamaModelsState(models);
    saveStored(OLLAMA_KEY, models);
  }, []);

  // Conversation history — listed in the notebook; Ask renders whichever
  // conversation `activeChatId` points at.
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatIdState] = useState(() => loadStored(ACTIVE_CHAT_KEY, "") || null);

  // The research console.
  const [mode, setModeState] = useState(() => (loadStored(MODE_KEY, "search") === "ask" ? "ask" : "search"));
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState(null); // { documentId, documentName } or null for the whole library
  const [searchParams, setSearchParams] = useState(loadSearchParams);
  const [askParams, setAskParams] = useState(loadAskParams);
  const [searchRun, setSearchRun] = useState(null);
  const consoleInput = useRef(null);
  const stacksRef = useRef(null);
  const heroRef = useRef(null);

  const [recents, setRecents] = useState(loadRecents);
  const [inquiries, setInquiries] = useState(loadInquiries);
  const [source, setSource] = useState(null);

  const api = useCallback(async (path, options = {}) => {
    const response = await fetch(`${API_BASE}${path}`, options);
    return parseResponse(response);
  }, []);

  const say = useCallback((text, tone = "neutral") => setNotice({ text, tone }), []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    saveStored(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => saveStored(KEYS_KEY, apiKeys), [apiKeys]);
  useEffect(() => saveStored(SEARCH_PARAMS_KEY, searchParams), [searchParams]);
  useEffect(() => saveStored(ASK_PARAMS_KEY, askParams), [askParams]);

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

  const clearQuery = useCallback(() => setQuery(""), []);
  const ask = useAsk({
    apiBase: API_BASE,
    activeId: activeChatId,
    onActiveIdChange: setActiveChatId,
    onConversationsChanged: refreshChatList,
    apiKeys,
    ollamaModels,
    onSwitch: clearQuery,
  });

  const setMode = useCallback((next) => {
    setModeState(next);
    saveStored(MODE_KEY, next);
  }, []);

  const focusConsole = useCallback(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
    // After the scroll starts, so focusing doesn't fight it.
    window.setTimeout(() => consoleInput.current?.focus(), 50);
  }, []);

  // The browser's default reaction to a dropped file is to navigate to it —
  // block that everywhere so a stray drop doesn't blow away the app.
  useEffect(() => {
    const preventDefault = (event) => event.preventDefault();
    window.addEventListener("dragover", preventDefault);
    window.addEventListener("drop", preventDefault);
    return () => {
      window.removeEventListener("dragover", preventDefault);
      window.removeEventListener("drop", preventDefault);
    };
  }, []);

  // "/" jumps to the console from anywhere that isn't already a text field.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      event.preventDefault();
      focusConsole();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focusConsole]);

  const refreshDocuments = useCallback(async () => {
    try {
      const data = await api("/documents");
      setDocuments(data.documents || []);
    } catch (error) {
      say(error.message, "error");
    }
  }, [api, say]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/health/ready`)
      .then((response) => response.json())
      .then((data) => !cancelled && setHealth(data))
      .catch(() => !cancelled && setHealth({ status: "offline", qdrant: false }));
    api("/config")
      .then((config) => {
        if (cancelled) return;
        setAskEnabled(Boolean(config?.generation?.enabled));
        setFloors({
          search: typeof config.rerank_min_score === "number" ? config.rerank_min_score : DEFAULT_FLOORS.search,
          thorough: typeof config.ask_thorough_min_score === "number" ? config.ask_thorough_min_score : DEFAULT_FLOORS.thorough,
        });
      })
      .catch(() => {});
    fetch(`${API_BASE}/library/root`)
      .then((response) => response.json())
      .then((data) => {
        if (cancelled) return;
        setLibraryRoot(data.path || "");
        setHostPath(data.host_path || "");
      })
      .catch(() => !cancelled && setLibraryRoot(""));
    return () => {
      cancelled = true;
    };
  }, [api]);

  // The search floor starts at the server's own default until the reader
  // moves it.
  useEffect(() => {
    setSearchParams((params) => (params.minScore == null ? { ...params, minScore: floors.search } : params));
  }, [floors]);

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
          say(`Folder ingestion finished with status: ${next.state}.`, next.state === "done" ? "success" : "error");
          refreshDocuments();
        }
      } catch (error) {
        say(error.message, "error");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [api, job, refreshDocuments, say]);

  // ---- conversations -------------------------------------------------------

  // Picking the chat that's already open just shows it; anything else
  // switches (the Ask hook loads it and drops anything still in flight for
  // the old one).
  function selectChat(id) {
    if (id !== activeChatId) setActiveChatId(id);
    setMode("ask");
    setNotebookOpen(false);
    window.scrollTo({ top: 0 });
  }

  function newChat() {
    ask.newChat();
    setMode("ask");
    setNotebookOpen(false);
    focusConsole();
  }

  async function deleteChat(id) {
    if (!window.confirm("Delete this conversation?")) return;
    try {
      await api(`/conversations/${id}`, { method: "DELETE" });
      if (id === activeChatId) setActiveChatId(null);
      refreshChatList();
    } catch (error) {
      say(error.message, "error");
    }
  }

  // ---- the console ---------------------------------------------------------

  const runSearch = useCallback(
    async (text, params, runScope) => {
      const started = performance.now();
      setSearchRun({ status: "running", query: text, params, scope: runScope });
      try {
        const result = await api("/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: text,
            top_k: params.topK,
            rerank: true,
            rerank_k: 20,
            rerank_min_score: params.minScore,
            max_per_doc: params.perDoc,
            max_text_chars: 20000,
            ...(runScope ? { document_id: runScope.documentId } : {}),
          }),
        });
        setSearchRun({
          status: "done",
          query: text,
          params,
          scope: runScope,
          results: result.results || [],
          lowConfidence: Boolean(result.low_confidence),
          elapsed: performance.now() - started,
        });
      } catch (error) {
        setSearchRun({ status: "error", query: text, params, scope: runScope, error: error.message });
      }
    },
    [api],
  );

  const effectiveSearchParams = { ...searchParams, minScore: searchParams.minScore ?? floors.search };

  function submit() {
    const text = query.trim();
    if (!text) return;
    setInquiries((list) => recordInquiry(list, text, mode === "ask" && askEnabled ? "ask" : "search"));
    if (mode === "ask" && askEnabled) {
      setQuery("");
      ask.ask(text, {
        mode: askParams.mode,
        topK: askParams.topK,
        minScore: askParams.minScore,
        documentId: scope?.documentId,
      });
    } else {
      runSearch(text, effectiveSearchParams, scope);
    }
  }

  // A search from the notebook re-runs at once; a question is put back in
  // the box instead, since sending it spends a model call and would land in
  // whichever conversation happens to be open.
  function runInquiry(entry) {
    setNotebookOpen(false);
    if (entry.mode === "ask" && askEnabled) {
      setMode("ask");
      setQuery(entry.q);
      focusConsole();
      return;
    }
    setMode("search");
    setQuery(entry.q);
    setInquiries((list) => recordInquiry(list, entry.q, "search"));
    runSearch(entry.q, effectiveSearchParams, scope);
    window.scrollTo({ top: 0 });
  }

  function scopeTo(documentId, documentName) {
    setScope({ documentId, documentName });
    setSource(null);
    focusConsole();
  }

  const isAsk = mode === "ask" && askEnabled;
  // The conversation Ask is writing into: shown in the top bar so it's never
  // ambiguous which chat a question will land in.
  const activeChatTitle = activeChatId
    ? chats.find((chat) => chat.id === activeChatId)?.title || "Untitled"
    : "New chat";
  // A chat that's still loading keeps the workspace up, so switching chats
  // doesn't flash the home screen in between.
  const workspaceActive = isAsk ? ask.conversation.length > 0 || ask.loading : searchRun !== null;
  const busy = isAsk ? ask.busy : searchRun?.status === "running";

  const indexedCount = documents.filter((document) => document.indexing_status === "indexed").length;
  const passageCount = documents.reduce((sum, doc) => sum + (doc.indexing_status === "indexed" ? doc.chunks || 0 : 0), 0);
  const healthReady = health?.status === "ready";
  const blockedReason = !health
    ? ""
    : !healthReady
      ? "The search index is offline — check that Qdrant is running."
      : indexedCount === 0
        ? "Nothing is indexed yet — add PDFs below to start."
        : "";

  // The docked console's height, as --dock-h, so the sticky rail and the
  // scroll-to-question offset clear it whatever its controls wrap to. A
  // layout effect so it's set before the thread's own (passive) effect
  // scrolls the newest question into view.
  useLayoutEffect(() => {
    const root = document.documentElement;
    const el = heroRef.current;
    if (!workspaceActive || !el || typeof ResizeObserver === "undefined") {
      root.style.setProperty("--dock-h", "0px");
      return undefined;
    }
    const observer = new ResizeObserver(() => root.style.setProperty("--dock-h", `${el.offsetHeight}px`));
    observer.observe(el);
    return () => observer.disconnect();
  }, [workspaceActive]);

  // ---- documents -----------------------------------------------------------

  const openDocument = useCallback((doc, page) => {
    setSource({ documentId: doc.document_id, documentName: doc.filename, page: page || 1 });
  }, []);

  const notePage = useCallback((documentId, page) => {
    setRecents((list) => recordOpen(list, documentId, page));
  }, []);

  const shelf = useMemo(() => shelfItems(recents, documents, SHELF_SIZE), [recents, documents]);

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setUploading(true);
    let completed = 0;
    try {
      for (const file of files) {
        say(`Uploading ${file.name}… (${completed + 1}/${files.length})`);
        const form = new FormData();
        form.append("file", file, file.name);
        const result = await api("/documents", { method: "POST", body: form });
        completed += 1;
        say(result.deduplicated ? `${file.name} was already in the library.` : `${file.name} is queued for indexing.`);
      }
      await refreshDocuments();
    } catch (error) {
      say(error.message, "error");
    } finally {
      setUploading(false);
    }
  }

  // Counted rather than toggled on enter/leave: dragging over a child
  // element fires leave-then-enter on the parent, which would otherwise flip
  // drag-active off and back on and make the overlay flicker. Only file
  // drags count — dragging text or a cover around shouldn't raise it.
  function handleDragEnter(event) {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault();
    dragCounter.current += 1;
    setDragActive(true);
  }

  function handleDragLeave(event) {
    if (!dragActive) return;
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
    if (!dropped.length) return;
    const pdfs = dropped.filter((file) => file.type === "application/pdf" || /\.pdf$/i.test(file.name));
    if (pdfs.length < dropped.length) {
      say(
        dropped.length === 1 ? "Only PDF files can be added." : `Skipped ${dropped.length - pdfs.length} non-PDF file(s).`,
        pdfs.length === 0 ? "error" : "neutral",
      );
    }
    if (pdfs.length) {
      stacksRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      uploadFiles(pdfs);
    }
  }

  // A convenience only: auto-ingest already scans `libraryRoot` on its own
  // (default every 60s), and setting a new root already triggers an import.
  // This just forces that scan to happen right now instead of waiting.
  async function rescanLibraryFolder() {
    setAttaching(true);
    say("Rescanning the library folder…");
    try {
      const result = await api("/library/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: libraryRoot || "." }),
      });
      setJob(result);
      say(`Rescan job ${result.job_id} is queued.`);
    } catch (error) {
      say(error.message, "error");
    } finally {
      setAttaching(false);
    }
  }

  // Shared by Settings (typing a path) and the Shelves' "Set as library"
  // shortcut. Uses raw fetch (not the `api()` helper) so it can give a
  // specific, actionable message for the two failure modes that actually
  // happen here — outside the mount, or simply not found.
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

  // Handles the single-document "Retry" and the bulk "Reindex selected/all"
  // actions. `/retry` already rebuilds from the source PDF whenever a
  // document's pipeline_version is behind the server's current one — exactly
  // what picking up an extraction fix needs — so this just calls it for each
  // document without one failure stopping the rest of the batch.
  async function reindexDocuments(ids) {
    if (!ids.length || reindexing) return;
    setReindexing(true);
    let succeeded = 0;
    let failed = 0;
    for (const id of ids) {
      say(`Reindexing… (${succeeded + failed + 1}/${ids.length})`);
      try {
        await api(`/documents/${id}/retry`, { method: "POST" });
        succeeded += 1;
      } catch (_error) {
        failed += 1;
      }
    }
    say(
      failed
        ? `Reindexed ${succeeded} document${succeeded === 1 ? "" : "s"}; ${failed} failed.`
        : `Queued ${succeeded} document${succeeded === 1 ? "" : "s"} for reindexing.`,
      failed ? "error" : "success",
    );
    setReindexing(false);
    refreshDocuments();
  }

  // The reader's marks (read, owned, type). Applied locally at once so the
  // toggle feels instant, then confirmed — or rolled back — by the server.
  async function patchDocument(id, changes) {
    const before = documents.find((doc) => doc.document_id === id);
    if (!before) return;
    const optimistic = { ...before };
    if ("read" in changes) optimistic.read_at = changes.read ? new Date().toISOString() : null;
    if ("owned" in changes) optimistic.owned = changes.owned == null ? null : Number(changes.owned);
    if ("kind" in changes) optimistic.kind_override = changes.kind === "auto" ? null : changes.kind;
    const replace = (doc) => setDocuments((list) => list.map((d) => (d.document_id === id ? doc : d)));
    replace(optimistic);
    try {
      replace(
        await api(`/documents/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(changes),
        }),
      );
    } catch (error) {
      replace(before);
      say(error.message, "error");
    }
  }

  async function removeDocument(document) {
    if (!window.confirm(`Remove “${document.filename}” from the library?`)) return;
    try {
      await api(`/documents/${document.document_id}`, { method: "DELETE" });
      say(`${document.filename} was removed.`);
      if (scope?.documentId === document.document_id) setScope(null);
      refreshDocuments();
    } catch (error) {
      say(error.message, "error");
    }
  }

  // ---- render --------------------------------------------------------------

  return (
    <div className="app" onDragEnter={handleDragEnter} onDragLeave={handleDragLeave} onDrop={handleDrop}>
      <header className="masthead">
        <div className="masthead-left">
          <button type="button" className="brand" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">AI Librarian</span>
          </button>
          {askEnabled && (
            <button
              type="button"
              className={`chat-crumb${isAsk ? " current" : ""}`}
              onClick={() => setNotebookOpen(true)}
              title="Switch conversation"
            >
              <span className="chat-crumb-sep" aria-hidden="true">/</span>
              <span className="chat-crumb-label">Chat</span>
              <span className="chat-crumb-title">{activeChatTitle}</span>
              <span className="chat-crumb-caret" aria-hidden="true">▾</span>
            </button>
          )}
        </div>
        <nav className="masthead-nav">
          <button type="button" className="nav-button" onClick={() => setNotebookOpen(true)} aria-expanded={notebookOpen}>
            <NotebookIcon />
            <span className="nav-label">History</span>
          </button>
          <button type="button" className="nav-button" onClick={() => stacksRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}>
            <LibraryIcon />
            <span className="nav-label">Library</span>
          </button>
          <span className="health" title={`Index: ${health?.qdrant ? "ready" : "offline"}`}>
            <span className={`health-dot${healthReady ? " ready" : health ? " down" : ""}`} />
            <span className="nav-label">{healthReady ? "Ready" : health?.status || "Connecting"}</span>
          </span>
          <div className="settings-anchor">
            <button
              type="button"
              className="icon-button"
              onClick={() => setSettingsOpen((open) => !open)}
              aria-label="Settings"
              aria-expanded={settingsOpen}
              title="Settings"
            >
              <GearIcon />
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
        </nav>
      </header>

      <main>
        <section className={`hero${workspaceActive ? " docked" : ""}`} ref={heroRef}>
          <div className="hero-center">
            {!workspaceActive && (
              <div className="hero-intro">
                <h1>{isAsk ? "Ask the library" : "Search the library"}</h1>
                <dl className="readout">
                  <div>
                    <dt>Documents</dt>
                    <dd>{indexedCount.toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>Passages</dt>
                    <dd>{passageCount.toLocaleString()}</dd>
                  </div>
                </dl>
              </div>
            )}
            <Console
              inputRef={consoleInput}
              docked={workspaceActive}
              mode={mode}
              onModeChange={setMode}
              askEnabled={askEnabled}
              query={query}
              onQueryChange={setQuery}
              onSubmit={submit}
              busy={busy}
              blockedReason={blockedReason}
              scope={scope}
              onClearScope={() => setScope(null)}
              searchParams={effectiveSearchParams}
              onSearchParams={setSearchParams}
              askParams={askParams}
              onAskParams={setAskParams}
              ask={ask}
              defaults={floors}
              onReset={() => {
                if (isAsk) newChat();
                else setSearchRun(null);
              }}
            />
            {!workspaceActive && inquiries.length > 0 && (
              // Laid out as a grid of equal columns so the row spans exactly
              // the search bar's width, whatever the queries' lengths.
              <div
                className="recent-inquiries"
                style={{ gridTemplateColumns: `auto repeat(${Math.min(3, inquiries.length)}, minmax(0, 1fr))` }}
              >
                <span className="eyebrow">Recent</span>
                {inquiries.slice(0, 3).map((entry) => (
                  <button type="button" className="inquiry-chip" key={`${entry.mode}:${entry.q}`} onClick={() => runInquiry(entry)} title={entry.q}>
                    <span className="inquiry-chip-glyph" aria-hidden="true">{entry.mode === "ask" ? "¶" : "§"}</span>
                    <span className="inquiry-chip-text">{entry.q}</span>
                  </button>
                ))}
              </div>
            )}
            {!workspaceActive && (
              <BookRow apiBase={API_BASE} items={shelf} onOpen={(item) => openDocument(item.doc, item.page)} />
            )}
          </div>
          {!workspaceActive && (
            <button type="button" className="scroll-cue" onClick={() => stacksRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              Library <span aria-hidden="true">↓</span>
            </button>
          )}
        </section>

        {workspaceActive && (
          <section className="workspace">
            {isAsk ? (
              <div className="findings-layout">
                <div className="findings">
                  {ask.error && <div className="notice error">{ask.error}</div>}
                  <AskThread
                    conversation={ask.conversation}
                    onViewSource={setSource}
                    onToggleCitation={ask.toggleCitation}
                    modelLabel={ask.modelLabel}
                    noModels={ask.noModels}
                    loading={ask.loading}
                  />
                </div>
                <AskRail conversation={ask.conversation} onToggleCitation={ask.toggleCitation} />
              </div>
            ) : (
              <SearchResults
                apiBase={API_BASE}
                run={searchRun}
                onViewSource={setSource}
                onScope={(result) => scopeTo(result.document_id, result.document)}
              />
            )}
          </section>
        )}

        <Stacks
          sectionRef={stacksRef}
          apiBase={API_BASE}
          documents={documents}
          libraryRoot={libraryRoot}
          settingRoot={settingRoot}
          onSetLibraryFolder={setLibraryFolder}
          onImported={refreshDocuments}
          onJob={setJob}
          onOpenDocument={(doc) => openDocument(doc)}
          onScope={(doc) => scopeTo(doc.document_id, doc.filename)}
          onUpload={uploadFiles}
          uploading={uploading}
          onRescan={rescanLibraryFolder}
          attaching={attaching}
          reindexing={reindexing}
          onReindex={reindexDocuments}
          onRemove={removeDocument}
          onPatch={patchDocument}
          notice={notice}
          job={job}
        />
      </main>

      <Notebook
        open={notebookOpen}
        onClose={() => setNotebookOpen(false)}
        askEnabled={askEnabled}
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={selectChat}
        onNewChat={newChat}
        onDeleteChat={deleteChat}
        inquiries={inquiries}
        onRunInquiry={runInquiry}
        onClearInquiries={() => setInquiries(clearInquiries())}
      />

      {source && (
        <SourceViewer
          apiBase={API_BASE}
          source={source}
          doc={documents.find((doc) => doc.document_id === source.documentId)}
          onPatch={(changes) => patchDocument(source.documentId, changes)}
          onClose={() => setSource(null)}
          onPage={notePage}
          onScope={({ documentId, documentName }) => scopeTo(documentId, documentName)}
        />
      )}

      {dragActive && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-overlay-card">
            <span className="drop-overlay-glyph">⇪</span>
            <strong>Drop PDFs to add them to the library</strong>
            <span>They'll be indexed and searchable in a minute or two.</span>
          </div>
        </div>
      )}
    </div>
  );
}

