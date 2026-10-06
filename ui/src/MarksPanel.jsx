import React, { useEffect, useMemo, useRef, useState } from "react";

import { COLORS, KIND_LABELS, markSummary } from "./annotations.js";
import UnderlinedWords from "./UnderlinedWords.jsx";

const FILTERS = [["all", "All"], ["highlight", "Highlights"], ["underline", "Underlines"], ["text", "Text"], ["ink", "Drawings"]];

// The reader's side panel: every mark in the book (click to jump to it, edit
// its comment or colour, remove it) and the underlined-words scan.
export default function MarksPanel({ apiBase, documentId, marks, selectedId, focusComment, page, onJump, onUpdate, onDelete, onWordsAdded }) {
  const [tab, setTab] = useState("marks");
  const [filter, setFilter] = useState("all");
  const comment = useRef(null);
  const selected = marks.find((mark) => mark.annotation_id === selectedId);

  useEffect(() => { if (selected) setTab("marks"); }, [selectedId]);
  useEffect(() => { if (focusComment && selected) comment.current?.focus(); }, [focusComment, selectedId, tab]);

  const shown = useMemo(() => marks.filter((mark) => filter === "all" || mark.kind === filter), [marks, filter]);
  const pages = useMemo(() => {
    const groups = new Map();
    shown.forEach((mark) => groups.set(mark.page, [...(groups.get(mark.page) || []), mark]));
    return [...groups.entries()].sort((a, b) => a[0] - b[0]);
  }, [shown]);

  return (
    <aside className="marks-panel" aria-label="Marks and vocabulary">
      <div className="marks-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "marks"} className={tab === "marks" ? "on" : ""} onClick={() => setTab("marks")}>Marks <span>{marks.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "words"} className={tab === "words" ? "on" : ""} onClick={() => setTab("words")}>Underlined words</button>
      </div>
      {tab === "words" ? (
        <div className="marks-body"><UnderlinedWords apiBase={apiBase} documentId={documentId} onAdded={onWordsAdded} /></div>
      ) : (
        <div className="marks-body">
          {selected && (
            <section className="mark-editor">
              <header><strong>{KIND_LABELS[selected.kind]}</strong><span className="muted">page {selected.page}</span></header>
              {selected.kind !== "ink" && selected.kind !== "text" && selected.text && <blockquote>{selected.text}</blockquote>}
              {selected.kind === "text" && (
                <textarea className="input" rows={3} defaultValue={selected.text} key={`t${selected.annotation_id}`}
                  onBlur={(event) => event.target.value.trim() && event.target.value.trim() !== selected.text && onUpdate(selected.annotation_id, { text: event.target.value })} />
              )}
              <div className="mark-colors">
                {COLORS[selected.kind].map((color) => (
                  <button key={color} type="button" className={`swatch${selected.color === color ? " on" : ""}`} style={{ background: color }} aria-label={`Colour ${color}`} onClick={() => onUpdate(selected.annotation_id, { color })} />
                ))}
              </div>
              {selected.kind !== "ink" && selected.kind !== "text" && (
                <textarea ref={comment} className="input" rows={3} placeholder="Add a comment (it is searchable)" defaultValue={selected.comment || ""} key={`c${selected.annotation_id}`}
                  onBlur={(event) => event.target.value.trim() !== (selected.comment || "") && onUpdate(selected.annotation_id, { comment: event.target.value })} />
              )}
              <div className="mark-editor-actions">
                {selected.page !== page && <button type="button" className="text-button" onClick={() => onJump(selected.page, selected.annotation_id)}>Go to page {selected.page}</button>}
                <button type="button" className="text-button danger" onClick={() => onDelete(selected)}>Delete</button>
              </div>
            </section>
          )}
          <div className="marks-filters">
            {FILTERS.map(([id, label]) => <button key={id} type="button" className={filter === id ? "on" : ""} onClick={() => setFilter(id)}>{label}</button>)}
          </div>
          {!marks.length && <p className="muted empty-state">Nothing marked yet. Choose Highlight, Underline, Text or Draw above, or select words on the page.</p>}
          {pages.map(([number, items]) => (
            <section key={number} className="marks-page">
              <h4>Page {number}</h4>
              <ul>
                {items.map((mark) => (
                  <li key={mark.annotation_id}>
                    <button type="button" className={mark.annotation_id === selectedId ? "on" : ""} onClick={() => onJump(mark.page, mark.annotation_id)}>
                      <i className="swatch tiny" style={{ background: mark.color }} />
                      <span><b>{KIND_LABELS[mark.kind]}</b> {markSummary(mark)}{mark.comment ? <em> — {mark.comment}</em> : null}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </aside>
  );
}
