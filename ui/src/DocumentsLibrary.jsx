import React, { useMemo, useState } from "react";

import { DOC_TYPES, EMPTY_DOC_FILTERS, docType, filterDocuments, isFiled, isWorkDocument, shelfOf, typeCounts, unfiledCount } from "./documentsLibrary.js";
import { displayTitle, readingStatus, timeAgo } from "./storage.js";

const STATUS = { to_read: "To read", reading: "Reading", read: "Read", abandoned: "Dropped" };
const FORMAT = { word: "DOCX", excel: "XLSX", powerpoint: "PPTX", markdown: "MD", text: "TXT", csv: "CSV" };

async function call(apiBase, path, options) {
  const response = await fetch(`${apiBase}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That did not work. Please try again.");
  return data;
}

// Review what Sort would do before anything is filed. Nothing on disk moves:
// filing sets a document's virtual shelf, which you can change at any time.
function SortPanel({ apiBase, onClose, onFiled }) {
  const [state, setState] = useState({ status: "loading", proposals: [], waiting: 0 });
  const [picked, setPicked] = useState({});
  const [message, setMessage] = useState("");
  React.useEffect(() => {
    let cancelled = false;
    call(apiBase, "/documents/classify", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then((data) => {
        if (cancelled) return;
        const proposals = data.proposals.filter((item) => !item.filed);
        setPicked(Object.fromEntries(proposals.map((item) => [item.document_id, true])));
        setState({ status: "ready", proposals, waiting: data.waiting });
      })
      .catch((failure) => { if (!cancelled) setState({ status: "error", proposals: [], waiting: 0, error: failure.message }); });
    return () => { cancelled = true; };
  }, [apiBase]);

  const groups = useMemo(() => {
    const byShelf = new Map();
    for (const item of state.proposals) byShelf.set(item.proposed_shelf, [...(byShelf.get(item.proposed_shelf) || []), item]);
    return [...byShelf.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [state.proposals]);
  const chosen = state.proposals.filter((item) => picked[item.document_id]);

  async function file() {
    setState((current) => ({ ...current, status: "filing" })); setMessage("");
    try {
      const data = await call(apiBase, "/documents/classify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ apply: true, document_ids: chosen.map((item) => item.document_id) }) });
      await onFiled();
      setState({ status: "done", proposals: [], waiting: state.waiting, filed: data.applied });
    } catch (failure) { setState((current) => ({ ...current, status: "ready" })); setMessage(failure.message); }
  }

  return <section className="docs-sort" aria-labelledby="docs-sort-heading">
    <div className="docs-sort-head">
      <h3 id="docs-sort-heading">Sort documents</h3>
      <button type="button" className="text-button" onClick={onClose}>{state.status === "done" ? "Done" : "Close"}</button>
    </div>
    {state.status === "loading" && <p className="muted" role="status">Reading your documents…</p>}
    {state.status === "error" && <p className="book-inline-error" role="alert">{state.error}</p>}
    {state.status === "done" && <p role="status">Filed {state.filed} document{state.filed === 1 ? "" : "s"}. Files on disk were not moved; shelves are virtual and you can change them anytime.</p>}
    {["ready", "filing"].includes(state.status) && <>
      {!state.proposals.length ? <p className="muted" role="status">Everything indexed is already filed.</p> : <>
        <p className="muted">Each document is typed from its text and placed on a shelf. Untick any you want to leave where they are.</p>
        <div className="docs-sort-groups">
          {groups.map(([shelf, items]) => <div className="docs-sort-group" key={shelf}>
            <div className="docs-sort-shelf"><strong>{shelf}</strong><span className="muted">{items.length}</span></div>
            <ul>{items.map((item) => <li key={item.document_id}>
              <label><input type="checkbox" checked={Boolean(picked[item.document_id])} onChange={(event) => setPicked((current) => ({ ...current, [item.document_id]: event.target.checked }))} />
                <span className="docs-sort-title">{item.title}</span><span className="docs-badge">{item.type_label}</span></label>
            </li>)}</ul>
          </div>)}
        </div>
        <div className="docs-sort-actions">
          <button type="button" className="button primary" disabled={!chosen.length || state.status === "filing"} onClick={file}>{state.status === "filing" ? "Filing…" : `File ${chosen.length} document${chosen.length === 1 ? "" : "s"}`}</button>
          <button type="button" className="text-button" onClick={() => setPicked(Object.fromEntries(state.proposals.map((item) => [item.document_id, chosen.length < state.proposals.length])))}>{chosen.length < state.proposals.length ? "Select all" : "Select none"}</button>
        </div>
      </>}
      {state.waiting > 0 && <p className="muted">{state.waiting} more {state.waiting === 1 ? "is" : "are"} still being indexed and will be sortable once they finish.</p>}
      {message && <p className="book-inline-error" role="alert">{message}</p>}
    </>}
  </section>;
}

export default function DocumentsLibrary({ apiBase, documents, onOpenDocument, onChanged }) {
  const [filters, setFilters] = useState(EMPTY_DOC_FILTERS);
  const [sorting, setSorting] = useState(false);
  const [error, setError] = useState("");
  const all = useMemo(() => documents.filter(isWorkDocument), [documents]);
  const rows = useMemo(() => filterDocuments(documents, filters), [documents, filters]);
  const counts = useMemo(() => typeCounts(documents, filters), [documents, filters]);
  const unfiled = useMemo(() => unfiledCount(documents), [documents]);
  const update = (key, value) => setFilters((current) => ({ ...current, [key]: value }));
  const active = Boolean(filters.query || filters.type !== "all" || filters.status !== "all" || filters.unfiled);

  async function setType(doc, value) {
    setError("");
    try {
      await call(apiBase, `/documents/${doc.document_id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ doc_type: value }) });
      await onChanged();
    } catch (failure) { setError(failure.message); }
  }

  return <div className="docs-library">
    <header className="docs-head">
      <div>
        <span className="eyebrow">Your documents</span>
        <h2>Documents</h2>
        <p className="muted">{all.length} {all.length === 1 ? "document" : "documents"}{unfiled ? ` · ${unfiled} not filed yet` : ""} · papers, reports, manuals and other working files</p>
      </div>
      <button type="button" className="button primary" disabled={!all.length} onClick={() => setSorting((open) => !open)} aria-expanded={sorting}>{sorting ? "Close sorting" : unfiled ? `Sort ${unfiled} documents` : "Sort documents"}</button>
    </header>

    {sorting && <SortPanel apiBase={apiBase} onClose={() => setSorting(false)} onFiled={async () => { await onChanged(); }} />}

    {!all.length ? <div className="docs-empty"><h3>No documents yet</h3><p className="muted">PDFs, Office files and text that are not books appear here once they are imported and indexed. Add files from the Library, and they will be typed and filed for you.</p></div> : <>
      <section className="docs-controls" aria-label="Filter documents">
        <div className="docs-search-row">
          <label className="docs-search"><span className="sr-only">Search documents</span><input className="input" type="search" value={filters.query} onChange={(event) => update("query", event.target.value)} placeholder="Search title, author, subject, shelf…" /></label>
          <label>Sort<select value={filters.sort} onChange={(event) => update("sort", event.target.value)}><option value="added">Recently added</option><option value="title">Title</option><option value="type">Type</option><option value="shelf">Shelf</option><option value="year">Year</option><option value="pages">Length</option></select></label>
          <label>Status<select value={filters.status} onChange={(event) => update("status", event.target.value)}><option value="all">All</option>{Object.entries(STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="docs-check"><input type="checkbox" checked={filters.unfiled} onChange={(event) => update("unfiled", event.target.checked)} />Not filed</label>
          <button type="button" className="text-button" disabled={!active} onClick={() => setFilters(EMPTY_DOC_FILTERS)}>Clear</button>
        </div>
        <div className="docs-chips" role="group" aria-label="Document type">
          <button type="button" className={`docs-chip${filters.type === "all" ? " active" : ""}`} aria-pressed={filters.type === "all"} onClick={() => update("type", "all")}>All <span>{counts.all || 0}</span></button>
          {DOC_TYPES.filter(([value]) => counts[value] || filters.type === value).map(([value, label]) => <button type="button" key={value} className={`docs-chip${filters.type === value ? " active" : ""}`} aria-pressed={filters.type === value} onClick={() => update("type", filters.type === value ? "all" : value)}>{label} <span>{counts[value] || 0}</span></button>)}
        </div>
      </section>
      {error && <p className="book-inline-error" role="alert">{error}</p>}
      <p className="docs-count" role="status">{rows.length} of {all.length} shown</p>
      {!rows.length ? <p className="muted docs-none">Nothing matches. <button type="button" className="text-button" onClick={() => setFilters(EMPTY_DOC_FILTERS)}>Clear filters</button></p> : <div className="docs-table-wrap">
        <table className="docs-table">
          <thead><tr><th scope="col">Document</th><th scope="col">Type</th><th scope="col">Shelf</th><th scope="col" className="num">Length</th><th scope="col" className="num">Year</th><th scope="col">Status</th><th scope="col">Added</th></tr></thead>
          <tbody>{rows.map((doc) => {
            const name = doc.title || displayTitle(doc.filename);
            const filed = isFiled(doc);
            return <tr key={doc.document_id}>
              <th scope="row"><button type="button" className="docs-open" onClick={() => onOpenDocument(doc)}><span className="docs-name">{name}</span><span className="docs-sub">{[FORMAT[doc.file_type] || doc.file_type?.toUpperCase(), doc.author].filter(Boolean).join(" · ")}</span></button></th>
              <td><select className="docs-type" value={docType(doc)} aria-label={`Type of ${name}`} onChange={(event) => setType(doc, event.target.value)}>{DOC_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td>
              <td className={filed ? "" : "docs-unfiled"} title={filed ? "Filed" : "Suggested shelf: not filed yet"}>{shelfOf(doc) || "—"}{!filed && shelfOf(doc) ? <span className="docs-suggested"> suggested</span> : null}</td>
              <td className="num">{doc.page_count || doc.pages ? `${doc.page_count || doc.pages} ${doc.file_type === "powerpoint" ? "slides" : "p."}` : "—"}</td>
              <td className="num">{doc.published_year || "—"}</td>
              <td>{STATUS[readingStatus(doc)] || "To read"}</td>
              <td className="muted">{doc.uploaded_at ? timeAgo(Date.parse(doc.uploaded_at)) : "—"}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
    </>}
  </div>;
}
