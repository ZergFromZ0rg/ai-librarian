import React, { useCallback, useEffect, useRef, useState } from "react";

const KINDS = { highlight: "Highlight", underline: "Underline", strikeout: "Struck out", text: "Text", note: "Note", drawing: "Drawing", shape: "Shape" };
const FOLDED = 6;

// What is marked in this book's PDF, read from the file itself. The app has no
// editor: the PDF opens in the browser's viewer (or any PDF app), the reader
// marks it up there, and the marks are read back and indexed so they can be
// searched and cited. A copy saved from a browser viewer is attached here and
// kept beside the original, which is never changed.
export default function PdfMarks({ apiBase, doc, onChanged }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [expanded, setExpanded] = useState(false);
  const input = useRef(null);
  const base = `${apiBase}/documents/${doc.document_id}`;

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
        <a className="button primary small" href={`${base}/file#page=${doc.current_page || 1}`} target="_blank" rel="noreferrer">Open PDF ↗</a>
        <button type="button" className="button small" disabled={busy} onClick={() => input.current.click()}>Add saved copy…</button>
        <input ref={input} hidden type="file" accept="application/pdf,.pdf" onChange={(event) => { attach(event.target.files[0]); event.target.value = ""; }} />
        <button type="button" className="text-button" disabled={busy} onClick={() => call("/annotations/rescan", { method: "POST" }, (body) => `Read again: ${count(body.annotations.length)}.`)}>Rescan</button>
        {usingCopy && <button type="button" className="text-button" disabled={busy} onClick={() => call("/annotated-copy", { method: "DELETE" }, () => "Back to the original PDF.")}>Use original</button>}
      </div>
    </div>
    <p className="pdf-marks-help">Open the PDF and mark it up with your viewer's own tools: highlight, underline, add text, draw. Marks made to the file are picked up automatically. If you saved from a browser's viewer, use <em>Add saved copy</em>; it is kept next to the original.</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {message && <p className="muted" role="status">{message}</p>}
    {data && marks.length === 0 && !error && <p className="muted">No marks yet.</p>}
    {shown.length > 0 && <ol className="pdf-marks-list">{shown.map((mark, index) => <li key={`${mark.page}-${index}`} className={`pdf-mark pdf-mark-${mark.kind}`}>
      <a className="pdf-mark-page" href={`${base}/file#page=${mark.page}`} target="_blank" rel="noreferrer" title={`Open page ${mark.page}`}>p. {mark.page}</a>
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
