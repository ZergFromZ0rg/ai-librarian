import React, { useState } from "react";
import { RATING_LABELS, ratingNumber } from "./ratings.js";

// How full star `index` (1-5) is for a rating: 0, 0.5 or 1.
const fill = (rating, index) => Math.max(0, Math.min(1, (rating || 0) - (index - 1)));

// A Letterboxd-style picker: click the left half of a star for a half star.
// It is one slider, so the arrow keys step by half a star and screen readers
// announce the value; Home/End jump to the ends and Delete clears it.
export default function StarRating({ value, onChange, disabled, size = "large" }) {
  const [hover, setHover] = useState(null);
  const shown = hover ?? value ?? 0;
  const pick = (event, index) => {
    const box = event.currentTarget.getBoundingClientRect();
    return event.clientX - box.left < box.width / 2 ? index - 0.5 : index;
  };
  function onKeyDown(event) {
    if (disabled) return;
    const current = value || 0;
    let next;
    if (["ArrowRight", "ArrowUp"].includes(event.key)) next = Math.min(5, current + 0.5);
    if (["ArrowLeft", "ArrowDown"].includes(event.key)) next = current <= 0.5 ? null : current - 0.5;
    if (event.key === "Home") next = 0.5;
    if (event.key === "End") next = 5;
    if (["Delete", "Backspace", "Escape"].includes(event.key)) next = null;
    if (next === undefined) return;
    event.preventDefault();
    onChange(next);
  }
  return <div className={`book-rating book-rating-${size}`}>
    <div className="book-stars" role="slider" tabIndex={disabled ? -1 : 0} aria-label="Your rating" aria-valuemin={0.5} aria-valuemax={5} aria-valuenow={value || undefined}
      aria-valuetext={value ? `${ratingNumber(value)} out of 5 stars, ${RATING_LABELS[value]}` : "Not rated"} aria-disabled={disabled || undefined}
      onKeyDown={onKeyDown} onMouseLeave={() => setHover(null)}>
      {[1, 2, 3, 4, 5].map((index) => <span key={index} className="book-star" data-fill={fill(shown, index)}
        onMouseMove={(event) => !disabled && setHover(pick(event, index))}
        onClick={(event) => { if (disabled) return; const next = pick(event, index); onChange(next === value ? null : next); }}>
        <span aria-hidden="true" className="book-star-base">★</span>
        <span aria-hidden="true" className="book-star-fill" style={{ width: `${fill(shown, index) * 100}%` }}>★</span>
      </span>)}
    </div>
    {size === "large" && <div className="book-rating-caption"><span>{shown ? `${ratingNumber(shown)}/5 · ${RATING_LABELS[shown]}` : "Not rated yet"}</span>{value && <button type="button" className="text-button" disabled={disabled} onClick={() => onChange(null)}>Clear rating</button>}</div>}
  </div>;
}

// The heart: "I liked this book", independent of the rating.
export function LikeButton({ liked, onChange, disabled }) {
  return <button type="button" className={`like-button${liked ? " on" : ""}`} aria-pressed={liked} disabled={disabled} onClick={() => onChange(!liked)}>
    <span aria-hidden="true">{liked ? "♥" : "♡"}</span>{liked ? "Liked" : "Like"}
  </button>;
}
