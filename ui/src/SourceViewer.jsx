import React, { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { MarkControls } from "./DocMarks.jsx";
import { displayTitle } from "./storage.js";

// A lightbox over a server-rendered page image with the matched passage
// highlighted, plus a link out to the raw PDF at the same page. `onPage`
// reports every page the reader lands on (so reopening the document from the
// shelf resumes there); `onScope` narrows the console to this document.
// `doc` is the document's current record (for the read/owned marks) and
// `onPatch(changes)` updates them.
export default function SourceViewer({ apiBase, source, doc, onPatch, onClose, onPage, onScope }) {
  const { documentId, documentName, page: startPage, matched, snippet } = source;
  const [page, setPage] = useState(startPage || 1);
  const [pageCount, setPageCount] = useState(null);
  const [status, setStatus] = useState("loading");

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
    setStatus("loading");
  }, [imageSrc]);

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
            {onScope && (
              <button type="button" className="text-button" onClick={() => onScope({ documentId, documentName })}>
                Search within
              </button>
            )}
            <a className="text-button" href={`${apiBase}/documents/${documentId}/file#page=${page}`} target="_blank" rel="noreferrer">
              Open PDF ↗
            </a>
            <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
              ×
            </button>
          </div>
        </div>
        <div className="viewer-page-area">
          {status === "loading" && <div className="viewer-status">Rendering page…</div>}
          {status === "error" && <div className="viewer-status error">Could not render this page.</div>}
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
