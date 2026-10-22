import React, { useMemo, useState } from "react";
import MonthLine from "./MonthLine.jsx";
import { finishedByMonth } from "./libraryRail.js";
import { libraryStats } from "./libraryStats.js";
import { ratingNumber, starText } from "./ratings.js";
import { loadStored, saveStored } from "./storage.js";

const METRIC_KEY = "ai-librarian.rail.metric";

function Breakdown({ title, rows, onSelect }) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  return <section className="stats-panel"><h3>{title}</h3>{rows.map((row) => <button type="button" className="stat-bar" key={row.label} disabled={!row.value} onClick={() => onSelect(row)}><span>{row.label}</span><i><b style={{ width: `${row.value / max * 100}%` }} /></i><strong>{row.value}</strong></button>)}</section>;
}

// Ratings at every half-star step, shortest to tallest like Letterboxd's
// histogram: one star on the left, five on the right, the average above.
function RatingHistogram({ stats, onSelect }) {
  const max = Math.max(1, ...stats.ratings.map((row) => row.value));
  const total = stats.ratings.reduce((sum, row) => sum + row.value, 0);
  return <section className="stats-panel stats-wide" aria-labelledby="ratings-heading">
    <div className="stats-panel-head"><h3 id="ratings-heading">Ratings</h3><span className="muted">{total.toLocaleString()} {total === 1 ? "book" : "books"} rated</span></div>
    <div className="histogram">
      <span className="histogram-edge" aria-hidden="true">★</span>
      <div className="histogram-bars" role="group" aria-label="Books at each rating">
        {stats.ratings.map((row) => <button type="button" key={row.rating} className="histogram-bar" disabled={!row.value} onClick={() => onSelect({ rating: String(row.rating) })} title={`${row.value} ${row.value === 1 ? "book" : "books"} rated ${starText(row.rating)}`} aria-label={`${starText(row.rating)}: ${row.value} ${row.value === 1 ? "book" : "books"}`}>
          <span className="histogram-count">{row.value || ""}</span>
          <i style={{ height: `${Math.max(row.value ? 6 : 2, row.value / max * 100)}%` }} />
        </button>)}
      </div>
      <span className="histogram-edge" aria-hidden="true">★★★★★</span>
      <div className="histogram-average"><strong>{stats.averageRating ? ratingNumber(Math.round(stats.averageRating * 10) / 10) : "—"}</strong><span>{stats.averageRating ? starText(Math.round(stats.averageRating * 2) / 2) : "no ratings yet"}</span><small>average</small></div>
    </div>
  </section>;
}

// A column chart: one column per label, tallest first-class, clickable.
function Columns({ title, note, rows, onSelect, empty }) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  return <section className="stats-panel stats-wide">
    <div className="stats-panel-head"><h3>{title}</h3><span className="muted">{note ?? `${total.toLocaleString()} ${total === 1 ? "book" : "books"}`}</span></div>
    {rows.length === 0 || total === 0 ? <p className="muted">{empty}</p> : <div className="columns" role="group" aria-label={title}>
      {rows.map((row) => <button type="button" key={row.label} className="column" disabled={!row.value} onClick={() => onSelect(row)} title={`${row.label}: ${row.value}`} aria-label={`${row.label}: ${row.value} ${row.value === 1 ? "book" : "books"}`}>
        <span className="column-count">{row.value || ""}</span>
        <i style={{ height: `${Math.max(row.value ? 4 : 1, row.value / max * 100)}%` }} />
        <span className="column-label">{row.label}</span>
      </button>)}
    </div>}
  </section>;
}

