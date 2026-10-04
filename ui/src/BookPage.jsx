import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { createPortal } from "react-dom";
import BookDetails from "./BookDetails.jsx";
import { displayTitle } from "./storage.js";
import { katexPlugin, prepareMath, remarkPlugins } from "./markdown.js";

export default function BookPage({ apiBase, documentId, passage, onClose, onRead, onAsk, onChanged, onCoverChanged, readerOpen, askEnabled }) {
  const [doc, setDoc] = useState(null);
  const [notes, setNotes] = useState([]);
  const [detailsDirty, setDetailsDirty] = useState(false);
  const [draft, setDraft] = useState("");
  const [quote, setQuote] = useState(passage?.snippet || passage?.matched || "");
  const [page, setPage] = useState(passage?.page || null);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(false);
  const closeRef = useRef(null);
  const readerVisible = useRef(readerOpen);
  readerVisible.current = readerOpen;
  const wasReading = useRef(false);
  useEffect(() => {
    if (wasReading.current && !readerOpen) closeRef.current?.focus();
    wasReading.current = readerOpen;
  }, [readerOpen]);
  const dirty = useRef(false);
  dirty.current = Boolean(draft || quote || detailsDirty);

  async function request(path, options) {
    const response = await fetch(`${apiBase}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save this change.");
    return data;
  }
  const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const loadNotes = async () => setNotes((await request(`/notes?source_document_id=${encodeURIComponent(documentId)}`)).notes);
  const setDocument = (data) => {
    setDoc(data);
  };
  function close() {
    if (!dirty.current || window.confirm("Discard your unsaved changes?")) onClose();
  }
  useEffect(() => {
    let cancelled = false;
    Promise.all([request(`/documents/${documentId}`), request(`/notes?source_document_id=${encodeURIComponent(documentId)}`)])
      .then(([data, result]) => { if (!cancelled) { setDocument(data); setNotes(result.notes); } })
      .catch((failure) => { if (!cancelled) setError(failure.message); });
    const previous = document.activeElement;
    closeRef.current?.focus();
    const key = (event) => {
      if (readerVisible.current) return;
      if (event.key === "Escape") { event.stopImmediatePropagation(); close(); }
      if (event.key === "Tab") {
        const elements = [...closeRef.current.closest("[role=dialog]").querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href]')].filter((el) => el.getClientRects().length);
        const first = elements[0], last = elements.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    const unload = (event) => { if (dirty.current) { event.preventDefault(); event.returnValue = ""; } };
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", key, true);
    window.addEventListener("beforeunload", unload);
    return () => { cancelled = true; document.body.style.overflow = overflow; window.removeEventListener("keydown", key, true); window.removeEventListener("beforeunload", unload); previous?.focus(); };
  }, [documentId]);

  async function run(action, message) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(message); onChanged(); } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  function resetDraft() { setDraft(""); setQuote(""); setPage(null); setEditing(null); setPreview(false); }
  function saveNote(event) {
    event.preventDefault();
    run(async () => {
      const text = draft.trim() || (quote ? "Saved passage" : "");
      await request(editing ? `/notes/${editing}` : "/notes", json(editing ? "PUT" : "POST", { text, ...(!editing ? { source_document_id: documentId, source_page: page, source_quote: quote || null } : {}) }));
      resetDraft(); await loadNotes();
    }, "Note saved. It will be searchable after indexing.");
  }
  function edit(note) {
    if ((draft || quote) && !window.confirm("Discard the current draft?")) return;
    setEditing(note.document_id); setDraft(note.text); setQuote(note.source_quote || ""); setPage(note.source_page); setPreview(false);
  }
  async function copyLink() {
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(`${window.location.href.split("#")[0]}#book/${documentId}`);
      setNotice("Book link copied.");
    } catch { setError("Could not copy the link. Copy it from the address bar."); }
  }
  const source = (at, snippet) => ({ documentId, documentName: doc.title || doc.filename, page: at || 1, snippet });
  const markdown = (text) => <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={[katexPlugin]} skipHtml>{prepareMath(text)}</ReactMarkdown>;
  const hasSource = doc?.record_type !== "standalone";
  return createPortal(
    <section className="book-page-overlay" role="dialog" aria-modal={!readerOpen} aria-hidden={readerOpen || undefined} inert={readerOpen || undefined} aria-labelledby="book-page-title">
      <div className="book-page">
        <header className="book-page-nav">
          <button ref={closeRef} className="text-button" onClick={close}>← Back to library</button>
          <a className="text-button" href={`#book/${documentId}`} onClick={(event) => { event.preventDefault(); copyLink(); }}>Copy book link</a>
        </header>
        {error && <p className="notice error" role="alert">{error}</p>}
        {notice && <p className="notice" role="status">{notice}</p>}
        {!doc ? <h1 id="book-page-title">{error ? "Book unavailable" : "Opening book…"}</h1> : <>
          <header className="book-overview">
            <div><div className="book-overview-kicker"><span className="eyebrow">Your personal library</span><span className="book-format">{hasSource ? doc.file_type?.toUpperCase() : "NO FILE"}</span></div><h1 id="book-page-title">{doc.title || displayTitle(doc.filename)}</h1>{doc.author && <p>{doc.author}</p>}</div>
            {hasSource ? <div className="book-page-actions"><button className="button primary" onClick={() => onRead(source(passage?.page))}>Read {doc.file_type === "epub" ? "EPUB" : doc.file_type === "pdf" ? "PDF" : "source"} ↗</button><button className="button" disabled={!askEnabled} title={!askEnabled ? "Enable an answering model in Settings" : undefined} onClick={() => { if (!dirty.current || window.confirm("Discard your unsaved changes?")) onAsk(source()); }}>Ask this book</button></div> : <p className="book-record-label">Catalogue record · no digital file attached</p>}
          </header>
          <BookDetails doc={doc} apiBase={apiBase} onDocument={(updated) => { setDocument(updated); onChanged(); }} onDirty={setDetailsDirty} onCoverChanged={onCoverChanged} />
          <div className="book-page-notes">
            <div className="book-page-section-head"><h2>Your notes</h2><span className="muted">{notes.length} saved · searchable in Notes</span></div>
            <form className="book-note-editor" onSubmit={saveNote}>
              <h3>{editing ? "Edit note" : quote ? "Save this passage" : "Add a thought"}</h3>
              {quote && <><blockquote>{quote}</blockquote><p className="muted">Source location {page || "—"} · saved separately from your commentary</p></>}
              {preview ? <div className="prose book-note-preview">{markdown(draft || "Nothing to preview yet.")}</div> : <textarea className="input" rows={5} maxLength={20000} aria-label="Note in Markdown" placeholder="What stood out? Write in Markdown, including headings, lists, and equations…" value={draft} onChange={(event) => setDraft(event.target.value)} />}
              <div className="book-page-actions">
                <button className="button primary small" disabled={busy || (!draft.trim() && !quote)}>Save note</button>
                <button type="button" className="text-button" onClick={() => setPreview(!preview)}>{preview ? "Write" : "Preview"}</button>
                {(editing || quote || draft) && <button type="button" className="text-button" onClick={() => { if (window.confirm("Discard this draft?")) resetDraft(); }}>Cancel draft</button>}
              </div>
            </form>
            {!notes.length && <p className="muted">Keep your interpretation alongside the source. Saved passages and your own notes will appear here.</p>}
            {notes.map((note) => <article className="book-note" key={note.document_id}>
              <div className="prose">{markdown(note.text)}</div>
              {note.source_quote && <blockquote>{note.source_quote}</blockquote>}
              <footer className="book-page-actions">
                {note.source_page && <button className="text-button" onClick={() => onRead(source(note.source_page, note.source_quote))}>Open source · {note.source_page}</button>}
                <button className="text-button" disabled={busy} onClick={() => edit(note)}>Edit</button>
                <button className="text-button danger" disabled={busy} onClick={() => { if (window.confirm("Delete this note?")) run(async () => { await request(`/documents/${note.document_id}`, { method: "DELETE" }); if (editing === note.document_id) resetDraft(); await loadNotes(); }, "Note deleted."); }}>Delete</button>
              </footer>
            </article>)}
          </div>
        </>}
      </div>
    </section>, document.body,
  );
}
