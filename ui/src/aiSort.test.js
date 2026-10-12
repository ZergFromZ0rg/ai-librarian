import { describe, expect, it } from "vitest";

import {
  describeChange,
  hasSortResult,
  isSortFinished,
  isSortRunning,
  modelNote,
  skippedLine,
  skippedTotal,
  sortActionProgress,
  sortHeadline,
  sortSummary,
  sourceLabel,
  undoableCount,
  undoneCount,
} from "./aiSort.js";

const finished = {
  state: "done",
  run_id: "abc",
  total: 140,
  done: 140,
  unchanged: 96,
  applied: [
    { id: "1", title: "A Happy Death", changes: [{ field: "author", before: "Camus", after: "Albert Camus" }] },
    { id: "2", title: "Walden", changes: [], undone: true },
  ],
  suggestions: [{ id: "s1" }, { id: "s2" }],
  skipped: { "already checked": 12, "edited by you": 3, "a note": 0 },
};

describe("AI sort progress", () => {
  it("shows nothing unless a run is going", () => {
    expect(sortActionProgress(finished)).toBeNull();
    expect(sortActionProgress(null)).toBeNull();
  });

  it("counts books done and names the one being read", () => {
    expect(sortActionProgress({ state: "running", total: 40, done: 10, current: "Walden" })).toEqual({
      label: "AI sort 10 of 40…",
      detail: "Walden",
      percent: 25,
    });
  });

  it("is indeterminate until the server has counted the books", () => {
    expect(sortActionProgress({ state: "running", total: 0, done: 0 }).percent).toBeNull();
  });

  it("tells running, finished and untouched reports apart", () => {
    expect(isSortRunning({ state: "running" })).toBe(true);
    expect(isSortFinished({ state: "interrupted" })).toBe(true);
    expect(isSortFinished({ state: "running" })).toBe(false);
    expect(hasSortResult(finished)).toBe(true);
    expect(hasSortResult({ state: "idle", run_id: null })).toBe(false);
    expect(hasSortResult({ state: "running", run_id: "x" })).toBe(false);
  });
});

describe("AI sort wording", () => {
  it("shows a spelling change as before and after", () => {
    expect(describeChange({ field: "author", before: "Camus", after: "Albert Camus" })).toEqual({
      label: "Author",
      text: "“Camus” → “Albert Camus”",
    });
    expect(describeChange({ field: "author", before: null, after: "Albert Camus" }).text).toBe("none → “Albert Camus”");
  });

  it("reads a fiction verdict and the filled-in details as sentences", () => {
    expect(describeChange({ field: "fiction", before: null, after: "fiction" }).text).toBe("Read as fiction");
    expect(describeChange({ field: "fiction", before: null, after: "non-fiction" }).text).toBe("Read as non-fiction");
    expect(describeChange({ field: "details", before: null, after: "isbn_13, page_count, published_year" }).text).toBe(
      "Filled in ISBN-13, pages, year",
    );
  });

  it("names the kinds a suggestion would move a file between", () => {
    expect(describeChange({ field: "kind", before: "document", after: "book" }).text).toBe("a document → a book");
  });

  it("capitalises where a change came from", () => {
    expect(sourceLabel("title page + catalogue")).toBe("Title page + catalogue");
    expect(sourceLabel("")).toBe("");
  });
});

describe("AI sort summary", () => {
  it("adds up what a run did", () => {
    expect(sortSummary(finished)).toBe("Checked 140 books: 1 changed, 1 put back, 96 left as they were; 2 suggestions to review.");
  });

  it("says so when a stopped run covered only part of the library", () => {
    expect(sortSummary({ run_id: "x", total: 50, done: 20, applied: [], suggestions: [], unchanged: 20 })).toBe(
      "Checked 20 of 50 books: 20 left as they were.",
    );
  });

  it("does not count a suggestion the reader approved as a change the sort made", () => {
    const report = { run_id: "x", total: 4, done: 4, unchanged: 3, applied: [{ via: "auto" }, { via: "review" }], suggestions: [{ id: "s" }] };
    expect(sortSummary(report)).toBe("Checked 4 books: 1 changed, 3 left as they were; 1 suggestion to review.");
  });

  it("says nothing needed changing when it did not", () => {
    expect(sortSummary({ run_id: "x", total: 3, done: 3, applied: [], suggestions: [], unchanged: 0 })).toBe(
      "Checked 3 books; nothing needed changing.",
    );
  });

  it("lists the books it skipped, most first, leaving out empty reasons", () => {
    expect(skippedLine(finished)).toBe("12 already checked · 3 edited by you");
    expect(skippedTotal(finished)).toBe(15);
  });

  it("counts what can still be undone", () => {
    expect(undoableCount(finished)).toBe(1);
    expect(undoneCount(finished)).toBe(1);
  });

  it("has a headline for every state", () => {
    expect(sortHeadline({ state: "cancelled" })).toBe("Sort stopped");
    expect(sortHeadline({ state: "idle" })).toBe("");
  });
});

describe("AI sort model note", () => {
  it("is quiet when a model is available", () => {
    expect(modelNote({ models: ["qwen2.5:7b"], ollamaConfigured: true })).toBe("");
  });

  it("explains why no model is offered", () => {
    expect(modelNote({ models: [], ollamaConfigured: false })).toMatch(/No local model is set up/);
    expect(modelNote({ models: [], ollamaConfigured: true })).toMatch(/did not answer/);
  });
});
