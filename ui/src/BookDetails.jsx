import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import AiCheck from "./AiCheck.jsx";
import { applyBody, matchRows } from "./catalogueMatch.js";
import { DOC_TYPES, TYPE_LABEL, isWorkDocument } from "./documentsLibrary.js";
import { bookShelf } from "./libraryExplore.js";
import LookupPanel from "./LookupPanel.jsx";
import { FORM_LABELS, displayTitle } from "./storage.js";

const today = () => {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};
function genres(doc) {
  if (Array.isArray(doc.genres)) return doc.genres;
  try { return JSON.parse(doc.genres_json || "[]"); } catch { return []; }
}
function values(doc) {
  return {
    title: doc.title || displayTitle(doc.filename), subtitle: doc.subtitle || "", author: doc.author || "",
    genres: genres(doc).join(", "), shelf: bookShelf(doc),
    kind: doc.kind_override || "auto", form: doc.book_form_override || "auto", doc_type: doc.doc_type_override || "auto", owned: doc.owned == null ? null : Boolean(doc.owned),
    acquisition_source: doc.acquisition_source || "", reading_status: doc.reading_status || (doc.read_at ? "read" : "to_read"),
    started_at: (doc.started_at || "").slice(0, 10), finished_at: (doc.finished_at || doc.read_at || "").slice(0, 10),
    publisher: doc.publisher || "", published_year: doc.published_year == null ? "" : String(doc.published_year),
    page_count: doc.page_count == null ? "" : String(doc.page_count), language: doc.language || "",
    isbn_10: doc.isbn_10 || "", isbn_13: doc.isbn_13 || "", rating: doc.rating || null, review: doc.review || "",
    liked: Boolean(doc.liked), review_spoiler: Boolean(doc.review_spoiler), reread: Boolean(doc.reread),
  };
}

function apiValue(key, value) {
  if (key === "genres") return value.split(",").map((item) => item.trim()).filter(Boolean);
  if (["published_year", "page_count"].includes(key)) return value === "" ? null : Number(value);
  // A shelf is typed as a plain name; the library files it under Books. Empty goes back to the suggested shelf.
  if (key === "shelf") return value.trim() ? `Books/${value.trim()}` : "";
  return value;
}

// Text you edit where you read it: a text box with no box, so it looks like the
// heading or the author line it sits in. It wraps and grows with its content
// (title and author are single-line values; Enter just leaves the field).
function HeroText({ className, value, onChange, label, placeholder, maxLength, disabled }) {
  const ref = useRef(null);
  const fit = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };
  useLayoutEffect(fit, [value]);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => { if (el.clientWidth !== width) { width = el.clientWidth; fit(); } });
    observer.observe(el);
    document.fonts?.ready.then(fit).catch(() => {});
    return () => observer.disconnect();
  }, []);
  return <textarea ref={ref} rows={1} className={`book-hero-edit ${className}`} value={value} maxLength={maxLength} disabled={disabled}
    aria-label={label} placeholder={placeholder} title={`Click to edit the ${label.toLowerCase()}`} spellCheck={label === "Title"}
    onChange={(event) => onChange(event.target.value.replace(/\s*\n+\s*/g, " "))}
    onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } }} />;
}

