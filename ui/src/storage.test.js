import { beforeEach, describe, expect, it } from "vitest";

import {
  displayTitle,
  docKind,
  historyEntries,
  isRead,
  ownedState,
  loadInquiries,
  loadRecents,
  recordInquiry,
  recordOpen,
  relevancePercent,
  shelfItems,
} from "./storage.js";

function memoryStorage() {
  const store = new Map();
  return {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  };
}

beforeEach(() => {
  globalThis.window = { localStorage: memoryStorage() };
});

const doc = (id, status = "indexed", indexedAt = "2026-01-01") => ({
  document_id: id,
  filename: `${id}.pdf`,
  indexing_status: status,
  indexed_at: indexedAt,
});

describe("recents", () => {
  it("moves a reopened document to the front and remembers its page", () => {
    let recents = recordOpen([], "a", 1, 1);
    recents = recordOpen(recents, "b", 4, 2);
    recents = recordOpen(recents, "a", 9, 3);
    expect(recents.map((r) => [r.id, r.page])).toEqual([["a", 9], ["b", 4]]);
    expect(loadRecents()).toEqual(recents);
  });

  it("survives corrupt storage", () => {
    window.localStorage.setItem("ai-librarian.recents", "{not json");
    expect(loadRecents()).toEqual([]);
  });
});

describe("inquiries", () => {
  it("dedupes by query and mode, newest first", () => {
    let list = recordInquiry([], "camus", "search", 1);
    list = recordInquiry(list, "camus", "ask", 2);
    list = recordInquiry(list, " camus ", "search", 3);
    expect(list.map((e) => `${e.mode}:${e.q}`)).toEqual(["search:camus", "ask:camus"]);
    expect(loadInquiries()).toEqual(list);
  });

  it("ignores blank queries", () => {
    expect(recordInquiry([], "   ", "search")).toEqual([]);
  });
});

describe("shelfItems", () => {
  it("puts recently opened documents first, then tops up with the newest indexed", () => {
    const documents = [doc("old", "indexed", "2026-01-01"), doc("new", "indexed", "2026-03-01"), doc("q", "queued"), doc("r")];
    const recents = [{ id: "r", page: 3, at: 5 }, { id: "gone", page: 1, at: 4 }];
    const items = shelfItems(recents, documents, 3);
    expect(items.map((i) => i.doc.document_id)).toEqual(["r", "new", "old"]);
    expect(items[0].page).toBe(3);
  });

  it("respects the limit within the recents themselves", () => {
    const documents = [doc("a"), doc("b")];
    expect(shelfItems([{ id: "a" }, { id: "b" }], documents, 1)).toHaveLength(1);
  });
});

describe("relevancePercent", () => {
  it("maps the score band to 0-100 and clamps", () => {
    expect(relevancePercent(0)).toBe(50);
    expect(relevancePercent(-20)).toBe(0);
    expect(relevancePercent(20)).toBe(100);
    expect(relevancePercent(undefined)).toBeNull();
  });
});

describe("displayTitle", () => {
  it("drops the extension and underscores", () => {
    expect(displayTitle("The_Myth_of_Sisyphus.PDF")).toBe("The Myth of Sisyphus");
  });
});

describe("document marks", () => {
  it("prefers the reader's kind over the detected one", () => {
    expect(docKind({ kind: "paper" })).toBe("paper");
    expect(docKind({ kind: "paper", kind_override: "book" })).toBe("book");
    expect(docKind({})).toBe("document");
  });

  it("reads read_at and owned", () => {
    expect(isRead({ read_at: "2026-01-01" })).toBe(true);
    expect(isRead({ read_at: null })).toBe(false);
    expect(ownedState({ kind: "book", owned: 1 })).toBe(true);
    expect(ownedState({ kind: "book", owned: 0 })).toBe(false);
    expect(ownedState({ kind: "book", owned: null })).toBe(false); // unmarked = not owned
    expect(ownedState({ kind: "paper", owned: 1 })).toBeNull(); // only books
  });
});

describe("historyEntries", () => {
  it("merges chats and searches newest first, leaving questions inside their chats", () => {
    const chats = [{ id: "c1", title: "Camus", updated_at: "2026-01-02T00:00:00Z", message_count: 4 }];
    const inquiries = [
      { q: "absurd", mode: "search", at: Date.parse("2026-01-03T00:00:00Z") },
      { q: "what is revolt?", mode: "ask", at: Date.parse("2026-01-04T00:00:00Z") },
      { q: "older", mode: "search", at: Date.parse("2026-01-01T00:00:00Z") },
    ];
    const entries = historyEntries(chats, inquiries);
    expect(entries.map((e) => e.key)).toEqual(["search:absurd", "chat:c1", "search:older"]);
    expect(entries[1]).toMatchObject({ type: "chat", title: "Camus", count: 4 });
  });
});
