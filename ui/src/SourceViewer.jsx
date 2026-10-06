import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";

import { KIND_FOR_TOOL, COLORS, TOOLS } from "./annotations.js";
import { MarkControls } from "./DocMarks.jsx";
import MarksPanel from "./MarksPanel.jsx";
import PageAnnotator from "./PageAnnotator.jsx";
import { katexPlugin, prepareMath, remarkPlugins } from "./markdown.js";
import { displayTitle } from "./storage.js";

// A lightbox over a server-rendered PDF or EPUB page image, plus a link to
// the original file. PDF matches are highlighted on the rendered page. For a
// PDF the lightbox is also a reader and editor: highlight, underline, type
// text, draw, and comment on the page, with every mark saved to the book and
// indexed for search (see PageAnnotator and MarksPanel). `onPage`
// reports every page the reader lands on (so reopening the document from the
// shelf resumes there); `onScope` narrows the console to this document.
// `doc` is the document's current record (for the read/owned marks) and
// `onPatch(changes)` updates them.
export default function SourceViewer({ apiBase, source, doc, onPatch, onClose, onPage, onScope, onBook, onSavePassage }) {
  const { documentId, documentName, page: startPage, matched, snippet } = source;
  const isPaged = !doc || doc.file_type === "pdf" || doc.file_type === "epub";
  const [page, setPage] = useState(startPage || 1);
  const [pageCount, setPageCount] = useState(null);
  const [status, setStatus] = useState("loading");
  const [extracted, setExtracted] = useState({ text: "", format: "markdown" });
  const editable = doc?.file_type === "pdf" && doc.collection_id !== "notes";
  const [tool, setTool] = useState("select");
  const [colors, setColors] = useState(() => ({ highlight: COLORS.highlight[0], underline: COLORS.underline[0], text: COLORS.text[0], ink: COLORS.ink[0] }));
  const [thickness, setThickness] = useState(0.004);
  const [textSize, setTextSize] = useState(0.018);
  const [zoom, setZoom] = useState(1);
  const [marks, setMarks] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [focusComment, setFocusComment] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);
  const [savedWords, setSavedWords] = useState(() => new Set());
  const [notice, setNotice] = useState("");
  const made = useRef([]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/documents/${documentId}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((meta) => {
        if (!cancelled && meta && meta.pages) setPageCount(meta.pages);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apiBase, documentId]);

  const tell = useCallback((text) => {
    setNotice(text);
    window.setTimeout(() => setNotice((current) => (current === text ? "" : current)), 3200);
  }, []);

  const loadSavedWords = useCallback(() => {
    fetch(`${apiBase}/vocabulary?document_id=${encodeURIComponent(documentId)}`)
      .then((response) => (response.ok ? response.json() : { words: [] }))
      .then((data) => setSavedWords(new Set(data.words.map((entry) => entry.normalized_word))))
      .catch(() => {});
  }, [apiBase, documentId]);

  useEffect(() => {
    if (!editable) return undefined;
    let cancelled = false;
    fetch(`${apiBase}/documents/${documentId}/annotations`)
      .then((response) => (response.ok ? response.json() : { annotations: [] }))
      .then((data) => {
        if (cancelled) return;
        setMarks(data.annotations);
        if (data.pdf_sync && !data.pdf_sync.ok) tell(data.pdf_sync.error);
        if (data.annotations.length) setPanelOpen(true);
      })
      .catch(() => {});
    loadSavedWords();
    return () => { cancelled = true; };
  }, [apiBase, documentId, editable, loadSavedWords]);

  async function send(path, method, body) {
    const response = await fetch(`${apiBase}${path}`, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That did not save.");
    return data;
  }

  const createMark = useCallback(async (body) => {
    try {
      const created = await send(`/documents/${documentId}/annotations`, "POST", body);
      setMarks((current) => [...current, created]);
      made.current.push(created.annotation_id);
      return created;
    } catch (failure) { tell(failure.message); return null; }
  }, [apiBase, documentId, tell]);

  const updateMark = useCallback(async (id, changes) => {
    try {
      const updated = await send(`/annotations/${id}`, "PUT", changes);
      setMarks((current) => current.map((mark) => (mark.annotation_id === id ? updated : mark)));
    } catch (failure) { tell(failure.message); }
  }, [apiBase, tell]);

  const deleteMark = useCallback(async (mark) => {
    try {
      await send(`/annotations/${mark.annotation_id}`, "DELETE");
      setMarks((current) => current.filter((item) => item.annotation_id !== mark.annotation_id));
      made.current = made.current.filter((id) => id !== mark.annotation_id);
      setSelectedId((current) => (current === mark.annotation_id ? null : current));
    } catch (failure) { tell(failure.message); }
  }, [apiBase, tell]);

  const addWord = useCallback(async ({ word, page: wordPage, quote }) => {
    try {
      await send("/vocabulary/harvest", "POST", { document_id: documentId, words: [{ word, page: wordPage, quote }] });
      setSavedWords((current) => new Set(current).add(word.toLowerCase()));
      tell(`“${word}” added to your vocabulary.`);
    } catch (failure) { tell(failure.message); }
  }, [apiBase, documentId, tell]);

  const selectMark = useCallback((id, options) => {
    setSelectedId(id);
    if (id) setPanelOpen(true);
    if (options?.comment) setFocusComment((n) => n + 1);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => onPage?.(documentId, page), 650);
    return () => window.clearTimeout(timer);
  }, [documentId, page, onPage]);

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
      if (event.target.closest?.("textarea, input, select")) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z" && made.current.length) {
        event.preventDefault();
        const last = marks.find((mark) => mark.annotation_id === made.current[made.current.length - 1]);
        if (last) deleteMark(last);
      } else if (event.key === "Escape") { if (tool !== "select") setTool("select"); else if (selectedId) setSelectedId(null); else onClose(); }
      else if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, step, marks, deleteMark, tool, selectedId]);

  // Sent on every page, not just the first: a passage can run onto the next
  // one, and the server simply marks nothing where it doesn't match.
  const highlight = (matched || snippet || "").slice(0, 600);
  const imageSrc = `${apiBase}/documents/${documentId}/page/${page}${
    highlight ? `?highlight=${encodeURIComponent(highlight)}` : ""
  }`;

  useEffect(() => {
    if (isPaged) setStatus("loading");
  }, [imageSrc, isPaged]);

  useEffect(() => {
    if (isPaged) return undefined;
    let cancelled = false;
    setStatus("loading");
    fetch(`${apiBase}/documents/${documentId}/extracted-page/${page}`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load extracted text.");
        return response.json();
      })
      .then((data) => {
        if (!cancelled) {
          setExtracted({ text: data.text || "", format: data.format || "markdown" });
          setStatus("ready");
        }
      })
      .catch(() => !cancelled && setStatus("error"));
    return () => {
      cancelled = true;
    };
  }, [apiBase, documentId, isPaged, page]);

  // Rendered through a portal straight onto <body>: any ancestor with its own
  // filter/transform/backdrop-filter turns `position: fixed` into "fixed to
  // that ancestor" instead of the viewport, which would silently
  // shrink/misposition this overlay if it were ever nested inside such a
  // panel. A portal sidesteps that regardless of what the panels do.
  return createPortal(
    <div className="viewer-overlay" onClick={onClose}>
      <div className={`viewer${editable ? " viewer-editor" : ""}`} role="dialog" aria-label={documentName} onClick={(event) => event.stopPropagation()}>
        <div className="viewer-bar">
          <div className="viewer-title" title={documentName}>
            <span className="eyebrow">Document</span>
            <strong>{displayTitle(documentName)}</strong>
          </div>
          <div className="viewer-nav">
            <button type="button" className="icon-button" onClick={() => step(-1)} disabled={page <= 1} aria-label="Previous page">
              ‹
            </button>
            <span className="viewer-page">
              p. {page}
              {pageCount ? <span className="muted"> / {pageCount}</span> : ""}
            </span>
            <button
              type="button"
              className="icon-button"
              onClick={() => step(1)}
              disabled={pageCount ? page >= pageCount : false}
              aria-label="Next page"
            >
              ›
            </button>
          </div>
          {doc && <MarkControls doc={doc} compact onPatch={onPatch} />}
          <div className="viewer-actions">
            {onBook && <button type="button" className="text-button" onClick={onBook}>Book details & notes</button>}
            {onSavePassage && snippet && page === (startPage || 1) && <button type="button" className="text-button" onClick={() => onSavePassage({ ...source, page })}>Save passage</button>}
            {onScope && (
              <button type="button" className="text-button" onClick={() => onScope({ documentId, documentName })}>
                Search within
              </button>
            )}
            <a className="text-button" href={`${apiBase}/documents/${documentId}/file${doc?.file_type === "pdf" ? `#page=${page}` : ""}`} target="_blank" rel="noreferrer">
              Open original ↗
            </a>
            <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
              ×
            </button>
          </div>
        </div>
        {editable && (
          <div className="viewer-tools" role="toolbar" aria-label="Annotation tools">
            <div className="tool-group">
              {TOOLS.map((item) => (
                <button key={item.id} type="button" className={tool === item.id ? "on" : ""} title={item.hint} aria-pressed={tool === item.id} onClick={() => setTool(item.id)}>{item.label}</button>
              ))}
            </div>
            {KIND_FOR_TOOL[tool] && (
              <div className="tool-group tool-colors" aria-label="Colour">
                {COLORS[KIND_FOR_TOOL[tool]].map((color) => (
                  <button key={color} type="button" className={`swatch${colors[KIND_FOR_TOOL[tool]] === color ? " on" : ""}`} style={{ background: color }} aria-label={`Colour ${color}`} onClick={() => setColors({ ...colors, [KIND_FOR_TOOL[tool]]: color })} />
                ))}
              </div>
            )}
            {tool === "draw" && (
              <select value={thickness} onChange={(event) => setThickness(Number(event.target.value))} aria-label="Pen thickness">
                <option value={0.002}>Fine pen</option><option value={0.004}>Medium pen</option><option value={0.009}>Marker</option>
              </select>
            )}
            {tool === "text" && (
              <select value={textSize} onChange={(event) => setTextSize(Number(event.target.value))} aria-label="Text size">
                <option value={0.014}>Small text</option><option value={0.018}>Medium text</option><option value={0.026}>Large text</option>
              </select>
            )}
            <div className="tool-group tool-zoom">
              <button type="button" onClick={() => setZoom((z) => Math.max(0.5, Math.round((z - 0.25) * 100) / 100))} aria-label="Zoom out">−</button>
              <button type="button" onClick={() => setZoom(1)} title="Fit to width">{Math.round(zoom * 100)}%</button>
              <button type="button" onClick={() => setZoom((z) => Math.min(3, Math.round((z + 0.25) * 100) / 100))} aria-label="Zoom in">+</button>
            </div>
            <div className="tool-spacer" />
            {notice && <span className="tool-notice" role="status">{notice}</span>}
            <a className="text-button" href={`${apiBase}/documents/${documentId}/file`} download title="Your marks are written into the PDF itself, so this file already contains them">Download PDF</a>
            <button type="button" className={`text-button${panelOpen ? " on" : ""}`} aria-pressed={panelOpen} onClick={() => setPanelOpen((open) => !open)}>Marks &amp; words ({marks.length})</button>
          </div>
        )}
        <div className="viewer-body">
        <div className="viewer-page-area">
          {status === "loading" && <div className="viewer-status">{isPaged ? "Rendering page…" : "Loading extracted text…"}</div>}
          {status === "error" && <div className="viewer-status error">Could not load this page.</div>}
          {isPaged && editable ? (
            <PageAnnotator
              apiBase={apiBase} documentId={documentId} page={page} imageSrc={imageSrc}
              marks={marks.filter((mark) => mark.page === page)} tool={tool} colors={colors} thickness={thickness} textSize={textSize} zoom={zoom}
              selectedId={selectedId} savedWords={savedWords}
              onLoad={() => setStatus("ready")} onError={() => setStatus("error")}
              onCreate={createMark} onUpdate={updateMark} onDelete={deleteMark} onSelect={selectMark} onAddWord={addWord}
            />
          ) : isPaged ? (
            <img
              key={imageSrc}
              src={imageSrc}
              alt={`${documentName}, page ${page}`}
              onLoad={() => setStatus("ready")}
              onError={() => setStatus("error")}
              style={status === "error" ? { display: "none" } : undefined}
            />
          ) : status === "ready" ? (
            // Office sheets/slides and Markdown are extracted as Markdown
            // (tables included); OCR and plain text stay verbatim.
            <div className={`viewer-text${extracted.format === "markdown" ? "" : " viewer-text-plain"}`}>
              {extracted.format === "markdown" ? (
                <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={[katexPlugin]} skipHtml>
                  {prepareMath(extracted.text)}
                </ReactMarkdown>
              ) : (
                extracted.text
              )}
            </div>
          ) : null}
        </div>
        {editable && panelOpen && (
          <MarksPanel
            apiBase={apiBase} documentId={documentId} marks={marks} selectedId={selectedId} focusComment={focusComment} page={page}
            onJump={(target, id) => { setPage(target); setSelectedId(id); }} onUpdate={updateMark} onDelete={deleteMark} onWordsAdded={loadSavedWords}
          />
        )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
