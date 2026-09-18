import React from "react";

import { docKind, isRead, ownedState } from "./storage.js";

const KIND_LABELS = { book: "Book", paper: "Paper", document: "Document" };

// Small marks laid over a cover: what it is (books and papers only), whether
// a book is owned, and read/unread. Display only — the controls live in the
// page viewer and the Index table.
export function CoverFlags({ doc }) {
  const kind = docKind(doc);
  const owned = ownedState(doc);
  const read = isRead(doc);
  return (
    <>
      {kind !== "document" && <span className={`flag flag-kind flag-${kind}`}>{KIND_LABELS[kind]}</span>}
      <span className={`flag flag-read${read ? " is-read" : ""}`} title={read ? "Read" : "Unread"}>
        {read ? "✓ Read" : "Unread"}
      </span>
      {owned != null && <span className={`flag flag-owned${owned ? " is-owned" : ""}`}>{owned ? "Owned" : "Not owned"}</span>}
    </>
  );
}

// The editable marks: read/unread for everything, owned/not owned for books,
// and a correction for the detected kind. `onPatch(changes)` sends them.
export function MarkControls({ doc, onPatch, compact = false }) {
  const kind = docKind(doc);
  const owned = ownedState(doc);
  const read = isRead(doc);
  return (
    <div className={`marks${compact ? " compact" : ""}`}>
      <button
        type="button"
        className={`mark-toggle${read ? " on" : ""}`}
        aria-pressed={read}
        onClick={() => onPatch({ read: !read })}
        title={read ? "Mark as unread" : "Mark as read"}
      >
        <span className="mark-box" aria-hidden="true">{read ? "✓" : ""}</span>
        Read
      </button>
      {kind === "book" && (
        <button
          type="button"
          className={`mark-toggle${owned ? " on" : ""}`}
          aria-pressed={Boolean(owned)}
          onClick={() => onPatch({ owned: !owned })}
          title={owned ? "Mark as not owned" : "Mark as owned"}
        >
          <span className="mark-box" aria-hidden="true">{owned ? "✓" : ""}</span>
          Owned
        </button>
      )}
      <select
        className="mark-kind"
        value={doc?.kind_override || "auto"}
        onChange={(event) => onPatch({ kind: event.target.value })}
        aria-label="Document type"
        title="Detected automatically; change it if the guess is wrong"
      >
        <option value="auto">Auto · {KIND_LABELS[doc?.kind || "document"]}</option>
        <option value="book">Book</option>
        <option value="paper">Paper</option>
        <option value="document">Document</option>
      </select>
    </div>
  );
}
