import { describe, expect, it } from "vitest";
import { libraryStats } from "./libraryStats.js";

it("summarizes finished pages, ratings, and breakdowns", () => {
  const stats = libraryStats([
    { document_id: "a", kind: "book", owned: 1, reading_status: "read", rating: 5, page_count: 300, file_type: "epub", author: "A", genres_json: '["Essay"]', finished_at: "2025-01-01" },
    { document_id: "b", kind: "book", reading_status: "reading", pages: 120, file_type: "pdf", author: "B", genres_json: '["Essay"]' },
    { document_id: "n", kind_override: "note", collection_id: "notes" },
  ], new Date("2025-06-01T00:00:00Z"));
  expect(stats).toMatchObject({ total: 2, owned: 1, reading: 1, completed: 1, rated: 1, pagesRead: 300, completedThisYear: 1, pagesThisYear: 300, averageRating: 5 });
  expect(stats.genres[0]).toEqual({ label: "Essay", value: 2 });
  expect(stats.years).toEqual([{ label: "2025", value: 1 }]);
});

it("builds a half-star histogram and the Letterboxd-style counts", () => {
  const stats = libraryStats([
    { document_id: "a", kind: "book", reading_status: "read", rating: 4.5, liked: 1, finished_at: "2025-03-09" },
    { document_id: "b", kind: "book", reading_status: "read", rating: 4.5, reread: 1, review: "Great", finished_at: "2025-03-20" },
    { document_id: "c", kind: "book", reading_status: "read", rating: 2, finished_at: "2024-12-31" },
    { document_id: "d", kind: "book", reading_status: "reading", rating: 5 },
  ], new Date("2025-06-01T00:00:00Z"));
  expect(stats.ratings).toHaveLength(10);
  expect(stats.ratings.find((row) => row.rating === 4.5).value).toBe(2);
  expect(stats.ratings.find((row) => row.rating === 0.5).value).toBe(0);
  expect(stats).toMatchObject({ liked: 1, reread: 1, reviewed: 1, year: 2025 });
  expect(stats.months[2]).toMatchObject({ label: "Mar", value: 2 });
  expect(stats.months.reduce((sum, row) => sum + row.value, 0)).toBe(2);
  expect(stats.averageRating).toBe(4);
});
