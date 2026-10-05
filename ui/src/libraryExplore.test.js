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
