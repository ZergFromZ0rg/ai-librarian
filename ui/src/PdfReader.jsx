import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import pdfjsLib from "./pdfjsGlobal.js"; // first: the viewer below reads PDF.js from a global
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import * as viewerLib from "pdfjs-dist/web/pdf_viewer.mjs";
import "pdfjs-dist/web/pdf_viewer.css";
import { createAutosaver } from "./autosave.js";
import PdfFindBar from "./PdfFindBar.jsx";
import PdfSidebar from "./PdfSidebar.jsx";
import { findSummary, shortcutFor } from "./pdfReaderKeys.js";
import { displayTitle } from "./storage.js";

const COLORS = [["Yellow", "#FFFF98"], ["Green", "#53FFBC"], ["Blue", "#80EBFF"], ["Pink", "#FFCBE6"], ["Red", "#FF4F5F"]];
const TOOLS = [["none", "Select"], ["highlight", "Highlight"], ["text", "Text"], ["draw", "Draw"]];
const SAVE_LABEL = { saved: "All changes saved", unsaved: "Unsaved changes", saving: "Saving…", retrying: "Could not save, retrying…", error: "Could not save" };
const RETRY_DELAYS = [5000, 15000, 45000]; // then every 45 s, until it works or the reader is closed
const CONFLICT_MESSAGE = "the marks in this book were changed somewhere else (another tab or device) since you opened it";
const asset = (path) => `${import.meta.env.BASE_URL}pdfjs/${path}`;
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

// Called when a book page opens, so the (large) PDF.js worker is already in the
// cache by the time "Read PDF" is clicked.
export function warmPdfReader() {
  if (document.querySelector("link[data-pdf-worker]")) return;
  const link = document.createElement("link");
  link.rel = "prefetch";
  link.href = workerUrl;
  link.dataset.pdfWorker = "1";
  document.head.appendChild(link);
}

// A failed save, described for the autosaver: whether waiting will help (the
// server being busy or down) or not (the file was refused, or the marks were
// changed elsewhere), and whether it was the latter.
async function saveFailure(response) {
  const body = await response.json().catch(() => ({}));
  const message = typeof body.detail === "string" ? body.detail : `the server answered ${response.status}`;
  const { status } = response;
  return Object.assign(new Error(message), { status, conflict: status === 409, retry: status >= 500 || status === 408 || status === 429 });
}

// The in-app PDF reader. Highlights, typed text and pen strokes are made with
// PDF.js's own editor and saved to the library automatically: a moment after
// each change (and again, by itself, if the server could not be reached), and for
// certain when the reader is closed (closing waits for the save). The marked-up
// copy lives beside the original, which is never altered, and the server reads its
// marks back out for search and the notes digest.
//
// The outer component only exists so that "Load the other version" can start the
// reader over: a new session opens the file afresh, on the same page.
export default function PdfReader(props) {
  const [session, setSession] = useState({ n: 0, page: props.page });
  return <PdfReaderSession key={session.n} {...props} page={session.page} onReload={(page) => setSession((previous) => ({ n: previous.n + 1, page }))} />;
}

