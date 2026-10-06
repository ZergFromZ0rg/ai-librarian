import { describe, expect, it } from "vitest";
import { applyBody, fullBody, looksLikeIsbn, matchRows } from "./catalogueMatch.js";

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
