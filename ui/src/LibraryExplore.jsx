import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import AddBookDialog from "./AddBookDialog.jsx";
import Cover from "./Cover.jsx";
import LibraryRail from "./LibraryRail.jsx";
import { starText } from "./ratings.js";
import { EMPTY_FILTERS, NO_SHELF, bookHeight, facetValues, filterChips, filterLabels, filterLibrary, libraryBooks, searchWithView, shelfColumns, shelfList, shelfRows, viewFromSearch } from "./libraryExplore.js";
import { FORM_LABELS, displayTitle, loadStored, readingStatus, saveStored } from "./storage.js";

const SAVED_KEY = "ai-librarian.library.saved-views";
const STATUS_TABS = [["all", "All"], ["to_read", "To read"], ["reading", "Reading"], ["read", "Read"], ["abandoned", "Abandoned"]];
const VISIBLE_SHELVES = 8;

// One book standing on a shelf. The cover is all there is on it: a hover or a
// focus lifts the book off the shelf and names it above, and a click opens the
// book's page. Touch screens have no hover, so there the shelf shows a label
// strip under each plank instead (see Bookcase).
function Poster({ apiBase, doc, onOpen }) {
  const title = doc.title || displayTitle(doc.filename);
  const status = readingStatus(doc);
  const progress = Math.round(doc.reading_progress || 0);
  const notes = doc.note_count || 0;
  return <button type="button" className="poster" style={{ "--height": bookHeight(doc.document_id) }} onClick={() => onOpen(doc)} aria-label={doc.author ? `${title}, by ${doc.author}` : title}>
    <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={320} className="poster-cover">
      {notes > 0 && <span className="note-ribbon" role="img" aria-label={`${notes} ${notes === 1 ? "note" : "notes"}`} title={`${notes} ${notes === 1 ? "note" : "notes"}`} />}
      {status === "reading" && progress > 0 && progress < 100 && <span className="poster-progress" aria-hidden="true"><span style={{ width: `${progress}%` }} /></span>}
    </Cover>
    <span className="poster-tip" aria-hidden="true"><strong>{title}</strong>{doc.author && <span>{doc.author}</span>}</span>
  </button>;
}

// The first slot on the first shelf: log a book you have, with no file. It stands
// among the books but is not one of them (it is not counted, sorted or filtered).
const ADD_TILE = Object.freeze({ addTile: true, document_id: "add-a-book" });

function AddTile({ onAdd }) {
  return <button type="button" className="poster poster-add" style={{ "--height": 1 }} onClick={onAdd} aria-label="Add a book to your library" aria-haspopup="dialog">
    <span className="poster-add-cover">
      <svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
      <strong>Add a book</strong>
      <small>Title and author</small>
    </span>
    <span className="poster-tip" aria-hidden="true"><strong>Add a book</strong><span>No file needed</span></span>
  </button>;
}

// Covers per shelf: a cover is at least `min` px wide, with `gap` px between.
// Phones get smaller covers so a shelf still holds three.
// In a narrow column (a window of about 1000px beside the rail) the covers
// shrink a little rather than leaving two to a shelf.
const COVERS = { hover: { min: 128, gap: 18 }, compact: { min: 108, gap: 14 }, touch: { min: 84, gap: 12 } };
const COMPACT_BELOW = 520;

