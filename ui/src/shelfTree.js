// Virtual shelves, as the server stores them: `shelf` is the reader's choice,
// `shelf_suggested` the automatic one ("Books/Philosophy/Albert Camus").
// Documents not placed yet (still indexing) sit on UNSORTED.

export const UNSORTED = "Unsorted";

export function effectiveShelf(doc) {
  return doc.shelf || doc.shelf_suggested || UNSORTED;
}

export function isSuggested(doc) {
  return !doc.shelf && Boolean(doc.shelf_suggested);
}

export function onShelf(doc, path) {
  if (!path) return true;
  const current = effectiveShelf(doc);
  return current === path || current.startsWith(`${path}/`);
}

export function prettyShelf(path) {
  return (path || "").split("/").join(" › ");
}

// Nested nodes { name, path, count, children } with counts that include
// sub-shelves, siblings sorted by name (Unsorted last).
export function buildShelfTree(documents) {
  const root = { name: "", path: "", count: 0, children: new Map() };
  for (const doc of documents) {
    root.count += 1;
    let node = root;
    for (const part of effectiveShelf(doc).split("/")) {
      const path = node.path ? `${node.path}/${part}` : part;
      if (!node.children.has(part)) node.children.set(part, { name: part, path, count: 0, children: new Map() });
      node = node.children.get(part);
      node.count += 1;
    }
  }
  const finish = (node) => ({
    ...node,
    children: [...node.children.values()]
      .sort((a, b) => (a.name === UNSORTED) - (b.name === UNSORTED) || a.name.localeCompare(b.name))
      .map(finish),
  });
  return finish(root);
}

export function allShelfPaths(documents) {
  const paths = new Set();
  for (const doc of documents) {
    const parts = effectiveShelf(doc).split("/");
    parts.forEach((_, index) => paths.add(parts.slice(0, index + 1).join("/")));
  }
  paths.delete(UNSORTED);
  return [...paths].sort((a, b) => a.localeCompare(b));
}
