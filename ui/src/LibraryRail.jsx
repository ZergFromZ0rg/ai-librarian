import React, { useEffect, useMemo, useState } from "react";

import Cover from "./Cover.jsx";
import { libraryStats } from "./libraryStats.js";
import { RAIL_SHELVES, currentlyReading, finishedByMonth, niceCeiling, pickQuote, quoteCandidates, readingProgress, shelfActive, shelfCounts } from "./libraryRail.js";
import { displayTitle, loadStored, saveStored } from "./storage.js";

const COLLAPSED_KEY = "ai-librarian.rail.collapsed";
const METRIC_KEY = "ai-librarian.rail.metric";

const ICONS = {
  all: <path d="M4 4h4v16H4zM10 4h4v16h-4zM16.5 4.5l3.8 1-3.9 14.6-3.8-1" />,
  owned: <path d="M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2zM5 18a2 2 0 0 1 2-2h11M9 8h5" />,
  to_read: <path d="M7 4h10v17l-5-4-5 4z" />,
  borrowed: <path d="M5 8h13l-3-3M19 16H6l3 3" />,
  statistics: <path d="M5 20V12M12 20V5M19 20v-5" />,
  quotes: <path d="M5 18v-5a5 5 0 0 1 5-5M13 18v-5a5 5 0 0 1 5-5M5 13h5v5H5zM13 13h5v5h-5z" />,
};

function Icon({ name }) {
  return <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{ICONS[name]}</svg>;
}

function Tile({ icon, label, count, hint, active, onClick }) {
  return <button type="button" className={`side-tile${active ? " active" : ""}`} aria-pressed={active ?? undefined} title={hint} onClick={onClick}>
    <span className="side-tile-icon"><Icon name={icon} /></span>
    <strong>{count.toLocaleString()}</strong>
    <span className="side-tile-label">{label}</span>
  </button>;
}

function RailSection({ id, title, open, onToggle, children }) {
  return <section className="side-section">
    <h3><button type="button" className="side-head" aria-expanded={open} aria-controls={`side-${id}`} onClick={() => onToggle(id)}>
      {title}
      <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={open ? "open" : ""}><path d="M6 9l6 6 6-6" /></svg>
    </button></h3>
    {open && <div id={`side-${id}`} className="side-body">{children}</div>}
  </section>;
}

function ReadingCard({ apiBase, doc, onOpen }) {
  const title = doc.title || displayTitle(doc.filename);
  const { percent, current, total, left } = readingProgress(doc);
  return <button type="button" className="side-reading" onClick={() => onOpen(doc)}>
    <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={160} className="side-cover" />
    <span className="side-reading-copy">
      <strong>{title}</strong>
      {total > 0 && <span>Page {current.toLocaleString()} of {total.toLocaleString()}</span>}
      {left != null && <span>{left.toLocaleString()} {left === 1 ? "page" : "pages"} left</span>}
    </span>
    <span className="side-progress">
      <span className="side-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={`${title}: ${percent}% read`}><i style={{ width: `${percent}%` }} /></span>
      <span className="side-percent">{percent}%</span>
    </span>
  </button>;
}

// Books finished per month (or pages), as a line: the quiet shape of a year's
// reading. One series in the app's accent, so there is no legend; the heading
// names it. Hover or focus a month to read its numbers; the same figures sit in
// a table for screen readers.
const W = 280;
const H = 132;
const PLOT = { left: 30, right: 10, top: 10, bottom: 110 };

