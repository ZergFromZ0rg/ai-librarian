import { describe, expect, it } from "vitest";
import { bookNotes, plainPreview, relatedBooks } from "./peek.js";

const books = [
  { document_id: "a", title: "The Myth of Sisyphus", author: "Albert Camus", kind: "book", genres_json: '["Essays","Philosophy"]', shelf: "Books/Philosophy" },
  { document_id: "b", title: "The Stranger", author: "albert camus", kind: "book", genres_json: '["Novel"]', shelf: "Books/Fiction" },
  { document_id: "c", title: "Being and Nothingness", author: "Jean-Paul Sartre", kind: "book", genres_json: '["Philosophy"]', shelf: "Books/Philosophy" },
  { document_id: "d", title: "Dune", author: "Frank Herbert", kind: "book", genres_json: '["Science fiction"]', shelf: "Books/Fiction" },
  { document_id: "e", title: "Cosmos", author: "Carl Sagan", kind: "book" },
  { document_id: "n", title: "A note", kind_override: "note", collection_id: "notes", author: "Albert Camus" },
];

describe("related books", () => {
  it("ranks the same author first, then shared genres, then the same shelf", () => {
    expect(relatedBooks(books[0], books).map((doc) => doc.document_id)).toEqual(["b", "c"]);
  });

  it("leaves out the book itself, notes and books that share nothing", () => {
    const ids = relatedBooks(books[4], books).map((doc) => doc.document_id);
    expect(ids).toEqual([]);
    expect(relatedBooks(books[3], books).map((doc) => doc.document_id)).toEqual(["b"]);
  });

  it("stops at the limit", () => {
    expect(relatedBooks(books[0], books, 1)).toHaveLength(1);
  });
});

describe("note previews", () => {
  it("shows Markdown as plain text", () => {
    expect(plainPreview("## Heading\n\n**Bold** and _italic_ with a [link](http://x.y).\n- one\n- two")).toBe("Heading Bold and italic with a link. one two");
  });

  it("cuts a long note with an ellipsis", () => {
    const text = plainPreview("word ".repeat(100), 40);
    expect(text.length).toBeLessThanOrEqual(40);
    expect(text.endsWith("…")).toBe(true);
  });

  it("copes with nothing", () => {
    expect(plainPreview(null)).toBe("");
  });
});

describe("a book's notes and quotes", () => {
  const notes = [
    { document_id: "1", source_document_id: "a", text: "My thoughts", source_quote: "One must imagine Sisyphus happy.", source_page: 12 },
    { document_id: "2", source_document_id: "a", text: "Saved passage", source_quote: "The struggle itself.", source_page: 31 },
    { document_id: "3", source_document_id: "a", text: "A plain note" },
    { document_id: "4", source_document_id: "b", text: "Another book" },
  ];

  it("separates what you wrote from the passages you kept, for this book only", () => {
    const { comments, quotes } = bookNotes(notes, "a");
    expect(comments.map((note) => note.document_id)).toEqual(["1", "3"]);
    expect(quotes.map((note) => note.document_id)).toEqual(["1", "2"]);
  });

  it("has nothing for a book with no notes", () => {
    expect(bookNotes(notes, "z")).toEqual({ comments: [], quotes: [] });
    expect(bookNotes(undefined, "a")).toEqual({ comments: [], quotes: [] });
  });
});
