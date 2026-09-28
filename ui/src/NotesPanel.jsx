import React, { useCallback, useEffect, useState } from "react";

import { timeAgo } from "./storage.js";

async function request(apiBase, path, method = "GET", body) {
  const response = await fetch(`${apiBase}${path}`, {
    method,
    ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function saveOnShortcut(event, save) {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    save();
  }
}

// Quick notes: jot an idea or a phrase; it is saved in the app (never in the
// read-only library folder), indexed, and searchable as the "Notes" library.
export default function NotesPanel({ apiBase, onChanged }) {
  const [notes, setNotes] = useState(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(null); // { id, text }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setNotes((await request(apiBase, "/notes")).notes);
    } catch (loadError) {
      setError(loadError.message);
      setNotes([]);
    }
  }, [apiBase]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while a note is still being indexed, so its status settles.
  useEffect(() => {
    if (!notes?.some((note) => note.indexing_status !== "indexed" && note.indexing_status !== "error")) return undefined;
    const timer = window.setTimeout(load, 2000);
    return () => window.clearTimeout(timer);
  }, [notes, load]);

  async function run(action) {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
      onChanged?.();
      return true;
    } catch (actionError) {
      setError(actionError.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const add = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    if (await run(() => request(apiBase, "/notes", "POST", { text }))) setDraft("");
  };
  const saveEdit = async () => {
    if (!editing?.text.trim() || busy) return;
    if (await run(() => request(apiBase, `/notes/${editing.id}`, "PUT", { text: editing.text }))) setEditing(null);
  };
  const remove = (note) => {
    if (!window.confirm(`Delete the note “${note.title}”?`)) return;
    run(() => request(apiBase, `/documents/${note.document_id}`, "DELETE"));
  };

  return (
    <div className="notes-panel">
      <div className="note-jot">
        <textarea
          className="input"
          rows={3}
          value={draft}
          placeholder="Jot an idea, a phrase, something to come back to…"
          aria-label="New note"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => saveOnShortcut(event, add)}
        />
        <div className="note-jot-foot">
          <span className="muted">Saved in the app and searchable as the Notes library. ⌘/Ctrl + Enter to save.</span>
          <button type="button" className="button primary small" disabled={busy || !draft.trim()} onClick={add}>
            Save note
          </button>
        </div>
      </div>
      {error && <div className="notice error">{error}</div>}

      {notes === null ? (
        <p className="muted">Loading notes…</p>
      ) : notes.length === 0 ? (
        <div className="empty">
          <strong>No notes yet</strong>
          <p>Anything you jot here stays searchable, and can be asked about like the rest of the library.</p>
        </div>
      ) : (
        <ul className="note-list">
          {notes.map((note) => (
            <li key={note.document_id} className="note">
              <div className="note-meta">
                <span className="mono">{timeAgo(Date.parse(note.created_at))}</span>
                {note.indexing_status !== "indexed" && (
                  <span className={`badge badge-${note.indexing_status}`}>{note.indexing_status}</span>
                )}
                <div className="note-actions">
                  {editing?.id !== note.document_id && (
                    <>
                      <button type="button" className="text-button" disabled={busy} onClick={() => setEditing({ id: note.document_id, text: note.text.trim() })}>
                        Edit
                      </button>
                      <button type="button" className="text-button danger" disabled={busy} onClick={() => remove(note)}>
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
              {editing?.id === note.document_id ? (
                <div className="note-edit">
                  <textarea
                    className="input"
                    rows={4}
                    autoFocus
                    value={editing.text}
                    aria-label="Edit note"
                    onChange={(event) => setEditing({ ...editing, text: event.target.value })}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") setEditing(null);
                      saveOnShortcut(event, saveEdit);
                    }}
                  />
                  <div className="note-edit-actions">
                    <button type="button" className="button primary small" disabled={busy || !editing.text.trim()} onClick={saveEdit}>
                      Save
                    </button>
                    <button type="button" className="text-button" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <p className="note-text">{note.text.trim()}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
