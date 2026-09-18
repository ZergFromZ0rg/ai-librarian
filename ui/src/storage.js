// Per-browser memory for the research console: what was recently opened (the
// covers floating around the search bar), what was recently asked (the
// "recent inquiries" list), and the console's own settings. All of it is a
// convenience — every read tolerates missing, corrupt, or blocked storage.

export const RECENTS_KEY = "ai-librarian.recents";
export const INQUIRIES_KEY = "ai-librarian.inquiries";

const MAX_RECENTS = 24;
const MAX_INQUIRIES = 30;

export function loadStored(key, fallback, parse = false) {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw != null) return parse ? JSON.parse(raw) : raw;
  } catch (_error) {
    // corrupt or unavailable storage
  }
  return fallback;
}

export function saveStored(key, value) {
  try {
    window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch (_error) {
    // best effort
  }
}

function loadList(key) {
  const value = loadStored(key, [], true);
  return Array.isArray(value) ? value : [];
}

// Most recent first, one entry per document, remembering the page last read
// so reopening a cover lands back where the reader left off.
export function loadRecents() {
  return loadList(RECENTS_KEY).filter((entry) => entry && typeof entry.id === "string");
}

export function recordOpen(recents, id, page = 1, now = Date.now()) {
  const next = [{ id, page, at: now }, ...recents.filter((entry) => entry.id !== id)].slice(0, MAX_RECENTS);
  saveStored(RECENTS_KEY, next);
  return next;
}

export function forgetRecent(recents, id) {
  const next = recents.filter((entry) => entry.id !== id);
  saveStored(RECENTS_KEY, next);
  return next;
}

export function loadInquiries() {
  return loadList(INQUIRIES_KEY).filter((entry) => entry && typeof entry.q === "string");
}

// Re-asking the same thing moves it to the top instead of duplicating it.
export function recordInquiry(inquiries, q, mode, now = Date.now()) {
  const clean = q.trim();
  if (!clean) return inquiries;
  const next = [
    { q: clean, mode, at: now },
    ...inquiries.filter((entry) => !(entry.q === clean && entry.mode === mode)),
  ].slice(0, MAX_INQUIRIES);
  saveStored(INQUIRIES_KEY, next);
  return next;
}

export function clearInquiries() {
  saveStored(INQUIRIES_KEY, []);
  return [];
}

// The covers for the floating shelf: recently opened documents first (still
// in the library, in the order they were opened), then topped up with the
// most recently indexed ones so a fresh browser still has something to show.
export function shelfItems(recents, documents, limit) {
  const byId = new Map(documents.map((doc) => [doc.document_id, doc]));
  const items = [];
  const seen = new Set();
  for (const entry of recents) {
    const doc = byId.get(entry.id);
    if (!doc || seen.has(entry.id)) continue;
    seen.add(entry.id);
    items.push({ doc, page: entry.page || 1, openedAt: entry.at });
    if (items.length >= limit) return items;
  }
  const fresh = documents
    .filter((doc) => !seen.has(doc.document_id) && doc.indexing_status === "indexed")
    .sort((a, b) => String(b.indexed_at || "").localeCompare(String(a.indexed_at || "")));
  for (const doc of fresh) {
    items.push({ doc, page: 1, openedAt: null });
    if (items.length >= limit) break;
  }
  return items;
}

// A cross-encoder score (roughly -10 … +10, higher is better) as a 0–100
// meter reading. Linear over the band that actually shows up in practice;
// the raw score is still shown alongside for anyone who wants it.
export function relevancePercent(score, low = -8, high = 8) {
  if (typeof score !== "number" || Number.isNaN(score)) return null;
  const clamped = Math.min(high, Math.max(low, score));
  return Math.round(((clamped - low) / (high - low)) * 100);
}

export function timeAgo(timestamp, now = Date.now()) {
  if (!timestamp) return "";
  const seconds = Math.max(0, Math.round((now - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return new Date(timestamp).toLocaleDateString();
}

// Filename → a readable title for a cover: no extension, separators as spaces.
export function displayTitle(filename) {
  return (filename || "Untitled")
    .replace(/\.pdf$/i, "")
    .replace(/[_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// What the reader's library treats a document as: their correction if they
// made one, otherwise the server's guess.
export function docKind(doc) {
  return doc?.kind_override || doc?.kind || "document";
}

export function isRead(doc) {
  return Boolean(doc?.read_at);
}

// Owned is only meaningful for books (null for anything else). A book the
// reader hasn't marked counts as not owned.
export function ownedState(doc) {
  if (docKind(doc) !== "book") return null;
  return Boolean(doc?.owned);
}

// One newest-first History list: saved chats (from the server) and past
// searches (this browser). Past *questions* aren't listed on their own —
// each already lives inside its chat, so listing it twice was just noise.
export function historyEntries(chats, inquiries) {
  const entries = [];
  for (const chat of chats || []) {
    const at = Date.parse(chat.updated_at || chat.created_at || "") || 0;
    entries.push({ type: "chat", key: `chat:${chat.id}`, id: chat.id, title: chat.title || "Untitled", count: chat.message_count || 0, at });
  }
  for (const entry of inquiries || []) {
    if (entry.mode !== "search") continue;
    entries.push({ type: "search", key: `search:${entry.q}`, q: entry.q, at: entry.at || 0, inquiry: entry });
  }
  return entries.sort((a, b) => b.at - a.at);
}