function MonthLine({ months, metric }) {
  const [hover, setHover] = useState(null);
  const values = months.map((month) => month[metric]);
  const top = niceCeiling(Math.max(0, ...values));
  const x = (index) => PLOT.left + (index / (months.length - 1)) * (W - PLOT.left - PLOT.right);
  const y = (value) => PLOT.bottom - (value / top) * (PLOT.bottom - PLOT.top);
  const points = values.map((value, index) => [x(index), y(value)]);
  const line = points.map(([px, py], index) => `${index ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ");
  const area = `${line} L${points[points.length - 1][0].toFixed(1)} ${PLOT.bottom} L${points[0][0].toFixed(1)} ${PLOT.bottom} Z`;
  const ticks = top % 2 === 0 ? [0, top / 2, top] : [0, top];
  const shown = hover == null ? null : months[hover];
  const unit = metric === "books" ? "book" : "page";
  return <div className="side-chart">
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${metric === "books" ? "Books" : "Pages"} finished each month over the last ${months.length} months`} onPointerLeave={() => setHover(null)}>
      {ticks.map((tick) => <g key={tick}>
        <line className="side-grid" x1={PLOT.left} x2={W - PLOT.right} y1={y(tick)} y2={y(tick)} />
        <text className="side-axis" x={PLOT.left - 6} y={y(tick) + 3.5} textAnchor="end">{tick.toLocaleString()}</text>
      </g>)}
      <path className="side-area" d={area} />
      <path className="side-line" d={line} />
      {hover != null && <line className="side-cross" x1={points[hover][0]} x2={points[hover][0]} y1={PLOT.top} y2={PLOT.bottom} />}
      {points.map(([px, py], index) => <circle key={months[index].key} className={`side-dot${hover === index ? " on" : ""}`} cx={px} cy={py} r={hover === index ? 5 : 4} />)}
      {months.map((month, index) => ((months.length - 1 - index) % 3 === 0) && <text key={month.key} className="side-axis" x={x(index)} y={H - 6} textAnchor="middle">{month.label}</text>)}
      {points.map(([px], index) => <rect key={`hit-${months[index].key}`} className="side-hit" x={px - (W - PLOT.left - PLOT.right) / (months.length - 1) / 2} y={0} width={(W - PLOT.left - PLOT.right) / (months.length - 1)} height={H} onPointerEnter={() => setHover(index)} />)}
    </svg>
    {shown && <div className={`side-tip${hover < 3 ? " left" : hover > months.length - 4 ? " right" : ""}`} style={{ left: `${(points[hover][0] / W) * 100}%`, top: `${(points[hover][1] / H) * 100}%` }}>
      <strong>{shown.label} {shown.year}</strong>
      <span>{shown[metric].toLocaleString()} {unit}{shown[metric] === 1 ? "" : "s"}{metric === "books" && shown.pages ? ` · ${shown.pages.toLocaleString()} pages` : ""}</span>
    </div>}
    <table className="sr-only">
      <caption>{metric === "books" ? "Books" : "Pages"} finished each month</caption>
      <thead><tr><th scope="col">Month</th><th scope="col">Books</th><th scope="col">Pages</th></tr></thead>
      <tbody>{months.map((month) => <tr key={month.key}><th scope="row">{month.label} {month.year}</th><td>{month.books}</td><td>{month.pages}</td></tr>)}</tbody>
    </table>
  </div>;
}

