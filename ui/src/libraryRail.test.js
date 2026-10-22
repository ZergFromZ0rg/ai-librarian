import { describe, expect, it } from "vitest";
import { currentlyReading, finishedByMonth, goalProgress, niceCeiling, parseGoal, pickQuote, quoteCandidates, readingProgress } from "./libraryRail.js";

const docs = [
  { document_id: "a", title: "Dune", kind: "book", owned: 1, reading_status: "read", page_count: 600, finished_at: "2026-10-02" },
  { document_id: "b", title: "The Trial", kind: "book", owned: 0, reading_status: "reading", page_count: 250, current_page: 100, reading_progress: 40, last_read_at: "2026-10-10T08:00:00Z" },
  { document_id: "c", title: "Piranesi", kind: "book", reading_status: "to_read", acquisition_source: "borrowed" },
  { document_id: "d", title: "Emma", kind: "book", owned: 1, reading_status: "reading", reading_progress: 18, last_read_at: "2026-10-12T08:00:00Z" },
  { document_id: "e", title: "Emma again", kind: "book", reading_status: "read", page_count: 300, finished_at: "2026-08-15" },
  { document_id: "n", title: "A note", kind_override: "note", collection_id: "notes" },
];

describe("reading goal", () => {
  it("keeps a goal to whole books, none when it is empty or nonsense", () => {
    expect([12, "24", " 7.9 "].map(parseGoal)).toEqual([12, 24, 7]);
    expect(["", "abc", 0, -3, null, undefined].map(parseGoal)).toEqual([0, 0, 0, 0, 0, 0]);
    expect(parseGoal(5000)).toBe(999);
  });

  it("has no progress without a goal", () => {
    expect(goalProgress(3, 0)).toBeNull();
  });

  it("shows how far along the year is against the share of the year gone", () => {
    const midYear = new Date(2026, 6, 2); // about half way
    expect(goalProgress(6, 12, midYear)).toMatchObject({ percent: 50, left: 6, pace: "on", gap: 0 });
    expect(goalProgress(9, 12, midYear)).toMatchObject({ pace: "ahead", gap: 3 });
    expect(goalProgress(2, 12, midYear)).toMatchObject({ pace: "behind", gap: 4 });
  });

  it("is done, and never over 100%, once the goal is reached", () => {
    expect(goalProgress(15, 12, new Date(2026, 2, 1))).toMatchObject({ percent: 100, left: 0, pace: "done" });
  });
});

describe("currently reading", () => {
  it("lists books in progress, latest read first, and reports how many there are", () => {
    const reading = currentlyReading(docs, 1);
    expect(reading.items.map((doc) => doc.document_id)).toEqual(["d"]);
    expect(reading.total).toBe(2);
  });

  it("works out the page and pages left from what is known", () => {
    expect(readingProgress(docs[1])).toEqual({ percent: 40, current: 100, total: 250, left: 150 });
    expect(readingProgress({ page_count: 200, reading_progress: 25 })).toEqual({ percent: 25, current: 50, total: 200, left: 150 });
    expect(readingProgress({ reading_progress: 18 })).toEqual({ percent: 18, current: 0, total: 0, left: null });
    expect(readingProgress({ page_count: 100, current_page: 400, reading_progress: 140 })).toEqual({ percent: 100, current: 100, total: 100, left: 0 });
  });
});

describe("finished by month", () => {
  const now = new Date(2026, 9, 15); // October 2026

  it("returns a window of months ending now, oldest first, across a year boundary", () => {
    const slots = finishedByMonth(docs, new Date(2026, 1, 10), 4);
    expect(slots.map((slot) => slot.key)).toEqual(["2025-11", "2025-12", "2026-01", "2026-02"]);
  });

  it("counts books and pages in the month they were finished", () => {
    const slots = finishedByMonth(docs, now, 12);
    expect(slots).toHaveLength(12);
    expect(slots[0].key).toBe("2025-11");
    expect(slots[11]).toMatchObject({ key: "2026-10", label: "Oct", books: 1, pages: 600 });
    expect(slots.find((slot) => slot.key === "2026-08")).toMatchObject({ books: 1, pages: 300 });
    expect(slots.reduce((sum, slot) => sum + slot.books, 0)).toBe(2);
  });

  it("leaves out read books with no finished date and anything outside the window", () => {
    const slots = finishedByMonth([{ document_id: "x", kind: "book", reading_status: "read" }, { document_id: "y", kind: "book", reading_status: "read", finished_at: "2020-01-01" }], now, 12);
    expect(slots.reduce((sum, slot) => sum + slot.books, 0)).toBe(0);
  });
});

describe("chart scale", () => {
  it("rounds the top of an axis up to a tidy number", () => {
    expect([0, 1, 3, 4].map(niceCeiling)).toEqual([2, 2, 4, 4]);
    expect([5, 7, 12, 90, 1300, 3420].map(niceCeiling)).toEqual([5, 10, 20, 100, 2000, 5000]);
  });
});

describe("random quote", () => {
  const notes = [
    { document_id: "n1", source_document_id: "a", source_page: 12, source_quote: "  Fear is the mind-killer.  ", text: "mine" },
    { document_id: "n2", source_document_id: "gone", source_quote: "Orphaned passage" },
    { document_id: "n3", text: "a plain note with no quote" },
    { document_id: "n4", source_document_id: "b", source_quote: "   " },
  ];

  it("keeps only notes that hold a quote, trimmed, with the book they came from", () => {
    expect(quoteCandidates(notes, docs)).toEqual([
      { id: "n1", text: "Fear is the mind-killer.", sourceId: "a", sourceTitle: "Dune", page: 12 },
      { id: "n2", text: "Orphaned passage", sourceId: null, sourceTitle: null, page: null },
    ]);
  });

  it("never repeats the quote just shown while another exists", () => {
    const list = quoteCandidates(notes, docs);
    expect(pickQuote(list, "n1", () => 0).id).toBe("n2");
    expect(pickQuote(list, "n2", () => 0.99).id).toBe("n1");
    expect(pickQuote(list.slice(0, 1), "n1").id).toBe("n1");
    expect(pickQuote([], null)).toBeNull();
  });
});