function PdfReaderSession({ apiBase, doc, page, onClose, onReload }) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const findInput = useRef(null);
  const lastSearched = useRef(""); // the query PDF.js is already showing matches for
  const lib = useRef({});
  const pageRef = useRef(page || 1);
  const [phase, setPhase] = useState("loading");
  const [error, setError] = useState("");
  const [pdfDoc, setPdfDoc] = useState(null);
  const [outline, setOutline] = useState(null); // null while it is being read
  const [pageNumber, setPageNumber] = useState(page || 1);
  const [pageCount, setPageCount] = useState(0);
  const [tool, setTool] = useState("none");
  const [color, setColor] = useState(COLORS[0][1]);
  const [saveState, setSaveState] = useState("saved");
  const [conflict, setConflict] = useState(false);
  const [conflictNote, setConflictNote] = useState("");
  const [closing, setClosing] = useState(false);
  const [closeError, setCloseError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarTab, setSidebarTab] = useState("contents");
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [findResult, setFindResult] = useState({});
  const id = doc.document_id;

  useEffect(() => {
    let cancelled = false;
    let loadingTask = null;
    let pdf = null;
    let saver = null;
    let resizeObserver = null;
    let pollTimer = null;
    let checkMarks = () => {};
    const onHidden = () => { if (document.visibilityState === "hidden") saver?.flush().catch(() => {}); };
    const onOnline = () => saver?.retryNow();
    const onBeforeUnload = (event) => {
      if (saver?.hasUnsaved()) { event.preventDefault(); event.returnValue = ""; }
    };

    (async () => {
      try {
        // Which saved copy of the marks this reader starts from, asked before the
        // file is fetched: if the copy changes in between, the save is refused as
        // a (harmless) false alarm, never accepted over marks nobody has seen.
        let baseVersion = null;
        try {
          const response = await fetch(`${apiBase}/documents/${id}/annotated-copy`);
          if (response.ok) baseVersion = (await response.json()).version ?? null;
        } catch { /* unchecked: a save then replaces whatever is there */ }
        if (cancelled) return;

        const eventBus = new viewerLib.EventBus();
        const linkService = new viewerLib.PDFLinkService({ eventBus });
        const findController = new viewerLib.PDFFindController({ linkService, eventBus });
        const viewer = new viewerLib.PDFViewer({
          container: containerRef.current,
          viewer: viewerRef.current,
          eventBus,
          linkService,
          findController,
          annotationEditorMode: pdfjsLib.AnnotationEditorType.NONE,
          annotationEditorHighlightColors: COLORS.map(([name, value]) => `${name.toLowerCase()}=${value}`).join(","),
          enableHighlightFloatingButton: true,
        });
        linkService.setViewer(viewer);
        // Re-fit whenever the reader's size changes (a window resize, the side
        // panel opening, or the page being laid out after it was first measured):
        // "fit width" computed against a zero-width container is a negative scale
        // and a blank page.
        const scroller = containerRef.current;
        resizeObserver = new ResizeObserver(() => {
          if (cancelled || !scroller.isConnected || !viewer.pdfDocument || scroller.clientWidth < 80) return;
          const value = viewer.currentScaleValue;
          if (["page-width", "page-fit", "page-actual", "auto"].includes(value)) viewer.currentScaleValue = value;
          viewer.update();
        });
        resizeObserver.observe(scroller);
        eventBus.on("pagesinit", () => {
          viewer.currentScaleValue = "page-width";
          viewer.currentPageNumber = Math.min(Math.max(1, page || 1), viewer.pagesCount);
          setPageCount(viewer.pagesCount);
        });
        eventBus.on("pagechanging", (event) => { pageRef.current = event.pageNumber; setPageNumber(event.pageNumber); });
        eventBus.on("updatefindcontrolstate", (event) => setFindResult({ state: event.state, current: event.matchesCount?.current || 0, total: event.matchesCount?.total || 0 }));
        eventBus.on("updatefindmatchescount", (event) => setFindResult((previous) => ({ ...previous, current: event.matchesCount.current, total: event.matchesCount.total })));

        loadingTask = pdfjsLib.getDocument({
          url: `${apiBase}/documents/${id}/file?t=${Date.now()}`,
          standardFontDataUrl: asset("standard_fonts/"),
          cMapUrl: asset("cmaps/"),
          cMapPacked: true,
          wasmUrl: asset("wasm/"),
        });
        pdf = await loadingTask.promise;
        if (cancelled) { try { loadingTask.destroy(); } catch { /* already gone */ } return; }

        // PDF.js only announces the first change after a save (and not a later edit
        // to the same mark, such as typing into a text box), so changes are found by
        // comparing the content hash of the marks with the hash last saved.
        const storage = pdf.annotationStorage;
        const marksHash = () => storage.serializable.hash;
        let savedHash = marksHash();
        let conflicted = false; // another tab or device saved first; nothing more is sent until the reader chooses
        let replacing = false; // the reader chose to replace those marks, so the server keeps them as the earlier version
        saver = createAutosaver({
          onState: setSaveState,
          retryDelays: RETRY_DELAYS,
          canRetry: (failure) => failure?.retry !== false,
          save: async () => {
            if (conflicted) throw Object.assign(new Error(CONFLICT_MESSAGE), { conflict: true, retry: false });
            const previous = savedHash;
            savedHash = marksHash(); // what this save will contain
            try {
              const bytes = await pdf.saveDocument();
              const form = new FormData();
              form.append("file", new Blob([bytes], { type: "application/pdf" }), "annotated.pdf");
              if (baseVersion !== null) form.append("base_version", baseVersion);
              if (replacing) form.append("replacing_other", "true");
              const response = await fetch(`${apiBase}/documents/${id}/annotated-copy`, { method: "POST", body: form, signal: AbortSignal.timeout?.(90000) });
              if (!response.ok) throw await saveFailure(response);
              const body = await response.json().catch(() => ({}));
              if (typeof body.version === "string") baseVersion = body.version; // the next save builds on this one
              replacing = false;
            } catch (failure) {
              savedHash = previous; // still unsaved: a retry must send it again
              if (failure?.conflict && !conflicted) { conflicted = true; setConflict(true); }
              throw failure;
            }
          },
        });
        // Only the first change starts the wait: re-arming it on every poll would
        // push the save back for as long as the reader keeps drawing.
        checkMarks = () => { if (!saver.hasUnsaved() && marksHash() !== savedHash) saver.markDirty(); };
        storage.onSetModified = checkMarks;
        storage.onResetModified = checkMarks; // an undo is a change too
        pollTimer = setInterval(checkMarks, 700);

        // "Keep my marks" after a conflict: find out which copy is there now, build on
        // it, and save over it.
        const keepMine = async () => {
          setConflictNote("");
          try {
            const response = await fetch(`${apiBase}/documents/${id}/annotated-copy`);
            if (!response.ok) throw new Error(`the server answered ${response.status}`);
            baseVersion = (await response.json()).version ?? null;
          } catch (failure) {
            setConflictNote(`Could not check the library (${failure?.message || "unknown error"}). Try again.`);
            return;
          }
          replacing = true;
          conflicted = false;
          setConflict(false);
          saver.markDirty();
        };

        lib.current = { pdfjsLib, eventBus, linkService, viewer, saver, checkMarks, keepMine };
        viewer.setDocument(pdf);
        linkService.setDocument(pdf);
        setPdfDoc(pdf);
        pdf.getOutline().then((items) => { if (!cancelled) setOutline(items || []); }).catch(() => { if (!cancelled) setOutline([]); });
        window.addEventListener("beforeunload", onBeforeUnload);
        window.addEventListener("online", onOnline);
        document.addEventListener("visibilitychange", onHidden);
        setPhase("ready");
      } catch (failure) {
        if (!cancelled) { setError(failure?.message || "This PDF could not be opened."); setPhase("error"); }
      }
    })();

    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      clearInterval(pollTimer);
      window.removeEventListener("beforeunload", onBeforeUnload);
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onHidden);
      // Leaving any other way than the Close button still saves before the file is
      // let go. Whatever happens, no retry may outlive the file it would save.
      const finish = () => { saver?.cancel(); try { loadingTask?.destroy(); } catch { /* nothing left to release */ } };
      if (saver?.hasUnsaved()) saver.flush().catch(() => {}).finally(finish);
      else finish();
      lib.current = {};
    };
  }, [apiBase, id]); // eslint-disable-line react-hooks/exhaustive-deps

  // The editing tool and the highlight colour follow the toolbar.
  useEffect(() => {
    const { pdfjsLib, viewer, eventBus } = lib.current;
    if (phase !== "ready" || !viewer) return;
    const types = pdfjsLib.AnnotationEditorType;
    const mode = { none: types.NONE, highlight: types.HIGHLIGHT, text: types.FREETEXT, draw: types.INK }[tool];
    viewer.annotationEditorMode = { mode };
    if (tool === "highlight") {
      eventBus.dispatch("switchannotationeditorparams", { source: null, type: pdfjsLib.AnnotationEditorParamsType.HIGHLIGHT_COLOR, value: color });
    }
  }, [tool, color, phase]);

  // Searching the whole PDF: PDF.js's find controller reads every page's text, not
  // just the few that are drawn, which is why this replaces the browser's Ctrl+F.
  const runFind = useCallback((query, again = false, previous = false) => {
    lastSearched.current = query;
    lib.current.eventBus?.dispatch("find", {
      source: window, type: again ? "again" : "", query, caseSensitive: false, entireWord: false, highlightAll: true, findPrevious: previous, matchDiacritics: true,
    });
  }, []);
  const stopFind = useCallback(() => { lastSearched.current = ""; lib.current.eventBus?.dispatch("findbarclose", { source: window }); }, []);
  const closeFind = useCallback(() => { setFindOpen(false); stopFind(); }, [stopFind]);
  const openFind = useCallback(() => {
    if (findInput.current) { findInput.current.focus(); findInput.current.select(); } else setFindOpen(true);
  }, []);
  useEffect(() => {
    if (findOpen) { findInput.current?.focus(); findInput.current?.select(); }
  }, [findOpen]);
  useEffect(() => {
    if (!findOpen || phase !== "ready") return undefined;
    const timer = setTimeout(() => {
      // Not again if Enter has already searched for this (that would jump back to the first match).
      if (findQuery.trim()) { if (findQuery !== lastSearched.current) runFind(findQuery); }
      else { setFindResult({}); stopFind(); }
    }, 250);
    return () => clearTimeout(timer);
  }, [findQuery, findOpen, phase, runFind, stopFind]);

  // Keys: Ctrl/Cmd+F or / to search, j and k for the next and previous page, Esc to
  // dismiss the search. They are stopped here so nothing behind the reader (the
  // app's own "/" to reach the console) also reacts.
  useEffect(() => {
    if (phase !== "ready") return undefined;
    const onKey = (event) => {
      const action = shortcutFor(event);
      if (!action) return;
      const { viewer } = lib.current;
      if (action === "find") { event.preventDefault(); event.stopPropagation(); openFind(); }
      else if (action === "escape") { if (findOpen) { event.preventDefault(); event.stopPropagation(); closeFind(); } }
      else if (viewer) { event.preventDefault(); event.stopPropagation(); if (action === "next-page") viewer.nextPage(); else viewer.previousPage(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [phase, findOpen, openFind, closeFind]);

  const requestClose = useCallback(async () => {
    if (closing) return;
    setClosing(true);
    setCloseError("");
    try {
      // A text box still being typed in only counts once it is committed: commit it,
      // give PDF.js a moment to apply that, then look for the change and save.
      try { lib.current.viewer?.annotationEditorUIManager?.commitOrRemove?.(); } catch { /* nothing active */ }
      document.activeElement?.blur?.();
      await new Promise((resolve) => setTimeout(resolve, 60));
      lib.current.checkMarks?.();
      await lib.current.saver?.flush();
    } catch (failure) {
      setClosing(false);
      if (!failure?.conflict) setCloseError(failure?.message || "unknown error"); // a conflict has its own notice, with its own choices
      return;
    }
    onClose(pageRef.current);
  }, [closing, onClose]);

  const closeWithoutSaving = () => { lib.current.saver?.discard(); onClose(pageRef.current); };
  const loadTheirs = () => { lib.current.saver?.discard(); onReload(pageRef.current); };

  const zoom = (factor) => { const { viewer } = lib.current; if (viewer) viewer.currentScale = Math.min(5, Math.max(0.3, viewer.currentScale * factor)); };
  const goTo = (value) => { const { viewer } = lib.current; const n = Math.round(Number(value)); if (viewer && n >= 1 && n <= viewer.pagesCount) viewer.currentPageNumber = n; };
  const undo = () => lib.current.viewer?.annotationEditorUIManager?.undo?.();
  // On a phone the panel covers the page, so it steps aside once a place has been picked.
  const stepAside = () => { if (window.matchMedia?.("(max-width: 700px)").matches) setSidebarOpen(false); };
  const goToItem = (item) => {
    if (item.dest) lib.current.linkService?.goToDestination(item.dest)?.catch?.(() => {});
    else if (/^https?:/i.test(item.url || "")) window.open(item.url, "_blank", "noopener,noreferrer");
    stepAside();
  };
  const ready = phase === "ready";

  // On document.body, so no parent's stacking context can put the reader under the book page.
  return createPortal(
    <div className="pdf-reader" role="dialog" aria-modal="true" aria-label={`Reading ${doc.title || doc.filename}`}>
      <header className="pdf-reader-bar">
        <button type="button" className="button" onClick={requestClose} disabled={closing}>{closing ? "Saving…" : "Close"}</button>
        <div className="pdf-reader-panels" role="group" aria-label="Contents and search">
          <button type="button" className={`pdf-tool${sidebarOpen ? " active" : ""}`} aria-pressed={sidebarOpen} disabled={!ready} title="Contents and pages" onClick={() => setSidebarOpen(!sidebarOpen)}>Contents</button>
          <button type="button" className={`pdf-tool${findOpen ? " active" : ""}`} aria-pressed={findOpen} disabled={!ready} title="Search this PDF (Ctrl/Cmd+F or /)" onClick={() => (findOpen ? closeFind() : openFind())}>Search</button>
        </div>
        <strong className="pdf-reader-title">{doc.title || displayTitle(doc.filename)}</strong>
        <div className="pdf-reader-tools" role="group" aria-label="Marking tools">
          {TOOLS.map(([value, label]) => (
            <button type="button" key={value} className={`pdf-tool${tool === value ? " active" : ""}`} aria-pressed={tool === value} disabled={!ready} onClick={() => setTool(value)}>{label}</button>
          ))}
          <span className="pdf-swatches" role="group" aria-label="Highlight colour">
            {COLORS.map(([name, value]) => (
              <button type="button" key={value} className={`pdf-swatch${color === value ? " active" : ""}`} style={{ background: value }} aria-label={`${name} highlight`} aria-pressed={color === value} onClick={() => { setColor(value); setTool("highlight"); }} />
            ))}
          </span>
          <button type="button" className="text-button" disabled={!ready} onClick={undo}>Undo</button>
        </div>
        <div className="pdf-reader-nav" role="group" aria-label="Page and zoom">
          <button type="button" className="text-button" aria-label="Zoom out" onClick={() => zoom(1 / 1.2)}>−</button>
          <button type="button" className="text-button" aria-label="Zoom in" onClick={() => zoom(1.2)}>+</button>
          <button type="button" className="text-button" onClick={() => { if (lib.current.viewer) lib.current.viewer.currentScaleValue = "page-width"; }}>Fit</button>
          <label className="pdf-page-field" title="Next page: J · Previous page: K">Page <input type="number" min="1" max={pageCount || undefined} value={pageNumber} onChange={(event) => { setPageNumber(event.target.value); goTo(event.target.value); }} /> of {pageCount || "…"}</label>
        </div>
        <span className={`pdf-save-state is-${saveState}`} role="status">{SAVE_LABEL[saveState]}</span>
        <a className="text-button pdf-plain-view" href={`${apiBase}/documents/${id}/file#page=${pageNumber}`} target="_blank" rel="noopener noreferrer" title="Open the file in the browser's own viewer. Marks made there are NOT saved to your library.">Browser view (won't save) ↗</a>
      </header>
      {findOpen && (
        <PdfFindBar
          inputRef={findInput}
          query={findQuery}
          onQuery={setFindQuery}
          summary={findSummary(findQuery, findResult)}
          onNext={() => runFind(findQuery, true, false)}
          onPrevious={() => runFind(findQuery, true, true)}
          onClose={closeFind}
        />
      )}
      {conflict && (
        <div className="pdf-reader-alert" role="alert">
          <span>The marks in this book were changed somewhere else (another tab or device) since you opened it. What you have marked since is not saved yet.</span>
          <button type="button" className="button" title="Save your marks over the other version. The other version is kept as an earlier version you can restore from the book page." onClick={() => lib.current.keepMine?.()}>Keep my marks</button>
          <button type="button" className="button" title="Drop the marks made here since you opened it and open the book with the other version's marks." onClick={loadTheirs}>Load the other version</button>
          {conflictNote && <span>{conflictNote}</span>}
        </div>
      )}
      {closeError && (
        <div className="pdf-reader-alert" role="alert">
          Your marks could not be saved ({closeError}).
          <button type="button" className="button" onClick={requestClose}>Try again</button>
          <button type="button" className="text-button danger" onClick={closeWithoutSaving}>Close without saving</button>
        </div>
      )}
      {phase === "loading" && <p className="pdf-reader-note muted">Opening the PDF…</p>}
      {phase === "error" && <p className="pdf-reader-note danger-text" role="alert">{error}</p>}
      <div className={`pdf-reader-body${sidebarOpen && ready ? " has-sidebar" : ""}`}>
        {sidebarOpen && ready && pdfDoc && (
          <PdfSidebar
            pdf={pdfDoc}
            outline={outline}
            pageCount={pageCount}
            currentPage={Number(pageNumber) || 1}
            tab={sidebarTab}
            onTab={setSidebarTab}
            onGoToPage={(n) => { const { viewer } = lib.current; if (viewer) viewer.currentPageNumber = n; stepAside(); }}
            onGoToItem={goToItem}
          />
        )}
        <div className="pdf-reader-main">
          <div ref={containerRef} className="pdf-reader-scroll">
            <div ref={viewerRef} className="pdfViewer" />
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
