import React, { useEffect, useState } from "react";

// Book details lookup: which catalogues answer, and whether new books are
// matched automatically. Off by default, because matching sends a book's
// cleaned-up file name to Open Library (and Google Books) without being asked.
export default function CatalogueSection({ apiBase, onChanged }) {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    fetch(`${apiBase}/catalogue/settings`).then((r) => (r.ok ? r.json() : null)).then(setState).catch(() => {});
  }, [apiBase]);

  async function toggle(next) {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/catalogue/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ auto_match: next }) });
      if (response.ok) setState(await response.json());
    } finally { setBusy(false); }
  }

  async function matchExisting() {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/catalogue/auto-match`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      setMessage(response.ok ? (data.queued ? `Matching ${data.queued} ${data.queued === 1 ? "book" : "books"} in the background. Details appear as they are found.` : "Every book has already been matched or tried.") : data.detail || "Could not start matching.");
      if (response.ok && data.queued) window.setTimeout(() => onChanged?.(), 15000);
    } finally { setBusy(false); }
  }

  if (!state) return null;
  const sources = state.providers.map((name) => (name === "googlebooks" ? "Google Books" : "Open Library")).join(" and ");
  return <section className="settings-section">
    <h3>Book details</h3>
    <p className="settings-note">
      Searches {sources}. A title with a typo or a download-site file name still finds the book.
      {state.providers.includes("googlebooks") && !state.google_key && " Google Books is often over its shared free quota; set GOOGLE_BOOKS_API_KEY on the server to keep it reliable."}
    </p>
    <label className="settings-check">
      <input type="checkbox" checked={state.auto_match} disabled={busy || state.offline} onChange={(e) => toggle(e.target.checked)} />
      <span>Match new books automatically</span>
    </label>
    <p className="settings-note">
      When a book finishes indexing, its cleaned-up file name is looked up and a confident match fills in only what is
      missing: author, year, pages, ISBN, and a cover. Anything you typed is kept. Off by default because file names are sent out.
    </p>
    <button type="button" className="secondary" disabled={busy || state.offline} onClick={matchExisting}>Match books already in the library</button>
    {message && <div className="status-message" role="status">{message}</div>}
  </section>;
}
