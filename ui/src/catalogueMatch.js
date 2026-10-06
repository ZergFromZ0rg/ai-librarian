// Field-by-field comparison between a book and a catalogue match. Nothing is
// applied implicitly: the reader ticks rows, and only ticked rows are sent.
const ROWS = [
  ["title", "Title"],
  ["subtitle", "Subtitle"],
  ["author", "Author"],
  ["publisher", "Publisher"],
  ["published_year", "Year"],
  ["page_count", "Pages"],
  ["language", "Language"],
  ["genres", "Genres"],
  ["isbn_13", "ISBN-13"],
  ["isbn_10", "ISBN-10"],
];

export function looksLikeIsbn(text) {
  const digits = String(text).replace(/[\s-]/g, "");
  return /^(?:\d{9}[\dXx]|\d{13})$/.test(digits);
}

function currentGenres(doc) {
  if (Array.isArray(doc.genres)) return doc.genres;
  try { return JSON.parse(doc.genres_json || "[]"); } catch { return []; }
}

function show(key, value) {
  if (value == null || value === "") return "";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

// Rows for every field the match knows. A row starts ticked only when the book
// has nothing there yet, so existing values are never replaced by accident.
export function matchRows(doc, candidate) {
  return ROWS.flatMap(([key, label]) => {
    const suggested = candidate[key];
    if (suggested == null || suggested === "" || (Array.isArray(suggested) && !suggested.length)) return [];
    const current = key === "genres" ? currentGenres(doc) : doc[key];
    const currentText = show(key, current);
    const suggestedText = show(key, suggested);
    if (currentText === suggestedText) return [];
    return [{ key, label, current: currentText, suggested: suggestedText, value: suggested, checked: currentText === "" }];
  });
}

export function applyBody(rows, ticked, candidate, withCover) {
  const fields = {};
  for (const row of rows) if (ticked[row.key] ?? row.checked) fields[row.key] = row.value;
  const body = { fields, provider_id: candidate.id };
  if (withCover && candidate.cover_id) body.cover_id = candidate.cover_id;
  return body;
}

// Every field of a match, for creating a brand-new book from it.
export function fullBody(candidate, withCover = true) {
  const fields = {};
  for (const [key] of ROWS) {
    const value = candidate[key];
    if (value != null && value !== "" && !(Array.isArray(value) && !value.length)) fields[key] = value;
  }
  const body = { fields, provider_id: candidate.id };
  if (withCover && candidate.cover_id) body.cover_id = candidate.cover_id;
  return body;
}
