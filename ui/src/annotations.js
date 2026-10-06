// Geometry and text helpers for the PDF reader's marks. Everything here is in
// page space: x and y run 0..1 across the page as displayed, which is also how
// the server stores marks, so a mark is independent of zoom.

export const COLORS = {
  highlight: ["#ffd84d", "#7be08f", "#7ac7ff", "#ff9bc4"],
  underline: ["#e5484d", "#2f6fed", "#17864f", "#1f2328"],
  text: ["#1f2328", "#e5484d", "#2f6fed", "#17864f"],
  ink: ["#2f6fed", "#e5484d", "#17864f", "#1f2328"],
};

export const TOOLS = [
  { id: "select", label: "Select", hint: "Drag across words to select them; click a mark to edit it" },
  { id: "highlight", label: "Highlight", hint: "Drag across words to highlight them" },
  { id: "underline", label: "Underline", hint: "Drag across words to underline them" },
  { id: "text", label: "Text", hint: "Click the page to type a note" },
  { id: "draw", label: "Draw", hint: "Draw freehand on the page" },
  { id: "erase", label: "Erase", hint: "Click a mark to remove it" },
];

// Which palette a tool draws from (the kind the server stores).
export const KIND_FOR_TOOL = { highlight: "highlight", underline: "underline", text: "text", draw: "ink" };

export function cleanWord(raw) {
  const word = (raw || "").trim().replace(/^[^\p{L}\p{N}_]+|[^\p{L}\p{N}_]+$/gu, "").replace(/’/g, "'");
  if (!word || word.length > 40 || !/\p{L}/u.test(word)) return null;
  return word;
}

export function isSingleWord(text) {
  return (text || "").trim().split(/\s+/).length === 1 && cleanWord(text) !== null;
}

// Index of the word under a point, or -1. A hair of padding makes the thin
// gaps between letters and words easy to land in while dragging.
export function wordAt(words, x, y, pad = 0.002) {
  for (let i = 0; i < words.length; i += 1) {
    const [x0, y0, x1, y1] = words[i];
    if (x >= x0 - pad && x <= x1 + pad && y >= y0 - pad && y <= y1 + pad) return i;
  }
  return -1;
}

// One rect per line for the words from index a to b (either order). Words are
// in reading order, so a range is simply the slice between them; consecutive
// words whose vertical centres agree are the same line.
export function selectionRects(words, a, b) {
  const lo = Math.max(0, Math.min(a, b));
  const hi = Math.min(words.length - 1, Math.max(a, b));
  const lines = [];
  for (let i = lo; i <= hi; i += 1) {
    const [x0, y0, x1, y1] = words[i];
    const centre = (y0 + y1) / 2;
    const line = lines[lines.length - 1];
    if (line && Math.abs(centre - line.centre) <= (line.rect[3] - line.rect[1]) * 0.6) {
      line.rect = [Math.min(line.rect[0], x0), Math.min(line.rect[1], y0), Math.max(line.rect[2], x1), Math.max(line.rect[3], y1)];
    } else {
      lines.push({ rect: [x0, y0, x1, y1], centre });
    }
  }
  return lines.map((line) => line.rect.map((v) => Math.round(v * 1e5) / 1e5));
}

export function selectionText(words, a, b) {
  const lo = Math.max(0, Math.min(a, b));
  const hi = Math.min(words.length - 1, Math.max(a, b));
  const out = [];
  for (let i = lo; i <= hi; i += 1) out.push(words[i][4]);
  return out.join(" ");
}

// The few words around a word, as a short quote to remember where it was seen.
export function contextAround(words, index, radius = 14) {
  const lo = Math.max(0, index - radius);
  const hi = Math.min(words.length - 1, index + radius);
  const text = selectionText(words, lo, hi);
  return `${lo > 0 ? "… " : ""}${text}${hi < words.length - 1 ? " …" : ""}`;
}

const inside = (rect, x, y, pad = 0) => x >= rect[0] - pad && x <= rect[2] + pad && y >= rect[1] - pad && y <= rect[3] + pad;

function distanceToSegment(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax;
  const dy = by - ay;
  const t = dx || dy ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

// The topmost mark under a point (later marks sit above earlier ones).
export function markAt(marks, x, y) {
  for (let i = marks.length - 1; i >= 0; i -= 1) {
    const mark = marks[i];
    if (mark.kind === "ink") {
      const near = (mark.paths || []).some((stroke) =>
        stroke.some((point, n) => (n ? distanceToSegment(x, y, stroke[n - 1], point) : Math.hypot(x - point[0], y - point[1])) < 0.012),
      );
      if (near) return mark;
    } else if ((mark.rects || []).some((rect) => inside(rect, x, y, mark.kind === "underline" ? 0.004 : 0))) {
      return mark;
    }
  }
  return null;
}

// Thin a freehand stroke: a pointer fires far more often than a line needs.
export function simplifyStroke(points, minGap = 0.0015) {
  if (points.length < 3) return points;
  const out = [points[0]];
  for (let i = 1; i < points.length - 1; i += 1) {
    const last = out[out.length - 1];
    if (Math.hypot(points[i][0] - last[0], points[i][1] - last[1]) >= minGap) out.push(points[i]);
  }
  out.push(points[points.length - 1]);
  return out.map(([x, y]) => [Math.round(x * 1e5) / 1e5, Math.round(y * 1e5) / 1e5]);
}

export const clamp01 = (v) => Math.min(1, Math.max(0, v));

export const KIND_LABELS = { highlight: "Highlight", underline: "Underline", text: "Text note", ink: "Drawing" };

// A short line describing a mark in the marks list.
export function markSummary(mark) {
  if (mark.kind === "ink") return "Freehand drawing";
  return (mark.text || "").trim() || KIND_LABELS[mark.kind];
}
