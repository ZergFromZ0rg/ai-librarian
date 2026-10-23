import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import pdfjsLib from "./pdfjsGlobal.js"; // first: the viewer below reads PDF.js from a global
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import * as viewerLib from "pdfjs-dist/web/pdf_viewer.mjs";
import "pdfjs-dist/web/pdf_viewer.css";
import { createAutosaver } from "./autosave.js";
import { displayTitle } from "./storage.js";

const COLORS = [["Yellow", "#FFFF98"], ["Green", "#53FFBC"], ["Blue", "#80EBFF"], ["Pink", "#FFCBE6"], ["Red", "#FF4F5F"]];
const TOOLS = [["none", "Select"], ["highlight", "Highlight"], ["text", "Text"], ["draw", "Draw"]];
const SAVE_LABEL = { saved: "All changes saved", unsaved: "Unsaved changes", saving: "Saving…", error: "Could not save" };
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

// The in-app PDF reader. Highlights, typed text and pen strokes are made with
// PDF.js's own editor and saved to the library automatically: a moment after
// each change, and for certain when the reader is closed (closing waits for the
// save). The marked-up copy lives beside the original, which is never altered,
// and the server reads its marks back out for search and the notes digest.
export default function PdfReader({ apiBase, doc, page, onClose }) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const lib = useRef({});
  const pageRef = useRef(page || 1);
  const [phase, setPhase] = useState("loading");
  const [error, setError] = useState("");
  const [pageNumber, setPageNumber] = useState(page || 1);
  const [pageCount, setPageCount] = useState(0);
  const [tool, setTool] = useState("none");
  const [color, setColor] = useState(COLORS[0][1]);
  const [saveState, setSaveState] = useState("saved");
  const [closing, setClosing] = useState(false);
  const [closeError, setCloseError] = useState("");
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
    const onBeforeUnload = (event) => {
      if (saver?.hasUnsaved()) { event.preventDefault(); event.returnValue = ""; }
    };

    (async () => {
      try {
        const eventBus = new viewerLib.EventBus();
        const linkService = new viewerLib.PDFLinkService({ eventBus });
        const viewer = new viewerLib.PDFViewer({
          container: containerRef.current,
          viewer: viewerRef.current,
          eventBus,
          linkService,
          annotationEditorMode: pdfjsLib.AnnotationEditorType.NONE,
          annotationEditorHighlightColors: COLORS.map(([name, value]) => `${name.toLowerCase()}=${value}`).join(","),
          enableHighlightFloatingButton: true,
        });
        linkService.setViewer(viewer);
        // Re-fit whenever the reader's size changes (a window resize, or the page
        // being laid out after it was first measured): "fit width" computed against
        // a zero-width container is a negative scale and a blank page.
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
        saver = createAutosaver({
          onState: setSaveState,
          save: async () => {
            const previous = savedHash;
            savedHash = marksHash(); // what this save will contain
            try {
              const bytes = await pdf.saveDocument();
              const form = new FormData();
              form.append("file", new Blob([bytes], { type: "application/pdf" }), "annotated.pdf");
              const response = await fetch(`${apiBase}/documents/${id}/annotated-copy`, { method: "POST", body: form, signal: AbortSignal.timeout?.(90000) });
              if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(typeof body.detail === "string" ? body.detail : `the server answered ${response.status}`);
              }
            } catch (failure) {
              savedHash = previous; // still unsaved: a retry must send it again
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

        lib.current = { pdfjsLib, eventBus, viewer, saver, checkMarks };
        viewer.setDocument(pdf);
        linkService.setDocument(pdf);
        window.addEventListener("beforeunload", onBeforeUnload);
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
      document.removeEventListener("visibilitychange", onHidden);
      // Leaving any other way than the Close button still saves before the file is let go.
      const finish = () => { try { loadingTask?.destroy(); } catch { /* nothing left to release */ } };
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
      setCloseError(failure?.message || "unknown error");
      return;
    }
    onClose(pageRef.current);
  }, [closing, onClose]);

  const zoom = (factor) => { const { viewer } = lib.current; if (viewer) viewer.currentScale = Math.min(5, Math.max(0.3, viewer.currentScale * factor)); };
  const goTo = (value) => { const { viewer } = lib.current; const n = Math.round(Number(value)); if (viewer && n >= 1 && n <= viewer.pagesCount) viewer.currentPageNumber = n; };
  const undo = () => lib.current.viewer?.annotationEditorUIManager?.undo?.();

  // On document.body, so no parent's stacking context can put the reader under the book page.
  return createPortal(
    <div className="pdf-reader" role="dialog" aria-modal="true" aria-label={`Reading ${doc.title || doc.filename}`}>
      <header className="pdf-reader-bar">
        <button type="button" className="button" onClick={requestClose} disabled={closing}>{closing ? "Saving…" : "Close"}</button>
        <strong className="pdf-reader-title">{doc.title || displayTitle(doc.filename)}</strong>
        <div className="pdf-reader-tools" role="group" aria-label="Marking tools">
          {TOOLS.map(([value, label]) => (
            <button type="button" key={value} className={`pdf-tool${tool === value ? " active" : ""}`} aria-pressed={tool === value} disabled={phase !== "ready"} onClick={() => setTool(value)}>{label}</button>
          ))}
          <span className="pdf-swatches" role="group" aria-label="Highlight colour">
            {COLORS.map(([name, value]) => (
              <button type="button" key={value} className={`pdf-swatch${color === value ? " active" : ""}`} style={{ background: value }} aria-label={`${name} highlight`} aria-pressed={color === value} onClick={() => { setColor(value); setTool("highlight"); }} />
            ))}
          </span>
          <button type="button" className="text-button" disabled={phase !== "ready"} onClick={undo}>Undo</button>
        </div>
        <div className="pdf-reader-nav" role="group" aria-label="Page and zoom">
          <button type="button" className="text-button" aria-label="Zoom out" onClick={() => zoom(1 / 1.2)}>−</button>
          <button type="button" className="text-button" aria-label="Zoom in" onClick={() => zoom(1.2)}>+</button>
          <button type="button" className="text-button" onClick={() => { if (lib.current.viewer) lib.current.viewer.currentScaleValue = "page-width"; }}>Fit</button>
          <label className="pdf-page-field">Page <input type="number" min="1" max={pageCount || undefined} value={pageNumber} onChange={(event) => { setPageNumber(event.target.value); goTo(event.target.value); }} /> of {pageCount || "…"}</label>
        </div>
        <span className={`pdf-save-state is-${saveState}`} role="status">{SAVE_LABEL[saveState]}</span>
        <a className="text-button" href={`${apiBase}/documents/${id}/file#page=${pageNumber}`} target="_blank" rel="noopener noreferrer" title="Open the file in the browser's own viewer. Marks made there are not saved here.">Browser viewer ↗</a>
      </header>
      {closeError && (
        <div className="pdf-reader-alert" role="alert">
          Your marks could not be saved ({closeError}).
          <button type="button" className="button" onClick={requestClose}>Try again</button>
          <button type="button" className="text-button danger" onClick={() => onClose(pageRef.current)}>Close without saving</button>
        </div>
      )}
      {phase === "loading" && <p className="pdf-reader-note muted">Opening the PDF…</p>}
      {phase === "error" && <p className="pdf-reader-note danger-text" role="alert">{error}</p>}
      <div className="pdf-reader-body">
        <div ref={containerRef} className="pdf-reader-scroll">
          <div ref={viewerRef} className="pdfViewer" />
        </div>
      </div>
    </div>,
    document.body,
  );
}
