import React from "react";

// The search row under the reader's top bar. Typing searches the whole PDF (the
// parent runs PDF.js's find controller); Enter and Shift+Enter step through the
// matches, Escape closes it.
export default function PdfFindBar({ inputRef, query, onQuery, summary, onNext, onPrevious, onClose }) {
  return (
    <div className="pdf-find" role="search">
      <input
        ref={inputRef}
        type="search"
        className="pdf-find-input"
        value={query}
        placeholder="Find in this PDF"
        aria-label="Find in this PDF"
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => onQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); if (event.shiftKey) onPrevious(); else onNext(); }
          else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
        }}
      />
      <span className="pdf-find-summary" role="status">{summary}</span>
      <button type="button" className="text-button" aria-label="Previous match" title="Previous match (Shift+Enter)" disabled={!query.trim()} onClick={onPrevious}>↑</button>
      <button type="button" className="text-button" aria-label="Next match" title="Next match (Enter)" disabled={!query.trim()} onClick={onNext}>↓</button>
      <button type="button" className="text-button" aria-label="Close search" title="Close search (Esc)" onClick={onClose}>✕</button>
    </div>
  );
}
