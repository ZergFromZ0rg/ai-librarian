import { displayTitle, readingStatus } from "./storage.js";

export const EMPTY_FILTERS = Object.freeze({
  query: "", status: "all", ownership: "all", rating: "all", format: "all",
  collection: "all", genre: "all", author: "all", acquisition: "all", finishedYear: "all", indexing: "all", notes: "all",
  sort: "title", order: "default",
});

export function genresFor(doc) {
  try {
    const genres = JSON.parse(doc.genres_json || "[]");
    return Array.isArray(genres) ? genres.filter(Boolean) : [];
  } catch (_error) {
    return [];
  }
}

export function libraryBooks(documents) {
  return (documents || []).filter((doc) => doc.collection_id !== "notes" && (doc.kind_override || doc.kind) !== "note");
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
      const haystack = `${title} ${doc.author || ""} ${doc.isbn_10 || ""} ${doc.isbn_13 || ""} ${doc.description || ""} ${doc.review || ""} ${genresFor(doc).join(" ")} ${noteText.get(doc.document_id) || ""}`;
      if (!haystack.toLocaleLowerCase().includes(needle)) return false;
    }
    if (f.status !== "all" && readingStatus(doc) !== f.status) return false;
    if (f.ownership !== "all" && (f.ownership === "owned") !== Boolean(doc.owned)) return false;
    if (f.rating === "rated" && !doc.rating) return false;
    if (!["all", "rated"].includes(f.rating) && Number(doc.rating || 0) !== Number(f.rating)) return false;
    if (f.format !== "all" && formatOf(doc) !== f.format) return false;
    if (f.collection !== "all" && (doc.collection_id || "legacy-root") !== f.collection) return false;
    if (f.genre !== "all" && !genresFor(doc).some((genre) => genre.toLocaleLowerCase() === f.genre.toLocaleLowerCase())) return false;
    if (f.author !== "all" && (doc.author || "Unknown author") !== f.author) return false;
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

// Active constraints in words, for an empty result that says why it is empty.
export function filterLabels(filters, collections = []) {
  const f = { ...EMPTY_FILTERS, ...filters };
  const label = (name, value) => `${name}: ${value}`;
  const out = [];
  if (f.query.trim()) out.push(`“${f.query.trim()}”`);
  if (f.status !== "all") out.push(label("Status", { to_read: "To read", reading: "Reading", read: "Read", abandoned: "Abandoned" }[f.status] || f.status));
  if (f.ownership !== "all") out.push(f.ownership === "owned" ? "Owned" : "Not owned");
  if (f.rating !== "all") out.push(f.rating === "rated" ? "Rated" : `${f.rating} stars`);
  if (f.format !== "all") out.push(label("Format", f.format));
  if (f.collection !== "all") out.push(label("Collection", collections.find((item) => item.id === f.collection)?.name || f.collection));
  if (f.genre !== "all") out.push(label("Genre", f.genre));
  if (f.author !== "all") out.push(label("Author", f.author));
  if (f.acquisition !== "all") out.push(label("Acquired", f.acquisition.replace("_", " ")));
  if (f.finishedYear !== "all") out.push(label("Finished", f.finishedYear));
  if (f.indexing !== "all") out.push(label("Index", f.indexing));
  if (f.notes !== "all") out.push(f.notes === "with" ? "Has notes" : "No notes");
  return out;
}

// The shareable form of a view: filters and layout as query parameters, only
// where they differ from the defaults. The "lib." prefix keeps them apart from
// anything else on the address.
const PARAMS = {
  query: "q", status: "status", ownership: "own", rating: "rating", format: "format", collection: "coll",
  genre: "genre", author: "author", acquisition: "acq", finishedYear: "year", indexing: "index", notes: "notes",
  sort: "sort", order: "order",
};
export const LAYOUTS = ["list", "ledger"];
const PREFIX = "lib.";

export function viewToParams(filters, layout = "list") {
  const f = { ...EMPTY_FILTERS, ...filters };
  const params = new URLSearchParams();
  for (const [key, name] of Object.entries(PARAMS)) if (f[key] !== EMPTY_FILTERS[key]) params.set(PREFIX + name, f[key]);
  if (layout !== "list") params.set(`${PREFIX}view`, layout);
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
    filters[key] = value;
  }
  const layout = source.get(`${PREFIX}view`);
  return { filters, layout: LAYOUTS.includes(layout) ? layout : "list" };
}

// `search` with this view's parameters replaced and everything else kept.
export function searchWithView(search, filters, layout) {
  const merged = new URLSearchParams(search);
  for (const key of [...merged.keys()]) if (key.startsWith(PREFIX)) merged.delete(key);
  for (const [key, value] of viewToParams(filters, layout)) merged.set(key, value);
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
