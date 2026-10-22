import React, { useEffect, useMemo, useState } from "react";

import Cover from "./Cover.jsx";
import { libraryStats } from "./libraryStats.js";
import { GOAL_KEY, MAX_GOAL, currentlyReading, goalProgress, parseGoal, pickQuote, quoteCandidates, readingProgress } from "./libraryRail.js";
import { displayTitle, loadStored, saveStored } from "./storage.js";

// An open book, drawn in a line: the picture for a shelf with nothing on it yet.
function QuietBook() {
  return <svg className="quiet-book" viewBox="0 0 64 44" width="56" height="39" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M32 9c-5-3.5-14-4.5-26-3v31c12-1.5 21-.5 26 3 5-3.5 14-4.5 26-3V6c-12-1.5-21-.5-26 3zM32 9v31" />
    <path d="M12 15c4-.6 8-.4 14 1.2M12 22c4-.6 8-.4 14 1.2M38 16.2c6-1.6 10-1.8 14-1.2M38 23.2c6-1.6 10-1.8 14-1.2" opacity="0.55" />
  </svg>;
}

function Figure({ label, count, active, onClick, hint }) {
  return <button type="button" className={`summary-figure${active ? " active" : ""}`} aria-pressed={active} title={hint} onClick={onClick}>
    <strong>{count.toLocaleString()}</strong>
    <span>{label}</span>
  </button>;
}

const PACE = {
  on: () => "Right on pace",
  done: () => "Goal reached. Well read.",
  ahead: (gap) => `${gap} ${gap === 1 ? "book" : "books"} ahead of pace`,
  behind: (gap) => `${gap} ${gap === 1 ? "book" : "books"} behind pace`,
};

// One number: how many books to finish this year. It lives in this browser,
// like the other small preferences, so it needs no server and no sign-in.
function Goal({ finished, year }) {
  const [target, setTarget] = useState(() => parseGoal(loadStored(GOAL_KEY, "")));
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const progress = goalProgress(finished, target);
  const open = () => { setDraft(target ? String(target) : ""); setEditing(true); };
  function save(event) {
    event.preventDefault();
    const next = parseGoal(draft);
    setTarget(next);
    saveStored(GOAL_KEY, next ? String(next) : "");
    setEditing(false);
  }
  if (editing) {
    return <form className="goal goal-form" onSubmit={save}>
      <label htmlFor="goal-books">Books to finish in {year}</label>
      <div className="goal-form-row">
        <input id="goal-books" className="input" type="number" inputMode="numeric" min="1" max={MAX_GOAL} value={draft} autoFocus onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setEditing(false); } }} />
        <button className="button primary small" type="submit">Save</button>
        <button className="text-button" type="button" onClick={() => setEditing(false)}>Cancel</button>
      </div>
      {target > 0 && <button type="button" className="text-button goal-remove" onClick={() => { setDraft(""); setTarget(0); saveStored(GOAL_KEY, ""); setEditing(false); }}>Remove goal</button>}
    </form>;
  }
  if (!progress) return <button type="button" className="goal goal-prompt" onClick={open}>Set a reading goal for {year}</button>;
  return <div className="goal">
    <div className="goal-head">
      <span className="eyebrow">{year} goal</span>
      <button type="button" className="text-button" onClick={open}>Change</button>
    </div>
    <p className="goal-count"><strong>{progress.finished.toLocaleString()}</strong> of {progress.target.toLocaleString()} {progress.target === 1 ? "book" : "books"}</p>
    <span className="summary-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-label={`${progress.finished} of ${progress.target} books finished in ${year}`}><i style={{ width: `${progress.percent}%` }} /></span>
    <p className={`goal-pace pace-${progress.pace}`}>{PACE[progress.pace](progress.gap)}</p>
  </div>;
}

