import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import Cover from "./Cover.jsx";
import { CoverFlags } from "./DocMarks.jsx";
import { displayTitle, timeAgo } from "./storage.js";

// How many recent documents the row offers.
export const SHELF_SIZE = 12;

// A row of recently opened documents under the search bar. It keeps itself
// current: opening a document moves it to the front, and the books slide to
// their new places rather than jumping (FLIP). Pointing at a book lifts it.
// When there are more books than fit, the row scrolls sideways — wheel,
// trackpad or the arrow buttons — with the ends fading out.
export default function BookRow({ apiBase, items, onOpen }) {
  const trackRef = useRef(null);
  const nodes = useRef(new Map()); // document_id -> element
  const lastRects = useRef(new Map()); // document_id -> left edge before this render
  const [edges, setEdges] = useState({ start: false, end: false });

  // FLIP: after a reorder, start each book where it used to be and let the
  // transition carry it to where it is now.
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
      void el.offsetWidth; // commit the starting position
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
    // A plain vertical wheel over the row scrolls it sideways.
    const onWheel = (event) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX) || el.scrollWidth <= el.clientWidth) return;
      const atStart = el.scrollLeft <= 0 && event.deltaY < 0;
      const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1 && event.deltaY > 0;
      if (atStart || atEnd) return; // let the page scroll on past the ends
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
    <section className={`book-row${edges.start ? " fade-start" : ""}${edges.end ? " fade-end" : ""}`} aria-label="Recently opened">
      <div className="book-row-head">
        <span className="eyebrow">Recently opened</span>
        <span className="book-row-nav">
          <button type="button" className="icon-button" onClick={() => page(-1)} disabled={!edges.start} aria-label="Scroll left">
            ‹
          </button>
          <button type="button" className="icon-button" onClick={() => page(1)} disabled={!edges.end} aria-label="Scroll right">
            ›
          </button>
        </span>
      </div>
      <div className="book-row-track" ref={trackRef} onScroll={updateEdges}>
        {items.map((item, index) => {
          const { doc } = item;
          return (
            <button
              type="button"
              key={doc.document_id}
              ref={(el) => {
                if (el) nodes.current.set(doc.document_id, el);
                else nodes.current.delete(doc.document_id);
              }}
              className="book"
              style={{ "--i": index }}
              onClick={() => onOpen(item)}
              title={doc.filename}
            >
              <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.filename} width={480}>
                <CoverFlags doc={doc} />
              </Cover>
              <span className="book-title">{displayTitle(doc.filename)}</span>
              <span className="book-meta">{item.openedAt ? `p. ${item.page} · ${timeAgo(item.openedAt)}` : "new"}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
