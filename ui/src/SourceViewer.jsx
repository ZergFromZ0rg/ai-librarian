import React, { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";

import { MarkControls } from "./DocMarks.jsx";
import { katexPlugin, prepareMath, remarkPlugins } from "./markdown.js";
import { displayTitle } from "./storage.js";

// A lightbox over a server-rendered PDF or EPUB page image, plus a link to
// the original file. PDF matches are highlighted on the rendered page. `onPage`
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

  useEffect(() => {
    onPage?.(documentId, page);
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
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, step]);

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
      <div className="viewer" role="dialog" aria-label={documentName} onClick={(event) => event.stopPropagation()}>
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
        <div className="viewer-page-area">
          {status === "loading" && <div className="viewer-status">{isPaged ? "Rendering page…" : "Loading extracted text…"}</div>}
          {status === "error" && <div className="viewer-status error">Could not load this page.</div>}
          {isPaged ? (
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
      </div>
    </div>,
    document.body,
  );
}
