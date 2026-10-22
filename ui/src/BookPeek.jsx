import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import Cover from "./Cover.jsx";
import StarRating, { LikeButton } from "./StarRating.jsx";
import { bookShelf, genresFor } from "./libraryExplore.js";
import { bookNotes, plainPreview, relatedBooks } from "./peek.js";
import { readingProgress } from "./libraryRail.js";
import { displayTitle, readingStatus } from "./storage.js";

const STATUSES = [["to_read", "To read"], ["reading", "Reading"], ["read", "Read"], ["abandoned", "Abandoned"]];
const FORMATS = { pdf: "PDF", epub: "EPUB", word: "DOCX", markdown: "Markdown", text: "TXT" };
const ACQUIRED = { book_store: "Book store", kindle: "Kindle", audiobook: "Audiobook", borrowed: "Borrowed", second_hand: "Second hand", gifted: "Gifted", library: "Library", other: "Other" };

function Row({ label, children }) {
  return <div className="peek-row"><dt>{label}</dt><dd>{children}</dd></div>;
}

// The shelf is typed, with the shelves already in use offered as you go, so
// "Philosophy" and a brand new shelf are the same gesture. Empty goes back to
// the shelf the library suggested.
function ShelfField({ doc, names, onChange }) {
  const current = bookShelf(doc);
  const [draft, setDraft] = useState(current);
  useEffect(() => setDraft(current), [current, doc.document_id]);
  function commit() {
    const next = draft.trim();
    setDraft(next);
    if (next !== current) onChange(next);
  }
  return <>
    <input className="peek-shelf-input" list="peek-shelf-names" value={draft} maxLength={80} placeholder="Add to a shelf" aria-label="Shelf" onChange={(event) => setDraft(event.target.value)} onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
        if (event.key === "Escape") { event.stopPropagation(); setDraft(current); event.currentTarget.blur(); }
      }} />
    <datalist id="peek-shelf-names">{names.map((name) => <option key={name} value={name} />)}</datalist>
  </>;
}

