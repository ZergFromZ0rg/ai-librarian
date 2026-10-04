import React, { useEffect, useRef, useState } from "react";
import { displayTitle } from "./storage.js";

const labels = ["Not for me", "It was okay", "I liked it", "Really good", "A favourite"];
function values(doc) {
  return { title: doc.title || displayTitle(doc.filename), author: doc.author || "", subject: doc.subject || "", kind: doc.kind_override || "auto", owned: doc.owned == null ? null : Boolean(doc.owned), read: Boolean(doc.read_at), rating: doc.rating || null, review: doc.review || "" };
}

export function StarRating({ value, onChange, disabled }) {
  const [hover, setHover] = useState(null);
  return <div className="book-rating">
    <div className="book-stars" role="radiogroup" aria-label="Your rating" onMouseLeave={() => setHover(null)}>
      {[1, 2, 3, 4, 5].map((star) => <button key={star} type="button" role="radio" aria-checked={value === star} aria-label={`${star} ${star === 1 ? "star" : "stars"}`} tabIndex={value === star || (!value && star === 1) ? 0 : -1} disabled={disabled}
        className={(hover ?? value ?? 0) >= star ? "filled" : ""}
        onMouseEnter={() => setHover(star)} onFocus={() => setHover(null)} onClick={() => onChange(star)}
        onKeyDown={(event) => {
          let next;
          if (["ArrowRight", "ArrowDown"].includes(event.key)) next = star === 5 ? 1 : star + 1;
          if (["ArrowLeft", "ArrowUp"].includes(event.key)) next = star === 1 ? 5 : star - 1;
          if (event.key === "Home") next = 1;
          if (event.key === "End") next = 5;
          if (next) { event.preventDefault(); onChange(next); event.currentTarget.parentElement.children[next - 1].focus(); }
        }}><span aria-hidden="true">★</span></button>)}
    </div>
    <div className="book-rating-caption"><span>{value ? `${value}/5 · ${labels[value - 1]}` : "Not rated yet"}</span>{value && <button type="button" className="text-button" disabled={disabled} onClick={() => onChange(null)}>Clear rating</button>}</div>
  </div>;
}

