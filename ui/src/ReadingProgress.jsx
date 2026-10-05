import React, { useEffect, useState } from "react";

export default function ReadingProgress({ apiBase, doc, onDocument, onRead, onDirty }) {
  const total = Number(doc.pages || doc.page_count || 0);
  const [progress, setProgress] = useState(Number(doc.reading_progress || 0));
  const [page, setPage] = useState(doc.current_page || "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { setProgress(Number(doc.reading_progress || 0)); setPage(doc.current_page || ""); }, [doc.document_id, doc.reading_progress, doc.current_page]);
  useEffect(() => { onDirty?.(Number(progress) !== Number(doc.reading_progress || 0) || String(page) !== String(doc.current_page || "")); }, [doc.current_page, doc.reading_progress, onDirty, page, progress]);
  function changePage(value) {
    setPage(value);
    if (value && total) setProgress(Math.min(100, Math.round(Number(value) / total * 100)));
  }
  async function save() {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/documents/${doc.document_id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reading_progress: Number(progress), current_page: page ? Number(page) : null }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not save progress.");
      onDocument(data); setMessage("Progress saved");
    } catch (failure) { setMessage(failure.message); } finally { setBusy(false); }
  }
  return <section className="reading-progress-card">
    <div className="reading-progress-head"><div><span className="eyebrow">Reading progress</span><strong>{progress}%</strong></div>{doc.last_read_at && <span className="muted">Updated {new Date(doc.last_read_at).toLocaleDateString()}</span>}</div>
    <input className="reading-slider" type="range" min="0" max="100" value={progress} aria-label="Reading progress percentage" onChange={(e) => setProgress(Number(e.target.value))} />
    <div className="reading-progress-actions">
      {total > 0 && <label>Page <input className="input" type="number" min="1" max={total} value={page} onChange={(e) => changePage(e.target.value)} /> <span>of {total.toLocaleString()}</span></label>}
      <button className="button small" type="button" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save progress"}</button>
      {doc.record_type !== "standalone" && <button className="button primary small" type="button" onClick={() => onRead({ documentId: doc.document_id, documentName: doc.title || doc.filename, page: Number(page) || 1 })}>Resume reading ↗</button>}
      {message && <span className="muted" role="status">{message}</span>}
    </div>
  </section>;
}
