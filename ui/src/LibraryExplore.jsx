import React, { useEffect, useMemo, useState } from "react";
import Cover from "./Cover.jsx";
import { CoverFlags } from "./DocMarks.jsx";
import LibraryLedger from "./LibraryLedger.jsx";
import { EMPTY_FILTERS, facetValues, filterLabels, filterLibrary, searchWithView, viewFromSearch } from "./libraryExplore.js";
import { displayTitle, loadStored, saveStored } from "./storage.js";

const SAVED_KEY = "ai-librarian.library.saved-views";

export default function LibraryExplore({ apiBase, documents, collections, onOpenDocument, preset }) {
  // The view lives in the address (?lib.status=read&lib.view=ledger…) so it can
  // be bookmarked, and survives opening a book over it and closing it again.
  const [initial] = useState(() => viewFromSearch(window.location.search));
  const [filters, setFilters] = useState(initial.filters);
  const [layout, setLayout] = useState(initial.layout);
  const [noteText, setNoteText] = useState(() => new Map());
  const [saved, setSaved] = useState(() => {
    const value = loadStored(SAVED_KEY, [], true);
    return Array.isArray(value) ? value : [];
  });
  const [saveName, setSaveName] = useState("");
  useEffect(() => { if (preset) setFilters({ ...EMPTY_FILTERS, ...preset }); }, [preset]);
  useEffect(() => {
    const next = searchWithView(window.location.search, filters, layout);
    if (next !== window.location.search) window.history.replaceState(null, "", `${window.location.pathname}${next}${window.location.hash}`);
  }, [filters, layout]);
  useEffect(() => () => {
    const cleared = searchWithView(window.location.search, EMPTY_FILTERS, "list");
    if (cleared !== window.location.search) window.history.replaceState(null, "", `${window.location.pathname}${cleared}${window.location.hash}`);
  }, []);
  // What you wrote about each book, so the search box can find it. Refreshed
  // when a note is added, removed or edited (their stamps change).
  const noteStamp = useMemo(() => documents.filter((doc) => doc.collection_id === "notes").map((doc) => `${doc.document_id}${doc.updated_at}`).join("|"), [documents]);
  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/notes`).then((response) => (response.ok ? response.json() : { notes: [] })).then((data) => {
      if (cancelled) return;
      const byBook = new Map();
      for (const note of data.notes || []) if (note.source_document_id && note.text) byBook.set(note.source_document_id, `${byBook.get(note.source_document_id) || ""} ${note.text}`);
      setNoteText(byBook);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [apiBase, noteStamp]);
  const facets = useMemo(() => facetValues(documents), [documents]);
  const results = useMemo(() => filterLibrary(documents, filters, noteText), [documents, filters, noteText]);
  const update = (key, value) => setFilters((current) => ({ ...current, [key]: value }));
  function saveView(event) {
    event.preventDefault();
    const name = saveName.trim();
    if (!name) return;
    const next = [{ id: Date.now(), name, filters }, ...saved.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase())].slice(0, 20);
    setSaved(next); saveStored(SAVED_KEY, next); setSaveName("");
  }
  function removeView(id) { const next = saved.filter((view) => view.id !== id); setSaved(next); saveStored(SAVED_KEY, next); }
  const activeCount = Object.entries(filters).filter(([key, value]) => !["sort", "order", "query"].includes(key) && value !== "all").length + (filters.query ? 1 : 0);
  return <div className="library-explore">
    <section className="explore-controls" aria-label="Filter library">
      <div className="explore-search-row">
        <label className="explore-search"><span>Search this library</span><input className="input" value={filters.query} onChange={(event) => update("query", event.target.value)} placeholder="Title, author, or ISBN" /></label>
        <label><span>Sort</span><select value={filters.sort} onChange={(event) => update("sort", event.target.value)}><option value="title">Title</option><option value="author">Author</option><option value="status">Status</option><option value="rating">Rating</option><option value="format">Format</option><option value="pages">Page count</option><option value="notes">Notes</option><option value="added">Recently added</option><option value="recent">Recently read</option><option value="finished">Recently finished</option></select></label>
        <button className="text-button" type="button" aria-pressed={filters.order === "reverse"} onClick={() => update("order", filters.order === "reverse" ? "default" : "reverse")}>{filters.order === "reverse" ? "Reversed" : "Reverse"}</button>
        <button className="text-button" type="button" disabled={!activeCount} onClick={() => setFilters(EMPTY_FILTERS)}>Clear {activeCount || ""}</button>
      </div>
      <div className="explore-facets">
        <label>Status<select value={filters.status} onChange={(e) => update("status", e.target.value)}><option value="all">All</option><option value="to_read">To read</option><option value="reading">Reading</option><option value="read">Read</option><option value="abandoned">Abandoned</option></select></label>
        <label>Ownership<select value={filters.ownership} onChange={(e) => update("ownership", e.target.value)}><option value="all">All</option><option value="owned">Owned</option><option value="unowned">Not owned</option></select></label>
        <label>Rating<select value={filters.rating} onChange={(e) => update("rating", e.target.value)}><option value="all">All</option><option value="rated">Any rating</option>{[5,4,3,2,1].map((n) => <option key={n} value={n}>{n} stars</option>)}</select></label>
        <label>Format<select value={filters.format} onChange={(e) => update("format", e.target.value)}><option value="all">All</option>{facets.formats.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Collection<select value={filters.collection} onChange={(e) => update("collection", e.target.value)}><option value="all">All</option>{collections.filter((item) => !item.builtin).map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
        <label>Genre<select value={filters.genre} onChange={(e) => update("genre", e.target.value)}><option value="all">All</option>{facets.genres.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Author<select value={filters.author} onChange={(e) => update("author", e.target.value)}><option value="all">All</option>{facets.authors.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Acquired<select value={filters.acquisition} onChange={(e) => update("acquisition", e.target.value)}><option value="all">All</option><option value="book_store">Book store</option><option value="kindle">Kindle</option><option value="audiobook">Audiobook</option><option value="borrowed">Borrowed</option><option value="second_hand">Second hand</option><option value="gifted">Gifted</option><option value="library">Library</option><option value="other">Other</option><option value="unknown">Unknown</option></select></label>
        <label>Finished<select value={filters.finishedYear} onChange={(e) => update("finishedYear", e.target.value)}><option value="all">Any year</option>{facets.finishedYears.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Notes<select value={filters.notes} onChange={(e) => update("notes", e.target.value)}><option value="all">All</option><option value="with">Has notes</option><option value="without">No notes</option></select></label>
        <label>Index<select value={filters.indexing} onChange={(e) => update("indexing", e.target.value)}><option value="all">All</option><option value="indexed">Indexed</option><option value="queued">Queued</option><option value="indexing">Indexing</option><option value="error">Failed</option><option value="catalogued">No file</option></select></label>
      </div>
      <div className="saved-views">
        <span className="eyebrow">Saved views</span>
        {saved.map((view) => <span className="saved-view" key={view.id}><button type="button" onClick={() => setFilters({ ...EMPTY_FILTERS, ...view.filters })}>{view.name}</button><button type="button" aria-label={`Delete ${view.name}`} onClick={() => removeView(view.id)}>×</button></span>)}
        <form onSubmit={saveView}><input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="View name" aria-label="Saved view name" /><button className="text-button" disabled={!saveName.trim()}>Save current</button></form>
      </div>
    </section>
    <div className="explore-result-head"><strong>{results.length.toLocaleString()}</strong> book{results.length === 1 ? "" : "s"}<span className="muted">Open a row to edit metadata, progress, notes, and vocabulary.</span>
      <span className="layout-toggle" role="group" aria-label="Layout">{[["list", "List"], ["ledger", "Ledger"]].map(([value, label]) => <button type="button" key={value} className={layout === value ? "active" : ""} aria-pressed={layout === value} onClick={() => setLayout(value)}>{label}</button>)}</span>
    </div>
    {layout === "ledger" && results.length > 0 && <LibraryLedger rows={results} filters={filters} onOpen={onOpenDocument} onSort={(key) => setFilters((current) => (current.sort === key ? { ...current, order: current.order === "reverse" ? "default" : "reverse" } : { ...current, sort: key, order: "default" }))} />}
    {layout === "list" && <div className="explore-results">
      {results.map((doc) => <button type="button" className="explore-book" key={doc.document_id} onClick={() => onOpenDocument(doc)}>
        <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={90}><CoverFlags doc={doc} /></Cover>
        <span className="explore-book-copy"><strong>{doc.title || displayTitle(doc.filename)}</strong><span>{doc.author || "Unknown author"}</span><small>{doc.file_type?.toUpperCase() || "BOOK"} · {doc.reading_status?.replace("_", " ") || "to read"}{doc.rating ? ` · ${"★".repeat(doc.rating)}` : ""}</small></span>
        <span className="explore-progress"><span style={{ width: `${doc.reading_progress || 0}%` }} />{doc.reading_progress || 0}%</span>
      </button>)}
    </div>}
    {!results.length && <p className="muted empty-state" role="status">{activeCount ? `No books match ${filterLabels(filters, collections).join(" · ")}. ` : "No books yet. "}{activeCount > 0 && <button type="button" className="text-button" onClick={() => setFilters(EMPTY_FILTERS)}>Clear filters</button>}</p>}
  </div>;
}
