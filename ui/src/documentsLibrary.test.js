import { describe, expect, it } from "vitest";
import { EMPTY_DOC_FILTERS, docType, filterDocuments, isWorkDocument, typeCounts, unfiledCount } from "./documentsLibrary.js";

const docs = [
  { document_id: "b", title: "A Novel", kind: "book" },
  { document_id: "s", title: "Catalogue only", record_type: "standalone", kind: "document" },
  { document_id: "n", title: "A note", collection_id: "notes", kind: "document" },
  { document_id: "1", title: "Attention is all you need", author: "Vaswani", kind: "paper", doc_type: "paper", published_year: 2017, pages: 15, uploaded_at: "2026-01-02", shelf_suggested: "Papers/Computer Science", indexing_status: "indexed", reading_status: "read" },
  { document_id: "2", title: "Q3 report", kind: "document", doc_type: "report", pages: 40, uploaded_at: "2026-03-01", shelf: "Work/Reports", indexing_status: "indexed", reading_status: "to_read" },
  { document_id: "3", title: "Router manual", filename: "router.pdf", kind: "document", doc_type: "manual", doc_type_override: "report", pages: 90, uploaded_at: "2026-02-01", indexing_status: "indexed" },
  { document_id: "4", title: "Untyped", kind: "document", uploaded_at: "2026-04-01", indexing_status: "queued" },
];

describe("documents library", () => {
  it("includes only working documents, never books, notes, or catalogue-only records", () => {
    expect(docs.filter(isWorkDocument).map((doc) => doc.document_id)).toEqual(["1", "2", "3", "4"]);
  });

  it("prefers the reader's type, and shows unclassified files as other", () => {
    expect(docType(docs[5])).toBe("report");
    expect(docType(docs[6])).toBe("other");
  });

  it("filters by search, type, status and filed state, and sorts", () => {
    const ids = (filters) => filterDocuments(docs, { ...EMPTY_DOC_FILTERS, ...filters }).map((doc) => doc.document_id);
    expect(ids({})).toEqual(["4", "2", "3", "1"]); // newest first
    expect(ids({ query: "vaswani" })).toEqual(["1"]);
    expect(ids({ query: "computer science" })).toEqual(["1"]); // matches the shelf
    expect(ids({ type: "report" })).toEqual(["2", "3"]);
    expect(ids({ status: "read" })).toEqual(["1"]);
    expect(ids({ unfiled: true })).toEqual(["4", "3", "1"]);
    expect(ids({ sort: "title" })).toEqual(["1", "2", "4", "3"].sort((a, b) => docs.find((d) => d.document_id === a).title.localeCompare(docs.find((d) => d.document_id === b).title)));
    expect(ids({ sort: "pages" })).toEqual(["3", "2", "1", "4"]);
    expect(ids({ sort: "year" })[0]).toBe("1");
  });

  it("counts types for the chips without applying the type filter, and counts unfiled indexed files", () => {
    expect(typeCounts(docs, { ...EMPTY_DOC_FILTERS, type: "paper" })).toEqual({ all: 4, paper: 1, report: 2, other: 1 });
    // Search also matches the type label, so the router manual (typed "Report") is found too.
    expect(typeCounts(docs, { ...EMPTY_DOC_FILTERS, query: "report" })).toEqual({ all: 2, report: 2 });
    expect(unfiledCount(docs)).toBe(2); // 1 and 3; the queued one has no text to classify yet
  });
});