// The line of the last twelve months, books or pages. Not tied to the calendar
// year like the columns above it, so it still reads well in January.
function LastTwelveMonths({ documents }) {
  const months = useMemo(() => finishedByMonth(documents), [documents]);
  const [metric, setMetric] = useState(() => (loadStored(METRIC_KEY, "books") === "pages" ? "pages" : "books"));
  const choose = (value) => { setMetric(value); saveStored(METRIC_KEY, value); };
  const total = months.reduce((sum, month) => sum + month[metric], 0);
  const unit = metric === "books" ? (total === 1 ? "book" : "books") : (total === 1 ? "page" : "pages");
  return <section className="stats-panel stats-wide stats-months">
    <div className="stats-panel-head">
      <h3>Last 12 months</h3>
      <span className="month-head">
        <span className="muted">{total.toLocaleString()} {unit}</span>
        <span className="month-metric" role="group" aria-label="Chart shows">
          {["books", "pages"].map((value) => <button type="button" key={value} className={metric === value ? "active" : ""} aria-pressed={metric === value} onClick={() => choose(value)}>{value === "books" ? "Books" : "Pages"}</button>)}
        </span>
      </span>
    </div>
    {months.some((month) => month.books > 0)
      ? <MonthLine months={months} metric={metric} />
      : <p className="muted">Books you finish will chart here. Mark a book as read and set the date you finished it.</p>}
  </section>;
}

export default function LibraryStats({ documents, collections = [], onExplore }) {
  const stats = useMemo(() => libraryStats(documents), [documents]);
  const collectionNames = new Map(collections.map((item) => [item.id, item.name]));
  return <div className="library-statistics">
    <div className="stat-cards">
      <button onClick={() => onExplore({})}><span>Library</span><strong>{stats.total}</strong><small>books</small></button>
      <button onClick={() => onExplore({ ownership: "owned" })}><span>Owned</span><strong>{stats.owned}</strong><small>books</small></button>
      <button onClick={() => onExplore({ status: "reading" })}><span>In progress</span><strong>{stats.reading}</strong><small>books</small></button>
      <button onClick={() => onExplore({ status: "read" })}><span>Finished</span><strong>{stats.completed}</strong><small>{stats.pagesRead.toLocaleString()} pages</small></button>
      <button onClick={() => onExplore({ liked: "liked" })}><span>Liked</span><strong>{stats.liked}</strong><small>{stats.reread ? `${stats.reread} re-read` : "books"}</small></button>
      <button onClick={() => onExplore({ status: "read", finishedYear: String(new Date().getFullYear()), sort: "finished" })}><span>Finished this year</span><strong>{stats.completedThisYear}</strong><small>{stats.pagesThisYear.toLocaleString()} pages</small></button>
      <button onClick={() => onExplore({ rating: "rated", sort: "rating" })}><span>Average rating</span><strong>{stats.averageRating ? stats.averageRating.toFixed(1) : "—"}</strong><small>of 5 · {stats.rated} rated</small></button>
    </div>
    <RatingHistogram stats={stats} onSelect={(preset) => onExplore(preset)} />
    <Columns title="Books finished by year" rows={stats.years} empty="No finished dates yet. Mark a book as read and set the date you finished it." onSelect={(row) => onExplore({ status: "read", finishedYear: row.label, sort: "finished" })} />
    <Columns title={`${stats.year} by month`} rows={stats.months} empty={`Nothing finished in ${stats.year} yet.`} onSelect={(row) => onExplore({ status: "read", finishedYear: String(stats.year), sort: "finished" })} />
    <LastTwelveMonths documents={documents} />
    <div className="stats-grid">
      <Breakdown title="Reading status" rows={stats.statuses} onSelect={(row) => onExplore({ status: row.key })} />
      <Breakdown title="Formats" rows={stats.formats} onSelect={(row) => onExplore({ format: row.label.toLowerCase() })} />
      <Breakdown title="Genres" rows={stats.genres} onSelect={(row) => onExplore({ genre: row.label })} />
      <Breakdown title="Authors" rows={stats.authors} onSelect={(row) => onExplore({ author: row.label })} />
      <Breakdown title="Acquisition" rows={stats.sources} onSelect={(row) => onExplore({ acquisition: row.key })} />
      <Breakdown title="Collections" rows={stats.collections.map((row) => ({ ...row, key: row.label, label: collectionNames.get(row.label) || row.label }))} onSelect={(row) => onExplore({ collection: row.key })} />
    </div>
  </div>;
}
