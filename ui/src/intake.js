// The review screen for new uploads: turning what the server proposed into form
// values and back, and the wording about where each value came from. Kept free of
// React so it can be tested.

import { formatBytes } from "./indexActivity.js";
import { loadStored, saveStored } from "./storage.js";

export const SUPPORTED_UPLOADS = /\.(pdf|epub|docx|xlsx|pptx|txt|md|csv)$/i;

const DRAFT_PREFIX = "ai-librarian.intake.draft.";

const BLANK = {
  title: "", subtitle: "", author: "", subject: "", genres: "", description: "", publisher: "",
  published_year: "", language: "", isbn_10: "", isbn_13: "", page_count: "", type: "book", shelf: "",
};

const text = (value) => (value == null ? "" : String(value));

// The proposal as the strings the form edits.
export function formFromItem(item) {
  const proposal = item?.proposal || {};
  return {
    ...BLANK,
    title: text(proposal.title), subtitle: text(proposal.subtitle), author: text(proposal.author),
    subject: text(proposal.subject), genres: (proposal.genres || []).join(", "), description: text(proposal.description),
    publisher: text(proposal.publisher), published_year: text(proposal.published_year), language: text(proposal.language),
    isbn_10: text(proposal.isbn_10), isbn_13: text(proposal.isbn_13), page_count: text(proposal.page_count),
    type: proposal.type || "book", shelf: text(proposal.shelf),
  };
}

const number = (value) => (value.trim() === "" ? null : Number(value));

// The body the save endpoint takes. Every field is sent, so what the reader saw
// and confirmed is stored as theirs, blanks included.
export function payloadFromForm(form) {
  return {
    title: form.title.trim(), subtitle: form.subtitle.trim(), author: form.author.trim(), subject: form.subject.trim(),
    genres: form.genres.split(",").map((genre) => genre.trim()).filter(Boolean),
    description: form.description.trim(), publisher: form.publisher.trim(), language: form.language.trim(),
    published_year: number(form.published_year), page_count: number(form.page_count),
    isbn_10: form.isbn_10.trim(), isbn_13: form.isbn_13.trim(), type: form.type, shelf: form.shelf.trim(),
  };
}

// The first thing wrong with a form, in words, or "" when it can be saved.
export function validateForm(form) {
  if (!form.title.trim()) return "Give it a title.";
  const year = number(form.published_year);
  if (year !== null && (!Number.isInteger(year) || year < 1000 || year > 2100)) return "The year must be between 1000 and 2100.";
  const pages = number(form.page_count);
  if (pages !== null && (!Number.isInteger(pages) || pages < 1)) return "The page count must be a whole number.";
  if (form.shelf.split("/").filter((part) => part.trim()).length > 6) return "A folder can be at most 6 levels deep.";
  return "";
}

// Fields that differ from what was proposed.
export function editedFields(form, item) {
  const proposed = formFromItem(item);
  return Object.keys(proposed).filter((key) => form[key] !== proposed[key]);
}

const SOURCE_WORDS = {
  "file name": "file name",
  "file properties": "file properties",
  "copyright page": "copyright page",
  "title page": "title page",
  "the opening text": "opening text",
  "the text of the file": "the text",
  "the file format": "file format",
  guessed: "a guess",
};

// How to label a field: "edited" once changed, else where the server found it
// (nothing when it found nothing, so an empty field is not mistaken for a verified one).
export function fieldTag(item, form, key) {
  if (editedFields(form, item).includes(key)) return "edited";
  if (!form[key]) return "";
  if (key === "shelf") return "suggested";
  return SOURCE_WORDS[item?.sources?.[key]] || "";
}

export function groupTypes(types = []) {
  const groups = [];
  for (const option of types) {
    let group = groups.find((entry) => entry.group === option.group);
    if (!group) groups.push((group = { group: option.group, options: [] }));
    group.options.push(option);
  }
  return groups;
}

// "PDF · 312 pages · 4.2 MB"
export function fileFacts(item) {
  const facts = [item?.format_label || (item?.format || "file").toUpperCase()];
  if (item?.pages) facts.push(`${item.pages.toLocaleString()} ${item.format === "epub" ? "sections" : item.pages === 1 ? "page" : "pages"}`);
  if (Number.isFinite(item?.size)) facts.push(formatBytes(item.size));
  return facts;
}

