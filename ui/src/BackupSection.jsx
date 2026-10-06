import React, { useRef, useState } from "react";

const COUNT_LABELS = [["books", "books"], ["notes", "notes"], ["vocabulary", "words"]];

function Summary({ report }) {
  const rows = COUNT_LABELS.map(([key, label]) => {
    const group = report[key];
    const parts = [
      group.created && `${group.created} ${report.dry_run ? "to add" : "added"}`,
      group.updated && `${group.updated} ${report.dry_run ? "to update" : "updated"}`,
      group.unchanged && `${group.unchanged} already here`,
      group.skipped && `${group.skipped} skipped`,
    ].filter(Boolean);
    return parts.length ? <li key={key}><strong>{label}:</strong> {parts.join(", ")}</li> : null;
  }).filter(Boolean);
  if (report.covers.restored) rows.push(<li key="covers"><strong>covers:</strong> {report.covers.restored} {report.dry_run ? "to restore" : "restored"}</li>);
  return <div className="backup-report" role="status">
    {(() => {
      const changes = ["books", "notes", "vocabulary"].some((key) => report[key].created || report[key].updated) || report.covers.restored > 0;
      return <>
        <strong>{!changes ? "Nothing to import" : report.dry_run ? "This file would change:" : "Import finished:"}</strong>
        {!changes && <p>Everything in this file is already in your library.</p>}
        {rows.length > 0 && <ul>{rows}</ul>}
      </>;
    })()}
    {report.books.kept_existing_values > 0 && <p>{report.books.kept_existing_values} value{report.books.kept_existing_values === 1 ? "" : "s"} you already set will be kept.</p>}
    {report.problems.length > 0 && <ul className="backup-problems">{report.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
  </div>;
}

// Download everything you created as one ZIP, or restore from one. Importing
// always shows what would change first, and never deletes anything.
export default function BackupSection({ apiBase, onImported }) {
  const [file, setFile] = useState(null);
  const [report, setReport] = useState(null);
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const picker = useRef(null);

  async function send(dryRun, chosen = file, overwrite = replace) {
    setBusy(true); setError("");
    try {
      const form = new FormData();
      form.append("file", chosen);
      const response = await fetch(`${apiBase}/import?dry_run=${dryRun}&conflict=${overwrite ? "replace" : "keep"}`, { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "This file could not be imported.");
      setReport(data);
      if (!dryRun) { setFile(null); await onImported?.(); }
    } catch (failure) { setReport(null); setError(failure.message); }
    finally { setBusy(false); }
  }
  function choose(event) {
    const chosen = event.target.files[0];
    event.target.value = "";
    if (!chosen) return;
    setFile(chosen); setReport(null); send(true, chosen);
  }
  const hasChanges = report?.dry_run && ["books", "notes", "vocabulary"].some((key) => report[key].created || report[key].updated) || (report?.dry_run && report.covers.restored > 0);

  return <section className="settings-section">
    <h3>Backup</h3>
    <p className="settings-note">
      Export your ratings, reviews, notes, vocabulary, book details and custom covers as one ZIP of
      plain JSON and Markdown. Your books themselves are not included. To restore, scan your library
      first, then import.
    </p>
    <div className="backup-actions">
      <button type="button" className="secondary" onClick={() => window.location.assign(`${apiBase}/export`)}>Export library</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => picker.current?.click()}>{busy && !report ? "Checking…" : "Import from file…"}</button>
      <input ref={picker} type="file" accept=".zip,application/zip" hidden onChange={choose} />
    </div>
    {error && <div className="status-message error" role="alert">{error}</div>}
    {report && <Summary report={report} />}
    {file && report?.dry_run && <>
      <div className="backup-choice" role="radiogroup" aria-label="When a value differs">
        <label><input type="radio" name="backup-conflict" checked={!replace} onChange={() => { setReplace(false); send(true, file, false); }} />Keep what I have</label>
        <label><input type="radio" name="backup-conflict" checked={replace} onChange={() => { setReplace(true); send(true, file, true); }} />Use the file's values</label>
      </div>
      <button type="button" className="primary" disabled={busy || !hasChanges} onClick={() => send(false)}>{busy ? "Importing…" : "Import now"}</button>
    </>}
  </section>;
}
