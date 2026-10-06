import React, { useState } from "react";

import Cover from "./Cover.jsx";
import { diaryMonths, ratingNumber, starText } from "./ratings.js";
import { displayTitle } from "./storage.js";

// A review that contains spoilers stays folded until the reader opts in.
function Review({ doc }) {
  const [shown, setShown] = useState(false);
  const text = (doc.review || "").trim();
  if (!text) return null;
  if (doc.review_spoiler && !shown) {
    return <p className="diary-review spoiler"><em>Spoilers ahead.</em> <button type="button" className="spoiler-show" onClick={() => setShown(true)}>Show review</button></p>;
  }
  return <p className="diary-review">{text}</p>;
}

// Finished books by month, newest first, the way Letterboxd lists a film
// diary: the day, the cover, the title, your stars, a heart if you liked it,
// a loop if it was a re-read, and your review.
export default function LibraryDiary({ apiBase, rows, onOpen }) {
  const groups = diaryMonths(rows);
  if (!groups.length) return <p className="muted empty-state" role="status">Nothing in your diary yet. Mark a book as read and give it a finished date to log it here.</p>;
  return <div className="diary">
    {groups.map((group) => <section className="diary-month" key={group.key} aria-label={group.label}>
      <h3>{group.label}<span>{[`${group.entries.length} ${group.entries.length === 1 ? "book" : "books"}`, group.pages ? `${group.pages.toLocaleString()} pages` : null, group.average ? `avg ${ratingNumber(Math.round(group.average * 10) / 10)}★` : null].filter(Boolean).join(" · ")}</span></h3>
      <ol>{group.entries.map((doc) => <li className="diary-entry" key={doc.document_id} onClick={(event) => { if (!event.target.closest("button, a")) onOpen(doc); }}>
        <time className="diary-day" dateTime={doc.finished_at.slice(0, 10)}>{Number(doc.finished_at.slice(8, 10))}</time>
        <button type="button" className="diary-cover" onClick={() => onOpen(doc)} aria-label={`Open ${doc.title || displayTitle(doc.filename)}`}>
          <Cover apiBase={apiBase} documentId={doc.document_id} filename={doc.title || doc.filename} author={doc.author} fileType={doc.file_type} width={90} />
        </button>
        <div className="diary-body">
          <div className="diary-line">
            <button type="button" className="diary-title" onClick={() => onOpen(doc)}>{doc.title || displayTitle(doc.filename)}</button>
            {doc.published_year && <span className="muted">{doc.published_year}</span>}
            {doc.author && <span className="muted diary-author">{doc.author}</span>}
          </div>
          <div className="diary-marks">
            {doc.rating ? <span className="diary-stars" aria-label={`${ratingNumber(doc.rating)} out of 5`}>{starText(doc.rating)}</span> : null}
            {doc.liked ? <span className="diary-heart" role="img" aria-label="Liked" title="Liked">♥</span> : null}
            {doc.reread ? <span className="diary-reread" role="img" aria-label="Re-read" title="Re-read">↻</span> : null}
          </div>
          <Review doc={doc} />
        </div>
      </li>)}</ol>
    </section>)}
  </div>;
}
