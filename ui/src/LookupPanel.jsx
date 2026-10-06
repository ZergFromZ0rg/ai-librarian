import React, { useEffect, useRef, useState } from "react";

import { looksLikeIsbn } from "./catalogueMatch.js";

// Search Open Library by ISBN, title or author through this app's own server
// (the browser never contacts the catalogue). `onPick` receives one candidate;
// what happens next - comparing, creating a book - is the caller's choice.
export default function LookupPanel({ apiBase, initialQuery = "", onPick, pickLabel = "Use this", autoFocus = true }) {
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef(null);
  useEffect(() => { if (autoFocus) input.current?.focus(); }, [autoFocus]);

  // A div, not a form: this panel sits inside the book editor's own form.
  async function search() {
    const text = query.trim();
    if (text.length < 2 || busy) return;
    setBusy(true); setError(""); setResults(null);
    try {
      const url = looksLikeIsbn(text)
        ? `${apiBase}/catalogue/isbn/${encodeURIComponent(text.replace(/[\s-]/g, ""))}`
        : `${apiBase}/catalogue/search?q=${encodeURIComponent(text)}`;
      const response = await fetch(url);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The search failed. Please try again.");
      setResults(data.results);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  return <div className="lookup-panel">
    <div className="lookup-form" role="search">
      <input ref={input} className="input" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); search(); } }} aria-label="ISBN, title or author" placeholder="ISBN, or title and author" maxLength={200} />
      <button type="button" className="button primary" onClick={search} disabled={busy || query.trim().length < 2}>{busy ? "Searching…" : "Search"}</button>
    </div>
    {error && <p className="book-inline-error" role="alert">{error} You can still fill the details in by hand.</p>}
    {results && !results.length && <p className="muted" role="status">No matches. Try the ISBN, or fewer words.</p>}
    {results?.length > 0 && <ul className="lookup-results" aria-label="Catalogue matches">
      {results.map((item) => <li key={item.id}>
        <span className="lookup-cover" aria-hidden="true">{item.cover_id && <img src={`${apiBase}/catalogue/cover?id=${item.cover_id}`} alt="" loading="lazy" onError={(event) => { event.currentTarget.style.display = "none"; }} />}</span>
        <span className="lookup-info">
          <strong>{item.title}</strong>
          <span>{[item.author, item.published_year, item.publisher].filter(Boolean).join(" · ")}</span>
          <small>{[item.page_count && `${item.page_count} pages`, (item.isbn_13 || item.isbn_10) && `ISBN ${item.isbn_13 || item.isbn_10}`].filter(Boolean).join(" · ")}</small>
        </span>
        <button type="button" className="button small" onClick={() => onPick(item)}>{pickLabel}</button>
      </li>)}
    </ul>}
    <p className="lookup-credit">Data from Open Library. Only what you type here is sent.</p>
  </div>;
}
