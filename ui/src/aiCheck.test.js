import { describe, expect, it } from "vitest";

import { appliedCount, checkNotes, checkSummary, describeChange, modelNote, parseOptions, sourceLabel } from "./aiCheck.js";

describe("AI check wording", () => {
  it("shows a spelling change as before and after", () => {
    expect(describeChange({ field: "author", before: "Camus", after: "Albert Camus" })).toEqual({
      label: "Author",
      text: "“Camus” → “Albert Camus”",
    });
    expect(describeChange({ field: "author", before: null, after: "Albert Camus" }).text).toBe("none → “Albert Camus”");
  });

  it("offers filled-in details in plain words", () => {
    expect(describeChange({ field: "details", after: "isbn_13, page_count, published_year" })).toEqual({
      label: "Details",
      text: "Fill in ISBN-13, pages, year",
    });
  });

  it("names the form a book would take", () => {
    expect(describeChange({ field: "form", before: null, after: "novella" })).toEqual({ label: "Form", text: "not decided → Novella" });
    expect(describeChange({ field: "form", before: "novel", after: "short_story" }).text).toBe("Novel → Short story");
  });

  it("names the kinds a change would move a file between", () => {
    expect(describeChange({ field: "kind", before: "document", after: "book" }).text).toBe("a document → a book");
  });

  it("capitalises where a change came from", () => {
    expect(sourceLabel("title page + catalogue")).toBe("Title page + catalogue");
    expect(sourceLabel("")).toBe("");
  });
});

describe("AI check summary", () => {
  it("says what a check found and how it looked", () => {
    expect(checkSummary({ run_id: "a", model: "qwen2.5:7b", options: { online: true }, suggestions: [{}, {}] })).toBe(
      "Checked with qwen2.5:7b and the online catalogue: 2 changes suggested.",
    );
    expect(checkSummary({ run_id: "a", model: "qwen2.5:7b", options: { online: false }, suggestions: [{}] })).toBe(
      "Checked with qwen2.5:7b: 1 change suggested.",
    );
    expect(checkSummary({ run_id: "a", model: null, options: { online: true }, suggestions: [] })).toBe(
      "Checked with the online catalogue: nothing to change.",
    );
    expect(checkSummary({ run_id: "a", model: null, options: {}, suggestions: [] })).toBe("Checked by the rules alone: nothing to change.");
    expect(checkSummary(null)).toBe("");
  });

  it("counts what can still be undone", () => {
    expect(appliedCount({ applied: [{ undone: true }, {}, {}] })).toBe(2);
    expect(appliedCount(null)).toBe(0);
  });

  it("passes the notes on and explains an empty model list", () => {
    expect(checkNotes({ notes: ["The local model did not answer.", ""] })).toEqual(["The local model did not answer."]);
    expect(modelNote({ models: ["qwen2.5:7b"] })).toBe("");
    expect(modelNote({ models: [], ollamaConfigured: false })).toMatch(/No local model is set up/);
    expect(modelNote({ models: [], ollamaConfigured: true })).toMatch(/did not answer/);
  });

  it("reads saved options safely, with the catalogue off unless it was chosen", () => {
    expect(parseOptions(null)).toEqual({ online: false, model: "" });
    expect(parseOptions({ online: true, model: "qwen2.5:7b" })).toEqual({ online: true, model: "qwen2.5:7b" });
    expect(parseOptions({ online: "yes", model: 5 })).toEqual({ online: false, model: "" });
  });
});
