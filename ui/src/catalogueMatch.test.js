import { describe, expect, it } from "vitest";
import { applyBody, bodyForLoggedBook, confidentMatch, findLoggedBook, fullBody, lookupQuery, looksLikeIsbn, matchRows, providerName } from "./catalogueMatch.js";

const candidate = {
  id: "/works/OL1W", title: "The Stranger", subtitle: null, author: "Albert Camus", publisher: "Penguin",
  published_year: 1942, page_count: 123, language: "en", genres: ["Fiction"], isbn_13: "9780679720201",
  isbn_10: null, cover_id: "4242",
};

describe("catalogue matches", () => {
  it("recognises ISBNs typed with hyphens or spaces", () => {
    expect(looksLikeIsbn("978-0-679-72020-1")).toBe(true);
    expect(looksLikeIsbn("0 679 72020 0")).toBe(true);
    expect(looksLikeIsbn("the stranger")).toBe(false);
  });

  it("ticks only fields the book has no value for, and skips identical ones", () => {
    const rows = matchRows({ title: "stranger", author: "", publisher: "Penguin", genres_json: "[]" }, candidate);
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row]));
    expect(byKey.title).toMatchObject({ checked: false, current: "stranger", suggested: "The Stranger" });
    expect(byKey.author.checked).toBe(true);
    expect(byKey.publisher).toBeUndefined();
    expect(byKey.subtitle).toBeUndefined();
    expect(byKey.genres.suggested).toBe("Fiction");
  });

  it("sends only ticked fields, and the cover only when asked", () => {
    const rows = matchRows({ title: "stranger", author: "" }, candidate);
    expect(applyBody(rows, {}, candidate, false)).toEqual(expect.objectContaining({ provider_id: "/works/OL1W" }));
    const body = applyBody(rows, { title: true, author: false }, candidate, true);
    expect(body.fields.title).toBe("The Stranger");
    expect(body.fields.author).toBeUndefined();
    expect(body.cover_id).toBe("4242");
    expect(applyBody(rows, {}, candidate, false).cover_id).toBeUndefined();
  });

  it("builds a complete body for a new book", () => {
    const body = fullBody(candidate);
    expect(body.fields).toMatchObject({ title: "The Stranger", genres: ["Fiction"], isbn_13: "9780679720201" });
    expect(body.fields.isbn_10).toBeUndefined();
  });
});

describe("logging a book by title and author", () => {
  const dune = { id: "/works/OL2W", title: "Dune", author: "Frank Herbert", score: 0.93, page_count: 412, published_year: 1965, cover_id: "77" };

  it("searches for the title and the author together", () => {
    expect(lookupQuery("Dune", "Frank Herbert")).toBe("Dune Frank Herbert");
    expect(lookupQuery("  Dune ", "")).toBe("Dune");
    expect(lookupQuery("", null)).toBe("");
  });

  it("trusts a clear best match, and nothing vaguer", () => {
    expect(confidentMatch([dune, { ...dune, id: "x", title: "Dune Messiah", score: 0.6 }])).toBe(dune);
    expect(confidentMatch([{ ...dune, score: 0.7 }])).toBeNull();
    expect(confidentMatch([dune, { ...dune, id: "y", title: "Dune: The Graphic Novel", score: 0.91 }])).toBeNull();
    // The same book listed twice is not a tie.
    expect(confidentMatch([dune, { ...dune, id: "z", score: 0.92 }])).toBe(dune);
    expect(confidentMatch([])).toBeNull();
    expect(confidentMatch(undefined)).toBeNull();
  });

  it("keeps what was typed unless the match only writes it better", () => {
    expect(bodyForLoggedBook(dune, { title: "dune", author: "frank herbert" }).fields).toMatchObject({ title: "Dune", author: "Frank Herbert", page_count: 412 });
    // A different wording of the title is not replaced.
    expect(bodyForLoggedBook(dune, { title: "Dune (my copy)", author: "" }).fields.title).toBeUndefined();
    expect(bodyForLoggedBook(dune, { title: "Dune", author: "" }).fields.author).toBe("Frank Herbert");
    // A lone surname is completed; a different author is left alone.
    expect(bodyForLoggedBook(dune, { title: "Dune", author: "Herbert" }).fields.author).toBe("Frank Herbert");
    expect(bodyForLoggedBook(dune, { title: "Dune", author: "Brian Herbert" }).fields.author).toBeUndefined();
    expect(bodyForLoggedBook(dune, { title: "Dune", author: "Frank Herbert" }).cover_id).toBe("77");
  });

  it("spots a book already on the shelf", () => {
    const shelf = [{ document_id: "a", title: "Dune", author: "Frank Herbert" }, { document_id: "b", filename: "The Stranger.pdf", title: "The Stranger" }, { document_id: "n", title: "Dune", collection_id: "notes" }];
    expect(findLoggedBook(shelf, " dune ", "Frank Herbert")?.document_id).toBe("a");
    expect(findLoggedBook(shelf, "DUNE", "")?.document_id).toBe("a");
    expect(findLoggedBook(shelf, "Dune", "Someone Else")).toBeNull();
    expect(findLoggedBook(shelf, "The Stranger", "Albert Camus")?.document_id).toBe("b");
    expect(findLoggedBook(shelf, "", "x")).toBeNull();
  });

  it("names the provider", () => {
    expect(providerName({ id: "gb:abc123" })).toBe("Google Books");
    expect(providerName({ id: "/works/OL1W" })).toBe("Open Library");
  });
});
