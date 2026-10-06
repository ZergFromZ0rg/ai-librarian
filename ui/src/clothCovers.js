// Deterministic cloth-binding colours for books without cover artwork. The
// colour comes from a stable seed (the book's id), never its position in a
// list, so a book keeps its binding when it is filtered, sorted or reordered.
const BINDINGS = [
  "#6b2d2d", "#304e49", "#2f4160", "#5a3d5c", "#7a4f2a", "#44505c",
  "#2a5a5e", "#8a4a2c", "#55582f", "#3b3f73", "#33363b", "#70485a",
];

export function hashSeed(seed) {
  let hash = 0x811c9dc5;
  for (const char of String(seed ?? "")) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash;
}

function darken(hex, factor) {
  const channels = [1, 3, 5].map((start) => Math.round(parseInt(hex.slice(start, start + 2), 16) * factor));
  return `#${channels.map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

export function clothColor(seed) {
  const bg = BINDINGS[hashSeed(seed) % BINDINGS.length];
  return { bg, edge: darken(bg, 0.72) };
}

export const CLOTH_COUNT = BINDINGS.length;
