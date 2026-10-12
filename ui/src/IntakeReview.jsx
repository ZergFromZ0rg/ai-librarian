import React, { useEffect, useRef, useState } from "react";

import { StagedAiCheck } from "./AiCheck.jsx";
import Cover from "./Cover.jsx";
import {
  SUPPORTED_UPLOADS,
  clearDraft,
  editedFields,
  fieldTag,
  fileFacts,
  formFromItem,
  groupTypes,
  loadDraft,
  payloadFromForm,
  saveAllSummary,
  saveDraft,
  shelfAfterTypeChange,
  validateForm,
} from "./intake.js";

function ItemCover({ apiBase, item, width }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [item.id]);
  if (item.has_cover && !failed) {
    return <img className="intake-cover" src={`${apiBase}/intake/${item.id}/cover?w=${width}`} alt="" draggable="false" onError={() => setFailed(true)} />;
  }
  return <Cover apiBase={apiBase} documentId={null} filename={item.filename} fileType={item.format} width={width} />;
}

// A form field whose label also says where its value was found.
function Field({ label, tag, className = "", children }) {
  return (
    <label className={`book-field ${className}`}>
      <span className="intake-label">
        {label}
        {tag && <small className={`intake-tag${tag === "edited" ? " edited" : ""}`}>{tag}</small>}
      </span>
      {children}
    </label>
  );
}

// The item next to this one, to show after it is saved or discarded.
function neighbour(items, id) {
  const index = items.findIndex((item) => item.id === id);
  return (items[index + 1] || items[index - 1])?.id || null;
}

