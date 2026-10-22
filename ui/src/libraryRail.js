import { libraryBooks } from "./libraryExplore.js";
import { displayTitle, readingStatus } from "./storage.js";

// The reading goal is one number: books to finish in a calendar year. It is
// kept in this browser (see GOAL_KEY), like the other small preferences.
export const GOAL_KEY = "ai-librarian.reading-goal";
export const MAX_GOAL = 999;

// A whole number of books from whatever was typed or stored; 0 means no goal.
export function parseGoal(value) {
  const number = Math.floor(Number(value));
  return Number.isFinite(number) && number > 0 ? Math.min(number, MAX_GOAL) : 0;
}

// Where the year stands against the goal. `pace` compares the books finished
// with the share of the year gone by, rounded to whole books: "ahead" or
// "behind" only when the gap is at least one book, otherwise "on".
export function goalProgress(finished, target, now = new Date()) {
  const goal = parseGoal(target);
  if (!goal) return null;
  const start = new Date(now.getFullYear(), 0, 1);
  const end = new Date(now.getFullYear() + 1, 0, 1);
  const expected = goal * ((now - start) / (end - start));
  const gap = Math.round(finished - expected);
  return {
    finished,
    target: goal,
    percent: Math.min(100, Math.round((finished / goal) * 100)),
    left: Math.max(0, goal - finished),
    pace: finished >= goal ? "done" : gap >= 1 ? "ahead" : gap <= -1 ? "behind" : "on",
    gap: Math.abs(gap),
  };
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
