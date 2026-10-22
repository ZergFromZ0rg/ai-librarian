import { bookShelf, genresFor, libraryBooks } from "./libraryExplore.js";

const same = (a, b) => Boolean(a) && Boolean(b) && String(a).trim().toLocaleLowerCase() === String(b).trim().toLocaleLowerCase();

// Books worth opening next to this one, best first: the same author counts most,
// then each genre they share, then standing on the same shelf. A book that shares
// nothing is never "related", so a small library may show few or none.
export function relatedBooks(doc, documents, limit = 6) {
  const genres = new Set(genresFor(doc).map((genre) => genre.toLocaleLowerCase()));
  const shelf = bookShelf(doc);
  const title = (book) => book.title || book.filename || "";
  return libraryBooks(documents)
    .filter((other) => other.document_id !== doc.document_id)
    .map((other) => {
      let score = 0;
      if (same(doc.author, other.author)) score += 5;
      for (const genre of genresFor(other)) if (genres.has(genre.toLocaleLowerCase())) score += 2;
      if (shelf && same(shelf, bookShelf(other))) score += 1.5;
      return { other, score };
    })
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score || title(a.other).localeCompare(title(b.other)))
    .slice(0, limit)
    .map((entry) => entry.other);
}

// A note's Markdown as one plain line of text, for a preview: no heading marks,
// emphasis, links, list bullets or code fences.
export function plainPreview(markdown, max = 220) {
  const text = String(markdown || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

// What a book's own notes hold: the reader's commentary, and the passages they
// kept (with the page). A passage saved with no comment of its own carries the
// placeholder text "Saved passage", which is not shown as a note.
export function bookNotes(notes, documentId) {
  const mine = (notes || []).filter((note) => note.source_document_id === documentId);
  return {
    comments: mine.filter((note) => (note.text || "").trim() && (note.text || "").trim() !== "Saved passage"),
    quotes: mine.filter((note) => (note.source_quote || "").trim()),
  };
}
