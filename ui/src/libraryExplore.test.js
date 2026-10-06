import { describe, expect, it } from "vitest";
import { facetValues, filterLibrary, libraryBooks } from "./libraryExplore.js";

const documents = [
  { document_id: "a", title: "Dune", author: "Frank Herbert", kind: "book", owned: 1, file_type: "epub", reading_status: "read", rating: 5, genres_json: '["Science Fiction"]', finished_at: "2026-01-02", indexing_status: "indexed" },
  { document_id: "b", title: "The Trial", author: "Franz Kafka", kind: "book", owned: 0, file_type: "pdf", reading_status: "reading", rating: 4, genres_json: '["Fiction", "Classics"]', indexing_status: "indexed" },
  { document_id: "c", title: "Loose note", kind_override: "note", collection_id: "notes", file_type: "markdown", indexing_status: "indexed" },
  { document_id: "d", title: "Invisible Cities", author: "Italo Calvino", kind: "book", record_type: "standalone", reading_status: "to_read", indexing_status: "catalogued" },
];

describe("library explore", () => {
  it("excludes notes and combines facets", () => {
    expect(libraryBooks(documents).map((doc) => doc.document_id)).toEqual(["a", "b", "d"]);
    expect(filterLibrary(documents, { query: "franz", status: "reading" }).map((doc) => doc.document_id)).toEqual(["b"]);
    expect(filterLibrary(documents, { format: "catalogue" }).map((doc) => doc.document_id)).toEqual(["d"]);
    expect(filterLibrary(documents, { ownership: "unowned", rating: "4" }).map((doc) => doc.document_id)).toEqual(["b"]);
  });

  it("extracts sorted formats and genres", () => {
    expect(facetValues(documents)).toEqual({
      authors: ["Frank Herbert", "Franz Kafka", "Italo Calvino"],
      finishedYears: ["2026"],
      formats: ["catalogue", "epub", "pdf"],
      genres: ["Classics", "Fiction", "Science Fiction"],
    });
  });
});

import { EMPTY_FILTERS, filterLabels, searchWithView, viewFromSearch, viewToParams } from "./libraryExplore.js";

const shelf = [
  { document_id: "a", title: "Dune", kind: "book", rating: 5, page_count: 600, finished_at: "2026-01-02", note_count: 2, file_type: "epub", reading_status: "read" },
  { document_id: "b", title: "The Trial", kind: "book", rating: 3, page_count: 250, note_count: 0, file_type: "pdf", reading_status: "reading" },
  { document_id: "c", title: "Piranesi", kind: "book", file_type: "epub", reading_status: "to_read" },
  { document_id: "d", title: "Emma", kind: "book", rating: 4, page_count: 400, finished_at: "2026-03-09", note_count: 1, file_type: "pdf", reading_status: "read" },
];
const ids = (filters, notes) => filterLibrary(shelf, filters, notes).map((doc) => doc.document_id);

describe("notes in the library", () => {
  it("filters to books with or without notes", () => {
    expect(ids({ notes: "with", sort: "title" })).toEqual(["a", "d"]);
    expect(ids({ notes: "without", sort: "title" })).toEqual(["c", "b"]);
  });

  it("finds a book by what you wrote about it", () => {
    const written = new Map([["b", "Kafka's bureaucratic nightmare, reread in winter"]]);
    expect(ids({ query: "nightmare" }, written)).toEqual(["b"]);
    expect(ids({ query: "nightmare" })).toEqual([]);
  });
});

describe("sorting for the ledger", () => {
  it("starts numbers and dates with the biggest, and text with A-Z", () => {
    expect(ids({ sort: "rating" })).toEqual(["a", "d", "b", "c"]);
    expect(ids({ sort: "pages" })).toEqual(["a", "d", "b", "c"]);
    expect(ids({ sort: "finished" })).toEqual(["d", "a", "c", "b"]); // unfinished books last, by title
    expect(ids({ sort: "title" })).toEqual(["a", "d", "c", "b"]);
  });

  it("reverses without ever moving books with no value ahead of real ones", () => {
    expect(ids({ sort: "rating", order: "reverse" })).toEqual(["b", "d", "a", "c"]);
    expect(ids({ sort: "pages", order: "reverse" })).toEqual(["b", "d", "a", "c"]);
    expect(ids({ sort: "title", order: "reverse" })).toEqual(["b", "c", "d", "a"]);
  });

  it("sorts by status and by notes, with ties broken by title", () => {
    expect(ids({ sort: "status" })).toEqual(["b", "c", "a", "d"]);
    expect(ids({ sort: "notes" })).toEqual(["a", "d", "c", "b"]);
  });
});

describe("shareable views", () => {
  it("writes only what differs from the defaults and reads it back", () => {
    expect(viewToParams(EMPTY_FILTERS).toString()).toBe("");
    const filters = { ...EMPTY_FILTERS, status: "read", notes: "with", query: "winter reads", sort: "rating", order: "reverse" };
    const text = viewToParams(filters, "ledger").toString();
    expect(text).toContain("lib.status=read");
    expect(viewFromSearch(`?${text}`)).toEqual({ filters, layout: "ledger" });
  });

  it("ignores unknown, oversized, and malformed values", () => {
    const search = `?lib.sort=bogus&lib.order=sideways&lib.view=cards&lib.q=${"x".repeat(300)}&lib.status=reading`;
    const view = viewFromSearch(search);
    expect(view.filters).toEqual({ ...EMPTY_FILTERS, status: "reading" });
    expect(view.layout).toBe("list");
  });

  it("replaces only its own parameters in an address", () => {
    expect(searchWithView("?foo=1&lib.status=read", { ...EMPTY_FILTERS, notes: "with" }, "list")).toBe("?foo=1&lib.notes=with");
    expect(searchWithView("?lib.status=read", EMPTY_FILTERS, "list")).toBe("");
  });

  it("describes active constraints in words", () => {
    expect(filterLabels({ ...EMPTY_FILTERS, status: "read", notes: "with", query: "x" })).toEqual(["“x”", "Status: Read", "Has notes"]);
    expect(filterLabels(EMPTY_FILTERS)).toEqual([]);
  });
});
