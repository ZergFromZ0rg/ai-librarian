import React from "react";
import { renderToString } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";

import DocumentsLibrary from "./DocumentsLibrary.jsx";
import LibraryExplore from "./LibraryExplore.jsx";
import LibraryRail from "./LibraryRail.jsx";
import LibraryStats from "./LibraryStats.jsx";
import ReaderLoading from "./ReaderLoading.jsx";
import { EMPTY_FILTERS, facetValues, filterChips, filterLabels, filterLibrary, shelfList } from "./libraryExplore.js";

// Records as the server really sends them have nulls and gaps (a catalogue book
// has no file, a fresh upload has no author), and a page must show them, not
// throw. Rendering to a string exercises every component's render path.
const gappy = [
  { document_id: "a", filename: "a.pdf", title: null, author: null, kind: "book", file_type: "pdf", genres_json: null, shelf: null, shelf_suggested: null, page_count: null, pages: null, finished_at: null, rating: null, note_count: null, reading_status: null, reading_progress: null },
  { document_id: "b", filename: "b", title: "", author: "", kind: "book", record_type: "standalone", file_type: "book", genres_json: "[]", subject: null, shelf: "", shelf_suggested: "Books", published_year: null, isbn_10: null, isbn_13: null, uploaded_at: null, indexing_status: "catalogued" },
  { document_id: "c", filename: "c.epub", title: "C", kind: "book", file_type: "epub", genres_json: "not json", shelf: "Books/Philosophy/", shelf_suggested: "//", reading_status: "read", finished_at: "2026-03-04", page_count: "300", rating: 4.5, liked: 1, reading_progress: "abc" },
  { document_id: "d", filename: "d.pdf", kind: "paper", file_type: "pdf", doc_type: null, shelf: null },
  { document_id: "e", filename: "e.pdf", kind: "book", file_type: "pdf", genres_json: '[null, 3, "Fiction", ""]', shelf: "Books/Fiction", reading_status: "reading", reading_progress: 40, current_page: null, last_read_at: null, updated_at: null },
  { document_id: "n", filename: "note.md", title: "A note", collection_id: "notes", kind: "note", file_type: "markdown" },
];
const noNotes = [{ document_id: "n1", text: null, source_document_id: "a", source_quote: null }, { document_id: "n2", text: "x", source_document_id: "gone", source_quote: "q", source_page: null }, {}];

beforeAll(() => {
  const store = new Map();
  globalThis.window = {
    location: { search: "", pathname: "/", hash: "" },
    history: { state: null, replaceState() {}, pushState() {} },
    localStorage: { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value)) },
    matchMedia: () => ({ matches: false }),
  };
});

describe("pages survive records with gaps", () => {
  it("filters, sorts, counts and lists shelves", () => {
    for (const sort of ["title", "author", "status", "rating", "format", "pages", "notes", "added", "recent", "finished"]) {
      expect(() => filterLibrary(gappy, { ...EMPTY_FILTERS, sort }, new Map())).not.toThrow();
    }
    expect(() => filterLibrary(gappy, { ...EMPTY_FILTERS, query: "phil", genre: "Fiction", shelf: "Philosophy", status: "read" })).not.toThrow();
    // A genre list with something other than text in it is read as the text it holds.
    expect(filterLibrary(gappy, { ...EMPTY_FILTERS, genre: "Fiction" }).map((doc) => doc.document_id)).toEqual(["e"]);
    expect(() => facetValues(gappy)).not.toThrow();
    expect(() => shelfList(gappy)).not.toThrow();
    expect(() => filterChips({ ...EMPTY_FILTERS, genre: "x", collection: "nope" }, [])).not.toThrow();
    expect(() => filterLabels(EMPTY_FILTERS)).not.toThrow();
  });

  it("renders the Library, the side summary and the statistics", () => {
    const common = { apiBase: "/api", documents: gappy, collections: [], onOpenDocument() {}, onNavigate() {} };
    expect(renderToString(<LibraryExplore {...common} />)).toContain("Your reading");
    expect(renderToString(<LibraryRail apiBase="/api" documents={gappy} notes={noNotes} status="all" onStatus={() => {}} onNavigate={() => {}} onOpenDocument={() => {}} />)).toContain("Books");
    expect(() => renderToString(<LibraryStats documents={gappy} collections={[]} onExplore={() => {}} />)).not.toThrow();
  });

  it("renders an empty library and the Documents page", () => {
    expect(renderToString(<LibraryExplore apiBase="/api" documents={[]} collections={[]} loaded onOpenDocument={() => {}} onNavigate={() => {}} />)).toContain("first shelf");
    expect(renderToString(<LibraryExplore apiBase="/api" documents={[]} collections={[]} loaded={false} onOpenDocument={() => {}} onNavigate={() => {}} />)).not.toContain("first shelf");
    expect(() => renderToString(<DocumentsLibrary apiBase="/api" documents={gappy} onOpenDocument={() => {}} onChanged={() => {}} />)).not.toThrow();
  });
});

describe("the reader's loading screen", () => {
  it("always offers a way out", () => {
    const html = renderToString(<ReaderLoading title="Some Book" onClose={() => {}} />);
    expect(html).toContain("Close");
    expect(html).toContain("Some Book");
    expect(html).toContain("Opening the reader");
  });
});
