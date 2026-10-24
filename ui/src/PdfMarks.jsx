import React, { useCallback, useEffect, useRef, useState } from "react";

const KINDS = { highlight: "Highlight", underline: "Underline", strikeout: "Struck out", text: "Text", note: "Note", drawing: "Drawing", shape: "Shape" };
const FOLDED = 6;

// What is marked in this book's PDF, read from the file itself. The PDF opens in
// the in-app reader, which saves every mark to the library, and the marks are
// read back and indexed so they can be searched and cited. A copy marked up
// somewhere else can still be attached here; it is kept beside the original,
// which is never changed.
export default function PdfMarks({ apiBase, doc, onChanged, onRead }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [expanded, setExpanded] = useState(false);
  const input = useRef(null);
  const base = `${apiBase}/documents/${doc.document_id}`;
  const read = (page) => onRead?.({ documentId: doc.document_id, documentName: doc.title || doc.filename, page: page || 1 });

  const call = useCallback(async (path, options, success) => {
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${base}${path}`, options);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "That did not work.");
      setData(body);
      if (success) { setMessage(success(body)); onChanged?.(); }
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }, [base, onChanged]);

  useEffect(() => { call("/annotations"); }, [doc.document_id, doc.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  const marks = data?.annotations || [];
  const usingCopy = data?.source === "annotated-copy";
  const shown = expanded ? marks : marks.slice(0, FOLDED);
  const count = (n) => `${n} ${n === 1 ? "mark" : "marks"}`;
  function attach(file) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    call("/annotated-copy", { method: "POST", body: form }, (body) => `Annotated copy attached. ${count(body.annotations.length)} read from it.`);
  }
  return <section className="pdf-marks-card" aria-labelledby="pdf-marks-heading">
    <div className="pdf-marks-head">
      <div>
        <span className="eyebrow" id="pdf-marks-heading">Marks in your PDF</span>
        <strong>{data ? count(marks.length) : "Reading…"}</strong>
        {data && <span className="muted">{usingCopy ? "from your annotated copy" : "from the original PDF"}</span>}
      </div>
      <div className="pdf-marks-actions">
        <button type="button" className="button primary small" onClick={() => read(doc.current_page)}>Read PDF</button>
        <button type="button" className="button small" disabled={busy} onClick={() => input.current.click()}>Add saved copy…</button>
        <input ref={input} hidden type="file" accept="application/pdf,.pdf" onChange={(event) => { attach(event.target.files[0]); event.target.value = ""; }} />
        <button type="button" className="text-button" disabled={busy} onClick={() => call("/annotations/rescan", { method: "POST" }, (body) => `Read again: ${count(body.annotations.length)}.`)}>Rescan</button>
        {usingCopy && <button type="button" className="text-button" disabled={busy} onClick={() => call("/annotated-copy", { method: "DELETE" }, () => "Back to the original PDF. Your marked-up copy is kept: Restore earlier version brings it back.")}>Use original</button>}
        {data?.has_earlier && <button type="button" className="text-button" disabled={busy} title="Swap in the version your marks had before your latest sitting. Press it again to swap back." onClick={() => call("/annotated-copy/restore-earlier", { method: "POST" }, () => "Restored the earlier version. Press Restore earlier version again to swap back.")}>Restore earlier version</button>}
      </div>
    </div>
    <p className="pdf-marks-help">Read the PDF here and highlight, add text or draw: every mark is saved to your library as you make it and indexed, so it can be searched and cited. Marked up a copy somewhere else, in another app or a browser's own viewer? Use <em>Add saved copy</em>; it is kept next to the original, which is never changed.</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {message && <p className="muted" role="status">{message}</p>}
    {data && marks.length === 0 && !error && <p className="muted">No marks yet.</p>}
    {shown.length > 0 && <ol className="pdf-marks-list">{shown.map((mark, index) => <li key={`${mark.page}-${index}`} className={`pdf-mark pdf-mark-${mark.kind}`}>
      <button type="button" className="pdf-mark-page" onClick={() => read(mark.page)} title={`Read page ${mark.page}`}>p. {mark.page}</button>
      <div className="pdf-mark-body">
        <span className="pdf-mark-kind">{KINDS[mark.kind] || mark.kind}{mark.author ? ` · ${mark.author}` : ""}</span>
        {mark.text && <blockquote>{mark.text}</blockquote>}
        {mark.comment && <p className="pdf-mark-comment">{mark.comment}</p>}
        {!mark.text && !mark.comment && <p className="muted">No text. It is a mark with nothing to read.</p>}
      </div>
    </li>)}</ol>}
    {marks.length > FOLDED && <button type="button" className="text-button" onClick={() => setExpanded((open) => !open)}>{expanded ? "Show fewer" : `Show all ${marks.length}`}</button>}
  </section>;
}