// A quick look at one book, from the shelf: what it is, where you are in it,
// what you wrote and kept from it, and what stands near it. Everything here can
// be changed in place; the full page is one click away for the rest.
export default function BookPeek({ apiBase, doc, documents, notes, shelfNames, onClose, onOpenPage, onPeek, onPatch }) {
  const panelRef = useRef(null);
  const title = doc.title || displayTitle(doc.filename);
  const status = readingStatus(doc);
  const { percent, current, total } = readingProgress(doc);
  const genres = genresFor(doc);
  const { comments, quotes } = useMemo(() => bookNotes(notes, doc.document_id), [notes, doc.document_id]);
  const related = useMemo(() => relatedBooks(doc, documents), [doc, documents]);
  const hasFile = doc.record_type !== "standalone";
  const format = hasFile ? FORMATS[doc.file_type] || doc.file_type?.toUpperCase() : null;
  const isbn = doc.isbn_13 || doc.isbn_10;
  const owned = doc.owned == null ? null : Boolean(doc.owned);
  const patch = (changes) => onPatch(doc.document_id, changes);

  // Moving to another book (a click on a cover, a related book) leaves focus
  // on the panel, so the keyboard stays with what just opened.
  useEffect(() => { panelRef.current?.focus({ preventScroll: true }); }, [doc.document_id]);
  useEffect(() => {
    const key = (event) => {
      if (event.key !== "Escape" || document.querySelector(".book-page-overlay, .pdf-reader")) return;
      onClose();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  return createPortal(<>
    <div className="peek-scrim" onClick={onClose} aria-hidden="true" />
    <aside ref={panelRef} id="book-peek" className="book-peek" role="dialog" aria-modal="false" aria-labelledby="peek-title" tabIndex={-1}>
      <header className="peek-head">
        <button type="button" className="peek-close" onClick={onClose} aria-label="Close details" title="Close (Esc)">×</button>
        <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={320} lazy={false} className="peek-cover" />
        <div className="peek-intro">
          <span className="eyebrow">{format ? `Book · ${format}` : "Book · catalogue record"}</span>
          <h2 id="peek-title">{title}</h2>
          {doc.author && <p className="peek-author">{doc.author}</p>}
          <p className="peek-meta">{[doc.published_year, total ? `${total.toLocaleString()} pages` : null].filter(Boolean).join(" · ")}</p>
          <div className="peek-marks">
            <StarRating size="small" value={doc.rating || null} onChange={(value) => patch({ rating: value })} />
            <LikeButton liked={Boolean(doc.liked)} onChange={(value) => patch({ liked: value })} />
          </div>
        </div>
      </header>

      <div className="peek-body">
        <section aria-labelledby="peek-status-heading">
          <h3 id="peek-status-heading">Reading status</h3>
          <div className="peek-status" role="group" aria-label="Reading status">
            {STATUSES.map(([value, label]) => <button type="button" key={value} className={status === value ? "active" : ""} aria-pressed={status === value} onClick={() => status !== value && patch({ reading_status: value })}>{label}</button>)}
          </div>
          {(status === "reading" || (percent > 0 && status !== "read")) && <div className="peek-progress">
            <span className="summary-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label="Reading progress"><i style={{ width: `${percent}%` }} /></span>
            <span>{percent}%{total ? ` · page ${current.toLocaleString()} of ${total.toLocaleString()}` : ""}</span>
          </div>}
          {status === "read" && doc.finished_at && <p className="peek-note-line">Finished {doc.finished_at}</p>}
        </section>

        <section aria-labelledby="peek-details-heading">
          <h3 id="peek-details-heading">Details</h3>
          <dl className="peek-facts">
            <Row label="Shelf"><ShelfField doc={doc} names={shelfNames} onChange={(name) => patch({ shelf: name ? `Books/${name}` : "" })} /></Row>
            {genres.length > 0 && <Row label="Genres"><span className="peek-tags">{genres.map((genre) => <span key={genre}>{genre}</span>)}</span></Row>}
            {doc.subject && <Row label="Subject">{doc.subject}</Row>}
            {isbn && <Row label="ISBN"><span className="mono">{isbn}</span></Row>}
            {owned != null && <Row label="Ownership">{owned ? "Owned" : "Not owned"}{doc.acquisition_source ? ` · ${ACQUIRED[doc.acquisition_source] || doc.acquisition_source}` : ""}</Row>}
          </dl>
        </section>

        <section aria-labelledby="peek-notes-heading">
          <h3 id="peek-notes-heading">Notes{comments.length > 0 && <span className="peek-count">{comments.length}</span>}</h3>
          {comments.length === 0
            ? <p className="peek-quiet">Nothing written yet. Your thoughts on this book will gather here.</p>
            : <ul className="peek-notes">{comments.slice(0, 3).map((note) => <li key={note.document_id}>{plainPreview(note.text)}</li>)}</ul>}
          {comments.length > 3 && <button type="button" className="text-button" onClick={() => onOpenPage(doc)}>All {comments.length} notes</button>}
        </section>

        <section aria-labelledby="peek-quotes-heading">
          <h3 id="peek-quotes-heading">Quotes{quotes.length > 0 && <span className="peek-count">{quotes.length}</span>}</h3>
          {quotes.length === 0
            ? <p className="peek-quiet">No passages kept. Save one from the reader and it will appear here.</p>
            : quotes.slice(0, 3).map((note) => <figure className="peek-quote" key={note.document_id}>
              <blockquote>{plainPreview(note.source_quote, 260)}</blockquote>
              {note.source_page && <figcaption>p. {note.source_page}</figcaption>}
            </figure>)}
          {quotes.length > 3 && <button type="button" className="text-button" onClick={() => onOpenPage(doc)}>All {quotes.length} quotes</button>}
        </section>

        {related.length > 0 && <section aria-labelledby="peek-related-heading">
          <h3 id="peek-related-heading">Related books</h3>
          <ul className="peek-related">
            {related.map((book) => <li key={book.document_id}>
              <button type="button" onClick={() => onPeek(book)} title={book.author ? `${book.title || displayTitle(book.filename)}, by ${book.author}` : book.title || displayTitle(book.filename)}>
                <Cover apiBase={apiBase} documentId={book.document_id} filename={book.title || book.filename} author={book.author} fileType={book.file_type} width={160} className="peek-related-cover" />
                <span>{book.title || displayTitle(book.filename)}</span>
              </button>
            </li>)}
          </ul>
        </section>}
      </div>

      <footer className="peek-foot">
        <button type="button" className="button primary" onClick={() => onOpenPage(doc)}>Open full page</button>
      </footer>
    </aside>
  </>, document.body);
}
