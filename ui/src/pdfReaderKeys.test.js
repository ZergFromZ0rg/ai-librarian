import { describe, expect, it } from "vitest";
import { FIND_STATE, findSummary, shortcutFor } from "./pdfReaderKeys.js";

const key = (name, extra = {}) => ({ key: name, target: { tagName: "DIV" }, ...extra });

describe("shortcutFor", () => {
  it("takes Ctrl/Cmd+F for the reader's own search, from anywhere", () => {
    expect(shortcutFor(key("f", { ctrlKey: true }))).toBe("find");
    expect(shortcutFor(key("F", { metaKey: true }))).toBe("find");
    expect(shortcutFor(key("f", { ctrlKey: true, target: { tagName: "INPUT" } }))).toBe("find");
    expect(shortcutFor(key("f", { ctrlKey: true, altKey: true }))).toBe(null);
  });

  it("pages with j and k and opens search with /", () => {
    expect(shortcutFor(key("j"))).toBe("next-page");
    expect(shortcutFor(key("k"))).toBe("previous-page");
    expect(shortcutFor(key("/"))).toBe("find");
  });

  it("leaves single keys alone while the reader is typing, or with a modifier held", () => {
    for (const target of [{ tagName: "INPUT" }, { tagName: "TEXTAREA" }, { tagName: "DIV", isContentEditable: true }]) {
      expect(shortcutFor(key("j", { target }))).toBe(null);
      expect(shortcutFor(key("/", { target }))).toBe(null);
    }
    expect(shortcutFor(key("j", { ctrlKey: true }))).toBe(null);
    expect(shortcutFor(key("k", { metaKey: true }))).toBe(null);
    expect(shortcutFor(key("j", { altKey: true }))).toBe(null);
    expect(shortcutFor(key("x"))).toBe(null);
  });

  it("reports Escape even from a box, so the search can be dismissed", () => {
    expect(shortcutFor(key("Escape", { target: { tagName: "INPUT" } }))).toBe("escape");
  });
});

describe("findSummary", () => {
  it("says nothing until something is typed", () => {
    expect(findSummary("", { state: FIND_STATE.FOUND, current: 1, total: 4 })).toBe("");
    expect(findSummary("   ", {})).toBe("");
  });

  it("shows the position among the matches", () => {
    expect(findSummary("moon", { state: FIND_STATE.FOUND, current: 2, total: 17 })).toBe("2 of 17");
    expect(findSummary("moon", { state: FIND_STATE.WRAPPED, current: 1, total: 17 })).toBe("1 of 17");
  });

  it("counts matches before one is selected, and while still searching", () => {
    expect(findSummary("moon", { state: FIND_STATE.FOUND, current: 0, total: 1 })).toBe("1 match");
    expect(findSummary("moon", { state: FIND_STATE.FOUND, current: 0, total: 3 })).toBe("3 matches");
    expect(findSummary("moon", { state: FIND_STATE.PENDING, current: 0, total: 0 })).toBe("Searching…");
    expect(findSummary("moon", { state: FIND_STATE.PENDING, current: 1, total: 5 })).toBe("1 of 5…");
  });

  it("says so when nothing matches", () => {
    expect(findSummary("zzz", { state: FIND_STATE.NOT_FOUND, current: 0, total: 0 })).toBe("No matches");
    expect(findSummary("zzz", { state: FIND_STATE.FOUND, current: 0, total: 0 })).toBe("No matches");
  });
});
