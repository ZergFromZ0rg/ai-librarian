// The Documents library: working files that are not books (papers, reports,
// manuals, slides, data, writing, legal and finance). Pure helpers, so the
// list, its counts and its filters always agree.
import { docKind, readingStatus } from "./storage.js";

export const DOC_TYPES = [
  ["paper", "Paper"],
  ["report", "Report"],
  ["manual", "Manual & spec"],
  ["slides", "Slides"],
  ["data", "Data"],
  ["writing", "Writing"],
  ["legal", "Legal & finance"],
  ["other", "Other"],
];
export const TYPE_LABEL = Object.fromEntries(DOC_TYPES);

export const EMPTY_DOC_FILTERS = { query: "", type: "all", status: "all", sort: "added", unfiled: false };

export function isWorkDocument(doc) {
  return Boolean(doc)
    && doc.record_type !== "standalone"
    && doc.collection_id !== "notes"
    && !["book", "note"].includes(docKind(doc));
}

// Not yet classified counts as "other" so every document has a visible type.
export function docType(doc) {
  return doc.doc_type_override || doc.doc_type || "other";
}

export const isFiled = (doc) => Boolean(doc.shelf);
export const shelfOf = (doc) => doc.shelf || doc.shelf_suggested || "";

function genres(doc) {
  if (Array.isArray(doc.genres)) return doc.genres;
  try { return JSON.parse(doc.genres_json || "[]"); } catch { return []; }
}

function haystack(doc) {
  return [doc.title, doc.filename, doc.author, doc.subject, doc.description, shelfOf(doc), TYPE_LABEL[docType(doc)], ...genres(doc)]
    .filter(Boolean).join(" ").toLocaleLowerCase();
}

const byText = (get) => (a, b) => String(get(a) || "").localeCompare(String(get(b) || ""), undefined, { sensitivity: "base" });
const byNumberDesc = (get) => (a, b) => (get(b) || 0) - (get(a) || 0);
const title = (doc) => doc.title || doc.filename;

const SORTS = {
  added: (a, b) => String(b.uploaded_at || "").localeCompare(String(a.uploaded_at || "")),
  title: byText(title),
  year: byNumberDesc((doc) => doc.published_year),
  pages: byNumberDesc((doc) => doc.page_count || doc.pages),
  type: (a, b) => byText((doc) => TYPE_LABEL[docType(doc)])(a, b) || byText(title)(a, b),
  shelf: (a, b) => byText(shelfOf)(a, b) || byText(title)(a, b),
};

export function filterDocuments(documents, filters = EMPTY_DOC_FILTERS) {
  const needle = filters.query.trim().toLocaleLowerCase();
  return documents
    .filter(isWorkDocument)
    .filter((doc) => (needle ? haystack(doc).includes(needle) : true))
    .filter((doc) => (filters.type === "all" ? true : docType(doc) === filters.type))
    .filter((doc) => (filters.status === "all" ? true : readingStatus(doc) === filters.status))
    .filter((doc) => (filters.unfiled ? !isFiled(doc) : true))
    .sort((a, b) => (SORTS[filters.sort] || SORTS.added)(a, b) || byText(title)(a, b));
}

// Counts for the type chips. They ignore the type filter itself (so a chip
// shows what choosing it would give) but respect the search and the rest.
export function typeCounts(documents, filters = EMPTY_DOC_FILTERS) {
  const base = filterDocuments(documents, { ...filters, type: "all" });
  const counts = { all: base.length };
  for (const doc of base) counts[docType(doc)] = (counts[docType(doc)] || 0) + 1;
  return counts;
}

export function unfiledCount(documents) {
  return documents.filter((doc) => isWorkDocument(doc) && !isFiled(doc) && doc.indexing_status === "indexed").length;
}
