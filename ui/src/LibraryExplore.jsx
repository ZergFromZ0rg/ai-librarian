import React, { useEffect, useMemo, useState } from "react";
import Cover from "./Cover.jsx";
import LibraryDiary from "./LibraryDiary.jsx";
import LibraryLedger from "./LibraryLedger.jsx";
import { starText } from "./ratings.js";
import { EMPTY_FILTERS, facetValues, filterLabels, filterLibrary, searchWithView, viewFromSearch } from "./libraryExplore.js";
import { FORM_LABELS, displayTitle, loadStored, readingStatus, saveStored } from "./storage.js";

const SAVED_KEY = "ai-librarian.library.saved-views";
const STATUS_LABELS = { to_read: "To read", reading: "Reading", read: "Read", abandoned: "Abandoned" };

// One book as a cover, the way a poster wall shows films: the artwork carries
// the book, and the details wait for a hover or a focus. Touch screens have no
// hover, so there the title sits under the cover instead.
function Poster({ apiBase, doc, onOpen }) {
  const title = doc.title || displayTitle(doc.filename);
  const status = readingStatus(doc);
  const progress = Math.round(doc.reading_progress || 0);
  const notes = doc.note_count || 0;
  return <button type="button" className="poster" onClick={() => onOpen(doc)} aria-label={doc.author ? `${title}, by ${doc.author}` : title}>
    <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={320} className="poster-cover">
      {notes > 0 && <span className="note-ribbon" role="img" aria-label={`${notes} ${notes === 1 ? "note" : "notes"}`} title={`${notes} ${notes === 1 ? "note" : "notes"}`} />}
      {status === "reading" && progress > 0 && progress < 100 && <span className="poster-progress" aria-hidden="true"><span style={{ width: `${progress}%` }} /></span>}
      <span className="poster-meta" aria-hidden="true">
        <span className={`poster-status status-${status}`}>{STATUS_LABELS[status] || STATUS_LABELS.to_read}</span>
        {doc.rating ? <span className="poster-stars">{starText(doc.rating)}</span> : null}
        {doc.liked ? <span className="poster-heart">♥</span> : null}
      </span>
    </Cover>
    <span className="poster-tip" aria-hidden="true"><strong>{title}</strong>{doc.author && <span>{doc.author}</span>}</span>
    <span className="poster-caption" aria-hidden="true"><strong>{title}</strong>{doc.author && <span>{doc.author}</span>}</span>
  </button>;
}

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
  const [showFilters, setShowFilters] = useState(false);
  useEffect(() => { if (preset) setFilters({ ...EMPTY_FILTERS, ...preset }); }, [preset]);
  useEffect(() => {
    const next = searchWithView(window.location.search, filters, layout);
    if (next !== window.location.search) window.history.replaceState(null, "", `${window.location.pathname}${next}${window.location.hash}`);
  }, [filters, layout]);
  useEffect(() => () => {
    const cleared = searchWithView(window.location.search, EMPTY_FILTERS, "grid");
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
  const facetCount = Object.entries(filters).filter(([key, value]) => !["sort", "order", "query"].includes(key) && value !== "all").length;
  const activeCount = facetCount + (filters.query ? 1 : 0);
  const summary = filterLabels({ ...filters, query: "" }, collections);
  return <div className="library-explore">
    <section className="explore-controls" aria-label="Filter library">
      <div className="explore-search-row">
        <label className="explore-search"><span>Search this library</span><input className="input" value={filters.query} onChange={(event) => update("query", event.target.value)} placeholder="Title, author, or ISBN" /></label>
        <label><span>Sort</span><select value={filters.sort} onChange={(event) => update("sort", event.target.value)}><option value="title">Title</option><option value="author">Author</option><option value="status">Status</option><option value="rating">Rating</option><option value="format">Format</option><option value="pages">Page count</option><option value="notes">Notes</option><option value="added">Recently added</option><option value="recent">Recently read</option><option value="finished">Recently finished</option></select></label>
        <button className="text-button" type="button" aria-pressed={filters.order === "reverse"} onClick={() => update("order", filters.order === "reverse" ? "default" : "reverse")}>{filters.order === "reverse" ? "Reversed" : "Reverse"}</button>
        <button className="button explore-filter-toggle" type="button" aria-expanded={showFilters} aria-controls="explore-filter-panel" onClick={() => setShowFilters((open) => !open)}>
          Filters{facetCount > 0 && <span className="explore-filter-count">{facetCount}</span>}
        </button>
        {activeCount > 0 && <button className="text-button" type="button" onClick={() => setFilters(EMPTY_FILTERS)}>Clear {activeCount}</button>}
      </div>
      {!showFilters && facetCount > 0 && <p className="explore-filter-summary" role="status">{summary.join(" · ")}</p>}
      {showFilters && <div id="explore-filter-panel" className="explore-filter-panel">
      <div className="explore-facets">
        <label>Status<select value={filters.status} onChange={(e) => update("status", e.target.value)}><option value="all">All</option><option value="to_read">To read</option><option value="reading">Reading</option><option value="read">Read</option><option value="abandoned">Abandoned</option></select></label>
        <label>Ownership<select value={filters.ownership} onChange={(e) => update("ownership", e.target.value)}><option value="all">All</option><option value="owned">Owned</option><option value="unowned">Not owned</option></select></label>
        <label>Rating<select value={filters.rating} onChange={(e) => update("rating", e.target.value)}><option value="all">All</option><option value="rated">Any rating</option>{[5,4.5,4,3.5,3,2.5,2,1.5,1,0.5].map((n) => <option key={n} value={n}>{starText(n)}</option>)}</select></label>
        <label>Liked<select value={filters.liked} onChange={(e) => update("liked", e.target.value)}><option value="all">All</option><option value="liked">♥ Liked</option></select></label>
        <label>Format<select value={filters.format} onChange={(e) => update("format", e.target.value)}><option value="all">All</option>{facets.formats.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Form<select value={filters.form} onChange={(e) => update("form", e.target.value)}><option value="all">All</option>{Object.entries(FORM_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}<option value="unknown">Not classified</option></select></label>
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
      </div>}
    </section>
    <div className="explore-result-head"><strong>{results.length.toLocaleString()}</strong> book{results.length === 1 ? "" : "s"}      <span className="layout-toggle" role="group" aria-label="Layout">{[["grid", "Covers"], ["ledger", "Ledger"], ["diary", "Diary"]].map(([value, label]) => <button type="button" key={value} className={layout === value ? "active" : ""} aria-pressed={layout === value} onClick={() => setLayout(value)}>{label}</button>)}</span>
    </div>
    {layout === "ledger" && results.length > 0 && <LibraryLedger rows={results} filters={filters} onOpen={onOpenDocument} onSort={(key) => setFilters((current) => (current.sort === key ? { ...current, order: current.order === "reverse" ? "default" : "reverse" } : { ...current, sort: key, order: "default" }))} />}
    {layout === "diary" && results.length > 0 && <LibraryDiary apiBase={apiBase} rows={results} onOpen={onOpenDocument} />}
    {layout === "grid" && <div className="poster-grid">
      {results.map((doc) => <Poster key={doc.document_id} apiBase={apiBase} doc={doc} onOpen={onOpenDocument} />)}
    </div>}
    {!results.length && <p className="muted empty-state" role="status">{activeCount ? `No books match ${filterLabels(filters, collections).join(" · ")}. ` : "No books yet. "}{activeCount > 0 && <button type="button" className="text-button" onClick={() => setFilters(EMPTY_FILTERS)}>Clear filters</button>}</p>}
  </div>;
}
