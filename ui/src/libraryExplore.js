import { hashSeed } from "./clothCovers.js";
import { FORM_LABELS, bookForm, displayTitle, libraryKind, readingStatus } from "./storage.js";

export const EMPTY_FILTERS = Object.freeze({
  query: "", status: "all", ownership: "all", rating: "all", format: "all",
  form: "all", collection: "all", genre: "all", author: "all", acquisition: "all", finishedYear: "all", indexing: "all", notes: "all", liked: "all",
  shelf: "all", sort: "title", order: "default",
});

export function genresFor(doc) {
  try {
    const genres = JSON.parse(doc.genres_json || "[]");
    return Array.isArray(genres) ? genres.filter((genre) => typeof genre === "string" && genre.trim()) : [];
  } catch (_error) {
    return [];
  }
}

// The first part of a shelf path is the kind of thing filed ("Books/Philosophy",
// "Papers/Physics"); a book's own shelf is what follows it.
const TOP_LEVEL_SHELVES = new Set(["Books", "Papers", "Documents", "Notes", "Reports", "Manuals", "Slides", "Data", "Writing", "Legal & Finance"]);
export const NO_SHELF = "(none)";

function shelfParts(path) {
  const parts = String(path || "").split("/").map((part) => part.trim()).filter(Boolean);
  if (TOP_LEVEL_SHELVES.has(parts[0])) parts.shift();
  return parts;
}

// The shelf a book stands on, as one name: "Books/Philosophy/Albert Camus" is
// on the Philosophy shelf. The reader's own shelf always wins. A suggested one
// only counts when it names a subject: "Books/Albert Camus" is just the author.
export function bookShelf(doc) {
  const own = shelfParts(doc.shelf);
  if (own.length) return own[0];
  const suggested = shelfParts(doc.shelf_suggested);
  if (suggested.length >= 2) return suggested[0];
  if (suggested.length === 1 && doc.subject && suggested[0].toLocaleLowerCase() === String(doc.subject).trim().toLocaleLowerCase()) return suggested[0];
  return "";
}

