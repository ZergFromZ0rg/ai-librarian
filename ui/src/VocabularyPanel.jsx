import React, { useEffect, useMemo, useState } from "react";

import { displayTitle } from "./storage.js";
import UnderlinedWords from "./UnderlinedWords.jsx";

// Words are collected, not typed in: underline a single word in your PDF
// viewer (any app) and scan the book, and the word arrives with its page and
// sentence. Here you review them and write the definitions.
export default function VocabularyPanel({ apiBase, documents = [], documentId = "", onOpenDocument, onDirty }) {
  const [words, setWords] = useState([]);
  const [draft, setDraft] = useState(null);
  const [query, setQuery] = useState("");
  const [bookFilter, setBookFilter] = useState("");
  const [collectFrom, setCollectFrom] = useState(documentId);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const books = useMemo(() => documents.filter((doc) => doc.collection_id !== "notes"), [documents]);
  const scannable = useMemo(() => books.filter((doc) => doc.file_type === "pdf"), [books]);
  const bookName = (doc) => doc.title || displayTitle(doc.filename);

  async function request(path, options) {
    const response = await fetch(`${apiBase}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not update vocabulary.");
    return data;
  }
  async function load() {
    try {
      const suffix = documentId ? `?document_id=${encodeURIComponent(documentId)}` : "";
      setWords((await request(`/vocabulary${suffix}`)).words);
    } catch (failure) { setError(failure.message); }
  }
  useEffect(() => { load(); }, [documentId]);
  useEffect(() => { onDirty?.(Boolean(draft)); }, [draft, onDirty]);
  useEffect(() => { if (!collectFrom && scannable.length) setCollectFrom(scannable[0].document_id); }, [scannable, collectFrom]);

  async function save(event) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const { word_id, word, definition, part_of_speech, example, document_id, source_page, source_quote } = draft;
      await request(`/vocabulary/${word_id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word, definition, part_of_speech, example, document_id: document_id || null, source_page: source_page || null, source_quote }),
      });
      setDraft(null); await load();
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  async function remove(item) {
    if (!window.confirm(`Delete “${item.word}” from vocabulary?`)) return;
    setBusy(true); setError("");
    try { await request(`/vocabulary/${item.word_id}`, { method: "DELETE" }); if (draft?.word_id === item.word_id) setDraft(null); await load(); }
    catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }

  const needle = query.trim().toLocaleLowerCase();
  const shown = words.filter((item) => (!bookFilter || item.document_id === bookFilter) && (!needle || `${item.word} ${item.definition}`.toLocaleLowerCase().includes(needle)));
  const byId = new Map(books.map((doc) => [doc.document_id, doc]));
  const collectDoc = byId.get(collectFrom);

  return <div className={`vocabulary-panel${documentId ? " vocabulary-book" : ""}`}>
    <div className="vocabulary-head">
      <div><h2>{documentId ? "Vocabulary" : "Vocabulary notebook"}</h2><p className="muted">Underline a single word in a book and it is collected here with its page and sentence.</p></div>
      {!documentId && <div className="vocabulary-filters"><input className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter words" aria-label="Filter vocabulary" /><select value={bookFilter} onChange={(e) => setBookFilter(e.target.value)} aria-label="Filter vocabulary by book"><option value="">All books</option>{books.map((doc) => <option value={doc.document_id} key={doc.document_id}>{bookName(doc)}</option>)}</select></div>}
    </div>
    {error && <p className="notice error" role="alert">{error}</p>}
    <section className="vocabulary-collect">
      <header>
        <h3>Collect underlined words</h3>
        {!documentId && (scannable.length
          ? <select value={collectFrom} onChange={(e) => setCollectFrom(e.target.value)} aria-label="Book to scan">{scannable.map((doc) => <option value={doc.document_id} key={doc.document_id}>{bookName(doc)}</option>)}</select>
          : null)}
      </header>
      {(documentId ? doc_is_pdf(books, documentId) : collectDoc)
        ? <UnderlinedWords key={collectFrom || documentId} apiBase={apiBase} documentId={documentId || collectFrom} onAdded={load} />
        : <p className="muted empty-state">{documentId ? "Only PDF books can be scanned for underlines." : "Add a PDF book to the library, underline words in it, then scan it here."}</p>}
      {collectDoc && !documentId && onOpenDocument && <button type="button" className="text-button" onClick={() => onOpenDocument(collectDoc)}>Open {bookName(collectDoc)} to underline words ↗</button>}
    </section>
    <div className="vocabulary-list">{shown.map((item) => {
      const source = byId.get(item.document_id);
      const editing = draft?.word_id === item.word_id;
      return <article key={item.word_id} className="vocabulary-entry">
        {editing ? <form className="vocabulary-edit" onSubmit={save}>
          <label>Word<input className="input" required maxLength={120} value={draft.word} onChange={(e) => setDraft({ ...draft, word: e.target.value })} /></label>
          <label>Part of speech<input className="input" maxLength={80} placeholder="noun, verb…" value={draft.part_of_speech || ""} onChange={(e) => setDraft({ ...draft, part_of_speech: e.target.value })} /></label>
          <label>Definition<textarea className="input" autoFocus rows={3} maxLength={4000} value={draft.definition} onChange={(e) => setDraft({ ...draft, definition: e.target.value })} /></label>
          <label>Example or context<textarea className="input" rows={2} maxLength={2000} value={draft.example || ""} onChange={(e) => setDraft({ ...draft, example: e.target.value })} /></label>
          <div className="book-page-actions"><button className="button primary small" disabled={busy}>{busy ? "Saving…" : "Save"}</button><button className="text-button" type="button" onClick={() => setDraft(null)}>Cancel</button></div>
        </form> : <>
          <header><div><h3>{item.word}</h3>{item.part_of_speech && <span>{item.part_of_speech}</span>}</div><div><button className="text-button" onClick={() => setDraft({ ...item })}>{item.definition ? "Edit" : "Add definition"}</button><button className="text-button danger" disabled={busy} onClick={() => remove(item)}>Delete</button></div></header>
          {item.definition ? <p>{item.definition}</p> : <p className="muted">No definition yet.</p>}
          {item.example && <p className="vocabulary-example-copy">“{item.example}”</p>}
          {item.source_quote && <blockquote>{item.source_quote}</blockquote>}
          {source && <button className="text-button" onClick={() => onOpenDocument?.(source, item.source_page)}>{bookName(source)}{item.source_page ? ` · page ${item.source_page}` : ""}</button>}
        </>}
      </article>;
    })}{!shown.length && <p className="muted empty-state">No vocabulary saved here yet.</p>}</div>
  </div>;
}

const doc_is_pdf = (books, id) => books.some((doc) => doc.document_id === id && doc.file_type === "pdf");
