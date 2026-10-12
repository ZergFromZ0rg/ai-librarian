import { describe, expect, it } from "vitest";

import {
  SUPPORTED_UPLOADS,
  apiError,
  shelfAfterTypeChange,
  editedFields,
  fieldTag,
  fileFacts,
  formFromItem,
  groupTypes,
  payloadFromForm,
  saveAllSummary,
  stageSummary,
  uploadMessage,
  validateForm,
} from "./intake.js";

const item = {
  id: "abc",
  format: "pdf",
  format_label: "PDF",
  pages: 312,
  size: 4404019,
  proposal: {
    title: "A Happy Death", subtitle: "", author: "Albert Camus", subject: "", genres: ["Fiction", "Novels"], description: "",
    publisher: "Knopf", published_year: 1972, language: "en", isbn_10: "", isbn_13: "9780306406157", page_count: null,
    type: "novella", shelf: "Books/Albert Camus",
  },
  sources: { title: "file name", author: "file name", publisher: "copyright page", published_year: "copyright page", language: "the text of the file" },
};

describe("intake form", () => {
  it("shows the proposal as the strings the form edits", () => {
    const form = formFromItem(item);
    expect(form).toMatchObject({ title: "A Happy Death", genres: "Fiction, Novels", published_year: "1972", page_count: "", type: "novella", shelf: "Books/Albert Camus" });
  });

  it("sends every field, blanks included, with numbers and genres as the server wants them", () => {
    const form = { ...formFromItem(item), author: "  ", page_count: "", genres: "Fiction,  , Novels " };
    expect(payloadFromForm(form)).toMatchObject({
      title: "A Happy Death", author: "", published_year: 1972, page_count: null, genres: ["Fiction", "Novels"], type: "novella", isbn_10: "",
    });
  });

  it("will not save without a title or with an impossible year, page count or shelf", () => {
    const form = formFromItem(item);
    expect(validateForm(form)).toBe("");
    expect(validateForm({ ...form, title: "  " })).toMatch(/title/);
    expect(validateForm({ ...form, published_year: "12" })).toMatch(/year/);
    expect(validateForm({ ...form, published_year: "19.5" })).toMatch(/year/);
    expect(validateForm({ ...form, page_count: "0" })).toMatch(/page count/);
    expect(validateForm({ ...form, shelf: "a/b/c/d/e/f/g" })).toMatch(/6 levels/);
  });

  it("knows which fields the reader changed", () => {
    const form = { ...formFromItem(item), author: "A. Camus", subject: "Philosophy" };
    expect(editedFields(form, item).sort()).toEqual(["author", "subject"]);
    expect(editedFields(formFromItem(item), item)).toEqual([]);
  });
});

describe("where each value came from", () => {
  const form = formFromItem(item);

  it("says where a found value came from, and nothing for an empty one", () => {
    expect(fieldTag(item, form, "author")).toBe("file name");
    expect(fieldTag(item, form, "publisher")).toBe("copyright page");
    expect(fieldTag(item, form, "subject")).toBe("");
    expect(fieldTag(item, form, "shelf")).toBe("suggested");
  });

  it("calls a changed value edited, whatever it was before", () => {
    expect(fieldTag(item, { ...form, author: "Camus" }, "author")).toBe("edited");
    expect(fieldTag(item, { ...form, subject: "History" }, "subject")).toBe("edited");
  });
});

describe("intake display helpers", () => {
  it("groups the type menu in order", () => {
    const groups = groupTypes([
      { value: "novel", label: "Novel", group: "Books" }, { value: "book", label: "Book", group: "Books" },
      { value: "paper", label: "Paper", group: "Documents" },
    ]);
    expect(groups.map((entry) => [entry.group, entry.options.length])).toEqual([["Books", 2], ["Documents", 1]]);
  });

  it("lists a file's format, size and length", () => {
    expect(fileFacts(item)).toEqual(["PDF", "312 pages", "4.2 MB"]);
    expect(fileFacts({ format: "epub", format_label: "EPUB", pages: 40 })).toEqual(["EPUB", "40 sections"]);
    expect(fileFacts({ format: "text", pages: 1 })).toEqual(["TEXT", "1 page"]);
  });

  it("accepts only the formats the library reads", () => {
    for (const name of ["a.pdf", "b.EPUB", "c.docx", "d.xlsx", "e.pptx", "f.txt", "g.md", "h.csv"]) expect(SUPPORTED_UPLOADS.test(name)).toBe(true);
    for (const name of ["a.exe", "b.doc", "c.pdf.zip"]) expect(SUPPORTED_UPLOADS.test(name)).toBe(false);
  });

  it("tells the reader what became of each upload", () => {
    expect(uploadMessage({ duplicate: { document_id: "x", title: "Walden" } }, "w.pdf")).toBe("w.pdf is already in the library as “Walden”.");
    expect(uploadMessage({ duplicate: { intake_id: "y" } }, "w.pdf")).toBe("w.pdf is already waiting for review.");
    expect(uploadMessage({}, "w.pdf")).toBe("w.pdf is ready to review.");
    expect(stageSummary({ staged: 2, duplicates: [{}], failures: [{ filename: "bad.pdf" }] })).toBe(
      "2 files are ready to review; 1 already in the library or waiting; 1 could not be read (bad.pdf).",
    );
    expect(stageSummary({ staged: 0, duplicates: [], failures: [] })).toBe("Nothing to review.");
    expect(saveAllSummary({ saved: 3, failed: [] })).toBe("Added 3 to the library; indexing has started.");
    expect(saveAllSummary({ saved: 1, failed: [{ title: "X", message: "ISBN invalid" }] })).toContain("X (ISBN invalid)");
  });
});

describe("intake folder and errors", () => {
  const types = [
    { value: "novel", group: "Books", shelf: "Books" }, { value: "nonfiction", group: "Books", shelf: "Books" },
    { value: "report", group: "Documents", shelf: "Reports" }, { value: "manual", group: "Documents", shelf: "Manuals" },
  ];

  it("moves the folder's top level with the type", () => {
    expect(shelfAfterTypeChange("Books/Philosophy/Albert Camus", types, "novel", "nonfiction")).toBe("Books/Philosophy/Albert Camus");
    expect(shelfAfterTypeChange("Reports/Economics", types, "report", "manual")).toBe("Manuals/Economics");
    expect(shelfAfterTypeChange("Books/Philosophy/Albert Camus", types, "novel", "report")).toBe("Reports");
  });

  it("leaves a folder the reader chose themselves", () => {
    expect(shelfAfterTypeChange("My Stuff/Camus", types, "novel", "report")).toBe("My Stuff/Camus");
    expect(shelfAfterTypeChange("Books", types, "novel", "ghost")).toBe("Books");
  });

  it("puts the server's reason into words", () => {
    expect(apiError({ detail: "this file is already in the library" }, 409)).toBe("this file is already in the library");
    expect(apiError({ detail: [{ loc: ["body", "isbn_13"], msg: "Value error, ISBN-13 checksum is invalid" }] }, 422)).toBe("isbn_13: ISBN-13 checksum is invalid");
    expect(apiError({}, 500)).toBe("Request failed (500)");
  });
});
