import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { bodyForLoggedBook, confidentMatch, findLoggedBook, lookupQuery, providerName } from "./catalogueMatch.js";
import Cover from "./Cover.jsx";

async function call(apiBase, path, options) {
  const response = await fetch(`${apiBase}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That did not work. Please try again.");
  return data;
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// Log a book you have, with no file: type its title and author, and the details
// and a cover are looked up and filled in. It then stands on the shelf like any
// other book; its page offers to attach the PDF whenever you have it. Only the
// text typed here is sent to the catalogue (Open Library, Google Books).
export default function AddBookDialog({ apiBase, documents, onClose, onChanged, onOpenBook }) {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [phase, setPhase] = useState("form"); // form, adding, looking, done
  const [error, setError] = useState("");
  const [again, setAgain] = useState(null); // a book already on the shelf, awaiting "Add anyway"
  const [result, setResult] = useState(null);
  const titleInput = useRef(null);
  const busy = phase === "adding" || phase === "looking";

  const inForm = phase === "form";
  useEffect(() => { if (inForm) titleInput.current?.focus(); }, [inForm]);
  useEffect(() => {
    const key = (event) => { if (event.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [busy, onClose]);

  async function submit(event, confirmed = false) {
    event?.preventDefault();
    const typed = { title: title.trim(), author: author.trim() };
    if (!typed.title || busy) return;
    const existing = findLoggedBook(documents, typed.title, typed.author);
    if (existing && !confirmed) { setAgain(existing); return; }
    setAgain(null); setError(""); setPhase("adding");
    let book;
    try {
      book = (await call(apiBase, "/owned-books", json("POST", { title: typed.title, author: typed.author || null, pdf_less: true }))).book;
    } catch (failure) {
      setError(failure.message); setPhase("form");
      return;
    }
    // The book is on the shelf from here on; the lookup only adds to it.
    let summary = { match: "none" };
    setPhase("looking");
    try {
      const found = await call(apiBase, `/catalogue/search?q=${encodeURIComponent(lookupQuery(typed.title, typed.author))}`);
      const candidate = confidentMatch(found.results);
      if (candidate) {
        book = { ...book, ...(await call(apiBase, `/documents/${book.document_id}/metadata-apply`, json("POST", bodyForLoggedBook(candidate, typed)))) };
        summary = { match: "found", provider: providerName(candidate), cover: Boolean(candidate.cover_id) };
      }
    } catch (failure) {
      summary = { match: "unavailable", reason: failure.message };
    }
    window.dispatchEvent(new Event("book-cover-changed"));
    try { await onChanged?.(); } catch { /* the shelf refreshes on its next load */ }
    setResult({ book, ...summary });
    setPhase("done");
  }

  function another() {
    setTitle(""); setAuthor(""); setResult(null); setError(""); setAgain(null); setPhase("form");
  }

  const note = !result ? "" : result.match === "found"
    ? `Details${result.cover ? " and cover" : ""} filled in from ${result.provider}.`
    : result.match === "unavailable"
      ? `The online lookup was not available (${result.reason}). The book is on your shelf with what you typed; you can look it up again from its page.`
      : "No confident match online, so the book is on your shelf with what you typed. Add the rest on its page.";

  return createPortal(
    <div className="add-book-layer" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <div className="add-book-card" role="dialog" aria-modal="true" aria-labelledby="add-book-title">
        {phase !== "done" ? <form onSubmit={submit}>
          <h2 id="add-book-title">Add a book</h2>
          <p className="add-book-lede">Log a book you have, with no file. Its details and cover are looked up for you, and it stands on the shelf like any other.</p>
          <label className="add-book-field">Title
            <input ref={titleInput} className="input" value={title} maxLength={300} placeholder="e.g. The Myth of Sisyphus" disabled={busy} onChange={(event) => { setTitle(event.target.value); setAgain(null); }} />
          </label>
          <label className="add-book-field">Author <span className="muted">(optional, helps find the right edition)</span>
            <input className="input" value={author} maxLength={240} placeholder="e.g. Albert Camus" disabled={busy} onChange={(event) => { setAuthor(event.target.value); setAgain(null); }} />
          </label>
          {again && <p className="add-book-warn" role="status">
            You already have “{again.title || again.filename}” on your shelf.{" "}
            <button type="button" className="text-button" onClick={() => submit(null, true)}>Add it again anyway</button>
          </p>}
          {error && <p className="book-inline-error" role="alert">{error}</p>}
          {busy && <p className="add-book-progress" role="status">{phase === "adding" ? "Adding to your shelf…" : "Looking up details and a cover…"}</p>}
          <div className="add-book-actions">
            <button type="submit" className="button primary" disabled={busy || !title.trim()}>{busy ? "Adding…" : "Add book"}</button>
            <button type="button" className="text-button" disabled={busy} onClick={onClose}>Cancel</button>
          </div>
          <p className="add-book-privacy">Only the title and author you type are sent to Open Library and Google Books.</p>
        </form> : <div className="add-book-done">
          <h2 id="add-book-title">Added to your shelf</h2>
          <div className="add-book-result">
            <Cover apiBase={apiBase} documentId={result.book.document_id} filename={result.book.title} author={result.book.author} fileType="book" width={160} lazy={false} className="add-book-cover" />
            <div>
              <strong>{result.book.title}</strong>
              {result.book.author && <span>{result.book.author}</span>}
              <span className="muted">{[result.book.published_year, result.book.page_count && `${result.book.page_count} pages`].filter(Boolean).join(" · ")}</span>
            </div>
          </div>
          <p role="status" className={`add-book-note${result.match === "found" ? " found" : ""}`}>{note}</p>
          <div className="add-book-actions">
            <button type="button" className="button primary" onClick={() => onOpenBook(result.book)}>Open book</button>
            <button type="button" className="button" onClick={another}>Add another</button>
            <button type="button" className="text-button" onClick={onClose}>Done</button>
          </div>
        </div>}
      </div>
    </div>,
    document.body,
  );
}
