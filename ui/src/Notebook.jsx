import React, { useEffect, useState } from "react";

import { historyEntries, timeAgo } from "./storage.js";

// History: one newest-first list of saved chats (server-side) and past
// searches (this browser only). A chat reopens the conversation; a search
// runs again. Closes on Escape or a click on the scrim.
export default function Notebook({ open, onClose, askEnabled, chats, activeChatId, onSelectChat, onNewChat, onDeleteChat, inquiries, onRunInquiry, onClearInquiries }) {
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const needle = filter.trim().toLowerCase();
  const all = historyEntries(askEnabled ? chats : [], inquiries);
  const entries = needle ? all.filter((entry) => (entry.type === "chat" ? entry.title : entry.q).toLowerCase().includes(needle)) : all;
  const hasSearches = all.some((entry) => entry.type === "search");
  // No chat selected means a new, not-yet-saved conversation is current —
  // show it at once rather than only after its first answer is saved.
  const showDraft = askEnabled && activeChatId === null && !needle;

  return (
    <>
      <div className={`scrim${open ? " open" : ""}`} onClick={onClose} aria-hidden="true" />
      <aside className={`notebook${open ? " open" : ""}`} aria-label="History" aria-hidden={!open} inert={!open}>
        <header className="notebook-head">
          <h2>History</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close history">
            ×
          </button>
        </header>

        <div className="notebook-filter">
          <input type="search" className="input" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter chats and searches…" aria-label="Filter history" />
        </div>

        <div className="notebook-body">
          {askEnabled && (
            <button type="button" className="button primary notebook-new" onClick={onNewChat}>
              + New chat
            </button>
          )}
          {all.length === 0 && !showDraft && <p className="muted">Chats you start and searches you run show up here.</p>}
          {all.length > 0 && entries.length === 0 && <p className="muted">Nothing matches “{filter}”.</p>}
          <ul className="entry-list">
            {showDraft && (
              <li className="entry active">
                <span className="entry-main entry-draft">
                  <span className="entry-glyph" aria-hidden="true">¶</span>
                  <span className="entry-text">New chat — in progress</span>
                </span>
              </li>
            )}
            {entries.map((entry) =>
              entry.type === "chat" ? (
                <li className={`entry${entry.id === activeChatId ? " active" : ""}`} key={entry.key}>
                  <button type="button" className="entry-main" onClick={() => onSelectChat(entry.id)} title="Open this chat">
                    <span className="entry-glyph" aria-hidden="true">¶</span>
                    <span className="entry-text">{entry.title}</span>
                    <span className="entry-when">{timeAgo(entry.at)}</span>
                  </button>
                  <button type="button" className="entry-delete" aria-label="Delete chat" title="Delete chat" onClick={() => onDeleteChat(entry.id)}>
                    ×
                  </button>
                </li>
              ) : (
                <li className="entry" key={entry.key}>
                  <button type="button" className="entry-main" onClick={() => onRunInquiry(entry.inquiry)} title="Run this search again">
                    <span className="entry-glyph" aria-hidden="true">§</span>
                    <span className="entry-text">{entry.q}</span>
                    <span className="entry-when">{timeAgo(entry.at)}</span>
                  </button>
                </li>
              ),
            )}
          </ul>
          {hasSearches && (
            <button type="button" className="text-button" onClick={onClearInquiries}>
              Clear search history
            </button>
          )}
        </div>
      </aside>
    </>
  );
}
