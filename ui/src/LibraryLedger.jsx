import React from "react";

import { SORTS } from "./libraryExplore.js";
import { displayTitle, readingStatus } from "./storage.js";

const STATUS = { to_read: "To read", reading: "Reading", read: "Read", abandoned: "Abandoned" };
const COLUMNS = [
  { key: "title", label: "Title" },
  { key: "author", label: "Author", optional: true },
  { key: "status", label: "Status" },
  { key: "rating", label: "Rating", numeric: true },
  { key: "format", label: "Format", optional: true },
  { key: "pages", label: "Pages", numeric: true, optional: true },
  { key: "finished", label: "Finished", optional: true },
  { key: "notes", label: "Notes", numeric: true },
  { key: "shelf", label: "Shelf", optional: true, plain: true },
];

// Which way a column currently runs: text A-Z and numbers/dates biggest first,
// flipped by the reverse toggle (the same rule filterLibrary sorts by).
function ariaSort(columnKey, filters) {
  if (filters.sort !== columnKey) return "none";
  const direction = (SORTS[columnKey]?.descending ? -1 : 1) * (filters.order === "reverse" ? -1 : 1);
  return direction === 1 ? "ascending" : "descending";
}

function Ribbon({ count }) {
  return <span className="note-ribbon-inline" role="img" aria-label={`${count} ${count === 1 ? "note" : "notes"}`} title={`${count} ${count === 1 ? "note" : "notes"}`} />;
}

// A dense, sortable inventory of the filtered books. Clicking a title opens the
// normal book page, so editing keeps its validation and unsaved-change guard.
export default function LibraryLedger({ rows, filters, onSort, onOpen }) {
  const pages = rows.reduce((sum, doc) => sum + (doc.page_count || doc.pages || 0), 0);
  const read = rows.filter((doc) => readingStatus(doc) === "read").length;
  const rated = rows.filter((doc) => doc.rating);
  const average = rated.length ? (rated.reduce((sum, doc) => sum + doc.rating, 0) / rated.length).toFixed(1) : null;
  return <div className="ledger-wrap">
    <table className="ledger">
      <caption className="sr-only">Your books. Use the column headings to sort.</caption>
      <thead><tr>{COLUMNS.map((column) => <th key={column.key} scope="col" className={`${column.numeric ? "num" : ""}${column.optional ? " ledger-optional" : ""}`} aria-sort={column.plain ? undefined : ariaSort(column.key, filters)}>
        {column.plain ? column.label : <button type="button" onClick={() => onSort(column.key)}>{column.label}<span aria-hidden="true" className="ledger-arrow">{filters.sort === column.key ? (ariaSort(column.key, filters) === "ascending" ? "▲" : "▼") : ""}</span></button>}
      </th>)}</tr></thead>
      <tbody>{rows.map((doc) => {
        const title = doc.title || displayTitle(doc.filename);
        const finished = (doc.finished_at || "").slice(0, 10);
        return <tr key={doc.document_id}>
          <th scope="row"><button type="button" className="ledger-title" onClick={() => onOpen(doc)}>{title}</button></th>
          <td className="ledger-optional">{doc.author || <span className="muted">—</span>}</td>
          <td><span className={`ledger-status status-${readingStatus(doc)}`}>{STATUS[readingStatus(doc)] || "To read"}</span></td>
          <td className="num">{doc.rating ? <span aria-label={`${doc.rating} out of 5`}>{"★".repeat(doc.rating)}</span> : <span className="muted">—</span>}</td>
          <td className="ledger-optional">{doc.record_type === "standalone" ? "Catalogue" : (doc.file_type || "").toUpperCase() || "—"}</td>
          <td className="num ledger-optional">{doc.page_count || doc.pages || <span className="muted">—</span>}</td>
          <td className="ledger-optional">{finished || <span className="muted">—</span>}</td>
          <td className="num">{doc.note_count > 0 ? <span className="ledger-notes"><Ribbon count={doc.note_count} />{doc.note_count}</span> : <span className="muted">—</span>}</td>
          <td className="ledger-optional ledger-shelf">{doc.shelf || doc.shelf_suggested || <span className="muted">—</span>}</td>
        </tr>;
      })}</tbody>
      <tfoot><tr><td colSpan={COLUMNS.length}>
        <strong>{rows.length.toLocaleString()}</strong> {rows.length === 1 ? "book" : "books"}
        {read ? ` · ${read} read` : ""}{pages ? ` · ${pages.toLocaleString()} pages` : ""}{average ? ` · average rating ${average}` : ""}
      </td></tr></tfoot>
    </table>
  </div>;
}
