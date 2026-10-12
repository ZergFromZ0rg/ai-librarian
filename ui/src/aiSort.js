// What the Library's "AI sort files" panel shows, worked out from the report the
// server keeps for the last run (GET /library/ai-sort). Kept free of React so the
// wording and the counts can be tested.

export const SORT_OPTIONS_KEY = "ai-librarian.aisort.options";

const TERMINAL_STATES = new Set(["done", "cancelled", "interrupted", "error", "undone"]);

export function isSortRunning(report) {
  return report?.state === "running";
}

// A finished (or cut-off) run that has something to look at or undo.
export function hasSortResult(report) {
  if (!report || report.state === "idle" || report.state === "running") return false;
  return Boolean(report.run_id);
}

export function isSortFinished(report) {
  return TERMINAL_STATES.has(report?.state);
}

export function sortActionProgress(report) {
  if (!isSortRunning(report)) return null;
  const total = report.total || 0;
  const done = report.done || 0;
  if (!total) return { label: "AI sort…", detail: "Looking through the library", percent: null };
  return {
    label: `AI sort ${done} of ${total}…`,
    detail: report.current || "Checking books",
    percent: Math.round((done / total) * 100),
  };
}

const FIELD_LABELS = {
  author: "Author",
  title: "Title",
  kind: "Kind",
  fiction: "Fiction",
  details: "Details",
};

const DETAIL_LABELS = {
  isbn_10: "ISBN-10",
  isbn_13: "ISBN-13",
  published_year: "year",
  page_count: "pages",
  publisher: "publisher",
  language: "language",
  subject: "subject",
  description: "description",
  subtitle: "subtitle",
  genres: "genres",
};

const KIND_LABELS = { book: "a book", paper: "a paper", document: "a document", note: "a note" };

function quoted(value) {
  return value ? `“${value}”` : "none";
}

// One change as a label and a sentence, e.g. Author: "Camus" → "Albert Camus".
export function describeChange(change) {
  const label = FIELD_LABELS[change.field] || change.field;
  if (change.field === "fiction") {
    return { label, text: change.after === "fiction" ? "Read as fiction" : "Read as non-fiction" };
  }
  if (change.field === "details") {
    const names = String(change.after || "")
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean)
      .map((name) => DETAIL_LABELS[name] || name.replace(/_/g, " "));
    return { label, text: names.length ? `Filled in ${names.join(", ")}` : "Filled in what was missing" };
  }
  if (change.field === "kind") {
    return { label, text: `${KIND_LABELS[change.before] || change.before || "unsorted"} → ${KIND_LABELS[change.after] || change.after}` };
  }
  return { label, text: `${quoted(change.before)} → ${quoted(change.after)}` };
}

// "title page + catalogue" → "Title page + catalogue".
export function sourceLabel(source) {
  const text = String(source || "").trim();
  if (!text) return "";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function undoneCount(report) {
  return (report?.applied || []).filter((item) => item.undone).length;
}

// Applied changes that can still be undone.
export function undoableCount(report) {
  return (report?.applied || []).filter((item) => !item.undone).length;
}

export function skippedTotal(report) {
  return Object.values(report?.skipped || {}).reduce((sum, count) => sum + count, 0);
}

// The skipped books grouped by why, biggest first: "12 already checked · 3 edited by you".
export function skippedLine(report) {
  return Object.entries(report?.skipped || {})
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([reason, count]) => `${count.toLocaleString()} ${reason}`)
    .join(" · ");
}

const STATE_HEADLINES = {
  running: "Sorting the library…",
  done: "Sort finished",
  cancelled: "Sort stopped",
  interrupted: "Sort was interrupted",
  error: "Sort stopped on an error",
  undone: "Sort undone",
};

export function sortHeadline(report) {
  return STATE_HEADLINES[report?.state] || "";
}

// "Checked 120 of 140 books: 18 changed, 96 left as they were; 6 suggestions to review."
// A book can be changed and also have a suggestion, so the review count is kept apart
// from the counts that add up to the books checked.
export function sortSummary(report) {
  if (!report || !report.run_id) return "";
  const checked = report.done || 0;
  const total = report.total || 0;
  const sortChanges = (report.applied || []).filter((item) => item.via !== "review");
  const changed = sortChanges.filter((item) => !item.undone).length;
  const putBack = sortChanges.length - changed;
  const review = (report.suggestions || []).length;
  const head = `Checked ${checked.toLocaleString()}${total && total !== checked ? ` of ${total.toLocaleString()}` : ""} ${total === 1 ? "book" : "books"}`;
  const results = [];
  if (changed) results.push(`${changed.toLocaleString()} changed`);
  if (putBack) results.push(`${putBack.toLocaleString()} put back`);
  if (report.unchanged) results.push(`${report.unchanged.toLocaleString()} left as they were`);
  let text = results.length ? `${head}: ${results.join(", ")}` : `${head}; nothing needed changing`;
  if (review) text += `; ${review.toLocaleString()} ${review === 1 ? "suggestion" : "suggestions"} to review`;
  return `${text}.`;
}

// Why the model picker is empty, for the line under it.
export function modelNote({ models = [], ollamaConfigured = false } = {}) {
  if (models.length) return "";
  if (!ollamaConfigured) return "No local model is set up (OLLAMA_URL is empty), so only the rules and strict catalogue matches will be used.";
  return "Ollama did not answer or has no models installed, so only the rules and strict catalogue matches will be used.";
}
