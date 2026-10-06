import { describe, expect, it } from "vitest";

import { cleanWord, contextAround, isSingleWord, markAt, selectionRects, selectionText, simplifyStroke, wordAt } from "./annotations.js";

// Two lines of three words each, in reading order.
const words = [
  [0.1, 0.1, 0.2, 0.12, "The"],
  [0.22, 0.1, 0.4, 0.12, "quixotic"],
  [0.42, 0.1, 0.5, 0.12, "plan,"],
  [0.1, 0.14, 0.2, 0.16, "met"],
  [0.22, 0.14, 0.3, 0.16, "an"],
  [0.32, 0.14, 0.5, 0.16, "ephemeral"],
];

describe("cleanWord / isSingleWord", () => {
  it("strips punctuation and rejects non-words", () => {
    expect(cleanWord("“Serendipity,”")).toBe("Serendipity");
    expect(cleanWord("don’t.")).toBe("don't");
    expect(cleanWord("1984")).toBeNull();
    expect(cleanWord("—")).toBeNull();
    expect(isSingleWord("plan,")).toBe(true);
    expect(isSingleWord("two words")).toBe(false);
  });
});

describe("wordAt", () => {
  it("finds the word under a point, with a little slack", () => {
    expect(wordAt(words, 0.3, 0.11)).toBe(1);
    expect(wordAt(words, 0.401, 0.11)).toBe(1);
    expect(wordAt(words, 0.9, 0.9)).toBe(-1);
  });
});

describe("selectionRects / selectionText", () => {
  it("merges a run into one rect per line, whichever way it was dragged", () => {
    expect(selectionRects(words, 1, 2)).toEqual([[0.22, 0.1, 0.5, 0.12]]);
    expect(selectionRects(words, 5, 1)).toEqual([
      [0.22, 0.1, 0.5, 0.12],
      [0.1, 0.14, 0.5, 0.16],
    ]);
    expect(selectionText(words, 2, 0)).toBe("The quixotic plan,");
  });

  it("clamps out-of-range indexes", () => {
    expect(selectionRects(words, -3, 0)).toEqual([[0.1, 0.1, 0.2, 0.12]]);
    expect(selectionText(words, 5, 99)).toBe("ephemeral");
  });
});

describe("contextAround", () => {
  it("marks truncation only where text was cut", () => {
    expect(contextAround(words, 1, 10)).toBe("The quixotic plan, met an ephemeral");
    expect(contextAround(words, 3, 1)).toBe("… plan, met an …");
  });
});

describe("markAt", () => {
  const marks = [
    { id: "a", kind: "highlight", rects: [[0.1, 0.1, 0.5, 0.12]] },
    { id: "b", kind: "ink", paths: [[[0.6, 0.6], [0.8, 0.6]]] },
  ];
  it("hits rect marks, ink strokes near the line, and misses elsewhere", () => {
    expect(markAt(marks, 0.3, 0.11).id).toBe("a");
    expect(markAt(marks, 0.7, 0.605).id).toBe("b");
    expect(markAt(marks, 0.7, 0.7)).toBeNull();
  });
  it("prefers the mark drawn last", () => {
    const stacked = [...marks, { id: "c", kind: "underline", rects: [[0.1, 0.1, 0.5, 0.12]] }];
    expect(markAt(stacked, 0.3, 0.11).id).toBe("c");
  });
});

describe("simplifyStroke", () => {
  it("drops points closer than the gap but keeps both ends", () => {
    const line = Array.from({ length: 200 }, (_, i) => [i * 0.0001, 0.5]);
    const thin = simplifyStroke(line);
    expect(thin.length).toBeLessThan(40);
    expect(thin[0]).toEqual([0, 0.5]);
    expect(thin[thin.length - 1][0]).toBeCloseTo(0.0199, 4);
  });
});
