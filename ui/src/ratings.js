import { readingStatus } from "./storage.js";

// Ratings run 0.5 to 5 in half-star steps, like Letterboxd. Older books hold
// whole numbers, which are simply the even steps.
export const HALF_STEPS = [0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5];

export const RATING_LABELS = {
  0.5: "Barely read", 1: "Not for me", 1.5: "Disappointing", 2: "It was okay", 2.5: "Decent",
  3: "I liked it", 3.5: "Pretty good", 4: "Really good", 4.5: "Excellent", 5: "A favourite",
};

export function isHalfStar(value) {
  return Number.isFinite(value) && value >= 0.5 && value <= 5 && value * 2 === Math.round(value * 2);
}

// 3.5 -> "★★★½". Empty for no rating.
export function starText(rating) {
  const value = Number(rating);
  if (!isHalfStar(value)) return "";
  return "★".repeat(Math.floor(value)) + (value % 1 ? "½" : "");
}

// "3.5/5" without a trailing ".0" on whole numbers.
export function ratingNumber(rating) {
  return Number.isInteger(Number(rating)) ? String(Number(rating)) : Number(rating).toFixed(1);
}

// How many books sit at each half-star step, lowest step first.
export function ratingHistogram(books) {
  return HALF_STEPS.map((rating) => ({ rating, value: books.filter((doc) => Number(doc.rating) === rating).length }));
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
export const MONTH_NAMES = MONTHS;

// Books finished per month of one year (twelve entries, January first).
export function monthCounts(books, year) {
  const counts = Array(12).fill(0);
  for (const doc of books) {
    const finished = doc.finished_at || "";
    if (readingStatus(doc) !== "read" || !finished.startsWith(String(year))) continue;
    const month = Number(finished.slice(5, 7)) - 1;
    if (month >= 0 && month < 12) counts[month] += 1;
  }
  return counts.map((value, index) => ({ label: MONTHS[index].slice(0, 3), value, month: index }));
}

// Finished books grouped by month, newest first: the reading diary. Books with
// no finished date are left out, since a diary entry needs a day.
export function diaryMonths(books) {
  const dated = books
    .filter((doc) => readingStatus(doc) === "read" && /^\d{4}-\d{2}-\d{2}/.test(doc.finished_at || ""))
    .sort((a, b) => b.finished_at.localeCompare(a.finished_at) || String(a.title).localeCompare(String(b.title)));
  const groups = [];
  for (const doc of dated) {
    const key = doc.finished_at.slice(0, 7);
    if (!groups.length || groups[groups.length - 1].key !== key) {
      groups.push({ key, label: `${MONTHS[Number(key.slice(5)) - 1]} ${key.slice(0, 4)}`, entries: [] });
    }
    groups[groups.length - 1].entries.push(doc);
  }
  for (const group of groups) {
    const rated = group.entries.filter((doc) => doc.rating);
    group.pages = group.entries.reduce((sum, doc) => sum + Number(doc.page_count || doc.pages || 0), 0);
    group.average = rated.length ? rated.reduce((sum, doc) => sum + Number(doc.rating), 0) / rated.length : 0;
  }
  return groups;
}
