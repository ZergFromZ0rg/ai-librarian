import React, { useState } from "react";

const SOURCE_LABELS = { "pdf-underline": "PDF underline", "drawn-line": "Drawn line", "pen-line": "Pen line" };

// Search one book for the single words that are underlined in its PDF (with
// any viewer: a browser, Preview, Acrobat), review them, and add the ones you want
// to the vocabulary notebook with the page and sentence they came from.
export default function UnderlinedWords({ apiBase, documentId, onAdded }) {
  const [phase, setPhase] = useState("idle");
  const [found, setFound] = useState([]);
  const [chosen, setChosen] = useState(() => new Set());
  const [message, setMessage] = useState("");

  async function scan() {
    setPhase("scanning");
    setMessage("");
    try {
      const response = await fetch(`${apiBase}/documents/${documentId}/underlined-words`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not scan this book.");
      setFound(data.words);
      setChosen(new Set(data.words.filter((item) => !item.saved).map((item) => item.word)));
      setPhase("done");
    } catch (failure) {
      setMessage(failure.message);
      setPhase("idle");
    }
  }

  async function add() {
    const words = found.filter((item) => chosen.has(item.word) && !item.saved).map(({ word, page, quote }) => ({ word, page, quote }));
    if (!words.length) return;
    setPhase("adding");
    try {
      const response = await fetch(`${apiBase}/vocabulary/harvest`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_id: documentId, words }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not add these words.");
      setFound((current) => current.map((item) => (chosen.has(item.word) ? { ...item, saved: true } : item)));
      setChosen(new Set());
      setMessage(`Added ${data.created} ${data.created === 1 ? "word" : "words"} to your vocabulary.`);
      onAdded?.();
    } catch (failure) {
      setMessage(failure.message);
    }
    setPhase("done");
  }

  const fresh = found.filter((item) => !item.saved);
  const toggle = (word) => setChosen((current) => { const next = new Set(current); if (next.has(word)) next.delete(word); else next.add(word); return next; });
  return (
    <div className="underlined-words">
      <div className="underlined-words-head">
        <button type="button" className="button small" onClick={scan} disabled={phase === "scanning" || phase === "adding"}>
          {phase === "scanning" ? "Reading the book…" : phase === "done" || phase === "adding" ? "Scan again" : "Find underlined words"}
        </button>
        {phase === "done" && fresh.length > 0 && (
          <>
            <button type="button" className="text-button" onClick={() => setChosen(new Set(fresh.map((item) => item.word)))}>All</button>
            <button type="button" className="text-button" onClick={() => setChosen(new Set())}>None</button>
          </>
        )}
      </div>
      {message && <p className="notice" role="status">{message}</p>}
      {phase === "done" && !found.length && (
        <p className="muted empty-state">
          No single underlined words found. Underlines made in this reader, Preview, Acrobat or Apple Books are detected; a scanned page with no text layer can’t be read.
        </p>
      )}
      {found.length > 0 && (
        <ul className="underlined-list">
          {found.map((item) => (
            <li key={item.word} className={item.saved ? "saved" : ""}>
              <label>
                <input type="checkbox" disabled={item.saved} checked={chosen.has(item.word)} onChange={() => toggle(item.word)} />
                <span className="underlined-word">{item.word}</span>
                <span className="muted underlined-meta">p. {item.pages.slice(0, 4).join(", ")}{item.pages.length > 4 ? "…" : ""} · {SOURCE_LABELS[item.source] || item.source}</span>
                {item.saved && <span className="underlined-saved">in vocabulary</span>}
              </label>
              {item.quote && <blockquote>{item.quote}</blockquote>}
            </li>
          ))}
        </ul>
      )}
      {phase === "done" && fresh.length > 0 && (
        <button type="button" className="button primary small" disabled={!chosen.size} onClick={add}>
          Add {chosen.size} {chosen.size === 1 ? "word" : "words"} to vocabulary
        </button>
      )}
    </div>
  );
}