// The side of the Library: where the shelves are, what you are reading now, a
// picture of your year, and a passage you saved. Everything on it is already in
// the library; it only gathers it in one place.
export default function LibraryRail({ apiBase, documents, notes, filters, onShelf, onNavigate, onOpenDocument }) {
  const counts = useMemo(() => shelfCounts(documents), [documents]);
  const reading = useMemo(() => currentlyReading(documents, 3), [documents]);
  const months = useMemo(() => finishedByMonth(documents), [documents]);
  const finishedThisYear = useMemo(() => libraryStats(documents).completedThisYear, [documents]);
  const quotes = useMemo(() => quoteCandidates(notes, documents), [notes, documents]);
  const [collapsed, setCollapsed] = useState(() => {
    const stored = loadStored(COLLAPSED_KEY, null, true);
    if (stored && typeof stored === "object") return stored;
    // On a phone the rail sits above the books, so its sections start folded
    // and the books stay close to the top.
    return window.matchMedia?.("(max-width: 1000px)").matches ? { reading: true, stats: true, quote: true } : {};
  });
  const [metric, setMetric] = useState(() => (loadStored(METRIC_KEY, "books") === "pages" ? "pages" : "books"));
  const [quoteId, setQuoteId] = useState(null);
  const toggle = (id) => setCollapsed((current) => {
    const next = { ...current, [id]: !current[id] };
    saveStored(COLLAPSED_KEY, next);
    return next;
  });
  const chooseMetric = (value) => { setMetric(value); saveStored(METRIC_KEY, value); };
  // Keep the quote on screen while the library refreshes; pick a new one only
  // when it has been deleted or there was none yet.
  useEffect(() => {
    if (quotes.length && !quotes.some((quote) => quote.id === quoteId)) setQuoteId(pickQuote(quotes)?.id ?? null);
    if (!quotes.length && quoteId) setQuoteId(null);
  }, [quotes, quoteId]);
  const quote = quotes.find((item) => item.id === quoteId) || null;
  const inYear = months.reduce((sum, month) => sum + month[metric], 0);
  const anyFinished = months.some((month) => month.books > 0);

  return <aside className="library-rail" aria-label="Your reading">
    <div className="side-tiles">
      {RAIL_SHELVES.map((shelf) => <Tile key={shelf.key} icon={shelf.key} label={shelf.label} count={counts[shelf.key]} hint={shelf.hint} active={shelfActive(filters, shelf.preset)} onClick={() => onShelf(shelf.preset)} />)}
      <Tile icon="statistics" label="Statistics" count={finishedThisYear} hint={`${finishedThisYear} ${finishedThisYear === 1 ? "book" : "books"} finished this year`} onClick={() => onNavigate("statistics")} />
      <Tile icon="quotes" label="Quotes" count={quotes.length} hint="Passages you saved from your books" onClick={() => onNavigate("notes")} />
    </div>

    <RailSection id="reading" title="Currently reading" open={!collapsed.reading} onToggle={toggle}>
      {reading.items.length === 0
        ? <p className="side-empty">Nothing in progress. Set a book to Reading and it will wait here.</p>
        : <>
          {reading.items.map((doc) => <ReadingCard key={doc.document_id} apiBase={apiBase} doc={doc} onOpen={onOpenDocument} />)}
          {reading.total > reading.items.length && <button type="button" className="text-button side-more" onClick={() => onShelf({ status: "reading" })}>All {reading.total} in progress</button>}
        </>}
    </RailSection>

    <RailSection id="stats" title="Statistics" open={!collapsed.stats} onToggle={toggle}>
      {anyFinished ? <>
        <div className="side-stat-head">
          <span>{inYear.toLocaleString()} {metric === "books" ? (inYear === 1 ? "book" : "books") : (inYear === 1 ? "page" : "pages")} · last 12 months</span>
          <span className="side-metric" role="group" aria-label="Chart shows">
            {["books", "pages"].map((value) => <button type="button" key={value} className={metric === value ? "active" : ""} aria-pressed={metric === value} onClick={() => chooseMetric(value)}>{value === "books" ? "Books" : "Pages"}</button>)}
          </span>
        </div>
        <MonthLine months={months} metric={metric} />
        <button type="button" className="text-button side-more" onClick={() => onNavigate("statistics")}>All statistics</button>
      </> : <p className="side-empty">Books you finish will chart here. Mark a book as read and set the date you finished it.</p>}
    </RailSection>

    <RailSection id="quote" title="Random quote" open={!collapsed.quote} onToggle={toggle}>
      {quote
        ? <figure className="side-quote">
          <blockquote>{quote.text}</blockquote>
          <figcaption>
            {quote.sourceId
              ? <button type="button" className="text-button" onClick={() => onOpenDocument({ document_id: quote.sourceId })}>{quote.sourceTitle}{quote.page ? ` · p. ${quote.page}` : ""}</button>
              : <span>Saved note</span>}
            {quotes.length > 1 && <button type="button" className="text-button side-shuffle" onClick={() => setQuoteId(pickQuote(quotes, quote.id)?.id ?? null)}>Another</button>}
          </figcaption>
        </figure>
        : <p className="side-empty">Add a note with a quote on a book’s page and one will turn up here.</p>}
    </RailSection>
  </aside>;
}
