import React, { useCallback, useEffect, useRef, useState } from "react";

const THUMB_WIDTH = 124; // CSS pixels; drawn at 1x, which is plenty at this size

// The reader's side panel: the PDF's own table of contents, and a strip of page
// thumbnails. Both are for getting around a long book, so neither is built until
// the panel is opened.
export default function PdfSidebar({ pdf, outline, pageCount, currentPage, tab, onTab, onGoToPage, onGoToItem }) {
  return (
    <aside className="pdf-sidebar" aria-label="Contents and pages">
      <div className="pdf-sidebar-tabs" role="tablist">
        {[["contents", "Contents"], ["pages", "Pages"]].map(([value, label]) => (
          <button type="button" role="tab" key={value} aria-selected={tab === value} className={`pdf-sidebar-tab${tab === value ? " active" : ""}`} onClick={() => onTab(value)}>{label}</button>
        ))}
      </div>
      {tab === "contents"
        ? <Contents outline={outline} onGoToItem={onGoToItem} />
        : <Thumbnails pdf={pdf} pageCount={pageCount} currentPage={currentPage} onGoToPage={onGoToPage} />}
    </aside>
  );
}

function Contents({ outline, onGoToItem }) {
  if (outline === null) return <p className="pdf-sidebar-note muted">Reading the contents…</p>;
  if (!outline.length) return <p className="pdf-sidebar-note muted">This PDF has no table of contents. Pages shows every page instead.</p>;
  return <nav className="pdf-outline" aria-label="Table of contents"><OutlineList items={outline} onGoToItem={onGoToItem} level={0} /></nav>;
}

function OutlineList({ items, onGoToItem, level }) {
  return <ul className="pdf-outline-list" style={{ "--level": level }}>{items.map((item, index) => <OutlineItem key={index} item={item} onGoToItem={onGoToItem} level={level} />)}</ul>;
}

// Children are only drawn once their parent is opened, so a book with thousands
// of entries stays quick.
function OutlineItem({ item, onGoToItem, level }) {
  const [open, setOpen] = useState(false);
  const children = item.items || [];
  const goes = Boolean(item.dest || /^https?:/i.test(item.url || ""));
  const title = (item.title || "").trim() || "Untitled";
  return (
    <li>
      <div className="pdf-outline-row">
        {children.length
          ? <button type="button" className="pdf-outline-toggle" aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${title}`} onClick={() => setOpen(!open)}>{open ? "▾" : "▸"}</button>
          : <span className="pdf-outline-toggle" aria-hidden="true" />}
        {goes
          ? <button type="button" className="pdf-outline-link" style={{ fontWeight: item.bold ? 600 : undefined, fontStyle: item.italic ? "italic" : undefined }} onClick={() => onGoToItem(item)}>{title}</button>
          : <span className="pdf-outline-link is-plain">{title}</span>}
      </div>
      {open && children.length > 0 && <OutlineList items={children} onGoToItem={onGoToItem} level={level + 1} />}
    </li>
  );
}

function Thumbnails({ pdf, pageCount, currentPage, onGoToPage }) {
  const [ratio, setRatio] = useState(1.3); // height / width, from the first page until each is drawn
  const listRef = useRef(null);
  const callbacks = useRef(new Map());
  const observer = useRef(null);

  useEffect(() => {
    let cancelled = false;
    pdf.getPage(1).then((page) => {
      const box = page.getViewport({ scale: 1 });
      if (!cancelled && box.width > 0) setRatio(box.height / box.width);
      page.cleanup();
    }).catch(() => {});
    return () => { cancelled = true; observer.current?.disconnect(); observer.current = null; };
  }, [pdf]);

  // One observer for all the thumbnails: a page is drawn when it comes near the
  // visible part of the list, and let go of again when it has long gone from it.
  // Made on first use, because the thumbnails' own effects run before this one's.
  const watch = useCallback((element, callback) => {
    if (!observer.current) {
      observer.current = new IntersectionObserver((entries) => {
        for (const entry of entries) callbacks.current.get(entry.target)?.(entry.isIntersecting);
      }, { root: listRef.current, rootMargin: "500px 0px" });
    }
    callbacks.current.set(element, callback);
    observer.current.observe(element);
    return () => { callbacks.current.delete(element); observer.current?.unobserve(element); };
  }, []);

  // Keep the page being read in view in the strip.
  useEffect(() => {
    listRef.current?.querySelector(`[data-page="${currentPage}"]`)?.scrollIntoView({ block: "nearest" });
  }, [currentPage]);

  return (
    <ol className="pdf-thumbs" ref={listRef} aria-label="Pages">
      {Array.from({ length: pageCount }, (_, index) => (
        <Thumb key={index} pdf={pdf} number={index + 1} ratio={ratio} active={currentPage === index + 1} watch={watch} onGo={onGoToPage} />
      ))}
    </ol>
  );
}

function Thumb({ pdf, number, ratio, active, watch, onGo }) {
  const itemRef = useRef(null);
  const canvasRef = useRef(null);
  const [drawn, setDrawn] = useState(false);

  useEffect(() => {
    let task = null;
    let gone = false;
    let visible = false;
    let drawing = false;
    let done = false;
    let again = false; // it came back into view while an earlier drawing was still winding down

    const release = () => {
      try { task?.cancel(); } catch { /* already finished */ }
      task = null;
      done = false;
      const canvas = canvasRef.current;
      if (canvas) { canvas.width = 0; canvas.height = 0; }
      setDrawn(false);
    };
    const draw = async () => {
      if (drawing || gone) return;
      drawing = true;
      try {
        const page = await pdf.getPage(number);
        const box = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: THUMB_WIDTH / box.width });
        const canvas = canvasRef.current;
        if (gone || !visible || !canvas) { page.cleanup(); return; }
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        task = page.render({ canvas, viewport });
        await task.promise;
        done = true;
        if (!gone && visible) setDrawn(true);
        page.cleanup();
      } catch { /* cancelled because it scrolled away, or the document closed */ } finally {
        drawing = false;
        if (again && visible && !done && !gone) { again = false; draw(); }
      }
    };

    const unwatch = watch(itemRef.current, (isVisible) => {
      visible = isVisible;
      if (!isVisible) release();
      else if (drawing) again = true;
      else if (!done) draw();
    });
    return () => { gone = true; unwatch(); try { task?.cancel(); } catch { /* nothing running */ } };
  }, [pdf, number, watch]);

  return (
    <li ref={itemRef} data-page={number}>
      <button type="button" className={`pdf-thumb${active ? " active" : ""}`} aria-label={`Page ${number}`} aria-current={active ? "page" : undefined} onClick={() => onGo(number)}>
        <span className="pdf-thumb-frame" style={drawn ? undefined : { aspectRatio: `1 / ${ratio}` }}>
          <canvas ref={canvasRef} className="pdf-thumb-canvas" />
        </span>
        <span className="pdf-thumb-number">{number}</span>
      </button>
    </li>
  );
}
