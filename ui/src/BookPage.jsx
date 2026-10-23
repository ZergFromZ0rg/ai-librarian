import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { createPortal } from "react-dom";
import BookDetails from "./BookDetails.jsx";
import Cover from "./Cover.jsx";
import PdfMarks from "./PdfMarks.jsx";
import ReadingProgress from "./ReadingProgress.jsx";
import StarRating, { LikeButton } from "./StarRating.jsx";
import VocabularyPanel from "./VocabularyPanel.jsx";
import { isWorkDocument } from "./documentsLibrary.js";
import { readingStatus } from "./storage.js";
import { katexPlugin, prepareMath, remarkPlugins } from "./markdown.js";

const STATUS_LABELS = { to_read: "To read", reading: "Reading", read: "Read", abandoned: "Abandoned" };
const FORMATS = { word: "DOCX", excel: "XLSX", powerpoint: "PPTX", markdown: "Markdown", text: "TXT" };

export default function BookPage({ apiBase, documentId, passage, shelfNames, guardRef, onClose, onRead, onAsk, onChanged, onCoverChanged, readerOpen, askEnabled }) {
  const [doc, setDoc] = useState(null);
  const [notes, setNotes] = useState([]);
  const [detailsDirty, setDetailsDirty] = useState(false);
  const [progressDirty, setProgressDirty] = useState(false);
  const [vocabularyDirty, setVocabularyDirty] = useState(false);
  const [draft, setDraft] = useState("");
  const [quote, setQuote] = useState(passage?.snippet || passage?.matched || "");
  const [page, setPage] = useState(passage?.page || null);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(false);
  // Opened from a search result with a passage: land on the notes, where it waits to be saved.
  const [tab, setTab] = useState(passage?.snippet || passage?.matched ? "notes" : "details");
  const [coverSlot, setCoverSlot] = useState(null);
  const [titleSlot, setTitleSlot] = useState(null);
  const tabsRef = useRef(null);
  const closeRef = useRef(null);
  const readerVisible = useRef(readerOpen);
  readerVisible.current = readerOpen;
  const wasReading = useRef(false);
  useEffect(() => {
    if (wasReading.current && !readerOpen) {
      closeRef.current?.focus();
      request(`/documents/${documentId}`).then(setDocument).catch((failure) => setError(failure.message));
    }
    wasReading.current = readerOpen;
  }, [documentId, readerOpen]);
  const dirty = useRef(false);
  dirty.current = Boolean(draft || quote || detailsDirty || progressDirty || vocabularyDirty);
  // The browser's Back button asks this before it closes the page.
  useEffect(() => {
    if (!guardRef) return undefined;
    guardRef.current = () => dirty.current;
    return () => { guardRef.current = null; };
  }, [guardRef]);

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
  const work = isWorkDocument(doc);

  // Stars and the heart save as soon as they are touched. While the form has
  // unsaved changes they wait, so a save here can never overwrite them.
  async function saveMarks(changes) {
    setBusy(true); setError(""); setNotice("");
    try { const updated = await request(`/documents/${documentId}`, json("PATCH", changes)); setDocument(updated); onChanged(); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  // Jump to a tab from elsewhere on the page, and to a control inside it.
  function goTab(next, selector) {
    setTab(next);
    window.setTimeout(() => {
      const target = (selector && document.querySelector(`#book-panel-${next} ${selector}`)) || tabsRef.current;
      target?.scrollIntoView({ behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: selector ? "center" : "start" });
      if (selector && target?.focus) target.focus({ preventScroll: true });
    }, 30);
  }

  const tabs = doc ? [
    ["details", "Details"],
    ["review", work ? "Summary" : "Review"],
    ["reading", "Reading"],
    ["notes", notes.length ? `Notes · ${notes.length}` : "Notes"],
    ...(hasSource && doc.file_type === "pdf" ? [["marks", "Marks"]] : []),
    ["vocab", "Vocabulary"],
    ...(!work ? [["find", "Find & check"]] : []),
  ] : [];
  function onTabKey(event) {
    const ids = tabs.map(([id]) => id);
    const at = ids.indexOf(tab);
    let next;
    if (event.key === "ArrowRight") next = (at + 1) % ids.length;
    if (event.key === "ArrowLeft") next = (at - 1 + ids.length) % ids.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = ids.length - 1;
    if (next === undefined) return;
    event.preventDefault();
    setTab(ids[next]);
    document.getElementById(`book-tab-${ids[next]}`)?.focus();
  }

  let art = null;
  let info = null;
  if (doc) {
    const status = readingStatus(doc);
    const progress = Math.min(100, Math.max(0, Math.round(Number(doc.reading_progress) || 0)));
    const total = Number(doc.page_count || doc.pages || 0);
    const unit = hasSource && doc.file_type === "epub" ? "section" : "page";
    const here = Number(doc.current_page) || Math.round((total * progress) / 100);
    const meta = [doc.published_year, total ? `${total.toLocaleString()} ${unit}${total === 1 ? "" : "s"}` : null, hasSource ? null : "Catalogue record · no file"].filter(Boolean);
    const format = hasSource ? FORMATS[doc.file_type] || doc.file_type?.toUpperCase() || "File" : null;
    const marksLocked = busy || detailsDirty;
    const showProgress = status === "reading" || (progress > 0 && status !== "read");
    art = work ? null : <aside className="book-hero-art">
      <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={640} lazy={false} className="book-hero-cover" />
      <div ref={setCoverSlot} className="book-hero-tools" />
    </aside>;
    info = <header className="book-hero-main">
        <div className="book-hero-kicker"><span className="eyebrow">{work ? "Document" : "Book"}</span>{format && <span className="book-format">{format}</span>}</div>
        <div ref={setTitleSlot} className="book-hero-edit-slot" />
        {meta.length > 0 && <p className="book-hero-meta">{meta.join(" · ")}</p>}
        <div className="book-hero-marks">
          {!work && <>
            <span title={detailsDirty ? "Save or discard your edits first" : undefined}><StarRating size="medium" value={doc.rating || null} disabled={marksLocked} onChange={(value) => saveMarks({ rating: value })} /></span>
            <span title={detailsDirty ? "Save or discard your edits first" : undefined}><LikeButton liked={Boolean(doc.liked)} disabled={marksLocked} onChange={(value) => saveMarks({ liked: value })} /></span>
          </>}
          <button type="button" className={`book-hero-status status-${status}`} title="Change the reading status" onClick={() => goTab("details", ".book-reading-status")}>{STATUS_LABELS[status] || STATUS_LABELS.to_read}</button>
        </div>
        {showProgress && <div className="book-hero-progress">
          <span className="book-hero-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-label="Reading progress"><i style={{ width: `${progress}%` }} /></span>
          <span>{progress}%{total ? ` · ${unit} ${here.toLocaleString()} of ${total.toLocaleString()}` : ""}</span>
          <button type="button" className="text-button" onClick={() => goTab("reading")}>Update</button>
        </div>}
        <div className="book-hero-actions">
          {hasSource && <>
            <button className="button primary" onClick={() => onRead(source(passage?.page || doc.current_page || 1))}>{doc.current_page ? "Resume" : "Read"} {doc.file_type === "epub" ? "EPUB" : doc.file_type === "pdf" ? "PDF" : "source"} ↗</button>
            <button className="button" disabled={!askEnabled} title={!askEnabled ? "Enable an answering model in Settings" : undefined} onClick={() => { if (!dirty.current || window.confirm("Discard your unsaved changes?")) onAsk(source()); }}>{work ? "Ask this document" : "Ask this book"}</button>
          </>}
          {detailsDirty && <button type="button" className="book-hero-unsaved" onClick={() => goTab("details")}>Unsaved changes · review</button>}
        </div>
        {work && <div ref={setCoverSlot} className="book-hero-tools" />}
    </header>;
  }

  return createPortal(
    <section className="book-page-overlay" role="dialog" aria-modal={!readerOpen} aria-hidden={readerOpen || undefined} inert={readerOpen || undefined} aria-labelledby="book-page-title">
      <div className="book-page">
        <header className="book-page-nav">
          <button ref={closeRef} className="text-button" onClick={close}>{work ? "← Back" : "← Back to library"}</button>
          <a className="text-button" href={`#book/${documentId}`} onClick={(event) => { event.preventDefault(); copyLink(); }}>Copy book link</a>
        </header>
        {error && <p className="notice error" role="alert">{error}</p>}
        {notice && <p className="notice" role="status">{notice}</p>}
        {!doc ? <h1 id="book-page-title">{error ? "Book unavailable" : "Opening book…"}</h1> : <>
          <div className={`book-layout${work ? " no-cover" : ""}`}>
          {art}
          <div className="book-main">
          {info}
          <div ref={tabsRef} className="book-tabs" role="tablist" aria-label="About this book" onKeyDown={onTabKey}>
            {tabs.map(([id, label]) => <button key={id} type="button" role="tab" id={`book-tab-${id}`} aria-selected={tab === id} aria-controls={`book-panel-${id}`} tabIndex={tab === id ? 0 : -1} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}{id === "details" && detailsDirty ? <span className="book-tab-dot" title="Unsaved changes" /> : null}</button>)}
          </div>

          <BookDetails doc={doc} apiBase={apiBase} tab={tab} coverSlot={coverSlot} titleSlot={titleSlot} shelfNames={shelfNames} onDocument={(updated) => { setDocument(updated); onChanged(); }} onDirty={setDetailsDirty} onCoverChanged={onCoverChanged} />

          <div className="book-tab-group" role="tabpanel" id="book-panel-reading" aria-labelledby="book-tab-reading" hidden={tab !== "reading"}>
            <ReadingProgress apiBase={apiBase} doc={doc} onRead={onRead} onDirty={setProgressDirty} onDocument={(updated) => { setDocument(updated); onChanged(); }} />
          </div>

          <div className="book-tab-group book-page-notes" role="tabpanel" id="book-panel-notes" aria-labelledby="book-tab-notes" hidden={tab !== "notes"}>
            <div className="book-page-section-head"><h2>{work ? "Notes" : "Reading notebook"}</h2><span className="muted">{notes.length} saved · searchable in Notes</span></div>
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

          {hasSource && doc.file_type === "pdf" && <div className="book-tab-group" role="tabpanel" id="book-panel-marks" aria-labelledby="book-tab-marks" hidden={tab !== "marks"}>
            <PdfMarks apiBase={apiBase} doc={doc} onChanged={onChanged} />
          </div>}

          <div className="book-tab-group" role="tabpanel" id="book-panel-vocab" aria-labelledby="book-tab-vocab" hidden={tab !== "vocab"}>
            <VocabularyPanel apiBase={apiBase} documents={[doc]} documentId={doc.document_id} onOpenDocument={(_, at) => onRead(source(at || 1))} onDirty={setVocabularyDirty} />
          </div>
          </div>
          </div>
        </>}
      </div>
    </section>, document.body,
  );
}
