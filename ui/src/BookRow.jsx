import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import Cover from "./Cover.jsx";
import { clothColor } from "./clothCovers.js";
import { CoverFlags } from "./DocMarks.jsx";
import { displayTitle, timeAgo } from "./storage.js";

export const SHELF_SIZE = 12;

const SPINE_HEIGHTS = [232, 218, 244, 224, 238, 210, 230, 220, 240, 215, 234, 222];

export default function BookRow({ apiBase, items, onOpen }) {
  const trackRef = useRef(null);
  const pointerType = useRef(null);
  const nodes = useRef(new Map());
  const lastRects = useRef(new Map());
  const [activeId, setActiveId] = useState(null);
  const [edges, setEdges] = useState({ start: false, end: false });
  const active = items.find((item) => item.doc.document_id === activeId);

  // Keep the row's existing slide animation when opening a book reorders it.
  useLayoutEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const next = new Map();
    for (const [id, el] of nodes.current) {
      const left = el.offsetLeft;
      next.set(id, left);
      const prev = lastRects.current.get(id);
      if (reduce || prev == null || prev === left) continue;
      el.style.transition = "none";
      el.style.transform = `translateX(${prev - left}px)`;
      void el.offsetWidth;
      el.style.transition = "";
      el.style.transform = "";
    }
    lastRects.current = next;
  }, [items]);

  const updateEdges = useCallback(() => {
    const el = trackRef.current;
    if (!el) return;
    setEdges({ start: el.scrollLeft > 4, end: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  }, []);

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return undefined;
    updateEdges();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateEdges);
    observer?.observe(el);
    const onWheel = (event) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX) || el.scrollWidth <= el.clientWidth) return;
      const atStart = el.scrollLeft <= 0 && event.deltaY < 0;
      const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1 && event.deltaY > 0;
      if (atStart || atEnd) return;
      event.preventDefault();
      el.scrollLeft += event.deltaY;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      observer?.disconnect();
      el.removeEventListener("wheel", onWheel);
    };
  }, [updateEdges, items.length]);

  const page = (direction) => {
    const el = trackRef.current;
    if (el) el.scrollBy({ left: direction * el.clientWidth * 0.8, behavior: "smooth" });
  };

  if (!items.length) return null;

  return (
    <section className="book-row" aria-label="Books from your library" onMouseLeave={(event) => {
      if (!event.currentTarget.contains(document.activeElement)) setActiveId(null);
    }}>
      <div className="book-row-head">
        <span className="eyebrow">From your library</span>
        <span className="book-row-hint">Hover over a spine to pull out a book</span>
        <span className="book-row-nav">
          <button type="button" className="icon-button" onClick={() => page(-1)} disabled={!edges.start} aria-label="Scroll books left">‹</button>
          <button type="button" className="icon-button" onClick={() => page(1)} disabled={!edges.end} aria-label="Scroll books right">›</button>
        </span>
      </div>
      <div className="bookcase">
        <div className={`bookcase-preview${active ? " is-active" : ""}`}>
          {active ? (
            <button type="button" key={active.doc.document_id} className="bookcase-preview-open" onClick={() => onOpen(active)} aria-label={`Open ${active.doc.title || displayTitle(active.doc.filename)}`}>
              <Cover apiBase={apiBase} documentId={active.doc.document_id} filename={active.doc.filename} author={active.doc.author} fileType={active.doc.file_type} width={480}>
                <CoverFlags doc={active.doc} />
              </Cover>
              <span className="bookcase-preview-title">{active.doc.title || displayTitle(active.doc.filename)}</span>
              <span className="bookcase-preview-meta">{[active.doc.author, active.doc.rating ? `★ ${active.doc.rating}/5` : null, active.openedAt ? `p. ${active.page} · ${timeAgo(active.openedAt)}` : null].filter(Boolean).join(" · ") || "Open book"}</span>
            </button>
          ) : (
            <div className="bookcase-empty" aria-hidden="true">
              <span className="bookcase-empty-mark">✦</span>
              <span>Select a spine</span>
              <small>to see its cover</small>
            </div>
          )}
        </div>
        <div className={`bookcase-stack${edges.start ? " fade-start" : ""}${edges.end ? " fade-end" : ""}`}>
          <div className="bookcase-track" ref={trackRef} onScroll={updateEdges}>
            {items.map((item, index) => {
              const { doc } = item;
              const title = doc.title || displayTitle(doc.filename);
              return (
                <button
                  type="button"
                  key={doc.document_id}
                  ref={(el) => {
                    if (el) nodes.current.set(doc.document_id, el);
                    else nodes.current.delete(doc.document_id);
                  }}
                  className={`spine-book${activeId === doc.document_id ? " is-active" : ""}`}
                  style={{ "--i": index, "--spine-color": clothColor(doc.document_id).bg, "--spine-height": `${SPINE_HEIGHTS[index % SPINE_HEIGHTS.length]}px` }}
                  onPointerEnter={(event) => { if (event.pointerType !== "touch") setActiveId(doc.document_id); }}
                  onPointerDown={(event) => { pointerType.current = event.pointerType; }}
                  onFocus={() => { if (pointerType.current !== "touch") setActiveId(doc.document_id); }}
                  onClick={() => {
                    const fromTouch = pointerType.current === "touch";
                    pointerType.current = null;
                    if (fromTouch && activeId !== doc.document_id) {
                      setActiveId(doc.document_id);
                      return;
                    }
                    onOpen(item);
                  }}
                  aria-label={`Open ${title}${doc.author ? ` by ${doc.author}` : ""}`}
                  title={`${title}${doc.author ? ` — ${doc.author}` : ""}`}
                >
                  <span className="spine-book-bands" aria-hidden="true" />
                  <span className="spine-book-title">{title}</span>
                  <span className="spine-book-author">{doc.author || "Author unknown"}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="bookcase-shelf" aria-hidden="true" />
      </div>
    </section>
  );
}
