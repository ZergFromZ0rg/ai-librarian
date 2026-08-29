import { describe, expect, it } from "vitest";

import { makeMatchHighlighter } from "./highlight.js";

// Build a minimal hast (rehype) tree and run the plugin over it.
const root = (...children) => ({ type: "root", children });
const text = (value) => ({ type: "text", value });
const el = (tagName, ...children) => ({ type: "element", tagName, properties: {}, children });

function run(matched, tree) {
  const factory = makeMatchHighlighter(matched);
  if (!factory) return null;
  factory()(tree); // plugin factory -> transformer -> mutate tree
  return tree;
}

function marks(node, found = []) {
  for (const child of node.children || []) {
    if (child.type === "element" && child.tagName === "mark") {
      found.push(child.children.map((c) => c.value).join(""));
    }
    marks(child, found);
  }
  return found;
}

describe("makeMatchHighlighter", () => {
  it("returns null for a needle shorter than 12 characters", () => {
    expect(makeMatchHighlighter("short")).toBe(null);
    expect(makeMatchHighlighter("   spaced   ")).toBe(null);
    expect(makeMatchHighlighter(null)).toBe(null);
    expect(makeMatchHighlighter(undefined)).toBe(null);
  });

  it("returns a usable plugin factory for a long-enough needle", () => {
    const factory = makeMatchHighlighter("a sufficiently long phrase");
    expect(typeof factory).toBe("function");
    expect(typeof factory()).toBe("function");
  });

  it("wraps the matched substring in <mark class='passage-match'>", () => {
    const tree = root(text("before the winning phrase appears after"));
    run("the winning phrase", tree);

    expect(tree.children.map((c) => c.type)).toEqual(["text", "element", "text"]);
    const mark = tree.children[1];
    expect(mark.tagName).toBe("mark");
    expect(mark.properties.className).toEqual(["passage-match"]);
    expect(mark.children[0].value).toBe("the winning phrase");
    expect(tree.children[0].value).toBe("before ");
    expect(tree.children[2].value).toBe(" appears after");
  });

  it("matches whitespace flexibly across newlines and runs of spaces", () => {
    const tree = root(text("intro\nthe   winning\nphrase\nrest"));
    run("the winning phrase", tree);
    expect(marks(tree)).toEqual(["the   winning\nphrase"]);
  });

  it("is case-insensitive", () => {
    const tree = root(text("THE WINNING PHRASE in caps"));
    run("the winning phrase", tree);
    expect(marks(tree)).toEqual(["THE WINNING PHRASE"]);
  });

  it("only marks the first occurrence", () => {
    const tree = root(
      text("the winning phrase once, "),
      text("and the winning phrase twice"),
    );
    run("the winning phrase", tree);
    expect(marks(tree)).toEqual(["the winning phrase"]);
  });

  it("leaves a match split across inline markup untouched", () => {
    const tree = root(text("the winning "), el("em", text("phrase")), text(" here"));
    run("the winning phrase", tree);
    expect(marks(tree)).toEqual([]);
    expect(tree.children.map((c) => c.type)).toEqual(["text", "element", "text"]);
  });

  it("descends into nested elements to find the match", () => {
    const tree = root(el("p", el("strong", text("the winning phrase lives deep"))));
    run("the winning phrase", tree);
    expect(marks(tree)).toEqual(["the winning phrase"]);
  });

  it("does not throw on regex metacharacters in the needle", () => {
    const needle = "cost is $5 (approx.) + tax [ok]";
    const tree = root(text(`note: ${needle} today`));
    expect(() => run(needle, tree)).not.toThrow();
    expect(marks(tree)).toEqual([needle]);
  });

  it("makes no change when the needle is absent from the tree", () => {
    const tree = root(text("nothing relevant to see in this passage"));
    const before = JSON.stringify(tree);
    run("a phrase that is not present", tree);
    expect(JSON.stringify(tree)).toBe(before);
  });
});
