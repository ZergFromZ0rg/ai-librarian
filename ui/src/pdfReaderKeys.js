// Pure helpers for the PDF reader's keyboard shortcuts and its search readout,
// kept apart from the component so they can be tested.

// PDF.js's FindState values (pdf_viewer.mjs).
export const FIND_STATE = { FOUND: 0, NOT_FOUND: 1, WRAPPED: 2, PENDING: 3 };

const isEditable = (target) => {
  if (!target) return false;
  const tag = String(target.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable === true;
};

// What a key press means to the reader: "find", "next-page", "previous-page",
// "escape", or null (not ours). Ctrl/Cmd+F always opens search, replacing the
// browser's own, which only sees the few pages PDF.js has drawn. The single-key
// shortcuts are ignored while typing in a box (the search field, or a text note
// being written on the page) and with any modifier held.
export function shortcutFor(event) {
  const key = event.key;
  if ((event.ctrlKey || event.metaKey) && !event.altKey && String(key).toLowerCase() === "f") return "find";
  if (key === "Escape") return "escape";
  if (event.ctrlKey || event.metaKey || event.altKey || isEditable(event.target)) return null;
  if (key === "/") return "find";
  if (key === "j") return "next-page";
  if (key === "k") return "previous-page";
  return null;
}

// The small readout beside the search box.
export function findSummary(query, { state, current, total } = {}) {
  if (!String(query || "").trim()) return "";
  if (state === FIND_STATE.NOT_FOUND || (state !== FIND_STATE.PENDING && !total)) return "No matches";
  if (state === FIND_STATE.PENDING && !total) return "Searching…";
  const where = current ? `${current} of ${total}` : `${total} ${total === 1 ? "match" : "matches"}`;
  return state === FIND_STATE.PENDING ? `${where}…` : where;
}
