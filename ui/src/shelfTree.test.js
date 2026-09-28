import { describe, expect, it } from "vitest";

import { allShelfPaths, buildShelfTree, effectiveShelf, isSuggested, onShelf, prettyShelf, UNSORTED } from "./shelfTree.js";

const docs = [
  { document_id: "a", shelf: null, shelf_suggested: "Books/Philosophy/Albert Camus" },
  { document_id: "b", shelf: "Books/Philosophy", shelf_suggested: "Documents" },
  { document_id: "c", shelf: null, shelf_suggested: "Documents/Food & Cooking" },
  { document_id: "d", shelf: null, shelf_suggested: null },
];

describe("shelf tree", () => {
  it("prefers the reader's shelf over the suggestion", () => {
    expect(effectiveShelf(docs[1])).toBe("Books/Philosophy");
    expect(effectiveShelf(docs[3])).toBe(UNSORTED);
    expect(isSuggested(docs[0])).toBe(true);
    expect(isSuggested(docs[1])).toBe(false);
    expect(isSuggested(docs[3])).toBe(false);
  });

  it("matches whole shelf names, including sub-shelves", () => {
    expect(onShelf(docs[0], "Books/Philosophy")).toBe(true);
    expect(onShelf(docs[0], "Books/Phil")).toBe(false);
    expect(onShelf(docs[2], "")).toBe(true);
  });

  it("builds counts that include sub-shelves, Unsorted last", () => {
    const tree = buildShelfTree(docs);
    expect(tree.count).toBe(4);
    expect(tree.children.map((node) => node.name)).toEqual(["Books", "Documents", UNSORTED]);
    const books = tree.children[0];
    expect(books.count).toBe(2);
    expect(books.children[0].path).toBe("Books/Philosophy");
    expect(books.children[0].children[0]).toMatchObject({ name: "Albert Camus", count: 1 });
  });

  it("lists every shelf path for suggestions, without Unsorted", () => {
    expect(allShelfPaths(docs)).toEqual([
      "Books",
      "Books/Philosophy",
      "Books/Philosophy/Albert Camus",
      "Documents",
      "Documents/Food & Cooking",
    ]);
    expect(prettyShelf("Books/Philosophy")).toBe("Books › Philosophy");
  });
});
