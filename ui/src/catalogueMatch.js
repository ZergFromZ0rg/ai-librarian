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

// ---- logging a book by title and author ---------------------------------------
// The reader types a title (and maybe an author); the catalogue is searched for
// it and a match is used only when it is clearly the right book. These helpers
// decide that, and what to keep of what was typed.

const fold = (text) => String(text || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

// What to search for: the title, then the author when there is one.
export function lookupQuery(title, author) {
  return [title, author].map((part) => String(part || "").trim()).filter(Boolean).join(" ");
}

// The one result worth trusting, or null. It has to score well and not tie with a
// different book, so a vague title does not quietly become the wrong book.
export function confidentMatch(results) {
  const [first, second] = results || [];
  if (!first || !(first.score >= 0.8)) return null;
  if (second && first.score - second.score < 0.05 && fold(first.title) !== fold(second.title)) return null;
  return first;
}

// A match, as the fields to apply to the book just logged. What the reader typed
// stays unless the match is plainly the same thing written better: the title only
// changes in capitals or punctuation, and an author typed as a lone surname
// ("Camus") is completed from the match ("Albert Camus"). Nothing is dropped.
export function bodyForLoggedBook(candidate, typed) {
  const body = fullBody(candidate);
  const { title, author } = typed;
  if (title && fold(title) !== fold(candidate.title)) delete body.fields.title;
  if (author) {
    const lone = !fold(author).includes(" ");
    const completes = lone && fold(candidate.author).split(" ").pop() === fold(author);
    if (!completes && fold(author) !== fold(candidate.author)) delete body.fields.author;
  }
  return body;
}

// A book already on the shelf with this title (and author, when both are known):
// logging it twice is allowed but worth a word first.
export function findLoggedBook(documents, title, author) {
  const wanted = fold(title);
  if (!wanted) return null;
  const who = fold(author);
  return (documents || []).find((doc) => {
    if (doc.collection_id === "notes" || fold(doc.title || doc.filename) !== wanted) return false;
    const theirs = fold(doc.author);
    return !who || !theirs || who === theirs;
  }) || null;
}

// Where a match came from, for the words shown to the reader.
export function providerName(candidate) {
  return String(candidate?.id || "").startsWith("gb:") ? "Google Books" : "Open Library";
}
