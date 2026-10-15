import { EMPTY_FILTERS, filterLibrary, libraryBooks } from "./libraryExplore.js";
import { displayTitle, readingStatus } from "./storage.js";

// The shelves the side rail jumps to. Each is a set of Library filters, so the
// rail and the Filters panel are two doors to the same room.
export const RAIL_SHELVES = [
  { key: "all", label: "All", preset: {}, hint: "Every book in the library" },
  { key: "owned", label: "Owned", preset: { ownership: "owned" }, hint: "Books you own" },
  { key: "to_read", label: "To read", preset: { status: "to_read" }, hint: "Books you plan to read" },
  { key: "borrowed", label: "Borrowed", preset: { acquisition: "borrowed" }, hint: "Books you borrowed" },
];

const SETTINGS = new Set(["query", "sort", "order"]);
const FACETS = Object.keys(EMPTY_FILTERS).filter((key) => !SETTINGS.has(key));

// A shelf is "on" when its filters are exactly the ones in force, whatever the
// search text and sort order are.
export function shelfActive(filters, preset) {
  return FACETS.every((key) => (filters?.[key] ?? "all") === (preset[key] ?? "all"));
}

// Opening a shelf starts a fresh view of it: the search text is dropped, the
// sort order the reader chose stays.
export function shelfFilters(filters, preset) {
  return { ...EMPTY_FILTERS, sort: filters?.sort ?? EMPTY_FILTERS.sort, order: filters?.order ?? EMPTY_FILTERS.order, ...preset };
}

// How many books each shelf would show, counted by the same filter that
// shows them, so a tile and its list can never disagree.
export function shelfCounts(documents) {
  const counts = {};
  for (const shelf of RAIL_SHELVES) counts[shelf.key] = filterLibrary(documents, { ...EMPTY_FILTERS, ...shelf.preset }).length;
  return counts;
}

const pagesOf = (doc) => Number(doc.page_count || doc.pages || 0);

// Where a book in progress stands. A book without a page count shows only the
// percentage; one with a page count also shows the page and pages left.
export function readingProgress(doc) {
  const total = pagesOf(doc);
  const percent = Math.min(100, Math.max(0, Math.round(Number(doc.reading_progress) || 0)));
  const current = total
    ? Math.min(total, Math.max(0, Number(doc.current_page) || Math.round((total * percent) / 100)))
    : 0;
  return { percent, current, total, left: total ? total - current : null };
}

// Books being read, the most recently read first.
export function currentlyReading(documents, limit = 3) {
  const reading = libraryBooks(documents)
    .filter((doc) => readingStatus(doc) === "reading")
    .sort((a, b) => String(b.last_read_at || b.updated_at || "").localeCompare(String(a.last_read_at || a.updated_at || "")));
  return { items: reading.slice(0, limit), total: reading.length };
}

const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// What was finished in each of the last `months` months, oldest first. A book
// counts in the month of its finished date; one marked read with no date has
// nothing to place it by and is left out.
export function finishedByMonth(documents, now = new Date(), months = 12) {
  const slots = [];
  for (let back = months - 1; back >= 0; back -= 1) {
    const date = new Date(now.getFullYear(), now.getMonth() - back, 1);
    const month = date.getMonth();
    slots.push({ key: `${date.getFullYear()}-${String(month + 1).padStart(2, "0")}`, label: MONTH_ABBR[month], year: date.getFullYear(), books: 0, pages: 0 });
  }
  const byKey = new Map(slots.map((slot) => [slot.key, slot]));
  for (const doc of libraryBooks(documents)) {
    if (readingStatus(doc) !== "read") continue;
    const slot = byKey.get(String(doc.finished_at || "").slice(0, 7));
    if (!slot) continue;
    slot.books += 1;
    slot.pages += pagesOf(doc);
  }
  return slots;
}

// A round number at or above `value` for the top of a chart axis.
export function niceCeiling(value) {
  if (value <= 4) return Math.max(2, Math.ceil(value / 2) * 2);
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * magnitude >= value) return step * magnitude;
  return 10 * magnitude;
}

// Passages the reader saved from their books, each with the book it came from.
export function quoteCandidates(notes, documents) {
  const titles = new Map((documents || []).map((doc) => [doc.document_id, doc.title || displayTitle(doc.filename)]));
  return (notes || [])
    .filter((note) => (note.source_quote || "").trim())
    .map((note) => ({
      id: note.document_id,
      text: note.source_quote.trim(),
      sourceId: titles.has(note.source_document_id) ? note.source_document_id : null,
      sourceTitle: titles.get(note.source_document_id) || null,
      page: note.source_page || null,
    }));
}

// One quote at random, never the one just shown while there is another.
export function pickQuote(candidates, avoidId = null, random = Math.random) {
  if (!candidates.length) return null;
  const pool = candidates.length > 1 ? candidates.filter((quote) => quote.id !== avoidId) : candidates;
  return pool[Math.min(pool.length - 1, Math.floor(random() * pool.length))];
}
