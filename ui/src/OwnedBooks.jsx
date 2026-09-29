import React, { useEffect, useState } from "react";

function MatchAlert({ book, documents, onOpenDocument }) {
  if (!book.match_alert || !book.match) return null;
  const document = documents.find((item) => item.document_id === book.match.document_id);
  return (
    <div className="owned-book-alert">
      <span aria-hidden="true">✓</span>
      PDF match found
      {document && (
        <button type="button" className="text-button" onClick={() => onOpenDocument(document)}>
          Open PDF
        </button>
      )}
    </div>
  );
}

export default function OwnedBooks({ apiBase, documents, onOpenDocument }) {
  const [books, setBooks] = useState([]);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [notes, setNotes] = useState("");
  const [pdfLess, setPdfLess] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const documentSignature = documents
    .map((document) => `${document.document_id}:${document.indexing_status}:${document.filename}`)
    .join("|");

  async function load() {
    try {
      const response = await fetch(`${apiBase}/owned-books`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not load owned books");
      setBooks(data.books || []);
    } catch (loadError) {
      setError(loadError.message);
    }
  }

  useEffect(() => {
    load();
  }, [apiBase, documentSignature]);

  async function addBook(event) {
    event.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/owned-books`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), author: author.trim() || null, notes: notes.trim() || null, pdf_less: pdfLess }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not save this book");
      setBooks((current) => [data.book, ...current.filter((item) => item.book_id !== data.book.book_id)]);
      setTitle("");
      setAuthor("");
      setNotes("");
      setPdfLess(true);
    } catch (addError) {
      setError(addError.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeBook(bookId) {
    try {
      await fetch(`${apiBase}/owned-books/${bookId}`, { method: "DELETE" });
      setBooks((current) => current.filter((book) => book.book_id !== bookId));
    } catch (removeError) {
      setError(removeError.message);
    }
  }

  return (
    <section className="owned-books" aria-label="Owned books">
      <div className="owned-books-intro">
        <div>
          <span className="eyebrow">Owned books</span>
          <h3>Keep a list of books you have, even without a PDF.</h3>
          <p>When a matching PDF arrives, it will be flagged here automatically.</p>
        </div>
        <span className="owned-books-count mono">{books.length} logged</span>
      </div>

      <form className="owned-book-form" onSubmit={addBook}>
        <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Book title" aria-label="Book title" />
        <input className="input" value={author} onChange={(event) => setAuthor(event.target.value)} placeholder="Author (optional)" aria-label="Author" />
        <input className="input" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Where you have it or a note (optional)" aria-label="Notes" />
        <label className="owned-book-check">
          <input type="checkbox" checked={pdfLess} onChange={(event) => setPdfLess(event.target.checked)} />
          PDF-less for now
        </label>
        <button type="submit" className="button primary" disabled={busy || !title.trim()}>{busy ? "Saving…" : "Add book"}</button>
      </form>

      {error && <div className="notice error">{error}</div>}
      {books.length === 0 ? (
        <p className="owned-books-empty muted">Nothing logged yet. Add a title above to start your personal list.</p>
      ) : (
        <div className="owned-book-list">
          {books.map((book) => (
            <article className={`owned-book-card${book.match_alert ? " has-match" : ""}`} key={book.book_id}>
              <div className="owned-book-card-main">
                <h4>{book.title}</h4>
                {book.author && <p className="owned-book-author">{book.author}</p>}
                {book.notes && <p className="owned-book-notes">{book.notes}</p>}
              </div>
              <div className="owned-book-card-side">
                <span className={`owned-book-status${book.match_alert ? " matched" : ""}`}>
                  {book.match_alert ? "PDF found" : book.pdf_less ? "PDF-less" : "Has PDF"}
                </span>
                <MatchAlert book={book} documents={documents} onOpenDocument={onOpenDocument} />
                <button type="button" className="text-button danger" onClick={() => removeBook(book.book_id)}>Remove</button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
