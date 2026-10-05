import { displayTitle, readingStatus } from "./storage.js";

export const EMPTY_FILTERS = Object.freeze({
  query: "", status: "all", ownership: "all", rating: "all", format: "all",
  collection: "all", genre: "all", author: "all", acquisition: "all", finishedYear: "all", indexing: "all", sort: "title",
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

export function filterLibrary(documents, filters = EMPTY_FILTERS) {
  const f = { ...EMPTY_FILTERS, ...filters };
  const needle = f.query.trim().toLocaleLowerCase();
  const rows = libraryBooks(documents).filter((doc) => {
    const title = doc.title || displayTitle(doc.filename);
    if (needle && !`${title} ${doc.author || ""} ${doc.isbn_10 || ""} ${doc.isbn_13 || ""} ${doc.description || ""} ${doc.review || ""} ${genresFor(doc).join(" ")}`.toLocaleLowerCase().includes(needle)) return false;
    if (f.status !== "all" && readingStatus(doc) !== f.status) return false;
    if (f.ownership !== "all" && (f.ownership === "owned") !== Boolean(doc.owned)) return false;
    if (f.rating === "rated" && !doc.rating) return false;
    if (!["all", "rated"].includes(f.rating) && Number(doc.rating || 0) !== Number(f.rating)) return false;
    const format = doc.record_type === "standalone" ? "catalogue" : (doc.file_type || "other").toLowerCase();
    if (f.format !== "all" && format !== f.format) return false;
    if (f.collection !== "all" && (doc.collection_id || "legacy-root") !== f.collection) return false;
    if (f.genre !== "all" && !genresFor(doc).some((genre) => genre.toLocaleLowerCase() === f.genre.toLocaleLowerCase())) return false;
    if (f.author !== "all" && (doc.author || "Unknown author") !== f.author) return false;
    if (f.acquisition !== "all" && (doc.acquisition_source || "unknown") !== f.acquisition) return false;
    if (f.finishedYear !== "all" && !(doc.finished_at || "").startsWith(f.finishedYear)) return false;
    if (f.indexing !== "all" && doc.indexing_status !== f.indexing) return false;
    return true;
  });
  const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
  return rows.sort((a, b) => {
    if (f.sort === "author") return collator.compare(a.author || "", b.author || "") || collator.compare(a.title || a.filename, b.title || b.filename);
    if (f.sort === "rating") return Number(b.rating || 0) - Number(a.rating || 0) || collator.compare(a.title || a.filename, b.title || b.filename);
    if (f.sort === "added") return String(b.uploaded_at || "").localeCompare(String(a.uploaded_at || ""));
    if (f.sort === "finished") return String(b.finished_at || "").localeCompare(String(a.finished_at || ""));
    if (f.sort === "pages") return Number(b.page_count || b.pages || 0) - Number(a.page_count || a.pages || 0) || collator.compare(a.title || a.filename, b.title || b.filename);
    if (f.sort === "recent") return String(b.last_read_at || b.updated_at || "").localeCompare(String(a.last_read_at || a.updated_at || ""));
    return collator.compare(a.title || displayTitle(a.filename), b.title || displayTitle(b.filename));
  });
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
