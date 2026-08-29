// Wrap the passage sub-string that actually won retrieval in <mark>, so the
// reader can see which part of a long passage the match hinged on. Whitespace
// is matched flexibly because the stored passage and the retrieval unit are
// wrapped differently; a match split across inline markup is simply left alone.
//
// Returns a rehype plugin factory (`() => (tree) => void`), or null when there
// is nothing safe to highlight (needle too short, or an un-compilable pattern).
export function makeMatchHighlighter(matched) {
  const needle = (matched || "").trim().slice(0, 400);
  if (needle.length < 12) return null;
  const pattern = needle
    .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\s+/g, "\\s+");
  let regex;
  try {
    regex = new RegExp(pattern, "i");
  } catch (_error) {
    return null;
  }
  const splitNode = (value) => {
    const match = regex.exec(value);
    if (!match) return null;
    const start = match.index;
    const end = start + match[0].length;
    const pieces = [];
    if (start > 0) pieces.push({ type: "text", value: value.slice(0, start) });
    pieces.push({
      type: "element",
      tagName: "mark",
      properties: { className: ["passage-match"] },
      children: [{ type: "text", value: value.slice(start, end) }],
    });
    if (end < value.length) pieces.push({ type: "text", value: value.slice(end) });
    return pieces;
  };
  return () => (tree) => {
    let done = false;
    const walk = (node) => {
      if (done || !node.children) return;
      const next = [];
      for (const child of node.children) {
        if (done || child.type !== "text") {
          if (child.type === "element") walk(child);
          next.push(child);
          continue;
        }
        const pieces = splitNode(child.value);
        if (pieces) {
          next.push(...pieces);
          done = true;
        } else {
          next.push(child);
        }
      }
      node.children = next;
    };
    walk(tree);
  };
}