// What to tell the reader after an upload has been read.
export function uploadMessage(outcome, filename) {
  if (outcome.duplicate?.document_id) return `${filename} is already in the library as “${outcome.duplicate.title}”.`;
  if (outcome.duplicate?.intake_id) return `${filename} is already waiting for review.`;
  return `${filename} is ready to review.`;
}

export function stageSummary({ staged, duplicates, failures }) {
  const parts = [];
  if (staged) parts.push(`${staged} ${staged === 1 ? "file is" : "files are"} ready to review`);
  if (duplicates.length) parts.push(`${duplicates.length} already in the library or waiting`);
  if (failures.length) parts.push(`${failures.length} could not be read (${failures.map((failure) => failure.filename).join(", ")})`);
  return parts.length ? `${parts.join("; ")}.` : "Nothing to review.";
}

export function saveAllSummary({ saved, failed }) {
  const head = `Added ${saved} to the library; indexing has started.`;
  return failed.length ? `${head} ${failed.length} could not be saved: ${failed.map((entry) => `${entry.title} (${entry.message})`).join("; ")}` : head;
}

// The folder after the type changes. While the folder is still on the old type's top
// level ("Books/..."), it moves to the new type's ("Reports"); a move between books and
// documents also drops the rest of the path, which was about the old kind of thing.
// A folder the reader pointed somewhere else is left alone.
export function shelfAfterTypeChange(shelf, types, from, to) {
  const before = types.find((option) => option.value === from);
  const after = types.find((option) => option.value === to);
  if (!before || !after || before.shelf === after.shelf) return shelf;
  const parts = shelf.split("/").map((part) => part.trim()).filter(Boolean);
  if (parts[0] !== before.shelf) return shelf;
  return before.group === after.group ? [after.shelf, ...parts.slice(1)].join("/") : after.shelf;
}

const BOOK_TYPES = new Set(["novel", "novella", "short_story", "collection", "nonfiction", "book"]);
// The form fields an AI check may fill in; everything else stays the reader's.
const CHECKED_FIELDS = ["title", "author", "subtitle", "publisher", "published_year", "page_count", "language", "isbn_10", "isbn_13", "description"];

export function isBookType(type) {
  return BOOK_TYPES.has(type);
}

function withType(form, type, types) {
  return { ...form, type, shelf: shelfAfterTypeChange(form.shelf, types, form.type, type) };
}

// A check's suggestion, as the patch the server proposed, applied to the review form:
// the field changes and so counts as edited. A suggested kind or form moves the type.
export function applyAiPatch(form, patch, types = []) {
  let next = { ...form };
  for (const [key, value] of Object.entries(patch || {})) {
    if (key === "form") next = withType(next, value, types);
    else if (key === "kind") {
      const type = value === "book" ? (isBookType(next.type) ? next.type : "book") : value === "paper" ? "paper" : (isBookType(next.type) || next.type === "paper" ? "other" : next.type);
      next = withType(next, type, types);
    } else if (key === "genres") next.genres = (value || []).join(", ");
    else if (CHECKED_FIELDS.includes(key)) next[key] = value == null ? "" : String(value);
  }
  return next;
}

// FastAPI sends a string for its own errors and a list for a rejected field.
export function apiError(data, status) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((entry) => {
      const field = (entry.loc || []).filter((part) => part !== "body").join(" ");
      return `${field ? `${field}: ` : ""}${String(entry.msg || "not valid").replace(/^Value error, /, "")}`;
    }).join("; ");
  }
  return `Request failed (${status})`;
}

// ---- drafts: what the reader typed survives a reload ----------------------
export function loadDraft(id) {
  const value = loadStored(DRAFT_PREFIX + id, null, true);
  return value && typeof value === "object" && !Array.isArray(value) ? { ...BLANK, ...value } : null;
}

export function saveDraft(id, form) {
  saveStored(DRAFT_PREFIX + id, form);
}

export function clearDraft(id) {
  try {
    window.localStorage.removeItem(DRAFT_PREFIX + id);
  } catch (_error) {
    // storage is only a convenience
  }
}
