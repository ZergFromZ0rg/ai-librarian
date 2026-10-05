import { genresFor, libraryBooks } from "./libraryExplore.js";
import { readingStatus } from "./storage.js";

function tally(items, key) {
  const counts = new Map();
  for (const item of items) {
    const value = key(item);
    if (!value) continue;
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return [...counts].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
}

export function libraryStats(documents, now = new Date()) {
  const books = libraryBooks(documents);
  const completed = books.filter((doc) => readingStatus(doc) === "read");
  const pagesRead = completed.reduce((sum, doc) => sum + Number(doc.page_count || doc.pages || 0), 0);
  const thisYear = String(now.getFullYear());
  const completedThisYear = completed.filter((doc) => (doc.finished_at || "").startsWith(thisYear));
  const ratingValues = books.map((doc) => Number(doc.rating || 0)).filter(Boolean);
  return {
    total: books.length,
    owned: books.filter((doc) => Boolean(doc.owned)).length,
    reading: books.filter((doc) => readingStatus(doc) === "reading").length,
    completed: completed.length,
    rated: books.filter((doc) => doc.rating).length,
    pagesRead,
    completedThisYear: completedThisYear.length,
    pagesThisYear: completedThisYear.reduce((sum, doc) => sum + Number(doc.page_count || doc.pages || 0), 0),
    averageRating: ratingValues.length ? ratingValues.reduce((sum, rating) => sum + rating, 0) / ratingValues.length : 0,
    statuses: tally(books, readingStatus).map((row) => ({ ...row, key: row.label, label: row.label === "to_read" ? "To read" : row.label[0].toUpperCase() + row.label.slice(1) })),
    ratings: [5, 4, 3, 2, 1].map((rating) => ({ label: `${rating} star`, value: books.filter((doc) => Number(doc.rating) === rating).length, rating })),
    formats: tally(books, (doc) => doc.record_type === "standalone" ? "catalogue" : (doc.file_type || "other").toUpperCase()),
    genres: tally(books.flatMap((doc) => genresFor(doc).map((genre) => ({ genre }))), (item) => item.genre).slice(0, 10),
    authors: tally(books, (doc) => doc.author || "Unknown author").slice(0, 10),
    sources: tally(books, (doc) => doc.acquisition_source || "unknown").slice(0, 10).map((row) => ({ ...row, key: row.label, label: row.label === "unknown" ? "Unknown" : row.label.replaceAll("_", " ").replace(/^./, (char) => char.toUpperCase()) })),
    collections: tally(books, (doc) => doc.collection_id || "legacy-root").slice(0, 10),
    years: tally(completed, (doc) => (doc.finished_at || "").slice(0, 4)).sort((a, b) => a.label.localeCompare(b.label)),
  };
}
