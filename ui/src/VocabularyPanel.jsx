import React, { useEffect, useMemo, useState } from "react";
import { displayTitle } from "./storage.js";

const blank = { word: "", definition: "", part_of_speech: "", example: "", document_id: "", source_page: "", source_quote: "" };

export default function VocabularyPanel({ apiBase, documents = [], documentId = "", onOpenDocument, onDirty }) {
  const [words, setWords] = useState([]);
  const [draft, setDraft] = useState(() => ({ ...blank, document_id: documentId }));
  const [editing, setEditing] = useState("");
  const [query, setQuery] = useState("");
  const [bookFilter, setBookFilter] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const books = useMemo(() => documents.filter((doc) => doc.collection_id !== "notes"), [documents]);
  async function request(path, options) {
    const response = await fetch(`${apiBase}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Could not update vocabulary.");
    return data;
  }
  async function load() {
    try {
      const suffix = documentId ? `?document_id=${encodeURIComponent(documentId)}` : "";
      setWords((await request(`/vocabulary${suffix}`)).words);
    } catch (failure) { setError(failure.message); }
  }
  useEffect(() => { load(); }, [documentId]);
  useEffect(() => { onDirty?.(Boolean(editing || draft.word || draft.definition || draft.part_of_speech || draft.example || draft.source_page || draft.source_quote)); }, [draft, editing, onDirty]);
  function reset() { setDraft({ ...blank, document_id: documentId }); setEditing(""); }
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const payload = { ...draft, document_id: draft.document_id || null, source_page: draft.source_page ? Number(draft.source_page) : null };
      await request(editing ? `/vocabulary/${editing}` : "/vocabulary", { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      reset(); await load();
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  function edit(item) { setEditing(item.word_id); setDraft({ ...blank, ...item, document_id: item.document_id || "", source_page: item.source_page || "" }); }
  async function remove(item) {
    if (!window.confirm(`Delete “${item.word}” from vocabulary?`)) return;
    setBusy(true); setError("");
    try { await request(`/vocabulary/${item.word_id}`, { method: "DELETE" }); if (editing === item.word_id) reset(); await load(); }
    catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  const shown = words.filter((item) => (!bookFilter || item.document_id === bookFilter) && (!query.trim() || `${item.word} ${item.definition}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  const byId = new Map(books.map((doc) => [doc.document_id, doc]));
  return <div className={`vocabulary-panel${documentId ? " vocabulary-book" : ""}`}>
    <div className="vocabulary-head"><div><h2>{documentId ? "Vocabulary" : "Vocabulary notebook"}</h2><p className="muted">Save a definition with the book and page where you found it.</p></div>{!documentId && <div className="vocabulary-filters"><input className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter words" aria-label="Filter vocabulary" /><select value={bookFilter} onChange={(e) => setBookFilter(e.target.value)} aria-label="Filter vocabulary by book"><option value="">All books</option>{books.map((doc) => <option value={doc.document_id} key={doc.document_id}>{doc.title || displayTitle(doc.filename)}</option>)}</select></div>}</div>
    {error && <p className="notice error" role="alert">{error}</p>}
    <form className="vocabulary-form" onSubmit={submit}>
      <label>Word<input className="input" required maxLength={120} value={draft.word} onChange={(e) => setDraft({ ...draft, word: e.target.value })} /></label>
      <label>Part of speech<input className="input" maxLength={80} placeholder="noun, verb…" value={draft.part_of_speech} onChange={(e) => setDraft({ ...draft, part_of_speech: e.target.value })} /></label>
      <label className="vocabulary-definition">Definition<textarea className="input" required rows={2} maxLength={4000} value={draft.definition} onChange={(e) => setDraft({ ...draft, definition: e.target.value })} /></label>
      <label>Book<select disabled={Boolean(documentId)} value={draft.document_id} onChange={(e) => setDraft({ ...draft, document_id: e.target.value })}><option value="">No linked book</option>{books.map((doc) => <option value={doc.document_id} key={doc.document_id}>{doc.title || displayTitle(doc.filename)}</option>)}</select></label>
      <label>Page<input className="input" type="number" min="1" value={draft.source_page} onChange={(e) => setDraft({ ...draft, source_page: e.target.value })} /></label>
      <label className="vocabulary-example">Example or context<textarea className="input" rows={2} maxLength={2000} value={draft.example} onChange={(e) => setDraft({ ...draft, example: e.target.value })} /></label>
      <label className="vocabulary-quote">Source quote<textarea className="input" rows={2} maxLength={4000} value={draft.source_quote} onChange={(e) => setDraft({ ...draft, source_quote: e.target.value })} /></label>
      <div className="book-page-actions"><button className="button primary small" disabled={busy}>{busy ? "Saving…" : editing ? "Update word" : "Save word"}</button>{editing && <button className="text-button" type="button" onClick={reset}>Cancel</button>}</div>
    </form>
    <div className="vocabulary-list">{shown.map((item) => { const source = byId.get(item.document_id); return <article key={item.word_id} className="vocabulary-entry"><header><div><h3>{item.word}</h3>{item.part_of_speech && <span>{item.part_of_speech}</span>}</div><div><button className="text-button" onClick={() => edit(item)}>Edit</button><button className="text-button danger" disabled={busy} onClick={() => remove(item)}>Delete</button></div></header><p>{item.definition}</p>{item.example && <p className="vocabulary-example-copy">“{item.example}”</p>}{item.source_quote && <blockquote>{item.source_quote}</blockquote>}{source && <button className="text-button" onClick={() => onOpenDocument?.(source)}>{source.title || displayTitle(source.filename)}{item.source_page ? ` · page ${item.source_page}` : ""}</button>}</article>; })}{!shown.length && <p className="muted empty-state">No vocabulary saved here yet.</p>}</div>
  </div>;
}