// The library's shelves, fullest first, and how many books stand on none.
export function shelfList(documents) {
  const counts = new Map();
  let unshelved = 0;
  for (const doc of libraryBooks(documents)) {
    const name = bookShelf(doc);
    if (!name) { unshelved += 1; continue; }
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  const shelves = [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  return { shelves, unshelved };
}

// The Library holds books. Papers and documents are tracked in the Documents
// library, notes in Notes; a PDF not classified yet waits here.
export function libraryBooks(documents) {
  return (documents || []).filter((doc) => doc.collection_id !== "notes" && ["book", "pending"].includes(libraryKind(doc)));
}

const STATUS_RANK = { reading: 0, to_read: 1, read: 2, abandoned: 3 };
const formatOf = (doc) => (doc.record_type === "standalone" ? "catalogue" : (doc.file_type || "other").toLowerCase());
const titleOf = (doc) => doc.title || displayTitle(doc.filename);

// How each sort reads a book. `descending` sorts start with the biggest/latest;
// a value of null means "not recorded", and those books always sort last, in
// either direction, so a click on a column header never buries real values.
export const SORTS = {
  title: { get: titleOf, text: true },
  author: { get: (doc) => doc.author || null, text: true },
  status: { get: (doc) => STATUS_RANK[readingStatus(doc)] ?? null },
  rating: { get: (doc) => doc.rating || null, descending: true },
  format: { get: formatOf, text: true },
  pages: { get: (doc) => doc.page_count || doc.pages || null, descending: true },
  added: { get: (doc) => doc.uploaded_at || null, text: true, descending: true },
  finished: { get: (doc) => doc.finished_at || null, text: true, descending: true },
  recent: { get: (doc) => doc.last_read_at || doc.updated_at || null, text: true, descending: true },
  notes: { get: (doc) => doc.note_count || null, descending: true },
};

// `noteText` maps a book's id to the text of the reader's own notes on it, so
// searching finds a book by what they wrote. Quoted passages are not included.
export function filterLibrary(documents, filters = EMPTY_FILTERS, noteText = new Map()) {
  const f = { ...EMPTY_FILTERS, ...filters };
  const needle = f.query.trim().toLocaleLowerCase();
  const rows = libraryBooks(documents).filter((doc) => {
    const title = titleOf(doc);
    if (needle) {
      const haystack = `${title} ${doc.author || ""} ${doc.isbn_10 || ""} ${doc.isbn_13 || ""} ${doc.description || ""} ${doc.review || ""} ${genresFor(doc).join(" ")} ${doc.subject || ""} ${bookShelf(doc)} ${noteText.get(doc.document_id) || ""}`;
      if (!haystack.toLocaleLowerCase().includes(needle)) return false;
    }
    if (f.status !== "all" && readingStatus(doc) !== f.status) return false;
    if (f.ownership !== "all" && (f.ownership === "owned") !== Boolean(doc.owned)) return false;
    if (f.rating === "rated" && !doc.rating) return false;
    if (!["all", "rated"].includes(f.rating) && Number(doc.rating || 0) !== Number(f.rating)) return false;
    if (f.liked === "liked" && !doc.liked) return false;
    if (f.format !== "all" && formatOf(doc) !== f.format) return false;
    if (f.form !== "all" && (bookForm(doc) || "unknown") !== f.form) return false;
    if (f.collection !== "all" && (doc.collection_id || "legacy-root") !== f.collection) return false;
    if (f.genre !== "all" && !genresFor(doc).some((genre) => genre.toLocaleLowerCase() === f.genre.toLocaleLowerCase())) return false;
    if (f.author !== "all" && (doc.author || "Unknown author") !== f.author) return false;
    if (f.shelf !== "all") {
      const shelf = bookShelf(doc);
      if (f.shelf === NO_SHELF ? shelf : shelf.toLocaleLowerCase() !== f.shelf.toLocaleLowerCase()) return false;
    }
    if (f.acquisition !== "all" && (doc.acquisition_source || "unknown") !== f.acquisition) return false;
    if (f.finishedYear !== "all" && !(doc.finished_at || "").startsWith(f.finishedYear)) return false;
    if (f.indexing !== "all" && doc.indexing_status !== f.indexing) return false;
    if (f.notes === "with" && !(doc.note_count > 0)) return false;
    if (f.notes === "without" && doc.note_count > 0) return false;
    return true;
  });
  const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
  const sort = SORTS[f.sort] || SORTS.title;
  // Text columns run A-Z first; number and date columns biggest/latest first.
  const direction = (sort.descending ? -1 : 1) * (f.order === "reverse" ? -1 : 1);
  return rows.sort((a, b) => {
    const left = sort.get(a);
    const right = sort.get(b);
    const missing = (left == null) - (right == null);
    if (missing) return missing;
    let order = 0;
    if (left != null) order = sort.text ? collator.compare(String(left), String(right)) : left - right;
    return order * direction || collator.compare(titleOf(a), titleOf(b));
  });
}

// Every active constraint, in words, with the filter it belongs to: the empty
// result says why it is empty, and each one becomes a chip that can be removed.
function describeFilters(filters, collections = []) {
  const f = { ...EMPTY_FILTERS, ...filters };
  const label = (name, value) => `${name}: ${value}`;
  const out = [];
  const add = (key, text) => out.push({ key, text });
  if (f.query.trim()) add("query", `“${f.query.trim()}”`);
  if (f.status !== "all") add("status", label("Status", { to_read: "To read", reading: "Reading", read: "Read", abandoned: "Abandoned" }[f.status] || f.status));
  if (f.shelf !== "all") add("shelf", label("Shelf", f.shelf === NO_SHELF ? "none" : f.shelf));
  if (f.ownership !== "all") add("ownership", f.ownership === "owned" ? "Owned" : "Not owned");
  if (f.rating !== "all") add("rating", f.rating === "rated" ? "Rated" : `${f.rating} ${Number(f.rating) === 1 ? "star" : "stars"}`);
  if (f.liked === "liked") add("liked", "Liked");
  if (f.format !== "all") add("format", label("Format", f.format));
  if (f.form !== "all") add("form", label("Form", FORM_LABELS[f.form] || "Not classified"));
  if (f.collection !== "all") add("collection", label("Collection", collections.find((item) => item.id === f.collection)?.name || f.collection));
  if (f.genre !== "all") add("genre", label("Genre", f.genre));
  if (f.author !== "all") add("author", label("Author", f.author));
  if (f.acquisition !== "all") add("acquisition", label("Acquired", f.acquisition.replace("_", " ")));
  if (f.finishedYear !== "all") add("finishedYear", label("Finished", f.finishedYear));
  if (f.indexing !== "all") add("indexing", label("Index", f.indexing));
  if (f.notes !== "all") add("notes", f.notes === "with" ? "Has notes" : "No notes");
  return out;
}

export function filterLabels(filters, collections = []) {
  return describeFilters(filters, collections).map((item) => item.text);
}

// The removable chips under the toolbar. The search text, the reading status
// and the shelf are not chips: each already has a control that shows it.
const SHOWN_ELSEWHERE = new Set(["query", "status", "shelf"]);
export function filterChips(filters, collections = []) {
  return describeFilters(filters, collections).filter((item) => !SHOWN_ELSEWHERE.has(item.key));
}

// The shareable form of a view: filters and layout as query parameters, only
// where they differ from the defaults. The "lib." prefix keeps them apart from
// anything else on the address.
const PARAMS = {
  query: "q", status: "status", ownership: "own", rating: "rating", format: "format", form: "form", collection: "coll",
  genre: "genre", author: "author", acquisition: "acq", finishedYear: "year", indexing: "index", notes: "notes", liked: "liked",
  shelf: "shelf", sort: "sort", order: "order",
};
const PREFIX = "lib.";

export function viewToParams(filters) {
  const f = { ...EMPTY_FILTERS, ...filters };
  const params = new URLSearchParams();
  for (const [key, name] of Object.entries(PARAMS)) if (f[key] !== EMPTY_FILTERS[key]) params.set(PREFIX + name, f[key]);
  return params;
}

export function viewFromSearch(search) {
  const source = new URLSearchParams(search);
  const filters = { ...EMPTY_FILTERS };
  for (const [key, name] of Object.entries(PARAMS)) {
    const value = source.get(PREFIX + name);
    if (value == null || value.length > 200) continue;
    if (key === "sort" && !SORTS[value]) continue;
    if (key === "order" && !["default", "reverse"].includes(value)) continue;
    // A status the tabs do not offer would leave no tab lit and hide every book.
    if (key === "status" && !["all", "to_read", "reading", "read", "abandoned"].includes(value)) continue;
    filters[key] = value;
  }
  return { filters };
}

// `search` with this view's parameters replaced and everything else kept.
export function searchWithView(search, filters) {
  const merged = new URLSearchParams(search);
  for (const key of [...merged.keys()]) if (key.startsWith(PREFIX)) merged.delete(key);
  for (const [key, value] of viewToParams(filters)) merged.set(key, value);
  const text = merged.toString();
  return text ? `?${text}` : "";
}

export function facetValues(documents) {
  const books = libraryBooks(documents);
  return {
    genres: [...new Set(books.flatMap(genresFor))].sort(),
    authors: [...new Set(books.map((doc) => doc.author || "Unknown author"))].sort(),
    finishedYears: [...new Set(books.map((doc) => (doc.finished_at || "").slice(0, 4)).filter(Boolean))].sort().reverse(),
    formats: [...new Set(books.map((doc) => doc.record_type === "standalone" ? "catalogue" : (doc.file_type || "other").toLowerCase()))].sort(),
  };
}

// How many covers stand on one shelf: as many as fit at `min` px each with
// `gap` between them, and never fewer than one.
export function shelfColumns(width, min, gap) {
  if (!(width > 0)) return 1;
  return Math.max(1, Math.floor((width + gap) / (min + gap)));
}

// The books cut into shelves of `columns` each; the last shelf may be short.
export function shelfRows(items, columns) {
  const size = Math.max(1, Math.floor(columns) || 1);
  const rows = [];
  for (let start = 0; start < items.length; start += size) rows.push(items.slice(start, start + size));
  return rows;
}

// Real shelves hold books of different heights. This gives every book a fixed
// height between 92% and 100% of the tallest, from its id alone, so a book
// keeps its height when the list is filtered or sorted.
export function bookHeight(seed) {
  return 0.92 + (hashSeed(seed) % 9) / 100;
}