// The books on a bookcase, one shelf per row. The number of covers on a shelf
// follows the width of the case, so every plank is as full as it can be and
// only the last one is short. Each shelf gets its own plank; on touch screens
// a strip of titles hangs under it, like the labels on a library shelf.
function Bookcase({ apiBase, books, onOpen, onAdd }) {
  const rowsRef = useRef(null);
  const [fit, setFit] = useState({ columns: 6, gap: COVERS.hover.gap });
  useLayoutEffect(() => {
    const el = rowsRef.current;
    if (!el) return undefined;
    const measure = () => {
      const style = window.getComputedStyle(el);
      const inner = el.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
      const { min, gap } = window.matchMedia?.("(hover: none)").matches ? COVERS.touch : inner < COMPACT_BELOW ? COVERS.compact : COVERS.hover;
      setFit((current) => {
        const columns = shelfColumns(inner, min, gap);
        return current.columns === columns && current.gap === gap ? current : { columns, gap };
      });
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(el);
    return () => observer?.disconnect();
  }, []);
  const shelves = useMemo(() => shelfRows([ADD_TILE, ...books], fit.columns), [books, fit.columns]);
  return <div className="case">
    <div className="case-rows" ref={rowsRef} style={{ "--columns": fit.columns, "--gap": `${fit.gap}px` }}>
      {shelves.map((row, index) => <section className="case-shelf" key={index} aria-label={`Shelf ${index + 1} of ${shelves.length}`}>
        <div className="case-books">
          {row.map((doc) => (doc.addTile ? <AddTile key={doc.document_id} onAdd={onAdd} /> : <Poster key={doc.document_id} apiBase={apiBase} doc={doc} onOpen={onOpen} />))}
        </div>
        <div className="case-plank" aria-hidden="true" />
        <div className="case-labels" aria-hidden="true">
          {row.map((doc) => (doc.addTile ? <span className="case-label" key={doc.document_id}><strong>Add a book</strong><span>No file needed</span></span> : <span className="case-label" key={doc.document_id}><strong>{doc.title || displayTitle(doc.filename)}</strong>{doc.author && <span>{doc.author}</span>}</span>))}
        </div>
      </section>)}
    </div>
  </div>;
}

const SORTS = [["title", "Title"], ["author", "Author"], ["status", "Status"], ["rating", "Rating"], ["format", "Format"], ["pages", "Page count"], ["notes", "Notes"], ["added", "Recently added"], ["recent", "Recently read"], ["finished", "Recently finished"]];

function Icon({ children }) {
  return <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{children}</svg>;
}

export default function LibraryExplore({ apiBase, documents, collections, loaded = true, onOpenDocument, onNavigate, onChanged, preset }) {
  // The view lives in the address (?lib.status=read&lib.sort=rating…) so it can
  // be bookmarked, and survives opening a book over it and closing it again.
  const [initial] = useState(() => viewFromSearch(window.location.search));
  const [filters, setFilters] = useState(initial.filters);
  const [notes, setNotes] = useState([]);
  const [saved, setSaved] = useState(() => {
    const value = loadStored(SAVED_KEY, [], true);
    // Only well-formed views: one with no name or filters would break the list.
    return Array.isArray(value) ? value.filter((view) => view && typeof view.name === "string" && view.filters && typeof view.filters === "object") : [];
  });
  const [saveName, setSaveName] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [allShelves, setAllShelves] = useState(false);
  const [adding, setAdding] = useState(false);
  useEffect(() => { if (preset) setFilters({ ...EMPTY_FILTERS, ...preset }); }, [preset]);
  useEffect(() => {
    const next = searchWithView(window.location.search, filters);
    if (next !== window.location.search) window.history.replaceState(window.history.state, "", `${window.location.pathname}${next}${window.location.hash}`);
  }, [filters]);
  useEffect(() => () => {
    const cleared = searchWithView(window.location.search, EMPTY_FILTERS);
    if (cleared !== window.location.search) window.history.replaceState(window.history.state, "", `${window.location.pathname}${cleared}${window.location.hash}`);
  }, []);
  // What you wrote about each book, so the search box can find it, and the
  // quotes the side panel shows. Refreshed when a note is added, removed or
  // edited (their stamps change).
  const noteStamp = useMemo(() => documents.filter((doc) => doc.collection_id === "notes").map((doc) => `${doc.document_id}${doc.updated_at}`).join("|"), [documents]);
  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/notes`).then((response) => (response.ok ? response.json() : { notes: [] })).then((data) => {
      if (cancelled) return;
      setNotes(data.notes || []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [apiBase, noteStamp]);
  const noteText = useMemo(() => {
    const byBook = new Map();
    for (const note of notes) if (note.source_document_id && note.text) byBook.set(note.source_document_id, `${byBook.get(note.source_document_id) || ""} ${note.text}`);
    return byBook;
  }, [notes]);
  const facets = useMemo(() => facetValues(documents), [documents]);
  const shelves = useMemo(() => shelfList(documents), [documents]);
  const bookCount = useMemo(() => libraryBooks(documents).length, [documents]);
  const results = useMemo(() => filterLibrary(documents, filters, noteText), [documents, filters, noteText]);
  const chips = useMemo(() => filterChips(filters, collections), [filters, collections]);
  const update = (key, value) => setFilters((current) => ({ ...current, [key]: value }));
  // Starting over keeps how the books are ordered, which is not a filter.
  const clearAll = () => setFilters((current) => ({ ...EMPTY_FILTERS, sort: current.sort, order: current.order }));
  function saveView(event) {
    event.preventDefault();
    const name = saveName.trim();
    if (!name) return;
    const next = [{ id: Date.now(), name, filters }, ...saved.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase())].slice(0, 20);
    setSaved(next); saveStored(SAVED_KEY, next); setSaveName("");
  }
  function removeView(id) { const next = saved.filter((view) => view.id !== id); setSaved(next); saveStored(SAVED_KEY, next); }
  const hasFilters = chips.length > 0 || filters.status !== "all" || filters.shelf !== "all" || Boolean(filters.query);
  const shownShelves = allShelves ? shelves.shelves : shelves.shelves.slice(0, VISIBLE_SHELVES);
  // A shelf picked from elsewhere (a link, a saved view) always shows, even past the fold.
  const picked = shelves.shelves.find((shelf) => shelf.name.toLocaleLowerCase() === filters.shelf.toLocaleLowerCase());
  const visibleShelves = picked && !shownShelves.includes(picked) ? [...shownShelves, picked] : shownShelves;

  return <div className="explore-layout">
  <div className="library-explore">
    <section className="explore-toolbar" aria-label="Find books">
      <div className="toolbar-row">
        <label className="toolbar-search">
          <Icon><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></Icon>
          <span className="sr-only">Search this library</span>
          <input type="search" value={filters.query} onChange={(event) => update("query", event.target.value)} placeholder="Search titles, authors, subjects, genres…" />
        </label>
        <label className="toolbar-sort"><span className="sr-only">Sort by</span>
          <select value={filters.sort} onChange={(event) => update("sort", event.target.value)}>{SORTS.map(([value, label]) => <option value={value} key={value}>Sort: {label}</option>)}</select>
        </label>
        <button className={`toolbar-icon${filters.order === "reverse" ? " on" : ""}`} type="button" aria-pressed={filters.order === "reverse"} aria-label="Reverse the order" title="Reverse the order" onClick={() => update("order", filters.order === "reverse" ? "default" : "reverse")}>
          <Icon><path d="M8 4v16M4.5 7.5L8 4l3.5 3.5M16 20V4M12.5 16.5L16 20l3.5-3.5" /></Icon>
        </button>
        <button className={`toolbar-filters${showFilters ? " open" : ""}`} type="button" aria-expanded={showFilters} aria-controls="explore-filter-panel" onClick={() => setShowFilters((open) => !open)}>
          <Icon><path d="M4 6h16M7 12h10M10 18h4" /></Icon>
          Filters{chips.length > 0 && <span className="explore-filter-count">{chips.length}</span>}
        </button>
      </div>
      <div className="toolbar-row toolbar-status-row">
        <div className="status-tabs" role="group" aria-label="Reading status">
          {STATUS_TABS.map(([value, label]) => <button type="button" key={value} className={filters.status === value ? "active" : ""} aria-pressed={filters.status === value} onClick={() => update("status", value)}>{label}</button>)}
        </div>
        <span className="toolbar-count" role="status"><strong>{results.length.toLocaleString()}</strong> {results.length === 1 ? "book" : "books"}</span>
      </div>
      {chips.length > 0 && <div className="toolbar-chips" role="group" aria-label="Active filters">
        {chips.map((chip) => <span className="filter-chip" key={chip.key}>{chip.text}<button type="button" aria-label={`Remove filter ${chip.text}`} onClick={() => update(chip.key, EMPTY_FILTERS[chip.key])}>×</button></span>)}
        {chips.length > 1 && <button type="button" className="text-button" onClick={clearAll}>Clear all</button>}
      </div>}
      {showFilters && <div id="explore-filter-panel" className="explore-filter-panel">
      <div className="explore-facets">
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

    {shelves.shelves.length > 0 && <nav className="shelf-strip" aria-label="Shelves">
      <span className="eyebrow">Shelves</span>
      <button type="button" className={`shelf-chip${filters.shelf === "all" ? " active" : ""}`} aria-pressed={filters.shelf === "all"} onClick={() => update("shelf", "all")}>All <i>{bookCount}</i></button>
      {visibleShelves.map((shelf) => <button type="button" key={shelf.name} className={`shelf-chip${picked === shelf ? " active" : ""}`} aria-pressed={picked === shelf} onClick={() => update("shelf", picked === shelf ? "all" : shelf.name)}>{shelf.name} <i>{shelf.count}</i></button>)}
      {shelves.shelves.length > VISIBLE_SHELVES && <button type="button" className="shelf-chip shelf-more" aria-expanded={allShelves} onClick={() => setAllShelves((open) => !open)}>{allShelves ? "Fewer" : `+${shelves.shelves.length - VISIBLE_SHELVES} more`}</button>}
      {shelves.unshelved > 0 && <button type="button" className={`shelf-chip shelf-none${filters.shelf === NO_SHELF ? " active" : ""}`} aria-pressed={filters.shelf === NO_SHELF} onClick={() => update("shelf", filters.shelf === NO_SHELF ? "all" : NO_SHELF)}>No shelf <i>{shelves.unshelved}</i></button>}
    </nav>}

    {loaded && <Bookcase apiBase={apiBase} books={results} onOpen={onOpenDocument} onAdd={() => setAdding(true)} />}
    {!results.length && bookCount === 0 && loaded && <div className="quiet-prompt quiet-library" role="status">
      <svg className="quiet-book" viewBox="0 0 64 44" width="72" height="50" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M32 9c-5-3.5-14-4.5-26-3v31c12-1.5 21-.5 26 3 5-3.5 14-4.5 26-3V6c-12-1.5-21-.5-26 3zM32 9v31" /><path d="M12 15c4-.6 8-.4 14 1.2M12 22c4-.6 8-.4 14 1.2M38 16.2c6-1.6 10-1.8 14-1.2M38 23.2c6-1.6 10-1.8 14-1.2" opacity="0.55" /></svg>
      <p><strong>Your first shelf is waiting.</strong><span>Use the first slot to log a book you have, or add a file with Add books.</span></p>
    </div>}
    {!results.length && bookCount > 0 && <p className="muted empty-state" role="status">No books match {filterLabels(filters, collections).join(" · ") || "these filters"}. {hasFilters && <button type="button" className="text-button" onClick={clearAll}>Clear filters</button>}</p>}
  </div>
  {adding && <AddBookDialog apiBase={apiBase} documents={documents} onClose={() => setAdding(false)} onChanged={onChanged} onOpenBook={(book) => { setAdding(false); onOpenDocument(book); }} />}
  <LibraryRail apiBase={apiBase} documents={documents} notes={notes} status={filters.status} onStatus={(status) => setFilters((current) => ({ ...current, status }))} onNavigate={onNavigate} onOpenDocument={onOpenDocument} />
  </div>;
}