// `tab` says which group of the form is showing (details, review, or find); all of
// it stays mounted so edits survive a change of tab. The cover tools and file card
// are drawn into `coverSlot`, under the big cover on the page.
export default function BookDetails({ doc, apiBase, onDocument, onDirty, onCoverChanged, tab = "details", coverSlot = null, titleSlot = null, shelfNames = [] }) {
  const [fields, setFields] = useState(() => values(doc));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState("");
  const [coverMessage, setCoverMessage] = useState("");
  const [dragging, setDragging] = useState(false);
  const [lookupOpen, setLookupOpen] = useState(false);
  const [match, setMatch] = useState(null);
  const [ticked, setTicked] = useState({});
  const [withCover, setWithCover] = useState(false);
  const [applying, setApplying] = useState(false);
  const input = useRef(null);
  const changed = JSON.stringify(fields) !== JSON.stringify(values(doc));
  useEffect(() => { onDirty(changed || Boolean(file)); }, [changed, file, onDirty]);
  // A new version of the book (after a save, or a rating from the page above)
  // resets the form in the same render. Doing it in an effect left one render
  // where the form looked edited, which flashed "unsaved changes".
  const [seenDoc, setSeenDoc] = useState(doc);
  if (seenDoc !== doc) { setSeenDoc(doc); setFields(values(doc)); }
  useEffect(() => {
    if (!file) { setPreview(""); return; }
    const url = URL.createObjectURL(file); setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  async function request(path, options) {
    const response = await fetch(`${apiBase}/documents/${doc.document_id}${path}`, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save. Please try again.");
    return data;
  }
  const set = (key, value) => { setFields((current) => ({ ...current, [key]: value })); setMessage(""); };
  const setReadingStatus = (status) => {
    setFields((current) => {
      const next = { ...current, reading_status: status };
      if (status === "to_read") { next.started_at = ""; next.finished_at = ""; }
      if (status === "reading") { next.started_at ||= today(); next.finished_at = ""; }
      if (status === "read") next.finished_at ||= today();
      if (status === "abandoned") next.finished_at = "";
      return next;
    });
    setMessage("");
  };
  async function save(event) {
    event.preventDefault(); setSaving(true); setError(""); setMessage("");
    try {
      const saved = values(doc);
      const changes = Object.fromEntries(
        Object.entries(fields)
          .filter(([key, value]) => value !== saved[key])
          .map(([key, value]) => [key, apiValue(key, value)]),
      );
      const updated = await request("", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
      setFields(values(updated)); onDocument(updated); setMessage("Book details and review saved.");
    } catch (failure) { setError(failure.message); }
    finally { setSaving(false); }
  }
  const matchRowsNow = match ? matchRows(doc, match) : [];
  function pickMatch(candidate) {
    setMatch(candidate); setTicked({}); setError(""); setMessage("");
    setWithCover(Boolean(candidate.cover_id) && doc.record_type === "standalone");
  }
  async function applyMatch() {
    setApplying(true); setError(""); setMessage("");
    try {
      const updated = await request("/metadata-apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(applyBody(matchRowsNow, ticked, match, withCover)) });
      setFields(values(updated)); onDocument(updated);
      if (withCover) onCoverChanged();
      setMatch(null); setLookupOpen(false); setMessage("Details from Open Library applied.");
    } catch (failure) { setError(failure.message); }
    finally { setApplying(false); }
  }
  function choose(next) {
    if (!next || coverBusy) return;
    setCoverError(""); setCoverMessage("");
    if (!["image/jpeg", "image/png", "image/webp"].includes(next.type)) { setCoverError("Choose a JPEG, PNG, or WebP image."); return; }
    if (next.size > 8 * 1024 * 1024) { setCoverError("Choose an image smaller than 8 MB."); return; }
    setFile(next);
  }
  async function saveCover(reset = false) {
    setCoverBusy(true); setCoverError(""); setCoverMessage("");
    try {
      const form = new FormData();
      if (!reset) form.append("file", file);
      await request("/cover", { method: reset ? "DELETE" : "PUT", ...(!reset ? { body: form } : {}) });
      setFile(null); onCoverChanged();
      setCoverMessage(reset ? (doc.record_type === "standalone" ? "Title cover restored." : "Original cover restored.") : "Cover updated.");
    } catch (failure) { setCoverError(failure.message); }
    finally { setCoverBusy(false); }
  }
  const format = ({ word: "DOCX", excel: "XLSX", powerpoint: "PPTX", markdown: "Markdown", text: "TXT" })[doc.file_type] || doc.file_type?.toUpperCase() || "File";
  const hasSource = doc.record_type !== "standalone";
  // Papers, reports and other working files: no cover, ratings or catalogue lookup.
  const work = isWorkDocument(doc);
  const inForm = ["details", "review", "find"].includes(tab);
  const coverTools = <div className={`book-cover-tools${dragging ? " dragging" : ""}`} tabIndex={work ? undefined : 0} aria-label={work ? "File" : "Cover and file. Paste or drop an image here to replace the cover."}
    onDragOver={work ? undefined : (event) => { event.preventDefault(); event.stopPropagation(); setDragging(true); }}
    onDragLeave={work ? undefined : () => setDragging(false)}
    onDrop={work ? undefined : (event) => { event.preventDefault(); event.stopPropagation(); setDragging(false); choose(event.dataTransfer.files[0]); }}
    onPaste={work ? undefined : (event) => { const pasted = [...event.clipboardData.items].find((item) => item.type.startsWith("image/")); if (pasted) { event.preventDefault(); choose(pasted.getAsFile()); } }}>
    {!work && <>
      {file && <div className="book-cover-preview"><img src={preview} alt="New cover preview" onError={() => setCoverError("This image could not be previewed. Please choose another.")} /><span className="cover-preview-label">PREVIEW · NOT SAVED</span></div>}
      <input ref={input} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => { choose(event.target.files[0]); event.target.value = ""; }} />
      <div className="book-cover-actions">
        <button type="button" className="button small" disabled={coverBusy} onClick={() => input.current.click()}>Change cover</button>
        {file
          ? <><button type="button" className="button primary small" disabled={coverBusy} onClick={() => saveCover()}>{coverBusy ? "Saving…" : "Use this cover"}</button><button type="button" className="text-button" disabled={coverBusy} onClick={() => setFile(null)}>Cancel</button></>
          : <button type="button" className="text-button" disabled={coverBusy} onClick={() => saveCover(true)}>{hasSource ? "Restore original cover" : "Use title cover"}</button>}
      </div>
      <p className="book-cover-help">Or drop or paste an image here · JPG, PNG, WebP · up to 8 MB</p>
      {coverError && <p className="book-inline-error" role="alert">{coverError}</p>}
      {coverMessage && <p className="book-inline-success" role="status">{coverMessage}</p>}
    </>}
    {hasSource ? <div className="book-file-card"><span className="eyebrow">In your library</span><div><span className={`book-format format-${doc.file_type}`}>{format}</span><span>{doc.pages || 0} {doc.file_type === "epub" ? "sections" : doc.pages === 1 ? "page" : "pages"}</span></div><p title={doc.filename}>{doc.filename}</p><a href={`${apiBase}/documents/${doc.document_id}/file`} target="_blank" rel="noreferrer">Open {format} ↗</a></div> : <div className="book-file-card book-record-card"><span className="eyebrow">Catalogue record</span><p>This book has no digital file. Its metadata, cover, reading state, review, and notes still live in your library.</p></div>}
  </div>;
  const heroText = <>
    <h1 id="book-page-title"><HeroText className="book-hero-title" label="Title" placeholder="Untitled" maxLength={300} value={fields.title} disabled={saving} onChange={(value) => set("title", value)} /></h1>
    <HeroText className="book-hero-author" label="Author" placeholder="Add an author" maxLength={240} value={fields.author} disabled={saving} onChange={(value) => set("author", value)} />
  </>;
  return <>
    {coverSlot && createPortal(coverTools, coverSlot)}
    {titleSlot && createPortal(heroText, titleSlot)}
    <form className="book-editor" onSubmit={save} hidden={!inForm}>
      <fieldset disabled={saving} className="book-editor-fields">
        <div className="book-tab-group" role="tabpanel" id="book-panel-find" aria-labelledby="book-tab-find" hidden={tab !== "find"}>
        {!work && <section className={`book-editor-card book-lookup-card${lookupOpen ? " open" : ""}`} aria-labelledby="lookup-heading">
          <div className="book-card-heading"><h2 id="lookup-heading">Find book information</h2><button type="button" className="text-button" title="Fill in the author, year, pages, ISBN and a cover from Open Library. You choose what to keep." aria-expanded={lookupOpen} onClick={() => { setLookupOpen((open) => !open); setMatch(null); }}>{lookupOpen ? "Close" : "Look up"}</button></div>
          {!lookupOpen && <p className="muted">Fill in the author, year, pages, ISBN and a cover from Open Library. You choose what to keep.</p>}
          {lookupOpen && changed && <p className="book-inline-error" role="alert">Save or discard your unsaved edits first, so applying a match cannot overwrite them.</p>}
          {lookupOpen && !match && <LookupPanel apiBase={apiBase} documentId={doc.document_id} initialQuery={[fields.title, fields.author].filter(Boolean).join(" ")} onPick={pickMatch} pickLabel="Compare" />}
          {lookupOpen && match && <div className="lookup-compare">
            <p className="lookup-compare-title"><strong>{match.title}</strong>{match.author ? ` · ${match.author}` : ""} <button type="button" className="text-button" onClick={() => setMatch(null)}>Back to results</button></p>
            {matchRowsNow.length === 0 && !match.cover_id ? <p className="muted">This match has nothing new to add.</p> : <table className="lookup-table">
              <caption className="sr-only">Choose which values to apply</caption>
              <thead><tr><th scope="col">Apply</th><th scope="col">Field</th><th scope="col">Yours</th><th scope="col">Open Library</th></tr></thead>
              <tbody>{matchRowsNow.map((row) => <tr key={row.key}>
                <td><input type="checkbox" aria-label={`Apply ${row.label}`} checked={ticked[row.key] ?? row.checked} onChange={(event) => setTicked((current) => ({ ...current, [row.key]: event.target.checked }))} /></td>
                <th scope="row">{row.label}</th><td className={row.current ? "" : "muted"}>{row.current || "empty"}</td><td>{row.suggested}</td>
              </tr>)}
              {match.cover_id && <tr><td><input type="checkbox" aria-label="Apply cover" checked={withCover} onChange={(event) => setWithCover(event.target.checked)} /></td><th scope="row">Cover</th><td className="muted">your current cover</td><td><img className="lookup-compare-cover" src={`${apiBase}/catalogue/cover?id=${match.cover_id}`} alt="Catalogue cover" /></td></tr>}
              </tbody>
            </table>}
            <div className="cover-preview-actions"><button type="button" className="button primary small" disabled={applying || changed} onClick={applyMatch}>{applying ? "Applying…" : "Apply selected"}</button><button type="button" className="text-button" onClick={() => setMatch(null)}>Cancel</button></div>
          </div>}
        </section>}
        {!work && hasSource && ["pdf", "epub"].includes(doc.file_type) && <AiCheck apiBase={apiBase} doc={doc} locked={changed} onDocument={onDocument} />}
        </div>
        <div className="book-tab-group" role="tabpanel" id="book-panel-details" aria-labelledby="book-tab-details" hidden={tab !== "details"}>
        <section className="book-editor-card" aria-labelledby="metadata-heading">
          <div className="book-card-heading"><h2 id="metadata-heading">{work ? "Document details" : "Book details"}</h2><span className="muted">{work ? "Keep it findable" : "Make it yours"}</span></div>
          <div className={work ? "book-field-pair" : "book-field-trio"}><label className="book-field">Subtitle<input className="input" maxLength={300} placeholder="Optional subtitle" value={fields.subtitle} onChange={(event) => set("subtitle", event.target.value)} /></label><label className="book-field" title="Separate multiple genres with commas">Genres<input className="input" maxLength={1000} placeholder="Philosophy, Essays" value={fields.genres} onChange={(event) => set("genres", event.target.value)} /></label>{!work && <label className="book-field" title="Where it stands in the Library. Type a new name to start a shelf.">Shelf<input className="input" list="book-shelf-names" maxLength={80} placeholder="Add to a shelf" value={fields.shelf} onChange={(event) => set("shelf", event.target.value)} /><datalist id="book-shelf-names">{shelfNames.map((name) => <option key={name} value={name} />)}</datalist></label>}</div>
          <div className={work ? "book-field-pair" : "book-field-trio"}><label className="book-field">Library type<select className="input" value={fields.kind} onChange={(event) => set("kind", event.target.value)}><option value="auto">Automatic · {doc.kind || "document"}</option><option value="book">Book</option><option value="paper">Paper</option><option value="document">Document</option></select></label>{!work && <label className="book-field">Form<select className="input" value={fields.form} onChange={(event) => set("form", event.target.value)}><option value="auto">Automatic · {FORM_LABELS[doc.book_form] || "not classified"}</option>{Object.entries(FORM_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}{work ? <label className="book-field">Document type<select className="input" value={fields.doc_type} onChange={(event) => set("doc_type", event.target.value)}><option value="auto">Automatic · {TYPE_LABEL[doc.doc_type] || "Other"}</option>{DOC_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label> : <label className="book-field">Source<select className="input" value={fields.acquisition_source} onChange={(event) => set("acquisition_source", event.target.value)}><option value="">Not specified</option><option value="book_store">Book store</option><option value="kindle">Kindle</option><option value="audiobook">Audiobook</option><option value="borrowed">Borrowed</option><option value="second_hand">Second hand</option><option value="gifted">Gifted</option><option value="library">Library</option><option value="other">Other</option></select></label>}</div>
          <div className="book-status-row">
            {!work && <fieldset><legend>Ownership</legend><div className="book-segments">{[[true, "Owned"], [false, "Not owned"]].map(([value, label]) => <label className={fields.owned === value ? "selected" : ""} key={label}><input type="radio" name="book-owned" checked={fields.owned === value} onChange={() => set("owned", value)} />{label}</label>)}</div></fieldset>}
            <fieldset className="book-reading-status"><legend>Reading status</legend><div className="book-segments">{[["to_read", "To read"], ["reading", "Reading"], ["read", "Read"], ["abandoned", "Abandoned"]].map(([value, label]) => <label className={fields.reading_status === value ? "selected" : ""} key={value}><input type="radio" name="book-reading-status" checked={fields.reading_status === value} onChange={() => setReadingStatus(value)} />{label}</label>)}</div></fieldset>
            {fields.reading_status !== "to_read" && <label className="book-field book-date-field">Started<input className="input" type="date" value={fields.started_at} max={fields.finished_at || undefined} onChange={(event) => set("started_at", event.target.value)} /></label>}
            {fields.reading_status === "read" && <label className="book-field book-date-field">Finished<input className="input" type="date" value={fields.finished_at} min={fields.started_at || undefined} onChange={(event) => set("finished_at", event.target.value)} /></label>}
            {fields.reading_status === "read" && <label className="book-check" title="Shown as a re-read in your diary"><input type="checkbox" checked={fields.reread} onChange={(event) => set("reread", event.target.checked)} />Re-read</label>}
          </div>
          <details className="book-publication">
            <summary>Publication details</summary>
            <div className="book-publication-fields">
              <div className="book-field-pair"><label className="book-field">Publisher<input className="input" maxLength={300} value={fields.publisher} onChange={(event) => set("publisher", event.target.value)} /></label><label className="book-field">Language<input className="input" maxLength={50} placeholder="en" value={fields.language} onChange={(event) => set("language", event.target.value)} /></label></div>
              <div className="book-field-pair"><label className="book-field">Publication year<input className="input" type="number" min="1000" max="2100" value={fields.published_year} onChange={(event) => set("published_year", event.target.value)} /></label><label className="book-field">Page count<input className="input" type="number" min="1" max="1000000" value={fields.page_count} onChange={(event) => set("page_count", event.target.value)} /></label></div>
              <div className="book-field-pair"><label className="book-field">ISBN-10<input className="input" inputMode="text" maxLength={24} value={fields.isbn_10} onChange={(event) => set("isbn_10", event.target.value)} /></label><label className="book-field">ISBN-13<input className="input" inputMode="numeric" maxLength={24} value={fields.isbn_13} onChange={(event) => set("isbn_13", event.target.value)} /></label></div>
              {doc.metadata_source && <p className="book-metadata-source">Metadata from {doc.metadata_source}{doc.metadata_source_id ? ` · ${doc.metadata_source_id}` : ""}. Your edits remain authoritative.</p>}
            </div>
          </details>
        </section>
        </div>
        <div className="book-tab-group" role="tabpanel" id="book-panel-review" aria-labelledby="book-tab-review" hidden={tab !== "review"}>
        <section className="book-editor-card book-review-card" aria-labelledby="review-heading">
          <div className="book-card-heading"><h2 id="review-heading">{work ? "Summary & takeaways" : "Your review"}</h2><span className="muted">{work ? "For your own reference" : "Just for you · rate it at the top of the page"}</span></div>
          <label className="book-field">{work ? "Key points" : "What did you think?"}<textarea className="input" rows={work ? 6 : 4} maxLength={20000} value={fields.review} onChange={(event) => set("review", event.target.value)} placeholder={work ? "What is this about, and what matters in it? What did you conclude or need to act on?" : "What stayed with you? Who would you recommend it to?"} /></label>
          {!work && <label className="book-check"><input type="checkbox" checked={fields.review_spoiler} onChange={(event) => set("review_spoiler", event.target.checked)} />This review contains spoilers <small>(hidden until you choose to show it)</small></label>}
          <p className="book-review-hint">{work ? "A short summary you can find again. Keep quoted passages and detailed thoughts in the notes below." : "Your overall impression lives here. Keep passages and detailed thoughts in the notes below."}</p>
        </section>
        </div>
      </fieldset>
      <div className="book-editor-footer">
        <span role="status">{saving ? "Saving your changes…" : message || (changed ? "You have unsaved changes" : "All changes saved")}</span>
        <div><button type="button" className="button" disabled={!changed || saving} onClick={() => { setFields(values(doc)); setError(""); setMessage(""); }}>Discard</button><button className="button primary" disabled={!changed || saving || !fields.title.trim()}>{saving ? "Saving…" : "Save changes"}</button></div>
      </div>
      {error && <p className="book-inline-error" role="alert">{error}</p>}
    </form>
  </>;
}