function ReadingRow({ apiBase, doc, onOpen }) {
  const title = doc.title || displayTitle(doc.filename);
  const { percent, current, total } = readingProgress(doc);
  return <button type="button" className="summary-reading" onClick={() => onOpen(doc)}>
    <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={120} className="summary-cover" />
    <span className="summary-reading-copy">
      <strong>{title}</strong>
      <span>{total > 0 ? `Page ${current.toLocaleString()} of ${total.toLocaleString()}` : doc.author || "In progress"}</span>
      <span className="summary-reading-bar">
        <span className="summary-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={`${title}: ${percent}% read`}><i style={{ width: `${percent}%` }} /></span>
        <span className="summary-percent">{percent}%</span>
      </span>
    </span>
  </button>;
}

// The side of the Library, kept to what a reader glances at: how many books,
// what is in progress, what is finished and how the year's goal is going,
// then the book being read and a line from the notes. The detailed numbers
// live in the Statistics tab. On a narrow screen it folds into a summary above
// the books (see the stylesheet).
export default function LibraryRail({ apiBase, documents, notes, status, onStatus, onNavigate, onOpenDocument }) {
  const stats = useMemo(() => libraryStats(documents), [documents]);
  const reading = useMemo(() => currentlyReading(documents, 3), [documents]);
  const quotes = useMemo(() => quoteCandidates(notes, documents), [notes, documents]);
  const [quoteId, setQuoteId] = useState(null);
  // Keep the quote on screen while the library refreshes; pick a new one only
  // when it has been deleted or there was none yet.
  useEffect(() => {
    if (quotes.length && !quotes.some((quote) => quote.id === quoteId)) setQuoteId(pickQuote(quotes)?.id ?? null);
    if (!quotes.length && quoteId) setQuoteId(null);
  }, [quotes, quoteId]);
  const quote = quotes.find((item) => item.id === quoteId) || null;

  return <aside className="library-rail" aria-label="Your reading">
    <section className="reading-summary" aria-labelledby="summary-heading">
      <h3 id="summary-heading">Your reading</h3>
      <div className="summary-figures">
        <Figure label="Books" count={stats.total} active={status === "all"} hint="Every book in the library" onClick={() => onStatus("all")} />
        <Figure label="Reading" count={stats.reading} active={status === "reading"} hint="Books you are reading now" onClick={() => onStatus("reading")} />
        <Figure label="Completed" count={stats.completed} active={status === "read"} hint="Books you have finished" onClick={() => onStatus("read")} />
      </div>
      <Goal finished={stats.completedThisYear} year={stats.year} />
      <button type="button" className="text-button summary-more" onClick={() => onNavigate("statistics")}>All statistics →</button>
    </section>

    <section className="rail-reading" aria-labelledby="reading-heading">
      <h3 id="reading-heading" className="rail-heading">Currently reading</h3>
      {reading.items.length === 0
        ? <div className="quiet-prompt">
          <QuietBook />
          <p><strong>Your next chapter awaits.</strong><span>Set a book to Reading and it will wait here.</span></p>
        </div>
        : <>
          {reading.items.map((doc) => <ReadingRow key={doc.document_id} apiBase={apiBase} doc={doc} onOpen={onOpenDocument} />)}
          {reading.total > reading.items.length && <button type="button" className="text-button summary-more" onClick={() => onStatus("reading")}>All {reading.total} in progress</button>}
        </>}
    </section>

    {quote && <section className="rail-quote" aria-labelledby="quote-heading">
      <h3 id="quote-heading" className="rail-heading">From your notes</h3>
      <figure className="summary-quote">
        <blockquote>{quote.text}</blockquote>
        <figcaption>
          {quote.sourceId
            ? <button type="button" className="text-button" onClick={() => onOpenDocument({ document_id: quote.sourceId })}>{quote.sourceTitle}{quote.page ? ` · p. ${quote.page}` : ""}</button>
            : <span>Saved note</span>}
          {quotes.length > 1 && <button type="button" className="text-button summary-shuffle" onClick={() => setQuoteId(pickQuote(quotes, quote.id)?.id ?? null)}>Another</button>}
        </figcaption>
      </figure>
    </section>}
  </aside>;
}
