// The "Check with AI" card on a book's page: the wording for what the check found,
// kept free of React so it can be tested.

import { FORM_LABELS } from "./storage.js";

export const CHECK_OPTIONS_KEY = "ai-librarian.aicheck.options";

const FIELD_LABELS = {
  author: "Author",
  title: "Title",
  kind: "Kind",
  form: "Form",
  details: "Details",
};

const DETAIL_LABELS = {
  isbn_10: "ISBN-10",
  isbn_13: "ISBN-13",
  published_year: "year",
  page_count: "pages",
  publisher: "publisher",
  language: "language",
  description: "description",
  subtitle: "subtitle",
  genres: "genres",
};

const KIND_LABELS = { book: "a book", paper: "a paper", document: "a document", note: "a note" };

const quoted = (value) => (value ? `“${value}”` : "none");

// One change as a label and a sentence, e.g. Author: "Camus" → "Albert Camus".
export function describeChange(change) {
  const label = FIELD_LABELS[change.field] || change.field;
  if (change.field === "form") {
    return { label, text: `${FORM_LABELS[change.before] || "not decided"} → ${FORM_LABELS[change.after] || change.after}` };
  }
  if (change.field === "details") {
    const names = String(change.after || "")
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean)
      .map((name) => DETAIL_LABELS[name] || name.replace(/_/g, " "));
    return { label, text: names.length ? `Fill in ${names.join(", ")}` : "Fill in what is missing" };
  }
  if (change.field === "kind") {
    return { label, text: `${KIND_LABELS[change.before] || change.before || "unsorted"} → ${KIND_LABELS[change.after] || change.after}` };
  }
  return { label, text: `${quoted(change.before)} → ${quoted(change.after)}` };
}

// "title page + catalogue" → "Title page + catalogue".
export function sourceLabel(source) {
  const text = String(source || "").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

// Applied changes that have not been undone.
export function appliedCount(result) {
  return (result?.applied || []).filter((item) => !item.undone).length;
}

// What the last check came to, in one line.
export function checkSummary(result) {
  if (!result?.run_id) return "";
  const waiting = (result.suggestions || []).length;
  const model = result.model ? ` with ${result.model}` : "";
  const online = result.options?.online ? " and the online catalogue" : "";
  const how = result.model || online ? `Checked${model}${model && online ? online : online ? " with the online catalogue" : ""}` : "Checked by the rules alone";
  if (!waiting) return `${how}: nothing to change.`;
  return `${how}: ${waiting} ${waiting === 1 ? "change" : "changes"} suggested.`;
}

// Why nothing came of a check that found nothing to suggest, when the notes say so.
export function checkNotes(result) {
  return (result?.notes || []).filter(Boolean);
}

// Which models the picker offers, and a line explaining an empty one.
export function modelNote({ models = [], ollamaConfigured = false } = {}) {
  if (models.length) return "";
  if (!ollamaConfigured) return "No local model is set up, so only the rules and, if you allow it, the online catalogue can be used.";
  return "Your local model did not answer, so only the rules and, if you allow it, the online catalogue can be used.";
}

// The saved choice of model and whether to ask the online catalogue.
export function parseOptions(stored) {
  return {
    online: stored?.online === true,
    model: typeof stored?.model === "string" ? stored.model : "",
  };
}
