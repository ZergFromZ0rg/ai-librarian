import React, { useEffect, useMemo, useState } from "react";
import Cover from "./Cover.jsx";
import { CoverFlags } from "./DocMarks.jsx";
import { EMPTY_FILTERS, facetValues, filterLibrary } from "./libraryExplore.js";
import { displayTitle, loadStored, saveStored } from "./storage.js";

const SAVED_KEY = "ai-librarian.library.saved-views";

export default function LibraryExplore({ apiBase, documents, collections, onOpenDocument, preset }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [saved, setSaved] = useState(() => {
    const value = loadStored(SAVED_KEY, [], true);
    return Array.isArray(value) ? value : [];
  });
  const [saveName, setSaveName] = useState("");
  useEffect(() => { if (preset) setFilters({ ...EMPTY_FILTERS, ...preset }); }, [preset]);
  const facets = useMemo(() => facetValues(documents), [documents]);
  const results = useMemo(() => filterLibrary(documents, filters), [documents, filters]);
  const update = (key, value) => setFilters((current) => ({ ...current, [key]: value }));
  function saveView(event) {
    event.preventDefault();
    const name = saveName.trim();
    if (!name) return;
    const next = [{ id: Date.now(), name, filters }, ...saved.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase())].slice(0, 20);
    setSaved(next); saveStored(SAVED_KEY, next); setSaveName("");
  }
  function removeView(id) { const next = saved.filter((view) => view.id !== id); setSaved(next); saveStored(SAVED_KEY, next); }
  const activeCount = Object.entries(filters).filter(([key, value]) => !["sort", "query"].includes(key) && value !== "all").length + (filters.query ? 1 : 0);
  return <div className="library-explore">
    <section className="explore-controls" aria-label="Filter library">
      <div className="explore-search-row">
        <label className="explore-search"><span>Search this library</span><input className="input" value={filters.query} onChange={(event) => update("query", event.target.value)} placeholder="Title, author, or ISBN" /></label>
        <label><span>Sort</span><select value={filters.sort} onChange={(event) => update("sort", event.target.value)}><option value="title">Title</option><option value="author">Author</option><option value="rating">Rating</option><option value="pages">Page count</option><option value="added">Recently added</option><option value="recent">Recently read</option><option value="finished">Recently finished</option></select></label>
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
        <label>Index<select value={filters.indexing} onChange={(e) => update("indexing", e.target.value)}><option value="all">All</option><option value="indexed">Indexed</option><option value="queued">Queued</option><option value="indexing">Indexing</option><option value="error">Failed</option><option value="catalogued">No file</option></select></label>
      </div>
      <div className="saved-views">
        <span className="eyebrow">Saved views</span>
        {saved.map((view) => <span className="saved-view" key={view.id}><button type="button" onClick={() => setFilters({ ...EMPTY_FILTERS, ...view.filters })}>{view.name}</button><button type="button" aria-label={`Delete ${view.name}`} onClick={() => removeView(view.id)}>×</button></span>)}
        <form onSubmit={saveView}><input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="View name" aria-label="Saved view name" /><button className="text-button" disabled={!saveName.trim()}>Save current</button></form>
      </div>
    </section>
    <div className="explore-result-head"><strong>{results.length.toLocaleString()}</strong> book{results.length === 1 ? "" : "s"}<span className="muted">Open a row to edit metadata, progress, notes, and vocabulary.</span></div>
    <div className="explore-results">
      {results.map((doc) => <button type="button" className="explore-book" key={doc.document_id} onClick={() => onOpenDocument(doc)}>
        <Cover apiBase={apiBase} documentId={doc.record_type === "standalone" ? null : doc.document_id} filename={doc.title || doc.filename} fileType={doc.file_type} width={90}><CoverFlags doc={doc} /></Cover>
        <span className="explore-book-copy"><strong>{doc.title || displayTitle(doc.filename)}</strong><span>{doc.author || "Unknown author"}</span><small>{doc.file_type?.toUpperCase() || "BOOK"} · {doc.reading_status?.replace("_", " ") || "to read"}{doc.rating ? ` · ${"★".repeat(doc.rating)}` : ""}</small></span>
        <span className="explore-progress"><span style={{ width: `${doc.reading_progress || 0}%` }} />{doc.reading_progress || 0}%</span>
      </button>)}
      {!results.length && <p className="muted empty-state">No books match these filters.</p>}
    </div>
  </div>;
}
