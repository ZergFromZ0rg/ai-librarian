import { describe, expect, it } from "vitest";

import { activityBucket, filterActivity, formatBytes, groupActivity, itemFacts, relativeTime, stageLabel } from "./indexActivity.js";

const items = [
  { document_id: "a", indexing_status: "queued", stage: "queued", queue_position: 2 },
  { document_id: "b", indexing_status: "indexing", stage: "embedding", completed_units: 20, total_units: 50 },
  { document_id: "c", indexing_status: "error", stage: "awaiting_ocr" },
  { document_id: "d", indexing_status: "indexed", stage: "complete", indexed_at: "2026-10-02T10:00:00Z" },
  { document_id: "e", indexing_status: "indexed", stage: "complete", indexed_at: "2026-10-03T10:00:00Z" },
];

describe("index activity helpers", () => {
  it("filters active work and groups every queue state", () => {
    expect(filterActivity(items, "active").map((item) => item.document_id)).toEqual(["a", "b"]);
    expect(activityBucket(items[2])).toBe("attention");
    const groups = groupActivity(items);
    expect(groups.processing).toHaveLength(1);
    expect(groups.queued).toHaveLength(1);
    expect(groups.attention).toHaveLength(1);
    expect(groups.finished.map((item) => item.document_id)).toEqual(["e", "d"]);
  });

  it("describes real embedding progress and queue position", () => {
    expect(stageLabel(items[0])).toBe("Queued · #2");
    expect(stageLabel(items[1])).toBe("Building search index · 20 of 50 passages");
    expect(stageLabel(items[2])).toBe("Waiting for OCR approval");
  });

  it("formats file details and relative timestamps", () => {
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(itemFacts({ size_bytes: 1048576, file_type: "pdf", pages: 12, retrieval_units: 34 })).toEqual(["1.0 MB", "PDF", "12 pages", "34 passages"]);
    expect(relativeTime("2026-10-04T11:59:30Z", Date.parse("2026-10-04T12:00:00Z"))).toBe("30s ago");
  });
});
