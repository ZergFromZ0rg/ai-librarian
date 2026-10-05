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
