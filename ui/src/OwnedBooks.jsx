import React, { useMemo, useState } from "react";

import { fullBody } from "./catalogueMatch.js";
import Cover from "./Cover.jsx";
import { CoverFlags } from "./DocMarks.jsx";
import LookupPanel from "./LookupPanel.jsx";

export default function OwnedBooks({ apiBase, documents, onOpenDocument, onChanged }) {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [lookupOpen, setLookupOpen] = useState(false);
  const books = useMemo(
    () => documents.filter((document) => document.record_type === "standalone"),
    [documents],
  );

  async function addBook(event) {
    event.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/owned-books`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          author: author.trim() || null,
          notes: notes.trim() || null,
          pdf_less: true,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not save this book");
      setTitle("");
      setAuthor("");
      setNotes("");
      await onChanged?.();
      onOpenDocument?.(data.book);
    } catch (addError) {
      setError(addError.message);
    } finally {
      setBusy(false);
    }
  }

  // Create a book from an Open Library match: details and cover come with it.
  async function addFromMatch(candidate) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/owned-books`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: candidate.title, author: candidate.author, pdf_less: true }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not save this book");
      let book = data.book;
      const applied = await fetch(`${apiBase}/documents/${book.document_id}/metadata-apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fullBody(candidate)),
      });
      if (applied.ok) book = { ...book, ...(await applied.json()) };
      else setError("The book was added, but its details could not be filled in. You can look it up again from its page.");
      setLookupOpen(false);
      window.dispatchEvent(new Event("book-cover-changed"));
      await onChanged?.();
      onOpenDocument?.(book);
    } catch (addError) {
      setError(addError.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeBook(book) {
    if (!window.confirm(`Remove “${book.title}” from your catalogue?`)) return;
    setError("");
    try {
      const response = await fetch(`${apiBase}/owned-books/${book.document_id}`, { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not remove this book");
      await onChanged?.();
    } catch (removeError) {
      setError(removeError.message);
    }
  }

  return (
    <section className="owned-books" aria-label="Books without files">
      <div className="owned-books-intro">
        <div>
          <span className="eyebrow">Catalogue books</span>
          <h3>Add a book even when you do not have a digital file.</h3>
          <p>It gets the same details, cover, shelves, reading status, review, and notes as every other book.</p>
        </div>
        <span className="owned-books-count mono">{books.length} without files</span>
      </div>

      <div className="owned-lookup">
        <button type="button" className="button" aria-expanded={lookupOpen} onClick={() => setLookupOpen((open) => !open)}>{lookupOpen ? "Close lookup" : "Find by ISBN or title"}</button>
        {lookupOpen && <LookupPanel apiBase={apiBase} onPick={addFromMatch} pickLabel={busy ? "Adding…" : "Add this book"} />}
      </div>

      <form className="owned-book-form" onSubmit={addBook}>
        <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Book title" aria-label="Book title" />
        <input className="input" value={author} onChange={(event) => setAuthor(event.target.value)} placeholder="Author (optional)" aria-label="Author" />
        <input className="input" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Initial review or note (optional)" aria-label="Initial review or note" />
        <button type="submit" className="button primary" disabled={busy || !title.trim()}>{busy ? "Adding…" : "Add to catalogue"}</button>
      </form>

      {error && <div className="notice error">{error}</div>}
      {books.length === 0 ? (
        <p className="owned-books-empty muted">No fileless books yet. Add a title above, then fill in its metadata and cover.</p>
      ) : (
        <div className="owned-book-list">
          {books.map((book) => (
            <article className="owned-book-card" key={book.document_id}>
              <button type="button" className="owned-book-open" onClick={() => onOpenDocument(book)}>
                <Cover apiBase={apiBase} documentId={book.document_id} filename={book.title} author={book.author} fileType="book" width={160} className="cover-mini">
                  <CoverFlags doc={book} />
                </Cover>
                <span className="owned-book-card-main">
                  <strong>{book.title}</strong>
                  {book.author && <span className="owned-book-author">{book.author}</span>}
                  <span className="owned-book-notes">Open book record</span>
                </span>
              </button>
              <div className="owned-book-card-side">
                <span className="owned-book-status">No file</span>
                <button type="button" className="text-button danger" onClick={() => removeBook(book)}>Remove</button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