export default function BookDetails({ doc, apiBase, onDocument, onDirty, onCoverChanged }) {
  const [fields, setFields] = useState(() => values(doc));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [revision, setRevision] = useState(0);
  const [coverFailed, setCoverFailed] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState("");
  const [coverMessage, setCoverMessage] = useState("");
  const [dragging, setDragging] = useState(false);
  const input = useRef(null);
  const changed = JSON.stringify(fields) !== JSON.stringify(values(doc));
  useEffect(() => { onDirty(changed || Boolean(file)); }, [changed, file, onDirty]);
  useEffect(() => { setFields(values(doc)); }, [doc]);
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
  async function save(event) {
    event.preventDefault(); setSaving(true); setError(""); setMessage("");
    try {
      const saved = values(doc);
      const changes = Object.fromEntries(Object.entries(fields).filter(([key, value]) => value !== saved[key]));
      const updated = await request("", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
      setFields(values(updated)); onDocument(updated); setMessage("Book details and review saved.");
    } catch (failure) { setError(failure.message); }
    finally { setSaving(false); }
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
      setFile(null); setRevision(Date.now()); setCoverFailed(false); onCoverChanged();
      setCoverMessage(reset ? "Original cover restored." : "Cover updated.");
    } catch (failure) { setCoverError(failure.message); }
    finally { setCoverBusy(false); }
  }
  const format = ({ word: "DOCX", excel: "XLSX", powerpoint: "PPTX", markdown: "Markdown", text: "TXT" })[doc.file_type] || doc.file_type?.toUpperCase() || "File";
  return <div className="book-management">
    <aside className="book-cover-panel" aria-label="Book cover and file">
      <div className={`book-cover-drop${dragging ? " dragging" : ""}`} tabIndex={0} aria-label="Cover preview. Paste or drop an image to replace it."
        onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); event.stopPropagation(); setDragging(false); choose(event.dataTransfer.files[0]); }}
        onPaste={(event) => { const pasted = [...event.clipboardData.items].find((item) => item.type.startsWith("image/")); if (pasted) { event.preventDefault(); choose(pasted.getAsFile()); } }}>
        {preview || !coverFailed ? <img key={preview || revision} src={preview || `${apiBase}/documents/${doc.document_id}/thumbnail?w=480&v=${revision}`} alt={file ? "New cover preview" : `Cover of ${doc.title}`} onError={() => { if (!file) setCoverFailed(true); else setCoverError("This image could not be previewed. Please choose another."); }} /> : <div className="book-cloth-cover"><span>YOUR LIBRARY</span><strong>{doc.title || displayTitle(doc.filename)}</strong><small>{doc.author || ""}</small></div>}
        {file && <span className="cover-preview-label">PREVIEW · NOT SAVED</span>}
      </div>
      <input ref={input} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => { choose(event.target.files[0]); event.target.value = ""; }} />
      <button type="button" className="button" disabled={coverBusy} onClick={() => input.current.click()}>Change cover</button>
      <p className="book-cover-help">Drop an image above or choose a file.<br />JPG, PNG, WebP · up to 8 MB</p>
      {file ? <div className="cover-preview-actions"><button type="button" className="button primary small" disabled={coverBusy} onClick={() => saveCover()}>{coverBusy ? "Saving…" : "Use this cover"}</button><button type="button" className="text-button" disabled={coverBusy} onClick={() => setFile(null)}>Cancel</button></div> : <button type="button" className="text-button" disabled={coverBusy} onClick={() => saveCover(true)}>Restore original cover</button>}
      {coverError && <p className="book-inline-error" role="alert">{coverError}</p>}
      {coverMessage && <p className="book-inline-success" role="status">{coverMessage}</p>}
      <div className="book-file-card"><span className="eyebrow">In your library</span><div><span className={`book-format format-${doc.file_type}`}>{format}</span><span>{doc.pages || 0} {doc.file_type === "epub" ? "sections" : doc.pages === 1 ? "page" : "pages"}</span></div><p title={doc.filename}>{doc.filename}</p><a href={`${apiBase}/documents/${doc.document_id}/file`} target="_blank" rel="noreferrer">Open {format} ↗</a></div>
    </aside>
    <form className="book-editor" onSubmit={save}>
      <fieldset disabled={saving} className="book-editor-fields">
        <section className="book-editor-card" aria-labelledby="metadata-heading">
          <div className="book-card-heading"><h2 id="metadata-heading">Book details</h2><span className="muted">Make it yours</span></div>
          <label className="book-field book-title-field">Title<input className="input" required maxLength={300} value={fields.title} onChange={(event) => set("title", event.target.value)} /></label>
          <div className="book-field-pair"><label className="book-field">Author<input className="input" maxLength={240} placeholder="Add an author" value={fields.author} onChange={(event) => set("author", event.target.value)} /></label><label className="book-field">Subject or genre<input className="input" maxLength={240} placeholder="e.g. Philosophy" value={fields.subject} onChange={(event) => set("subject", event.target.value)} /></label></div>
          <label className="book-field">Library type<select className="input" value={fields.kind} onChange={(event) => set("kind", event.target.value)}><option value="auto">Automatic · {doc.kind || "document"}</option><option value="book">Book</option><option value="paper">Paper</option><option value="document">Document</option></select></label>
          <div className="book-field-pair book-status-fields">
            <fieldset><legend>Ownership</legend><div className="book-segments">{[[true, "Owned"], [false, "Not owned"]].map(([value, label]) => <label className={fields.owned === value ? "selected" : ""} key={label}><input type="radio" name="book-owned" checked={fields.owned === value} onChange={() => set("owned", value)} />{label}</label>)}</div>{fields.owned == null && <small className="muted">Not specified</small>}</fieldset>
            <fieldset><legend>Reading</legend><div className="book-segments">{[[false, "Unread"], [true, "Read"]].map(([value, label]) => <label className={fields.read === value ? "selected" : ""} key={label}><input type="radio" name="book-read" checked={fields.read === value} onChange={() => set("read", value)} />{label}</label>)}</div></fieldset>
          </div>
        </section>
        <section className="book-editor-card book-review-card" aria-labelledby="review-heading">
          <div className="book-card-heading"><h2 id="review-heading">Your review</h2><span className="muted">Just for you</span></div>
          <StarRating value={fields.rating} onChange={(value) => set("rating", value)} disabled={saving} />
          <label className="book-field">What did you think?<textarea className="input" rows={4} maxLength={20000} value={fields.review} onChange={(event) => set("review", event.target.value)} placeholder="What stayed with you? Who would you recommend it to?" /></label>
          <p className="book-review-hint">Your overall impression lives here. Keep passages and detailed thoughts in the notes below.</p>
        </section>
      </fieldset>
      <div className="book-editor-footer">
        <span role="status">{saving ? "Saving your changes…" : message || (changed ? "You have unsaved changes" : "All changes saved")}</span>
        <div><button type="button" className="button" disabled={!changed || saving} onClick={() => { setFields(values(doc)); setError(""); setMessage(""); }}>Discard</button><button className="button primary" disabled={!changed || saving || !fields.title.trim()}>{saving ? "Saving…" : "Save changes"}</button></div>
      </div>
      {error && <p className="book-inline-error" role="alert">{error}</p>}
    </form>
  </div>;
}
