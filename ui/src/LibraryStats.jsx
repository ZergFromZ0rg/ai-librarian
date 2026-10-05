import React, { useMemo } from "react";
import { libraryStats } from "./libraryStats.js";

function Breakdown({ title, rows, onSelect }) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  return <section className="stats-panel"><h3>{title}</h3>{rows.map((row) => <button type="button" className="stat-bar" key={row.label} disabled={!row.value} onClick={() => onSelect(row)}><span>{row.label}</span><i><b style={{ width: `${row.value / max * 100}%` }} /></i><strong>{row.value}</strong></button>)}</section>;
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
      <button onClick={() => onExplore({ rating: "rated", sort: "rating" })}><span>Rated</span><strong>{stats.rated}</strong><small>books</small></button>
      <button onClick={() => onExplore({ status: "read", finishedYear: String(new Date().getFullYear()), sort: "finished" })}><span>Finished this year</span><strong>{stats.completedThisYear}</strong><small>{stats.pagesThisYear.toLocaleString()} pages</small></button>
      <button onClick={() => onExplore({ rating: "rated", sort: "rating" })}><span>Average rating</span><strong>{stats.averageRating ? stats.averageRating.toFixed(1) : "—"}</strong><small>of 5</small></button>
    </div>
    <div className="stats-grid">
      <Breakdown title="Reading status" rows={stats.statuses} onSelect={(row) => onExplore({ status: row.key })} />
      <Breakdown title="Ratings" rows={stats.ratings} onSelect={(row) => onExplore({ rating: String(row.rating) })} />
      <Breakdown title="Formats" rows={stats.formats} onSelect={(row) => onExplore({ format: row.label.toLowerCase() })} />
      <Breakdown title="Genres" rows={stats.genres} onSelect={(row) => onExplore({ genre: row.label })} />
      <Breakdown title="Authors" rows={stats.authors} onSelect={(row) => onExplore({ author: row.label })} />
      <Breakdown title="Acquisition" rows={stats.sources} onSelect={(row) => onExplore({ acquisition: row.key })} />
      <Breakdown title="Collections" rows={stats.collections.map((row) => ({ ...row, key: row.label, label: collectionNames.get(row.label) || row.label }))} onSelect={(row) => onExplore({ collection: row.key })} />
      <Breakdown title="Books finished by year" rows={stats.years.length ? stats.years : [{ label: "No finished dates yet", value: 0 }]} onSelect={(row) => onExplore({ status: "read", finishedYear: row.label, sort: "finished" })} />
    </div>
  </div>;
}
