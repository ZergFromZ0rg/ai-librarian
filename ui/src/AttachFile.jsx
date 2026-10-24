import React, { useRef, useState } from "react";

const TYPES = /\.(pdf|epub|docx|xlsx|pptx|txt|md|csv)$/i;

// The "File" tab of a book logged without one. In place of the reader it says
// there is no PDF, and takes the file: chosen, or dropped here. The book keeps
// everything the reader saved about it (rating, review, notes, shelf, cover);
// it simply becomes an ordinary book with a file, read and indexed in the
// background.
export default function AttachFile({ apiBase, doc, onAttached }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const input = useRef(null);

  async function upload(file) {
    if (!file || busy) return;
    if (!TYPES.test(file.name)) { setError("Choose a PDF, EPUB, Word, Excel, PowerPoint, text, Markdown or CSV file."); return; }
    setBusy(true); setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${apiBase}/documents/${doc.document_id}/file`, { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The upload did not work. Please try again.");
      onAttached(data);
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
    }
  }

  // Dropping here must not also reach the page's own "drop files to add books" handler.
  const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
  return <section className="attach-file" aria-labelledby="attach-heading">
    <div className={`attach-drop${dragging ? " dragging" : ""}`} onDragEnter={(event) => { stop(event); setDragging(true); }} onDragOver={(event) => { stop(event); setDragging(true); }} onDragLeave={(event) => { stop(event); setDragging(false); }}
      onDrop={(event) => { stop(event); setDragging(false); upload(event.dataTransfer.files?.[0]); }}>
      <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M12 17v-5M9.5 14.5L12 12l2.5 2.5" /></svg>
      <h2 id="attach-heading">No PDF yet</h2>
      <p>This book is logged in your library, but there is no file to read. Add its PDF (or EPUB) and it opens in the reader, can be marked up, and becomes searchable. Your rating, review, notes and shelf stay as they are.</p>
      <input ref={input} hidden type="file" accept=".pdf,.epub,.docx,.xlsx,.pptx,.txt,.md,.csv" onChange={(event) => { upload(event.target.files[0]); event.target.value = ""; }} />
      <button type="button" className="button primary" disabled={busy} onClick={() => input.current.click()}>{busy ? "Uploading…" : "Choose a file"}</button>
      <span className="muted">or drop it here</span>
      {error && <p className="book-inline-error" role="alert">{error}</p>}
    </div>
  </section>;
}
