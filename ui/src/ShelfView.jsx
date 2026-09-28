import React, { useMemo, useState } from "react";

import { displayTitle } from "./storage.js";
import { allShelfPaths, buildShelfTree, effectiveShelf, isSuggested, onShelf, prettyShelf, UNSORTED } from "./shelfTree.js";

async function send(apiBase, path, method, body) {
  const response = await fetch(`${apiBase}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function ShelfNode({ node, selected, onSelect, depth = 0 }) {
  const [open, setOpen] = useState(depth < 1 || selected.startsWith(`${node.path}/`));
  const hasChildren = node.children.length > 0;
  return (
    <li>
      <div className={`shelf-node${selected === node.path ? " active" : ""}`} style={{ paddingLeft: `${8 + depth * 14}px` }}>
        <button
          type="button"
          className={`shelf-twisty${hasChildren ? "" : " leaf"}`}
          aria-label={open ? `Collapse ${node.name}` : `Expand ${node.name}`}
          aria-expanded={hasChildren ? open : undefined}
          disabled={!hasChildren}
          onClick={() => setOpen((value) => !value)}
        >
          {hasChildren ? (open ? "▾" : "▸") : ""}
        </button>
        <button type="button" className="shelf-node-name" onClick={() => onSelect(node.path)}>
          <span className={node.name === UNSORTED ? "muted" : ""}>{node.name}</span>
          <span className="shelf-node-count mono">{node.count}</span>
        </button>
      </div>
      {hasChildren && open && (
        <ul>
          {node.children.map((child) => (
            <ShelfNode key={child.path} node={child} selected={selected} onSelect={onSelect} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

function MoveField({ initial, paths, onSave, onCancel, label }) {
  const [value, setValue] = useState(initial);
  const listId = useMemo(() => `shelf-paths-${Math.random().toString(36).slice(2)}`, []);
  return (
    <form
      className="shelf-move"
      onSubmit={(event) => {
        event.preventDefault();
        if (value.trim()) onSave(value.trim());
      }}
    >
      <input
        className="input"
        autoFocus
        list={listId}
        value={value}
        aria-label={label}
        placeholder="Books / Philosophy / Albert Camus"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => event.key === "Escape" && onCancel()}
      />
      <datalist id={listId}>
        {paths.map((path) => (
          <option key={path} value={path} />
        ))}
      </datalist>
      <button type="submit" className="button primary small">Save</button>
      <button type="button" className="text-button" onClick={onCancel}>Cancel</button>
    </form>
  );
}

export default function ShelfView({ apiBase, documents, onChanged, onOpenDocument, onScopeShelf }) {
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState(null); // document id, or "__shelf__" for renaming the selected shelf
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const tree = useMemo(() => buildShelfTree(documents), [documents]);
  const paths = useMemo(() => allShelfPaths(documents), [documents]);
  const waiting = documents.filter(isSuggested);
  const onSelected = documents
    .filter((doc) => onShelf(doc, selected))
    .sort((a, b) => effectiveShelf(a).localeCompare(effectiveShelf(b)) || (a.title || a.filename).localeCompare(b.title || b.filename));
  const selectedExists = !selected || paths.includes(selected) || selected === UNSORTED;
  const shownPath = selectedExists ? selected : "";

  async function run(action) {
    setBusy(true);
    setError("");
    try {
      await action();
      await onChanged?.();
    } catch (actionError) {
      setError(actionError.message);
    } finally {
      setBusy(false);
      setEditing(null);
    }
  }

  const accept = (ids) => run(() => send(apiBase, "/shelves/accept", "POST", ids ? { document_ids: ids } : {}));
  const place = (doc, shelf) => run(() => send(apiBase, `/documents/${doc.document_id}`, "PATCH", { shelf }));
  const rename = (target) =>
    run(async () => {
      const result = await send(apiBase, "/shelves/move", "POST", { source: shownPath, target });
      setSelected(result.shelf);
    });

  if (!documents.length) {
    return (
      <div className="empty">
        <strong>No shelves yet</strong>
        <p>Documents are sorted onto shelves as they finish indexing.</p>
      </div>
    );
  }

  return (
    <div className="shelf-view">
      <div className="shelf-review">
        <p>
          {waiting.length ? (
            <>
              <strong>{waiting.length}</strong> {waiting.length === 1 ? "document is" : "documents are"} on a suggested shelf.
            </>
          ) : (
            "Every document is on a shelf you've confirmed."
          )}{" "}
          <span className="muted">Sorted by type, subject and author. Files on disk are never moved.</span>
        </p>
        {waiting.length > 0 && (
          <button type="button" className="button primary small" disabled={busy} onClick={() => accept(null)}>
            Accept all suggestions
          </button>
        )}
      </div>
      {error && <div className="notice error">{error}</div>}

      <div className="shelf-columns">
        <nav className="shelf-tree" aria-label="Shelves">
          <ul>
            <li>
              <div className={`shelf-node${shownPath === "" ? " active" : ""}`} style={{ paddingLeft: "8px" }}>
                <span className="shelf-twisty leaf" />
                <button type="button" className="shelf-node-name" onClick={() => setSelected("")}>
                  <span>All shelves</span>
                  <span className="shelf-node-count mono">{tree.count}</span>
                </button>
              </div>
            </li>
            {tree.children.map((node) => (
              <ShelfNode key={node.path} node={node} selected={shownPath} onSelect={setSelected} />
            ))}
          </ul>
        </nav>

        <section className="shelf-contents">
          <header className="shelf-contents-head">
            {editing === "__shelf__" ? (
              <MoveField initial={shownPath} paths={paths} label="New shelf name" onSave={rename} onCancel={() => setEditing(null)} />
            ) : (
              <>
                <h3>{shownPath ? prettyShelf(shownPath) : "All shelves"}</h3>
                <span className="muted mono">{onSelected.length}</span>
                <div className="shelf-contents-actions">
                  {shownPath && shownPath !== UNSORTED && (
                    <>
                      <button type="button" className="text-button accent" onClick={() => onScopeShelf?.(shownPath)}>
                        Search this shelf
                      </button>
                      <button type="button" className="text-button" disabled={busy} onClick={() => setEditing("__shelf__")}>
                        Rename or move
                      </button>
                    </>
                  )}
                </div>
              </>
            )}
          </header>

          <ul className="shelf-docs">
            {onSelected.map((doc) => (
              <li key={doc.document_id} className="shelf-doc">
                <div className="shelf-doc-main">
                  <button type="button" className="shelf-doc-title" onClick={() => onOpenDocument?.(doc)}>
                    {displayTitle(doc.title || doc.filename)}
                  </button>
                  <div className="shelf-doc-meta">
                    {[doc.author, doc.subject].filter(Boolean).join(" · ") || <span className="faint">{doc.filename}</span>}
                  </div>
                  {editing === doc.document_id ? (
                    <MoveField
                      initial={effectiveShelf(doc) === UNSORTED ? "" : effectiveShelf(doc)}
                      paths={paths}
                      label={`Shelf for ${doc.filename}`}
                      onSave={(shelf) => place(doc, shelf)}
                      onCancel={() => setEditing(null)}
                    />
                  ) : (
                    <div className="shelf-doc-path mono">
                      {prettyShelf(effectiveShelf(doc))}
                      {isSuggested(doc) && <span className="shelf-suggested">suggested</span>}
                    </div>
                  )}
                </div>
                {editing !== doc.document_id && (
                  <div className="shelf-doc-actions">
                    {isSuggested(doc) && (
                      <button type="button" className="text-button accent" disabled={busy} onClick={() => accept([doc.document_id])}>
                        Accept
                      </button>
                    )}
                    <button type="button" className="text-button" disabled={busy} onClick={() => setEditing(doc.document_id)}>
                      Move
                    </button>
                    {doc.shelf && doc.shelf_suggested && doc.shelf !== doc.shelf_suggested && (
                      <button type="button" className="text-button" disabled={busy} title={`Back to ${prettyShelf(doc.shelf_suggested)}`} onClick={() => place(doc, null)}>
                        Use suggestion
                      </button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
