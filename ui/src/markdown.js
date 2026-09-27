// Shared Markdown setup for passages, answers and the text viewer: GitHub
// tables plus typeset math.
//
// Passages carry math in two different shapes, and only one is real LaTeX:
//   * OCR'd scans (Nougat) write LaTeX: \( x^2 \) inline and \[ ... \] display.
//   * The PDF layout pass recovers dropped equations as `$$ ... $$` around
//     plain Unicode text ("A1 → C1 ∴ …"). Typeset as math, that would lose
//     every space between words, so those are shown as ordinary text.
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

// A LaTeX command (\frac, \alpha…) or a braced super/subscript.
const LATEX_HINT = /\\[A-Za-z]+|[\^_]\{/;

export function prepareMath(text) {
  if (!text) return "";
  return (
    text
      // Unicode-only "$$ … $$" from the layout pass: drop the fences.
      .replace(/\$\$([\s\S]+?)\$\$/g, (whole, body) => (LATEX_HINT.test(body) ? whole : body.trim()))
      // \[ … \] display math → a $$ block on its own lines.
      .replace(/\\\[([\s\S]+?)\\\]/g, (_whole, body) => `\n\n$$\n${body.trim()}\n$$\n\n`)
      // \( … \) inline math → inline $$…$$ (single dollars stay literal, so
      // prices like "$5 and $10" are never mistaken for math).
      .replace(/\\\(([\s\S]+?)\\\)/g, (_whole, body) => `$$${body.trim()}$$`)
  );
}

export const remarkPlugins = [remarkGfm, [remarkMath, { singleDollarTextMath: false }]];

// Malformed OCR LaTeX renders as its source text rather than a red error box.
export const katexPlugin = [rehypeKatex, { throwOnError: false, strict: "ignore", errorColor: "inherit" }];