// The "Add to library" screen: uploads that have been read and classified, each with
// a form to check and correct before it is saved into the library.
export default function IntakeReview({ apiBase, intake, startId, uploading, onFiles, onLibraryChanged, onClose }) {
  const { items, options } = intake;
  const [selectedId, setSelectedId] = useState(startId || items[0]?.id || null);
  const [forms, setForms] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [checks, setChecks] = useState({}); // each upload's last "Check with AI" result, kept while this screen is open
  const saved = useRef(0);
  const frame = useRef(null);
  const picker = useRef(null);

  useEffect(() => { intake.loadOptions(); frame.current?.focus(); }, []);

  // Each upload's form starts from what the reader typed earlier (kept in this browser),
  // else from what was found.
  useEffect(() => {
    setForms((current) => {
      let next = current;
      for (const item of items) {
        if (!next[item.id]) next = { ...next, [item.id]: loadDraft(item.id) || formFromItem(item) };
      }
      return next;
    });
    if (!items.some((item) => item.id === selectedId)) setSelectedId(items[0]?.id || null);
  }, [items]);

  const item = items.find((entry) => entry.id === selectedId) || null;
  const form = item ? forms[item.id] || formFromItem(item) : null;
  const types = options.types.length ? options.types : item ? [{ value: form.type, label: form.type, group: "Type" }] : [];
  const problem = form ? validateForm(form) : "";

  function close() {
    onClose(saved.current ? saveAllSummary({ saved: saved.current, failed: [] }) : "");
  }

  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape" && !busy) close();
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && item && !busy) {
        event.preventDefault();
        handleSave();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Store a changed form (and keep it as a draft while it differs from what was found).
  function commit(next) {
    setForms((current) => ({ ...current, [item.id]: next }));
    if (editedFields(next, item).length) saveDraft(item.id, next);
    else clearDraft(item.id);
    setError("");
    setMessage("");
  }

  function change(key, value) {
    const next = { ...form, [key]: value };
    if (key === "type") next.shelf = shelfAfterTypeChange(form.shelf, types, form.type, value);
    commit(next);
  }

  async function handleSave(event) {
    event?.preventDefault();
    if (!item || busy) return;
    if (problem) { setError(problem); return; }
    setBusy("save");
    setError("");
    setMessage("");
    const next = neighbour(items, item.id);
    try {
      const doc = await intake.save(item.id, payloadFromForm(form));
      saved.current += 1;
      setMessage(`“${doc.title}” is in your library and queued for indexing.`);
      if (next) setSelectedId(next);
      else onClose(saveAllSummary({ saved: saved.current, failed: [] }));
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }

  // Files that could not be read are left for the reader: saving one only makes a
  // library entry that fails to index.
  const readable = items.filter((entry) => entry.state !== "unreadable");

  async function handleSaveAll() {
    if (!readable.length || busy) return;
    const left = items.length - readable.length;
    const question = `Add ${left ? `${readable.length} of the ${items.length}` : `all ${readable.length}`} uploads to the library exactly as they are shown here?`
      + (left ? ` The ${left} that could not be read ${left === 1 ? "stays" : "stay"} here for you to decide.` : "");
    if (!window.confirm(question)) return;
    setBusy("all");
    setError("");
    setMessage("");
    const failed = [];
    let added = 0;
    for (const entry of readable) {
      const values = forms[entry.id] || formFromItem(entry);
      try {
        const invalid = validateForm(values);
        if (invalid) throw new Error(invalid);
        await intake.save(entry.id, payloadFromForm(values), { refreshLibrary: false });
        added += 1;
        saved.current += 1;
      } catch (failure) {
        failed.push({ title: values.title || entry.filename, message: failure.message });
      }
    }
    onLibraryChanged?.();
    setBusy("");
    if (failed.length) setError(saveAllSummary({ saved: added, failed }));
    else if (left) setMessage(`Added ${added} to the library. ${left} that could not be read ${left === 1 ? "stays" : "stay"} here for you to decide.`);
    else onClose(saveAllSummary({ saved: saved.current, failed: [] }));
  }

  async function handleDiscard() {
    if (!item || busy) return;
    if (!window.confirm(`Discard “${form.title || item.filename}”? The uploaded file is deleted and nothing is added to the library.`)) return;
    setBusy("discard");
    setError("");
    const next = neighbour(items, item.id);
    try {
      await intake.discard(item.id);
      setSelectedId(next);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }

  function addFiles(fileList) {
    const files = Array.from(fileList || []).filter((file) => SUPPORTED_UPLOADS.test(file.name));
    if (files.length && !uploading) onFiles(files);
  }

  const edited = item ? editedFields(form, item) : [];
  const tag = (key) => fieldTag(item, form, key);
  const notes = item ? [item.format_note, ...(item.notes || [])].filter(Boolean) : [];

  return (
    <section
      ref={frame}
      tabIndex={-1}
      className="intake-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="intake-title"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }}
    >
      <div className="intake">
        <header className="intake-head">
          <div>
            <h1 id="intake-title">Add to library</h1>
            <p>
              Check what was found in each file and correct anything that is wrong. Nothing joins your library until you save it.
              Saving starts indexing: reading the text, splitting it into searchable passages, and finding any highlights or
              markup already in a PDF.
            </p>
          </div>
          <div className="intake-head-actions">
            <input ref={picker} type="file" multiple hidden accept=".pdf,.epub,.docx,.xlsx,.pptx,.txt,.md,.csv" onChange={(event) => { addFiles(event.target.files); event.target.value = ""; }} />
            <button type="button" className="button" disabled={uploading || Boolean(busy)} onClick={() => picker.current?.click()}>
              {uploading ? "Reading…" : "Add more files"}
            </button>
            <button type="button" className="button" disabled={busy === "all"} onClick={close}>Close</button>
          </div>
        </header>

        {!item ? (
          <div className="intake-empty">
            <strong>{uploading ? "Reading your files…" : "Nothing is waiting for review"}</strong>
            <p>Drop files anywhere on this page, or choose them. PDF, EPUB, Word, Excel, PowerPoint, text, Markdown and CSV are supported.</p>
            {error && <div className="notice error" role="alert">{error}</div>}
          </div>
        ) : (
          <div className="intake-body">
            <nav className="intake-list" aria-label="Uploads waiting for review">
              <div className="intake-list-head">
                <span>{items.length} waiting</span>
                {readable.length > 1 && (
                  <button type="button" className="text-button" disabled={Boolean(busy)} onClick={handleSaveAll}>
                    {busy === "all" ? "Saving…" : `Save all (${readable.length})`}
                  </button>
                )}
              </div>
              <div className="intake-rows">
              {items.map((entry) => {
                const values = forms[entry.id] || formFromItem(entry);
                const touched = editedFields(values, entry).length > 0;
                return (
                  <button
                    key={entry.id}
                    type="button"
                    className={`intake-row${entry.id === item.id ? " active" : ""}`}
                    aria-current={entry.id === item.id ? "true" : undefined}
                    onClick={() => { setSelectedId(entry.id); setError(""); setMessage(""); }}
                  >
                    <span className="intake-row-cover"><ItemCover apiBase={apiBase} item={entry} width={120} /></span>
                    <span className="intake-row-text">
                      <strong>{values.title || entry.filename}</strong>
                      <span>{values.author || "No author found"} · {entry.format_label}</span>
                    </span>
                    {entry.state === "unreadable" && <span className="intake-flag warn" title="This file could not be read">!</span>}
                    {touched && <span className="intake-flag" title="You have edited this one">edited</span>}
                  </button>
                );
              })}
              </div>
            </nav>

            <div className="intake-detail">
              <div className="intake-file">
                <div className="intake-file-cover"><ItemCover apiBase={apiBase} item={item} width={320} /></div>
                <div className="intake-file-card">
                  <span className="eyebrow">The file</span>
                  <p title={item.filename}>{item.filename}</p>
                  <div>{fileFacts(item).map((fact) => <span key={fact} className="intake-fact">{fact}</span>)}</div>
                </div>
              </div>

              <form className="intake-form book-editor" onSubmit={handleSave}>
                {notes.map((note) => <div key={note} className={`notice ${item.state === "unreadable" ? "error" : "warn"}`}>{note}</div>)}
                <p className="intake-legend">
                  The small grey note beside a label says where its value was found. No note means nothing was found, so check it.
                </p>
                <fieldset className="book-editor-fields" disabled={busy === "save" || busy === "discard" || busy === "all"}>
                  <section className="book-editor-card" aria-labelledby="intake-what">
                    <div className="book-card-heading"><h2 id="intake-what">What is it, and where does it go?</h2></div>
                    <div className="book-field-pair">
                      <Field label="Type" tag={tag("type")}>
                        <select className="input" value={form.type} onChange={(event) => change("type", event.target.value)}>
                          {groupTypes(types).map((group) => (
                            <optgroup key={group.group} label={group.group}>
                              {group.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                            </optgroup>
                          ))}
                        </select>
                      </Field>
                      <Field label="Folder" tag={tag("shelf")}>
                        <input className="input" list="intake-shelves" maxLength={500} value={form.shelf} placeholder="Books/Philosophy" onChange={(event) => change("shelf", event.target.value)} />
                        <datalist id="intake-shelves">
                          {[...new Set([item.proposal?.shelf, ...options.shelves].filter(Boolean))].map((shelf) => <option key={shelf} value={shelf} />)}
                        </datalist>
                      </Field>
                    </div>
                    {item.proposal?.shelf && form.shelf !== item.proposal.shelf && (
                      <p className="intake-suggest">
                        Suggested folder: {item.proposal.shelf}{" "}
                        <button type="button" className="text-button" onClick={() => change("shelf", item.proposal.shelf)}>Use it</button>
                      </p>
                    )}
                  </section>

                  <section className="book-editor-card" aria-labelledby="intake-details">
                    <div className="book-card-heading"><h2 id="intake-details">Details</h2></div>
                    <div className="book-field-pair book-title-pair">
                      <Field label="Title" tag={tag("title")} className="book-title-field">
                        <input className="input" required maxLength={300} value={form.title} onChange={(event) => change("title", event.target.value)} />
                      </Field>
                      <Field label="Subtitle" tag={tag("subtitle")}>
                        <input className="input" maxLength={300} value={form.subtitle} onChange={(event) => change("subtitle", event.target.value)} />
                      </Field>
                    </div>
                    <div className="book-field-trio">
                      <Field label="Author" tag={tag("author")}>
                        <input className="input" maxLength={240} placeholder="Not found" value={form.author} onChange={(event) => change("author", event.target.value)} />
                      </Field>
                      <Field label="Year" tag={tag("published_year")}>
                        <input className="input" type="number" min="1000" max="2100" placeholder="Not found" value={form.published_year} onChange={(event) => change("published_year", event.target.value)} />
                      </Field>
                      <Field label="Subject" tag={tag("subject")}>
                        <input className="input" maxLength={240} placeholder="e.g. Philosophy" value={form.subject} onChange={(event) => change("subject", event.target.value)} />
                      </Field>
                    </div>
                    <div className="book-field-trio">
                      <Field label="Publisher" tag={tag("publisher")}>
                        <input className="input" maxLength={300} value={form.publisher} onChange={(event) => change("publisher", event.target.value)} />
                      </Field>
                      <Field label="Language" tag={tag("language")}>
                        <input className="input" maxLength={50} placeholder="en" value={form.language} onChange={(event) => change("language", event.target.value)} />
                      </Field>
                      <Field label="Genres" tag={tag("genres")}>
                        <input className="input" maxLength={1000} placeholder="Comma separated" value={form.genres} onChange={(event) => change("genres", event.target.value)} />
                      </Field>
                    </div>
                    <div className="book-field-trio">
                      <Field label="ISBN-13" tag={tag("isbn_13")}>
                        <input className="input" inputMode="numeric" maxLength={24} value={form.isbn_13} onChange={(event) => change("isbn_13", event.target.value)} />
                      </Field>
                      <Field label="ISBN-10" tag={tag("isbn_10")}>
                        <input className="input" maxLength={24} value={form.isbn_10} onChange={(event) => change("isbn_10", event.target.value)} />
                      </Field>
                      <Field label="Page count" tag={tag("page_count")}>
                        <input className="input" type="number" min="1" placeholder="Optional" value={form.page_count} onChange={(event) => change("page_count", event.target.value)} />
                      </Field>
                    </div>
                    <Field label="Description" tag={tag("description")}>
                      <textarea className="input book-description" rows={2} maxLength={20000} value={form.description} onChange={(event) => change("description", event.target.value)} />
                    </Field>
                  </section>

                  <StagedAiCheck
                    key={item.id}
                    apiBase={apiBase}
                    item={item}
                    form={form}
                    types={types}
                    result={checks[item.id] || null}
                    onResult={(result) => setChecks((current) => ({ ...current, [item.id]: result }))}
                    onForm={commit}
                  />
                </fieldset>

                <div className="book-editor-footer intake-footer">
                  <span role="status">
                    {busy === "save" ? "Adding to your library…" : message || (edited.length ? `${edited.length} ${edited.length === 1 ? "field" : "fields"} edited` : "Looks right? Save it to add it to your library.")}
                  </span>
                  <div>
                    <button type="button" className="button danger" disabled={Boolean(busy)} onClick={handleDiscard}>Discard</button>
                    <button className="button primary" disabled={Boolean(busy) || Boolean(problem)} title={problem || "Add to the library (Ctrl or ⌘ + Enter)"}>
                      {busy === "save" ? "Saving…" : "Save to library"}
                    </button>
                  </div>
                </div>
                {error && <p className="book-inline-error" role="alert">{error}</p>}
              </form>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
